"""Read each tracked credential's expiry status and set its expiry date. The
token values themselves are managed through the write-only settings API; this
exposes the non-secret expiry metadata (which the settings API never returns)."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from console import credentials
from console.db.session import get_session

router = APIRouter(prefix="/api/credentials")

_TRACKED_KEYS = {key for key, _ in credentials.TRACKED}


class ExpiryUpdate(BaseModel):
    expires_at: str | None = None  # "YYYY-MM-DD", or null to clear


@router.get("")
async def list_credentials(session: AsyncSession = Depends(get_session)) -> list[dict]:
    return await credentials.status(session)


@router.put("/{key}/expiry", status_code=204)
async def set_expiry(
    key: str, body: ExpiryUpdate, session: AsyncSession = Depends(get_session)
) -> None:
    if key not in _TRACKED_KEYS:
        raise HTTPException(status_code=404, detail=f'unknown credential "{key}"')
    try:
        await credentials.set_expiry(session, key, body.expires_at)
    except ValueError:
        raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD")
    await session.commit()
