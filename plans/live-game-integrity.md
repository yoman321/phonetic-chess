# Live game integrity

Status: draft, not frozen. Blocked on the decisions at the bottom.

## Problem

Three unrelated defects can lose, freeze, or silently corrupt a game already in
progress. None is a layout or style issue; each is reachable in normal play.

**A network blip deletes the game.** `GameView.jsx:87` emits `join_session` once at
effect setup. Socket.IO reconnects with a new socket id and rooms are keyed per
socket id, so the client is never re-added and silently stops receiving moves.
`presence` meanwhile saw the room go empty and armed a 10-minute deletion timer
that nothing now cancels, so the session is deleted with both players still at the
board. Verified in the installed client: `emitBuffered()` flushes and then clears
`sendBuffer` (`socket.io-client/build/cjs/socket.js:625-632`), so the initial
buffered emit is never replayed on a later connect.

**A stuck spinner locks both players out.** `say_move` clears the thinking
indicator in exactly two `except` branches (`sessions_ops.py:238`, `:242`) and
nowhere else; there is no `finally` in the operations layer. Any Groq error outside
those types (bad `GROQ_API_KEY` raises `AuthenticationError`, a malformed request
raises `BadRequestError`), or a throw in `board.san()` or the DB write, leaves the
dots spinning. Chatbox input is disabled by `!!thinkingSide`, so neither player can
retry.

**One database connection is shared by every request.** `application.py:18` opens a
single connection at import; `presence.init` (`:25`) and the blueprint closure
(`:27`, `controller/sessions.py:7`) both capture that one object, so every request
and the presence timer thread use it. Transaction state lives on the connection, so
two requests cannot hold two transactions: `Transaction._push_savepoint`
(`psycopg/transaction.py:209`) nests the second as a SAVEPOINT when
`pgconn.transaction_status != IDLE`. psycopg's own lock (`connection.py:77`) is
taken per operation and is not held across a `with pg.transaction()` block, so it
does not prevent this. Consequences: one game's `ROLLBACK` discards another game's
already-broadcast move; non-LIFO unwinding raises `OutOfOrderTransactionNesting`;
and `create_session`, which relies on `autocommit` with no explicit transaction,
can have its INSERT swallowed by an unrelated open transaction — a player gets a
201 and a share URL for a row that never existed.

## Invariants

Gates derive from these, not from the implementation below. Each is assertable on a
number or an exact value.

1. **Rejoin.** After a socket reconnect, the client is in the session room. Assert:
   a `move` broadcast issued after the reconnect is received by that client
   (received count == 1).
2. **Survival.** A session with at least one socket connected within
   `IDLE_TTL_SECONDS` is never deleted. Assert: `SELECT count(*) FROM sessions
   WHERE id = :sid` == 1 at TTL + 5s, after a disconnect/reconnect cycle inside the
   TTL.
3. **Resync.** After a reconnect gap during which N moves were played, the client's
   FEN equals the server's. Assert: string equality between the client board's FEN
   and `GET /sessions/:id` → `fen`.
4. **Indicator balance.** Every `say_move` call emits exactly one `thinking on:true`
   and exactly one `thinking on:false`, on every outcome — success, mapped
   `ApiError`, and unexpected exception type. Assert: counts == 1 and 1 in all three
   cases.
5. **Isolation.** One operation's failure never rolls back another operation's
   completed write. Assert: after operation A raises, game B's `fen` column equals
   the value B wrote.
6. **Distinct connections.** Two operations executing concurrently never share a
   connection. Assert: `SELECT pg_backend_pid()` differs between them.
7. **No leak.** Connection count returns to baseline after requests complete.
   Assert: `count(*) FROM pg_stat_activity WHERE datname='phonetic_chess'` equals
   the pre-test baseline.

## Phase 1 — Rejoin the room on every connect, and resync

**File:** `frontend/src/modules/GameView/GameView.jsx` only. Satisfies invariants 1-3.

