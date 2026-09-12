"""Invariant 6 — a connection insert does not wait on an in-flight `say_move`.

Inserting a `session_connections` row makes Postgres check the foreign key,
which it does by taking `FOR KEY SHARE` on the parent `sessions` row. That
conflicts with `FOR UPDATE`, which `say_move` holds across its Groq round-trip.
Left alone, joining a game with a phrase in flight would block for up to ~90s:
today's bug, relocated from a Python lock into Postgres and landing on the join
itself. `FOR NO KEY UPDATE` still excludes other movers but lets the joiner's
foreign-key check through.

**This is a claim about PostgreSQL, not about this codebase**, and `handoff.md`
names it the one thing that must be demonstrated before anything depends on it.

The plan asked for this to be asserted by timing the socket `join_session`.
That test cannot fail: `on_join_session` (`controller/sockets.py:14-21`) only
calls `join_room`, `track_join` and `cancel_cleanup`, all in-memory dict writes
that cannot wait on a Postgres row lock — so it returns in roughly zero
milliseconds today, and it still does at every phase boundary, because the lock
downgrade lands in phase 2 and the insert that would block only appears in phase
3. **Dropped on the user's decision, 2026-09-12.** What is left is the same
claim asserted where it can actually be observed, against the real lock rather
than a hand-written one.

Three distinguishable outcomes, which is what makes it a gate:
  - today, with no table:                     UndefinedTable
  - phase 1 alone, `FOR UPDATE` still there:  blocks, trips pgdb's 5s
                                              statement_timeout
  - phase 2, downgraded:                      returns promptly
"""
import threading
import time

import pytest

from controller_operations import sessions_ops
from tests.conftest import Latch

pytestmark = pytest.mark.integration

PROMPTLY_MS = 500


def _park_the_llm(monkeypatch, latch):
    """Hold say_move open inside its transaction until the test releases it."""
    def _parked(*_args, **_kwargs):
        latch.wait_here()
        return ("e2e4", "eager", "attack", "because")

    monkeypatch.setattr(sessions_ops, "pick_move_with_llm", _parked)


def _say(app_module, session, text="attack"):
    client = app_module.app.test_client()
    return client.post(
        f"/sessions/{session['sid']}/say",
        json={"text": text, "playerToken": session["white_token"]},
    )


def test_a_connection_insert_does_not_wait_on_an_in_flight_say_move(
    app_module, pgdb, make_session, monkeypatch
):
    """Asserts elapsed milliseconds, not ordering.

    The lock is the real one — `select_state_for_update` (`queries/sessions.py:61`)
    held by a parked `say_move` — rather than a `FOR UPDATE` written by the test,
    so the downgrade is demonstrated where it actually has to work.
    """
    session = make_session("fk-under-lock")
    sid = session["sid"]
    latch = Latch()
    _park_the_llm(monkeypatch, latch)

    thread = threading.Thread(target=_say, args=(app_module, session), daemon=True)
    try:
        thread.start()
        latch.await_parked()

        started = time.monotonic()
        with pgdb.cursor() as cur:
            cur.execute(
                "INSERT INTO session_connections (session_id, socket_sid) "
                "VALUES (%s, %s)",
                (sid, "fk-probe"),
            )
        elapsed_ms = (time.monotonic() - started) * 1000
    finally:
        latch.release.set()
        thread.join(timeout=15)

    assert elapsed_ms < PROMPTLY_MS, (
        f"the connection insert waited {elapsed_ms:.0f}ms on the locked session row"
    )
