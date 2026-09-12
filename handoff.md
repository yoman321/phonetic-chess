# Handoff

Session: 2026-09-12. Role: **Build**, `plans/presence-table.md`, **all six
phases**. Nothing committed.

# `plans/presence-table.md` is DONE

**Closed by the user on 2026-09-12.** All six phases built, all eleven invariants
gated and green, and every one of them driven through the real app in a browser.
The next piece of work is a different feature — see *Next step*.

**The build review was skipped, by decision.** `AGENTS.md` Sessions would have a
separate session write `plans/presence-table.impl-review.md` before this closed.
The user chose to move on instead. Recorded here because the rule was set aside
deliberately, not overlooked — if the presence code later misbehaves, know that no
second pair of eyes ever read this diff. The two things a reviewer would have been
pointed at are listed under *What a reviewer never looked at*.

**The plan file itself was not edited.** `plans/presence-table.md` froze on
2026-09-12 and its own header forbids edits by any session, so "done" is recorded
here, which is where decisions that outlive a plan already live.

## Summary of the closed work

**The plan is implemented and everything is green.** Presence lives in Postgres,
nothing is deleted, nothing runs in the background, and the deadline is enforced
on every write path with the client showing the final position under a modal. All
41 backend tests pass, all 6 frontend tests pass, lint and build are clean.

One gate had to be arbitrated: invariant 7's contradicted invariant 3 and could
not pass against any correct implementation. **The user decided on 2026-09-12 to
follow invariant 3** — the stamp is written on every disconnect, and the gate was
narrowed to match. See below.

The user asked for the whole plan in one session rather than one phase at a time.
Phases were still built and verified in order; the per-phase results are below so
the boundaries are still visible.

## What changed

### Phase 1 — schema

`backend/schema.sql`, 30 lines appended, verbatim from the plan: the
`last_disconnected_at` column, the `session_connections` table
(`(session_id, socket_sid)` PK, `ON DELETE CASCADE`), the `socket_sid` index, the
unconditional `session_mark_last_disconnect()` function, and the
`AFTER DELETE ... FOR EACH ROW` trigger. The plan's comment above the function
was kept, with its closing "see below" — a pointer into the plan, not into the
file — replaced by the fact it referred to.

### Phase 2 — the connections module and the lock downgrade

`backend/queries/connections.py` (new): `track` (`ON CONFLICT DO NOTHING`),
`release`, `release_all`, `is_idle(cur, sid, ttl_seconds)`. Plain functions
taking a cursor, matching the `queries/` idiom.

`queries/sessions.py:61` — `select_state_for_update`, `FOR UPDATE` →
`FOR NO KEY UPDATE`, name kept. `select_tokens_for_update` (`:41`) left alone,
per the plan.

### Phase 3 — presence rewrite and callers

`controller_operations/presence.py` rewritten. Gone: `_presence_lock`,
`_active_sids`, `_cleanup_timers`, `_delete_session`, `schedule_cleanup`,
`cancel_cleanup`, `reschedule_existing_sessions`. What remains is `init`,
`track_join`, `remove_socket`, `is_idle`, `clear_connections` — each a thin
wrapper opening its own connection. Holds no state, starts nothing.

`controller/sockets.py` — `on_join_session` drops `cancel_cleanup`;
`on_disconnect` calls `remove_socket` and drops the `schedule_cleanup` loop.

`sessions_ops.py` — the `presence` import became
`from controller_operations.presence import IDLE_TTL_SECONDS`; the
`schedule_cleanup` call in `create_session` is gone.

`application.py:22` — `reschedule_existing_sessions()` became
`presence.clear_connections()`.

`queries/sessions.py` — `delete_session` and `select_all_session_ids` deleted,
dead once presence stopped calling them. `select_tokens`' docstring no longer
argues its safety from "only delete_session removes the row".

`tests/conftest.py` — the `reschedule_existing_sessions` comment at `:17` rewritten
to name the boot wipe and its destructive side effect; the `IDLE_TTL_SECONDS=3600`
comment rewritten to its new reason; `clean_presence` deleted.
`tests/test_presence_delete_race.py` deleted.

### Phase 4 — enforcement

`select_session_summary(cur, sid, ttl_seconds)` gained the `ended` column,
computed by the plan's predicate. `get_session` passes `IDLE_TTL_SECONDS`. Its
one caller was updated; there are no others.

