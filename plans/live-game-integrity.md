# Live game integrity

Status: **FROZEN 2026-09-09** by the user. Gates are written and were observed
failing; ready to build. Do not edit while building against it.

Round 1 was the 2026-09-08 draft; it failed grading with seven findings. Round 2
was the first 2026-09-09 revision; it failed grading with ten
(`plans/live-game-integrity.review.md`). This is round 3. What changed is at the
bottom.

Written during the grading session at the user's direction, so the plan and its
review now share a session. The review was not edited.

## Problem

Four defects can lose, freeze, or silently corrupt a game already in progress.
None is a layout or style issue; each is reachable in normal play.

**A network blip deletes the game.** `GameView.jsx:87` emits `join_session` once at
effect setup. Socket.IO reconnects with a new socket id and rooms are keyed per
socket id, so the client is never re-added and silently stops receiving moves.
`presence` meanwhile saw the room go empty and armed a 10-minute deletion timer
that nothing now cancels, so the session is deleted with both players still at the
board. Verified in the installed client: `emitBuffered()` flushes and then clears
`sendBuffer` (`socket.io-client@4.8.3` `build/cjs/socket.js:625-632`), so the
initial buffered emit is never replayed on a later connect.

**A stuck spinner locks the mover out.** `say_move` clears the thinking indicator
in exactly two `except` branches (`sessions_ops.py:239`, `:243`) and nowhere else;
there is no `finally` in the operations layer. Any Groq error outside those types
(a bad `GROQ_API_KEY` raises `AuthenticationError`, a malformed request raises
`BadRequestError`), or a throw in `board.san()` or the DB write, leaves the dots
spinning. Chatbox input is disabled by `!!thinkingSide` (`GameView.jsx:316`), so
the player whose turn it is cannot retry. The opponent is already blocked by
`!myTurn` and is unaffected.

**A game can start before both players are present.** `say_move` checks only that
there are no prior moves and that white is moving (`sessions_ops.py:182-193`); it
never checks that black has joined. `make_move` does not check either. So white
can open a game, immediately spend a paid Groq call, and advance the board while
the invitee is still reading the share link. The invitee then arrives mid-game to
a position they never saw played.

**One database connection is shared by every request.** `application.py:18` opens a
single connection at import; `presence.init` (`:24`) and the blueprint closure
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
number or an exact value; each is false against today's code; and each names the
tier that can observe it, because an invariant no tier can reach is not a gate.

1. **Rejoin.** The client emits `join_session` once per `connect` event, not once
   per mount. Assert (frontend): drive the mocked socket through
   `connect` → `disconnect` → `connect`; `join_session` emit count == 2.
   *Today: 1.*

   This is the whole client-side fix. The server half already works —
   `sockets.py:19-21` calls `join_room`, `track_join` and `cancel_cleanup` on every
   `join_session` — so there is nothing to gate there. End-to-end survival across a
   reconnect is covered by the Phase 1 manual verification, not by an invariant,
   because a backend-only test of it passes today.

2. **No delete after a join.** A session is never deleted once a socket has been
   tracked in its room, including when the join lands during the deletion.
   Assert (backend, Postgres): park `_delete_session` between its membership
   re-check and its DELETE, call `track_join(sid, "s1")`, release it, then
   `SELECT count(*) FROM sessions WHERE id = :sid` == 1. *Today: 0* — the re-check
   happens under `_presence_lock` but the DELETE runs after it is released
   (`presence.py:20-27`).

   **Superseded 2026-09-09, by decision — the gate asserts something narrower.**
   That `count(*) == 1` cannot hold once Phase 4 moves the DELETE inside
   `_presence_lock`: the park point is then inside the critical section, the
   parked `track_join` serialises *after* the DELETE, and the row is gone either
   way. `backend/tests/test_presence_delete_race.py` instead asserts that **no
   DELETE is ever issued while a socket is tracked**, which is false today and
   true after the fix. The residual window — a join that serialises behind the
   DELETE still joins a room whose session is gone — is accepted and stays open.
   Do not change the test to match the paragraph above it.

3. **Resync.** After a reconnect gap during which N moves were played, the client's
   board equals what the server reported. Assert (frontend): string equality
   between the client board's FEN and the `fen` the stubbed `getSession` returned.

