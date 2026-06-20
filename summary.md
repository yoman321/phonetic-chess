# Phonetic Chess — Notes

## Rebuilding / Deploying

Frontend changes (favicon, OG/social image, meta tags, React code) are baked into
the image at **build time** by Vite, so a plain restart won't pick them up — the
image must be rebuilt with `--build`.

Use this command (BuildKit disabled — needed for the build to succeed in this
environment):

```bash
DOCKER_BUILDKIT=0 docker compose up -d --build
```

- `DOCKER_BUILDKIT=0` — disables BuildKit; required here for the build to complete.
- `--build` — rebuilds images (re-runs Vite, copies `frontend/public/*` such as
  `favicon.svg` and `og-image.png` into `dist/`).
- `-d` — detached.

Build args (`VITE_API_BASE=/api`, `VITE_SOCKET_URL=""`) come from the `frontend.build.args`
block in `docker-compose.yml`, so they don't need to be passed manually.

Local app: **http://localhost:8080** (Caddy fronts it with HTTPS in production at
https://phoneticchess.duckdns.org).

### Verify after deploy

```bash
curl -s http://localhost:8080/favicon.svg | grep -q '22,10 C 32.5' && echo "new favicon served"
curl -s -o /dev/null -w "HTTP %{http_code}  type=%{content_type}\n" http://localhost:8080/og-image.png
```

## Social / LinkedIn preview

- OG image: `frontend/public/og-image.png` (1200×630), source `og-image.svg`.
- Meta tags live in `frontend/index.html`.
- LinkedIn caches link previews ~7 days. After deploying, force a re-scrape via the
  **LinkedIn Post Inspector** (https://www.linkedin.com/post-inspector/) so the new
  thumbnail shows.
