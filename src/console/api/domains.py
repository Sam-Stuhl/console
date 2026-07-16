"""Manage the base domains apps can be hosted under: the primary CONSOLE_DOMAIN
(always present, not removable) plus configured extras. This is the readable
counterpart to the write-only settings API. A domain is not a secret and the UI
needs to list what exists, so domains live here rather than in /api/settings.

Each extra is still a one-time manual Cloudflare setup (a wildcard tunnel route
plus DNS); the console only records which domains exist so a project can pick
one, and never touches Cloudflare DNS or the tunnel. See docs/server-setup.md."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from console import config, domains
from console.db.session import get_session

router = APIRouter(prefix="/api/domains")


class DomainsUpdate(BaseModel):
    extras: list[str] = []


def _shape(extras: list[str]) -> dict:
    return {"primary": config.DOMAIN, "extras": extras}


@router.get("")
async def get_domains(session: AsyncSession = Depends(get_session)) -> dict:
    """The primary domain and the configured extras."""
    return _shape((await domains.available(session))[1:])


@router.put("")
async def set_domains(
    body: DomainsUpdate, session: AsyncSession = Depends(get_session)
) -> dict:
    """Replace the extra domains wholesale. The primary is fixed by config and
    cannot be added or removed here."""
    try:
        extras = await domains.set_extras(session, body.extras)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    await session.commit()
    return _shape(extras)
