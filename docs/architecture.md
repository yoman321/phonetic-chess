# Architecture

Phonetic Chess is a two-player online chess app with one novel input path: instead
of moving a piece, a player types a short phrase describing the *mood* of their
next move ("play it safe", "go for the throat"). The backend ranks legal moves
with a vendored Sunfish static eval, hands the top candidates plus board context
to an LLM, and plays back the move whose character best fits the phrase — along
with a one-sentence rationale the opponent can open.

Both input paths (drag a piece, or type a phrase) write to the same state machine
and broadcast the same `move` event.

---

## Repo layout

```
backend/
  application.py              app wiring: Flask, SocketIO, blueprint
  db.py                       connect(): one short-lived connection per operation
  schema.sql                  baked into the db image at build time
  controller/                 HTTP + WebSocket edges — parse, delegate, serialize
    sessions.py               /sessions REST routes, ApiError -> JSON handler
    sockets.py                join_session / disconnect handlers
  controller_operations/      business logic — takes parsed input, returns payload
    sessions_ops.py           create / get / join / move / say
    engine.py                 Sunfish-backed ranking and static eval
    llm.py                    Groq call, JSON parsing, retry loop
    presence.py               room membership + idle-session GC
    helpers.py                ids, tokens, PGN append, terminal-status detection
    errors.py                 ApiError
  queries/                    SQL only, one module per table
    sessions.py  moves.py  connections.py  llm_calls.py
  error_logger/logger.py      INFO to stdout, ERROR to error_file/<date>.log
  vendor/sunfish.py           vendored Sunfish 2023 (piece values + PSTs)

frontend/
  nginx.conf                  SPA fallback + /api and /socket.io reverse proxy
  src/
    App.jsx                   two routes: / and /sessionId/:sessionId
    api.js socket.js storage.js
    modules/                  one folder per component, .jsx + .css
      Menu/ GameView/ Chessboard/ Chatbox/ EvalBar/ Avatar/ FatalError/

docker-compose.yml            db + backend + frontend
```

The backend's three-layer split is consistent and worth preserving: **controller**
(edge) → **controller_operations** (logic, raises `ApiError`) → **queries** (SQL).
Operations never touch Flask; queries never contain logic.

---

## Runtime topology

```
browser
  │  HTTPS
  ▼
Caddy (production only, host)          automatic Let's Encrypt cert
  │  http://127.0.0.1:8080
  ▼
frontend container — nginx :80
  ├── /            → static Vite bundle, SPA fallback to index.html
  ├── /api/        → proxy_pass http://backend:5001/   (trailing slash strips /api)
  └── /socket.io/  → proxy_pass, Upgrade headers, read timeout 3600s
        │
        ▼
backend container :5001 (expose only, no host port)
  gunicorn -w 1 -k geventwebsocket.gunicorn.workers.GeventWebSocketWorker
        │
        ├──→ db container :5432   postgres:16-alpine, schema.sql baked into initdb
        └──→ api.groq.com         OpenAI-compatible endpoint, via the openai SDK
```

`-w 1` is deliberate: Flask-SocketIO has no message queue configured, so
`socketio.emit(..., to=room)` reaches only the clients of the worker that sent it.
Presence now lives in Postgres rather than in per-process dicts, which is a
*prerequisite* for multiple workers, not the thing that achieves it — the message
queue is still missing. The boot wipe (`presence.clear_connections()`,
`application.py:22`) is also single-process by construction: a second worker
starting would clear the first worker's connections.

Locally without Docker, the frontend dev server talks to `http://127.0.0.1:5001`
directly — `api.js:1` and `socket.js:8` default to that when the `VITE_*` build
args are absent.

---

## Data model

Five tables and one reporting view, all declared in `schema.sql`. There is no
user table. Games are kept rather than collected, and the LLM call log is
warehouse data that deliberately outlives a deleted game — see Identity and
session lifecycle.

**sessions** — one row per game.
`id` (8-char base36 PK), `fen`, `pgn`, `white_token`, `black_token`,
`status` CHECK-constrained to `active | white_won | black_won | draw`,
`created_at`, `updated_at`, `last_disconnected_at`. Indexed on `updated_at`.
`last_disconnected_at` is written only by the trigger below and is NULL for a game
no socket has ever left.

