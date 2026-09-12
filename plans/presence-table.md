# Presence in a table, with the idle clock kept by a trigger

Plan session, 2026-09-12. Revised the same day after arbitration — see
"What the revision changed".

**FROZEN 2026-09-12.** No edits from here. The gates session derives its
assertions from the Invariants section below; the build sessions work against
both. A session that finds this plan wrong stops and says so rather than working
around it (`AGENTS.md`, Sessions).

## Context

Room membership and pending deletions live in two module-level dicts in
`backend/controller_operations/presence.py` — `_active_sids` (session id → set of
socket ids) and `_cleanup_timers` (session id → `threading.Timer`) — behind one
process-wide `threading.Lock`. Three consequences:

- **Deleting a game depends on in-memory presence, so it races.** Phase 4 of the
  closed `plans/live-game-integrity.md` pulled the `DELETE` inside the lock to
  close the gap. That fixed the race and created a stall.
- **The lock is global.** With one session's `DELETE` waiting on a row lock held
  by an in-flight `say_move` — which holds `SELECT ... FOR UPDATE` across a Groq
  call, up to ~90s — every presence call for *unrelated* sessions blocks. Moves
  and broadcasts are unaffected, but entering a room is not, so a client loading
  or reconnecting in that window can move and receive none of its opponent's
  moves. This is the HIGH finding in `plans/live-game-integrity.impl-review.md`.
- **It pins the app to one worker**, and `reschedule_existing_sessions()` walks
  every session at import purely to rebuild state a restart threw away.

Four decisions taken by the user on 2026-09-12 shape the design:

**Games are never deleted.** A game that goes quiet keeps its row and its moves,
so a player who hits a problem can reach a developer and have it inspected. That
removes the reason the hard parts were hard — every awkward piece of the current
design exists to guarantee we never destroy a game someone was still playing.

**Nothing runs in the background.** No sweeper, no thread, no timer. The clock is
started by a database trigger when a connection goes, and read when somebody
comes back.

**Whether a game has ended is computed, never written.** `sessions.status` is
untouched; it is a comparison, made at the moment someone tries to use the game.

**A player who comes back too late is told so.** The board loads with its final
position and a modal over it saying the game ended, offering a new game or the
menu. Every write path refuses.

Intended outcome: `presence.py` holds no state and starts nothing. Both dicts,
the lock, the per-game timers, the boot-time rebuild and the `DELETE` are gone.

## What the revision changed

Eight corrections to the first frozen text, each expanded in place below. This
list is so a reader who saw the original can find them.

1. **The trigger stamps unconditionally.** Its `IF NOT EXISTS` guard lost the
   timestamp whenever two sockets of one game disconnected concurrently, and the
   `COALESCE` fallback then reported a live game as ended within seconds. See
   *Why the guard had to go*. Invariant 3 is replaced.
2. **The ended client renders its own branch.** The board is gated on `color`
   (`GameView.jsx:351`) and the whole render is gated on `validating` (`:318`),
   so "final position with a modal over it" was unreachable by the mechanism the
   original gave. See *Frontend code*. Invariant 11 is rewritten to assert the
   FEN.
3. **`sessions_ops` keeps its `presence` import**, changed rather than dropped —
   five paths in it now need `IDLE_TTL_SECONDS`.
4. **The move paths' check has a named call site**, inside the existing locked
   transaction, with a stated precedence against `game_over`.
5. **`select_session_summary` gains a TTL parameter**, which the original
   required without saying.
6. **Only `select_state_for_update` (`:61`) is downgraded.** The `:41` downgrade
   was justified by a scenario that cannot occur.
7. **Verification step 7 times the socket join, not the GET.** The GET takes no
   lock and passes today — it was not a gate.
8. **The socket refusal tells the client.** A silent refusal reproduces the exact
   symptom this work exists to remove.

## Decisions

| Decision | Chosen |
|---|---|
| Quiet games are | left alone; never deleted, never modified |
| Disconnect | deletes the connection row |
| The idle clock | `sessions.last_disconnected_at`, stamped by an `AFTER DELETE` trigger on every disconnect |
| Ended-ness is | computed on demand, never written to `status` |
| Window | unchanged, `IDLE_TTL_SECONDS` default 600 |
| Read path | returns the position and `ended: true` |
| Write paths | refuse with `game_ended`, 410 |
| Client | modal over the board, two buttons, board oriented white |
| Background work | none |
| Foreign key | kept, with the move-path lock downgraded |