4. **Indicator balance.** Every `say_move` that emits `thinking on:true` emits
   exactly one `on:false` afterwards, and that `on:false` is the last thinking
   event it emits. Holds on success, on a mapped `ApiError`, and on an unexpected
   exception type. **Do not count `on:true`** — `_on_llm_retry`
   (`sessions_ops.py:220-230`) emits one per retry, so a correct implementation
   emits up to `LLM_MAX_RETRIES` of them. Fourth case, asserted separately: a call
   rejected *before* the LLM (`missing_text`, `missing_token`, `not_found`,
   `game_over`, `not_a_player`, `waiting_for_opponent_move`, `not_your_turn`,
   `no_legal_moves`, and the `waiting_for_opponent_join` Phase 3 adds) emits **zero**
   thinking events of either kind. *Today, with a bad API key: one `on:true` and
   zero `on:false`.*

5. **Isolation.** One operation's failure never rolls back another operation's
   completed write. Assert (backend, Postgres): A must be *inside* its transaction
   while B commits — see the interleaving recipe in Phase 0. Sequentially this
   passes today and is not a gate.

6. **Distinct connections.** Two operations never share a connection. Assert
   (backend, Postgres): `SELECT pg_backend_pid()` in each, values differ.
   *Today: identical.*

7. **No leak.** Assert (backend, Postgres): during N concurrent in-flight
   operations, `count(*) FROM pg_stat_activity WHERE datname='phonetic_chess'` is
   exactly baseline + N, and returns to baseline once they complete. *Today it
   rises by 0.* Both halves are load-bearing: "rises by N" proves the connections
   are separate, "returns to baseline" proves they are closed. Measuring only after
   completion passes before the work exists and is therefore not a gate. Uses the
   same interleaving recipe as invariant 5.

8. **Both players present.** No move is accepted until both colours are claimed.
   Assert (backend, no Postgres): with a fake cursor returning a row whose
   `black_token` is `NULL`, `say_move` as white raises `ApiError`
   `waiting_for_opponent_join` with status 409, and `pick_move_with_llm` is called
   0 times. *Today: no error, one call, board advances.*

**Note for arbitration:** invariants 6 and 7 assert mechanism — which backend pid,
how many rows in `pg_stat_activity` — rather than behaviour a player can observe.
`AGENTS.md` says to assert the invariant, not the shape. 5 is the one that states
the actual guarantee. Collapsing 6 and 7 into 5 is defensible and would remove two
gates; kept for now because they are the only direct evidence that the connection
change did what it claims. Listed under Open.

---

## Phase 0 — Test infrastructure

No product behaviour. Exists because the gates session cannot write a failing test
without a runner. **This is a Build session, not the gates session** — it writes
config and dependencies, and `AGENTS.md` restricts the gates session to failing
tests only.

### Backend

New `backend/requirements-dev.txt`, kept separate from `requirements.txt` so the
Docker image does not install it:

```
pytest==8.*
```

Tests in `backend/tests/`. Two tiers, and the split is structural — invariants 2
and 5-7 assert on Postgres internals and cannot be faked:

| Invariant | Postgres | Approach |
|---|---|---|
| 4 — indicator balance | no | fake `socketio` recording emits; stub `pick_move_with_llm` to raise `RuntimeError`; fake cursor |
| 8 — both players | no | fake cursor returning a row with one token `NULL` |
| 2 — no delete after a join | yes | park `_delete_session` mid-way, `track_join`, release, count rows |
| 5 — isolation | yes | interleaved: A parked inside its transaction while B commits |
| 6 — distinct connections | yes | `SELECT pg_backend_pid()` in each, assert different |
| 7 — no leak | yes | count `pg_stat_activity` before, during, after N in-flight ops |

Mark the Postgres tier `@pytest.mark.integration` and register the marker in
`pytest.ini` so `<test-fast>` can skip it.

### The interleaving recipe — invariants 2, 5 and 7

Three gates need one operation held open while another runs. Without this they
pass against today's code, and `AGENTS.md` is explicit that a gate which passes
before the work exists is not a gate. Use one mechanism for all three:

```python
# tests/conftest.py
import threading

class Latch:
    """Park an operation at a known point until the test releases it."""
    def __init__(self):
        self.reached = threading.Event()   # set by the op when it parks
        self.release = threading.Event()   # set by the test to let it go

    def wait_here(self):
        self.reached.set()
        self.release.wait(timeout=10)
```

- **Invariant 5.** Stub `pick_move_with_llm` to call `latch.wait_here()`. Run
  `say_move` for game A in a thread; wait for `latch.reached`; A is now parked
  inside its transaction with its row lock held. Run `make_move` for game B to
  completion on the main thread. Set `latch.release` and have the stub raise, so A
  rolls back. Join the thread, then assert B's `fen` equals what B wrote.
