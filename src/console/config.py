import os

LOG_TAIL_DEFAULT = int(os.environ.get("CONSOLE_LOG_TAIL", "500"))
LOG_TAIL_MAX = 2000

DB_PATH = os.environ.get("CONSOLE_DB_PATH", "./console.db")
KEY_FILE = os.environ.get("CONSOLE_KEY_FILE", "/run/secrets/console_key")

# Validation caps for console.toml, sized to the home server
MEMORY_CAP_BYTES = 16 * 1024**3
CPUS_CAP = 8.0
HEALTH_TIMEOUT_CAP = 300