## Schema

`schema.sql`, written idempotently so the whole file stays safe to re-run:

```sql
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS last_disconnected_at TIMESTAMPTZ;

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
-- last disconnect has overwritten it. Guarding on "was this the last row?" is
-- what the first draft did, and it loses the write — see below.
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
```

`_active_sids` becomes the rows of `session_connections`. `_cleanup_timers`
becomes `last_disconnected_at` — a timestamp compared on demand instead of a
timer object waiting to fire. A game created and never opened has no rows and a
`NULL` stamp, and falls back to `created_at`.

Row-level rather than statement-level, because a disconnect deletes that socket's
row in every room it had joined, and each of those games needs its own stamp.
`RETURN NULL` is correct for an `AFTER` trigger — the return value is ignored.

### Why the guard had to go

`db.py:18` connects with `autocommit=True`, so each `release()` DELETE is its own
transaction. Two sockets of the same game disconnecting at once — both tabs
closed, the browser quit, a network drop taking both — run two concurrent
transactions. Postgres fires `FOR EACH ROW` `AFTER` triggers at end of statement,
still inside the open transaction, and a plain `SELECT` under READ COMMITTED
cannot see the other transaction's uncommitted delete and takes no row lock that
would serialise against it. Each trigger would see the other's row as still
present, neither would take the `IF NOT EXISTS` branch, both would commit, and
`last_disconnected_at` would never be written.

The failure is not "the game never goes idle". With the stamp `NULL` the
predicate falls to `COALESCE(..., created_at)`, and for any game older than
`IDLE_TTL_SECONDS` that comparison is already true — so a half-hour game whose
two tabs close together reads `ended: true` seconds later. The unconditional
write removes the race rather than serialising it; the alternative, locking the
parent row inside the trigger before the check, costs the same `sessions`-row
lock the write itself costs and buys nothing.

A `NULL` stamp therefore means exactly one thing: no socket has ever
disconnected from this game.

### The cascade

The trigger also fires when a session is deleted and the cascade removes its
connections. The `UPDATE` then matches nothing, because the parent row is already
gone. Harmless, and academic after this change since nothing deletes sessions.

### Applying it

There is no migration runner, and `db.Dockerfile` bakes `schema.sql` into the
initdb scripts, which run only on an empty volume. So the file alone reaches a
fresh test database and nothing else. Everywhere else:

```bash
psql "$DATABASE_URL" -f backend/schema.sql
```

This is additive only — it can create and add, never alter or remove. That is how
the live `moves` table ended up with six columns (`phrase`, `matched_phrase`,
`score`, `text`, `intent`, `rationale`) that `schema.sql` does not declare and
`queries/moves.py` never touches. Verified against the running local database on
2026-09-12.

## What the disconnect waits for

The trigger's `UPDATE sessions` takes a lock on the game row, and `say_move`
holds that row across its Groq round-trip. So a socket disconnecting from a game
with a phrase in flight waits for the call to finish before its stamp lands — you
reach it by submitting a phrase and closing the tab. Unconditional stamping makes
this happen on every disconnect rather than only the last one; the wait is the
same length and the same shape.

**Leave it alone.** The call always returns: `LLM_TIMEOUT` bounds it, retries are
hard-capped at three, and the lock is released either way. The disconnect then
writes the stamp correctly. All that happens is the clock starts up to ~90s late,
which means the game stays joinable slightly longer — the safe direction. A
`lock_timeout` here would be strictly worse: it trades a late write for a lost
one, and a lost stamp is now a game that reads as never-opened.

One secondary effect, recorded rather than fixed. When the disconnect is the kind
engine.io detects by ping timeout rather than a clean client close, it runs on
that server's single service task, so a parked one delays timeout detection for
other clients until the call returns. Bounded by the same `LLM_TIMEOUT`, and
strictly better than today, where the equivalent wait happens while holding a
process-wide Python lock that blocks every other room's joins as well.

## Why there is no heartbeat

A table of live connections is only as good as the event that removes a row, so
the obvious question is what happens when a client vanishes without saying
goodbye. Nothing needs adding: Socket.IO already heartbeats, server-driven. Read
from the installed `engineio/base_server.py:35` — the server pings each client
every `ping_interval` (default 25s) and expects a pong within `ping_timeout`
(default 20s), and a background service task (`engineio/server.py:472`) closes
anything that misses one, which fires the same `disconnect` handler a clean close
does. A dead client is reaped in ~45s with no code of ours.

