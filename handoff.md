# Handoff

Session: 2026-09-09. Role: **Build**, `plans/live-game-integrity.md`.
The plan was not edited.

**Deviation from `AGENTS.md`, at the user's direction:** it says to build one
phase and stop at the boundary. The user asked for all of it in one session, so
Phases 1, 2, 3 and 4 were built in plan order, each phase's gates checked as it
landed. Every phase is now implemented.

## Where the work stands

All eight invariants hold. Nothing in the plan is left unbuilt.

```
backend  full   25 passed                       (0.8s, Postgres tier included)
backend  fast   20 passed, 5 deselected         (0.2s, no database)
frontend         4 passed                       (0.6s)
lint             eslint . — clean
build            vite build — 396.08 kB
```

The backend suite went from 20.5s to 0.8s. That is Phase 4: the old shared
connection made concurrent operations wedge client-side, and the tests had to
release parked operations one at a time and wait out timeouts.

## What changed

### Phase 1 — rejoin and resync (`GameView.jsx`)

`applySessionState(s)` extracted to component scope as a `useCallback`; both
effects use it. The `join_session` emit moved off effect setup onto a `connect`
handler, so a reconnect with a new socket id re-enters the room and cancels the
deletion timer. On a *re*connect (`hasConnectedRef`) it refetches the session and
applies it. `moveSeqRef` increments in `onMove` and is compared before the
refetch's `.then()` applies, so a broadcast landing mid-fetch wins over the older
fetched FEN.

### Phase 2 — the indicator always clears (`sessions_ops.say_move`)

Everything from the `thinking on:true` emit to the end of the transaction is
inside `try/finally`, with the single `on:false` in the `finally`. The two
duplicated emits in the `except` branches are deleted; the branches themselves
stay, for the error-code mapping the client switches on. Restoring those emits
would put two `on:false` on the mapped paths and break invariant 4 from the other
side.

### Phase 3 — no moves until both players have joined

`make_move` and `say_move` raise `waiting_for_opponent_join` 409 right after the
`status != "active"` check, before the token→colour mapping. `join_session` is
split: an unlocked `select_tokens` lookup answers a returning player and a full
game, and only a genuinely free colour reaches the `FOR UPDATE` claim.
`select_session_summary` gained `both_joined` — the boolean only, never the token
values, since `GET /sessions/:id` is unauthenticated. `player_joined` is emitted
after the claim commits. Frontend: `opponentJoined` state seeded inside
`applySessionState` (so the reconnect path carries it), from the join response,
and from `player_joined`; it gates the chatbox and all four board handlers; the
intro line and a chat line for the new error code.

### Phase 4 — one connection per operation

New `backend/db.py` with a `connect()` context manager reading `DATABASE_URL`
per call, not at import — `application.py` calls `load_dotenv()` after its import
block, so a module-level read would `KeyError` under `cd backend && python
application.py`. `application.py` passes `db.connect` (the factory) to
`presence.init` and `make_sessions_bp`; the module-level `pg` is gone. All five
operations open their own. `create_session`'s connection and cursor sit outside
the 5-attempt id loop. `presence` holds a factory and, separately, now issues its
DELETE **inside** `_presence_lock` rather than after releasing it.

## What was verified

- Every phase's gates were observed failing before its change, each on its own
  assertion. Phase 1: `expected 1 to be 2`, and the board still at the start FEN.
  Phase 2: four indicator tests. Phase 3: five gating tests. Phase 4: three
  connection tests plus the presence race.
- `<test-full>` passes: backend 25/25 including the Postgres tier, frontend 4/4.
  `<lint>` and `<build>` pass.
- `<typecheck>` has no command and `<lint>` is frontend-only, so both are vacuous
  on the backend — not passing. In their place, the backend diff was read for
  what they would have caught: every changed signature has exactly one call site
  and it matches (`join_session` gained `socketio`; five operations renamed
  `pg`→`db`); `_db` is None only before `init`, as `_pg` was; the new `ApiError`
  is mapped end to end, code → 409 → `err.message` → chat line.
- Beyond the gates, two behaviours no automated test covers were driven through
  the real Flask wiring:
  - Full lifecycle: create → `both_joined:false` and no tokens in the payload →
    move and say both 409 `waiting_for_opponent_join` → black claims
    (`opponentJoined:true`) → `both_joined:true` → move 200 → third visitor
    `session_full` → black returns with their token.
  - **The lock finding, closed.** With a `say_move` parked mid-LLM holding the
    row on session X, `POST /sessions/X/join` returned in 1.6 ms with a stored
    token and 2.0 ms with none on a full game. Two prior plan revisions got this
    wrong; two *different* games cannot surface it, so the check is same-session.

## Not done

**The manual browser passes have not been run** — Phase 1's forced reconnect with
`IDLE_TTL_SECONDS=30`, Phase 3's second-tab check, and the Full pass
(`docker compose up --build`, one game to checkmate mixing dragged and tone
moves). They need a running stack and a human at two browsers. Phase 1's is the
only check covering end-to-end survival across a reconnect; invariant 1
deliberately does not.

**The chat hole stays**, by design: a reconnect resyncs the board, not
`messages`. Already in `BACKLOG.md`.

## Findings for arbitration

