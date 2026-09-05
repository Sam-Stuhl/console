import re
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from console import access_paths, appicon, cloudflare, config, domains
from console.db.models import Project, ProjectHealth
from console.db.session import get_session
from console.deploy import builder
from console.deploy.history import deploy_state
from console.errors import Conflict, Invalid, Unavailable, Upstream
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
    domain: str | None = None  # None means the primary CONSOLE_DOMAIN


class ProjectOut(BaseModel):
    id: str
    name: str
    repo: str
    branch: str
    subdomain: str
    domain: str  # the base domain it serves under
    created_at: datetime
    url: str  # where it serves, e.g. https://blog.example.com
    protected: bool  # Cloudflare Access login is in front of it
    access_emails: list[str]
    health: str  # live liveness from the monitor: up | down | unknown
    has_icon: bool  # the app's favicon has been fetched (else the UI shows initials)
    icon_fetched_at: datetime | None  # doubles as a cache-buster for the icon URL
    deploy_status: str | None  # latest deployment's status (queued/building/deploying/live/failed/…)
    is_live: bool  # a deployment is currently live (serving), independent of the monitor ping
    # What CI tags this project's images as, minus the tag: the prefill for the
    # deploy-an-image form, so only a tag has to be typed. Derived, never stored.
    image_hint: str


class BuildRequest(BaseModel):
    ref: str | None = None  # branch, tag, or sha; None means the tracked branch


class AccessUpdate(BaseModel):
    protected: bool
    emails: list[str] = []


class DomainUpdate(BaseModel):
    domain: str | None = None  # None means the primary CONSOLE_DOMAIN
    # For a protected app, how to move its Access login gate to the new hostname:
    # "auto" recreates it via Cloudflare; "manual" leaves it to the user.
    repoint: Literal["auto", "manual"] = "manual"


class DomainChangeResult(BaseModel):
    project: ProjectOut
    redeploy_required: bool  # the Traefik host label only changes on next deploy
    note: str | None = None  # what happened to the Access gate, if anything


def _out(
    project: Project,
    health: str = "unknown",
    deploy_status: str | None = None,
    is_live: bool = False,
) -> ProjectOut:
    domain = domains.of(project)
    return ProjectOut(
        id=project.id,
        name=project.name,
        repo=project.repo,
        branch=project.branch,
        subdomain=project.subdomain,
        domain=domain,
        created_at=project.created_at,
        url=f"https://{project.subdomain}.{domain}",
        protected=project.protected,
        access_emails=project.access_emails.split(",") if project.access_emails else [],
        health=health,
        has_icon=project.icon_data is not None,
        icon_fetched_at=project.icon_fetched_at,
        deploy_status=deploy_status,
        is_live=is_live,
        # GHCR paths are lowercase, which is what the build workflow pushes to.
        image_hint=f"ghcr.io/{project.repo.lower()}:",
    )


async def get_project(project_id: str, session: AsyncSession) -> Project:
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="no such project")
    return project


