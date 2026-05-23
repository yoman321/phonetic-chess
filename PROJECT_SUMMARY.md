# Phonetic Chess — Project Summary

A two-player web chess app where each side **types a short phrase** to describe the mood/intent of their next move, and an LLM (Groq Llama-3.3-70B) picks the actual chess move that matches that tone from a list of engine-vetted candidates. The opponent can also drag pieces normally; the tone-based path is the novel feature.

## Stack

- **Backend**: Python 3, Flask + Flask-SocketIO, psycopg 3 (PostgreSQL), `python-chess` for rules, vendored **Sunfish** for static eval / move ranking, OpenAI SDK pointed at Groq's OpenAI-compatible endpoint.
- **Frontend**: React 19 + Vite 8, `react-router-dom` v7, `react-chessboard` v5, `chess.js` for client-side legal-move highlighting, `socket.io-client`.
- **Infra**: `docker-compose.yml` with three services — `db` (Postgres with `schema.sql` baked in), `backend`, `frontend` (nginx serving the Vite build + reverse-proxying `/api` and websockets to backend). Single host port: `8080`.

## Repo Layout

```
phonetic_chess/
├── docker-compose.yml
├── .env.example                       # documents GROQ_API_KEY + LLM_* overrides
├── backend/
│   ├── application.py                 # Flask app factory, registers BPs + sockets, inits presence
│   ├── schema.sql                     # sessions + moves tables
│   ├── Dockerfile, db.Dockerfile
│   ├── requirements.txt
│   ├── controller/                    # HTTP + socket entry points (thin)
│   │   ├── sessions.py                # /sessions, /sessions/<sid>, /join, /move, /say
│   │   └── sockets.py                 # join_session, disconnect
│   ├── controller_operations/         # business logic (raises ApiError on failure)
│   │   ├── sessions_ops.py            # create/get/join/make_move/say_move
│   │   ├── engine.py                  # Sunfish-backed evaluate() + rank_moves()
│   │   ├── llm.py                     # pick_move_with_llm() — Groq call + retry loop
│   │   ├── presence.py                # idle-session cleanup via threading.Timer
│   │   ├── helpers.py                 # IDs, tokens, PGN appender, status_from_board
│   │   └── errors.py                  # ApiError class
│   ├── queries/                       # SQL only
│   │   ├── sessions.py
│   │   └── moves.py
│   ├── error_logger/logger.py         # daily-rotated ERROR log + stdout INFO
│   ├── error_file/                    # YYYY-MM-DD.log files land here
│   └── vendor/sunfish/                # vendored chess engine (used for eval + top-N)
└── frontend/
    ├── nginx.conf                     # static + /api proxy + /socket.io proxy
    ├── Dockerfile
    ├── vite.config.js, eslint.config.js, package.json
    └── src/
        ├── App.jsx                    # routes: / → Menu, /sessionId/:id → GameView
        ├── api.js                     # fetch wrappers for the 4 backend routes
        ├── socket.js                  # singleton socket.io client (autoConnect off)
        ├── storage.js                 # sessionStorage helpers for player token
        └── modules/
            ├── Menu/                  # landing screen + Create/Join modals
            ├── GameView/              # main game UI, wires board + chat + sockets
            ├── Chessboard/            # thin react-chessboard wrapper
            ├── Chatbox/               # message list, draft input, thinking dots
            ├── EvalBar/               # centipawn bar driven by evalCp
            ├── Avatar/                # side-coloured avatar
            └── FatalError/            # error screen w/ back button
```

## Data Model (`backend/schema.sql`)

- **sessions**: `id` (8-char base36, PK), `fen`, `pgn`, `white_token`, `black_token`, `status` ∈ {`active`, `white_won`, `black_won`, `draw`, `abandoned`}, timestamps. Index on `updated_at`.
- **moves**: `(session_id, ply)` PK, `uci`, `san`, `tone_summary` (nullable, only set for LLM-picked moves), `created_at`. Cascades on session delete.

## HTTP API

All return JSON. Errors come from the central `ApiError` handler registered on the blueprint.