Not acted on, per `AGENTS.md`.

1. **Phase 4's file list is short by one.** It says `tests/conftest.py`'s only
   change is `op_db` → `lambda: conn`. But the operations now do
   `with db() as pg`, and `FakeConnection` had no `__enter__`/`__exit__` — every
   fake-tier test would error. Added them, four lines; no assertion moved and no
   gate was relaxed. Flagging rather than deciding it.
2. **The plan's `logger.info` for `waiting_for_opponent_join` breaks with the
   surrounding idiom**, which is `logger.error` for every rejection in that file.
   Followed the plan. `BACKLOG.md` separately wants those 21 `logger.error` sites
   demoted, so the plan is arguably ahead of the file — but the file is now mixed.
3. **`docs/architecture.md` was corrected**, which no phase's file list includes.
   Its "Structural notes" described the single shared connection and "no test
   suite" as current fact, and the idle-GC and API sections were falsified by
   Phases 3 and 4. Only sentences this work made false were touched.
4. Carried forward, still undecided: whether to collapse invariants 6 and 7 into
   5, and whether `AGENTS.md` should state a freeze criterion. Both are now
   cheaper to decide — 6 and 7 pass, and the Postgres tier costs 0.8s, not 20.

Also carried forward: the `game_over` `TypeError` is still open in `BACKLOG.md`
and still out of scope. It means every attempt to move in a finished game returns
a 500 with no error code.

## Files

New: `backend/db.py`.

Changed: `backend/application.py`, `backend/controller/sessions.py`,
`backend/controller_operations/sessions_ops.py`,
`backend/controller_operations/presence.py`, `backend/queries/sessions.py`,
`backend/tests/conftest.py`, `frontend/src/modules/GameView/GameView.jsx`,
`docs/architecture.md`, `docs/gotchas.md`.

Not touched: `AGENTS.md`, `plans/`, `BACKLOG.md`, the four gate test files.

`docs/gotchas.md` lost three entries whose cause Phase 4 fixed — leftover rows
from a held row lock, concurrent operations wedging in `Transaction.__exit__`,
and the teardown `statement timeout` warning.

## Next step

Coding and the automated tiers are finished. Four of the five roles in
`AGENTS.md` have run — Plan, Grade the plan, Write the gates, Build. **Review the
build has not.** In order:

**1. A Review-the-build session.** Read the diff against the frozen plan; write
`plans/live-game-integrity.impl-review.md`. Severity, location, why it breaks,
smallest fix — no praise, no summary of the diff. Do not edit the code from that
session.

This carries more weight than usual: all four phases were built in one session
at the user's direction rather than stopping at each boundary, so nothing checked
the work between phases. `AGENTS.md` is also explicit that a clean review is not
verification — it does not replace the gates, it is a second signal.

Start with the four findings above, then these, which are where a single-session
build is most likely to have drifted:

- `say_move`'s `finally` fires *inside* the transaction on the success path,
  before the `move` emit after commit. Intended (the plan argues it, and the
  client clears `thinkingSide` on both events) — worth confirming, not assuming.
- `join_session` now opens two connections on the claim path, sequentially. The
  plan says so; check they never overlap.
- `create_session` holds one connection and one cursor across all five id
  attempts. Safe only because `autocommit=True` leaves no failed transaction open
  after a `UniqueViolation`.
- `presence._delete_session` now holds `_presence_lock` across a connect and a
  DELETE. That is the fix, but the lock is held longer than it was.

**2. The manual passes.** Three checks in the plan's Verification section that no
automated test covers, all needing two browsers and a running stack:

- **Phase 1** — force a reconnect with `getSocket().io.engine.close()` at
  `IDLE_TTL_SECONDS=30`. Moves must keep arriving and the session must outlive
  TTL + 5s. This is the *only* end-to-end proof that a network blip no longer
  deletes a live game; invariant 1 deliberately does not cover it.
- **Phase 3** — open the share link in a second tab; the first tab's input must
  enable on `player_joined` with no refresh. Then the reconnect ordering: with
  white's socket closed, join as black, let white reconnect — white's input must
  enable from the refetch alone, with no `player_joined` received.
- **Full pass** — `docker compose up --build`, one game to checkmate using both a
  dragged and a tone move, eval bar updating, both clients in sync.

Offered and not taken this session: driving these through Chrome instead.

**3. Arbitration.** The four findings above are for a human. `AGENTS.md`: findings
are arbitrated by a human, do not act on them.

**Not committed.** Every change is in the working tree. `AGENTS.md` Boundaries
forbid this session committing, pushing, tagging, merging, or opening a PR — that
is the user's, once the review clears.

## What a later session should not redo

- **Do not re-relax `tests/test_presence_delete_race.py`** to the plan's
  `count(*) == 1` wording. The narrower assertion is deliberate and was settled
  by the user on 2026-09-09; the plan carries an inline note saying so.
- **Do not restore `say_move`'s per-`except` `thinking off` emits.** Two
  `on:false` on the mapped paths breaks invariant 4 from the other side.
- **Do not read `DATABASE_URL` at import in `db.py`.** `application.py` calls
  `load_dotenv()` after its import block, so it would `KeyError` under the
  documented local path and stay invisible under Docker.