`join_session`, `make_move` and `say_move` raise `ApiError("game_ended", 410)` at
the call sites the plan named — join after `select_tokens` raises `not_found` and
before the returning-player branch; the move paths inside the existing locked
transaction, immediately after `select_state_for_update` and **before** the
`status != "active"` check. No keyword named `status`, so `game_ended` does not
hit the `game_over` `TypeError`.

`on_join_session` checks the deadline before joining the room and before
inserting its row, and emits `game_ended` to the caller when it refuses.

### Phase 5 — the client

`GameEndedModal.jsx` and `GameEndedModal.css` (new) — the menu's modal shape
restated rather than imported, so `GameView` does not depend on `Menu.css`.
"New game" navigates to `/` with `state: { openModal: "create" }`; "Back to menu"
navigates plainly.

`Menu.jsx` seeds `openModal` from `useLocation().state`.

`GameView.jsx` — an `ended` flag; the load effect applies the session state, sets
the flag and returns **without** joining when `s.ended`; a dedicated render branch
between the `validating` and `fatalError` blocks; a `game_ended` socket listener
alongside `move` and `thinking`.

### Phase 6 — docs

`docs/architecture.md` — the Idle GC paragraph replaced (the predicate, why the
trigger is unconditional, the disconnect wait, the boot wipe and restart grace,
why there is no heartbeat, the lock downgrade); the `:214` claim that
"abandonment is a row delete" corrected; the `-w 1` rationale rewritten to say
this is a prerequisite for multiple workers rather than the thing that achieves
them; the data model now three tables; the HTTP table's `ended` and `game_ended`;
the WebSocket section's refusal and new event; the `IDLE_TTL_SECONDS` entry.

`README.md` — a new "Applying a schema change" section with the `psql -f` line.
`backend/Dockerfile:24` — the comment no longer names `presence.reschedule`.
`BACKLOG.md` — four entries under "Found while working".

## The gate that was arbitrated

`test_a_game_past_its_deadline_keeps_its_row_its_moves_and_its_status` failed
with:

```
AssertionError: presence modified the game row
{'last_disconnected_at': datetime(2026, 9, 12, 13, 53, 8, ...)} != {'last_disconnected_at': None}
```

`assert after == before` compared the **whole session row** across a join and a
disconnect — but invariant 3 requires `last_disconnected_at` to be written on
exactly that event. The two gates contradicted each other and no correct
implementation could satisfy both.

Invariant 7 as the plan words it is "presence never deletes a session row, never
deletes a `moves` row, and never writes `sessions.status`". The test's other three
assertions — row still present, `status == "active"`, the move still there —
already cover that and were passing. Only the whole-row equality was wrong, and it
asserted the shape rather than the invariant.

**The user decided on 2026-09-12 to follow invariant 3.** The gate now compares
the row through a `_except_the_stamp` helper that drops the one column a
disconnect is entitled to write, with the reasoning in the test's docstring so the
exclusion does not read as an oversight later.

**It was not weakened into passing.** Eight columns are still compared — `id`,
`fen`, `pgn`, `white_token`, `black_token`, `status`, `created_at`, `updated_at`
— so a presence path that touched the board, a token, or the status would still
fail it, as would the deletion the other two assertions catch. Only
`last_disconnected_at` is excluded, and writing it is the behaviour invariant 3
mandates and `test_last_disconnect_stamp.py` independently gates.

This is the only gate this session changed. The plan was not edited.

## Deviations from the frozen plan

`plans/presence-table.md` was not edited. Four departures, all recorded here:

**1. `clean_presence` was deleted, not converted.** The plan says it "becomes a
delete of the fixture's own connection rows through `pgdb`", and separately says
`test_presence_delete_race.py` — "the only consumer of `clean_presence`" — is
deleted. Both together leave a fixture nothing uses. Deleting it matches what the
plan does with `select_all_session_ids` and `delete_session` in the same phase. If
a later gate wants the fixture, write it then, against a real need.

**2. `FakeCursor` learned a fifth query shape.** The plan's "Tests that must
change" did not foresee this. The fast tier answers by SQL text, and the new idle
predicate starts with `SELECT` and contains `FROM sessions`, so it was being
handed a session row and raising `KeyError: 'idle'` — it broke ten pre-existing
unit tests. `FakeCursor` now matches `AS idle` first and answers `{"idle": False}`:
these fakes describe a game someone is playing, and the deadline is asserted
against a real database in the integration tier where the clock is real. This is a
fake keeping up with the code, not a gate being relaxed.

