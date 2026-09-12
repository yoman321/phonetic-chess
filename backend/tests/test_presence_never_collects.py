"""Invariants 7, 8 and 9 — nothing is destroyed, nothing runs, nothing is held.

A quiet game keeps its row and its moves so a player who hits a problem can
reach a developer and have it inspected. Ended-ness is computed at the moment
someone tries to use the game, never written to `status`. And presence holds no
in-process state, so a freshly started process answers the same questions
without a rebuild step.

Two of these lower `IDLE_TTL_SECONDS` instead of backdating the row, against the
rule the other gate files follow. They have to: today's collection is a
`threading.Timer` armed from whatever value `presence` holds at call time, and
unless it actually fires inside the test there is nothing to catch it doing.
After the work there is no timer for the constant to reach and both pass on
their own terms.
"""
import json
import os
import subprocess
import sys
import time

import pytest

from controller_operations import presence

pytestmark = pytest.mark.integration

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COLLECTION_WINDOW = 1.5         # generous against a TTL of 0.2

PAST_THE_WINDOW = 7200

# A fresh process, importing the app the way gunicorn does and doing nothing
# else. No rebuild step, no timers to arm - just an answer.
_PROBE = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
import application
client = application.app.test_client()
print(json.dumps({sid: client.get("/sessions/" + sid).get_json()
                  for sid in sys.argv[2:]}))
"""


def _row(pgdb, sid):
    with pgdb.cursor() as cur:
        cur.execute("SELECT * FROM sessions WHERE id = %s", (sid,))
        return cur.fetchone()


def _move_count(pgdb, sid):
    with pgdb.cursor() as cur:
        cur.execute(
            "SELECT count(*) AS n FROM moves WHERE session_id = %s", (sid,)
        )
        return cur.fetchone()["n"]


def _insert_move(pgdb, sid, ply, uci, san):
    with pgdb.cursor() as cur:
        cur.execute(
            "INSERT INTO moves (session_id, ply, uci, san) VALUES (%s, %s, %s, %s)",
            (sid, ply, uci, san),
        )


def _backdate(pgdb, sid, seconds):
    with pgdb.cursor() as cur:
        cur.execute(
            "UPDATE sessions SET created_at = NOW() - make_interval(secs => %s) "
            "WHERE id = %s",
            (seconds, sid),
        )


def _except_the_stamp(row):
    """The game row minus the one column a disconnect is allowed to write."""
    return {k: v for k, v in row.items() if k != "last_disconnected_at"}


def _wait_for_the_row_to_vanish(pgdb, sid, timeout=COLLECTION_WINDOW):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _row(pgdb, sid) is None:
            return True
        time.sleep(0.05)
    return False


def test_a_game_past_its_deadline_keeps_its_row_its_moves_and_its_status(
    app_module, pgdb, make_session, monkeypatch
):
    """Invariant 7 — presence destroys nothing and writes no status.

    `abandoned` stays a dead value in the CHECK constraint: it was considered
    and deliberately left unwritten.

    `last_disconnected_at` is excluded from the row comparison: invariant 3
    requires it to be written on every disconnect, which is exactly the event
    this test performs. Comparing the whole row asserted the shape rather than
    the invariant and made the two contradict each other. Everything else about
    the game — including `status` and every move — must still be untouched.
    """
    from flask_socketio import SocketIOTestClient

    session = make_session("keep-everything")
    sid = session["sid"]
    _insert_move(pgdb, sid, 1, "e2e4", "e4")
    before = _row(pgdb, sid)

    monkeypatch.setattr(presence, "IDLE_TTL_SECONDS", 0.2)

    client = SocketIOTestClient(app_module.app, app_module.socketio)
    client.emit("join_session", {"sessionId": sid})
    client.disconnect()

    assert not _wait_for_the_row_to_vanish(pgdb, sid), "the session row was deleted"
    after = _row(pgdb, sid)
    assert _except_the_stamp(after) == _except_the_stamp(before), (
        "presence modified the game row"
    )
    assert after["status"] == "active"
    assert _move_count(pgdb, sid) == 1


def test_a_game_left_alone_is_not_touched_by_anything(app_module, pgdb, monkeypatch):
    """Invariant 8 — nothing happens on its own.

    Goes through the real `POST /sessions` rather than `make_session`, because
    `create_session` (`sessions_ops.py:49`) is what arms the collector today and
    a direct INSERT would make this vacuous. Cleans up its own row for the same
    reason: it is not a `make_session` row.
    """
    monkeypatch.setattr(presence, "IDLE_TTL_SECONDS", 0.2)

    created = app_module.app.test_client().post("/sessions", json={"color": "white"})
    assert created.status_code == 201
    sid = created.get_json()["id"]

    try:
        before = _row(pgdb, sid)
        assert before is not None

        assert not _wait_for_the_row_to_vanish(pgdb, sid), (
            "the game was collected with nobody touching it"
        )
        assert _row(pgdb, sid) == before
    finally:
        with pgdb.cursor() as cur:
            cur.execute("DELETE FROM sessions WHERE id = %s", (sid,))


def test_a_freshly_started_process_answers_the_same_with_no_rebuild_step(
    pgdb, make_session
):
    """Invariant 9 — presence carries no in-process state.

    A subprocess rather than `importlib.reload`: reloading inside the
    session-scoped app process would leave `controller/sockets.py` bound to
    functions from a stale module object and contaminate every other gate.

    It asks through `GET /sessions/:id` rather than calling into presence, so
    the gate does not have to guess the arity of a function that does not exist
    yet.
    """
    idle = make_session("fresh-idle")
    live = make_session("fresh-live")
    _backdate(pgdb, idle["sid"], PAST_THE_WINDOW)

    probe = subprocess.run(
        [sys.executable, "-c", _PROBE, BACKEND_DIR, idle["sid"], live["sid"]],
        capture_output=True,
        text=True,
        timeout=60,
        env=os.environ.copy(),
    )
    assert probe.returncode == 0, probe.stderr
    answers = json.loads(probe.stdout)

    assert answers[idle["sid"]]["ended"] is True
    assert answers[live["sid"]]["ended"] is False
