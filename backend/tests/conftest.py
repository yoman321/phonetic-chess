"""Shared fixtures for both test tiers.

Tier 1 (no marker) runs against fakes: a recording socketio and a cursor that
answers by SQL text. Tier 2 (`@pytest.mark.integration`) needs a live Postgres,
because invariants 2 and 5-7 assert on Postgres internals that cannot be faked.
"""
import os
import secrets
import threading
import warnings

import pytest

# Set, not default: a developer with a real DATABASE_URL exported must not have
# the suite run against their own data. Must happen before application is
# imported: reschedule_existing_sessions() opens a connection at import time.
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql://phonetic:phonetic@127.0.0.1:55432/phonetic_chess",
)
# presence schedules a deletion timer for every session it sees. An hour is long
# enough that no test row is collected while a test is still using it.
os.environ.setdefault("IDLE_TTL_SECONDS", "3600")

START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"

# Session ids are suffixed per run, so a row left behind by an interrupted run
# cannot collide with the next run's INSERT.
RUN_ID = secrets.token_hex(4)


def op_db(conn):
    """Whatever the operations' first parameter currently is.

    Phase 4 landed: the operations take a zero-arg factory and open their own
    connection, so this hands back a factory over the fake. No assertion moved.
    """
    return lambda: conn


class Latch:
    """Park an operation at a known point until the test releases it."""

    def __init__(self):
        self.reached = threading.Event()   # set by the op when it parks
        self.release = threading.Event()   # set by the test to let it go

    def wait_here(self):
        self.reached.set()
        if not self.release.wait(timeout=10):
            raise AssertionError("Latch was never released")

    def await_parked(self, timeout=10):
        if not self.reached.wait(timeout=timeout):
            raise AssertionError("operation never reached the latch")


class FakeSocketIO:
    """Records emits instead of sending them."""

    def __init__(self):
        self.emits = []

    def emit(self, event, payload=None, to=None, **_kwargs):
        self.emits.append((event, payload, to))

    def events(self, name):
        return [payload for event, payload, _to in self.emits if event == name]


def fake_session_row(**overrides):
    row = {
        "id": "sess0001",
        "fen": START_FEN,
        "pgn": "",
        "status": "active",
        "white_token": "wtok",
        "black_token": "btok",
    }
    row.update(overrides)
    return row


class FakeCursor:
    """Answers the four query shapes sessions_ops reaches through queries/."""

    def __init__(self, session_row, last_move_row=None, fail_on=None):
        self.session_row = session_row
        self.last_move_row = last_move_row
        self.fail_on = fail_on      # substring; execute() raises when it matches
        self.executed = []
        self._result = None

    def execute(self, sql, params=None):
        collapsed = " ".join(sql.split())
        self.executed.append((collapsed, params))
        if self.fail_on and self.fail_on in collapsed:
            raise RuntimeError(f"database write failed: {self.fail_on}")
        if collapsed.startswith("SELECT") and "FROM sessions" in collapsed:
            self._result = self.session_row
        elif collapsed.startswith("SELECT") and "FROM moves" in collapsed:
            self._result = self.last_move_row
        else:
            self._result = None

    def fetchone(self):
        return self._result

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


class _NullTransaction:
    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


class FakeConnection:
    """Stands in for a psycopg connection. Hands out one shared FakeCursor so a
    test can read back every statement the operation issued."""

    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor

    def transaction(self):
        return _NullTransaction()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


@pytest.fixture
def socketio():
    return FakeSocketIO()


@pytest.fixture
def no_llm(monkeypatch):
    """Fail loudly if an operation reaches the LLM when it should not.

    Returns a list of call arg tuples so a test can assert the call count.
    """
    from controller_operations import sessions_ops

    calls = []

    def _spy(*args, **kwargs):
        calls.append((args, kwargs))
        return ("e2e4", "tone", "intent", "rationale")

    monkeypatch.setattr(sessions_ops, "pick_move_with_llm", _spy)
    return calls


# --- integration tier -------------------------------------------------------
# Everything below imports application, which connects at import time, so it is
# reached only from a fixture. Collection must stay possible without a database.


@pytest.fixture(scope="session")
def app_module():
    import application

    return application


@pytest.fixture
def pgdb():
    """A connection of the test's own, for observing what the app wrote.

    Deliberately not the app's connection: invariants 5-7 are about how many
    connections exist and what each one sees.
    """
    import psycopg
    from psycopg.rows import dict_row

    # statement_timeout so a teardown DELETE that collides with a row lock still
    # held by a wedged operation fails loudly instead of hanging the suite.
    conn = psycopg.connect(
        os.environ["DATABASE_URL"],
        autocommit=True,
        row_factory=dict_row,
        options="-c statement_timeout=5000",
    )
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def make_session(pgdb):
    """Insert a session with both colours claimed, and clean it up afterwards.

    Both tokens are set because Phase 3 lands before Phase 4: once a move is
    refused until both players have joined, a half-claimed row cannot move.
    """
    created = []

    def _make(name, fen=START_FEN):
        sid = f"{name}-{RUN_ID}"
        with pgdb.cursor() as cur:
            cur.execute(
                "INSERT INTO sessions (id, fen, white_token, black_token) "
                "VALUES (%s, %s, %s, %s)",
                (sid, fen, f"w-{sid}", f"b-{sid}"),
            )
        created.append(sid)
        return {"sid": sid, "white_token": f"w-{sid}", "black_token": f"b-{sid}"}

    yield _make

    for sid in created:
        try:
            with pgdb.cursor() as cur:
                cur.execute("DELETE FROM sessions WHERE id = %s", (sid,))
        except Exception as exc:      # noqa: BLE001 - teardown must not mask a failure
            warnings.warn(
                f"could not clean up session {sid}: {exc}. An operation is still "
                "holding the row lock; `scripts/testdb.sh down` discards it.",
                stacklevel=1,
            )


@pytest.fixture
def clean_presence(app_module):
    """Drop presence's in-memory room state before and after a test."""
    from controller_operations import presence

    def _reset():
        with presence._presence_lock:
            for timer in presence._cleanup_timers.values():
                timer.cancel()
            presence._cleanup_timers.clear()
            presence._active_sids.clear()

    _reset()
    yield presence
    _reset()
