# Phonetic Chess — Project Summary

A two-player web chess app where each side **types a short phrase** to describe the mood/intent of their next move, and an LLM (Groq Llama-3.3-70B) picks the actual chess move that matches that tone from a list of engine-vetted candidates. The opponent can also drag pieces normally; the tone-based path is the novel feature.

## Stack

- **Backend**: Python 3, Flask + Flask-SocketIO, psycopg 3 (PostgreSQL), `python-chess` for rules, vendored **Sunfish** for static eval / move ranking, OpenAI SDK pointed at Groq's OpenAI-compatible endpoint.
- **Frontend**: React 19 + Vite 8, `react-router-dom` v7, `react-chessboard` v5, `chess.js` for client-side legal-move highlighting, `socket.io-client`.
- **Infra**: `docker-compose.yml` with three services — `db` (Postgres with `schema.sql` baked in), `backend`, `frontend` (nginx serving the Vite build + reverse-proxying `/api` and websockets to backend). Single host port: `8080`.

## Repo Layout

```
phonetic_chess/
├── docker-compose.yml
├── .env.example                       # documents GROQ_API_KEY + LLM_* overrides
├── backend/
│   ├── application.py                 # Flask app factory, registers BPs + sockets, inits presence
│   ├── schema.sql                     # sessions + moves tables
│   ├── Dockerfile, db.Dockerfile
│   ├── requirements.txt
│   ├── controller/                    # HTTP + socket entry points (thin)
│   │   ├── sessions.py                # /sessions, /sessions/<sid>, /join, /move, /say
│   │   └── sockets.py                 # join_session, disconnect
│   ├── controller_operations/         # business logic (raises ApiError on failure)
│   │   ├── sessions_ops.py            # create/get/join/make_move/say_move
│   │   ├── engine.py                  # Sunfish-backed evaluate() + rank_moves()
│   │   ├── llm.py                     # pick_move_with_llm() — Groq call + retry loop
│   │   ├── presence.py                # idle-session cleanup via threading.Timer
│   │   ├── helpers.py                 # IDs, tokens, PGN appender, status_from_board
│   │   └── errors.py                  # ApiError class
│   ├── queries/                       # SQL only
│   │   ├── sessions.py
│   │   └── moves.py
│   ├── error_logger/logger.py         # daily-rotated ERROR log + stdout INFO
│   ├── error_file/                    # YYYY-MM-DD.log files land here
│   └── vendor/sunfish/                # vendored chess engine (used for eval + top-N)
└── frontend/
    ├── nginx.conf                     # static + /api proxy + /socket.io proxy
    ├── Dockerfile
    ├── vite.config.js, eslint.config.js, package.json
    └── src/
        ├── App.jsx                    # routes: / → Menu, /sessionId/:id → GameView
        ├── api.js                     # fetch wrappers for the 4 backend routes
        ├── socket.js                  # singleton socket.io client (autoConnect off)
        ├── storage.js                 # sessionStorage helpers for player token
        └── modules/
            ├── Menu/                  # landing screen + Create/Join modals
            ├── GameView/              # main game UI, wires board + chat + sockets
            ├── Chessboard/            # thin react-chessboard wrapper
            ├── Chatbox/               # message list, draft input, thinking dots
            ├── EvalBar/               # centipawn bar driven by evalCp
            ├── Avatar/                # side-coloured avatar
            └── FatalError/            # error screen w/ back button
```

## Data Model (`backend/schema.sql`)

- **sessions**: `id` (8-char base36, PK), `fen`, `pgn`, `white_token`, `black_token`, `status` ∈ {`active`, `white_won`, `black_won`, `draw`, `abandoned`}, timestamps. Index on `updated_at`.
- **moves**: `(session_id, ply)` PK, `uci`, `san`, `tone_summary` (nullable, only set for LLM-picked moves), `created_at`. Cascades on session delete.

## HTTP API

All return JSON. Errors come from the central `ApiError` handler registered on the blueprint.

| Method | Path | Body | Purpose |
|---|---|---|---|
| POST | `/sessions` | `{color?: "white"\|"black"\|"random"}` | Create game, returns `{id, fen, status, created_at, color, playerToken}` |
| GET | `/sessions/<sid>` | – | Returns session summary + computed `evalCp` |
| POST | `/sessions/<sid>/join` | `{playerToken?: string}` | Claims white/black slot (or recognises existing token), returns `{color, playerToken}` |
| POST | `/sessions/<sid>/move` | `{uci, playerToken}` | Manual move (drag/click on board) |
| POST | `/sessions/<sid>/say` | `{text, playerToken}` | Tone-based move — LLM picks UCI from Sunfish top-15 + emits tone_summary |

After any move, the server broadcasts a `move` socket event to room `session:<sid>`. The `/say` flow also emits `thinking` events (`{on: true/false, side, status}`) so the opponent's UI shows typing dots.

