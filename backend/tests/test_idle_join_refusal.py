"""Invariants 1, 2 and 10 — who may enter a room, and when.

A game whose room has been empty longer than `IDLE_TTL_SECONDS` is refused: the
socket does not enter the room, is not recorded as connected, and is told so.
Inside the window it enters normally. The deadline runs from the last
disconnect, falling back to `created_at` for a game no socket has ever left.

Named for what it asserts. The nearest surviving behaviour from the deleted
`test_presence_delete_race.py` is invariant 1, but it is a different assertion
and a file named for a race that no longer exists would describe nothing.

Two choices worth knowing before editing:

Games are pushed past the deadline by **backdating the row**, never by lowering
`IDLE_TTL_SECONDS`. The constant is read at import (`presence.py:7`), pinned to
3600 by `conftest.py:23`, and `sessions_ops` binds its own copy — a monkeypatch
would assert the plumbing rather than the invariant.

Room membership is asserted by **broadcasting to the room and seeing what the
client received**, not by reading presence's internals. Those internals are what
this work deletes.
"""
import contextlib

import pytest
from flask_socketio import SocketIOTestClient

from controller_operations.helpers import room_name

pytestmark = pytest.mark.integration

PAST_THE_WINDOW = 7200          # conftest pins IDLE_TTL_SECONDS to 3600


def _backdate(pgdb, sid, seconds):
    with pgdb.cursor() as cur:
        cur.execute(
            "UPDATE sessions SET created_at = NOW() - make_interval(secs => %s) "
            "WHERE id = %s",
            (seconds, sid),
        )


def _stamp(pgdb, sid, seconds_ago):
    with pgdb.cursor() as cur:
        cur.execute(
            "UPDATE sessions SET last_disconnected_at = "
            "NOW() - make_interval(secs => %s) WHERE id = %s",
            (seconds_ago, sid),
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


def _connection_count(pgdb, sid):
    with pgdb.cursor() as cur:
        cur.execute(
            "SELECT count(*) AS n FROM session_connections WHERE session_id = %s",
            (sid,),
        )
        return cur.fetchone()["n"]


def _track(pgdb, sid, socket_sid):
    with pgdb.cursor() as cur:
        cur.execute(
            "INSERT INTO session_connections (session_id, socket_sid) "
            "VALUES (%s, %s)",
            (sid, socket_sid),
        )


@contextlib.contextmanager
def _socket(app_module):
    """A connected Socket.IO client, disconnected on the way out.

    The test client runs handlers synchronously in the calling thread
    (`flask_socketio/test_client.py` forces `async_handlers = False`), so an
    emit has finished doing its work by the time it returns.
    """
    client = SocketIOTestClient(app_module.app, app_module.socketio)
    client.get_received()                     # drop the connect frames
    try:
        yield client
    finally:
        if client.is_connected():
            client.disconnect()


def _names(received):
    return [pkt["name"] for pkt in received]


def _in_the_room(app_module, client, sid):
    """Broadcast to the room and report whether this client is in it."""
    app_module.socketio.emit("move", {"fen": "probe"}, to=room_name(sid))
    return [pkt for pkt in client.get_received() if pkt["name"] == "move"] != []


def test_a_socket_joining_an_idle_game_is_refused_and_told_why(
    app_module, pgdb, make_session
):
    """Invariant 1.

    All three halves are load-bearing. A refusal that does not reach the client
    reproduces the exact symptom this work exists to remove: a reconnecting
    player sitting in a game, receiving no moves, and told nothing.
    """
    session = make_session("idle-refused")
    sid = session["sid"]
    _backdate(pgdb, sid, PAST_THE_WINDOW)

    with _socket(app_module) as client:
        client.emit("join_session", {"sessionId": sid})
        received = client.get_received()

        assert "game_ended" in _names(received), (
            f"the socket was not told the game ended; got {_names(received)}"
        )
        assert not _in_the_room(app_module, client, sid)
        assert _connection_count(pgdb, sid) == 0


def test_a_socket_joining_inside_the_window_enters_normally(
    app_module, pgdb, make_session
):
    """Invariant 2, the admitting half."""
    session = make_session("idle-admitted")
    sid = session["sid"]

    with _socket(app_module) as client:
        client.emit("join_session", {"sessionId": sid})

        assert "game_ended" not in _names(client.get_received())
        assert _connection_count(pgdb, sid) == 1
        assert _in_the_room(app_module, client, sid)


def test_the_deadline_runs_from_the_last_disconnect_not_from_creation(
    app_module, pgdb, make_session
):
    """Invariant 2 — the COALESCE reads liveness first, creation last.

    An hours-old game somebody left a moment ago is still live.
    """
    session = make_session("idle-recent")
    sid = session["sid"]
    _backdate(pgdb, sid, PAST_THE_WINDOW)
    _stamp(pgdb, sid, 0)

    with _socket(app_module) as client:
        client.emit("join_session", {"sessionId": sid})

        assert "game_ended" not in _names(client.get_received())
        assert _connection_count(pgdb, sid) == 1


def test_a_game_no_socket_has_ever_left_is_judged_from_creation(
    app_module, pgdb, make_session
):
    """Invariant 2 — the fallback. A NULL stamp means nobody ever disconnected,
    so an old game nobody opened is judged from `created_at` and is refused."""
    session = make_session("idle-never-open")
    sid = session["sid"]
    _backdate(pgdb, sid, PAST_THE_WINDOW)

    assert _seconds_since_stamp(pgdb, sid) is None

    with _socket(app_module) as client:
        client.emit("join_session", {"sessionId": sid})
        assert "game_ended" in _names(client.get_received())


def test_a_restart_gives_a_game_that_had_connections_a_fresh_window(
    app_module, pgdb, make_session
):
    """Invariant 10.

    The boot wipe is one `DELETE FROM session_connections`, which fires the row
    trigger once per row, so every game that had a socket gets its clock
    restarted and a full fresh window. A game that had none keeps whatever stamp
    it had, so a correctly-ended game stays ended — that negative half is what
    stops the wipe resurrecting everything.
    """
    from controller_operations import presence

    occupied = make_session("boot-occupied")
    empty = make_session("boot-empty")
    for session in (occupied, empty):
        _backdate(pgdb, session["sid"], PAST_THE_WINDOW)
        _stamp(pgdb, session["sid"], PAST_THE_WINDOW)
    _track(pgdb, occupied["sid"], "sock-from-before-the-restart")

    presence.clear_connections()

    assert _connection_count(pgdb, occupied["sid"]) == 0
    assert _seconds_since_stamp(pgdb, occupied["sid"]) < 5
    assert _seconds_since_stamp(pgdb, empty["sid"]) > PAST_THE_WINDOW - 60

    with _socket(app_module) as client:
        client.emit("join_session", {"sessionId": occupied["sid"]})
        assert "game_ended" not in _names(client.get_received())

    with _socket(app_module) as client:
        client.emit("join_session", {"sessionId": empty["sid"]})
        assert "game_ended" in _names(client.get_received())
