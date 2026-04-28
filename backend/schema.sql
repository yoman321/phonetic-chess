CREATE TABLE IF NOT EXISTS sessions (
    id           TEXT        PRIMARY KEY,
    fen          TEXT        NOT NULL,
    pgn          TEXT        NOT NULL DEFAULT '',
    white_token  TEXT,
    black_token  TEXT,
    status       TEXT        NOT NULL DEFAULT 'active'
                            CHECK (status IN ('active', 'white_won', 'black_won', 'draw', 'abandoned')),
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

ALTER TABLE moves ADD COLUMN IF NOT EXISTS tone_summary TEXT;

CREATE INDEX IF NOT EXISTS idx_moves_session_id ON moves(session_id);