The mount effect already loads board state at `:45-47` (`game.load(s.fen)`,
`setPosition`, `setEvalCp`). Extract those into a local `applySessionState(s)` and
reuse it; do not write a second copy.

Bind the join to the `connect` event rather than running it once (`:84-142`):

```jsx
const hasConnectedRef = useRef(false);   // useRef is not currently imported

const onConnect = () => {
  socket.emit("join_session", { sessionId });
  if (hasConnectedRef.current) {
    // reconnect: catch up on moves played while we were gone
    getSession(sessionId).then((s) => { if (s) applySessionState(s); }).catch(() => {});
  }
  hasConnectedRef.current = true;
};

socket.on("connect", onConnect);
socket.on("move", onMove);
socket.on("thinking", onThinking);
socket.connect();
if (socket.connected) onConnect();   // already open on remount; connect won't re-fire

return () => {
  socket.off("connect", onConnect);
  socket.off("move", onMove);
  socket.off("thinking", onThinking);
  hasConnectedRef.current = false;    // no spurious resync on StrictMode remount
  socket.disconnect();
};
```

No server change. `controller/sockets.py:on_join_session` already calls `track_join`
then `cancel_cleanup`, and `presence._delete_session` re-checks room membership
under the lock before deleting, so a rejoin inside the TTL is safe.

## Phase 2 — Guarantee the indicator clears

**File:** `backend/controller_operations/sessions_ops.py`, `say_move` only.
Satisfies invariant 4.

Wrap from the `thinking on` emit (`:214`) through the end of the transaction block
in `try/finally`; drop the two duplicated `thinking off` emits from the `except`
branches, keeping the branches themselves for their error-code mapping (the
frontend switches on those codes at `GameView.jsx:158`).

```python
socketio.emit("thinking", {"on": True, "side": player_side, "status": "thinking"}, to=room_name(sid))
try:
    def _on_llm_retry(attempt): ...        # unchanged

    try:
        chosen_uci, tone_summary, intent, rationale = pick_move_with_llm(...)
    except (urllib.error.URLError, TimeoutError) as e:
        logger.exception("say_move: llm_unavailable sid=%s", sid)
        raise ApiError("llm_unavailable", 502, detail=str(e))
    except (ValueError, json.JSONDecodeError, KeyError) as e:
        logger.exception("say_move: llm_bad_response sid=%s", sid)
        raise ApiError("llm_bad_response", 502, detail=str(e))

    move = chess.Move.from_uci(chosen_uci)
    ...                                     # through insert_move, unchanged
finally:
    socketio.emit("thinking", {"on": False, "side": player_side}, to=room_name(sid))
```

Ordering is safe: on success the `finally` fires inside the transaction while the
`move` event is emitted after commit (`:272`); the client clears `thinkingSide` on
both events, so the early clear is harmless.

## Phase 3 — One connection per operation

**Files:** new `backend/db.py`; `application.py`;
`controller_operations/sessions_ops.py`; `controller_operations/presence.py`.
`controller/sessions.py` passes the value through unchanged. Satisfies invariants
5-7.

Verified: psycopg's `Connection.__exit__` commits on clean exit, rolls back on
exception, and closes unless the connection belongs to a pool — so a per-operation
`with psycopg.connect(...)` is self-closing and needs no pool dependency.

```python
# backend/db.py
import os
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row

DATABASE_URL = os.environ["DATABASE_URL"]

@contextmanager
def connect():
    """One short-lived connection per operation. Sharing one connection across
    greenlets nests transactions into savepoints — see docs/architecture.md."""
    with psycopg.connect(DATABASE_URL, autocommit=True, row_factory=dict_row) as conn:
        yield conn
```

Keep the injection seam — pass the *factory* where the connection is passed today,
so operations stay testable with a fake:

- `application.py`: drop the module-level `pg`; `presence.init(db_connect)` and
  `make_sessions_bp(db_connect, socketio)`.
