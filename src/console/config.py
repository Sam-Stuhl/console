import os

LOG_TAIL_DEFAULT = int(os.environ.get("CONSOLE_LOG_TAIL", "500"))
LOG_TAIL_MAX = 2000

DB_PATH = os.environ.get("CONSOLE_DB_PATH", "./console.db")
KEY_FILE = os.environ.get("CONSOLE_KEY_FILE", "/run/secrets/console_key")

# Validation caps for console.toml, sized to the home server
MEMORY_CAP_BYTES = 16 * 1024**3
CPUS_CAP = 8.0
HEALTH_TIMEOUT_CAP = 300

# Host rule suffix: app.localhost in dev, app.<your-domain> in prod
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
# The GitHub account whose repos are allowed to deploy to this console. There
# is deliberately no default: a console that trusts any owner is a console
# anyone can deploy to, so an unset value rejects every webhook rather than
# waving them through.
OIDC_OWNER = os.environ.get("CONSOLE_OIDC_OWNER", "")

# The repo holding the reusable build workflow that app repos call. Defaults to
# the upstream project, which is what a fresh install wants; point it at your
# own fork if you maintain one.
WORKFLOW_REPO = os.environ.get("CONSOLE_WORKFLOW_REPO", "Sam-Stuhl/console")

# Outbound GitHub access: listing the operator's repos when registering a
# project, and reading a repo's console.toml for a deploy CI did not drive.
# This is a credential the console uses to call GitHub, never a way to log in
# to the console; Cloudflare Access remains the only inbound gate.
#
# The client id is normally saved in Settings, in the browser; this env var is
# the fallback for anyone who would rather keep it in their compose file, the
# same arrangement as the Cloudflare account id.
#
# It has no default on purpose. This is a public project, so a shipped default
# would make the upstream author the OAuth trust anchor for every install; each
# operator registers their own OAuth app (device flow enabled) instead. With
# neither the setting nor this set, the feature is simply off.
GITHUB_CLIENT_ID = os.environ.get("CONSOLE_GITHUB_CLIENT_ID", "")
# Overridable like CONSOLE_CF_API_BASE, so the client can be pointed at a
# stand-in during development. Nothing but a test should ever change it.
GITHUB_API = os.environ.get("CONSOLE_GITHUB_API", "https://api.github.com")
# Where the browser is sent to approve, and where the code is exchanged. The
# base is overridable for the same reason as GITHUB_API: so the flow can be
# driven against a stand-in in development. Nothing but a test should change it.
GITHUB_OAUTH_BASE = os.environ.get("CONSOLE_GITHUB_OAUTH_BASE", "https://github.com")
GITHUB_AUTHORIZE_URL = f"{GITHUB_OAUTH_BASE}/login/oauth/authorize"
GITHUB_TOKEN_URL = f"{GITHUB_OAUTH_BASE}/login/oauth/access_token"
# Listing private repos needs the coarse "repo" scope: OAuth apps cannot
# express anything narrower. A GitHub App could (per-repo, read-only) and is
# the upgrade path if that ever matters.
GITHUB_SCOPE = "repo"
# How long the operator has to finish approving on github.com before the
# state cookie tying the two halves of the redirect together expires.
GITHUB_STATE_TTL = 600  # seconds
GITHUB_TIMEOUT = 15  # per-request HTTP timeout
GITHUB_PAGE_SIZE = 100  # one page of repos or branches; the pickers are not browsers
GITHUB_FILE_MAX_BYTES = 256 * 1024  # a console.toml is a few hundred bytes

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

# Credential expiry: warn this many days before a tracked token lapses.
CREDENTIAL_WARN_DAYS = 14

# App icon: the console pulls each app's own favicon from its running container
# (container-to-container, like the monitor) to show in place of initials.
ICON_TIMEOUT = 5  # per-request HTTP timeout
ICON_MAX_BYTES = 256 * 1024  # refuse anything larger than this

# Reaper timeouts for stuck deployments, in seconds
REAPER_INTERVAL = 60
BUILD_TIMEOUT = 30 * 60
DEPLOY_TIMEOUT_MARGIN = 10 * 60
QUEUED_TIMEOUT = 60 * 60
