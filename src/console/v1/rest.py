"""The /v1 HTTP surface: a stable, token-authenticated API for scripts and AI
agents.

This is an APIRouter on the main app rather than a mounted sub-app so it shares
one dependency graph, one set of exception handlers, and one test fixture. The
version-scoped spec at /v1/openapi.json is built by hand from this router's
routes, which gets the separate document without the separate application.

Routes are thin on purpose: resolve the caller's token, call service, translate
console.errors into status codes. Anything that changes state depends on
require_write, so a read-only token cannot reach it."""

from fastapi import APIRouter, Depends, Query, Request
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from console import config
from console.db.session import get_session
from console.errors import (
    ConsoleError,
    Conflict,
    Invalid,
    NotFound,
    Unavailable,
    Upstream,
)
from console.v1 import models, service
from console.v1.auth import require_token, require_write

router = APIRouter(prefix="/v1", tags=["v1"])

TITLE = "console API"
VERSION = "1"
DESCRIPTION = """\
Read and operate a self-hosted console install.

Authenticate with a token minted in the console's Settings, sent as
`Authorization: Bearer csk_…`. Tokens carry either `read` or `write` scope;
every endpoint that changes something requires `write`.

Anywhere a project is addressed you may use its id, its name, or its subdomain.

Secret values are deliberately unreachable here. You can list which secrets a
project has, but reading, setting, or importing one stays in the browser
console behind Cloudflare Access.
"""

# console.errors carry a message written for the caller; the surface decides
# only what status code each kind deserves.
STATUS = {
    NotFound: 404,
    Invalid: 400,
    Conflict: 409,
    Unavailable: 503,
    Upstream: 502,
}


def install_error_handler(app) -> None:
    """Translate domain errors raised anywhere under /v1 into status codes."""

    @app.exception_handler(ConsoleError)
    async def _handle(_request: Request, exc: ConsoleError):
        return JSONResponse(
            status_code=STATUS.get(type(exc), 500), content={"detail": str(exc)}
        )


def openapi_schema() -> dict:
    """A spec describing only the /v1 routes, so a caller pointed at this API
    sees the machine surface and not the SPA's internal endpoints.

    Built from this router's own routes rather than by filtering the app's:
    FastAPI wraps included routers, so app.routes holds wrapper objects whose
    paths are not the endpoints' paths. The router's own list is already
    prefixed and is exactly what we want to publish."""
    return get_openapi(
        title=TITLE, version=VERSION, description=DESCRIPTION, routes=router.routes
    )


# ------------------------------------------------------------------ bodies


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    repo: str = Field(description='"owner/name" on GitHub')
    subdomain: str
    branch: str = Field(default="main", min_length=1, max_length=100)
    domain: str | None = Field(default=None, description="null means the primary domain")


class AccessUpdate(BaseModel):
    protected: bool
    emails: list[str] = []


class AccessPathCreate(BaseModel):
    path: str  # no leading slash needed, e.g. "api/ingest"


class DomainUpdate(BaseModel):
    domain: str | None = None
    repoint: str = Field(
        default="manual",
        description='For a protected app: "auto" moves its Access gate to the '
        'new hostname, "manual" leaves it to you.',
    )


class CommandCreate(BaseModel):
    command: str = Field(min_length=1, max_length=4096)


class DeploymentCreate(BaseModel):
    image: str = Field(description="full image ref including a tag")
    ref: str | None = Field(
        default=None,
        description="git ref to read console.toml from; defaults to the project's branch",
    )
    console_toml: str | None = Field(
        default=None, description="fallback for when GitHub cannot be reached"
    )


# ------------------------------------------------------------------- reads


@router.get("/system", summary="How the whole install is doing")
async def get_system(
    session: AsyncSession = Depends(get_session), _=Depends(require_token)
) -> models.System:
    return await service.get_system(session)


@router.get("/projects", summary="Every project")
async def list_projects(
    session: AsyncSession = Depends(get_session), _=Depends(require_token)
) -> list[models.Project]:
    return await service.list_projects(session)


@router.get("/projects/{project}", summary="One project")
async def get_project(
    project: str,
    session: AsyncSession = Depends(get_session),
    _=Depends(require_token),
) -> models.Project:
    return await service.get_project(session, project)


