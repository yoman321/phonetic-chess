# Phonetic Chess

A two-player web chess app with a twist: instead of (only) moving pieces, each
side can **type a short phrase describing the mood or intent** of their next
move, and an LLM picks the actual chess move that best matches that tone — chosen
from a list of engine-vetted candidates. Players can also just drag pieces
normally; the tone-based path is the novel feature.

**Live:** https://phoneticchess.duckdns.org

## Benchmarks

- **Every move played is legal — 100%, never "usually."** The LLM's chosen UCI is
  checked against `python-chess`'s full legal-move set for the position before
  anything is applied; an out-of-set answer is rejected and re-asked up to
  `LLM_MAX_RETRIES` (3), and a run that never returns a legal move fails the
  request rather than playing something. Sunfish ranks and trims the candidate
  list the model is shown — it does not do the legality check.

| Measured | Result |
|---|---|
| Illegal moves reaching the board | **0 of 174,983** candidates ranked across 200 random games |
| Sunfish candidate ranking (`rank_moves`) | 0.13 ms median, 0.17 ms p95 |
| Sunfish static eval (`evaluate`) | 0.02 ms median, 0.03 ms p95 |
| LLM round trip (Groq), tone move | not benchmarked — dominates the above by ~3 orders of magnitude |
| Tone-move input prompt | **262.6 tokens**, down from 434.6 — a 171.9-token cut, measured 2026-09-19 |

Ranking and eval measured over 7 positions (4-48 legal moves each), 200 runs
apiece, Python 3.14 on an Apple M4. Both are pure-Python and search-free, so
per-move engine cost is negligible next to the network call.

The prompt figure is the provider's own `usage.prompt_tokens`, averaged over 60
live calls across 20 frozen positions — 40 on the old prompt, 20 on the new one.
`plans/machine-readable-move-prompt.md` has the method and the tone-parity check
that went with it. Output tokens are unchanged; only the input side was touched.
To re-run it (it spends money on Groq and on a judge CLI, so it is skipped by
default):

```bash
(cd backend && LLM_LIVE=1 JUDGE_LIVE=1 JUDGE_CLI=codex \
    .venv/bin/pytest -q tests/test_machine_readable_move_prompt_live.py)
```

`LLM_LIVE=1` allows the provider calls, `JUDGE_LIVE=1` allows the tone judge, and
`JUDGE_CLI` picks the judge binary — Codex only today, see `docs/gotchas.md`.
Results land in `backend/.benchmark-results/machine-readable-move-prompt/<run-id>/`,
which is local output and not committed.

## How It Works

1. A player types something like *"play it safe"* or *"go for the throat."*
2. The backend ranks every legal move with a vendored **Sunfish** static eval and
   takes up to eight candidates.
3. Those candidates plus board context are sent to **Groq (Qwen3.8 27B)**, which
   returns the move whose character best fits the phrase and an updated tone.
4. The move is applied, persisted, and broadcast to both players over WebSockets;
   either player can press "?" to ask for its explanation. The answer is saved
   with the move and reused on later requests.
5. Committed LLM moves are logged to Postgres for personal analysis, including
   their retries, latency and token counts. Failed or rolled-back moves create
   no new analysis rows. Logging can fail without changing the game. Metrics
   come out of a `llm_call_metrics` view; there is no dashboard, just SQL.

`POST /sessions/<sid>/moves/<ply>/explain` takes `{"playerToken": "..."}` and
returns `intent` and `rationale`. The move's `player_text`, `pre_move_fen`, and
`prior_tone` are saved atomically in `moves`; the cached answer also lives there.
Manual and old moves without saved context return `explanation_unavailable`.
The game and explanation feature never read analysis tables. Request counts,
explanation usage and latency are optional copies in `llm_calls`.

The candidate list is advisory — the model may pick any legal move — so it can
choose a slightly "off" move when that better fits an unusual tone, while the
suggestions keep things sound.

## Stack

- **Backend** — Python 3, Flask + Flask-SocketIO, PostgreSQL (psycopg 3),
  `python-chess` for rules, vendored Sunfish for evaluation, OpenAI SDK pointed at
  Groq's OpenAI-compatible endpoint.
- **Frontend** — React 19 + Vite, `react-chessboard`, `chess.js`, `socket.io-client`.
- **Infra** — Docker Compose with three services (`db`, `backend`, `frontend`);
  in production a Caddy reverse proxy fronts the app with automatic HTTPS.

## Running Locally

Requires Docker and a `GROQ_API_KEY`.

```bash
cp .env.example backend/.env   # then paste your GROQ_API_KEY
docker compose up --build      # → http://localhost:8080
```

