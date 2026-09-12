"""Invariant 3 — `last_disconnected_at` is written on every disconnect.

The trigger stamps unconditionally. A guard on "was this the last row?" loses
the write whenever two sockets of one game go at once, and a lost stamp is worse
than a late one: with the column NULL the predicate falls through to
`COALESCE(..., created_at)`, so a half-hour game whose two tabs close together
reads as ended seconds later.

A NULL stamp must therefore mean exactly one thing — no socket has ever
disconnected from this game.

These delete `session_connections` rows directly rather than going through a
socket. The invariant is a property of the trigger, and driving it from two
Socket.IO clients would run both handlers in one thread and never overlap.
"""
import os
import threading
import time

import psycopg
import pytest
from psycopg.rows import dict_row

from tests.conftest import Latch

pytestmark = pytest.mark.integration


def _track(pgdb, sid, socket_sid):
    with pgdb.cursor() as cur:
        cur.execute(
            "INSERT INTO session_connections (session_id, socket_sid) "
            "VALUES (%s, %s)",
            (sid, socket_sid),
        )


def _release(pgdb, socket_sid):
    with pgdb.cursor() as cur:
        cur.execute(
            "DELETE FROM session_connections WHERE socket_sid = %s", (socket_sid,)
        )


def _clear_stamp(pgdb, sid):
    with pgdb.cursor() as cur:
        cur.execute(
            "UPDATE sessions SET last_disconnected_at = NULL WHERE id = %s", (sid,)
        )


def _seconds_since_stamp(pgdb, sid):
    with pgdb.cursor() as cur:
        cur.execute(
            "SELECT EXTRACT(EPOCH FROM (NOW() - last_disconnected_at)) AS s "
            "FROM sessions WHERE id = %s",
            (sid,),
        )
        row = cur.fetchone()
    return None if row["s"] is None else float(row["s"])


def test_a_disconnect_stamps_even_with_another_socket_still_connected(
    pgdb, make_session
):
    """The cheapest statement of "unconditional", with no timing in it.

    A guarded trigger skips the write here because a row remains, and this fails
    on the first assertion.
    """
    sid = make_session("stamp-pair")["sid"]
    _track(pgdb, sid, "pair-a")
    _track(pgdb, sid, "pair-b")
    _clear_stamp(pgdb, sid)

    _release(pgdb, "pair-a")

    elapsed = _seconds_since_stamp(pgdb, sid)
    assert elapsed is not None, "the disconnect left the stamp NULL"
    assert elapsed < 5


def test_the_stamp_survives_two_concurrent_disconnects(pgdb, make_session):
    """The case the revision was written around.

    `db.py:18` connects with `autocommit=True`, so a real disconnect DELETE is a
    single statement that cannot be held open. Constructing the interleaving
    deterministically needs explicit transactions on two connections, which is a
    stronger condition than production can produce — the safe direction, and the
    only way to make this reliable rather than a flaky race.

    Under a guarded trigger neither transaction sees the other's uncommitted
    delete under READ COMMITTED, so neither takes the write branch and the stamp
    stays NULL. Under the unconditional one the second `UPDATE sessions` simply
    waits on the first's row lock and then lands.
    """
    sid = make_session("stamp-race")["sid"]
    _track(pgdb, sid, "race-a")
    _track(pgdb, sid, "race-b")
    _clear_stamp(pgdb, sid)

    latch = Latch()
    errors = []

    def _delete_in_its_own_transaction(socket_sid, park):
        try:
            conn = psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM session_connections WHERE socket_sid = %s",
                        (socket_sid,),
                    )
                if park:
                    latch.wait_here()      # hold the transaction open
                conn.commit()
            finally:
                conn.close()
        except Exception as exc:           # noqa: BLE001 - reported, not swallowed
            errors.append(exc)

    first = threading.Thread(
        target=_delete_in_its_own_transaction, args=("race-a", True), daemon=True
    )
    second = threading.Thread(
        target=_delete_in_its_own_transaction, args=("race-b", False), daemon=True
    )
    try:
        first.start()
        latch.await_parked()
        # The second delete overlaps the first: it either blocks on the row lock
        # the first trigger's UPDATE took, or - with a guard - sails past it.
        second.start()
        time.sleep(0.2)
    finally:
        latch.release.set()
        first.join(timeout=10)
        second.join(timeout=10)

    assert errors == []
    elapsed = _seconds_since_stamp(pgdb, sid)
    assert elapsed is not None, "two concurrent disconnects lost the stamp"
    assert elapsed < 5


def test_a_null_stamp_means_no_socket_has_ever_left(pgdb, make_session):
    """The other half of invariant 3, and what the COALESCE fallback relies on."""
    sid = make_session("stamp-null")["sid"]

    assert _seconds_since_stamp(pgdb, sid) is None

    _track(pgdb, sid, "null-a")
    assert _seconds_since_stamp(pgdb, sid) is None

    _release(pgdb, "null-a")
    assert _seconds_since_stamp(pgdb, sid) is not None
