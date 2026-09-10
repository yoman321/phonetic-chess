import os
import threading

from error_logger import logger
from queries import sessions as sessions_q

IDLE_TTL_SECONDS = int(os.environ.get("IDLE_TTL_SECONDS", "600"))  # default 10 min

_presence_lock = threading.Lock()
_active_sids = {}      # session_id -> set of socketio sids in the room
_cleanup_timers = {}   # session_id -> threading.Timer
_db = None             # zero-arg connection factory, not a connection


def init(db):
    global _db
    _db = db


def _delete_session(sid):
    with _presence_lock:
        _cleanup_timers.pop(sid, None)
        if _active_sids.get(sid):
            return  # someone reconnected just before deletion fired
        # The DELETE stays inside the lock. Released first, a track_join landing
        # between the re-check and the DELETE would leave a client sitting in a
        # room whose session no longer exists.
        with _db() as pg, pg.cursor() as cur:
            sessions_q.delete_session(cur, sid)
    logger.info("[cleanup] deleted idle session %s", sid)


def schedule_cleanup(sid):
    with _presence_lock:
        existing = _cleanup_timers.pop(sid, None)
        if existing:
            existing.cancel()
        timer = threading.Timer(IDLE_TTL_SECONDS, _delete_session, args=(sid,))
        timer.daemon = True
        _cleanup_timers[sid] = timer
        timer.start()


def cancel_cleanup(sid):
    with _presence_lock:
        existing = _cleanup_timers.pop(sid, None)
    if existing:
        existing.cancel()


def reschedule_existing_sessions():
    with _db() as pg, pg.cursor() as cur:
        ids = sessions_q.select_all_session_ids(cur)
    for sid in ids:
        schedule_cleanup(sid)


def track_join(sid, socket_sid):
    with _presence_lock:
        _active_sids.setdefault(sid, set()).add(socket_sid)


def remove_socket(socket_sid):
    """Drop a socket from all rooms; return list of sessions that became empty."""
    now_empty = []
    with _presence_lock:
        for sid, members in list(_active_sids.items()):
            if socket_sid in members:
                members.discard(socket_sid)
                if not members:
                    _active_sids.pop(sid, None)
                    now_empty.append(sid)
    return now_empty
