"""Tracks when the console's tokens expire, so a lapsed GHCR or Cloudflare token
does not silently break deploys or the access toggle. Expiry is a manual date
you set per credential (universal and reliable; auto-detecting from the provider
API is a future add). A check folded into the monitor sweep pushes one ntfy
warning when a credential enters the warning window, deduped by expiry date so it
does not repeat, and high-priority once actually expired.

Expiry dates are non-secret metadata stored in the settings table under
'{key}_expires_at'; they are read back through the credentials API (the settings
API is write-only). '{key}_expiry_alerted' holds the date we last alerted for."""

from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from console import alerts, config, settings_store

# The tokens worth tracking: each can expire and break something quietly.
TRACKED: list[tuple[str, str]] = [
    (settings_store.GHCR_TOKEN, "GitHub packages token"),
    (settings_store.CF_API_TOKEN, "Cloudflare API token"),
    (settings_store.BACKUP_GITHUB_TOKEN, "backup repo token"),
]


def _expiry_key(key: str) -> str:
    return f"{key}_expires_at"


def _alerted_key(key: str) -> str:
    return f"{key}_expiry_alerted"


def _state(days_left: int | None) -> str:
    if days_left is None:
        return "none"
    if days_left < 0:
        return "expired"
    if days_left <= config.CREDENTIAL_WARN_DAYS:
        return "warn"
    return "ok"


async def _days_left(session: AsyncSession, key: str, today: date) -> tuple[str | None, int | None]:
    raw = await settings_store.get(session, _expiry_key(key))
    if not raw:
        return None, None
    try:
        expires = date.fromisoformat(raw)
    except ValueError:
        return raw, None
    return raw, (expires - today).days


async def status(session: AsyncSession, today: date | None = None) -> list[dict]:
    today = today or date.today()
    configured = await settings_store.keys_set(session)
    out = []
    for key, label in TRACKED:
        expires_at, days_left = await _days_left(session, key, today)
        out.append(
            {
                "key": key,
                "label": label,
                "set": key in configured,
                "expires_at": expires_at,
                "days_left": days_left,
                "state": _state(days_left),
            }
        )
    return out


async def set_expiry(session: AsyncSession, key: str, expires_at: str | None) -> None:
    if expires_at:
        date.fromisoformat(expires_at)  # validate; raises ValueError on bad input
        await settings_store.set_value(session, _expiry_key(key), expires_at)
    else:
        await settings_store.delete(session, _expiry_key(key))
    # A new date reopens alerting for that date.
    await settings_store.delete(session, _alerted_key(key))


async def check_expiries(session: AsyncSession, today: date | None = None) -> None:
    """Alert once when a set credential is within the warning window."""
    today = today or date.today()
    configured = await settings_store.keys_set(session)
    for key, label in TRACKED:
        if key not in configured:
            continue
        expires_at, days_left = await _days_left(session, key, today)
        if days_left is None or days_left > config.CREDENTIAL_WARN_DAYS:
            await settings_store.delete(session, _alerted_key(key))
            continue
        if await settings_store.get(session, _alerted_key(key)) == expires_at:
            continue  # already warned for this date
        if days_left < 0:
            title, message, priority = (
                f"{label} expired",
                f"The {label} expired on {expires_at}. Renew it and update the expiry.",
                "high",
            )
        else:
            title, message, priority = (
                f"{label} expires soon",
                f"The {label} expires in {days_left} day(s), on {expires_at}.",
                "high",
            )
        await alerts.send(session, title, message, tags=["key"], priority=priority)
        await settings_store.set_value(session, _alerted_key(key), expires_at or "")
    await session.commit()
