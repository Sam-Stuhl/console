import os

LOG_TAIL_DEFAULT = int(os.environ.get("CONSOLE_LOG_TAIL", "500"))
LOG_TAIL_MAX = 2000

DB_PATH = os.environ.get("CONSOLE_DB_PATH", "./console.db")
KEY_FILE = os.environ.get("CONSOLE_KEY_FILE", "/run/secrets/console_key")

# Validation caps for console.toml, sized to the home server
MEMORY_CAP_BYTES = 16 * 1024**3
CPUS_CAP = 8.0
HEALTH_TIMEOUT_CAP = 300

# Host rule suffix: app.localhost in dev, app.samstuhl.com in prod
DOMAIN = os.environ.get("CONSOLE_DOMAIN", "localhost")

# GitHub Actions OIDC. The app repos' workflows must request their token
# with audience=console; GitHub's default audience is the owner URL, which
# we deliberately do not use so the value is stable and greppable.
OIDC_ISSUER = "https://token.actions.githubusercontent.com"
OIDC_JWKS_URL = OIDC_ISSUER + "/.well-known/jwks"
OIDC_AUDIENCE = os.environ.get("CONSOLE_OIDC_AUDIENCE", "console")
OIDC_OWNER = "sam-stuhl"

# Deploy engine. Router priorities count down from PRIORITY_START so the
# live container's router always outranks its unverified replacement.
PRIORITY_START = 4_000_000_000
HEALTH_POLL_INTERVAL = 2

# Reaper timeouts for stuck deployments, in seconds
REAPER_INTERVAL = 60
BUILD_TIMEOUT = 30 * 60
DEPLOY_TIMEOUT_MARGIN = 10 * 60
QUEUED_TIMEOUT = 60 * 60
