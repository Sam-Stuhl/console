# console

A single-tenant, self-hosted PaaS control plane for your own hardware. It is a FastAPI + React web console that registers projects, builds their images on a push, runs zero-downtime deploys behind Traefik, and keeps a deploy history you can roll back from. Think of a small Render or Railway that you own end to end, running on a spare machine.

The goal is one place to run everything about a project that isn't writing code: deploy, roll back, read logs, manage secrets and access, run a one-off command, or open a shell into a running app. Centralize the setup so hosting a new site is a registration, not a runbook.

"Single-tenant" means one operator, not one particular person: the console assumes the people using it all trust each other, so it has no user accounts or permissions of its own. Everything specific to an installation (your domain, your GitHub account, where the deploy clone lives) is configuration. Nothing about any one deployment is compiled in.

## What it does

- **Projects**: register an app, give it a `console.toml`, and manage it from one place.
- **Deploys**: a push to the tracked branch is noticed within thirty seconds; the console builds the image on the server inside a resource-capped BuildKit builder, pushes it to GHCR, pulls it back, and runs it. The new container starts alongside the old one and only takes traffic after it passes a health check. Nothing builds on GitHub, so no CI minutes are involved.
- **Zero-downtime swaps**: routing is handled by Traefik priority labels, so the live container keeps serving until its replacement is healthy. A failed deploy changes nothing.
- **History, rollback, and redeploy**: every deploy is an append-only row. Roll back to any build that once served traffic, or redeploy the latest (retry a failed deploy after fixing the cause, or re-run the live one to pick up changed secrets), all through the same safe pipeline.
- **Run commands and shell in**: run a one-off maintenance command in an app's live container (a migration, a backfill, a one-time login) and watch its output, or open a full interactive terminal into the container from the browser. Both exec into the running container, so a token written by a login lands where the live app can read it. App repos need no setup for this.
- **Secrets**: per-project secrets encrypted at rest with Fernet, with paste-or-drop `.env` import and copy-as-`.env` export.
- **Access**: a per-project toggle puts a Cloudflare Access login in front of an app; the console creates or removes the Access application through the Cloudflare API, gated to the emails you list.
- **Multiple domains**: apps serve at `{subdomain}.{domain}`, and a project can pick from more than one base domain. Each extra domain is a one-time manual Cloudflare setup (a wildcard tunnel route plus DNS, like the primary); the console only records which domains exist, sets the Traefik host rule to the chosen one, and gates the right hostname. It never touches Cloudflare DNS or the tunnel.
- **Uptime and alerts**: a background monitor pings each live app's health check on a tick, so the project's status dot reflects a real ping rather than deploy history. A sustained outage or a failed deploy pushes a notification to an ntfy topic you subscribe to on your phone, with a recovery notification when it comes back.
- **Credential expiry**: set an expiry date for the tokens that can lapse (the GHCR pull token, the Cloudflare API token, the backup repo token) and the console warns you through the same ntfy channel before they expire, so a dead token never silently breaks a deploy.
- **Backups**: a nightly encrypted copy of the console's own state (its SQLite database plus the Fernet key that everything is encrypted with) pushed off-box to a private GitHub repo. The bundle is encrypted with a passphrase you set from the console (or mount as a secret) and keep in your password manager, so the destination alone reveals nothing, and `python -m console.backup.restore` recovers it. Losing the key means losing every stored secret, so this is the backup that matters.
- **Settings**: server-level credentials the console manages for you: a GitHub `write:packages` token so it can push what it builds and pull private images, the Cloudflare Access API token + account id, and the backup destination, all stored encrypted and set from the UI, with step-by-step instructions on the page.
- **Validation and starters**: a `console.toml` checker runs the real deploy validator, and each project gets prefilled starter `console.toml` and `Dockerfile` files as a setup checklist that disappears once the first deploy lands.

## How a deploy works

```
push to the tracked branch
        -> the watcher sees the new head (polls GitHub every 30 s)
        -> builder: docker buildx build from the repo at that sha, push to GHCR
        -> engine pulls the image and runs the new container alongside the live one
        -> health check
        -> Traefik priority label swap (new container takes traffic)
        -> reaper removes the old container on its next tick
```