- **Invariant 7.** Same latch, N threads. Sample `pg_stat_activity` once all N have
  parked, and again after all N have joined.
- **Invariant 2.** Monkeypatch `sessions_q.delete_session` to call
  `latch.wait_here()` before delegating. Fire `_delete_session` in a thread, wait
  for `reached`, call `track_join`, release, join, then count rows.

### Where the Postgres tier gets a database

The compose `db` service publishes no host port — `docker-compose.yml` has exactly
one `ports:` entry, at `:56`, and it belongs to `frontend`. So `docker compose up -d db`
leaves Postgres reachable only inside the compose network and host-run pytest cannot
connect to it.

**Decision taken:** a throwaway container on a published port, built from the
existing `backend/db.Dockerfile` so the schema is already baked in. No
`docker-compose.yml` edit, so no Boundaries approval, and test data never shares a
volume with development data.

New `backend/scripts/testdb.sh`:

```bash
#!/usr/bin/env sh
# Throwaway Postgres for the integration tier. Port 55432 so it cannot collide
# with a local server on 5432 or with the compose stack.
set -e
IMAGE=phonetic-chess-testdb
NAME=pc-test-db
case "$1" in
  up)
    docker build -q -t "$IMAGE" -f backend/db.Dockerfile backend >/dev/null
    docker rm -f "$NAME" >/dev/null 2>&1 || true
    docker run --rm -d --name "$NAME" -p 127.0.0.1:55432:5432 \
      -e POSTGRES_USER=phonetic -e POSTGRES_PASSWORD=phonetic \
      -e POSTGRES_DB=phonetic_chess "$IMAGE" >/dev/null
    until docker exec "$NAME" pg_isready -U phonetic -d phonetic_chess >/dev/null 2>&1; do
      sleep 1
    done
    ;;
  down) docker rm -f "$NAME" >/dev/null 2>&1 || true ;;
  *) echo "usage: $0 {up|down}" >&2; exit 2 ;;
esac
```

`conftest.py` **sets** `DATABASE_URL` rather than defaulting it, so a developer with
a real `DATABASE_URL` exported cannot have the suite run against their own data:

```python
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql://phonetic:phonetic@127.0.0.1:55432/phonetic_chess",
)
```

That isolation also keeps invariant 7's baseline clean: nothing else connects to
that database, so the count cannot drift under the test.

### The seam that survives Phase 4

Phase 4 renames the operations' first parameter from a connection to a zero-arg
factory. The gates for invariants 4 and 8 are written in the Phase 2 and Phase 3
sessions, before that lands, so they must not hard-code either shape. Put the seam
in one place:

```python
# tests/conftest.py
def op_db(conn):
    """Whatever the operations' first parameter currently is.
    Phase 4 changes this one line to `return lambda: conn`."""
    return conn
```

Gates call `say_move(op_db(fake_conn), fake_socketio, ...)`. Phase 4's file list
includes updating this function, and no assertion moves.

### Frontend

Vitest, because the project is already on Vite 8 (`frontend/package.json`) and
vitest reuses that config rather than introducing a second build pipeline.

Add to devDependencies: `vitest`, `jsdom`, `@testing-library/react`. Not
`@testing-library/jest-dom` — every assertion invariants 1 and 3 need is a count or
an exact string, so its matchers buy nothing and it would need a `setupFiles` entry
to register at all.

`vite.config.js` gains a `test` block. Without it jsdom is installed but never
used, and `@testing-library/react` fails on `document`:

```js
test: {
  environment: "jsdom",
  globals: false,          // tests import { describe, it, expect, vi } from "vitest"
},
```

`globals: false` is deliberate. `eslint.config.js` sets `globals: globals.browser`
and extends `js.configs.recommended`, which enables `no-undef` — so bare
`describe`/`it`/`expect`/`vi` would fail `npm run lint`, which is part of the
definition of done. Importing them explicitly needs no eslint change at all. Call
`cleanup()` from `@testing-library/react` in an `afterEach`, since `globals: false`
means vitest does not do it automatically.

Add scripts:

```json
"test": "vitest run",
"test:watch": "vitest"
```

Invariants 1 and 3 exercise `GameView`'s socket behaviour, so `getSocket()`
(`frontend/src/socket.js:5`) is mocked with `vi.mock` and driven by hand — emit
`connect`, emit `move`, assert what the component did. `getSession` is stubbed too.
No real server, no real backend.

### `AGENTS.md` Commands block

