"""Server-level settings, managed from the console UI: the GHCR read token and
the Cloudflare Access credentials. Values are write-only over the API (you can
set or clear one, and see which are configured, but never read a value back),
the same rule as project secrets."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from console import settings_store
from console.db.session import get_session

router = APIRouter(prefix="/api/settings")


class SettingValue(BaseModel):
    value: str


@router.get("")
async def list_settings(session: AsyncSession = Depends(get_session)) -> dict:
    """Which known settings are configured. Never returns values."""
    present = await settings_store.keys_set(session)
    return {"set": sorted(present & settings_store.KNOWN)}


@router.put("/{key}", status_code=204)
async def put_setting(
    key: str, body: SettingValue, session: AsyncSession = Depends(get_session)
) -> None:
    if key not in settings_store.KNOWN:
        raise HTTPException(status_code=404, detail=f'unknown setting "{key}"')
    if not body.value.strip():
        raise HTTPException(status_code=400, detail="value cannot be empty")
    await settings_store.set_value(session, key, body.value.strip())
    await session.commit()


@router.delete("/{key}", status_code=204)
async def delete_setting(
    key: str, session: AsyncSession = Depends(get_session)
) -> None:
    if key not in settings_store.KNOWN:
        raise HTTPException(status_code=404, detail=f'unknown setting "{key}"')
    await settings_store.delete(session, key)
    await session.commit()
