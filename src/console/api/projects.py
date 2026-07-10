import re
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from console.db.models import Project
from console.db.session import get_session
from console.schema.console_toml import validate_subdomain_format
from console.starters import starter_files

REPO_RE = re.compile(r"^[\w.-]+/[\w.-]+$")

router = APIRouter(prefix="/api/projects")


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    repo: str
    branch: str = Field(default="main", min_length=1, max_length=100)
    subdomain: str


class ProjectOut(BaseModel):
    id: str
    name: str
    repo: str
    branch: str
    subdomain: str
    created_at: datetime

    model_config = {"from_attributes": True}


async def get_project(project_id: str, session: AsyncSession) -> Project:
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="no such project")
    return project


@router.get("")
async def list_projects(session: AsyncSession = Depends(get_session)) -> list[ProjectOut]:
    result = await session.scalars(select(Project).order_by(Project.created_at))
    return [ProjectOut.model_validate(p) for p in result]


@router.post("", status_code=201)
async def create_project(
    body: ProjectCreate, session: AsyncSession = Depends(get_session)
) -> ProjectOut:
    # Validated here rather than in the pydantic model so failures are 400s
    # with one flat, readable message the UI can show inline.
    try:
        validate_subdomain_format(body.subdomain)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not REPO_RE.match(body.repo):
        raise HTTPException(
            status_code=400, detail=f'repo "{body.repo}" must look like "owner/name"'
        )
    for field in ("name", "repo", "subdomain"):
        value = getattr(body, field)
        exists = await session.scalar(
            select(Project).where(getattr(Project, field) == value)
        )
        if exists:
            raise HTTPException(
                status_code=409,
                detail=f'a project with {field} "{value}" already exists',
            )
    project = Project(**body.model_dump())
    session.add(project)
    await session.commit()
    return ProjectOut.model_validate(project)


@router.get("/{project_id}")
async def get_one(
    project_id: str, session: AsyncSession = Depends(get_session)
) -> ProjectOut:
    return ProjectOut.model_validate(await get_project(project_id, session))


@router.delete("/{project_id}", status_code=204)
async def delete_project(
    project_id: str, session: AsyncSession = Depends(get_session)
) -> None:
    project = await get_project(project_id, session)
    await session.delete(project)
    await session.commit()


@router.get("/{project_id}/starters")
async def get_starters(
    project_id: str, session: AsyncSession = Depends(get_session)
) -> dict:
    """Commented console.toml and Dockerfile templates, prefilled with this
    project's name and subdomain, for bootstrapping the app repo."""
    project = await get_project(project_id, session)
    return starter_files(project)
