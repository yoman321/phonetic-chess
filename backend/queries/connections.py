"""SQL queries against the `session_connections` table."""


def track(cur, sid, socket_sid):
    """Record a socket as connected to a session.

    ON CONFLICT DO NOTHING because a client that emits join_session twice for
    one room is a duplicate, not an error, and the original joined_at is the
    honest one.
    """
    cur.execute(
        "INSERT INTO session_connections (session_id, socket_sid) "
        "VALUES (%s, %s) ON CONFLICT DO NOTHING",
        (sid, socket_sid),
    )


def release(cur, socket_sid):
    """Drop every row for a socket, across all the rooms it had joined.

    The AFTER DELETE trigger stamps sessions.last_disconnected_at once per row
    deleted, so each affected game gets its own stamp.
    """
    cur.execute(
        "DELETE FROM session_connections WHERE socket_sid = %s", (socket_sid,)
    )


def release_all(cur):
    """The boot wipe. Sockets do not survive the process that held them, so
    every row is stale at startup. The same trigger fires once per row, which
    restarts the idle clock of every game that had connections."""
    cur.execute("DELETE FROM session_connections")


def is_idle(cur, sid, ttl_seconds):
    """True when nobody is connected and the room has been empty longer than
    ttl_seconds.

    COALESCE covers the game created and never opened, which has no stamp and
    is judged from created_at. With the trigger unconditional that is the only
    state a NULL stamp can mean.

    Returns False for a session id that does not exist — there is no row to
    compare, and callers check existence themselves.
    """
    cur.execute(
        "SELECT NOT EXISTS ("
        "    SELECT 1 FROM session_connections c WHERE c.session_id = s.id"
        ") AND COALESCE(s.last_disconnected_at, s.created_at) "
        "    < NOW() - make_interval(secs => %s) AS idle "
        "FROM sessions s WHERE s.id = %s",
        (ttl_seconds, sid),
    )
    row = cur.fetchone()
    return bool(row and row["idle"])