So no application-level heartbeat, and none from the client either — it would
duplicate the transport's own ping, add traffic per player, and a backgrounded
tab would happily keep claiming to be alive.

The one failure this leaves is the process dying without running its disconnect
handlers, which orphans rows that claim sockets that no longer exist. The boot
wipe covers it: rows survive a crash only until the restart that caused it. That
is correct while the app is single-process, and it is the same constraint that
already keeps it at `-w 1`.

## The lock downgrade

Inserting a connection row makes Postgres check the foreign key, which it does by
taking `FOR KEY SHARE` on the parent `sessions` row. That conflicts with
`FOR UPDATE` — which `say_move` holds across its Groq round-trip. Left alone, a
socket joining a game with a phrase in flight blocks for up to ~90s: today's bug,
relocated from a Python lock into Postgres and landing on the join itself.

In `queries/sessions.py`, change `FOR UPDATE` to `FOR NO KEY UPDATE` in
`select_state_for_update` (`:61`) — used by `make_move` and `say_move`, and the
only lock held across the LLM call. It updates `fen`, `pgn`, `status` and
`updated_at`; no key column is involved, since `sessions`' only unique index is
the `id` primary key and `idx_sessions_updated_at` is not unique.
`FOR NO KEY UPDATE` still conflicts with itself, so mover-versus-mover exclusion
is unchanged, and it does not conflict with `FOR KEY SHARE`, so the joiner's
foreign-key check passes an in-flight `say_move`. Keep the function name — no
drive-by renames.

**`select_tokens_for_update` (`:41`) is left alone.** The first draft downgraded
it too, on the grounds that `join_session` could wait on an in-flight `say_move`.
It cannot: `sessions_ops.py:66-69` deliberately serves returning players from an
unlocked `select_tokens`, so `:41` is reached only when a colour is still free,
and `say_move` refuses outright unless both tokens are set
(`sessions_ops.py:200`). The lock it takes is held for two statements with no
network call inside, so a concurrent connection insert waits microseconds.
Changing it would be a change with no reachable case behind it.

**The downgrade is a claim about PostgreSQL, not about this codebase, and nothing
may depend on it until it has been demonstrated.** Invariant 6 is the gate.

## The predicate

One expression, no writes, evaluated wherever the game is used:

```sql
NOT EXISTS (SELECT 1 FROM session_connections c WHERE c.session_id = s.id)
AND COALESCE(s.last_disconnected_at, s.created_at) < NOW() - make_interval(secs => %s)
```

Two conditions: nobody is connected right now, and the room has been empty longer
than the window. `COALESCE` covers the game created and never opened, which has
no stamp and is judged from `created_at`. With the trigger unconditional, that is
the only state a `NULL` stamp can mean.

Two things that look like details and are not:

**The socket join must evaluate this before inserting its own row**, or the
`NOT EXISTS` sees the joiner and no game is ever idle.

**Nothing clears `last_disconnected_at` on rejoin.** It does not need clearing —
the predicate reads liveness first, and the stamp is overwritten on the next
disconnect. Clearing it would mean writing to the game row on the join path,
which is the one path that has to stay off that lock.

## Where the deadline is enforced

The read path reports; every write path refuses. This split is what lets the
client show the final position with a modal over it.

**`GET /sessions/:id`** keeps returning 200 with the position, and gains an
`ended` boolean. `select_session_summary` (`queries/sessions.py:18`) already
computes `both_joined` in SQL; `ended` is computed the same way, from the
expression above. Its signature becomes
`select_session_summary(cur, sid, ttl_seconds)` — the predicate needs the window
as a parameter, so every caller passes it. `get_session` supplies
`presence.IDLE_TTL_SECONDS`. The docstring's promise that this endpoint never
returns token values still holds and still has no gate — see Out of scope.

`ended` means the idle deadline passed. It is not `status`: a game won by
checkmate is over in a different sense and `GameView` already says so.

**`POST /sessions/:id/join`, `/move` and `/say`** raise
`ApiError("game_ended", 410)`. 410 is the honest code — the game was here and is
not coming back. Gating the two move paths is what closes the hole where a client
with a stored token and no socket keeps playing a game that has ended.

Named call sites, so the build session does not have to invent them:

