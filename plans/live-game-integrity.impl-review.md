# Implementation review — live game integrity

Reviewed: `858dc2a` against `a8030ba`, read against the frozen
`plans/live-game-integrity.md`. Reviewer did not build this.

Suite re-run independently before reviewing: backend 25 passed (Postgres tier
included, 0.84s), backend fast 20 passed / 5 deselected, frontend 4 passed,
`eslint .` clean, `vite build` 396.08 kB. `<typecheck>` has no command and
`<lint>` is frontend-only, so neither covers the backend; they are vacuous here,
not passing.

Five findings. Two are code, two are gate coverage, one is a silent-failure note.

---

## 1. HIGH — a fired cleanup timer blocks room joins and new games across all sessions for the length of an LLM call

**Location:** `backend/controller_operations/presence.py:20-30`.

**Why it breaks.** Phase 4 moved the DELETE inside `_presence_lock`. A
`DELETE FROM sessions WHERE id = %s` blocks on the row lock that `say_move`
holds via `select_state_for_update` (`sessions_ops.py:193`) for the whole Groq
call — `LLM_TIMEOUT` 30s x `LLM_MAX_RETRIES` 3 plus backoff (`llm.py:14,21,22`),
so ~90s and up. `_presence_lock` is held for all of it, and it is a single
process-wide lock: `track_join` and `cancel_cleanup` both take it
(`presence.py:58-60`, `:44-48`) and are both called from `on_join_session`
(`controller/sockets.py:20-21`). The database row lock is per session; this one
is not, so the wait is not confined to the session being deleted.

**Scope, measured rather than argued.** With session X's DELETE parked, every
presence call for an unrelated session Y blocked for as long as the park lasted
and returned in 0.000s the moment it cleared — `track_join`, `cancel_cleanup`,
`schedule_cleanup` and `remove_socket` alike.

Only four call sites take the lock: `sockets.py:20-21` (socket join),
`sockets.py:25-26` (disconnect), and `sessions_ops.py:49` (`create_session`, which
blocks there while holding an open database connection). So what stalls
process-wide is **joining a room, disconnect bookkeeping, and new game creation**.

What does *not* stall, on any session: `make_move`, `say_move`, `get_session` and
the HTTP `join_session` never touch `_presence_lock`. Games already in progress
keep working — moves post, the LLM runs, broadcasts go out. The player-visible
failure is narrower than "the server hangs": a client loading or reconnecting to
any game during the stall gets a usable board and can move, but never enters the
room, so it receives none of the opponent's moves until the stall clears.

This is a stall, not a deadlock — there is no cycle, and the DELETE proceeds once
`say_move` commits.

Reachable because `_delete_session`'s re-check (`:24`) only requires the *room*
to be empty, and an in-flight `say_move` needs no socket — the HTTP POST
continues after the socket drops. That is the exact network-blip case Phase 1
exists for. It is directly reachable at `IDLE_TTL_SECONDS=30`, which the plan's
own Phase 1 verification instructs the tester to set.

Confirmed empirically against Postgres 16: with a row held by
`SELECT ... FOR UPDATE` in one connection, `DELETE` on that row in a second
blocked and returned only when cut off by `lock_timeout`.

**Smallest fix.** The connection is fresh and short-lived, so bound the wait on
it before the DELETE and let a failure fall through to the next schedule:

```python
with _db() as pg, pg.cursor() as cur:
    cur.execute("SET lock_timeout = '1s'")
    sessions_q.delete_session(cur, sid)
```

Invariant 2's assertion — no DELETE issued while a socket is tracked — is
untouched by this; the re-check and the DELETE both stay inside the lock.

---

## 2. MEDIUM — "never return the token values" is a stated security rule with no gate

**Location:** `backend/queries/sessions.py:18-27`, `select_session_summary`.

**Why it breaks.** `GET /sessions/:id` is unauthenticated, and the plan is
explicit that a leaked token is a stolen colour. That endpoint's payload is now
produced by a query that reads both token columns in order to compute
`both_joined`. Nothing asserts the response contains no token. A later edit that
selects `white_token` — to compute a second flag, or by copying the
`select_tokens` body — hands a colour to anyone holding the share link, and
every one of the 29 tests stays green.

**Smallest fix.** One assertion in `backend/tests/test_move_gating.py`:

```python
assert not any("token" in k for k in sessions_ops.get_session(op_db(conn), sid))
```

---

## 3. MEDIUM — the board-handler half of Phase 3 is untested

**Location:** `frontend/src/modules/GameView/GameView.jsx:249, 263, 270, 283`,
and the input gate at `:368`.

