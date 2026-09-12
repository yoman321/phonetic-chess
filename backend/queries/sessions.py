"""SQL queries against the `sessions` table."""


def insert_session(cur, sid, fen, color, token):
    """Insert a new session, assigning the chosen color to the given token.

    Returns the inserted row (id, fen, status, created_at).
    """
    token_column = "white_token" if color == "white" else "black_token"
    cur.execute(
        f"INSERT INTO sessions (id, fen, {token_column}) VALUES (%s, %s, %s) "
        "RETURNING id, fen, status, created_at",
        (sid, fen, token),
    )
    return cur.fetchone()


def select_session_summary(cur, sid, ttl_seconds):
    """Public session state. Returns whether both colours are claimed, never
    the token values — GET /sessions/:id is unauthenticated.

    `ended` is the idle deadline, computed here and never stored: nobody is
    connected and the room has been empty longer than ttl_seconds. It is not
    `status` — a game won by checkmate is over in a different sense.
    """
    cur.execute(
        "SELECT s.id, s.fen, s.pgn, s.status, s.created_at, s.updated_at, "
        "(s.white_token IS NOT NULL AND s.black_token IS NOT NULL) AS both_joined, "
        "(NOT EXISTS ("
        "    SELECT 1 FROM session_connections c WHERE c.session_id = s.id"
        " ) AND COALESCE(s.last_disconnected_at, s.created_at) "
        "   < NOW() - make_interval(secs => %s)) AS ended "
        "FROM sessions s WHERE s.id = %s",
        (ttl_seconds, sid),
    )
    return cur.fetchone()


def select_tokens(cur, sid):
    """Unlocked read. Safe because tokens never change once set: the setters
    below are called only when the column is NULL, nothing else writes those
    columns, and nothing removes the row."""
    cur.execute(
        "SELECT white_token, black_token FROM sessions WHERE id = %s",
        (sid,),
    )
    return cur.fetchone()


def select_tokens_for_update(cur, sid):
    cur.execute(
        "SELECT white_token, black_token FROM sessions WHERE id = %s FOR UPDATE",
        (sid,),
    )
    return cur.fetchone()


def set_white_token(cur, sid, token):
    cur.execute(
        "UPDATE sessions SET white_token = %s WHERE id = %s", (token, sid)
    )


def set_black_token(cur, sid, token):
    cur.execute(
        "UPDATE sessions SET black_token = %s WHERE id = %s", (token, sid)
    )


def select_state_for_update(cur, sid):
    """NO KEY UPDATE, not UPDATE: this lock is held across the LLM call, and
    a plain FOR UPDATE would block the FOR KEY SHARE that Postgres takes on
    this row to check a session_connections foreign key — so a socket joining
    a game with a phrase in flight would wait on it. It still conflicts with
    itself, so mover-versus-mover exclusion is unchanged, and update_after_move
    touches no key column."""
    cur.execute(
        "SELECT fen, pgn, status, white_token, black_token FROM sessions "
        "WHERE id = %s FOR NO KEY UPDATE",
        (sid,),
    )
    return cur.fetchone()


def update_after_move(cur, sid, fen, pgn, status):
    cur.execute(
        "UPDATE sessions SET fen = %s, pgn = %s, status = %s, "
        "updated_at = NOW() WHERE id = %s",
        (fen, pgn, status, sid),
    )