- `join_session` — after the `select_tokens` lookup raises `not_found`, before
  the returning-player branch, using `connections_q.is_idle(cur, sid, ttl)` on
  the cursor already open. A returning player and a new joiner are refused
  alike.
- `make_move` and `say_move` — inside the existing
  `with db() as pg, pg.transaction()` block, on the same cursor, immediately
  after `select_state_for_update` returns a row and **before** the
  `status != "active"` check. Evaluating it under the row lock keeps it
  consistent with the state the move is about to be validated against.

**`game_ended` is checked before `game_over`.** A game past its deadline is
unreachable however it finished, so the deadline is the more useful answer; and
`game_over` cannot currently construct its own error (`ApiError("game_over",
409, status=...)` raises `TypeError` — a known backlog entry), so ordering it
second keeps the broken path off the new one. Record the ordering; do not fix
`game_over` here.

Note the existing `game_over` code means something different — a finished game,
raised by both move paths. `game_ended` is the idle deadline. Two codes, two
meanings, and the client distinguishes them. The closeness of the two names was
raised with the user on 2026-09-12 and left as it is. Recorded so it is not
rediscovered as an accident. If they are ever renamed it has to happen before the
gates session, since the gates assert on the code string.

**The socket `join_session`** checks too, and when the game is idle it does not
join the room, does not record a connection, and **emits `game_ended` back to the
requesting socket**. The emit is the correction: a silent refusal leaves a
reconnecting client sitting in a game receiving no moves and told nothing, which
is the exact symptom of the HIGH finding this work exists to remove. Redundant
with the gated load path for a fresh client, which never opens a socket — kept
because a reconnect after a long-parked tab reaches it and nothing else does.

## Backend code

**New — `backend/queries/connections.py`**, matching the existing `queries/`
idiom (plain functions taking a cursor, no connection handling):

- `track(cur, sid, socket_sid)` — `INSERT ... ON CONFLICT DO NOTHING`
- `release(cur, socket_sid)` — `DELETE FROM session_connections WHERE
  socket_sid = %s`; the trigger does the rest
- `release_all(cur)` — the boot wipe, `DELETE FROM session_connections`
- `is_idle(cur, sid, ttl_seconds)` — the predicate as a standalone `SELECT`,
  used by `join_session`, both move paths and the socket handler

**Rewritten — `controller_operations/presence.py`.** Holds no module state beyond
the connection factory and `IDLE_TTL_SECONDS`, and starts nothing. Gone entirely:
`_presence_lock`, `_active_sids`, `_cleanup_timers`, `_delete_session`,
`schedule_cleanup`, `cancel_cleanup`, `reschedule_existing_sessions`. What remains
is `init`, `track_join`, `remove_socket`, `is_idle` and `clear_connections`, each
a thin wrapper opening its own connection through the factory.

No new configuration. `IDLE_TTL_SECONDS` keeps its name, its default of 600, its
home in `presence.py` and its meaning — it is now compared against rather than
counted down.

**`controller_operations/sessions_ops.py`** — `get_session` returns the new
`ended` field; `join_session`, `make_move` and `say_move` raise `game_ended` at
the call sites named above. Drop the `schedule_cleanup` call in `create_session`
(`:49`); `created_at` already defaults to `NOW()`, so a game nobody opens still
passes its deadline on schedule. **The `presence` import at `:25` changes rather
than goes** — `from controller_operations.presence import IDLE_TTL_SECONDS`.
Five paths in this module need the window, and the first draft deleted the import
that carries it. No cycle: `presence` does not import `sessions_ops`.

**`controller/sockets.py`** — `on_join_session` checks first; when idle it emits
`game_ended` to the caller and returns, otherwise it joins and records. The
`cancel_cleanup` call disappears; there is nothing to cancel. `on_disconnect`
calls `remove_socket` and does nothing with the result; the `schedule_cleanup`
loop disappears.

**`application.py`** — `presence.init(db.connect)` stays; the
`reschedule_existing_sessions()` line becomes the boot wipe. The restart grace
falls out of the trigger rather than being coded: `DELETE FROM
session_connections` fires the row trigger once **per row**, and since every row
is gone by the end of the statement each one stamps `NOW()` on its game. A game
that had connections gets its clock restarted at boot and a fresh full window;
a game that had none keeps whatever stamp it had, so a correctly-ended game stays
ended. Sockets that reconnect immediately record themselves again. One statement
instead of a walk over every session, and no timers to arm.