**Why it breaks.** The plan states the reason for these four edits directly:
"Gate the board handlers too, or dragging bypasses the chatbox restriction."
That is the entire client-side point of Phase 3. The frontend gates cover
invariants 1 and 3 only, and invariant 8 is backend-only, so deleting
`|| !opponentJoined` from any of the four handlers leaves the full suite green
and restores the defect Phase 3 was written to close.

**Smallest fix.** One vitest case: render with `getSession` stubbed to
`both_joined: false`, fire a square click on a piece of the player's colour,
assert `postMove` call count is 0.

---

## 4. LOW — the ten-case rejection gate asserts only that *something* raised

**Location:** `backend/tests/test_say_move_indicator.py:126`,
`with pytest.raises(Exception)`.

**Why it breaks.** Nine of the ten parametrised cases have a knowable `ApiError`
code; the blanket `Exception` exists only for `game_over`, whose pre-existing
`TypeError` is documented in the comment above the table. As written, a case that
begins raising an entirely different error — including an `AttributeError` from a
typo — still passes. The thinking-event count assertion carries invariant 4, so
the gate is not hollow, but the parametrisation no longer proves that the named
rejection is the one that fired.

**Smallest fix.** Carry the expected code in each tuple and assert it for the
nine; keep `game_over` as the documented exception until its `TypeError` is
fixed.

---

## 5. LOW — `opponentJoined` fails closed on a missing field, silently and with no recovery

**Location:** `frontend/src/modules/GameView/GameView.jsx:43` and `:67-68`, with the
initial state at `:31`.

**Why it breaks.** The state starts `false` and both writes are guarded by
`typeof ... === "boolean"`. A payload missing `both_joined` or `opponentJoined` —
a frontend bundle newer than the backend it talks to — leaves the board and the
chat input permanently disabled, with no message explaining why and no path back
short of the opponent's `player_joined` arriving. Failing closed is the right
default and the plan asked for these guards; the objection is that the failure is
indistinguishable from "your opponent has not arrived yet."

**Smallest fix.** None if intended. Otherwise treat a missing field as `true`
where `s.status === "active"` and the client already holds a colour, or surface
it as a system chat line.

---

## On the four items the handoff flagged for this session

Each was checked; none is a defect.

**`say_move`'s `finally` fires inside the transaction on the success path.**
Correct as built. The one consequence worth stating: if the COMMIT itself fails,
the client has already received `thinking off` and never receives `move`, so the
board stays put and the mover's input re-enables. That is the right outcome, and
it is why the early clear is safe rather than merely harmless.

**`join_session`'s two connections never overlap.** Confirmed by reading
`sessions_ops.py:65-113`. Every exit from the lookup block — the returning-player
`return`, the `session_full` raise, and the fallthrough — leaves the `with` before
the claim block opens its own. `db()` is called twice, sequentially.

**`create_session` holds one cursor across all five id attempts.** Safe, and the
handoff's stated reason is the correct one. Confirmed empirically: under
`autocommit=True`, psycopg 3.3.3 leaves no aborted transaction after a
`UniqueViolation` and the same cursor executes the next INSERT successfully. Note
`logger.error` and the `raise` at `:51-52` sit outside the `with`, so the
connection closes before the 500 — correct.

**`presence._delete_session` holds the lock longer.** That is finding 1.

## On the handoff's own four findings

1. **`conftest.py`'s `FakeConnection.__enter__`/`__exit__`.** Agreed: a plan
   defect, not a build deviation. `with db() as pg` mechanically requires them
   and no assertion moved. One consequence the handoff does not draw:
   `op_db` returns `lambda: conn`, so both `db()` calls in `join_session` hand
   back the *same* `FakeConnection`. The fast tier therefore cannot observe that
   the lookup and the claim use different connections — only the Postgres tier
   can. Not a defect; a limit on what the fake tier proves.
2. **`logger.info` for `waiting_for_opponent_join` against 21 `logger.error`
   sites.** Confirmed, cosmetic, and the plan's call. Arbitration item.
3. **`docs/architecture.md` edited outside any phase's file list.** Correct to
   flag. The edits are confined to sentences this work falsified. No change
   needed.
4. **Invariants 6/7, and an `AGENTS.md` freeze criterion.** Human decisions,
   untouched.

## Not covered by this review

The three manual browser passes in the plan's Verification section remain unrun,
including the only end-to-end check that a reconnect no longer deletes a live
game. Finding 1 interacts with the Phase 1 pass directly: at the
`IDLE_TTL_SECONDS=30` that pass prescribes, a phrase sent from a client whose
socket has dropped can stall the join it is about to attempt.