**session_connections** — one row per socket per room, `PRIMARY KEY (session_id,
socket_sid)`, cascade-deleted with the session, additionally indexed on
`socket_sid` because the disconnect looks up by socket alone. This is the live
membership of every room. An `AFTER DELETE ... FOR EACH ROW` trigger
(`session_mark_last_disconnect`) stamps `sessions.last_disconnected_at = NOW()`
unconditionally on every row removed.

**moves** — one row per ply, `PRIMARY KEY (session_id, ply)`, cascade-deleted with
the session. Holds `uci`, `san`, and `tone_summary` — the rolling narrative the LLM
maintains. `tone_summary` is NULL for dragged moves. LLM moves also save
`player_text`, `pre_move_fen`, and `prior_tone` in the move transaction.
Nullable `intent` and `rationale` cache the on-demand explanation. Manual and
old moves have NULL context; analysis data is never used to backfill it.

**llm_calls** — one row per committed LLM move, keyed by an
identity `id`. It stores the session and target ply, request context, model and
both retry limits, aggregate outcome and latency, and the parsed success fields.
The outcome is `unexpected` from construction until a terminal `ok`, `exhausted`,
or `transport` result replaces it, so an unanticipated exit cannot make the row
uninsertable and discard earlier attempt history.
`session_id` intentionally has no foreign key: these records are retained as
warehouse data even if a game is deleted. Indexes support session/ply, time, and
outcome filtering. Failed invocations and rolled-back moves create no new rows;
historical failure rows remain. `intent` and `rationale` start NULL and may be
copied after an explanation succeeds. `explain_requests` counts server requests,
including cache hits; `explain_prompt_tokens`, `explain_completion_tokens`, and
`explain_latency_ms` copy successful generation usage and latency. These writes
are best effort. The game and LLM system never read analysis tables.

**llm_call_attempts** — one row per content-loop attempt, keyed by `(call_id,
attempt)` and cascade-deleted with its call. It separates content outcomes
(`bad_json`, `missing_key`, `invalid_uci`, `bad_shape`) from transport failures,
and labels an ending the classifier does not name as `unexpected`. Classified
attempts store the exact response content whenever content was returned;
`unexpected` deliberately stores NULL content and the exception in
`error_detail`. The row also records SDK retry counts, provider HTTP status,
latency, and optional token usage. NULL token counts mean the provider returned
no usage; NULL `sdk_retries` means a transport failure exposed no final count.

**llm_call_metrics** — a daily, per-model view with calls by final outcome,
content-loop attempts, estimated HTTP requests, retry incidence, off-list moves,
latency percentiles, and token totals. Unknown transport retry counts are filled
from that call's recorded SDK retry limit at read time, never stored as measured.

`fen` is the authoritative game state; `pgn` is display-only, appended as text
(`helpers.py:21`) rather than generated by python-chess. Odd `ply` is White.

---

## HTTP API

All JSON. Errors are `{"error": "<code>", ...extra}` with the status carried on the
`ApiError` (`errors.py`), rendered by a single `app_errorhandler` (`sessions.py:10`).

| Route | Does | Notable failures |
|---|---|---|
| `POST /sessions` | Creates a game, assigns the creator a color and token | `bad_color` 400, `could_not_allocate_session_id` 500 after 5 id collisions |
| `GET /sessions/<sid>` | Board summary + `evalCp` + `both_joined` + `ended` | `not_found` 404 |
| `POST /sessions/<sid>/join` | Claims a free color, or re-identifies an existing token; returns `opponentJoined` | `session_full` 409, `game_ended` 410 |
| `POST /sessions/<sid>/move` | Plays an explicit UCI | `game_ended` 410, `not_your_turn` 403, `illegal_move` 400, `game_over` 409, `waiting_for_opponent_join` 409 |
| `POST /sessions/<sid>/say` | Tone phrase → LLM-chosen move | `game_ended` 410, `llm_unavailable` 502, `llm_bad_response` 502, `waiting_for_opponent_join` 409 |
| `POST /sessions/<sid>/moves/<ply>/explain` | Either player's token → stored or generated `intent` and `rationale` | `missing_token` 401, `not_a_player` 403, `not_found` 404, `explanation_unavailable` 409, `llm_unavailable` / `llm_bad_response` 502 |

