"""Invariant 2 — no delete after a join.

presence re-checks room membership under _presence_lock, then releases the lock
and only then issues the DELETE. A join landing in that window is invisible to
the check that already ran, so the session is deleted with a socket in its room.

The assertion is on what presence knew at the moment it issued the DELETE. The
plan's wording ("count(*) == 1 after releasing the parked delete") cannot hold
once the DELETE moves inside the lock: the parked join then serialises *after*
the delete and the row is gone either way. What the fix actually guarantees, and
what is false today, is that no DELETE is ever issued while a socket is tracked.

The divergence from the plan's wording is deliberate and was accepted on
2026-09-09: the plan is not changing, and the residual window — a join that
serialises behind the DELETE still joins a room whose session is gone — stays
open by decision. Do not "correct" this back to asserting count(*) == 1.
"""
import threading
import time

import pytest

from queries import sessions as sessions_q
from tests.conftest import Latch

pytestmark = pytest.mark.integration


def _row_count(pgdb, sid):
    with pgdb.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM sessions WHERE id = %s", (sid,))
        return cur.fetchone()["n"]


def test_no_delete_is_issued_while_a_socket_is_tracked(
    pgdb, make_session, clean_presence, monkeypatch
):
    presence = clean_presence
    session = make_session("pres0001")
    sid = session["sid"]

    latch = Latch()
    observed = []
    real_delete = sessions_q.delete_session

    def parked_delete(cur, target_sid):
        latch.wait_here()
        observed.append(set(presence._active_sids.get(target_sid, ())))
        return real_delete(cur, target_sid)

    monkeypatch.setattr(sessions_q, "delete_session", parked_delete)

    deleter = threading.Thread(
        target=presence._delete_session, args=(sid,), daemon=True
    )
    joiner = threading.Thread(
        target=presence.track_join, args=(sid, "sock-1"), daemon=True
    )
    try:
        deleter.start()
        latch.await_parked()

        # The join races the parked delete. Today it lands immediately, so this
        # loop exits as soon as it is visible; once the DELETE is inside the
        # lock the join cannot land and the loop runs out its deadline.
        joiner.start()
        deadline = time.monotonic() + 0.5
        while time.monotonic() < deadline and not presence._active_sids.get(sid):
            time.sleep(0.005)
    finally:
        latch.release.set()
        deleter.join(timeout=10)
        joiner.join(timeout=10)

    assert len(observed) == 1, "the DELETE was never issued"
    assert observed[0] == set()


def test_a_join_before_the_check_cancels_the_delete(
    pgdb, make_session, clean_presence, monkeypatch
):
    """Control: the re-check already covers a join that lands before it."""
    presence = clean_presence
    session = make_session("pres0002")
    sid = session["sid"]

    deletes = []
    real_delete = sessions_q.delete_session

    def counting_delete(cur, target_sid):
        deletes.append(target_sid)
        return real_delete(cur, target_sid)

    monkeypatch.setattr(sessions_q, "delete_session", counting_delete)

    presence.track_join(sid, "sock-1")
    presence._delete_session(sid)

    assert deletes == []
    assert _row_count(pgdb, sid) == 1
