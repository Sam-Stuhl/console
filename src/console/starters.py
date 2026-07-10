"""Starter files for new app repos, prefilled from the project row. The
console.toml starter must always pass the validator; a test enforces it."""

from console.db.models import Project

CONSOLE_TOML_TEMPLATE = """\
# console.toml for {name}
# Lives at the repo root. The build workflow sends this file to the console
# on every build, and it is validated before anything touches Docker. You
# can check edits anytime on the console's "toml check" page.

# Secrets your app needs at runtime, listed by NAME only. The values are
# stored in the console (project page -> secrets), never in git.
# IMPORTANT: this line must stay ABOVE the first [section] header; in TOML
# a bare key written below a header belongs to that section.
secrets = []

[app]
name = "{name}"
subdomain = "{subdomain}"  # serves at {subdomain}.samstuhl.com
port = 8080                # the port your app listens on inside the container

[health]
path = "/health"  # must answer HTTP 200 once the app is ready to serve
timeout = 60      # seconds before the deploy is marked failed (max 300)

[resources]
memory = "512m"  # like "512m" or "1g", max 16g
cpus = 1.0       # max 8.0

[env]
# Non-secret environment variables, injected at runtime. Values in quotes.
# LOG_LEVEL = "info"
"""

DOCKERFILE_TEMPLATE = """\
# Dockerfile for {name}
# GitHub Actions builds this on every push to {branch} and pushes the image
# to GHCR; the server only pulls and runs it. The build targets amd64 (the
# server), so your arm64 Mac never matters here.
#
# Swap the base image and commands for your stack. The only contract with
# the console is at the bottom: listen on app.port from console.toml and
# answer 200 on the health path.

FROM python:3.13-slim

WORKDIR /app

# Dependencies first, so Docker caches this layer between builds
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# The console injects [env] and secrets at runtime. Never bake a .env file
# into the image; add .env to .dockerignore and .gitignore instead.

# Your app must listen on the port set in console.toml (app.port) and
# answer GET /health (health.path) with 200 once it is ready.
CMD ["python", "-m", "app"]
"""

# The thin caller. All real logic lives once in the console repo's reusable
# workflow; this just points at it and grants the permissions it needs.
WORKFLOW_TEMPLATE = """\
# Deploy {name} through the console.
#
# This is a thin caller. The build+notify logic lives once in
# sam-stuhl/console/.github/workflows/app-deploy.yml; update it there and
# every app picks it up. Pin @main to a commit sha if you want stability
# over always-latest.
#
# One-time account setup (once, not per app):
#   1. Console repo (if private): Settings -> Actions -> General -> Access
#      -> allow "Accessible from repositories owned by sam-stuhl", so this
#      caller can reach the reusable workflow.
#   2. Cloudflare Access: add a Bypass policy for the /hooks/* path on the
#      console hostname. Those endpoints authenticate themselves with the
#      GitHub OIDC token, so Access must let them through.

name: deploy

on:
  push:
    branches: ["{branch}"]

jobs:
  deploy:
    uses: sam-stuhl/console/.github/workflows/app-deploy.yml@main
    permissions:
      id-token: write
      packages: write
      contents: read
"""


def starter_files(project: Project) -> dict[str, str]:
    return {
        "console_toml": CONSOLE_TOML_TEMPLATE.format(
            name=project.name, subdomain=project.subdomain
        ),
        "dockerfile": DOCKERFILE_TEMPLATE.format(
            name=project.name, branch=project.branch
        ),
        "workflow": WORKFLOW_TEMPLATE.format(
            name=project.name, branch=project.branch
        ),
    }
