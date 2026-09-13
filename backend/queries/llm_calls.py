"""SQL queries against the LLM call log tables."""


def insert_call(cur, record):
    """Insert one completed LLM call and return its generated id."""
    cur.execute(
        "INSERT INTO llm_calls ("
        "session_id, ply, model, reasoning_effort, max_retries, "
        "sdk_max_retries, fen, player_text, candidate_ucis, prior_tone, "
        "outcome, attempts, latency_ms, chosen_uci, off_list, intent, "
        "rationale, tone_summary"
        ") VALUES ("
        "%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
        "%s, %s, %s"
        ") RETURNING id",
        (
            record["session_id"],
            record["ply"],
            record["model"],
            record["reasoning_effort"],
            record["max_retries"],
            record["sdk_max_retries"],
            record["fen"],
            record["player_text"],
            record["candidate_ucis"],
            record["prior_tone"],
            record["outcome"],
            record["attempts"],
            record["latency_ms"],
            record["chosen_uci"],
            record["off_list"],
            record["intent"],
            record["rationale"],
            record["tone_summary"],
        ),
    )
    return cur.fetchone()["id"]


def insert_attempts(cur, call_id, attempts):
    """Insert every attempt for one call in a single database round trip."""
    cur.executemany(
        "INSERT INTO llm_call_attempts ("
        "call_id, attempt, outcome, latency_ms, sdk_retries, status_code, "
        "raw_content, error_detail, prompt_tokens, completion_tokens, "
        "reasoning_tokens"
        ") VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        [
            (
                call_id,
                attempt["attempt"],
                attempt["outcome"],
                attempt["latency_ms"],
                attempt["sdk_retries"],
                attempt["status_code"],
                attempt["raw_content"],
                attempt["error_detail"],
                attempt["prompt_tokens"],
                attempt["completion_tokens"],
                attempt["reasoning_tokens"],
            )
            for attempt in attempts
        ],
    )
