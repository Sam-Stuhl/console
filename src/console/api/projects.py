import re
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from console import cloudflare, config
from console.db.models import Project
from console.db.session import get_session
from console.schema.console_toml import validate_subdomain_format
from console.starters import starter_files

REPO_RE = re.compile(r"^[\w.-]+/[\w.-]+$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

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
    url: str  # where it serves, e.g. https://notion-sync.samstuhl.com
    protected: bool  # Cloudflare Access login is in front of it
    access_emails: list[str]


class AccessUpdate(BaseModel):
    protected: bool
    emails: list[str] = []


def _out(project: Project) -> ProjectOut:
    return ProjectOut(
        id=project.id,
        name=project.name,
        repo=project.repo,
        branch=project.branch,
        subdomain=project.subdomain,
        created_at=project.created_at,
        url=f"https://{project.subdomain}.{config.DOMAIN}",
        protected=project.protected,
        access_emails=project.access_emails.split(",") if project.access_emails else [],
    )


async def get_project(project_id: str, session: AsyncSession) -> Project:
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="no such project")
    return project


@router.get("")
async def list_projects(session: AsyncSession = Depends(get_session)) -> list[ProjectOut]:
    result = await session.scalars(select(Project).order_by(Project.created_at))
    return [_out(p) for p in result]


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
    return _out(project)


@router.get("/{project_id}")
async def get_one(
    project_id: str, session: AsyncSession = Depends(get_session)
) -> ProjectOut:
    return _out(await get_project(project_id, session))


@router.put("/{project_id}/access")
async def set_access(
    project_id: str,
    body: AccessUpdate,
    session: AsyncSession = Depends(get_session),
) -> ProjectOut:
    """Turn the Cloudflare Access login on or off for this app's hostname.
    Cloudflare is reconciled first; the row is only saved if that succeeds, so
    the console's record never claims a gate that was not actually created."""
    project = await get_project(project_id, session)

    emails = [e.strip() for e in body.emails if e.strip()]
    if body.protected:
        if not emails:
            raise HTTPException(
                status_code=400,
                detail="add at least one email, or the app would allow no one in",
            )
        for email in emails:
            if not EMAIL_RE.match(email):
                raise HTTPException(status_code=400, detail=f'"{email}" is not a valid email')

    hostname = f"{project.subdomain}.{config.DOMAIN}"
    try:
        cf_app_id = await cloudflare.reconcile(
            hostname, body.protected, emails, project.cf_app_id
        )
    except cloudflare.AccessApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    project.protected = body.protected
    project.access_emails = ",".join(emails) if body.protected else None
    project.cf_app_id = cf_app_id
    await session.commit()
    return _out(project)


@router.delete("/{project_id}", status_code=204)
async def delete_project(
    project_id: str, session: AsyncSession = Depends(get_session)
) -> None:
    project = await get_project(project_id, session)
    # Best effort: remove the Access app so deleting a project does not leave a
    # dangling login gate. A Cloudflare failure must not block the delete.
    await cloudflare.delete_if_present(project.cf_app_id)
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
