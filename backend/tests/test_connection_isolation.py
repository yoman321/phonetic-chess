"""Invariants 5, 6 and 7 — one connection per operation.

application.py opens a single connection at import and hands the same object to
every request and to the presence timer thread. Transaction state lives on the
connection, so two operations cannot hold two transactions: psycopg nests the
second as a SAVEPOINT, and the first one's ROLLBACK then discards the second
one's already-committed work.

These go through the Flask test client on purpose. The wiring is what is under
test, so nothing here may substitute for how application.py hands an operation
its database handle.
"""
import threading
import time

import pytest

from controller_operations import sessions_ops
from queries import sessions as sessions_q
from tests.conftest import Latch

pytestmark = pytest.mark.integration

CONCURRENT_OPS = 3


def _backend_count(pgdb):
    with pgdb.cursor() as cur:
        cur.execute(
            "SELECT count(*) AS n FROM pg_stat_activity "
            "WHERE datname = current_database()"
        )
        return cur.fetchone()["n"]


def _fen(pgdb, sid):
    with pgdb.cursor() as cur:
        cur.execute("SELECT fen FROM sessions WHERE id = %s", (sid,))
        return cur.fetchone()["fen"]


def _park_the_llm(monkeypatch, latch, then_raise=False):
    """Hold say_move open inside its transaction until the test releases it."""
    def _parked(*_args, **_kwargs):
        latch.wait_here()
        if then_raise:
            raise RuntimeError("groq failed after the transaction was open")
        return ("e2e4", "eager", "attack", "because")

    monkeypatch.setattr(sessions_ops, "pick_move_with_llm", _parked)


def _say(app_module, session, text="attack"):
    client = app_module.app.test_client()
    return client.post(
        f"/sessions/{session['sid']}/say",
        json={"text": text, "playerToken": session["white_token"]},
    )


def _move(app_module, session, uci="e2e4"):
    client = app_module.app.test_client()
    return client.post(
        f"/sessions/{session['sid']}/move",
        json={"uci": uci, "playerToken": session["white_token"]},
    )


def test_a_failing_operation_does_not_roll_back_another_ones_move(
    app_module, pgdb, make_session, monkeypatch
):
    """Invariant 5 — isolation."""
    slow = make_session("iso-slow")
    other = make_session("iso-other")
    latch = Latch()
    _park_the_llm(monkeypatch, latch, then_raise=True)

    thread = threading.Thread(
        target=_say, args=(app_module, slow), daemon=True
    )
    try:
        thread.start()
        latch.await_parked()

        response = _move(app_module, other)
        assert response.status_code == 200
        committed_fen = response.get_json()["fen"]
    finally:
        latch.release.set()
        thread.join(timeout=15)

    assert _fen(pgdb, other["sid"]) == committed_fen


def test_two_in_flight_operations_use_different_connections(
    app_module, pgdb, make_session, monkeypatch
):
    """Invariant 6 — distinct connections."""
    slow = make_session("pid-slow")
    other = make_session("pid-other")
    latch = Latch()
    _park_the_llm(monkeypatch, latch)

    pids = []
    real_select = sessions_q.select_state_for_update

    def recording_select(cur, sid):
        cur.execute("SELECT pg_backend_pid() AS pid")
        pids.append(cur.fetchone()["pid"])
        return real_select(cur, sid)

    monkeypatch.setattr(sessions_q, "select_state_for_update", recording_select)

    thread = threading.Thread(
        target=_say, args=(app_module, slow), daemon=True
    )
    try:
        thread.start()
        latch.await_parked()
        assert _move(app_module, other).status_code == 200
    finally:
        latch.release.set()
        thread.join(timeout=15)

    assert len(pids) == 2
    assert len(set(pids)) == 2


def test_in_flight_operations_each_hold_one_connection_and_release_it(
    app_module, pgdb, make_session, monkeypatch
):
    """Invariant 7 — no leak.

    Both halves are load-bearing: the rise by N proves the connections are
    separate, the return to baseline proves they are closed. Measuring only
    after completion passes before the work exists and is therefore not a gate.

    Each parked operation gets its own latch and is released alone, so no two
    threads ever execute at the same moment. That is not a weaker test — the
    assertion is about N operations being open simultaneously, which is what the
    parking establishes. It is also the only way to run this today: three
    threads committing on one shared connection wedge psycopg client-side, and
    a wedged connection would take the rest of the suite with it.
    """
    sessions = [make_session(f"leak-{i}") for i in range(CONCURRENT_OPS)]
    latches = {s["sid"]: Latch() for s in sessions}
    arrived = threading.Semaphore(0)

    def _parked(text, *_args, **_kwargs):
        arrived.release()
        latches[text].wait_here()          # the phrase carries the session id
        return ("e2e4", "eager", "attack", "because")

    monkeypatch.setattr(sessions_ops, "pick_move_with_llm", _parked)

    baseline = _backend_count(pgdb)
    threads = {
        s["sid"]: threading.Thread(
            target=_say, args=(app_module, s), kwargs={"text": s["sid"]},
            daemon=True,
        )
        for s in sessions
    }
    try:
        for thread in threads.values():
            thread.start()
        for _ in range(CONCURRENT_OPS):
            assert arrived.acquire(timeout=15), "an operation never parked"

        assert _backend_count(pgdb) == baseline + CONCURRENT_OPS
    finally:
        for sid, thread in threads.items():
            latches[sid].release.set()
            thread.join(timeout=15)

    _assert_returns_to(pgdb, baseline)


def _assert_returns_to(pgdb, baseline, timeout=10.0):
    """Postgres reaps a closed backend a moment after the client hangs up."""
    deadline = time.monotonic() + timeout
    count = _backend_count(pgdb)
    while count != baseline and time.monotonic() < deadline:
        time.sleep(0.05)
        count = _backend_count(pgdb)
    assert count == baseline
