# Handoff

Session: 2026-09-09 into 2026-09-10. Role: **Review the build**,
`plans/live-game-integrity.md`. Wrote `plans/live-game-integrity.impl-review.md`.
No source was edited.

**`plans/live-game-integrity.md` is closed.** The user closed it on 2026-09-10
after the review, accepting all five findings as deferred rather than fixed. The
next piece of work is the presence rework — see "Next step".

## Where the feature landed

All five roles in `AGENTS.md` ran: Plan, Grade the plan, Write the gates, Build,
Review the build. All eight invariants are gated and green.

Suite re-run independently during the review, not taken from the Build session's
report:

```
backend  full   25 passed                       (0.84s, Postgres tier included)
backend  fast   20 passed, 5 deselected         (0.24s, no database)
frontend         4 passed                       (0.92s)
lint             eslint . — clean
build            vite build — 396.08 kB
```

`<typecheck>` has no command and `<lint>` is frontend-only, so neither covers the
backend. Vacuous, not passing — in their place the review read the backend diff
for what they would have caught.

**Closed with two things unverified, by decision:**

- The five review findings are deferred, not fixed. All are now in `BACKLOG.md`
  under "Carried over from live-game-integrity".
- The three manual browser passes were never run by any session. Phase 1's
  forced-reconnect check remains the only end-to-end proof that a network blip no
  longer deletes a live game; invariant 1 deliberately does not cover it. Also in
  `BACKLOG.md`.

## What the review found

`plans/live-game-integrity.impl-review.md` holds the full text. Summary, since the
plan is closed and this file is what carries forward:

One HIGH — the idle-cleanup DELETE now runs inside a process-wide lock and can
block on a row held by an in-flight `say_move` for the length of a Groq call.
Measured on 2026-09-10: presence calls for *unrelated* sessions block for the
duration. Gameplay elsewhere is unaffected — moves and broadcasts never take that
lock — but entering a room is, so a client loading or reconnecting during the
window can move and yet receive none of its opponent's moves. This is folded into
the presence work below rather than patched.

Two MEDIUM, both missing gates rather than broken code: nothing asserts the
unauthenticated `GET /sessions/:id` withholds tokens, and the `!opponentJoined`
guard on the four board handlers can be deleted with the whole suite staying
green. Two LOW. All five are in `BACKLOG.md`.

The four items the Build session flagged for this review are **correct as built**.
Two were confirmed empirically rather than by reading: a psycopg cursor is
reusable after a `UniqueViolation` under autocommit, so `create_session`'s shared
cursor is safe; and a DELETE does block on a row held by `SELECT ... FOR UPDATE`,
which is what makes the HIGH finding real.

## Next step

**A Plan session for the presence rework.** `BACKLOG.md`, first entry: "Presence
does not belong in a dictionary". Decided by the user on 2026-09-10 — room
membership and pending deletions should not be two module-level dicts behind one
global lock.

The entry states the shape the plan has to reckon with, so read it before
starting. Briefly: the two halves are different problems. *Which games are stale*
belongs in the database, and moving it there removes the delete-vs-join race by
construction instead of by locking — but a sweep on `updated_at` does not mean
what the current timer means, so the plan has to define "stale", most likely a
`last_seen` column touched on socket join and disconnect. *Who is connected right
now* should not go into Postgres at all; if one worker stops being enough the
answer is Flask-SocketIO's `message_queue` with Redis, which needs dependency
approval.

Not a plan session's job, but the constraint that motivates it: `-w 1` in
`backend/Dockerfile` exists because a second worker would get its own copy of both
dicts.

## Repository state

**Nothing is committed from this session.** `858dc2a` holds the Build session's
work — the handoff it wrote claimed otherwise, which was wrong. Uncommitted in the
working tree:

- `plans/live-game-integrity.impl-review.md` (new)
- `docs/gotchas.md` — one entry, below
- `handoff.md`

`BACKLOG.md` was also rewritten — the presence entry, the five carried findings,
and a header saying the plan is closed — but it is **gitignored**
(`.gitignore:29`), so it is not tracked and will not travel with a commit. It
lives only on this machine. Worth deciding whether that is still what you want now
that it carries the next scheduled piece of work; not changed here.

`AGENTS.md` Boundaries forbid this session committing, pushing, tagging, merging,
or opening a PR. That is the user's to do.

The frozen plan and all backend and frontend source are untouched, as are
`AGENTS.md` and the five gate test files.

## Local stack

Docker is not running on this machine, so the app was brought up the dev way from
`README.md` rather than via compose, at the user's request, to test by hand:

- Postgres 16 via `pg_ctl -D /opt/homebrew/var/postgresql@16` — a plain start,
  **not** `brew services`, so no launch agent was installed.
- Backend on `127.0.0.1:5001` from `backend/.venv`. A stale one from an earlier
  session was listening there under system Python with no database behind it; it
  was killed and relaunched.
- Vite on `:5173`. No frontend `.env` exists and none is needed — `api.js:1` and
  `socket.js:9` default to `http://127.0.0.1:5001`.

All three were still running when this session ended. Stop them with:

```bash
lsof -ti tcp:5001 -sTCP:LISTEN | xargs kill; lsof -ti tcp:5173 -sTCP:LISTEN | xargs kill
/opt/homebrew/opt/postgresql@16/bin/pg_ctl -D /opt/homebrew/var/postgresql@16 stop
```

The `GROQ_API_KEY` in `backend/.env` was invalid; the user replaced it mid-session
and tone moves work (verified: "go for the throat" → f4, 200, ~1s).

**`docs/gotchas.md` gained one entry** (`:12-15`): editing `backend/.env` changes
nothing until the backend is restarted, because `load_dotenv()` runs once at
import and the reloader watches only `.py` files. The symptom is a chat line
reading "Phrase rejected: Failed to fetch" — the uncaught 401 is rendered by the
Werkzeug debugger, which sets no CORS header, so the browser blocks the response
and `fetch` rejects with a `TypeError`.

## Still open, not scheduled

- Everything in `BACKLOG.md` other than the presence entry, including the
  `game_over` `TypeError` that makes every move in a finished game a 500 with no
  error code, and the README setup drift found while running the app.
- Whether to collapse invariants 6 and 7 into 5, and whether `AGENTS.md` should
  state a freeze criterion. Carried since 2026-09-09; both are moot for the closed
  plan but would shape the next one.

## What a later session should not redo

- **Do not fix the cleanup stall by moving the DELETE back outside the lock.**
  That reopens the race Phase 4 closed. `BACKLOG.md` says what to do instead if
  the presence rework is deferred again.
- **Do not re-relax `tests/test_presence_delete_race.py`** to the plan's
  `count(*) == 1` wording. Settled by the user on 2026-09-09; the plan carries an
  inline note saying so.
- **Do not restore `say_move`'s per-`except` `thinking off` emits.** Two
  `on:false` on the mapped paths breaks invariant 4 from the other side.
- **Do not read `DATABASE_URL` at import in `db.py`.** `application.py` calls
  `load_dotenv()` after its import block, so it would `KeyError` under the
  documented local path and stay invisible under Docker.
