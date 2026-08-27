"""Cloudflare Access bypass paths: the exception that lets a machine reach one
path of an app that is otherwise behind the browser login.

Access evaluates the more specific path first, so an app registered on
`host/api/ingest` with a Bypass policy wins over the login gate on `host`. That
is exactly the shape docs/server-setup.md builds by hand for the console's own
/hooks; this module does it from the console instead of the Cloudflare
dashboard, for an app's own API paths and for the console's.

A bypass is a real hole, not a convenience: the path is then reachable by
anyone on the internet, and whatever authentication the app does itself is the
only thing in front of it. So the console refuses the two shapes that are
mistakes rather than trades: an empty path (which would take the login off the
whole hostname) and, on the console's own hostname, /api (which has no
authentication of its own by design, and would hand over every project, deploy,
log, and secret write). The /v1 surface exists for that case and wants a
console-issued token.

A bypass made by hand in the Cloudflare dashboard is invisible here, which
matters twice: the console cannot close it, and it would happily create a second
app for the same path. discover() finds those (an Access app scoped to a path,
with a bypass policy, on a hostname this console knows) and adopt() records one
against its existing Cloudflare id, changing nothing at Cloudflare. That is why
adoption is safe to run against a live install: nothing is created, deleted, or
re-pointed, so the path that CI depends on never stops working.
"""

import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from console import cloudflare, config, domains
from console.db.models import AccessPath, Project

# Path segments of unreserved URL characters. No scheme, query, fragment,
# wildcard, or space: a bypass is matched as a literal prefix, so anything
# fancier is a typo that would silently not match what the user meant.
PATH_RE = re.compile(r"^[A-Za-z0-9._~-]+(?:/[A-Za-z0-9._~-]+)*$")

# First segments the console will not open up on its OWN hostname. /api is the
# SPA's unauthenticated write surface (Access is its only gate), and /hooks
# self-authenticates but is not the console's to hand out casually here: it is
# part of setup, added once in Cloudflare, and listed in the UI as such.
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
) -> tuple[AccessPath, bool]:
    """Open a path: create the bypass in Cloudflare, then record it. Cloudflare
    goes first so the console never lists a hole it did not actually open.

    Returns (row, adopted), where adopted says the path already existed in
    Cloudflare and was taken over rather than created. Raises ValueError on a
    bad or duplicate path, AccessNotConfigured without credentials, and
    AccessApiError if Cloudflare refuses."""
    path = normalize(raw_path, console_own=project_id is None)
    existing = await listing(session, project_id)
    if any(row.path == path for row in existing):
        raise ValueError(f'"{path}" is already open on {hostname}')

    # If Cloudflare already has a bypass for exactly this path, take that one
    # over instead of creating a second app for it. Two apps claiming one path
    # is the mess adoption exists to clean up, not one to add to.
    candidates = await discover(session)
    for candidate in candidates:
        if candidate["project_id"] == project_id and candidate["path"] == path:
            return await adopt(session, candidate["cf_app_id"], candidates), True

    token, account_id = await cloudflare.resolve_credentials(session)
    cf_app_id = await cloudflare.Access(token, account_id).create_bypass(hostname, path)

    row = AccessPath(
        project_id=project_id, hostname=hostname, path=path, cf_app_id=cf_app_id
    )
    session.add(row)
    await session.commit()
    return row, False


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


# ------------------------------------------------------------- adoption


def _split_domain(domain: str) -> tuple[str, str] | None:
    """An Access app's domain into (hostname, path), or None when it carries no
    path. A path is what distinguishes a bypass from a login gate, so an app
    without one is never a candidate."""
    value = (domain or "").strip()
    for scheme in ("https://", "http://"):
        if value.lower().startswith(scheme):
            value = value[len(scheme) :]
    hostname, sep, path = value.partition("/")
    path = path.strip("/")
    if not sep or not path or not hostname:
        return None
    return hostname.lower(), path


async def _known_hostnames(session: AsyncSession) -> dict[str, str | None]:
    """Hostnames this console is entitled to manage, mapped to the project they
    belong to (None for the console's own). Anything else in the account is
    someone else's business and is left alone."""
    hosts: dict[str, str | None] = {config.HOSTNAME.lower(): None}
    for project in await session.scalars(select(Project)):
        hosts[f"{project.subdomain}.{domains.of(project)}".lower()] = project.id
    return hosts


async def discover(session: AsyncSession) -> list[dict]:
    """Bypasses that exist in Cloudflare but not here: the ones set up by hand
    before the console could do it. Read-only against Cloudflare.

    Returns dicts of cf_app_id, hostname, path, project_id (None for the
    console's own hostname)."""
    known = await _known_hostnames(session)
    recorded = {row.cf_app_id for row in await session.scalars(select(AccessPath))}

    token, account_id = await cloudflare.resolve_credentials(session)
    access = cloudflare.Access(token, account_id)

    found: list[dict] = []
    for app in await access.list_apps():
        app_id = app.get("id")
        if not app_id or app_id in recorded:
            continue
        split = _split_domain(app.get("domain", ""))
        if split is None:
            continue
        hostname, path = split
        if hostname not in known:
            continue
        if not await access.is_bypass(app_id):  # a gate on a path, not a bypass
            continue
        found.append(
            {
                "cf_app_id": app_id,
                "hostname": hostname,
                "path": path,
                "project_id": known[hostname],
            }
        )
    return found


async def adopt(
    session: AsyncSession, cf_app_id: str, candidates: list[dict] | None = None
) -> AccessPath:
    """Take over an existing Cloudflare bypass: record it here against the id it
    already has. Nothing is created or changed at Cloudflare, so the path keeps
    working exactly as it did.

    Identified by Cloudflare's app id rather than by its path, because the thing
    adoption exists to clean up is precisely the case where two apps claim the
    same path."""
    for candidate in candidates if candidates is not None else await discover(session):
        if candidate["cf_app_id"] != cf_app_id:
            continue
        existing = await listing(session, candidate["project_id"])
        if any(row.path == candidate["path"] for row in existing):
            raise ValueError(
                f"\"{candidate['path']}\" is already open on "
                f"{candidate['hostname']}; this is a second Cloudflare app for "
                "the same path, so delete one of them in Cloudflare"
            )
        row = AccessPath(
            project_id=candidate["project_id"],
            hostname=candidate["hostname"],
            path=candidate["path"],
            cf_app_id=cf_app_id,
        )
        session.add(row)
        await session.commit()
        return row
    raise ValueError(
        "that Cloudflare app is not one this console can adopt: it is already "
        "recorded, is not a bypass, or is not on a hostname this console manages"
    )