| Method | Path | Body | Purpose |
|---|---|---|---|
| POST | `/sessions` | `{color?: "white"\|"black"\|"random"}` | Create game, returns `{id, fen, status, created_at, color, playerToken}` |
| GET | `/sessions/<sid>` | – | Returns session summary + computed `evalCp` |
| POST | `/sessions/<sid>/join` | `{playerToken?: string}` | Claims white/black slot (or recognises existing token), returns `{color, playerToken}` |
| POST | `/sessions/<sid>/move` | `{uci, playerToken}` | Manual move (drag/click on board) |
| POST | `/sessions/<sid>/say` | `{text, playerToken}` | Tone-based move — LLM picks UCI from Sunfish top-15 + emits tone_summary |

After any move, the server broadcasts a `move` socket event to room `session:<sid>`. The `/say` flow also emits `thinking` events (`{on: true/false, side, status}`) so the opponent's UI shows typing dots.

## The Tone-Based Move Flow (`say_move` → `pick_move_with_llm`)

1. Validate token, turn, and that the opponent has moved since this player's last move (enforced via `moves.ply` parity, plus the special "white moves first" case when no prior moves).
2. Call `engine.rank_moves(board, top_n=15)` — Sunfish static eval of every legal move; top 15 returned as `(uci, san)` pairs. Falls back to arbitrary 15 if Sunfish chokes.
3. Build the prompt: system message defines the tone→move mapping rules; user message includes prior tone summary, opponent's last move, the new player text, FEN, and the candidate list.
4. Call Groq via the OpenAI SDK (`response_format=json_object`, temp 0.7). The SDK already retries 3× on 429/connect/timeout — those bubble up as `TimeoutError` → `llm_unavailable` (502).
5. Validate the returned UCI is in `all_legal_ucis` (NOT just the candidate set — candidates are advisory). On bad JSON / bad UCI, retry up to `LLM_MAX_RETRIES=3` with exponential backoff, emitting a `thinking` event with `status: "retrying"` so the UI can show "wrong move, retrying".
6. On final failure, raise `llm_bad_response` (502, UI prompts user to retry).
7. On success, push the move, write `tone_summary` into the `moves` row, emit a `move` event including `intent` + `rationale` + `priorTone` so the frontend can show a "?" explanation popover next to the move bubble.

## Presence / Cleanup (`controller_operations/presence.py`)

- Module-level `_active_sids` (sid → set of socket sids) and `_cleanup_timers` (sid → `threading.Timer`), guarded by a single `threading.Lock`.
- On socket join: `track_join` + `cancel_cleanup`. On disconnect: any sessions that emptied get `schedule_cleanup` (`IDLE_TTL_SECONDS=600`, env-configurable).
- `_delete_session` re-checks under the lock that no one rejoined before firing the `DELETE`.
- At startup, `reschedule_existing_sessions()` schedules a cleanup for every persisted session so a crash/restart doesn't leave orphans.

## Frontend State Machine (`GameView.jsx`)

- On mount: `getSession` → load FEN into a `chess.js` instance → `joinSession` (passing any token in `sessionStorage`) → store returned token + color.
- Subscribes to `move` (updates board, eval bar, chat) and `thinking` (drives typing-dot subcomponents in `Chatbox`).
- Drag/click moves: optimistically push to local `chess.js`, then `postMove`; on rejection, `game.undo()` and append a system message.
- Tone messages: optimistically push the user's text bubble (with `pendingId`); on `llm_bad_response` or `llm_unavailable`, remove the bubble, restore the draft, and show a `subscriptError` in the chat.
- The board orientation is locked to the player's color.

## Env Vars

- `GROQ_API_KEY` (required) — passed via `backend/.env` + `env_file:` in compose.
- `DATABASE_URL` — set in `docker-compose.yml` to point at the `db` service (overrides any value in `backend/.env`).
- Optional: `LLM_MODEL` (default `llama-3.3-70b-versatile`), `LLM_TIMEOUT` (30), `LLM_MAX_RETRIES` (clamped to 3), `LLM_BACKOFF_BASE` (0.5), `IDLE_TTL_SECONDS` (600).

## Running