All seven entries are placeholders today, so no session can meet the stated
definition of done. Proposed fill. Each half is a subshell: chaining bare `cd`s
would resolve the second relative to the first.

```bash
# <setup>
(cd backend && python -m venv .venv && .venv/bin/pip install -r requirements.txt -r requirements-dev.txt)
(cd frontend && npm install)

# <test-fast>     unit only, no database
(cd backend && .venv/bin/pytest -q -m "not integration")
(cd frontend && npm test)

# <test-full>     includes the Postgres-backed tier
backend/scripts/testdb.sh up
(cd backend && .venv/bin/pytest -q)
(cd frontend && npm test)
backend/scripts/testdb.sh down

# <test-single>
(cd backend && .venv/bin/pytest -q tests/test_say_move.py::test_indicator_clears)
(cd frontend && npm test -- -t "emits join_session on every connect")

# <typecheck>
(none configured — see below)

# <lint>
(cd frontend && npm run lint)

# <build>
(cd frontend && npm run build)
```

**Two gaps, named rather than papered over.** The backend has no linter and no type
checker — nothing in `requirements.txt` provides either — and the frontend is plain
JSX with no `tsc`. So `<typecheck>` has no real command and `<lint>` covers the
frontend only. Adding `ruff` and/or `mypy` is a separate dependency decision, not
made.

**`AGENTS.md` has not been edited.** Its own Boundaries forbid it without explicit
approval. The block above is written out so it can be approved or pasted directly.

---

## Phase 1 — Rejoin the room on every connect, and resync

**File:** `frontend/src/modules/GameView/GameView.jsx` only. Satisfies invariants
1 and 3.

The mount effect already loads board state at `:45-47` (`game.load(s.fen)`,
`setPosition`, `setEvalCp`). Extract those into an `applySessionState(s)` at
component scope — both effects need it — and reuse it. Do not write a second copy.
**Phase 3 adds a line to this function, not to the mount effect**; see there.

Bind the join to the `connect` event rather than running it once (`:83-142`):

```jsx
const hasConnectedRef = useRef(false);   // useRef is not currently imported (`:1`)
const moveSeqRef = useRef(0);            // guards the resync against a mid-flight move

// in onMove, before anything else:
moveSeqRef.current += 1;

const onConnect = () => {
  socket.emit("join_session", { sessionId });
  if (hasConnectedRef.current) {
    // reconnect: catch up on moves played while we were gone
    const seq = moveSeqRef.current;
    getSession(sessionId)
      .then((s) => {
        if (!s || seq !== moveSeqRef.current) return;  // a move landed mid-fetch
        applySessionState(s);
      })
      .catch(() => {});
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

The `moveSeqRef` guard is not optional. Without it, a `move` broadcast that arrives
and is applied between the fetch going out and its `.then()` running is overwritten
by the older fetched FEN, and that move is never re-sent — the fix would cause the
desync it exists to repair. `GET /sessions/:id` returns no `ply`, so a version
compare would mean deriving ply from the FEN's fullmove counter plus side-to-move;
the counter is simpler and equally correct.

No server change. `controller/sockets.py:on_join_session` already calls `track_join`
then `cancel_cleanup`, and `presence._delete_session` re-checks room membership
under the lock before deleting.

**Known limit:** this resyncs the board, not `messages`. After a reconnect gap the
board catches up and the chat has a hole. No invariant covers it; recorded in
`BACKLOG.md`.

---

## Phase 2 — Guarantee the indicator clears

**File:** `backend/controller_operations/sessions_ops.py`, `say_move` only.
Satisfies invariant 4.

Wrap from the `thinking on` emit (`:214`) to the end of the enclosing
`with pg.transaction()` block in `try/finally`; drop the two duplicated
`thinking off` emits at `:239` and `:243`, keeping the `except` branches themselves
for their error-code mapping (the frontend switches on those codes).

The `try` nests *inside* the existing `with` at `:163`, since the emit at `:214` is
already inside it. That is deliberate, not an oversight — see the ordering note
below.

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

Keep the duplicated emits deleted. Leaving them would produce two `on:false` on the
mapped-error paths, which invariant 4 forbids.

Every path that raises before `:214` stays as it is and emits nothing. That is
invariant 4's fourth case, and it is what the gate asserts.

---

## Phase 3 — No moves until both players have joined

**Files:** `backend/controller_operations/sessions_ops.py`,
`backend/queries/sessions.py`, `backend/controller/sessions.py`,
`frontend/src/modules/GameView/GameView.jsx`. Satisfies invariant 8.

Must land before Phase 4. Phase 4 is what turns the `FOR UPDATE` in `join_session`
into a real wait; this phase is what guarantees nothing is ever waiting on it.

### Gate the two move operations

`make_move` and `say_move` already read both token columns via
`select_state_for_update` (`queries/sessions.py:49`). After the existing
`status != "active"` check (`:109` and `:168` respectively):

```python
if not row["white_token"] or not row["black_token"]:
    logger.info("%s: waiting_for_opponent_join sid=%s", op_name, sid)
    raise ApiError("waiting_for_opponent_join", 409)
