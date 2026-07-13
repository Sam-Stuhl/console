"""ntfy alert channel. Publishes short notifications (an app went down or came
back, a deploy failed) to the configured ntfy topic. Reusable: credential-expiry
warnings (roadmap #5) will send through here too.

send() is a no-op that returns False when no topic is configured, so callers
never have to check first. The topic is the only secret; on public ntfy.sh
anyone who knows it can read the notifications, so it doubles as the address."""

import logging

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from console import config, settings_store

logger = logging.getLogger(__name__)


async def send(
    session: AsyncSession,
    title: str,
    message: str,
    *,
    tags: list[str] | None = None,
    priority: str = "default",
) -> bool:
    """Publish to the configured ntfy topic. Returns whether it was sent."""
    topic = await settings_store.get(session, settings_store.NTFY_TOPIC)
    if not topic:
        return False
    server = (
        await settings_store.get(session, settings_store.NTFY_SERVER)
        or config.NTFY_DEFAULT_SERVER
    )
    headers = {"Title": title, "Priority": priority}
    if tags:
        headers["Tags"] = ",".join(tags)
    url = f"{server.rstrip('/')}/{topic}"
    try:
        async with httpx.AsyncClient(timeout=10) as http:
            resp = await http.post(url, content=message.encode(), headers=headers)
    except httpx.HTTPError as exc:
        logger.warning("ntfy send error: %s", exc)
        return False
    if resp.status_code >= 400:
        logger.warning("ntfy send failed: %s %s", resp.status_code, resp.text[:200])
        return False
    return True
