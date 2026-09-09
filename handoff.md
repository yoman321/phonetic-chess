# Handoff

Session: 2026-09-08 (second). Role: **Grade the plan**. No code was changed, and
`plans/live-game-integrity.md` was not edited.

## Where the work stands

`plans/live-game-integrity.md` was graded against the code and the installed
libraries. **It should not freeze.** Seven findings; F1-F5 need your arbitration
first.

The findings are recorded below rather than in `plans/live-game-integrity.review.md`
— that file has not been written yet. If you want the grading output in the location
`AGENTS.md` specifies, that is a one-file write.

## What checked out

Verified, not taken on faith:

- All ~25 file:line references in the plan resolve to what it says they do.
- `emitBuffered()` clears `sendBuffer` after flushing
  (`node_modules/socket.io-client/build/cjs/socket.js:626-633`), so a buffered emit is
  not replayed on a later connect. The Phase 1 diagnosis is right.
- `Transaction._push_savepoint` decides outer-vs-inner from
  `pgconn.transaction_status == IDLE` (`psycopg/transaction.py:209`); `Connection.lock`
  is a plain per-operation `Lock()` (`connection.py:77`); `Connection.__exit__` commits,
  rolls back, and closes unless `_pool` is set (`connection.py:155-176`). The
  savepoint-nesting corruption is real and the per-operation `with psycopg.connect()`
  mechanism is sound.
- `AuthenticationError` / `BadRequestError` appear in neither `except` tuple in
  `pick_move_with_llm` nor in `say_move`, so they do escape with the thinking
  indicator still on. The Phase 2 repro is valid.
- psycopg 3.3 detects gevent monkey-patching and skips `wait_c`
  (`psycopg/waiting.py:405-422,449`), so DB waits are cooperative under the deployed
  worker. Relevant to F1 and F6.

## Findings

### F1 — High. Phase 3 introduces an unbounded hang on a common user action

`plans/live-game-integrity.md:144-195`, and the exclusion at `:245-248`.