**3. A docstring on `select_state_for_update`** explaining the downgrade. It is
the one line in the repo a later reader would "fix" back on sight, and
`select_tokens` two functions above already carries this kind of docstring
defending its own lock choice.

**4. `presence.is_idle`'s default is resolved per call**, not bound at def time.
Written first as `def is_idle(sid, ttl_seconds=IDLE_TTL_SECONDS)`, which freezes
the value at import and silently ignores a test that rebinds
`presence.IDLE_TTL_SECONDS` — exactly the class of error the backend's
read-instead-of-typecheck is meant to catch. Now `ttl_seconds=None` with the
module global read inside.

**5. Invariant 7's gate was narrowed**, on the user's decision of 2026-09-12 to
follow invariant 3 where the two collided. Its own section above.

**Standing, from the gates session:** invariant 6's socket-level timing test was
dropped, decided by the user on 2026-09-12, because `on_join_session` opened no
database connection and the test could not fail at any point in the sequence.
Invariant 6's claim is asserted by `test_fk_check_passes_a_locked_session.py`.
Note that `on_join_session` now *does* open one, via the idle check — but the
timing test still would not fail, because the phase 2 downgrade is what keeps that
connection from blocking, and it landed first. Do not re-add it.

## Verification

```
                         before   p1    p2    p3    p4    p5/p6   final
backend, full tier       18 F     15 F  14 F  11 F   1 F   1 F    41 passed
backend, fast tier       20 P     20 P  20 P  20 P  20 P  20 P    20 passed, 21 deselected
frontend                  2 F      2 F   2 F   2 F   2 F   6 P    6 passed
frontend lint             clean ............................. clean
frontend build            clean ............................. clean, 397.44 kB
```

The step from `1 F` to green is the arbitrated gate above, not a code change.

`<typecheck>` has no command and `<lint>` is frontend-only, so **neither covers
the backend — vacuous, not passing.**

### What was done instead, on the backend

Read the diff for the classes of error a type checker would catch, and ran three
things the tests do not cover:

**Every call site of every changed signature was grepped, not assumed.**
`select_session_summary` gained a third parameter and has exactly one caller,
updated. `connections_q.is_idle` takes three arguments at all four call sites.
`presence.is_idle` takes one at its only call site. No references survive to any
of the seven deleted presence symbols or the two deleted query functions.

**`connections.py` was exercised against the live database**, because nothing
called it when it was written and a bad column name would have surfaced two phases
later as someone else's bug. All four functions, every branch of the predicate:
duplicate `track` is a no-op, liveness beats the clock, `release` fires the
trigger, `release_all` clears. `is_idle` cannot return `None` — `NOT EXISTS` never
yields `NULL` and `COALESCE` covers the nullable column — and returns `False` for
a session id that does not exist, which is unreachable from every call site since
each has already established the row exists.

**The app was booted and the whole enforcement path driven over HTTP**, which no
test does end to end:

```
GET fresh     : ended = False
GET idle      : 200 ended = True | fen present: True | token-free: True
  POST join   : 410 {'error': 'game_ended'}
  POST move   : 410 {'error': 'game_ended'}
  POST say    : 410 {'error': 'game_ended'}
board unchanged: True | status: active
```

That is the plan's manual steps 2 and 4, and invariants 4, 5 and 7 together. The
boot wipe ran at import against the development database without incident.

No new exception type is raised anywhere — `game_ended` is an `ApiError`, already
mapped by the single `app_errorhandler` — so there is nothing new for the
controller layer to handle.

`flask_socketio.emit`'s source was read rather than recalled: with neither `to`
nor `broadcast` set it replies only to the originating client, which is what the
socket refusal needs. `react-chessboard`'s `onPieceDrop` and `onSquareClick` are
optional-chained in the installed build, so the ended branch omitting them leaves
the board read-only and drops rejected — deliberate, not an oversight.

### The browser pass — done, 2026-09-12

Backend on `:5001` and Vite on `:5173`, driven through Chrome. The plan's manual
list, step by step:

**1. The disconnect stamps.** A live game's socket inserted its row on join
(`last_disconnected_at` NULL); navigating away deleted it and the trigger stamped
the game. **Took ~50s, not instantly** — see the finding below.