- **Docker**: `docker compose up --build` from repo root → http://localhost:8080
- **Local backend**: `cd backend && python application.py` (port 5001)
- **Local frontend**: `cd frontend && npm run dev` (Vite default port)
- Frontend defaults to `http://127.0.0.1:5001` for both REST and socket if `VITE_API_BASE` / `VITE_SOCKET_URL` are unset.

## Notable Design Choices

- **Sunfish for eval, not search**: only `Position.value()` is called for ranking — no search, no castling-rights tracking needed.
- **Candidate list is advisory**: LLM may pick any legal UCI; this is a guardrail that lets it pick a slightly "off" move if it better fits a weird tone, while keeping the top suggestions sound.
- **Thin controllers**: HTTP routes (`controller/`) only parse + dispatch; all logic lives in `controller_operations/`. The single `ApiError` handler on the blueprint converts exceptions to JSON.
- **No auth, just tokens**: player identity = opaque token stored in `sessionStorage` per session id. The `/join` route either returns the matching color for a known token or claims the next free slot.
- **Daily log file**: `error_logger.logger` writes ERROR-level lines to `backend/error_file/YYYY-MM-DD.log` and mirrors INFO+ to stdout.

## Where to Look First When Resuming

- New backend feature touching gameplay: start in `controller_operations/sessions_ops.py`.
- LLM prompt or retry behaviour: `controller_operations/llm.py`.
- Frontend wiring for sockets / API / state: `frontend/src/modules/GameView/GameView.jsx`.
- Schema / queries: `backend/schema.sql` + `backend/queries/`.
- Deployment / env: `docker-compose.yml` + `backend/Dockerfile` + `frontend/Dockerfile` + `frontend/nginx.conf`.

---

# Deployment Guide (EC2 + Caddy)

Picks up **after** the EC2 instance has been launched. Target setup: single `t4g.small` (ARM, Amazon Linux 2023, 20 GB gp3, delete-on-termination), Elastic IP, custom domain, HTTPS via Caddy auto-cert, ~$14/mo + ~$10/yr domain + capped Groq spend.

## Deployment Progress & Live Status (2026-05-23)

**Current state: app is running on EC2, reachable directly via the Elastic IP over plain HTTP — no domain / HTTPS yet.**

- Instance: `t4g.small`, Amazon Linux 2023 (host `ip-172-31-85-22`), ARM (`aarch64`).
- All three containers build and the stack runs: `db` (healthy), `backend` (gunicorn on internal `5001`), `frontend` (nginx, published `0.0.0.0:8080->80`).
- Access pattern for this phase: **`http://<elastic-ip>:8080`** (plain HTTP, port 8080). Same-origin `/api` + `/socket.io` proxying through the frontend nginx works as-is, so no code changes were needed for IP-based access.
- The guide's "bind frontend to `127.0.0.1:8080:80`" edit was **intentionally NOT applied** — that's only for when Caddy fronts the app. Kept `8080:80` (public) so the Elastic IP is directly reachable.
- **Required AWS step for reachability:** open inbound **TCP 8080** in the instance's Security Group (Source = My IP while testing). By default only SSH/22 is open.

### Gotchas hit during first deploy (and fixes)

1. **Backend container kept exiting — `ModuleNotFoundError: No module named 'openai'`.** `controller_operations/llm.py` imports the OpenAI SDK (used for Groq), but `openai` was missing from `backend/requirements.txt` (the Dockerfile installs only that file + gunicorn/gevent). **Fix:** added `openai>=1.30,<2` to `requirements.txt` (verified locally end-to-end, then committed + pushed). The `<2` pin guards the 1.x import style (`OpenAI` client + `APIConnectionError`/`APITimeoutError`/`RateLimitError`). Watch for other dev-only deps that may have similarly drifted out of `requirements.txt`.
2. **`compose build requires buildx 0.17.0 or later`.** The setup installed the `docker-compose` plugin but not `buildx`. **Fix:** installed the latest `docker-buildx` plugin into `/usr/libexec/docker/cli-plugins` (use the `linux-arm64` asset for `t4g`). Fallback: `DOCKER_BUILDKIT=0 docker compose ...` to use the legacy builder.
3. **`permission denied ... /var/run/docker.sock`.** `ec2-user` not in the `docker` group, and the existing `tmux` session predated the group change. **Fix:** `sudo usermod -aG docker ec2-user` then `newgrp docker` (immediate, in-shell) or full SSH logout/login (permanent). A pre-existing tmux server keeps the stale group until killed/recreated.
4. **`pip` "running as the 'root' user" warning during build** — harmless inside a container (the image *is* the isolated env); not an error.
5. **`curl https://127.0.0.1:8080` → `SSL routines::wrong version number`** — used HTTPS against a plaintext HTTP port. For this phase everything is `http://...:8080`; HTTPS only exists once Caddy is in front.

