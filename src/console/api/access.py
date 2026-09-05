"""Cloudflare Access bypass paths, for an app or for the console itself.

Two scopes, one implementation: /api/projects/{id}/access/paths opens a path on
that app's hostname, /api/access/paths opens one on the console's own. The
console scope exists because its machine surfaces (/v1 and /mcp, for scripts
and agents) need the same hole, and setting it up by hand in the
Cloudflare dashboard is the step people miss.

What a bypass costs is in access_paths' docstring; the UI says it too, at the
point of adding one."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from console import access_paths, cloudflare, config, domains
from console.api.projects import get_project
from console.db.models import AccessPath
from console.db.session import get_session

router = APIRouter(tags=["access"])


class PathCreate(BaseModel):
    path: str


class AccessPathOut(BaseModel):
    id: str
    path: str
    hostname: str
    url: str  # what a caller actually hits, ready to paste into a client
    created_at: datetime


class AccessPathList(BaseModel):
    # The hostname is returned even when the list is empty: it is what the UI
    # shows the path against, and for the console scope nothing else knows it.
    hostname: str
    paths: list[AccessPathOut]


def _out(row: AccessPath) -> AccessPathOut:
    return AccessPathOut(
        id=row.id,
        path=row.path,
        hostname=row.hostname,
        url=f"https://{row.hostname}/{row.path}",
        created_at=row.created_at,
    )


async def _add(
    session: AsyncSession, project_id: str | None, hostname: str, raw_path: str
) -> AccessPathOut:
    try:
        row = await access_paths.add(
            session, project_id=project_id, hostname=hostname, raw_path=raw_path
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except cloudflare.AccessApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return _out(row)


async def _remove(session: AsyncSession, project_id: str | None, path_id: str) -> None:
    row = await session.get(AccessPath, path_id)
    if row is None or row.project_id != project_id:
        raise HTTPException(status_code=404, detail="no such bypass path")
    try:
        await access_paths.remove(session, row)
    except cloudflare.AccessApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.get("/api/projects/{project_id}/access/paths")
async def list_project_paths(
    project_id: str, session: AsyncSession = Depends(get_session)
) -> AccessPathList:
    project = await get_project(project_id, session)
    return AccessPathList(
        hostname=f"{project.subdomain}.{domains.of(project)}",
        paths=[_out(row) for row in await access_paths.listing(session, project_id)],
    )


@router.post("/api/projects/{project_id}/access/paths", status_code=201)
async def add_project_path(
    project_id: str,
    body: PathCreate,
    session: AsyncSession = Depends(get_session),
) -> AccessPathOut:
    """Open one path on this app's hostname to callers with no Access login."""
    project = await get_project(project_id, session)
    hostname = f"{project.subdomain}.{domains.of(project)}"
    return await _add(session, project_id, hostname, body.path)


@router.delete("/api/projects/{project_id}/access/paths/{path_id}", status_code=204)
async def remove_project_path(
    project_id: str, path_id: str, session: AsyncSession = Depends(get_session)
) -> None:
    await get_project(project_id, session)
    await _remove(session, project_id, path_id)


@router.get("/api/access/paths")
async def list_console_paths(
    session: AsyncSession = Depends(get_session),
) -> AccessPathList:
    return AccessPathList(
        hostname=config.HOSTNAME,
        paths=[_out(row) for row in await access_paths.listing(session, None)],
    )


@router.post("/api/access/paths", status_code=201)
async def add_console_path(
    body: PathCreate, session: AsyncSession = Depends(get_session)
) -> AccessPathOut:
    """Open one path on the console's own hostname: /v1 or /mcp for scripts
    and agents. /api is refused; see access_paths."""
    return await _add(session, None, config.HOSTNAME, body.path)


@router.delete("/api/access/paths/{path_id}", status_code=204)
async def remove_console_path(
    path_id: str, session: AsyncSession = Depends(get_session)
) -> None:
    await _remove(session, None, path_id)
