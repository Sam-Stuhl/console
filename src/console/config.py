import os

LOG_TAIL_DEFAULT = int(os.environ.get("CONSOLE_LOG_TAIL", "500"))
LOG_TAIL_MAX = 2000
