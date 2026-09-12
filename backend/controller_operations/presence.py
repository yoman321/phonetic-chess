"""Who is connected to which game, and how long each game has been empty.

Both live in Postgres: `session_connections` holds a row per socket per room,
and the AFTER DELETE trigger on it stamps `sessions.last_disconnected_at`. This
module holds no state of its own and starts nothing — no lock, no dicts, no
timers, no boot-time rebuild — so a restart loses nothing it has to reconstruct
and two callers for unrelated games never contend.

Nothing here deletes a game. A game that goes quiet keeps its row and its moves;
whether it has passed its deadline is computed when someone tries to use it.
"""
import os

from queries import connections as connections_q

IDLE_TTL_SECONDS = int(os.environ.get("IDLE_TTL_SECONDS", "600"))  # default 10 min

_db = None  # zero-arg connection factory, not a connection


def init(db):
    global _db
    _db = db


def track_join(sid, socket_sid):
    with _db() as pg, pg.cursor() as cur:
        connections_q.track(cur, sid, socket_sid)


def remove_socket(socket_sid):
    """Drop a socket from every room it had joined.

    Returns nothing. The trigger stamps each affected game, so there is no list
    of newly-empty sessions for a caller to act on any more.
    """
    with _db() as pg, pg.cursor() as cur:
        connections_q.release(cur, socket_sid)


def is_idle(sid, ttl_seconds=None):
    """True when nobody is connected to the game and the room has been empty
    longer than the window.

    The default is resolved per call, not bound at def time, so a test that
    rebinds IDLE_TTL_SECONDS on this module is actually obeyed.

    Callers already holding a cursor should use `queries.connections.is_idle`
    directly rather than opening a second connection — the move paths evaluate
    it under their own row lock.
    """
    if ttl_seconds is None:
        ttl_seconds = IDLE_TTL_SECONDS
    with _db() as pg, pg.cursor() as cur:
        return connections_q.is_idle(cur, sid, ttl_seconds)


def clear_connections():
    """The boot wipe. Sockets do not outlive the process that held them, so
    every row is stale at startup and rows orphaned by a crash go with them.

    This also restarts the idle clock: the DELETE fires the row trigger once per
    row, so every game that had connections gets a fresh full window from boot,
    while a game that had none keeps its stamp and stays correctly ended.
    """
    with _db() as pg, pg.cursor() as cur:
        connections_q.release_all(cur)
