# Phonetic Chess

A two-player web chess app with a twist: instead of (only) moving pieces, each
side can **type a short phrase describing the mood or intent** of their next
move, and an LLM picks the actual chess move that best matches that tone — chosen
from a list of engine-vetted candidates. Players can also just drag pieces
normally; the tone-based path is the novel feature.

**Live:** https://phoneticchess.duckdns.org

## How It Works

1. A player types something like *"play it safe"* or *"go for the throat."*
2. The backend ranks every legal move with a vendored **Sunfish** static eval and
   takes the top ~15 candidates.
3. Those candidates plus board context are sent to **Groq (Llama-3.3-70B)**, which
   returns the move whose character best fits the phrase, along with a short
   rationale.
4. The move is applied, persisted, and broadcast to both players over WebSockets;
   the opponent sees a "?" popover explaining the intent behind the move.

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
cp backend/.env.example backend/.env   # then paste your GROQ_API_KEY
docker compose up --build              # → http://localhost  (frontend on host port 80)
```

For frontend-only dev with hot reload: `cd frontend && npm run dev` (the backend
can run separately via `cd backend && python application.py` on port 5001).

## Environment Variables

- `GROQ_API_KEY` (required) — set in `backend/.env`.
- `DATABASE_URL` — overridden in compose to point at the `db` service.
- Optional tuning: `LLM_MODEL` (default `llama-3.3-70b-versatile`), `LLM_TIMEOUT`,
  `LLM_MAX_RETRIES`, `LLM_BACKOFF_BASE`, `IDLE_TTL_SECONDS`.

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
