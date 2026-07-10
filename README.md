# console

Single-tenant self-hosted PaaS control plane. Phase 1: read-only container
console over the local Docker socket.

## Dev setup

Runtime (once):

```bash
colima start --cpu 4 --memory 8 --disk 60 --vm-type=vz --mount-type=virtiofs
docker network create web
docker compose up -d        # traefik + whoami test container
```

Backend (once): create the venv and a console key. The key encrypts every
stored secret; losing it means losing them all. It lives outside git.

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m console.keygen .console_key.dev
```

Run (migrations apply automatically on startup; `alembic upgrade head` also
works standalone):

```bash
CONSOLE_KEY_FILE=.console_key.dev \
  .venv/bin/uvicorn console.main:app --port 8000 --app-dir src --reload
```

Without a valid `CONSOLE_KEY_FILE` the console still runs; only secret
operations fail, with a 503 explaining why. In production the key mounts
read-only at `/run/secrets/console_key` (the default path).

Frontend (second terminal; dev server proxies /api to :8000):

```bash
cd frontend
npm install
npm run dev                 # http://localhost:5173
```

## Production mode

```bash
cd frontend && npm run build
```

Then uvicorn alone serves everything at :8000, SPA included.

## Tests

```bash
.venv/bin/pytest
```

## Docs

- `docs/manual-deploy.md`: the hand-run deploy cycle that specs the future
  deploy engine.
