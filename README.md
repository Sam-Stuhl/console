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

Backend:

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/uvicorn console.main:app --port 8000 --app-dir src --reload
```

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
