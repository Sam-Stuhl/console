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

# PYTHONUNBUFFERED so uvicorn's output reaches docker logs as it happens.
# Without it Python block-buffers stdout when it is a pipe, and the container
# looks silent: requests that plainly happened leave no trace, which makes
# anything on the box undiagnosable.
#
# No CONSOLE_DOMAIN or CONSOLE_OIDC_OWNER default: those are per-deployment and
# compose.prod.yaml requires both. An image that shipped one operator's domain
# would hand it to every install that forgot to set its own.
ENV PYTHONUNBUFFERED=1 \
    CONSOLE_DB_PATH=/data/console.db \
    CONSOLE_OIDC_AUDIENCE=console \
    CONSOLE_KEY_FILE=/run/secrets/console_key
EXPOSE 8000

# Migrations run in the app's lifespan on startup, before it serves.
#
# --forwarded-allow-ips=* so X-Forwarded-Proto from Traefik is honored. Uvicorn
# trusts those headers only from 127.0.0.1 by default, and Traefik reaches this
# container from a docker network address, so without it the app believes every
# request arrived over plain http: absolute URLs it builds come out as http://
# on an https site, and cookies it marks secure-if-https never are. Trusting
# the header is safe here because nothing can reach this container except
# through Traefik; no port is published to the host.
CMD ["uvicorn", "console.main:app", "--app-dir", "src", "--host", "0.0.0.0", \
     "--port", "8000", "--forwarded-allow-ips", "*"]