@router.get("")
async def list_projects(session: AsyncSession = Depends(get_session)) -> list[ProjectOut]:
    projects = (await session.scalars(select(Project).order_by(Project.created_at))).all()
    health_rows = await session.scalars(select(ProjectHealth))
    health = {h.project_id: h.state for h in health_rows}
    latest, live = await deploy_state(session)
    return [
        _out(p, health.get(p.id, "unknown"), latest.get(p.id), p.id in live)
        for p in projects
    ]


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
    if body.domain is not None and body.domain not in await domains.available(session):
        raise HTTPException(
            status_code=400,
            detail=f'domain "{body.domain}" is not configured; add it in Settings first',
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


@router.get("/domains")
async def list_domains(session: AsyncSession = Depends(get_session)) -> dict:
    """The base domains a project can be created under (primary + configured
    extras). Declared before /{project_id} so the static path wins."""
    return {"domains": await domains.available(session)}


@router.get("/{project_id}")
async def get_one(
    project_id: str, session: AsyncSession = Depends(get_session)
) -> ProjectOut:
    project = await get_project(project_id, session)
    health = await session.get(ProjectHealth, project_id)
    latest, live = await deploy_state(session)
    return _out(
        project,
        health.state if health else "unknown",
        latest.get(project_id),
        project_id in live,
    )


@router.post("/{project_id}/builds", status_code=202)
async def request_build(
    project_id: str,
    body: BuildRequest,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Build the repo at a ref on the box and deploy what comes out. This is
    what a push used to trigger through GitHub Actions; the ref is resolved
    through the GitHub connection and the build runs here."""
    project = await get_project(project_id, session)
    try:
        deployment = await builder.request_build(session, project, body.ref)
    except Invalid as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Conflict as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except Unavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Upstream as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return {"deployment_id": deployment.id, "status": "building"}


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

    hostname = f"{project.subdomain}.{domains.of(project)}"
    token, account_id = await cloudflare.resolve_credentials(session)  # 503 if unconfigured
    try:
        cf_app_id = await cloudflare.Access(token, account_id).reconcile(
            hostname, body.protected, emails, project.cf_app_id
        )
    except cloudflare.AccessApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    project.protected = body.protected
    project.access_emails = ",".join(emails) if body.protected else None
    project.cf_app_id = cf_app_id
    await session.commit()
    return _out(project)


@router.put("/{project_id}/domain")
async def set_domain(
    project_id: str,
    body: DomainUpdate,
    session: AsyncSession = Depends(get_session),
) -> DomainChangeResult:
    """Move a project to a different base domain. The change only reaches Traefik
    on the next deploy (the host label is baked in then), so the result always
    flags that a redeploy is required.

    If the app is protected by Cloudflare Access, its login gate is tied to the
    old hostname. "auto" recreates the gate for the new hostname (creating the
    new one first, then removing the old, so a Cloudflare failure changes
    nothing); "manual" leaves the gate for the user to move themselves."""
    project = await get_project(project_id, session)

    if body.domain is not None and body.domain not in await domains.available(session):
        raise HTTPException(
            status_code=400,
            detail=f'domain "{body.domain}" is not configured; add it in Settings first',
        )
    target = body.domain or config.DOMAIN
    health = await session.get(ProjectHealth, project_id)
    state = health.state if health else "unknown"

    if target == domains.of(project):
        return DomainChangeResult(
            project=_out(project, state),
            redeploy_required=False,
            note="Already on this domain.",
        )

    note: str | None = None
    if project.protected:
        new_hostname = f"{project.subdomain}.{target}"
        if body.repoint == "auto":
            emails = project.access_emails.split(",") if project.access_emails else []
            token, account_id = await cloudflare.resolve_credentials(session)  # 503
            access = cloudflare.Access(token, account_id)
            try:
                new_id = await access.reconcile(new_hostname, True, emails, None)
            except cloudflare.AccessApiError as exc:
                raise HTTPException(status_code=502, detail=str(exc))
            if project.cf_app_id:  # remove the old-hostname gate, best effort
                try:
                    await access.delete_app(project.cf_app_id)
                except Exception:
                    pass
            project.cf_app_id = new_id
            note = f"Moved the Cloudflare Access gate to {new_hostname}."
        else:
            note = (
                "Access is still on, but its login gate still points at the old "
                "hostname. Move it in Cloudflare, or toggle protection off and "
                "back on here to recreate it for the new hostname."
            )

    # Bypass paths are tied to the hostname too, and unlike the login gate they
    # exist whether or not the app is protected. A failed move must not block
    # the domain change, so move() reports rather than raises.
    if body.repoint == "auto":
        moved, failed = await access_paths.move(
            session, project_id, f"{project.subdomain}.{target}"
        )
        if moved or failed:
            paths_note = f"Moved {moved} bypass path(s)."
            if failed:
                paths_note += (
                    f" {failed} could not be recreated: check them in Cloudflare, "
                    "or remove and re-add them here."
                )
            note = f"{note} {paths_note}" if note else paths_note

    # Store null for the primary so the "null = primary" invariant holds.
    project.domain = None if target == config.DOMAIN else target
    await session.commit()
    return DomainChangeResult(
        project=_out(project, state), redeploy_required=True, note=note
    )


@router.delete("/{project_id}", status_code=204)
async def delete_project(
    project_id: str, session: AsyncSession = Depends(get_session)
) -> None:
    project = await get_project(project_id, session)
    # Best effort: remove the Access app so deleting a project does not leave a
    # dangling login gate. A Cloudflare hiccup or unconfigured creds must not
    # block the delete.
    if project.cf_app_id:
        try:
            token, account_id = await cloudflare.resolve_credentials(session)
            await cloudflare.Access(token, account_id).delete_app(project.cf_app_id)
        except Exception:
            pass
    # Same for any bypass paths, or the hostname keeps a hole with nothing left
    # in the console to show it.
    await access_paths.forget(session, project_id)
    await session.delete(project)
    await session.commit()


@router.get("/{project_id}/icon")
async def get_icon(
    project_id: str, session: AsyncSession = Depends(get_session)
) -> Response:
    """The app's fetched favicon bytes, or 404 if none has been captured yet
    (the UI falls back to initials). The restrictive headers neutralize a
    hostile SVG favicon if this URL is ever opened directly."""
    project = await get_project(project_id, session)
    if project.icon_data is None:
        raise HTTPException(status_code=404, detail="no icon for this project")
    return Response(
        content=project.icon_data,
        media_type=project.icon_content_type or "application/octet-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'",
        },
    )


@router.post("/{project_id}/icon/refresh")
async def refresh_icon(
    project_id: str, session: AsyncSession = Depends(get_session)
) -> dict:
    """Pull the app's favicon from its running container now. Returns whether
    one was found; an app that serves no favicon just keeps its initials."""
    project = await get_project(project_id, session)
    fetched = await appicon.fetch_and_store(session, project)
    await session.commit()
    return {"fetched": fetched}


@router.get("/{project_id}/starters")
async def get_starters(
    project_id: str, session: AsyncSession = Depends(get_session)
) -> dict:
    """Commented console.toml and Dockerfile templates, prefilled with this
    project's name and subdomain, for bootstrapping the app repo."""
    project = await get_project(project_id, session)
    return starter_files(project)
