# Console image: build the React SPA, then serve it with the FastAPI app.
# Targets linux/amd64 (the server); CI builds it on an amd64 runner. It runs
# from the source tree with --app-dir src, exactly like dev, so main.py
# resolves frontend/dist and alembic the same way it does locally.

# Stage 1: build the SPA
FROM node:22-slim AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Stage 2: python runtime
FROM python:3.13-slim
WORKDIR /app

# Install deps (and the package) from pyproject. We still run from the
# source tree below, so main.py's path resolution stays correct.
COPY pyproject.toml alembic.ini ./
COPY src ./src
COPY alembic ./alembic
RUN pip install --no-cache-dir .

# The built SPA, at the path main.py expects: <root>/frontend/dist
COPY --from=frontend /build/dist ./frontend/dist

ENV CONSOLE_DB_PATH=/data/console.db \
    CONSOLE_DOMAIN=samstuhl.com \
    CONSOLE_OIDC_AUDIENCE=console \
    CONSOLE_KEY_FILE=/run/secrets/console_key
EXPOSE 8000

# Migrations run in the app's lifespan on startup, before it serves.
CMD ["uvicorn", "console.main:app", "--app-dir", "src", "--host", "0.0.0.0", "--port", "8000"]
