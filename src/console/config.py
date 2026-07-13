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

# Cloudflare Access automation. The console manages ONLY per-app Access
# applications (the login gate), never DNS, the tunnel, or routing. The token
# is scoped to "Access: Apps and Policies -> Edit" and nothing else, so a
# console compromise cannot touch the rest of the account. Both are optional:
# without them the access toggle 503s and everything else keeps working.
CF_API_TOKEN_FILE = os.environ.get("CONSOLE_CF_API_TOKEN_FILE", "/run/secrets/cf_api_token")
CF_ACCOUNT_ID = os.environ.get("CONSOLE_CF_ACCOUNT_ID", "")
CF_API_BASE = os.environ.get("CONSOLE_CF_API_BASE", "https://api.cloudflare.com/client/v4")

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

# One-off commands exec'd in an app's live container
COMMAND_TIMEOUT = 30 * 60  # wall-clock cap for a single run, seconds
COMMAND_OUTPUT_MAX = 256 * 1024  # stored-output cap per run, bytes

# Off-box backup of the console's own state. The passphrase encrypts the
# bundle and must live outside the DB it protects, so it is a mounted secret
# like the Fernet key, never a UI setting. The destination creds ride the
# settings store. Without a passphrase and a destination, backups are skipped.
BACKUP_PASSPHRASE_FILE = os.environ.get(
    "CONSOLE_BACKUP_PASSPHRASE_FILE", "/run/secrets/console_backup_passphrase"
)
BACKUP_INTERVAL = int(os.environ.get("CONSOLE_BACKUP_INTERVAL", str(24 * 3600)))  # seconds
BACKUP_RETENTION = int(os.environ.get("CONSOLE_BACKUP_RETENTION", "7"))  # keep newest N

# Liveness monitor: ping each live app's health URL on a tick, alert on a
# sustained outage. Container-to-container, so it only resolves from inside the
# docker network (like deploys); from host uvicorn every app reads down.
MONITOR_INTERVAL = 60  # seconds between sweeps
MONITOR_TIMEOUT = 5  # per-check HTTP timeout
MONITOR_FAIL_THRESHOLD = 2  # consecutive failures before alerting (anti-flap)
NTFY_DEFAULT_SERVER = "https://ntfy.sh"

# Reaper timeouts for stuck deployments, in seconds
REAPER_INTERVAL = 60
BUILD_TIMEOUT = 30 * 60
DEPLOY_TIMEOUT_MARGIN = 10 * 60
QUEUED_TIMEOUT = 60 * 60
