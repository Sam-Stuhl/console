"""The base domains apps can be hosted under: the primary CONSOLE_DOMAIN plus
any extras recorded in settings. Each extra domain is a one-time manual
Cloudflare setup (a wildcard tunnel route + DNS, exactly like the primary); the
console only records which domains exist so a project can pick one, and never
touches Cloudflare DNS or the tunnel itself. See docs/server-setup.md.

A project's host is {subdomain}.{domain}; a null project.domain means the
primary, so projects that predate multi-domain keep working unchanged."""

from sqlalchemy.ext.asyncio import AsyncSession

from console import config, settings_store
from console.db.models import Project


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


def of(project: Project) -> str:
    """The project's domain, falling back to the primary."""
    return project.domain or config.DOMAIN