### Verified working

- `docker compose ps` → all three `Up`, `db` healthy.
- `curl -I http://127.0.0.1:8080` → `200`.
- `curl -X POST http://127.0.0.1:8080/api/sessions -d '{}'` → valid session JSON (confirms frontend nginx → backend → db chain).
- Local laptop run of the same images passed identically before the push.
- *Still to confirm in-browser:* live Groq `/say` round-trip and two-tab WebSocket/typing-dots over the Elastic IP.

## ▶ Next Step — serve from a real web address (domain + HTTPS) instead of the bare Elastic IP

Right now users would have to type `http://<elastic-ip>:8080`. The next milestone is a proper, memorable address served over HTTPS:

1. **Buy + point a domain** at the Elastic IP (Step 2 below): A records for `apex` and `www`. Verify with `dig <domain> +short`.
2. **Apply the localhost-only frontend binding** in `docker-compose.yml` (`127.0.0.1:8080:80`) so the app is reachable only via the reverse proxy, then `docker compose up -d`.
3. **Install + configure Caddy** (Steps 4 & 6 below): a two-line Caddyfile reverse-proxying `127.0.0.1:8080`. Caddy auto-fetches a Let's Encrypt cert, redirects HTTP→HTTPS, and transparently upgrades WebSockets.
4. **Lock down the Security Group:** once Caddy is the public face, allow inbound **80 + 443** and remove the temporary **8080** rule.
5. Result: `https://yourdomain.com` with a green padlock — see Steps 1–7 of the guide below for the full walkthrough.

## Codebase Readiness Check

The app is **deployment-ready as-is** — no code changes required to work behind HTTPS + reverse proxy:

- Frontend uses same-origin paths in production: `docker-compose.yml` passes `VITE_API_BASE=/api` and `VITE_SOCKET_URL=""` as build args; `frontend/Dockerfile:12-15` bakes them into the Vite bundle.
- `frontend/nginx.conf` already proxies `/api/` (REST) and `/socket.io/` (with proper `Upgrade`/`Connection` headers for WebSocket).
- Backend gunicorn binds `0.0.0.0:5001` with `GeventWebSocketWorker` (real WS upgrade, not polling fallback).
- CORS is wide-open (`CORS(app)` + `cors_allowed_origins="*"`) — fine because all browser traffic is same-origin through the proxy chain. Tighten later if desired.

The only **required edits before `docker compose up`** are:
1. Create `backend/.env` from `backend/.env.example` and paste the real `GROQ_API_KEY` (compose fails without this file).
2. In `docker-compose.yml`, bind the frontend to localhost only so Caddy is the public face:
   ```yaml
   frontend:
     ports:
       - "127.0.0.1:8080:80"
   ```

## Step 1 — Elastic IP (AWS Console)

EC2 → **Elastic IPs** → **Allocate Elastic IP address** → defaults are fine → **Associate** to the instance. Tag with `Name: phonetic-chess-eip`.

**Cost note:** EIP is free *only while attached to a running instance*. Stopped instance or unattached EIP = $3.60/mo. On project decommission, **release** the EIP.

## Step 2 — Domain + DNS (~$10/yr at registrar)

Buy domain at Cloudflare/Namecheap/Route 53. Add two A records:
- `phoneticchess.com` → `<elastic-ip>`
- `www.phoneticchess.com` → `<elastic-ip>`

Verify propagation before step 6: `dig phoneticchess.com +short` should return the EIP.

## Step 3 — SSH into the instance