`select_all_session_ids` and `delete_session` in `queries/sessions.py` become
dead once presence stops calling them. Delete them; nothing else references
either. `select_tokens`' docstring (`:32-33`) argues its safety from "only
delete_session removes the row" — rewrite that clause, since the function it
names is going and the argument is now simply that nothing removes the row.

## Frontend code

**`api.js`** — `getSession` passes `ended` through unchanged; no change needed
beyond it being in the payload. The other three helpers already parse the error
body and throw `body.error`, so `game_ended` arrives as `e.message` without
touching them.

Leave `getSession`'s `if (res.status === 404) return null` alone. That is how a
genuinely missing game bounces to the menu, and it stays correct — an ended game
is now a 200, not a 404, so the two cannot be confused.

**`modules/GameView/GameEndedModal.jsx`** (new) — the menu's modal shape
(`Menu.css:72-147`: `.modal-backdrop`, `.modal`, `.modal-title`,
`.modal-actions`): backdrop, card, title "Oops — game ended", a line of copy, and
two buttons. "New game" navigates to `/` with router state asking the menu to
open its create modal; "Back to menu" navigates to `/` plainly. Its own small CSS
file rather than reaching into `Menu.css` — about twenty lines, and it keeps
`GameView` from depending on another module's stylesheet.

**`modules/Menu/Menu.jsx`** — `openModal` is already local state (`:7`), so
seeding it from `location.state` is a one-line change and "New game" lands on the
colour picker.

**`modules/GameView/GameView.jsx`** — this is the second correction, and it is
more than the first draft's "set a flag". Two existing gates stand in the way:
`validating` short-circuits the whole render (`:318`) and the board is gated on
`color` (`:351`, which also feeds `orientation`). Setting an `ended` flag and
returning early leaves the component on "Loading session…" with no board and no
modal.

In the load effect (`:48`), when `s.ended` is true:

- `applySessionState(s)` so the final position is in `game`,
- `setEnded(true)` and `setValidating(false)`,
- return **without** calling `joinSession`.

Then a dedicated render branch, between the `validating` and `fatalError` blocks:
header, the board rendered with `position` and `orientation="white"`, and
`GameEndedModal` over it. **Orientation is always white** — colour is not
recoverable client-side, since `storage.js` keeps only the token and the one call
that maps a token to a colour is the join that now refuses. The board is behind a
modal, so the orientation is cosmetic; stating it stops a later session treating
it as a bug.

`color` stays null, so the socket effect (`:100`) returns early and no socket is
opened — that part of the first draft's mechanism is kept, it just cannot double
as the render gate.

Also add a `game_ended` listener alongside `move` and `thinking` in the socket
effect, setting the same `ended` flag. That is the client half of the socket-side
refusal: a client that joined before the deadline and reconnects after it gets
the modal instead of silence.

`FatalError` stays as it is; `session_full` keeps using it.

The reconnect resync (`:109`) calls `getSession` and swallows errors. It can now
see `ended: true`, because the resync fires from `onConnect` concurrently with
the `join_session` emit and may land before the server has inserted the row — and
because the server may refuse the join outright. The `game_ended` listener covers
both, so no handling is added at the resync itself. Stated so the next reader
knows it was considered rather than missed.

## Invariants

Gates derive from these, not from the implementation.

1. A socket joining a game whose room has been empty longer than
   `IDLE_TTL_SECONDS` does not enter the room, is not recorded as connected, and
   receives a `game_ended` event.
2. A socket joining before that deadline enters normally. The deadline is
   measured from the last disconnect, or from `created_at` for a game never
   joined.
3. `last_disconnected_at` is written on **every** disconnect, including when two
   sockets of the same game disconnect in concurrent transactions. After the last
   socket of a game goes, the stamp is non-`NULL` and within a second of the
   disconnect. It is `NULL` only for a game no socket has ever left.
4. `GET /sessions/:id` for a game past its deadline returns 200, the final
   position, and `ended: true` — and still returns no token value.
5. `POST /join`, `/move` and `/say` on a game past its deadline all return 410
   with code `game_ended`, and the board is unchanged afterwards.
6. A socket join for a session with a `say_move` in flight completes promptly —
   it does not wait on the LLM call. Assert on elapsed milliseconds for the
   socket `join_session` against a parked LLM, not on ordering, and not on the
   unlocked `GET`.
