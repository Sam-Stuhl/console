"""Run a one-off command in a project's live container and read back its
output. A command exec's into the running container the deploy engine created
for the project; if nothing is live, running is refused. Output streams into
the command_runs row, which the UI polls until the run reaches a terminal
state, the same poll-not-stream shape as deployments."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from console.api.projects import get_project
from console.commands import runner
from console.db.models import CommandRun
from console.db.session import get_session
from console.docker.containers import find_project_container

router = APIRouter(prefix="/api/projects/{project_id}/commands")

LIST_LIMIT = 50


class CommandCreate(BaseModel):
    command: str = Field(min_length=1, max_length=4096)


class CommandRunOut(BaseModel):
    id: str
    command: str
    container_name: str | None
    status: str
    exit_code: int | None
    failure_reason: str | None
    created_at: datetime
    finished_at: datetime | None

    model_config = {"from_attributes": True}


class CommandRunDetail(CommandRunOut):
    output: str | None


async def _get_run(
    session: AsyncSession, project_id: str, run_id: str
) -> CommandRun:
    cmd_run = await session.get(CommandRun, run_id)
    if cmd_run is None or cmd_run.project_id != project_id:
        raise HTTPException(status_code=404, detail="no such command run")
    return cmd_run


@router.get("")
async def list_runs(
    project_id: str, session: AsyncSession = Depends(get_session)
) -> list[CommandRunOut]:
    await get_project(project_id, session)
    result = await session.scalars(
        select(CommandRun)
        .where(CommandRun.project_id == project_id)
        .order_by(CommandRun.created_at.desc())
        .limit(LIST_LIMIT)
    )
    return [CommandRunOut.model_validate(r) for r in result]


@router.get("/{run_id}")
async def get_run(
    project_id: str,
    run_id: str,
    session: AsyncSession = Depends(get_session),
) -> CommandRunDetail:
    return CommandRunDetail.model_validate(await _get_run(session, project_id, run_id))


@router.post("", status_code=202)
async def create_run(
    project_id: str,
    body: CommandCreate,
    session: AsyncSession = Depends(get_session),
) -> dict:
    await get_project(project_id, session)
    command = body.command.strip()
    if not command:
        raise HTTPException(status_code=400, detail="command cannot be empty")
    # Fail fast if nothing is live; the runner re-checks in case it stops
    # between here and the exec.
    if await find_project_container(project_id) is None:
        raise HTTPException(
            status_code=409, detail="app is not running; deploy it first"
        )

    cmd_run = CommandRun(project_id=project_id, command=command, status="running")
    session.add(cmd_run)
    await session.commit()
    runner.enqueue(cmd_run.id)
    return {"run_id": cmd_run.id, "status": "running"}