**2. Reopen past the window.** The modal renders over the final position, two
buttons, board oriented white. The backend access log shows only
`GET /sessions/:id` — **no `POST /join` and no `/socket.io/` handshake at all**,
which is invariant 11 in the real app rather than against a mocked socket. The
row, its moves and `status = active` were untouched. Both buttons go where they
should: "New game" lands on `/` with the colour picker already open, "Back to
menu" on the plain menu.

**3. Reopen inside the window.** The same game, opened minutes earlier, joined
normally — board, chatbox, colour assigned, connection row present. The *same*
game after its deadline showed the modal, so the clock is running from the
disconnect rather than from `created_at`. Steps 2 and 3 on one session.

**4. Write paths past the window.** All three return 410 `game_ended` with the
board unchanged — done over HTTP against the running server.

**5. Nothing happens on its own.** No process is running that could touch a quiet
game; `pg_stat_activity` showed no background connection.

**6. Restart grace.** With a tab open, restarting the backend wiped the
connection row, and the trigger stamped `last_disconnected_at = NOW()` — a fresh
full window from boot, `ended: false` immediately after. The browser's socket
then re-registered under a **new** socket id 1.5s later, unprompted.

**7. The HIGH finding — the one this work exists to close.** Timed both ways,
with the LLM pointed at a black-hole port so `say_move` held its row lock for a
real interval instead of the 0.6s a healthy Groq call takes:

| `select_state_for_update` | socket connect + join | connection row |
|---|---|---|
| `FOR NO KEY UPDATE` (shipped) | 6 ms | **inserted 11.5s into the parked call** |
| plain `FOR UPDATE` (control) | 6 ms | **never — `INSERT` stuck in `wait_event_type = Lock`, 0 rows** |

The control was produced by `sed`-ing the downgrade back out, restarting, and
reverting; `git diff` confirms the file is byte-identical to before, and the
suite is 41 passed after. The emit itself is 6 ms either way because
`socket.emit` is fire-and-forget — the difference is entirely server-side, which
is why the row is the thing to measure and the round trip is not.

That is the HIGH finding reproduced and then shown fixed, against a real
PostgreSQL, rather than argued from the manual.

### One finding from the browser pass

**A hard disconnect takes ~45-50s to register, not milliseconds.** Navigating
away from a game left the connection row in place for about 50 seconds before the
stamp landed. This is engine.io's heartbeat doing exactly what the plan's "Why
there is no heartbeat" section says it does — ping every 25s, 20s timeout, reaped
by the server's service task — and not a defect. Worth writing down because it
has two consequences nobody has stated:

- `IDLE_TTL_SECONDS` effectively carries up to ~45s of slack on any disconnect
  that is not a clean client close. In the safe direction: the game stays
  joinable slightly longer.
- A test or a manual check that closes a tab and expects the stamp within a
  second will look broken when it is not. Wait the full ~50s.

Not filed as a defect and not in `BACKLOG.md`, because nothing needs fixing.

## Where the schema lives

| Database | How it got there |
|---|---|
| test, `127.0.0.1:55432` | `psql -f backend/schema.sql`, by hand |
| development, `127.0.0.1:5432` | `psql "$DATABASE_URL" -f backend/schema.sql`, by hand |
| a fresh test database | `db.Dockerfile` bakes the file into initdb |

`schema.sql` stays safe to re-run — every statement is `IF NOT EXISTS` or
`OR REPLACE`, except the trigger, which is `DROP IF EXISTS` then `CREATE`.

There is no migration runner. **The 55432 database was migrated in place, not
rebuilt** — it is a local `initdb` cluster under `TMPDIR`, not Docker (no Docker
daemon here, so `testdb.sh` takes its `local_up` branch). `local_up` runs
`schema.sql` only when it *creates* the database, so `testdb.sh down && up`
rebuilds clean, but a kept cluster needs `psql -f` by hand.

## Next step

**Playing against a bot.** Decided by the user on 2026-09-12, immediately after
closing the presence plan. Nothing has been designed yet — the next session is a
**Plan** session writing `plans/<feature>.md`, not a build.

No scope was given beyond the sentence above. Do not invent it: what the bot is
for, how strong it should be, and how a player starts a game against one are
product decisions and belong to the user. Ask before planning.

What is worth knowing before that conversation, because it is in the repo already
and changes what is cheap:

- **A chess engine is already vendored and wired up.** `vendor/sunfish.py` is
  present, and `controller_operations/engine.py` uses it for `evaluate(board)`
  and `rank_moves(board, top_n)` — position scoring and move ordering, used today
  to build the LLM's candidate list. A bot that plays moves is closer than it
  looks.
