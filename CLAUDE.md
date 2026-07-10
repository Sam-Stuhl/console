# console

Single-tenant, self-hosted PaaS control plane. One user (Sam), forever.
FastAPI + React web console managing Docker containers on a home server:
status, deploy history, logs, one-click rollback, project registration,
secrets. The full original brief lives in the session history; the durable
rules are here.

## Hard non-goals (decided; do not add, suggest, or relitigate)

- No auth inside the app. Cloudflare Access at the edge handles it.
- No building on the server. GitHub Actions builds and pushes to GHCR;
  the server only pulls and runs.
- No managed services. DATABASE_URL is an opaque secret string (Neon).
- No live log streaming. Polling only. No SSE, no websockets.
- No self-deployment. The console is deployed by hand over a remote Docker
  context and must never be the only way to reach the server.
- No blue/green, multi-node, autoscaling. No buildpack/nixpacks opinions;
  those words must not appear in this codebase.

## Safety invariant

The currently-live container is never stopped until its replacement has
answered a health check. A failed deploy changes nothing.

## Architecture facts

- GitHub: personal account sam-stuhl, deliberately no org. Repos are a mix
  of public and private; the server does one docker login to ghcr.io with a
  read:packages PAT (stored on the box, outside git).
- Domain samstuhl.com (spelling still unverified against the registrar).
- Traefik discovers containers via labels on the external network `web`.
  Adding an app never touches Traefik config or the tunnel.
- Traefik must be v3.6+: Docker engine 29 dropped API < 1.40 and older
  Traefik fails with "client version 1.24 is too old" (symptom: 404 on
  every route).
- Webhooks authenticate via GitHub OIDC (pyjwt), no shared secrets.
  Reject unless repository_owner == "sam-stuhl".
- console.toml contract: `secrets = [...]` must sit ABOVE the first
  [section] header (TOML assigns bare keys after a header to that section).
  The validator has a pointed error for the misplaced form.
- Fernet key file via CONSOLE_KEY_FILE (default /run/secrets/console_key),
  never in git. Without it the console serves everything except secret
  operations, which 503.
- Dev is Colima on an arm64 Mac; prod is amd64. Never "fix" the mismatch,
  never add --platform to local builds.

## Status (2026-07-09)

Phases 0-2 complete: runtime proven, manual deploy spec in
docs/manual-deploy.md (this is the deploy engine's spec), read-only
container views, projects + revealable secrets + console.toml validator.
Next is Phase 3: OIDC webhook receiver, deploy engine (resolve the
router-overlap question documented in the runbook), reaper. Then deploy
history UI + rollback, then server setup on the PC.

## How Sam works

- Backend: propose approach, files, and tradeoffs; wait for agreement
  before writing. Frontend: just build and show.
- Sam is new to Docker; explain container concepts on first use.
- Small focused commits, real messages, never a Co-Authored-By trailer.
- No em dashes anywhere, prose or code.
- Ask when a decision is genuinely his; if he drifts toward a non-goal,
  say why it is on the list before doing anything.

## Commands

```bash
.venv/bin/pytest                          # backend tests
CONSOLE_KEY_FILE=.console_key.dev \
  .venv/bin/uvicorn console.main:app --port 8000 --app-dir src --reload
npm run dev --prefix frontend             # Vite on :5173, proxies /api
npm run build --prefix frontend           # then uvicorn alone serves the SPA
docker compose up -d                      # traefik + whoami (network `web` must exist)
```

## UI design system

Custom DaisyUI theme "console" in frontend/src/index.css: chroma-0 soot
neutrals, bone ink, one ember primary used sparingly, status colors carry
meaning. Monospace for all machine data. Dense hairline tables, status
dots, skeleton loading, inline two-step confirms instead of modals.
150ms transitions; only transitional states pulse.