@router.get("/projects/{project}/deployments", summary="Deploy history")
async def list_deployments(
    project: str,
    session: AsyncSession = Depends(get_session),
    _=Depends(require_token),
) -> list[models.Deployment]:
    return await service.list_deployments(session, project)


@router.get(
    "/projects/{project}/deployments/{deployment_id}",
    summary="One deployment, including its deploy log",
)
async def get_deployment(
    project: str,
    deployment_id: str,
    session: AsyncSession = Depends(get_session),
    _=Depends(require_token),
) -> models.DeploymentDetail:
    return await service.get_deployment(session, project, deployment_id)


@router.get("/projects/{project}/container", summary="The app's container")
async def get_container(
    project: str,
    session: AsyncSession = Depends(get_session),
    _=Depends(require_token),
) -> models.Container:
    return await service.get_container(session, project)


@router.get("/projects/{project}/logs", summary="The app's container logs")
async def get_logs(
    project: str,
    tail: int = Query(default=config.LOG_TAIL_DEFAULT, ge=1, le=config.LOG_TAIL_MAX),
    session: AsyncSession = Depends(get_session),
    _=Depends(require_token),
) -> models.Logs:
    return await service.get_logs(session, project, tail)


@router.get(
    "/projects/{project}/secrets",
    summary="Which secrets this project has (names only, never values)",
)
async def list_secret_keys(
    project: str,
    session: AsyncSession = Depends(get_session),
    _=Depends(require_token),
) -> list[models.SecretKey]:
    return await service.list_secret_keys(session, project)


@router.get("/projects/{project}/commands", summary="One-off command history")
async def list_commands(
    project: str,
    session: AsyncSession = Depends(get_session),
    _=Depends(require_token),
) -> list[models.CommandRun]:
    return await service.list_commands(session, project)


@router.get(
    "/projects/{project}/commands/{run_id}",
    summary="One command run, including its output",
)
async def get_command(
    project: str,
    run_id: str,
    session: AsyncSession = Depends(get_session),
    _=Depends(require_token),
) -> models.CommandRunDetail:
    return await service.get_command(session, project, run_id)


@router.get("/backups", summary="Backup status and history")
async def get_backups(
    session: AsyncSession = Depends(get_session), _=Depends(require_token)
) -> models.Backups:
    return await service.get_backups(session)


# ------------------------------------------------------------------ writes


@router.post("/projects", status_code=201, summary="Register a project")
async def create_project(
    body: ProjectCreate,
    session: AsyncSession = Depends(get_session),
    _=Depends(require_write),
) -> models.Project:
    return await service.create_project(
        session,
        name=body.name.strip(),
        repo=body.repo.strip(),
        subdomain=body.subdomain.strip(),
        branch=body.branch.strip(),
        domain=body.domain,
    )


@router.delete("/projects/{project}", status_code=204, summary="Delete a project")
async def delete_project(
    project: str,
    session: AsyncSession = Depends(get_session),
    _=Depends(require_write),
) -> None:
    await service.delete_project(session, project)


@router.put("/projects/{project}/access", summary="Turn the Access login on or off")
async def set_access(
    project: str,
    body: AccessUpdate,
    session: AsyncSession = Depends(get_session),
    _=Depends(require_write),
) -> models.Project:
    return await service.set_access(session, project, body.protected, body.emails)


@router.get(
    "/projects/{project}/access/paths",
    summary="Paths on this app that skip the Access login",
)
async def list_project_access_paths(
    project: str,
    session: AsyncSession = Depends(get_session),
    _=Depends(require_token),
) -> models.AccessPathList:
    return await service.list_access_paths(session, project)


@router.post(
    "/projects/{project}/access/paths",
    status_code=201,
    summary="Let machines reach one path without the login",
)
async def open_project_access_path(
    project: str,
    body: AccessPathCreate,
    session: AsyncSession = Depends(get_session),
    _=Depends(require_write),
) -> models.AccessPath:
    return await service.open_access_path(session, project, body.path)


