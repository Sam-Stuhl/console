"""Cloudflare Access bypass paths: the exception that lets a machine reach one
path of an app that is otherwise behind the browser login.

Access evaluates the more specific path first, so an app registered on
`host/api/ingest` with a Bypass policy wins over the login gate on `host`. This
module builds that shape from the console instead of the Cloudflare dashboard,
for an app's own API paths and for the console's machine paths (/v1, /mcp).

A bypass is a real hole, not a convenience: the path is then reachable by
anyone on the internet, and whatever authentication the app does itself is the
only thing in front of it. So the console refuses the two shapes that are
mistakes rather than trades: an empty path (which would take the login off the
whole hostname) and, on the console's own hostname, /api (which has no
authentication of its own by design, and would hand over every project, deploy,
log, and secret write). The /v1 surface exists for that case and wants a
console-issued token.
"""

import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from console import cloudflare
from console.db.models import AccessPath

# Path segments of unreserved URL characters. No scheme, query, fragment,
# wildcard, or space: a bypass is matched as a literal prefix, so anything
# fancier is a typo that would silently not match what the user meant.
PATH_RE = re.compile(r"^[A-Za-z0-9._~-]+(?:/[A-Za-z0-9._~-]+)*$")

# First segments the console will not open up on its OWN hostname. /api is the
# SPA's unauthenticated write surface (Access is its only gate).
CONSOLE_RESERVED = {"api"}


def normalize(raw: str, *, console_own: bool) -> str:
    """A stored path from what someone typed: no slashes on either end, no
    query string, no wildcard. Raises ValueError with the reason."""
    path = raw.strip().strip("/").strip()
    if not path:
        raise ValueError(
            "give a path to open up, like api/ingest. An empty path would take "
            "the login off the whole hostname."
        )
    if not PATH_RE.match(path) or ".." in path.split("/"):
        raise ValueError(
            f'"{raw.strip()}" is not a path this can use: give segments like '
            "api/ingest, with no scheme, query string, or wildcard."
        )
    if console_own and path.split("/")[0].lower() in CONSOLE_RESERVED:
        raise ValueError(
            "/api is the console's own unauthenticated write surface, so a "
            "bypass on it would hand this console to the internet. Use /v1 "
            "with a console API token instead."
        )
    return path


async def listing(session: AsyncSession, project_id: str | None) -> list[AccessPath]:
    """Bypass paths for one project, or for the console itself (project_id
    None), oldest first."""
    rows = await session.execute(
        select(AccessPath)
        .where(AccessPath.project_id.is_(project_id) if project_id is None
               else AccessPath.project_id == project_id)
        .order_by(AccessPath.created_at)
    )
    return list(rows.scalars())


async def add(
    session: AsyncSession, *, project_id: str | None, hostname: str, raw_path: str
) -> AccessPath:
    """Create the bypass in Cloudflare, then record it. Cloudflare goes first so
    the console never lists a hole it did not actually open. Raises ValueError
    on a bad or duplicate path, AccessNotConfigured without credentials, and
    AccessApiError if Cloudflare refuses."""
    path = normalize(raw_path, console_own=project_id is None)
    existing = await listing(session, project_id)
    if any(row.path == path for row in existing):
        raise ValueError(f'"{path}" is already open on {hostname}')

    token, account_id = await cloudflare.resolve_credentials(session)
    cf_app_id = await cloudflare.Access(token, account_id).create_bypass(hostname, path)

    row = AccessPath(
        project_id=project_id, hostname=hostname, path=path, cf_app_id=cf_app_id
    )
    session.add(row)
    await session.commit()
    return row


async def remove(session: AsyncSession, row: AccessPath) -> None:
    """Close a bypass. Cloudflare goes first and its failure is NOT swallowed:
    dropping the row on a failed delete would leave the path open with nothing
    in the console saying so."""
    token, account_id = await cloudflare.resolve_credentials(session)
    await cloudflare.Access(token, account_id).delete_app(row.cf_app_id)
    await session.delete(row)
    await session.commit()


async def move(
    session: AsyncSession, project_id: str, new_hostname: str
) -> tuple[int, int]:
    """Point a project's bypasses at a new hostname after a domain change, the
    same new-first-then-delete order the login gate uses. Returns (moved,
    failed): a bypass that cannot be recreated must not block the domain
    change, so failures are counted and reported rather than raised."""
    moved = failed = 0
    rows = await listing(session, project_id)
    if not rows:
        return 0, 0
    try:
        token, account_id = await cloudflare.resolve_credentials(session)
    except cloudflare.AccessNotConfigured:
        return 0, len(rows)  # nothing to move them with; the caller reports it
    access = cloudflare.Access(token, account_id)
    for row in rows:
        try:
            new_id = await access.create_bypass(new_hostname, row.path)
        except Exception:
            failed += 1
            continue
        old_id = row.cf_app_id
        row.hostname = new_hostname
        row.cf_app_id = new_id
        moved += 1
        try:  # best effort: the new one is live, the old host is going away
            await access.delete_app(old_id)
        except Exception:
            pass
    await session.commit()
    return moved, failed


async def forget(session: AsyncSession, project_id: str) -> None:
    """Best-effort removal of a project's bypass apps from Cloudflare, for when
    the project itself is being deleted. The rows go with the project (cascade);
    a Cloudflare hiccup must not block the delete."""
    try:
        token, account_id = await cloudflare.resolve_credentials(session)
    except Exception:
        return
    access = cloudflare.Access(token, account_id)
    for row in await listing(session, project_id):
        try:
            await access.delete_app(row.cf_app_id)
        except Exception:
            pass
