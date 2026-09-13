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