Key already downloaded as `~/.ssh/phonetic-chess.pem` (Ed25519 supported on Amazon Linux 2023; not on Windows AMIs).

```bash
chmod 600 ~/.ssh/phonetic-chess.pem
ssh -i ~/.ssh/phonetic-chess.pem ec2-user@<elastic-ip>
```

Add to `~/.ssh/config` for shortcut + VS Code Remote-SSH:
```
Host phonetic-ec2
    HostName <elastic-ip>
    User ec2-user
    IdentityFile ~/.ssh/phonetic-chess.pem
```

**Working from EC2 itself:** Claude Code *can* be installed on EC2 (`npm i -g @anthropic-ai/claude-code`, device-code auth works over SSH) but 2 GB RAM is tight alongside Docker. **Recommended:** VS Code Remote-SSH extension on laptop — Claude Code in VS Code's terminal sees EC2 files as local. Fallback: two terminals (Claude locally, SSH session for paste targets).

Always start a `tmux` session before long commands: `tmux new -s deploy` so flaky SSH doesn't kill `docker compose build`.

## Step 4 — Install Docker + Caddy on EC2

```bash
# Docker
sudo dnf install -y docker git
sudo systemctl enable --now docker
sudo usermod -aG docker ec2-user
sudo mkdir -p /usr/libexec/docker/cli-plugins
sudo curl -SL https://github.com/docker/compose/releases/latest/download/docker-compose-linux-aarch64 \
  -o /usr/libexec/docker/cli-plugins/docker-compose
sudo chmod +x /usr/libexec/docker/cli-plugins/docker-compose

# Caddy (reverse proxy + auto-HTTPS via Let's Encrypt)
sudo dnf install -y 'dnf-command(copr)'
sudo dnf copr enable -y @caddy/caddy
sudo dnf install -y caddy

# Log out + back in for docker group membership to apply
exit
```

## Step 5 — Clone, configure, build

```bash
ssh phonetic-ec2
git clone <repo-url> phonetic_chess && cd phonetic_chess

# Create backend/.env with real key
cp backend/.env.example backend/.env
nano backend/.env   # set GROQ_API_KEY=gsk_...

# Edit docker-compose.yml frontend port binding (see Codebase Readiness Check above)
nano docker-compose.yml

# Build + start
docker compose up -d --build
curl -I http://127.0.0.1:8080   # should return 200 OK
```

**Memory warning:** `npm run build` (Vite) peaks at ~1.5 GB. Fine on t4g.small (2 GB), tight on t4g.micro (1 GB). On 1 GB, either add swap (`fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile`, then persist via `/etc/fstab`) or pre-build the image on laptop and push to ECR/Docker Hub.

## Step 6 — Caddy config (auto-HTTPS)

```bash
sudo nano /etc/caddy/Caddyfile
```

Replace contents with:
```
phoneticchess.com, www.phoneticchess.com {
    reverse_proxy 127.0.0.1:8080
}
```

That's the entire config. Caddy auto-fetches Let's Encrypt cert for both names, renews forever, redirects HTTP → HTTPS, upgrades WebSockets transparently (Socket.IO Just Works™).

```bash
sudo systemctl enable --now caddy
sudo journalctl -u caddy -f   # watch cert issuance
```

Look for `certificate obtained successfully` within ~30 sec.

## Step 7 — Test

Visit `https://phoneticchess.com`. Verify:
- Green padlock
- Menu loads, can create session
- Drag a piece (manual move via REST)
- Send a tone message (LLM round-trip)
- Open second tab, join with the URL → typing dots appear → WSS working

## Troubleshooting

| Symptom | Likely cause | Check |
|---|---|---|
| 502 from app | Backend or frontend container crashed | `docker compose logs backend frontend` |
| Caddy can't get cert | DNS not propagated, or port 80 blocked | `dig <domain> +short`; security group port 80 |
| WebSocket fails | Browser shows `wss://...` failing | Caddy logs; confirm nginx `Upgrade` headers reach backend |
| `Permission denied (publickey)` on SSH | Wrong user or key | Confirm `ec2-user`, `chmod 600`, correct `-i` path |
| Connection timed out on SSH | Security group changed or your IP changed | EC2 → security group → re-add SSH from current `My IP` |