`ended` and `game_ended` are the idle deadline, not `status`: a game won by
checkmate is over in a different sense and is reported through `status`. The read
path reports, every write path refuses — that split is what lets the client show
the final position with a modal over it. `game_ended` is checked ahead of
`game_over` in both move paths, because a game past its deadline is unreachable
however it finished.

## WebSocket events

Room name is `session:<sid>` (`helpers.py:17`).

- **client → server** `join_session {sessionId}` — checks the idle deadline
  first; when the game is past it, the socket joins nothing, is recorded nowhere,
  and gets `game_ended` back. Otherwise it joins the room and inserts its
  `session_connections` row. The check must precede the insert, or the predicate
  sees the joiner and no game is ever idle.
- **server → client** `game_ended` — `{sessionId}`, to the requesting socket only.
  A silent refusal would leave a reconnecting client in a room receiving no moves
  and given no reason.
- **server → client** `move` — the full post-move payload: `fen`, `pgn`, `status`,
  `ply`, `uci`, `san`, `evalCp`, plus (say-moves only) `text`, `tone_summary`,
  `priorTone`. Explanations arrive only through the explanation route.
- **server → client** `thinking` — `{on, side, status}` where status is `thinking`
  or `retrying`, driving the opponent's typing dots and the mover's own subscript.
  Emitted under a `finally`, so the `on:false` always arrives.
- **server → client** `player_joined` — `{color}`, after the claiming transaction
  commits. For the player already waiting; the joiner learns from their own response.

Sockets are broadcast-only for state; every mutation goes over HTTP and comes back
through the room. The client that initiated a move gets it twice (HTTP response and
socket broadcast) and reconciles by comparing FENs (`GameView.jsx:104`).

---

## The move pipeline

### Dragged move — `make_move` (`sessions_ops.py:139`)

Parse UCI → open a transaction and `SELECT ... FOR UPDATE` (`sessions.py:47`) →
status must be `active` → token maps to a color → that color must equal `board.turn`
→ move must be in `board.legal_moves` → compute SAN, push, derive new FEN/PGN/status
→ update session, insert move → commit → emit.

The client is optimistic: it applies the move to a local `chess.js` board first and
calls `game.undo()` if the POST fails (`GameView.jsx:143`).

### Tone move — `say_move` (`sessions_ops.py:213`)

Same lock and ownership checks, then:

1. **Alternation guard** — reads the last move; if the same side made it, reject with
   `waiting_for_opponent_move`. With no prior moves, only White may proceed.
2. **Candidates** — `rank_moves` (`engine.py:88`) converts the FEN into a Sunfish
   `Position`, scores every legal move with `pos.value(move)`, and returns the top 8
   as `(uci, san)` pairs. No search — piece-square deltas and capture bonuses only,
   well under a millisecond. On any Sunfish failure it falls back to the first 8
   legal moves; positions with ≤8 legal moves skip ranking entirely.
3. **Broadcast `thinking`** so the opponent sees dots while the model works.
4. **LLM** — `pick_move_with_llm` (`llm.py:281`) renders its two messages with
   `build_move_prompt` (`llm.py:145`) and hands them to the shared provider loop
   `_pick_move_from_messages` (`llm.py:169`). The user message is one compact JSON
   object with the keys `TONE`, `LAST`, `MSG`, `FEN`, `CAND`, `RULE` — the prior
   tone summary, the opponent's last move as bare UCI, the new phrase, the FEN,
   and the candidate UCIs. Response is forced to `json_object` with
   `{uci, tone_summary}`. Changed 2026-09-19 by
   `plans/machine-readable-move-prompt.md`, which cut the measured input prompt
   from 434.55 to 262.60 tokens; SAN was dropped from the candidate list and an
   absent prior tone is now JSON `null` rather than the prose "(none yet)".
5. **Validation** — the returned UCI is checked against **all legal moves**, not just
   the 8 candidates. The candidate list is advisory by design: the prompt's `RULE`
   key says "choose CAND; leave it only if none fits MSG; tone_summary nonempty,"
   which lets the model play a deliberately off-beat move when the tone calls for
   it while keeping the default sound.