```

Name it `waiting_for_opponent_join`. `waiting_for_opponent_move` already exists in
`say_move` (`:188`, `:193`) and means something else — you moved last, wait your
turn.

### Split `join_session` into lookup and claim

Currently one transaction that takes `FOR UPDATE` even when it only reads
(`:66-67`, early returns at `:72-77`). Split it — and note that **`session_full` is
decided in the unlocked half**. That is the point of the split, not a detail: a
game that is already full must never take the row lock, because by then a
`say_move` can be holding it.

```python
def join_session(pg, socketio, sid, existing_token):
    # Returning player, or a full game. Pure lookup, no lock. Tokens are immutable
    # once written, so nothing here can read a value that is about to change.
    with pg.cursor() as cur:
        row = sessions_q.select_tokens(cur, sid)          # new: no FOR UPDATE
        if not row:
            logger.error("join_session: not_found sid=%s", sid)
            raise ApiError("not_found", 404)
        if existing_token:
            if existing_token == row["white_token"]:
                return {"color": "white", "playerToken": existing_token,
                        "opponentJoined": bool(row["black_token"])}
            if existing_token == row["black_token"]:
                return {"color": "black", "playerToken": existing_token,
                        "opponentJoined": bool(row["white_token"])}
        if row["white_token"] and row["black_token"]:
            # Both colours claimed and this token matches neither: a third visitor,
            # or a player whose sessionStorage was cleared. Answered without the
            # lock — this is the path that would otherwise wait on an in-flight
            # say_move.
            logger.error("join_session: session_full sid=%s", sid)
            raise ApiError("session_full", 409)

    # A colour is free, so by invariant 8 no move has been accepted and nothing can
    # be holding this row. Claim it under the lock.
    with pg.transaction(), pg.cursor() as cur:
        row = sessions_q.select_tokens_for_update(cur, sid)
        if not row:
            raise ApiError("not_found", 404)
        token = new_player_token()
        if not row["white_token"]:
            sessions_q.set_white_token(cur, sid, token)
            color, opponent_joined = "white", bool(row["black_token"])
        elif not row["black_token"]:
            sessions_q.set_black_token(cur, sid, token)
            color, opponent_joined = "black", bool(row["white_token"])
        else:
            # Lost a race for the last slot between the lookup and the lock.
            logger.error("join_session: session_full sid=%s", sid)
            raise ApiError("session_full", 409)

    # After commit, matching make_move (`:151`) and say_move (`:272`).
    socketio.emit("player_joined", {"color": color}, to=room_name(sid))
    return {"color": color, "playerToken": token, "opponentJoined": opponent_joined}
