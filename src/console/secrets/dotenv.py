"""Parse and render .env files for bulk secret import/export. Forgiving on
input (comments, blank lines, export prefixes, quotes, repeated keys), and
every skipped line comes back with a reason so nothing vanishes silently."""

import re

from console.schema.console_toml import SECRET_NAME_RE

# Values that can go in a .env line bare, without quotes
_SAFE_VALUE_RE = re.compile(r"^[A-Za-z0-9_@%+=:,./-]*$")


def parse_dotenv(text: str) -> tuple[dict[str, str], list[str]]:
    """Returns (pairs, skipped). Repeated keys: last one wins, like dotenv
    tooling does. Skipped entries are human-readable line reports."""
    pairs: dict[str, str] = {}
    skipped: list[str] = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, sep, value = line.partition("=")
        if not sep:
            skipped.append(f"line {lineno}: no '=' found")
            continue
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif " #" in value:
            # Trailing comment on an unquoted value
            value = value.split(" #", 1)[0].rstrip()
        if not SECRET_NAME_RE.match(key):
            skipped.append(
                f'line {lineno}: "{key}" is not an uppercase env-var style name'
            )
            continue
        if not value:
            skipped.append(f"line {lineno}: {key} has an empty value")
            continue
        pairs[key] = value
    return pairs, skipped


def render_dotenv(pairs: dict[str, str]) -> str:
    lines = []
    for key in sorted(pairs):
        value = pairs[key]
        if _SAFE_VALUE_RE.match(value):
            lines.append(f"{key}={value}")
        else:
            escaped = value.replace("\\", "\\\\").replace('"', '\\"')
            lines.append(f'{key}="{escaped}"')
    return "\n".join(lines) + ("\n" if lines else "")