- **But only four sunfish symbols are used** (`parse`, `Position`, `pst`,
  `Move`). Its actual *search*, transposition table and UCI loop are unused — an
  existing backlog entry notes 494 lines for those four symbols. A bot that picks
  a move may want the search that is sitting there untouched.
- **`say_move` already turns a phrase into a chosen move via the LLM.** A bot is
  a second, non-human source of moves entering the same pipeline. The shape of
  `make_move` / `say_move` — validate under the row lock, persist, then
  `socketio.emit("move", ...)` to the room — is the thing a bot move has to fit
  into, and both paths already share ~60% of their bodies (backlog entry).
- **Both move paths hold `FOR NO KEY UPDATE` across the LLM call.** If a bot move
  is computed inside that transaction, it inherits the same constraint the
  presence work just spent a plan on. Read *The lock downgrade* in
  `plans/presence-table.md` before putting anything slow under that lock.
- **A bot occupies a colour, and a colour is a token.** `join_session` hands out
  `white_token` / `black_token` and refuses a third visitor. Whether the bot holds
  a real token, or is something the session is flagged with, is a schema question
  worth settling in the plan rather than in the code.
- **`game_over` is broken** (below). A bot will finish games far more often than
  two humans arranging to meet, so the 500-instead-of-409 on a finished game stops
  being theoretical. Worth fixing as part of, or just before, this work.

### Also waiting, from the session that just closed

**A player who closes their tab loses their seat.** Raised by the user on
2026-09-12 while testing by hand. The token lives in `sessionStorage`
(`storage.js:4`), which the browser wipes on tab close, so reopening the URL gets
`session_full` — the server cannot tell a returning player from a stranger with
the link. **Not caused by the presence work**: that branch of `join_session` is
byte-identical to before, and past the deadline the same request now gives the
clearer `game_ended` 410 instead of the old silent 404 bounce. Left undecided —
three options were put to the user (persist the token in `localStorage`, make a
provably-empty seat reclaimable, or keep the rule and fix the wording) and none
was chosen. It is in `BACKLOG.md`.

Note it may become moot: if a bot game is one human and one bot, the second seat
is not a person who can be locked out of it.

## What a reviewer never looked at

The two things this session would have flagged to a build-review session, kept in
case the presence code is ever suspected:

- **the narrowed invariant 7 gate** — whether excluding `last_disconnected_at`
  kept its teeth. Eight columns are still compared; the reasoning is above.
- **deviation 2, the `FakeCursor` change** — it touches the fixture ten
  pre-existing unit tests depend on, and a fake that lies is worse than no fake.

## Decisions the user made, carried forward

Still true and not derivable from the code:

- A quiet game is **not deleted** — row and moves kept so a player who hits a
  problem can reach a developer.
- Ended-ness is **computed, never written**. `abandoned` stays a dead value in the
  `sessions.status` CHECK constraint, deliberately.
- **No background work at all.**
- `IDLE_TTL_SECONDS` **stays at 600** and keeps its name.
- The ended game shows a **modal over the final position**, two buttons, board
  oriented white.
- The **foreign key is kept**, which required the lock downgrade.
- `tests/test_presence_delete_race.py` is **deleted, not rewritten**.
- **Invariant 6's socket-level timing test is dropped.**
- Where invariants 3 and 7 collided, **invariant 3 wins**: the stamp is written on
  every disconnect, and invariant 7's gate excludes that column rather than the
  implementation suppressing the write.

**Settled, not open:** `game_ended` sitting one word from the existing `game_over`
was raised and left as it is. The gates assert on the code string.

**Superseded:** the deleted race test's docstring recorded a 2026-09-09 decision
about the residual delete-versus-join window staying open. Moot — nothing deletes
a session now, so the race is gone rather than closed. Recorded so it is not
rediscovered as an accident from the deleted file.

## Repository state

**Nothing is committed.** `d0a902c` holds the last committed work. `AGENTS.md`
Boundaries forbid this session committing, pushing, tagging, merging or opening a
PR.

Modified: `README.md`, `backend/Dockerfile`, `backend/application.py`,
`backend/tests/test_presence_never_collects.py` (the arbitrated gate),
`backend/controller/sockets.py`, `backend/controller_operations/presence.py`,
`backend/controller_operations/sessions_ops.py`, `backend/queries/sessions.py`,
`backend/schema.sql`, `backend/tests/conftest.py`, `docs/architecture.md`,
`frontend/src/modules/GameView/GameView.jsx`, `frontend/src/modules/Menu/Menu.jsx`,
`handoff.md`.