7. Presence never deletes a session row and never deletes a `moves` row, and
   never writes `sessions.status`. A game far past its deadline still reads
   `active`.
8. Nothing happens on its own. Absent a process restart, a game that passes its
   deadline while no socket and no request touches it is unchanged in the
   database.
9. Presence carries no in-process state. A game is refused or admitted
   identically under a freshly imported module with no rebuild step.
10. After a restart, a session that was occupied admits a joining socket for at
    least `IDLE_TTL_SECONDS` from boot.
11. A client loading a game past its deadline renders the board at the session's
    final FEN with the modal over it, does not call `joinSession`, and opens no
    socket.

Invariant 3 is the one that changed shape. The old wording — set on the last
delete and not on earlier ones — described the guard that had to be removed, and
a gate written from it would have locked in the lost-stamp race. The gate for the
new wording is two concurrent `release()` calls against one game, each on its own
autocommit connection, asserting the stamp is set afterwards. It fails against
the guarded trigger and passes against the unconditional one.

## Tests that must change

- **`tests/conftest.py`** — `clean_presence` (`:238`) reaches into
  `_presence_lock`, `_cleanup_timers` and `_active_sids` and becomes an
  `AttributeError`. It becomes a delete of the fixture's own connection rows
  through `pgdb`. The comment at `:17` about `reschedule_existing_sessions`
  opening a connection at import stops being true and must go — this repo treats
  comments as load-bearing. Note that importing `application` now *wipes*
  `session_connections`, a new destructive side effect of importing the app in a
  test process, and the reason invariant 8 is qualified. `IDLE_TTL_SECONDS=3600`
  at `:23` keeps working but its stated reason changes: nothing is collected any
  more, so it is about not refusing a join mid-test.
- **`tests/test_connection_isolation.py`** — invariant 7 counts rows in
  `pg_stat_activity` and asserts an exact number. Nothing here starts a
  background connection, so the count should be unaffected. Re-run it rather than
  editing it; if it moves, something started that should not have.
- **`tests/test_presence_delete_race.py`** — delete it. Every symbol it uses
  (`presence._active_sids`, `presence._delete_session`,
  `sessions_q.delete_session`) ceases to exist, and its docstring defends one
  assertion shape about a DELETE that no longer happens. The standing instruction
  in `handoff.md` not to weaken it, and the dated 2026-09-09 note about the
  residual delete-versus-join window, are about a race that is *gone* rather than
  closed — that note moves to `handoff.md` marked superseded, where decisions
  that outlive a plan already live. The nearest surviving behaviour is
  invariant 1, which is a different assertion and belongs in a new file named for
  it (`tests/test_idle_join_refusal.py`); rewriting this file in place would keep
  a name describing nothing in the new design. **Settled with the user on
  2026-09-12** — this was the open item blocking the gates session.
- **New — `tests/test_last_disconnect_stamp.py`** — invariant 3's concurrent
  case, `integration`-marked. Two threads, each with its own autocommit
  connection, deleting the two connection rows of one game at overlapping times
  via `Latch`; assert `last_disconnected_at IS NOT NULL` afterwards.
- **`GameView.test.jsx`** gains invariant 11. It already fakes the socket and the
  API (`vi.mock` of `../../socket` and `../../api`, plus a `ChessBoard` stand-in
  that carries `data-position`), so the gate is a session that answers
  `ended: true`, an assertion that `data-position` equals that session's FEN, and
  assertions that `joinSession` was never called and `getSocket` never connected.

## Verification

Per `AGENTS.md`: gates observed failing for missing behaviour first, then
`<test-full>`, `<lint>`, `<build>` green. `<typecheck>` has no command and
`<lint>` is frontend-only, so neither covers the backend — vacuous, not passing.
In their place, read the backend diff for wrong argument names and counts (the
new `ttl_seconds` parameter on `select_session_summary` touches every caller), a
`None` reaching something that cannot take one, and raised-but-unmapped
exceptions.

One claim is about PostgreSQL rather than this code and must be demonstrated
against a live database before anything depends on it: that `FOR NO KEY UPDATE`
lets a concurrent foreign-key check through where `FOR UPDATE` does not
(invariant 6). The trigger's behaviour is no longer a claim of that kind — the
unconditional write has no branch to be wrong about — but invariant 3's
concurrent gate covers it anyway.

By hand, with `IDLE_TTL_SECONDS` set low:

