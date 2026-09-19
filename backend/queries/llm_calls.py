"""SQL queries against the LLM call log tables."""


def increment_explain_requests(cur, session_id, ply):
    cur.execute(
        "UPDATE llm_calls SET explain_requests = explain_requests + 1 "
        "WHERE session_id = %s AND ply = %s AND outcome = 'ok'",
        (session_id, ply),
    )


def save_explanation(cur, session_id, ply, intent, rationale, usage, latency_ms):
    cur.execute(
        "UPDATE llm_calls SET intent = %s, rationale = %s, "
        "explain_prompt_tokens = %s, explain_completion_tokens = %s, "
        "explain_latency_ms = %s "
        "WHERE session_id = %s AND ply = %s AND outcome = 'ok'",
        (
            intent, rationale,
            usage.prompt_tokens if usage is not None else None,
            usage.completion_tokens if usage is not None else None,
            latency_ms, session_id, ply,
        ),
    )


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