The safety invariant: the live container is never stopped until its replacement has answered a health check.

Nothing calls into the console to make this happen: it polls GitHub through the connection it already holds, so there is no webhook, no shared secret, and no path that has to skip the login. The build runs in a BuildKit builder created once on the host with memory and CPU caps, and one build runs at a time.

## Architecture

- **Backend**: FastAPI, SQLAlchemy 2.0 async, Alembic migrations, and the Docker SDK for container operations. Secrets use Fernet with the key supplied via `CONSOLE_KEY_FILE` (never in git); without it the console runs but secret operations return a clear 503.
- **Frontend**: React + Vite + TypeScript single-page app with a custom dense, monospace-for-machine-data UI. In production the built SPA is served by the same backend.
- **Routing**: Traefik discovers containers by labels on an external Docker network named `web`. A single wildcard route on the Cloudflare tunnel means adding an app never touches Traefik, DNS, or the tunnel.
- **State**: SQLite on a host bind-mount, holding projects, secrets, settings, and append-only deploy history. Each *app* brings its own database as an opaque `DATABASE_URL` secret; the console itself needs none.

## Scope

This is deliberately single-tenant and small. Some things are non-goals on purpose: no in-app auth (a Cloudflare Access edge handles it), no build farm (one capped builder, one build at a time, and never the console's own image), no log streaming (polling only, the one exception being the interactive terminal, which needs a websocket a poll cannot replace), and no blue/green or multi-node orchestration.

## Configuration

Everything installation-specific is environment, not code. The console reads:

| variable | required | what it is |
| --- | --- | --- |
| `CONSOLE_DOMAIN` | yes in prod | your base domain, so apps serve at `{subdomain}.{domain}` |
| `CONSOLE_OIDC_OWNER` | yes in prod | the GitHub account whose images may deploy here, and the login for pushes. Unset refuses every image rather than trusting anyone |
| `CONSOLE_KEY_FILE` | yes | path to the Fernet key that encrypts stored secrets |
| `CONSOLE_DB_PATH` | no | where the SQLite database lives |
| `CONSOLE_IMAGE` | no | override the console's own image, used to pin a known-good tag |
| `CONSOLE_BACKUP_PASSPHRASE_FILE` | no | mount the backup passphrase instead of storing it in Settings |

The self-hosted workflows take two repository variables, since a workflow file
cannot know where you put things: `CONSOLE_DEPLOY_DIR` (the absolute path of the
deploy clone on your box) and `CONSOLE_ENGINE_START` (a command to start your
container engine, only if it is not already a boot-time service). Label that
machine's runner `console-host`.

## Dev setup

Runtime (once). Any Docker engine works: Docker Engine on Linux, or Docker Desktop, colima, OrbStack, or Rancher Desktop on macOS and Windows. Start yours, then:

```bash
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

## API and AI agents

Besides the web console there is a token-authenticated machine surface: a REST
API under `/v1` and an MCP server at `/mcp`, so a script or an AI agent can read
project status, deploy history, and app logs, and drive deploys, rollbacks,
restarts, and one-off commands. Secret values are unreachable from both. Mint a
token in Settings -> **api tokens**; the walkthrough, including the Cloudflare
Access change it needs and the trade that involves, is in
[`docs/api.md`](docs/api.md).

## Production

`compose.prod.yaml` runs the stack tunnel-only: Traefik, the console image from GHCR, and cloudflared, with the console's SQLite database on a host bind-mount and the Fernet key mounted as a compose secret. To enable off-box backups, set a passphrase and the destination repo + token in Settings (the passphrase can also be mounted as a secret via `CONSOLE_BACKUP_PASSPHRASE_FILE`). The console updates itself: a self-hosted runner on the box pulls the new image and recreates the container on a push to `main`, out-of-band from its own deploy engine, so a bad build can never leave it unable to recover. The full server walkthrough is in [`docs/server-setup.md`](docs/server-setup.md); the hand-run deploy cycle that the engine automates is in [`docs/manual-deploy.md`](docs/manual-deploy.md).

## Tests

```bash
.venv/bin/pytest
```

## License

MIT. See [LICENSE](LICENSE).