6. **Retry and classify** — bad JSON, a missing key, an out-of-set UCI, or a
   response with no usable content retries up to `LLM_MAX_RETRIES` (capped at 3)
   with `LLM_BACKOFF_BASE * 2**attempt` backoff, emitting `status: "retrying"`
   each time. An exception the named classifiers do not recognise is recorded as
   `unexpected`, without raw response content, and follows the same retry path.
   Provider HTTP errors, connection failures, and timeouts are classified as
   `transport` and are not retried by this loop — the SDK already applied its own
   retry policy — so they surface as `llm_unavailable`.
7. **Capture** — an in-memory `CallLog` records the request, every attempt's
   latency, outcome, permitted raw content, token usage, the SDK's retry count
   when available, and the final parsed fields. The SDK and content-loop retry
   counts remain separate.
8. Apply, persist the move with the new `tone_summary` and explanation context,
   and commit its transaction. Only after a successful commit, open a fresh
   connection and atomically write the call and its attempts. A logging failure
   is reported to the error logger but never changes the move result. Then emit
   the move. Indicator cleanup still runs on every move path.

### On-demand explanation

`explain_move` authenticates either player and reads the requested `(session_id,
ply)` from `moves`, without turn or last-mover checks. A cached answer returns
immediately. A cache miss uses that move's saved message, pre-move FEN, prior
tone, UCI and SAN with the LAZY prompt. Generation holds no session row lock.
The answer is saved by updating only `moves.intent` and `moves.rationale`.
Failures use the same LLM error codes as `say_move` and leave the game unchanged.
Concurrent cache misses may generate twice; no shared invocation is guaranteed.

`Chatbox` opens the card with prior tone already present, then shows loading,
the answer, or a card-local error. `GameView` stores the answer on the message;
closing and reopening a cached card makes no request. A failed card can retry.
Each server request attempts an analysis counter update, and successful
generation attempts a separate usage copy. Missing rows or failed analysis
writes have no effect on the answer or cache.

### Tone as game state

`tone_summary` is the interesting piece of design. Each call folds the new message
into a running one-or-two-sentence narrative of the game's mood, stored on the move
row and read back on the next `say` (`moves.py:12`). It gives the model continuity
without replaying the whole message history, so context stays flat-sized regardless
of game length. `intent` and `rationale` are generated on demand per move and feed
the "?" popover in the chat.

### LLM call metrics

The readout is SQL-only. Daily per-model totals keep the content-loop and SDK
transport layers distinct:

```sql
SELECT *
FROM llm_call_metrics
ORDER BY day DESC, model;
```

Break down detectable failure classes directly from attempts:

```sql
SELECT outcome, count(*) AS attempts
FROM llm_call_attempts
GROUP BY outcome
ORDER BY outcome;
```

Provider HTTP failures are queryable without parsing error text:

```sql
SELECT status_code, count(*) AS failures
FROM llm_call_attempts
WHERE outcome = 'transport' AND status_code IS NOT NULL
GROUP BY status_code
ORDER BY status_code;
```

### Evaluation

`evaluate` (`engine.py:67`) sums Sunfish piece-square values, negating when Black is
to move so the result is always centipawns from White's POV. It rides along on every
`move` payload and on `GET /sessions/<sid>`. `EvalBar.jsx` clamps to ±1000cp and
renders the split as two stacked fills. Parse failures return 0 rather than raising.

---

## Identity and session lifecycle

There are no accounts. `POST /sessions` mints an 8-character session id and a
`secrets.token_urlsafe(24)` player token; the token *is* the player's identity and
lives in `sessionStorage` under `pc-token:<sessionId>` (`storage.js`). The second
visitor to `/join` claims whichever color slot is empty; a third gets `session_full`.
Presenting a known token re-identifies the holder, so a refresh keeps your side.

Because the token is per-tab `sessionStorage`, opening the same game in a second tab
claims the *other* color rather than resuming — which is also how you play yourself
during development.

**The idle deadline** (`presence.py`, `queries/connections.py`) — nothing is
collected and no game is ever deleted. A game that goes quiet keeps its row and its
moves, so a player who hits a problem can reach a developer. Whether a game has
*ended* is computed when someone tries to use it, from one expression:

```sql
NOT EXISTS (SELECT 1 FROM session_connections c WHERE c.session_id = s.id)
AND COALESCE(s.last_disconnected_at, s.created_at) < NOW() - make_interval(secs => %s)
```

Nobody connected right now, and the room empty longer than `IDLE_TTL_SECONDS`
(default 600). `COALESCE` covers the game created and never opened, which has no
stamp and is judged from `created_at`.

`presence.py` holds no state and starts nothing — no lock, no dicts, no timers, no
boot-time rebuild — so two callers for unrelated games never contend, and a restart
loses nothing it has to reconstruct. A socket join inserts a row; a disconnect
deletes every row for that socket and the trigger stamps each affected game.

The stamp is written on **every** disconnect, not only the last one. Guarding on
"was this the last row?" loses the write when two sockets of one game disconnect in
concurrent transactions — neither sees the other's uncommitted delete under READ
COMMITTED — and a NULL stamp then falls through to `created_at`, reporting a
half-hour game as ended seconds after both its tabs close. Written
unconditionally, a NULL stamp means exactly one thing: no socket has ever left.

A disconnect from a game with a phrase in flight waits for the LLM call, because
the trigger's `UPDATE sessions` needs the row `say_move` holds. Leave it: the call
is bounded by `LLM_TIMEOUT`, and the only effect is that the clock starts late,
which keeps the game joinable slightly longer. A `lock_timeout` here would trade a
late write for a lost one, and a lost stamp reads as a game never opened.

On boot, `presence.clear_connections()` (`application.py:22`) deletes every
connection row — sockets do not outlive the process that held them, so rows
orphaned by a crash go with them. The restart grace falls out of the trigger rather
than being coded: the DELETE fires it once per row, so every game that had
connections gets a fresh full window from boot, while a game that had none keeps
its stamp and stays correctly ended.

There is no heartbeat of ours. Socket.IO already pings each client every
`ping_interval` (25s) and reaps anything missing a pong within `ping_timeout`
(20s), firing the same `disconnect` handler a clean close does.

`say_move` and `make_move` take `FOR NO KEY UPDATE`, not `FOR UPDATE`
(`queries/sessions.py:61`). A `session_connections` insert makes Postgres check the
foreign key by taking `FOR KEY SHARE` on the parent row, which `FOR UPDATE` blocks
— so a socket joining a game with a phrase in flight would wait up to ~90s.
`FOR NO KEY UPDATE` still conflicts with itself, so mover-versus-mover exclusion is
unchanged.

There is no `abandoned` status: ended-ness is computed, never stored, and no game is
deleted either. The value was carried in the CHECK constraint as a dead option for a
while and removed on 2026-09-13, once nothing had ever written it. `schema.sql`
narrows the constraint with an explicit drop-and-add, because
`CREATE TABLE IF NOT EXISTS` cannot alter a table that already exists.

---

## Frontend

React 19 + Vite, two routes (`App.jsx`): `/` renders the menu, `/sessionId/:sessionId`
renders the game. Anything else redirects to `/`.

`GameView.jsx` is the only stateful component. It owns a single long-lived `chess.js`
instance (created once via lazy `useState`) as the local mirror of server state, plus
`position`, `messages`, `evalCp`, `thinkingSide`, and selection state. Two effects:
one loads and joins the session, one manages the socket subscription — keyed on
`color` so it connects only after identity is known, and disconnecting on cleanup.

`ChessBoard` and `EvalBar` are presentational. `Chatbox` renders the message log,
the per-move "?" explanation card, thinking dots for either side, and the input,
disabled whenever it isn't your turn or a move is being computed.

A game past its deadline gets its own render branch rather than a flag on the main
one: that render gates the board on `color`, which stays null here so the socket
effect returns early and no socket is opened. The branch renders the final position
with `GameEndedModal` over it, orientation fixed white — colour is not recoverable
client-side, since storage keeps only the token and the one call that maps a token
to a colour is the join that now refuses. The board is behind the modal, so the
orientation is cosmetic. A `game_ended` socket listener sets the same flag, for a
client that joined before the deadline and reconnects after it.

