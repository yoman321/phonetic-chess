"""SQL queries against the `moves` table."""


def insert_move(
    cur, sid, ply, uci, san, tone_summary=None, player_text=None,
    pre_move_fen=None, prior_tone=None,
):
    cur.execute(
        "INSERT INTO moves (session_id, ply, uci, san, tone_summary, "
        "player_text, pre_move_fen, prior_tone) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (sid, ply, uci, san, tone_summary, player_text, pre_move_fen, prior_tone),
    )


def select_last_move(cur, sid):
    """Return (ply, uci, san, tone_summary) for the latest move, or None."""
    cur.execute(
        "SELECT ply, uci, san, tone_summary FROM moves "
        "WHERE session_id = %s ORDER BY ply DESC LIMIT 1",
        (sid,),
    )
    return cur.fetchone()


def get_for_explain(cur, session_id, ply):
    cur.execute(
        "SELECT uci, san, player_text, pre_move_fen, prior_tone, intent, rationale "
        "FROM moves WHERE session_id = %s AND ply = %s",
        (session_id, ply),
    )
    return cur.fetchone()


def save_explanation(cur, session_id, ply, intent, rationale):
    cur.execute(
        "UPDATE moves SET intent = %s, rationale = %s "
        "WHERE session_id = %s AND ply = %s",
        (intent, rationale, session_id, ply),
    )
