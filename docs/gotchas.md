# Gotchas

Known issues, symptom → fix. Delete an entry once its cause is fixed.

- `npm install --save-dev @testing-library/react` leaves
  `Cannot find module '@testing-library/dom'` → `frontend/.npmrc` sets
  `legacy-peer-deps=true`, so peers are not auto-installed. Install
  `@testing-library/dom` explicitly.
- `scripts/testdb.sh up` fails with `failed to connect to the docker API` →
  Docker Desktop is not running. The script falls back to a local `initdb`
  cluster, which needs postgres 16 on `PATH` (`brew install postgresql@16`).
- Editing `backend/.env` changes nothing until the backend is restarted →
  `load_dotenv()` runs once at import and the reloader watches only `.py` files.
  A stale `GROQ_API_KEY` reaches the browser as "Phrase rejected: Failed to
  fetch": the uncaught 401 is rendered by the debugger, which sets no CORS header.

Removed 2026-09-09, cause fixed by Phase 4 of `plans/live-game-integrity.md`
(one connection per operation): rows left behind by an operation still holding a
row lock, three-or-more concurrent operations wedging in `Transaction.__exit__`,
and the `statement timeout` warning in integration teardown. The full suite now
runs in under a second with no warnings.
