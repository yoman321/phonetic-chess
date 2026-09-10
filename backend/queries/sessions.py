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


def select_session_summary(cur, sid):
    """Public session state. Returns whether both colours are claimed, never
    the token values — GET /sessions/:id is unauthenticated."""
    cur.execute(
        "SELECT id, fen, pgn, status, created_at, updated_at, "
        "(white_token IS NOT NULL AND black_token IS NOT NULL) AS both_joined "
        "FROM sessions WHERE id = %s",
        (sid,),
    )
    return cur.fetchone()


def select_tokens(cur, sid):
    """Unlocked read. Safe because tokens never change once set: the setters
    below are called only when the column is NULL, nothing else writes those
    columns, and only delete_session removes the row."""
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
    cur.execute(
        "SELECT fen, pgn, status, white_token, black_token FROM sessions "
        "WHERE id = %s FOR UPDATE",
        (sid,),
    )
    return cur.fetchone()


def update_after_move(cur, sid, fen, pgn, status):
    cur.execute(
        "UPDATE sessions SET fen = %s, pgn = %s, status = %s, "
        "updated_at = NOW() WHERE id = %s",
        (fen, pgn, status, sid),
    )


def delete_session(cur, sid):
    cur.execute("DELETE FROM sessions WHERE id = %s", (sid,))


def select_all_session_ids(cur):
    cur.execute("SELECT id FROM sessions")
    return [r["id"] for r in cur]