Two LLM errors are handled specially on send (`GameView.jsx:158`): `llm_bad_response`
and `llm_unavailable` both remove the optimistic bubble, restore the text to the
draft box so the phrase isn't lost, and show an inline subscript error rather than a
chat message.

---

## Configuration

`GROQ_API_KEY` is required and read at import (`llm.py:11`). `DATABASE_URL` is
read per connection by `db.py` and is force-overridden in compose to point at the
`db` service, so the value in `backend/.env` is ignored under Docker.

Optional: `LLM_MODEL` (default `qwen/qwen3.8-27b`), `LLM_TIMEOUT`
(30), `LLM_REASONING_EFFORT` (`none` — thinking is off by default because reasoning
tokens bill as output and every move is one latency-sensitive call; set to `default`
to enable), `LLM_MAX_TOKENS` (400 — declared on every call, because the SDK otherwise sends
its own default of 2048 and Groq refuses the request pre-emptively; a value that
truncates the reply yields invalid JSON), `LLM_MAX_RETRIES` (3, hard-capped at 3),
`LLM_BACKOFF_BASE` (0.5),
`IDLE_TTL_SECONDS` (600 — how long a room must sit empty before the game is
refused; it is compared against, not counted down, and nothing is deleted when it
passes).

Frontend config is build-time only — Vite inlines `VITE_API_BASE` and
`VITE_SOCKET_URL` into the bundle, passed as Docker build args, so changing them
requires a rebuild rather than a restart.

---

## Deployment

Single `t4g.small` EC2 instance (Amazon Linux 2023) running the compose stack behind
an Elastic IP, with `phoneticchess.duckdns.org` pointed at it and Caddy on the host
terminating TLS and reverse-proxying to `127.0.0.1:8080`. The frontend publishes only
to localhost precisely so Caddy is the sole public face.

The db image bakes `schema.sql` into `/docker-entrypoint-initdb.d` at build time
rather than bind-mounting it — a deliberate workaround for macOS Desktop/Documents
file-access protections. Note that initdb scripts run **only on an empty data
volume**, so a schema change needs a migration or a `db_data` volume reset.

---

## Structural notes

Recorded as observations, not proposed work.

**One connection per operation.** `db.connect()` is a context manager opening a
short-lived `psycopg.connect(...)`; `application.py` passes the *factory* to the
blueprint and to presence, and each operation opens and closes its own. This is
deliberate rather than incidental. Transaction state lives on the connection:
`Transaction._push_savepoint` decides outer-vs-inner by checking
`pgconn.transaction_status == IDLE`, so a second `transaction()` entered on a shared
connection while the first is open becomes a **SAVEPOINT inside it** — one game's
ROLLBACK then discards another's committed move. psycopg's connection lock
(`connection.py:77`) does not prevent it: it is taken per operation and is not held
across a `with pg.transaction()` block. A pool would work too; at this traffic level
a connect costs ~1-3 ms against a call that may take 30 s.

**The LLM call sits inside the transaction.** `say_move` holds the `FOR UPDATE` row
lock across the entire Groq round-trip — up to `LLM_TIMEOUT` seconds plus retries and
backoff. Nothing waits on it: no move is accepted until both colours are claimed, and
`join_session` answers a returning player and a full game from an unlocked lookup, so
the lock is only ever taken while a colour is still free. What remains is a resource
argument — each in-flight `say_move` pins one Postgres backend.

**The alternation guard is redundant.** `say_move`'s last-mover check duplicates the
`my_color != board.turn` check that follows it, since both derive from the same move
sequence. Harmless defense in depth, but it is the reason `say` needs a `moves` read
that `move` doesn't.

**The test suite is two tiers.** `backend/tests/` runs against fakes by default;
`@pytest.mark.integration` marks the tests that need a live Postgres, which
`backend/scripts/testdb.sh` brings up on 127.0.0.1:55432. The frontend uses vitest
against a hand-driven fake socket. See `AGENTS.md` for the commands.

**CORS is fully open** — `CORS(app)` and `cors_allowed_origins="*"` (`application.py:21`).
Behind the nginx/Caddy same-origin setup nothing depends on it, but it means the API
is callable from any origin. Combined with the absence of rate limiting, session
creation and LLM calls are unmetered per client; the README names Flask-Limiter as
the intended next hardening step.