- `sessions_ops.py`: rename the first parameter `pg` → `db` on all five operations
  and open per call. Seven sites:
  - `create_session`, `get_session`: `with db() as pg, pg.cursor() as cur:`
  - `join_session`, `make_move`, `say_move`:
    `with db() as pg, pg.transaction(), pg.cursor() as cur:`
- `presence.py`: `_pg` → `_db`; `_delete_session` (`:20`) and
  `reschedule_existing_sessions` (`:48`) each open their own. This also fixes the
  timer thread, which today reaches into the request-thread connection.

Per-operation connect against `db:5432` inside the compose network costs ~1-3 ms,
irrelevant next to a call that may take 30 s.

Known consequence: because the LLM call stays inside the transaction this phase,
each in-flight `say_move` pins one Postgres backend for the duration of the model
call. Acceptable now — Groq's rate limit binds long before Postgres's default
`max_connections` of 100 — but it is why moving the LLM call out of the transaction
becomes urgent if usage grows. Not a lock-in: `db()` is a context manager, so
swapping in `psycopg-pool` later changes one function, not five call sites.

## Verification

Each phase reproduces its failure first, then re-runs after the change.

**Phase 1** — two browsers on one game. Force a reconnect from the console:
`getSocket().io.engine.close()`. Set `IDLE_TTL_SECONDS=30` to observe deletion
quickly.
- Before: the reconnected client receives 0 of the opponent's subsequent moves; at
  TTL + 5s, `GET /sessions/:id` returns 404 with both players present.
- After: invariants 1-3 hold.

**Phase 2** — set `GROQ_API_KEY=bad`, restart, send a phrase.
- Before: 1 `thinking on:true` and 0 `on:false`; input disabled on both clients.
- After: counts are 1 and 1; input usable; chat shows the rejection.

**Phase 3** — two games in parallel with a stubbed slow LLM, failing one.
- Before: game B's `fen` reverts to its pre-move value after A rolls back.
- After: invariants 5-7 hold.

**Full pass** — `docker compose up --build`, then play one game to checkmate using
both a dragged and a tone move, confirming the eval bar updates and both clients
stay in sync.

**Definition of done cannot currently be met.** `AGENTS.md` requires `<test-full>`,
`<typecheck>`, `<lint>`, `<build>` to pass, and all four are placeholders in the
Commands block. The only real commands that exist today are `npm run lint` and
`npm run build` in `frontend/`; the backend has no test, lint, or typecheck command
configured. Filling that block in is a prerequisite for any session claiming done.

## Decisions required before the gates session

1. **pytest as a dev dependency** (`AGENTS.md`: no new dependency without
   approval). Invariants 4-7 have clean fail-first unit gates with it — stub
   `pick_move_with_llm` to raise `RuntimeError` and count emits on a fake socketio;
   run two operations against a fake factory and compare `pg_backend_pid()`.
   Without approval the gates for Phases 2-3 are manual only and not repeatable.
   Recommended: approve.
2. **A frontend test runner** (vitest or equivalent) for invariants 1-3. There is
   none in `package.json`, so Phase 1's gate is manual either way unless one is
   added. A second, separate dependency decision.
3. **`psycopg-pool` instead of per-operation connections.** Not recommended now;
   per-operation is correct and dependency-free at this traffic level.

## Out of scope

Deliberately excluded so they are not silently absorbed; all recorded in
`BACKLOG.md`.

- Moving the LLM call out of the transaction. Doing it correctly means splitting
  `say_move` into read → call → reopen → re-validate the FEN is unchanged, which is
  new optimistic-concurrency logic and deserves its own plan.
- Tone continuity reset on dragged moves, missing game-over UI, the
  `JoinGameModal` null token, resign/draw, promotion choice, ERROR-level logging of
  routine client errors, migrations, rate limiting, README drift.
- Any directory restructuring. Reviewed this session and deliberately deferred.