```

Three things to keep exactly as written. The `if existing_token:` guard: without it,
a `None` token compares equal to a `NULL` column and a joiner is handed a colour
they never claimed. `opponentJoined` computed from the *other* column rather than
hardcoded `True`: `create_session` always sets one token, so a claim normally makes
the pair complete, but the expression stays correct if that ever stops being true.
And the `player_joined` emit after the `with` block, not inside it: emitting before
commit would enable white's input for an opponent that a failed commit never
recorded.

The lookup is safe without a lock because tokens never change once set:
`set_white_token` / `set_black_token` are called only when the column is `NULL`,
nothing else writes those columns, and only `delete_session` removes the row.

`join_session` gains a `socketio` parameter; update the call in
`controller/sessions.py:23-26`, which already has it in scope.

### Queries

- Add `select_tokens` — `select_tokens_for_update` without the `FOR UPDATE`.
- Extend `select_session_summary` to return
  `(white_token IS NOT NULL AND black_token IS NOT NULL) AS both_joined`.
  **Return the boolean only. Never return the token values** — `GET /sessions/:id`
  is unauthenticated, and a leaked token is a stolen colour.

### Frontend

- New `opponentJoined` state.
- **Seed it inside `applySessionState`**, not in the mount effect:
  `if (typeof s.both_joined === "boolean") setOpponentJoined(s.both_joined);`.
  Phase 1's reconnect path calls that function, so putting the line anywhere else
  means a player whose socket dropped before the opponent joined never learns they
  can move — input disabled with no recovery but a page reload.
- Also set it from the `joinSession` response (`:49`), which returns
  `opponentJoined` directly, and set it true on a `player_joined` handler
  registered alongside `onMove` (`:89`) and `onThinking` (`:131`).
- Input gate: `disabled={!myTurn || !!thinkingSide || !opponentJoined}` (`:316`).
- Gate the board handlers too, or dragging bypasses the chatbox restriction —
  `:197`, `:211`, `:218`, `:231` each already test `!myTurn`.
- Intro text (`:56-64`): white's "Play your first move." becomes a waiting message
  while `!opponentJoined`.
- Map the new `waiting_for_opponent_join` code to a chat line, alongside the
  existing error-code handling.

The socket effect returns early until `color` is set (`:84`), so a joining player
is not yet in the room when their own claim emits `player_joined`. That is correct:
the event exists for the player who was already waiting; the joiner learns from
their own response payload.

### Why this closes the round-1 finding about the lock

The 2026-09-08 draft's Phase 3 (now Phase 4) created an unbounded wait: with one
transaction per operation, a second request on the same session row blocks until
the first commits, and `say_move` holds that row across the whole Groq call — up to
`LLM_TIMEOUT` (30 s) × `LLM_MAX_RETRIES` (3) plus backoff (`llm.py:14,21,22`).
`POST /sessions/:id/join` takes the same lock and runs on every `GameView` mount.

After this phase the lock is only ever taken while a colour is still free:

- No move is accepted until both tokens are set, so nothing can be holding the row
  while a slot remains open.
- Every path that does not need to write a token now returns before the lock — a
  returning player from the token match, a third visitor and a player who lost
  their token from the `session_full` check. The round-2 grading caught that the
  previous revision let those last two fall through into the locked block, which
  put the original failure right back.
- The one remaining wait is two clients racing for the last free slot. That is one
  `UPDATE` long, not one LLM call long.

So no `FOR UPDATE NOWAIT`, no `lock_timeout`, no schema change, and no optimistic
concurrency. The LLM call stays inside the transaction; nothing waits on it.

**Pre-existing limit, unchanged:** `storage.js` uses `sessionStorage`, so a player
who closes the tab returns with no token. On a full game they now get `session_full`
without taking a lock; on a half-full game they still claim the free slot and may
take the other colour. Already in `BACKLOG.md`.

---

## Phase 4 — One connection per operation

**Files:** new `backend/db.py`; `application.py`;
`controller_operations/sessions_ops.py`; `controller_operations/presence.py`;
`controller/sessions.py`; `backend/tests/conftest.py`. Satisfies invariants 2, 5,
6 and 7.

Verified: psycopg's `Connection.__exit__` commits on clean exit, rolls back on
exception, and closes unless the connection belongs to a pool
(`psycopg/connection.py:155-176`) — so a per-operation `with psycopg.connect()` is
self-closing and needs no pool dependency. `commit()` under `autocommit=True`
returns early when `transaction_status == IDLE` (`_connection_base.py:570-585`)
rather than raising, so the clean-exit path is a no-op rather than an error.

```python
# backend/db.py
import os
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row


@contextmanager
def connect():
    """One short-lived connection per operation. Sharing one connection across
    greenlets nests transactions into savepoints — see docs/architecture.md."""
    url = os.environ["DATABASE_URL"]        # read per call, not at import
    with psycopg.connect(url, autocommit=True, row_factory=dict_row) as conn:
        yield conn
