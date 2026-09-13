"""Invariant 13 — the log does not drag a database into the fast tier.

Two halves, and they fail for different reasons.

The first is that `say_move` still runs with no Postgres anywhere. The logging
write is the one place in this feature that opens a connection of its own, and
it opens it from a `finally` that runs on every path out, so a fast-tier test
that never asked for a database would start reaching for one the moment that
`finally` stopped going through the injected factory. `psycopg.connect` is
replaced with something that raises, which is what a developer with no
`testdb.sh` running has anyway — only loud instead of a 30-second timeout.

The second is the import rule the other two gate files state in prose and
neither asserts: no test file imports `queries.llm_calls`. A test that reaches
through the query layer stops being a statement about what a row is, and — for
a fast-tier file — imports a module that exists to talk to Postgres.

No marker: this file is tier 1 and must collect and pass with no database.
"""
import ast
import pathlib
import time

import pytest

from controller_operations import sessions_ops
from controller_operations.errors import ApiError
from tests.conftest import FakeConnection, FakeCursor, fake_session_row, op_db

TESTS_DIR = pathlib.Path(__file__).resolve().parent


def _records(outcome, uci="e2e4", raise_with=None):
    """A `pick_move_with_llm` stand-in that fills the log the way the real one
    does, then optionally raises.

    The fakes the other fast-tier files use ignore `log=` and so leave a
    `CallLog` with no attempts, which `_persist_call_log` correctly declines to
    write. That is the right behaviour and it is useless here: reaching the
    write is the whole point of these two gates.
    """
    def _fake(*_a, log=None, **_k):
        if log is not None:
            log.add_attempt(1, outcome, time.monotonic())
            if outcome == "ok":
                log.finish(outcome, chosen_uci=uci, off_list=False,
                           intent="an intent", rationale="a rationale",
                           tone_summary="eager")
            else:
                log.finish(outcome)
        if raise_with is not None:
            raise raise_with
        return (uci, "eager", "an intent", "a rationale")

    return _fake


def _say(socketio, monkeypatch, llm, fail_on=None):
    cursor = FakeCursor(fake_session_row(), None, fail_on=fail_on)
    monkeypatch.setattr(sessions_ops, "pick_move_with_llm", llm)
    sessions_ops.say_move(
        op_db(FakeConnection(cursor)), socketio, "sess0001", "press them", "wtok"
    )
    return cursor


def _logged(cursor):
    return [sql for sql, _params in cursor.executed if "INSERT INTO llm_call" in sql]


@pytest.fixture
def no_database(monkeypatch):
    """Make any connection attempt outside the injected factory raise.

    Patched on the `psycopg` module itself rather than on a name imported into
    one operation module, so it catches a connection opened from anywhere the
    logging path reaches.
    """
    import psycopg

    def _refuse(*_args, **_kwargs):
        raise AssertionError(
            "the fast tier opened a Postgres connection; the log write must go "
            "through the db factory the operation was given"
        )

    monkeypatch.setattr(psycopg, "connect", _refuse)


def test_a_logged_say_move_needs_no_database(socketio, no_database, monkeypatch):
    """The success path. The log write runs, and every statement it issues goes
    to the cursor the operation was handed."""
    cursor = _say(socketio, monkeypatch, llm=_records("ok"))
    assert _logged(cursor), (
        "the log write never ran, so this says nothing about where it connects"
    )


def test_a_failed_say_move_needs_no_database(socketio, no_database, monkeypatch):
    """The `finally` path. The log write runs after the move has already
    raised, which is the path that must not reach for a connection of its own,
    and the one a passing success case would not cover."""
    fake = _records("transport", raise_with=TimeoutError("groq down"))
    cursor = FakeCursor(fake_session_row(), None)
    monkeypatch.setattr(sessions_ops, "pick_move_with_llm", fake)

    with pytest.raises(ApiError) as excinfo:
        sessions_ops.say_move(
            op_db(FakeConnection(cursor)), socketio, "sess0001", "press them",
            "wtok",
        )

    assert excinfo.value.code == "llm_unavailable"
    assert _logged(cursor), "the failed call was never logged"


def _imported_modules(path):
    """Every module name the file imports, by parse rather than by substring:
    two gate files name `queries.llm_calls` in their docstrings to say they do
    not import it, and a grep cannot tell that apart from importing it."""
    tree = ast.parse(path.read_text())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def test_no_test_file_imports_the_query_layer():
    """Invariant 13's second half, over every file in the tier."""
    offenders = sorted(
        path.name
        for path in TESTS_DIR.glob("*.py")
        if "queries.llm_calls" in _imported_modules(path)
    )
    assert offenders == [], (
        f"{offenders} import the query layer; a gate asserts what a row is, so "
        "it must read the row back and not call the function that wrote it"
    )


# --- the call-level catch-all -----------------------------------------------

def test_a_record_is_insertable_before_anything_finishes_it():
    """Decided 2026-09-13: `llm_calls.outcome` carries `unexpected` until a real
    outcome replaces it.

    Asserted on the record rather than through Postgres because it is a claim
    about `CallLog`, not about the table: the column is NOT NULL, so a record
    whose outcome is still None is one the insert rejects — and both inserts
    share a transaction, so rejecting it discards the attempt rows that were
    classified correctly on the way there.
    """
    from controller_operations.llm import CallLog

    record = CallLog(
        "sess0001", 1, "fen", "press them", [("e2e4", "e4")], None
    ).call_record()

    assert record["outcome"] == "unexpected", (
        "a call that ends in a way nothing anticipated must still be insertable; "
        f"outcome was {record['outcome']!r}, which the NOT NULL rejects"
    )