Compose publishes the frontend on `127.0.0.1:8080` only — not on port 80 and not
on a public interface — because in production Caddy is the sole public face and
reverse-proxies to it. The backend is `expose`d on 5001 inside the compose
network with no host port; nginx in the frontend container proxies `/api` to it.

If the image build fails under BuildKit (seen on macOS), disable it:

```bash
DOCKER_BUILDKIT=0 docker compose up --build
```

Vite bakes `VITE_API_BASE` and `VITE_SOCKET_URL` into the bundle at build time, so
frontend changes only appear after a rebuild — `docker compose up` without `--build`
will keep serving the old assets.

For frontend-only dev with hot reload: `cd frontend && npm run dev` (the backend
can run separately via `cd backend && python application.py` on port 5001).

### Applying a schema change

There is no migration runner. `db.Dockerfile` bakes `backend/schema.sql` into the
initdb scripts, which run only on an empty volume — so the file reaches a fresh
database and nothing else. For a database that already has data:

```bash
psql "$DATABASE_URL" -f backend/schema.sql
```

`schema.sql` is safe to re-run and keeps existing data. Tables and indexes are
only ever added. Constraints, functions, triggers and the `llm_call_metrics` view
are recreated each run, since `CREATE TABLE IF NOT EXISTS` cannot update them —
so the view is briefly absent mid-run and a query against it right then will
fail.

## Tests

Two suites, both runnable from a checkout. The backend needs a virtualenv with
`requirements-dev.txt`; the frontend needs `npm install`.

```bash
# setup
(cd backend && python -m venv .venv && .venv/bin/pip install -r requirements.txt -r requirements-dev.txt)
(cd frontend && npm install)

# fast — unit tier only, no database
(cd backend && .venv/bin/pytest -q -m "not integration")
(cd frontend && npm test)

# full — includes the Postgres-backed tier
backend/scripts/testdb.sh up
(cd backend && .venv/bin/pytest -q)
(cd frontend && npm test)
backend/scripts/testdb.sh down
```

`backend/scripts/testdb.sh` starts a throwaway Postgres on **127.0.0.1:55432**,
never the compose `db` service, so test data cannot touch the development volume.
It builds `backend/db.Dockerfile` when the Docker daemon is reachable and
otherwise falls back to a local `initdb` cluster under `TMPDIR` (needs
postgres 16 on `PATH`). Point the tier somewhere else with `TEST_DATABASE_URL`.

Tests marked `integration` are the only ones that need a database; `-m "not
integration"` runs with none present.

## Environment Variables

- `GROQ_API_KEY` (required) — set in `backend/.env`. Read once at import
  (`llm.py:11`), so the backend must restart to pick up a change.
- `DATABASE_URL` — overridden in compose to point at the `db` service.
- Optional tuning: `LLM_MODEL` (default `qwen/qwen3.8-27b`), `LLM_REASONING_EFFORT`
  (default `none`; set to `default` to enable thinking mode), `LLM_TIMEOUT`,
  `LLM_MAX_TOKENS` (default `400` — the output ceiling per call; too low
  truncates the reply into invalid JSON), `LLM_MAX_RETRIES`, `LLM_BACKOFF_BASE`,
  `IDLE_TTL_SECONDS`.

## Production Deployment

The app runs over HTTPS at **https://phoneticchess.duckdns.org**, deployed via:

- **EC2** — a single `t4g.small` instance (Amazon Linux 2023) running the Docker
  Compose stack.
- **Elastic IP** — a fixed public IP attached to the instance.
- **DuckDNS** — `phoneticchess.duckdns.org` points at the Elastic IP.
- **Caddy** — reverse proxy in front of the app that automatically provisions and
  renews a Let's Encrypt certificate, giving HTTPS with no manual cert management.

## Cost & Abuse Protection

Roughly **~$14/mo** (EC2 `t4g.small` + 20 GB gp3 + attached Elastic IP), plus the
domain and capped Groq usage. EC2 is fixed-cost; the real spend risk is Groq (every
tone move hits the LLM). Key protections:

- **Groq spending cap** — set a monthly hard limit in the Groq console; when hit,
  the existing retry logic surfaces a friendly "try again" instead of running up a
  bill.
- **AWS Budgets + Cost Anomaly Detection** — alert (and optionally auto-stop EC2)
  on unexpected spend.
- **App-level rate limiting** (Flask-Limiter behind the proxy) is the recommended
  next hardening step to cap per-IP game creation and LLM calls.
- Release the Elastic IP and delete orphan EBS volumes when decommissioning.
