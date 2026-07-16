"""The base domains apps can be hosted under: the primary CONSOLE_DOMAIN plus
any extras recorded in settings. Each extra domain is a one-time manual
Cloudflare setup (a wildcard tunnel route + DNS, exactly like the primary); the
console only records which domains exist so a project can pick one, and never
touches Cloudflare DNS or the tunnel itself. See docs/server-setup.md.

A project's host is {subdomain}.{domain}; a null project.domain means the
primary, so projects that predate multi-domain keep working unchanged."""

import re

from sqlalchemy.ext.asyncio import AsyncSession

from console import config, settings_store
from console.db.models import Project

# A plausible bare domain: labels of letters/digits/hyphens, a real TLD, no
# scheme, path, port, or wildcard. Case-insensitive; we do not touch DNS, so
# this is just a sanity guard against fat-fingered input, not a resolver.
DOMAIN_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$", re.I)


async def available(session: AsyncSession) -> list[str]:
    """Primary domain first, then configured extras (deduped)."""
    domains = [config.DOMAIN]
    extras = await settings_store.get(session, settings_store.DOMAINS)
    if extras:
        for raw in extras.split(","):
            candidate = raw.strip()
            if candidate and candidate not in domains:
                domains.append(candidate)
    return domains


async def set_extras(session: AsyncSession, extras: list[str]) -> list[str]:
    """Replace the configured extra domains. Strips, drops blanks/the primary/
    duplicates, and validates each looks like a domain. Returns the stored list.
    Raises ValueError on a malformed domain."""
    cleaned: list[str] = []
    for raw in extras:
        candidate = raw.strip()
        if not candidate or candidate == config.DOMAIN or candidate in cleaned:
            continue
        if not DOMAIN_RE.match(candidate):
            raise ValueError(f'"{candidate}" is not a valid domain name')
        cleaned.append(candidate)
    if cleaned:
        await settings_store.set_value(session, settings_store.DOMAINS, ",".join(cleaned))
    else:
        await settings_store.delete(session, settings_store.DOMAINS)
    return cleaned


def of(project: Project) -> str:
    """The project's domain, falling back to the primary."""
    return project.domain or config.DOMAIN