## The Tone-Based Move Flow (`say_move` → `pick_move_with_llm`)

1. Validate token, turn, and that the opponent has moved since this player's last move (enforced via `moves.ply` parity, plus the special "white moves first" case when no prior moves).
2. Call `engine.rank_moves(board, top_n=15)` — Sunfish static eval of every legal move; top 15 returned as `(uci, san)` pairs. Falls back to arbitrary 15 if Sunfish chokes.
3. Build the prompt: system message defines the tone→move mapping rules; user message includes prior tone summary, opponent's last move, the new player text, FEN, and the candidate list.
4. Call Groq via the OpenAI SDK (`response_format=json_object`, temp 0.7). The SDK already retries 3× on 429/connect/timeout — those bubble up as `TimeoutError` → `llm_unavailable` (502).
5. Validate the returned UCI is in `all_legal_ucis` (NOT just the candidate set — candidates are advisory). On bad JSON / bad UCI, retry up to `LLM_MAX_RETRIES=3` with exponential backoff, emitting a `thinking` event with `status: "retrying"` so the UI can show "wrong move, retrying".
6. On final failure, raise `llm_bad_response` (502, UI prompts user to retry).
7. On success, push the move, write `tone_summary` into the `moves` row, emit a `move` event including `intent` + `rationale` + `priorTone` so the frontend can show a "?" explanation popover next to the move bubble.

## Presence / Cleanup (`controller_operations/presence.py`)

- Module-level `_active_sids` (sid → set of socket sids) and `_cleanup_timers` (sid → `threading.Timer`), guarded by a single `threading.Lock`.
- On socket join: `track_join` + `cancel_cleanup`. On disconnect: any sessions that emptied get `schedule_cleanup` (`IDLE_TTL_SECONDS=600`, env-configurable).
- `_delete_session` re-checks under the lock that no one rejoined before firing the `DELETE`.
- At startup, `reschedule_existing_sessions()` schedules a cleanup for every persisted session so a crash/restart doesn't leave orphans.

## Frontend State Machine (`GameView.jsx`)

- On mount: `getSession` → load FEN into a `chess.js` instance → `joinSession` (passing any token in `sessionStorage`) → store returned token + color.
- Subscribes to `move` (updates board, eval bar, chat) and `thinking` (drives typing-dot subcomponents in `Chatbox`).
- Drag/click moves: optimistically push to local `chess.js`, then `postMove`; on rejection, `game.undo()` and append a system message.
- Tone messages: optimistically push the user's text bubble (with `pendingId`); on `llm_bad_response` or `llm_unavailable`, remove the bubble, restore the draft, and show a `subscriptError` in the chat.
- The board orientation is locked to the player's color.

## Env Vars

- `GROQ_API_KEY` (required) — passed via `backend/.env` + `env_file:` in compose.
- `DATABASE_URL` — set in `docker-compose.yml` to point at the `db` service (overrides any value in `backend/.env`).
- Optional: `LLM_MODEL` (default `llama-3.3-70b-versatile`), `LLM_TIMEOUT` (30), `LLM_MAX_RETRIES` (clamped to 3), `LLM_BACKOFF_BASE` (0.5), `IDLE_TTL_SECONDS` (600).

## Running

- **Docker**: `docker compose up --build` from repo root → http://localhost:8080
- **Local backend**: `cd backend && python application.py` (port 5001)
- **Local frontend**: `cd frontend && npm run dev` (Vite default port)
- Frontend defaults to `http://127.0.0.1:5001` for both REST and socket if `VITE_API_BASE` / `VITE_SOCKET_URL` are unset.

## Notable Design Choices

- **Sunfish for eval, not search**: only `Position.value()` is called for ranking — no search, no castling-rights tracking needed.
- **Candidate list is advisory**: LLM may pick any legal UCI; this is a guardrail that lets it pick a slightly "off" move if it better fits a weird tone, while keeping the top suggestions sound.
- **Thin controllers**: HTTP routes (`controller/`) only parse + dispatch; all logic lives in `controller_operations/`. The single `ApiError` handler on the blueprint converts exceptions to JSON.
- **No auth, just tokens**: player identity = opaque token stored in `sessionStorage` per session id. The `/join` route either returns the matching color for a known token or claims the next free slot.
- **Daily log file**: `error_logger.logger` writes ERROR-level lines to `backend/error_file/YYYY-MM-DD.log` and mirrors INFO+ to stdout.

## Where to Look First When Resuming

- New backend feature touching gameplay: start in `controller_operations/sessions_ops.py`.
- LLM prompt or retry behaviour: `controller_operations/llm.py`.
- Frontend wiring for sockets / API / state: `frontend/src/modules/GameView/GameView.jsx`.
- Schema / queries: `backend/schema.sql` + `backend/queries/`.
- Deployment / env: `docker-compose.yml` + `backend/Dockerfile` + `frontend/Dockerfile` + `frontend/nginx.conf`.