---

# Cost & Abuse Protection

EC2 itself is **fixed-cost** — t4g.small is ~$12/mo whether idle or pegged. Data egress is the only variable AWS cost and irrelevant at this app's traffic size. The **real spike vector is Groq** (every `/say` hits the LLM). Protections in priority order:

## Layer 1 — Groq spending cap (most important, 2 min)

Groq Console → **Settings → Spending Limits** → set monthly hard cap (e.g. $10). When hit, Groq returns 429 → existing retry logic in `controller_operations/llm.py` raises `llm_unavailable` (502) → UI prompts user to retry. App stays up, bill stops. Also create a **separate production API key** to rotate independently of dev.

## Layer 2 — App-level rate limiting (biggest moat, ~30 min)

Add `Flask-Limiter` to backend:

```python
# backend/requirements.txt
Flask-Limiter==3.8.0

# application.py
from flask_limiter import Limiter
from werkzeug.middleware.proxy_fix import ProxyFix

app.wsgi_app = ProxyFix(app.wsgi_app, x_for=2)   # Caddy + nginx = 2 hops
limiter = Limiter(
    key_func=lambda: request.headers.get("X-Forwarded-For", request.remote_addr).split(",")[0].strip(),
    app=app,
    default_limits=["200/hour"],
)
```

Decorate hot routes in `controller/sessions.py`:
```python
@bp.post("/sessions")
@limiter.limit("10/hour")          # cap new game creation per IP
def create_session(): ...

@bp.post("/sessions/<sid>/say")
@limiter.limit("20/minute")        # cap LLM calls per IP
def say_move(sid): ...
```

**Why this matters:** without proxy header handling, every request looks like `127.0.0.1` (Caddy local), so all clients share one rate-limit bucket. `ProxyFix(x_for=2)` strips the right number of hops (Caddy + nginx).

## Layer 3 — AWS Budgets (5 min)

Billing → **Budgets** → Create budget:
- Cost budget, Monthly, **$25**
- Alerts at 50% actual + 100% forecasted → email
- Optional Budget Action: auto-stop EC2 at 100% via IAM role (`ec2:StopInstances`)

Also enable **Cost Anomaly Detection** (free, emails on baseline-deviating spend).

## Layer 4 — Cloudflare in front (optional, free)

DNS A record → Cloudflare → EC2 instead of direct. Free tier gives bot challenge, basic rate-limit rules, DDoS protection. Caddy still handles the cert (or hand it to Cloudflare via "Full Strict" mode).

## AWS Cost-Spike Vectors to Avoid

Forgotten resources, not crawlers, cause AWS bill surprises:

| Vector | Cost | How to avoid |
|---|---|---|
| NAT Gateway | $32/mo + data | Never create one; stay in public subnet |
| Unattached Elastic IP | $3.60/mo | Release when not in use |
| Orphan EBS volume | $0.08/GB-mo | Confirm "Delete on termination" at launch |
| Forgotten large instance | $$$/mo | AWS Budget alert at $25 |
| RDS instead of Docker Postgres | $13+/mo | Don't open the RDS console |
| Detailed Monitoring on EC2 | $2.10/mo | Leave disabled |
| CloudWatch Logs verbose | $0.50/GB ingestion | Keep app log level INFO+, not DEBUG |

## Monthly Hygiene (2 min, when billing email arrives)

1. EC2 → **Volumes**: any `State: available` (unattached)? Delete.
2. EC2 → **Elastic IPs**: unassociated? Release.
3. EC2 → **Snapshots**: anything unfamiliar? Delete.
4. EC2 → **Instances**: anything running you don't recognize? Terminate.
5. Billing → **Bills**: scan for line items that aren't EC2 / EBS / data transfer. Investigate.

**Expected bill:** $12.26 (t4g.small) + $1.60 (20 GB gp3) + $0–2 (egress) + $0 (attached EIP) = **~$14/mo**, plus $10/yr domain, plus capped Groq spend. If a month is materially higher than $15, something is wrong — find it.
