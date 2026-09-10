# Grading: plans/live-game-integrity.md (revised 2026-09-09)

Role: Grade the plan. The artifact was produced by the prior Plan session, not by
this one. The plan was not edited.

Verified against the code, not read on its own terms. Ten findings. The first four
block freezing the plan; the first is the one the whole revision rests on.

---

## High

### 1. The `FOR UPDATE` wait F1 was about is not actually gone

**Where:** Phase 3, "Split `join_session` into lookup and claim" and "Why this closes F1".

The plan asserts "the lock cannot be contended for" because the claim path "is the
transaction that *sets the second one*" and "never runs again." That is false. The
claim block is reached by anyone whose token does not match either column, on a
game that is already full:

- a third person opening the share link — a plain `POST /sessions/:id/join`,
  which currently lands on `session_full` (`sessions_ops.py:86-87`) *after*
  `select_tokens_for_update` has already taken the row lock;
- a player whose `sessionStorage` was cleared — the plan's own "pre-existing
  limit" at the end of Phase 3;
- any stale or hand-supplied token.

In the plan's sketch, the lookup only early-returns on a *match*; every non-match
falls through into `with pg.transaction()`. Before Phase 4 that costs nothing,
because one shared connection nests into a savepoint rather than blocking. After
Phase 4 it is a real `FOR UPDATE` wait behind an in-flight `say_move`, which holds
that row for up to `LLM_TIMEOUT` (30s) × `LLM_MAX_RETRIES` (3) plus backoff
(`llm.py:14,21,22`). That is the F1 failure mode, reintroduced by the phase that
was supposed to remove it. Calling the lost-token case "not made worse" is wrong:
Phase 4 is what makes it a wait.

**Smallest fix:** decide the whole outcome from the unlocked lookup. If both token
columns are non-NULL and the presented token matches neither, raise `session_full`
there. Enter the locked block only when the lookup showed a free slot — which, by
invariant 8, means no move can be in flight. Two clients racing for the last slot
both enter the lock, one wins, the other gets `session_full`; that wait is one
`UPDATE`, not one LLM call.

### 2. Invariant 4 is false against a correct implementation

**Where:** Invariants, item 4.

"Every `say_move` emits exactly one terminal `thinking on:false`." `say_move` has
nine paths that raise *before* the `on:true` emit at `sessions_ops.py:214`:
`missing_text` (`:157`), `missing_token` (`:160`), `not_found` (`:166`),
`game_over` (`:169`), `not_a_player` (`:177`), `waiting_for_opponent_move`
(`:186`, `:192`), `not_your_turn` (`:197`), `no_legal_moves` (`:202`) — plus
`waiting_for_opponent_join`, which Phase 3 adds at the same altitude. Under the
plan's `try/finally`, which opens at the emit, all of those emit zero thinking
events. A gate written literally from this wording fails against correct code, and
the only ways out are relaxing the gate or contorting the implementation, both of
which `AGENTS.md` forbids.

**Smallest fix:** scope it — "every `say_move` that emits `thinking on:true` emits
exactly one `on:false` afterwards, and it is the last thinking event it emits" —
and name the fourth outcome explicitly: a call rejected before the LLM emits no
thinking events at all.

### 3. Phase 0's Postgres tier has no database it can reach

**Where:** Phase 0, "It runs against the compose `db` service: `docker compose up -d db`."

`docker-compose.yml` publishes exactly one host port, at `:56`, and it belongs to
`frontend`. The `db` service has `build`, `environment`, `volumes`, `healthcheck`
and no `ports:`, so it is reachable only inside the compose network. Host-run
pytest cannot connect to it. Invariants 5, 6 and 7 are precisely the three that
need it, and no `DATABASE_URL` for the test run is named anywhere in the plan.

**Smallest fix:** pick one and write it down. Either add
`ports: ["127.0.0.1:5432:5432"]` to the `db` service — a `docker-compose.yml`
change, which `AGENTS.md` Boundaries put behind a user decision, so it belongs in
the plan's Open list — or run the tier inside the container
(`docker compose run --rm backend pytest -q -m integration`) and put that in
`<test-full>` instead of the bare `pytest`. Either way, state the `DATABASE_URL`
the tier uses.

### 4. Invariant 2 has no test tier, and invariant 1 cannot be asserted as worded

**Where:** Invariants 1-2; Phase 0's tier table and Frontend section.

Invariant 2 asserts `SELECT count(*) FROM sessions WHERE id = :sid` == 1 at
TTL + 5s. The Phase 0 table lists only invariants 4, 5, 6, 7, 8; the frontend tier
is explicitly "No real server, no real backend." jsdom cannot run that SQL, so
invariant 2 has nowhere to live. It is the invariant covering the headline symptom
— the game getting deleted with both players at the board — so it cannot just be
dropped silently.

Invariant 1 has a milder version of the same problem: "the client is in the session
room… a `move` broadcast issued after the reconnect is received by that client,
received count == 1." With a hand-driven `vi.mock` socket, whether the client
receives a broadcast is whatever the test chooses to call. The only thing that
harness can actually observe is that the client emitted `join_session` after
`connect`.

**Smallest fix:** restate 1 as the emit assertion (`join_session` emit count == 1
per `connect` event, including reconnects — today: 1 total across N connects).
Give 2 a backend integration test that drives `presence.track_join` /
`remove_socket` against the real DB with `IDLE_TTL_SECONDS` shortened, and add it
to the Phase 0 table; or fold what it protects into 1 and say so.

---

## Medium

### 5. Invariant 5's gate passes today, and 5-7 never say how concurrency is produced

**Where:** Phase 0 tier table, rows for invariants 5, 6, 7.

