"""Restart / stop / start an app's container from the console, the small
container ops that otherwise need the shell. These act only on the container the
deploy engine labeled for the project and never create or remove one, so the
deploy safety invariant is untouched: stopping is the user deliberately taking
the app offline, not a deploy dropping a healthy one."""

import docker.errors
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from console.api.projects import get_project
from console.db.session import get_session
from console.docker.client import get_client, run
from console.docker.containers import list_project_containers, shape_container

router = APIRouter(prefix="/api/projects/{project_id}")

ACTIONS = ("start", "stop", "restart")


async def _sole_container(project_id: str):
    """The project's one container, or an HTTPException. Refuses when a deploy is
    mid-flight (two containers) so an action never hits the wrong one."""
    containers = await list_project_containers(project_id, include_stopped=True)
    if not containers:
        raise HTTPException(
            status_code=409, detail="no container for this app; deploy it first"
        )
    if len(containers) > 1:
        raise HTTPException(
            status_code=409, detail="a deploy is in progress; try again shortly"
        )
    return containers[0]


@router.get("/container")
async def get_container(
    project_id: str, session: AsyncSession = Depends(get_session)
) -> dict:
    await get_project(project_id, session)
    containers = await list_project_containers(project_id, include_stopped=True)
    if not containers:
        return {"state": "absent"}
    # Mid-deploy there are two; prefer the running one for a sensible display.
    running = [c for c in containers if c.status == "running"]
    return shape_container((running or containers)[0].attrs)


@router.post("/controls/{action}")
async def control(
    project_id: str,
    action: str,
    session: AsyncSession = Depends(get_session),
) -> dict:
    if action not in ACTIONS:
        raise HTTPException(status_code=404, detail=f'unknown action "{action}"')
    await get_project(project_id, session)
    container = await _sole_container(project_id)
    try:
        await run(getattr(container, action))
    except docker.errors.DockerException as exc:
        raise HTTPException(status_code=502, detail=f"{action} failed: {exc}")
    # Reload so the response reflects the new state.
    refreshed = await run(get_client().containers.get, container.id)
    return shape_container(refreshed.attrs)
