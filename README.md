# console

A single-tenant, self-hosted PaaS control plane. It is the web console I use to deploy my own projects to a home server: a FastAPI + React app that registers projects, receives build webhooks, pulls images, runs zero-downtime deploys behind Traefik, and keeps a deploy history I can roll back from. Think of a small, personal Render or Railway that I own end to end.

The goal is one place to run everything about a project that isn't writing code: deploy, roll back, read logs, manage secrets and access, run a one-off command, or open a shell into a running app. Centralize the setup so hosting a new site is a registration, not a runbook.

## What it does

- **Projects**: register an app, give it a `console.toml`, and manage it from one place.
- **Deploys**: GitHub Actions builds an image and pushes it to GHCR; a webhook tells the console, which pulls the image and runs it. The new container starts alongside the old one and only takes traffic after it passes a health check.
- **Zero-downtime swaps**: routing is handled by Traefik priority labels, so the live container keeps serving until its replacement is healthy. A failed deploy changes nothing.
- **History, rollback, and redeploy**: every deploy is an append-only row. Roll back to any build that once served traffic, or redeploy the latest — retry a failed deploy after fixing the cause, or re-run the live one to pick up changed secrets — all through the same safe pipeline.
- **Run commands and shell in**: run a one-off maintenance command in an app's live container (a migration, a backfill, a one-time login) and watch its output, or open a full interactive terminal into the container from the browser. Both exec into the running container, so a token written by a login lands where the live app can read it. App repos need no setup for this.
- **Secrets**: per-project secrets encrypted at rest with Fernet, with paste-or-drop `.env` import and copy-as-`.env` export.
- **Access**: a per-project toggle puts a Cloudflare Access login in front of an app; the console creates or removes the Access application through the Cloudflare API, gated to the emails you list.
- **Backups**: a nightly encrypted copy of the console's own state (its SQLite database plus the Fernet key that everything is encrypted with) pushed off-box to a private GitHub repo. The bundle is encrypted with a passphrase you set from the console (or mount as a secret) and keep in your password manager, so the destination alone reveals nothing, and `python -m console.backup.restore` recovers it. Losing the key means losing every stored secret, so this is the backup that matters.
- **Settings**: server-level credentials the console manages for you — a GitHub `read:packages` token so it can pull private images, the Cloudflare Access API token + account id, and the backup destination — stored encrypted and set from the UI, with step-by-step instructions on the page.
- **Validation and starters**: a `console.toml` checker runs the real deploy validator, and each project gets prefilled starter `console.toml`, `Dockerfile`, and `deploy.yml` files as a setup checklist that disappears once the first deploy lands.

## How a deploy works

```
GitHub Actions build -> push image to GHCR
        -> POST /hooks/build-finished (authenticated by GitHub OIDC)
        -> engine pulls the image and runs the new container alongside the live one
        -> health check
        -> Traefik priority label swap (new container takes traffic)
        -> reaper removes the old container on its next tick
```

The safety invariant: the live container is never stopped until its replacement has answered a health check.

Webhooks authenticate with GitHub OIDC (no shared secrets); the build workflow requests a token scoped to this console and the receiver rejects anything not owned by the expected account. The build logic lives once here as a reusable workflow, and each app repo calls it with a thin `deploy.yml`.

## Architecture

- **Backend**: FastAPI, SQLAlchemy 2.0 async, Alembic migrations, the Docker SDK for container operations, and PyJWT for OIDC verification. Secrets use Fernet with the key supplied via `CONSOLE_KEY_FILE` (never in git); without it the console runs but secret operations return a clear 503.
- **Frontend**: React + Vite + TypeScript single-page app with a custom dense, monospace-for-machine-data UI. In production the built SPA is served by the same backend.
- **Routing**: Traefik discovers containers by labels on an external Docker network named `web`. A single wildcard route on the Cloudflare tunnel means adding an app never touches Traefik, DNS, or the tunnel.
- **State**: SQLite on a host bind-mount — projects, secrets, settings, and append-only deploy history. Each *app* brings its own database as an opaque `DATABASE_URL` secret; the console itself needs none.

## Scope

This is deliberately single-tenant and small. Some things are non-goals on purpose: no in-app auth (a Cloudflare Access edge handles it), no building on the server (GitHub Actions builds and pushes to GHCR; the server only pulls and runs), no log streaming (polling only, the one exception being the interactive terminal, which needs a websocket a poll cannot replace), and no blue/green or multi-node orchestration.

## Dev setup

Runtime (once):

```bash
colima start --cpu 4 --memory 8 --disk 60 --vm-type=vz --mount-type=virtiofs
docker network create web
docker compose up -d        # traefik + whoami test container
```

Backend (once): create the venv and a console key. The key encrypts every stored secret; losing it means losing them all. It lives outside git.

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m console.keygen .console_key.dev
```

Run (migrations apply automatically on startup; `alembic upgrade head` also works standalone):

```bash
CONSOLE_KEY_FILE=.console_key.dev \
  .venv/bin/uvicorn console.main:app --port 8000 --app-dir src --reload
```

Frontend (second terminal; the dev server proxies `/api` to `:8000`):

```bash
cd frontend
npm install
npm run dev                 # http://localhost:5173
```

For a production-style run, build the SPA and let uvicorn serve everything at `:8000`:

```bash
cd frontend && npm run build
```

## Production

`compose.prod.yaml` runs the stack tunnel-only: Traefik, the console image from GHCR, and cloudflared, with the console's SQLite database on a host bind-mount and the Fernet key mounted as a compose secret. To enable off-box backups, set a passphrase and the destination repo + token in Settings (the passphrase can also be mounted as a secret via `CONSOLE_BACKUP_PASSPHRASE_FILE`). The console updates itself: a self-hosted runner on the box pulls the new image and recreates the container on a push to `main` — out-of-band from its own deploy engine, so a bad build can never leave it unable to recover. The full server walkthrough is in [`docs/server-setup.md`](docs/server-setup.md); the hand-run deploy cycle that the engine automates is in [`docs/manual-deploy.md`](docs/manual-deploy.md).

## Tests

```bash
.venv/bin/pytest
```

## License

MIT. See [LICENSE](LICENSE).