1. Two browsers in one game. Close one — confirm `last_disconnected_at` is set.
   Close the second — confirm it advances. Then close both together (quit the
   browser) on a fresh game and confirm the stamp is still set, and that the game
   is *not* immediately ended.
2. Reopen past the window. Confirm the final position renders with the modal over
   it, that no socket connects, and that `sessions` and `moves` are untouched
   with `status` still `active`. Both buttons go where they should, and "New
   game" opens the colour picker.
3. Same, but reopen inside the window. Confirm play continues normally.
4. Past the window, post a move straight to `/move` with the stored token.
   Confirm 410 `game_ended` and an unchanged board.
5. Leave a game past its deadline and touch nothing. Confirm the database is
   unchanged — nothing is running.
6. Restart the backend with both tabs open. Confirm both rejoin, and that a game
   left empty before the restart gets a full fresh window from boot.
7. Start a phrase move in one tab and, while it is in flight, open the game in a
   second browser and **time the socket's `join_session` round trip** — the
   client must be in the room and receiving broadcasts without waiting for the
   LLM. Timing the `GET` instead proves nothing: it takes no lock and returns
   promptly today, before any of this work. This is the HIGH finding the work
   exists to close.

## Out of scope — goes to `BACKLOG.md`

- **The client never reads `status`.** `GameView.jsx:104` destructures eight
  fields from the move broadcast and skips it, so checkmate still produces no
  announcement. Existing entry, untouched here — `ended` is a separate field and
  does not close it.
- **`game_over` cannot construct its error.** `ApiError("game_over", 409,
  status=...)` raises a `TypeError`, so refusing a move in a finished game is a
  500 with no code. Already in the backlog. `game_ended` is written to avoid the
  same trap — no keyword named `status` — and is ordered ahead of `game_over` for
  the same reason.
- **`abandoned` stays a dead value** in the `sessions.status` CHECK constraint.
  Recorded as dead already; note that it was considered here and deliberately
  left unwritten.
- **"Never return the token values" still has no gate** (`queries/sessions.py:18`).
  This work adds a third computed column to that same query, which is one more
  edit away from leaking a colour. Invariant 4 asserts it for the ended case;
  the general gate is still missing.
- **This does not unlock `gunicorn -w N`.** Flask-SocketIO still has no message
  queue, so `socketio.emit(..., to=room)` reaches only one worker's clients. And
  the boot wipe is single-process by construction — a second worker starting
  would clear the first's connections. Moving presence into Postgres is a
  prerequisite for multiple workers, not the thing that achieves it. Update the
  `-w 1` comments in `backend/Dockerfile:24` and `docs/architecture.md:77` to say
  that, rather than deleting them.
- **`psycopg`'s wait function is chosen at import.** It cooperates with gevent
  only because gunicorn monkey-patches before the app loads; adding `--preload`
  or importing psycopg from a gunicorn config would silently switch it to a
  C-level `poll()` that freezes the whole worker on every query. Latent today,
  unrelated to this change, cheap to guard with an assertion at startup.

## Docs to update before the session ends

- `docs/architecture.md` — the Idle GC paragraph (`:207-212`); **`:214`, "The
  `abandoned` status in the schema is never written — abandonment is a row
  delete"**, which stops being true and the first draft missed; the `-w 1`
  rationale (`:70-78`); the configuration list (`:251`) where `IDLE_TTL_SECONDS`
  keeps its name but changes meaning; and the HTTP section for the new `ended`
  field and the `game_ended` code.
- `README.md` — the `psql -f` line for applying the schema to an existing
  database. No new environment variables.
- `backend/Dockerfile:24` — the comment naming `presence.reschedule`.
- `handoff.md` — rewritten whole.

## Phases

Build one at a time; stop at each boundary.

1. Schema: the column, the table, the index, the function and the trigger.
   Nothing calls them yet.
2. `queries/connections.py` and the `FOR NO KEY UPDATE` downgrade on `:61`, so
   the lock change can be verified in isolation.
3. Presence rewrite and its callers: join and disconnect write to the table, the
   dicts and timers go, the boot wipe replaces the rebuild. The deadline is not
   enforced yet.
4. Enforcement: `ended` on the read path, `game_ended` on the three write paths,
   and the check plus emit in `on_join_session`.
5. The client: the modal, the menu's router state, the `GameView` ended render
   branch, and the `game_ended` socket listener.
6. Docs and backlog.