Row 5 reads "two operations, one raises, assert the other's `fen` survived." Run
sequentially against today's code, that passes: B commits under `autocommit=True`,
then A opens its transaction and rolls back, and B is untouched. The corruption
needs A's transaction *open across* B's write. Per `AGENTS.md`, a gate that passes
before the work exists is not a gate. Invariant 7 has the same hole — "during N
concurrent in-flight operations" with no mechanism given for holding N operations
in flight inside pytest. (Invariant 6 survives sequentially, since each new
connection gets a new backend pid while the shared connection does not.)

**Smallest fix:** put the interleaving in Phase 0, not only in Phase 4's
verification prose. Stub `pick_move_with_llm` to block on a `threading.Event`;
start A in a thread and let it park inside its transaction; run B to completion;
release A with an exception; then assert B's `fen`. Say that the same event is what
holds N operations in flight for invariant 7.

### 6. `opponentJoined` is seeded outside `applySessionState`, so a reconnect strands white

**Where:** Phase 3 "Frontend" (seed "from `getSession`'s `both_joined` (`:39`)")
against Phase 1's `applySessionState`.

Phase 1 defines `applySessionState` as the extraction of `GameView.jsx:45-47` —
`game.load`, `setPosition`, `setEvalCp` — and that is what the reconnect path
calls. Phase 3 seeds `opponentJoined` at `:39`, in the mount effect, outside that
function. So: white's socket drops, black joins during the gap, `player_joined` is
broadcast to a room white is not in, white reconnects and refetches — and
`applySessionState` does not carry `both_joined`. `opponentJoined` stays false, the
chatbox and all four board handlers stay disabled, and only a page reload recovers.
Phase 1 exists to repair exactly this class of gap.

**Smallest fix:** set `opponentJoined` from `s.both_joined` inside
`applySessionState`, and have the mount effect seed it through the same function.

### 7. The unit gates for invariants 4 and 8 are written against a signature Phase 4 changes

**Where:** Phase 0 tier table (rows 4 and 8) against Phase 4's rename list.

Phases 2 and 3 land before Phase 4, so their gates call
`say_move(fake_conn, fake_socketio, …)`. Phase 4 then renames that parameter to
`db` and makes every operation call `db()` to obtain a connection. Every one of
those tests breaks at the phase boundary, and the build session hits it mid-phase
with no instruction covering it.

**Smallest fix:** put the fake behind one fixture in `backend/tests/conftest.py`,
and add "update the `db` fixture" as an explicit item in Phase 4's file list, so
the plumbing moves and the assertions do not.

### 8. `<lint>` and vitest are both broken as Phase 0 specifies them

**Where:** Phase 0 "Frontend" and the `<lint>` entry in the Commands block.

Three concrete gaps, all in the definition of done:

- `frontend/eslint.config.js` sets `globals: globals.browser` and extends
  `js.configs.recommended`, which enables `no-undef`. `describe`, `it`, `expect`
  and `vi` in new test files are undefined globals, so `npm run lint` — item 3 of
  the plan's own definition of done — fails the moment the gates exist.
- `frontend/vite.config.js` has no `test` block. "vitest reuses that config" is
  true of the build config only; without `environment: "jsdom"` the installed
  jsdom is never used and `@testing-library/react` fails on `document`.
- `@testing-library/jest-dom` registers nothing without a `setupFiles` entry.

**Smallest fix:** add `test: { environment: "jsdom", globals: true, setupFiles:
"./src/setupTests.js" }` to `vite.config.js`, and either add a vitest-globals
block for `**/*.test.jsx` to the eslint config, or import
`describe`/`it`/`expect`/`vi` from `"vitest"` in each test file, which needs no
eslint change at all.

---

## Low

### 9. The Commands block chains `cd`s, so everything after the first runs in the wrong directory

**Where:** Phase 0, `<setup>`, `<test-fast>`, `<test-full>`, `<test-single>`.

`cd backend && … ` followed by `cd frontend && npm install` resolves the second
`cd` relative to `backend/`. This block is meant to be pasted verbatim into
`AGENTS.md` and copy-pasted by every later session.

**Smallest fix:** wrap each half in a subshell — `(cd backend && …)`,
`(cd frontend && …)`.

### 10. `player_joined` is emitted before its transaction commits

**Where:** Phase 3, the claim-path sketch.

The emit sits inside `with pg.transaction()`. Every other emit in the file is
after the block — `make_move:151`, `say_move:272` — and Phase 2 reasons carefully
about exactly this ordering. If the commit fails, white's input enables for an
opponent who was never recorded, and their next move returns 409.

**Smallest fix:** emit after the `with` block, matching the file's idiom.

---

## Line references

Checked; three are off by a line or misattributed. `GameView.jsx`'s socket effect
is `:83-142`, not `:83-141`. `create_session`'s id-collision loop is `:42-50`, not
`:41-50`. Phase 3 says the input gate lives at `GameView.jsx:316` (correct) but
`BACKLOG.md` cites `:181` for hardcoded promotion, which is `:199` — outside this
plan, noted only because the two are read together.

Everything else verified as stated: `sessions_ops.py:214/220-230/239/243/272`,
`:109`, `:168`, `select_state_for_update` returning both token columns
(`queries/sessions.py:49`), `psycopg/connection.py:155-176` (`__exit__` commits,
rolls back, closes when not pooled), `connection.py:77` (`self.lock = Lock()`),
`transaction.py:209` (`_push_savepoint`), `socket.io-client@4.8.3`
`socket.js:625-632` (`emitBuffered` clears `sendBuffer`), and `commit()` in
autocommit mode returning early at `IDLE` rather than raising — which is what makes
Phase 4's `db()` context manager safe.
