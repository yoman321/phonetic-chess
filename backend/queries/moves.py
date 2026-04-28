"""SQL queries against the `moves` table."""


def insert_move(cur, sid, ply, uci, san, tone_summary=None):
    cur.execute(
        "INSERT INTO moves (session_id, ply, uci, san, tone_summary) "
        "VALUES (%s, %s, %s, %s, %s)",
        (sid, ply, uci, san, tone_summary),
    )


def select_last_move(cur, sid):
    """Return (ply, uci, san, tone_summary) for the latest move, or None."""
    cur.execute(
        "SELECT ply, uci, san, tone_summary FROM moves "
        "WHERE session_id = %s ORDER BY ply DESC LIMIT 1",
        (sid,),
    )
    return cur.fetchone()
