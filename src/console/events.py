"""Outbound event hook. Posts what the console did (a deploy went live or failed,
a build failed, an app went down or came back, a backup ran, a credential is
about to expire) as one small JSON object to a URL the operator configures, for
a timeline or an automation to record. The alert channel (alerts.py) tells a
person; this tells a machine.

    POST <event_url>
    Authorization: Bearer <event_token>      (only when a token is set)
    {"kind": "console.deploy.live", "because": "...", "refs": {...}, "at": "..."}

`kind` is dotted and starts with `console.`; `refs` holds references only (a
project name, a deployment id, a sha), never logs, failure output or secret
values, so the receiver can follow a ref back here for the detail. `at` is when
it happened, UTC.

post() is a no-op that returns False when no URL is configured, and it never
raises: a receiver that is down or refuses the event costs that event, never
the deploy, check or backup that sent it."""

import logging
from datetime import datetime, timezone

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from console import settings_store

logger = logging.getLogger(__name__)

TIMEOUT = 5  # seconds: the caller is a deploy or a health pass, and waits for this


async def post(
    session: AsyncSession,
    kind: str,
    because: str,
    refs: dict | None = None,
    at: datetime | None = None,
) -> bool:
    """Send one event to the configured URL. Returns whether it was accepted."""
    try:
        url = await settings_store.get(session, settings_store.EVENT_URL)
        if not url:
            return False
        token = await settings_store.get(session, settings_store.EVENT_TOKEN)
        when = (at or datetime.now(timezone.utc)).replace(tzinfo=None)
        body = {
            "kind": kind,
            "because": because,
            "refs": {k: v for k, v in (refs or {}).items() if v is not None},
            "at": when.isoformat(timespec="seconds") + "Z",
        }
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        async with httpx.AsyncClient(timeout=TIMEOUT) as http:
            resp = await http.post(url, json=body, headers=headers)
    except Exception as exc:  # noqa: BLE001 - an event must never break its sender
        logger.warning("event %s not sent: %s", kind, exc)
        return False
    if resp.status_code >= 400:
        logger.warning("event %s refused: %s %s", kind, resp.status_code, resp.text[:200])
        return False
    return True