Deleted: `backend/tests/test_presence_delete_race.py`.

New: `backend/queries/connections.py`,
`frontend/src/modules/GameView/GameEndedModal.{jsx,css}`,
`plans/presence-table.md` (frozen, unedited), the five gate files under
`backend/tests/`, and the appended block in `GameView.test.jsx` — the last six
written by the gates session and unedited here.

`BACKLOG.md` is still **gitignored** (`.gitignore:29`), so its three new entries
live only in this working tree. Whether that should stay gitignored is still
undecided.

## Local stack

**Nothing is running.** All of it was stopped on 2026-09-12 at the user's
request, after the manual testing pass.

| What | Port | State |
|---|---|---|
| backend | 5001 | stopped |
| Vite | 5173 | stopped |
| development Postgres 16 | 5432 | **stopped, data intact on disk** |
| throwaway test database | 55432 | **stopped and deleted** |

Neither database server had a launch agent; both were started by hand, so
**neither comes back on its own after a reboot or a login.** Start them again
with:

```bash
# development database — keeps its data, and carries the presence schema
/opt/homebrew/opt/postgresql@16/bin/pg_ctl -D /opt/homebrew/var/postgresql@16 start

# test database — recreated empty, schema.sql applied by the script
backend/scripts/testdb.sh up
```

**The test cluster was discarded, not just stopped.** `testdb.sh down` removes
the data directory under `TMPDIR`. That is what it is for, and `up` rebuilds it
from `schema.sql`, so the integration tier needs no manual `psql` — but the games
created during the browser pass are gone with it, which is intended.

**The development database keeps its data and its schema.** It carries
`last_disconnected_at`, `session_connections`, the index and the trigger, applied
by hand with `psql -f backend/schema.sql`. The six sessions created during the
browser pass were deleted; one unrelated session and zero connection rows remain.
Nothing needs re-applying on the next start.

There is still no migration runner. A future schema change reaches a fresh test
database through `db.Dockerfile`'s initdb, and reaches this development database
only if someone runs `psql "$DATABASE_URL" -f backend/schema.sql` — now written up
in `README.md`.

Nothing cost over ten minutes with a non-obvious cause, so `docs/gotchas.md` is
unchanged. Its three entries stand, including: editing `backend/.env` changes
nothing until the backend restarts, because `load_dotenv()` runs once at import
and the reloader watches only `.py` files.

Worth knowing if a gate behaves oddly: `flask_socketio.SocketIOTestClient`
reassigns `socketio.server._send_packet` and forces `async_handlers = False` on
the shared, session-scoped `application.socketio`. That is what makes emits
synchronous and timing meaningful, and it persists for every later test in the
process.

## What a later session should not redo

- **Do not read `DATABASE_URL` at import in `db.py`.**
- **Do not restore `say_move`'s per-`except` `thinking off` emits.**
- **Do not add a `lock_timeout` to the disconnect path.** A timeout trades a late
  write for a lost one, and a lost stamp is a game that reads as never-opened.
- **Do not clear `last_disconnected_at` when a player rejoins.** Clearing it means
  writing to the game row on the join path, the one path that has to stay off that
  lock.
- **Do not restore the trigger's `IF NOT EXISTS` guard.** It looks like an
  optimisation and is a lost write.
- **Do not restore `FOR UPDATE` in `select_state_for_update`.** There is a gate
  that catches it.
- **Do not downgrade `select_tokens_for_update` (`queries/sessions.py:41`).**
- **Do not gate the ended client's board on `color`.** `color` stays null so the
  socket effect returns early; the ended render branch is separate for that reason
  and its white orientation is deliberate, not a bug.
- **Do not re-add a socket-level timing test for invariant 6**, even though
  `on_join_session` now touches the database.
- **Do not convert the backdating helpers to `IDLE_TTL_SECONDS` monkeypatches.**
- **Do not "fix" the ~45s disconnect detection.** It is engine.io's heartbeat and
  the plan chose it deliberately over an application-level one.
- **Do not import `queries.connections` from a test file.** The fast tier must
  stay runnable with no database.
- **Do not give `presence.is_idle` a default bound at def time.**