@router.delete(
    "/projects/{project}/access/paths/{path:path}",
    status_code=204,
    summary="Put the login back in front of one path",
)
async def close_project_access_path(
    project: str,
    path: str,
    session: AsyncSession = Depends(get_session),
    _=Depends(require_write),
) -> None:
    await service.close_access_path(session, project, path)


@router.get("/access/paths", summary="Paths on the console that skip the login")
async def list_console_access_paths(
    session: AsyncSession = Depends(get_session),
    _=Depends(require_token),
) -> models.AccessPathList:
    """The console's own machine paths: /v1 and /mcp, for callers like this
    one."""
    return await service.list_access_paths(session, None)


@router.post(
    "/access/paths",
    status_code=201,
    summary="Open one path on the console's own hostname",
)
async def open_console_access_path(
    body: AccessPathCreate,
    session: AsyncSession = Depends(get_session),
    _=Depends(require_write),
) -> models.AccessPath:
    """/api is refused: it is the console's own unauthenticated write surface,
    so a bypass there would hand the console to the internet. This surface,
    /v1, is the supported way in for a machine."""
    return await service.open_access_path(session, None, body.path)


@router.delete(
    "/access/paths/{path:path}",
    status_code=204,
    summary="Close one path on the console's own hostname",
)
async def close_console_access_path(
    path: str,
    session: AsyncSession = Depends(get_session),
    _=Depends(require_write),
) -> None:
    await service.close_access_path(session, None, path)


@router.put("/projects/{project}/domain", summary="Move a project to another domain")
async def set_domain(
    project: str,
    body: DomainUpdate,
    session: AsyncSession = Depends(get_session),
    _=Depends(require_write),
) -> models.DomainChange:
    return await service.set_domain(session, project, body.domain, body.repoint)


@router.post(
    "/projects/{project}/deployments",
    status_code=202,
    summary="Deploy an image that already exists in the registry",
)
async def deploy_image(
    project: str,
    body: DeploymentCreate,
    session: AsyncSession = Depends(get_session),
    _=Depends(require_write),
) -> models.Accepted:
    return await service.deploy_image(
        session, project, body.image, body.ref, body.console_toml
    )


class BuildCreate(BaseModel):
    ref: str | None = Field(
        default=None,
        description="branch, tag, or sha to build; defaults to the project's branch",
    )


@router.post(
    "/projects/{project}/builds",
    status_code=202,
    summary="Build the repo on the box, push the image, and deploy it",
)
async def build_project(
    project: str,
    body: BuildCreate,
    session: AsyncSession = Depends(get_session),
    _=Depends(require_write),
) -> models.Accepted:
    return await service.build_project(session, project, body.ref)


@router.post(
    "/projects/{project}/deployments/{deployment_id}/rollback",
    status_code=202,
    summary="Roll back to a build that served traffic",
)
async def rollback(
    project: str,
    deployment_id: str,
    session: AsyncSession = Depends(get_session),
    _=Depends(require_write),
) -> models.Accepted:
    return await service.rollback(session, project, deployment_id)


@router.post(
    "/projects/{project}/deployments/{deployment_id}/redeploy",
    status_code=202,
    summary="Re-run a build's image and config as a fresh deployment",
)
async def redeploy(
    project: str,
    deployment_id: str,
    session: AsyncSession = Depends(get_session),
    _=Depends(require_write),
) -> models.Accepted:
    return await service.redeploy(session, project, deployment_id)


@router.post(
    "/projects/{project}/controls/{action}",
    summary="start, stop, or restart the app's container",
)
async def control(
    project: str,
    action: str,
    session: AsyncSession = Depends(get_session),
    _=Depends(require_write),
) -> models.Container:
    return await service.control(session, project, action)


@router.post(
    "/projects/{project}/commands",
    status_code=202,
    summary="Run a one-off command in the app's container",
)
async def run_command(
    project: str,
    body: CommandCreate,
    session: AsyncSession = Depends(get_session),
    _=Depends(require_write),
) -> models.Accepted:
    return await service.run_command(session, project, body.command)


@router.post("/backups", status_code=202, summary="Back up the console now")
async def trigger_backup(
    session: AsyncSession = Depends(get_session), _=Depends(require_write)
) -> models.Accepted:
    return await service.trigger_backup(session)
