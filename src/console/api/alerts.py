"""Send a test notification, so you can confirm the ntfy topic reaches your
phone. Configuring the topic itself goes through the settings API."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from console import alerts
from console.db.session import get_session

router = APIRouter(prefix="/api/alerts")


@router.post("/test")
async def send_test(session: AsyncSession = Depends(get_session)) -> dict:
    sent = await alerts.send(
        session,
        "console test",
        "Alerts are wired up. This is a test notification.",
        tags=["bell"],
    )
    if not sent:
        raise HTTPException(
            status_code=400,
            detail="no ntfy topic configured, or the send failed (see logs)",
        )
    return {"sent": True}
