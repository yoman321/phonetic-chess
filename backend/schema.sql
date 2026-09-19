CREATE TABLE IF NOT EXISTS sessions (
    id           TEXT        PRIMARY KEY,
    fen          TEXT        NOT NULL,
    pgn          TEXT        NOT NULL DEFAULT '',
    white_token  TEXT,
    black_token  TEXT,
    status       TEXT        NOT NULL DEFAULT 'active'
                            CHECK (status IN ('active', 'white_won', 'black_won', 'draw')),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sessions_updated_at ON sessions(updated_at);

CREATE TABLE IF NOT EXISTS moves (
    session_id      TEXT        NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    ply             INTEGER     NOT NULL,
    uci             TEXT        NOT NULL,
    san             TEXT        NOT NULL,
    tone_summary    TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (session_id, ply)
);

CREATE INDEX IF NOT EXISTS idx_moves_session_id ON moves(session_id);

ALTER TABLE moves ADD COLUMN IF NOT EXISTS player_text TEXT;
ALTER TABLE moves ADD COLUMN IF NOT EXISTS pre_move_fen TEXT;
ALTER TABLE moves ADD COLUMN IF NOT EXISTS prior_tone TEXT;
ALTER TABLE moves ADD COLUMN IF NOT EXISTS intent TEXT;
ALTER TABLE moves ADD COLUMN IF NOT EXISTS rationale TEXT;

ALTER TABLE sessions ADD COLUMN IF NOT EXISTS last_disconnected_at TIMESTAMPTZ;

-- Narrows the CHECK above on a database that already exists, which
-- CREATE TABLE IF NOT EXISTS cannot do. 'abandoned' was never written by any
-- code path: ended-ness is computed from last_disconnected_at, never stored.
-- Drop-then-add rather than ADD IF NOT EXISTS so a re-run replaces the old
-- five-value constraint instead of leaving it in place.
ALTER TABLE sessions DROP CONSTRAINT IF EXISTS sessions_status_check;
ALTER TABLE sessions ADD CONSTRAINT sessions_status_check
    CHECK (status IN ('active', 'white_won', 'black_won', 'draw'));

CREATE TABLE IF NOT EXISTS session_connections (
    session_id  TEXT        NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    socket_sid  TEXT        NOT NULL,
    joined_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (session_id, socket_sid)
);

-- the disconnect looks up by socket alone; the PK is no use for that, since a
-- btree is ordered by its first column and one socket id can sit under any game
CREATE INDEX IF NOT EXISTS idx_session_connections_socket
    ON session_connections(socket_sid);

-- Unconditional by design. A stamp written while somebody is still connected is
-- never read: the predicate tests liveness first and only falls through to this
-- column once the table is empty for that game, by which point the genuinely
-- last disconnect has overwritten it. Guarding on "was this the last row?" loses
-- the write when two sockets of one game disconnect concurrently.
CREATE OR REPLACE FUNCTION session_mark_last_disconnect() RETURNS TRIGGER AS $$
BEGIN
    UPDATE sessions SET last_disconnected_at = NOW() WHERE id = OLD.session_id;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_session_last_disconnect ON session_connections;
CREATE TRIGGER trg_session_last_disconnect
    AFTER DELETE ON session_connections
    FOR EACH ROW EXECUTE FUNCTION session_mark_last_disconnect();

CREATE TABLE IF NOT EXISTS llm_calls (
    id                BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    session_id        TEXT        NOT NULL,
    ply               INTEGER     NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    model             TEXT        NOT NULL,
    reasoning_effort  TEXT        NOT NULL,
    max_retries       INTEGER     NOT NULL,
    sdk_max_retries   INTEGER     NOT NULL,
    fen               TEXT        NOT NULL,
    player_text       TEXT        NOT NULL,
    candidate_ucis    TEXT[]      NOT NULL,
    prior_tone        TEXT,
    outcome           TEXT        NOT NULL
                                  CHECK (outcome IN ('ok', 'exhausted', 'transport',
                                                     'unexpected')),
    attempts          INTEGER     NOT NULL,
    latency_ms        INTEGER     NOT NULL,
    chosen_uci        TEXT,
    off_list          BOOLEAN,
    intent            TEXT,
    rationale         TEXT,
    tone_summary      TEXT
);

-- CREATE TABLE IF NOT EXISTS leaves the old CHECK in place on an existing
-- database. Replace it so reapplying this file also migrates live tables.
ALTER TABLE llm_calls DROP CONSTRAINT IF EXISTS llm_calls_outcome_check;
ALTER TABLE llm_calls ADD CONSTRAINT llm_calls_outcome_check
    CHECK (outcome IN ('ok', 'exhausted', 'transport', 'unexpected'));

ALTER TABLE llm_calls ADD COLUMN IF NOT EXISTS explain_requests INTEGER NOT NULL DEFAULT 0;
ALTER TABLE llm_calls ADD COLUMN IF NOT EXISTS explain_prompt_tokens INTEGER;
ALTER TABLE llm_calls ADD COLUMN IF NOT EXISTS explain_completion_tokens INTEGER;
ALTER TABLE llm_calls ADD COLUMN IF NOT EXISTS explain_latency_ms INTEGER;

CREATE INDEX IF NOT EXISTS idx_llm_calls_session
    ON llm_calls(session_id, ply);
CREATE INDEX IF NOT EXISTS idx_llm_calls_created
    ON llm_calls(created_at);
CREATE INDEX IF NOT EXISTS idx_llm_calls_outcome
    ON llm_calls(outcome);

CREATE TABLE IF NOT EXISTS llm_call_attempts (
    call_id            BIGINT      NOT NULL
                                   REFERENCES llm_calls(id) ON DELETE CASCADE,
    attempt            INTEGER     NOT NULL,
    outcome            TEXT        NOT NULL
                                   CHECK (outcome IN ('ok', 'bad_json',
                                                      'missing_key', 'invalid_uci',
                                                      'bad_shape', 'transport',
                                                      'unexpected')),
    latency_ms         INTEGER     NOT NULL,
    sdk_retries        INTEGER,
    status_code        INTEGER,
    raw_content        TEXT,
    error_detail       TEXT,
    prompt_tokens      INTEGER,
    completion_tokens  INTEGER,
    reasoning_tokens   INTEGER,
    PRIMARY KEY (call_id, attempt)
);

ALTER TABLE llm_call_attempts
    DROP CONSTRAINT IF EXISTS llm_call_attempts_outcome_check;
ALTER TABLE llm_call_attempts ADD CONSTRAINT llm_call_attempts_outcome_check
    CHECK (outcome IN ('ok', 'bad_json', 'missing_key', 'invalid_uci',
                       'bad_shape', 'transport', 'unexpected'));

DROP VIEW IF EXISTS llm_call_metrics;
CREATE VIEW llm_call_metrics AS
SELECT
    date_trunc('day', c.created_at)                           AS day,
    c.model,
    count(*)                                                  AS calls,
    count(*) FILTER (WHERE c.outcome = 'ok')                  AS ok,
    count(*) FILTER (WHERE c.outcome = 'exhausted')           AS exhausted,
    count(*) FILTER (WHERE c.outcome = 'transport')           AS transport,
    count(*) FILTER (WHERE c.outcome = 'unexpected')          AS unexpected,
    count(*) FILTER (WHERE c.attempts > 1)                    AS needed_a_retry,
    sum(c.attempts)                                           AS loop_attempts,
    sum(a.http_requests)                                      AS http_requests,
    count(*) FILTER (WHERE c.off_list)                        AS off_list,
    percentile_cont(0.5) WITHIN GROUP (ORDER BY c.latency_ms) AS p50_ms,
    percentile_cont(0.95) WITHIN GROUP (ORDER BY c.latency_ms) AS p95_ms,
    sum(a.prompt_tokens)                                      AS prompt_tokens,
    sum(a.completion_tokens)                                  AS completion_tokens,
    sum(a.reasoning_tokens)                                   AS reasoning_tokens
FROM llm_calls c
JOIN LATERAL (
    SELECT
        sum(1 + COALESCE(t.sdk_retries, c.sdk_max_retries)) AS http_requests,
        sum(t.prompt_tokens)                                 AS prompt_tokens,
        sum(t.completion_tokens)                             AS completion_tokens,
        sum(t.reasoning_tokens)                              AS reasoning_tokens
    FROM llm_call_attempts t
    WHERE t.call_id = c.id
) a ON TRUE
GROUP BY 1, 2;