Phase 3 gives each operation its own transaction while the LLM call stays inside it.
Today two concurrent requests on one session share a connection, so the second nests
as a SAVEPOINT and never waits on the `FOR UPDATE` row lock. Once they are independent
transactions, the second blocks in Postgres until the first commits — up to ~2 minutes
(`llm.py:28-32`: `timeout=LLM_TIMEOUT` 30s x the SDK's 4 attempts, plus backoff).

`POST /sessions/<sid>/join` takes the same row lock (`queries/sessions.py:27-30`) and
runs on every `GameView` mount (`GameView.jsx:49`). Nothing bounds the wait:
`lock_timeout` defaults to 0 and nginx's `proxy_read_timeout` is 3600s.

Cleanest repro, two people and one game: A creates the game and immediately types a
phrase — `say_move` allows it before Black has joined (`sessions_ops.py:182-188`) — and
locks the row for the whole Groq call. B clicks the share link, `joinSession` hits
`FOR UPDATE` on that row, and B sits on "Loading session..." for up to two minutes.
Same shape for a mid-game refresh, a second tab, or the idle GC's DELETE at TTL.

The plan's Phase 3 verification uses two *different* games, which are different rows,
so it cannot see this.

**Smallest fix:** `SELECT ... FOR UPDATE NOWAIT` (or `options="-c lock_timeout=5000"`
on the connection), mapping `psycopg.errors.LockNotAvailable` to
`ApiError("busy", 409)`; and add a same-session step to the Phase 3 verification.
Otherwise sequence Phase 3 behind the LLM-outside-transaction work.

### F2 — Medium. Invariant 7 is not a gate

`plans/live-game-integrity.md:66-68`. "Connection count returns to baseline" passes
*before* the change: one connection is opened at import (`application.py:18`) and no
request opens or closes one, so the count is constant. `AGENTS.md`: "A gate that passes
before the work exists is not a gate."

**Smallest fix:** assert what separates the two designs — during N concurrent in-flight
operations the count rises by exactly N and returns to baseline after. Before: 0.
After: N.

### F3 — Medium. Invariant 4 is false for one of the outcomes it enumerates

`plans/live-game-integrity.md:58-61`. On the `llm_bad_response` path
`pick_move_with_llm` calls `on_retry` before each retry (`llm.py:132-141`) and
`_on_llm_retry` emits `{"on": True, "status": "retrying"}` (`sessions_ops.py:220-230`).
With `LLM_MAX_RETRIES=3` that outcome emits three `on:true`, not one, so a gate
asserting `count(on=True) == 1` for "mapped ApiError" fails against a correct
implementation.

**Smallest fix:** restate as "exactly one terminal `on:false` per `say_move`, and the
last `thinking` event emitted is `on:false`", counting `on:true` only where
`status == "thinking"`.

### F4 — Medium. The Phase 1 resync can silently rewind the board

`plans/live-game-integrity.md:82-89`. The resync fetch is async and unguarded: a `move`
broadcast that arrives and is applied between the fetch and its `.then()` is overwritten
by the older fetched FEN, and the missed move is never re-sent. That violates invariant
3 via the fix itself.

**Smallest fix:** bump a counter in `onMove`, capture it before the fetch, drop the
result if it changed. (`GET /sessions/:id` returns no `ply`, so a version compare means
deriving ply from the FEN's fullmove plus side-to-move.)

### F5 — Medium. `db.py` reads `DATABASE_URL` at import, before `load_dotenv()`

`plans/live-game-integrity.md:156-171`. The module-level read runs when
`application.py` imports it (`:1-12`), before `load_dotenv()` at `:14`. The current read
sits at `:16`, after it — that ordering is deliberate. Under the documented local path
(`README.md:47`, `cd backend && python application.py`) `DATABASE_URL` comes from
`backend/.env`, so the import raises `KeyError`. Invisible under Docker, so the plan's
own "Full pass" would not catch it.

**Smallest fix:** read the URL inside `connect()`, or pass it in from `application.py`.

### F6 — Low. "a rejoin inside the TTL is safe" overstates the code

`plans/live-game-integrity.md:106-108`, against `presence.py:20-27`. The membership
re-check is under `_presence_lock`, but the DELETE runs after the lock is released
(`:25-26`), and the greenlet yields there. A `join_session` landing in that window
leaves the client in a room for a deleted session. One DB round-trip wide, once per
TTL — but invariant 2 says "never", and a long gate could flake.

**Smallest fix:** move `sessions_q.delete_session` inside the lock, or scope invariant 2
to exclude the window.

### F7 — Nits

- `:179` says "Seven sites" then lists five operations; the other two are in `presence.py`.
- Phase 3 does not say whether `with db()` in `create_session` wraps the 5-attempt
  id-collision loop or sits inside it (`sessions_ops.py:42-50`). Both work; pick one so
  the builder doesn't.
- `:149` keeps the parameter name `pg` in `controller/sessions.py:7` while it now holds
  a factory — inconsistent with the `pg` -> `db` rename in `sessions_ops.py`.
- `:25` "neither player can retry": the opponent is already blocked by `!myTurn`
  (`GameView.jsx:316`). Only the mover is wrongly locked out.
- Phase 1 resyncs the board but not `messages`, so after a reconnect gap the board jumps
  forward and the chat has a hole. No invariant covers it; state it as a limit.

## Discussion on F1, for whoever revises the plan

Three things were ruled out in conversation, so they don't get re-litigated:

**Different games are not the issue.** A session is one row, and every action on that
game goes through it — `say` and `move` via `select_state_for_update`, `join` via
`select_tokens_for_update`, all `FOR UPDATE` on the same row. Two different games are
two different rows and never conflict.

**Named locks (Postgres advisory locks) do not fix it.** They change what you lock, not
how long you hold it, and they can't route around the conflict: even if `join_session`
dropped `FOR UPDATE` for `pg_advisory_xact_lock(hashtext(sid))`, its
`UPDATE sessions SET black_token = ...` still takes the row's write lock, which is the
lock `say_move` is holding. Postgres takes that one for you. `join` would block at the
UPDATE instead of the SELECT, for the same two minutes. The only thing advisory locks
add is non-blocking acquisition, and `FOR UPDATE NOWAIT` gives that with one keyword —
no hashing an 8-char base36 id into a bigint, no collision risk between unrelated games,
no session-level lock that leaks if `psycopg-pool` is swapped in later.

**So there are two real options.** Bound the wait (`FOR UPDATE NOWAIT` or
`lock_timeout`, mapped to a 409): small, ships with Phase 3, but B still can't join for
up to two minutes — they just find out immediately. Or remove the cause: read the FEN
unlocked, call the LLM, then open a short transaction that takes `FOR UPDATE`,
re-validates the FEN hasn't changed, and writes. Lock held ~1ms, nothing waits, no
timeout needed. That is the optimistic-concurrency work the plan deferred to its own
spec.

## Next step

Your arbitration on F1-F5, then a revised **Plan** session against
`plans/live-game-integrity.md`. Not a gates session — F2 and F3 mean two of the seven
invariants cannot produce a valid gate as written, so gates are blocked regardless of
the dependency decisions.

## Still blocking, unchanged from the previous session

1. **The `AGENTS.md` Commands block is placeholders.** `<test-full>`, `<typecheck>`,
   `<lint>`, `<build>` are all unfilled, so no session can meet the stated definition of
   done. The only real commands are `npm run lint` and `npm run build` in `frontend/`.
   Not edited — per its own Boundaries, that needs your say-so.
2. **pytest as a dev dependency**, for gates on invariants 4-7.
3. **A frontend test runner** (vitest or equivalent), for invariants 1-3. There is none
   in `package.json`.

## Out-of-scope finding

`docs/architecture.md:239` says `GROQ_API_KEY` is "required"; `llm.py:11` uses
`os.environ.get("GROQ_API_KEY", "")`, so a missing key starts fine and fails at the
first Groq call. Belongs in `BACKLOG.md`; not added there this session.