```

`DATABASE_URL` must be read inside `connect()`, not at module level. `application.py`
imports its modules in the block at `:1-12` and calls `load_dotenv()` at `:14`; the
current read sits at `:16`, after it, and that ordering is deliberate. A module-level
read in `db.py` would run during the import block, before the `.env` file is loaded,
and raise `KeyError` under the documented local path (`README.md`: `cd backend &&
python application.py`). It would be invisible under Docker, where the variable comes
from compose — so the Full pass below would not catch it either.

Keep the injection seam — pass the *factory* where the connection is passed today,
so operations stay testable with a fake:

- `application.py`: drop the module-level `pg` (`:18`); `presence.init(db.connect)`
  and `make_sessions_bp(db.connect, socketio)`.
- `controller/sessions.py`: rename the parameter `pg` → `db` (`:7`) and pass it
  through unchanged.
- `sessions_ops.py`: rename the first parameter `pg` → `db` on all five operations
  and open per call:
  - `create_session`, `get_session`: `with db() as pg, pg.cursor() as cur:`
  - `make_move`, `say_move`: `with db() as pg, pg.transaction(), pg.cursor() as cur:`
  - `join_session`: **both** blocks from Phase 3 — the lookup becomes
    `with db() as pg, pg.cursor() as cur:` and the claim becomes
    `with db() as pg, pg.transaction(), pg.cursor() as cur:`. Two connections in
    the claim case, sequential, never overlapping.
- `create_session`: `with db()` goes **outside** the 5-attempt id-collision loop
  (`:42-50`) — one operation, one connection — with the cursor outside it too.
  Safe because `autocommit=True` means a `UniqueViolation` leaves no open failed
  transaction on the connection, and the cursor stays usable after the error.
- `presence.py`: `_pg` → `_db`; `_delete_session` (`:20`) and
  `reschedule_existing_sessions` (`:48`) each open their own. This also fixes the
  timer thread, which today reaches into the request-thread connection.
- `tests/conftest.py`: change `op_db` to `return lambda: conn`. This is the only
  test change; no assertion moves.

Also in `presence.py`: move `sessions_q.delete_session` **inside** `_presence_lock`
(`:20-27`). Today the membership re-check happens under the lock but the DELETE runs
after it is released. A `track_join` landing in that window leaves a client in a room
for a deleted session. This is what invariant 2 gates.

Per-operation connect against `db:5432` inside the compose network costs ~1-3 ms,
irrelevant next to a call that may take 30 s. Not a lock-in: `db()` is a context
manager, so swapping in `psycopg-pool` later changes one function, not five call
sites.

---

## Verification

Each phase reproduces its failure first, then re-runs after the change.

**Phase 0** — `<test-fast>` and `<test-full>` execute and report zero tests.
`backend/scripts/testdb.sh up` brings a container up and `pg_isready` succeeds
against `127.0.0.1:55432`. Nothing to reproduce; this phase adds no behaviour.

**Phase 1** — two browsers on one game. Force a reconnect from the console:
`getSocket().io.engine.close()`. Set `IDLE_TTL_SECONDS=30` to observe deletion
quickly.
- Before: the reconnected client receives 0 of the opponent's subsequent moves; at
  TTL + 5s, `GET /sessions/:id` returns 404 with both players present.
- After: moves keep arriving, and the session is still there past TTL + 5s. This
  end-to-end survival check is manual by design — see the note under invariant 1.

**Phase 2** — set `GROQ_API_KEY=bad`, restart, send a phrase.
- Before: one `thinking on:true` and zero `on:false`; the mover's input stays
  disabled.
- After: the last thinking event is `on:false`; input usable; chat shows the
  rejection. Repeat with a stubbed `pick_move_with_llm` that returns an illegal UCI
  to exercise the retry path and confirm multiple `on:true` do not fail the gate.

**Phase 3** — create a game, do not open the share link.
- Before: `POST /sessions/:id/say` as white returns 200, makes one Groq call, and
  advances the board.
- After: 409 `waiting_for_opponent_join`, zero Groq calls, board unchanged. Then
  open the link in a second tab; the first tab's input enables on `player_joined`
  without a refresh.
- **Also check the reconnect ordering:** with white's socket closed from the
  console, join as black, then let white reconnect. White's input must enable from
  the refetch alone, with no `player_joined` received.

**Phase 4** — two operations in parallel with a stubbed slow LLM, failing one.
- Before: game B's `fen` reverts to its pre-move value after A rolls back.
- After: invariants 5-7 hold.
- **Include a same-session step:** while a `say_move` is in flight on session X,
  issue `POST /sessions/X/join` — once with a stored token, once with none on a
  full game — and assert both return promptly rather than blocking. Two *different*
  games are two different rows and cannot surface contention; that is what the
  2026-09-08 draft got wrong, and the no-token case is what the first 2026-09-09
  revision got wrong.

**Full pass** — `docker compose up --build`, then play one game to checkmate using
both a dragged and a tone move, confirming the eval bar updates and both clients
stay in sync.

**Definition of done**, per `AGENTS.md`, for every session after this one:

1. Its gates were observed failing before the change, and failing for missing
   behaviour — not a typo, missing import, or unbuilt fixture.
2. `<test-full>` passes — the pytest suite *and* the vitest suite, including the
   Postgres-backed tier.
3. `<lint>` and `<build>` pass.
4. `<typecheck>` — no command exists; see the gap in Phase 0.

Items 2 and 4 are why the `AGENTS.md` Commands block must be filled in first.

---

## Decisions

**Settled:**

1. **No moves until both players have joined** (Phase 3). A product-direction
   change, decided by the user on 2026-09-09. It is also what closes the lock
   finding.
2. **pytest** as a backend dev dependency, **vitest** as a frontend one (Phase 0).
   Approved 2026-09-09. `jsdom` and `@testing-library/react` come with vitest;
   `@testing-library/jest-dom` was dropped as unnecessary.
3. **Done includes the new tests passing**, not merely that the code builds.
4. **Per-operation connections, not `psycopg-pool`.** Correct and dependency-free
   at this traffic level.
5. **The integration tier runs against a throwaway container on port 55432**, built
   from `backend/db.Dockerfile`, rather than against the compose `db` service.
   Assumed, not confirmed by the user — chosen because it needs no
   `docker-compose.yml` edit and therefore no Boundaries approval, and because it
   keeps test data off the development volume.

**Open:**

1. Explicit approval to write the Commands block into `AGENTS.md`. Its Boundaries
   forbid editing it without one. The block is in Phase 0.
2. Whether to add `ruff` and/or `mypy` so `<lint>` and `<typecheck>` cover the
   backend. No invariant here needs them; it affects only how complete the
   definition of done is.
3. Whether to collapse invariants 6 and 7 into 5. They assert mechanism rather than
   observable behaviour, which `AGENTS.md` discourages; dropping them removes two
   gates and most of the Postgres tier's complexity, at the cost of the only direct
   evidence that connections are actually separate and actually closed.
4. Whether `AGENTS.md` should state a freeze criterion. It defines a gates session
   that reads a "frozen plan" but never says when a plan becomes frozen, so the
   plan/grade loop has no exit but a human calling it. Suggested wording: *a plan
   freezes when no open finding would change an invariant.*

---

## What changed in this revision

Against the ten findings in `plans/live-game-integrity.review.md`:

- **The lock finding** — `join_session`'s lookup now answers `session_full` itself,
  so a full game never reaches the locked block. The previous revision only
  early-returned on a token match, which left a third visitor and a
  cleared-`sessionStorage` player falling through into the lock on a live game.
- **Invariant 4** — scoped to calls that reached the `on:true` emit, with the
  nine-path "emits nothing" case stated as a separate assertion.
- **The test database** — the compose `db` service publishes no host port, so the
  tier now runs against a throwaway container via `backend/scripts/testdb.sh`, with
  `conftest.py` forcing `DATABASE_URL` so the suite cannot hit a dev database.
- **Invariants 1 and 2** — 1 restated as the `join_session` emit count, which is
  what a mocked socket can actually observe; 2 repointed at the presence race,
  which is gate-able and false today, with end-to-end survival moved to manual
  verification because a backend-only test of it passes today.
- **Invariants 2, 5 and 7** — one `Latch` recipe added to Phase 0 so all three are
  interleaved. Sequentially, invariant 5's gate passed against today's code.
- **`opponentJoined`** — seeded inside `applySessionState` so Phase 1's reconnect
  path carries it.
- **The Phase 4 rename** — gates call through `op_db` in `conftest.py`, and
  updating that one function is now an item in Phase 4's file list.
- **Frontend config** — `test` block added to `vite.config.js` with
  `globals: false`, which also keeps `npm run lint` passing; `jest-dom` dropped.
- **Commands block** — each half wrapped in a subshell.
- **`player_joined`** — emitted after the transaction commits.
- Line references corrected: `GameView.jsx:83-142`, `create_session`'s loop
  `:42-50`, `say_move`'s prior-move checks `:182-193` and
  `waiting_for_opponent_move` raised at `:188`/`:193`.

## Out of scope

Deliberately excluded so they are not silently absorbed; all recorded in
`BACKLOG.md`.

- Moving the LLM call out of the transaction. Phase 3 removes the pressure to do
  it — nothing waits on the lock any more — so it stays deferred. It still deserves
  its own plan if concurrent play grows enough for backend count to matter.
- Tone continuity reset on dragged moves, missing game-over UI, the
  `JoinGameModal` null token, resign/draw, promotion choice, ERROR-level logging of
  routine client errors, migrations, rate limiting, README drift.
- Any directory restructuring. Reviewed 2026-09-08 and deliberately deferred.
