"""What /v1 can actually do, independent of how it was called.

Every function here takes a session and returns a v1 model, raising the shared
console.errors on failure. Nothing in this module knows about HTTP status codes
or MCP, which is the point: the REST routes and the MCP tools are both thin
translations over these, so the two surfaces cannot drift apart.

There is deliberately no way to read, write, or import a secret value here, and
no terminal. Those stay behind Cloudflare Access in the browser. A caller can
learn which secrets exist (list_secret_keys) but never what they are."""

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from console import (
    access_paths,
    cloudflare,
    config,
    credentials,
    domains,
    settings_store,
)
from console.api.projects import EMAIL_RE, REPO_RE  # the project field rules
from console.backup import engine as backup_engine
from console.commands import runner
from console.db.models import (
    AccessPath,
    BackupRun,
    CommandRun,
    Deployment,
    Project,
    ProjectHealth,
    Secret,
)
from console.deploy import builder, engine as deploy_engine, history, manual, plan
from console.docker import containers as docker_containers
from console.docker.client import get_client, run
from console.errors import Conflict, Invalid, NotFound, Unavailable
from console.schema.console_toml import validate_subdomain_format
from console.v1 import models

DEPLOYMENT_LIMIT = 50
COMMAND_LIMIT = 50
BACKUP_LIMIT = 20
CONTROL_ACTIONS = ("start", "stop", "restart")


# ---------------------------------------------------------------- projects


async def resolve_project(session: AsyncSession, ref: str) -> Project:
    """A project by id, name, or subdomain.

    Agents work from names, not UUIDs, and a caller reading the console's own
    UI sees names and subdomains rather than ids. Ids are tried first so an
    exact id can never be shadowed by someone else's name."""
    project = await session.get(Project, ref)
    if project is not None:
        return project
    lowered = ref.lower()
    project = await session.scalar(
        select(Project).where(
            (func.lower(Project.name) == lowered)
            | (func.lower(Project.subdomain) == lowered)
        )
    )
    if project is None:
        raise NotFound(f'no project matching "{ref}"')
    return project


def _project(
    project: Project,
    health: str = "unknown",
    deploy_status: str | None = None,
    is_live: bool = False,
) -> models.Project:
    domain = domains.of(project)
    return models.Project(
        id=project.id,
        name=project.name,
        repo=project.repo,
        branch=project.branch,
        subdomain=project.subdomain,
        domain=domain,
        url=f"https://{project.subdomain}.{domain}",
        protected=project.protected,
        access_emails=project.access_emails.split(",") if project.access_emails else [],
        health=health,
        deploy_status=deploy_status,
        is_live=is_live,
        auto_build=project.auto_build,
        # GHCR paths are lowercase, which is what the build workflow pushes to.
        image_hint=f"ghcr.io/{project.repo.lower()}:",
        created_at=project.created_at,
    )


async def list_projects(session: AsyncSession) -> list[models.Project]:
    projects = (await session.scalars(select(Project).order_by(Project.created_at))).all()
    health = {h.project_id: h.state for h in await session.scalars(select(ProjectHealth))}
    latest, live = await history.deploy_state(session)
    return [
        _project(p, health.get(p.id, "unknown"), latest.get(p.id), p.id in live)
        for p in projects
    ]


async def get_project(session: AsyncSession, ref: str) -> models.Project:
    project = await resolve_project(session, ref)
    health = await session.get(ProjectHealth, project.id)
    latest, live = await history.deploy_state(session)
    return _project(
        project,
        health.state if health else "unknown",
        latest.get(project.id),
        project.id in live,
    )


async def create_project(
    session: AsyncSession,
    name: str,
    repo: str,
    subdomain: str,
    branch: str = "main",
    domain: str | None = None,
) -> models.Project:
    try:
        validate_subdomain_format(subdomain)
    except ValueError as exc:
        raise Invalid(str(exc))
    if not REPO_RE.match(repo):
        raise Invalid(f'repo "{repo}" must look like "owner/name"')
    if domain is not None and domain not in await domains.available(session):
        raise Invalid(f'domain "{domain}" is not configured; add it in Settings first')
    for field, value in (("name", name), ("repo", repo), ("subdomain", subdomain)):
        if await session.scalar(select(Project).where(getattr(Project, field) == value)):
            raise Conflict(f'a project with {field} "{value}" already exists')

    project = Project(
        name=name, repo=repo, branch=branch, subdomain=subdomain, domain=domain
    )
    session.add(project)
    await session.commit()
    return _project(project)


async def delete_project(session: AsyncSession, ref: str) -> None:
    project = await resolve_project(session, ref)
    # Best effort, matching the UI: a Cloudflare hiccup must not block a delete,
    # but leaving a dangling login gate behind is worth one attempt to avoid.
    if project.cf_app_id:
        try:
            token, account_id = await cloudflare.resolve_credentials(session)
            await cloudflare.Access(token, account_id).delete_app(project.cf_app_id)
        except Exception:
            pass
    await access_paths.forget(session, project.id)
    await session.delete(project)
    await session.commit()


async def set_access(
    session: AsyncSession, ref: str, protected: bool, emails: list[str]
) -> models.Project:
    """Turn the Cloudflare Access login on or off for this app's hostname.
    Cloudflare is reconciled first and the row saved only if that succeeds, so
    the console never records a gate that was not actually created."""
    project = await resolve_project(session, ref)
    cleaned = [e.strip() for e in emails if e.strip()]
    if protected:
        if not cleaned:
            raise Invalid("add at least one email, or the app would allow no one in")
        for email in cleaned:
            if not EMAIL_RE.match(email):
                raise Invalid(f'"{email}" is not a valid email')

    hostname = f"{project.subdomain}.{domains.of(project)}"
    try:
        token, account_id = await cloudflare.resolve_credentials(session)
        cf_app_id = await cloudflare.Access(token, account_id).reconcile(
            hostname, protected, cleaned, project.cf_app_id
        )
    except cloudflare.AccessNotConfigured as exc:
        raise Unavailable(str(exc))
    except cloudflare.AccessApiError as exc:
        raise Unavailable(str(exc))

    project.protected = protected
    project.access_emails = ",".join(cleaned) if protected else None
    project.cf_app_id = cf_app_id
    await session.commit()
    return _project(project)


async def set_domain(
    session: AsyncSession, ref: str, domain: str | None, repoint: str = "manual"
) -> models.DomainChange:
    """Move a project to a different base domain. Traefik only learns the new
    host label on the next deploy, so the result always says a redeploy is
    required."""
    project = await resolve_project(session, ref)
    if repoint not in ("auto", "manual"):
        raise Invalid('repoint must be "auto" or "manual"')
    if domain is not None and domain not in await domains.available(session):
        raise Invalid(f'domain "{domain}" is not configured; add it in Settings first')

    target = domain or config.DOMAIN
    health = await session.get(ProjectHealth, project.id)
    state = health.state if health else "unknown"
    if target == domains.of(project):
        return models.DomainChange(
            project=_project(project, state),
            redeploy_required=False,
            note="Already on this domain.",
        )

    note: str | None = None
    if project.protected:
        new_hostname = f"{project.subdomain}.{target}"
        if repoint == "auto":
            emails = project.access_emails.split(",") if project.access_emails else []
            try:
                token, account_id = await cloudflare.resolve_credentials(session)
                access = cloudflare.Access(token, account_id)
                new_id = await access.reconcile(new_hostname, True, emails, None)
            except cloudflare.AccessNotConfigured as exc:
                raise Unavailable(str(exc))
            except cloudflare.AccessApiError as exc:
                raise Unavailable(str(exc))
            if project.cf_app_id:  # remove the old gate, best effort
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
                "back on to recreate it for the new hostname."
            )

    # Bypass paths hang off the hostname too, and unlike the login gate they
    # exist whether or not the app is protected. A move that fails is reported,
    # never raised: it must not block the domain change.
    if repoint == "auto":
        moved, failed = await access_paths.move(
            session, project.id, f"{project.subdomain}.{target}"
        )
        if moved or failed:
            paths_note = f"Moved {moved} bypass path(s)."
            if failed:
                paths_note += (
                    f" {failed} could not be recreated: check them in Cloudflare, "
                    "or close and reopen them here."
                )
            note = f"{note} {paths_note}" if note else paths_note

    # Null for the primary, so the "null means primary" invariant holds.
    project.domain = None if target == config.DOMAIN else target
    await session.commit()
    return models.DomainChange(
        project=_project(project, state), redeploy_required=True, note=note
    )


# ------------------------------------------------------------ access paths


async def _access_scope(
    session: AsyncSession, ref: str | None
) -> tuple[str | None, str]:
    """The (project id, hostname) a bypass path belongs to. ref None means the
    console's own hostname, which is not a project."""
    if ref is None:
        return None, config.HOSTNAME
    project = await resolve_project(session, ref)
    return project.id, f"{project.subdomain}.{domains.of(project)}"


def _access_path(row: AccessPath) -> models.AccessPath:
    return models.AccessPath(
        id=row.id,
        project_id=row.project_id,
        hostname=row.hostname,
        path=row.path,
        url=f"https://{row.hostname}/{row.path}",
        created_at=row.created_at,
    )


async def list_access_paths(
    session: AsyncSession, ref: str | None = None
) -> models.AccessPathList:
    """Which paths on a hostname skip the Cloudflare Access login."""
    project_id, hostname = await _access_scope(session, ref)
    return models.AccessPathList(
        hostname=hostname,
        paths=[_access_path(row) for row in await access_paths.listing(session, project_id)],
    )


async def open_access_path(
    session: AsyncSession, ref: str | None, path: str
) -> models.AccessPath:
    """Let anyone reach one path without the login. Read what that costs in
    access_paths before calling it: the app's own authentication becomes the
    only thing in front of that path."""
    project_id, hostname = await _access_scope(session, ref)
    try:
        row = await access_paths.add(
            session, project_id=project_id, hostname=hostname, raw_path=path
        )
    except ValueError as exc:
        raise Invalid(str(exc))
    except cloudflare.AccessNotConfigured as exc:
        raise Unavailable(str(exc))
    except cloudflare.AccessApiError as exc:
        raise Upstream(str(exc))
    return _access_path(row)


async def close_access_path(
    session: AsyncSession, ref: str | None, path: str
) -> None:
    """Put the login back in front of one path. Addressed by the path itself,
    not an id, since that is what a caller has in hand."""
    project_id, hostname = await _access_scope(session, ref)
    # console_own=False here on purpose: this only tidies what was typed. The
    # refusals belong to opening a path, and applying them to a close would
    # answer "cannot be opened" to someone trying to shut something.
    try:
        wanted = access_paths.normalize(path, console_own=False)
    except ValueError as exc:
        raise Invalid(str(exc))
    for row in await access_paths.listing(session, project_id):
        if row.path == wanted:
            try:
                await access_paths.remove(session, row)
            except cloudflare.AccessNotConfigured as exc:
                raise Unavailable(str(exc))
            except cloudflare.AccessApiError as exc:
                raise Upstream(str(exc))
            return
    raise NotFound(f'no bypass path "{wanted}" on {hostname}')


# ------------------------------------------------------------- deployments


async def _deployment(
    session: AsyncSession, project: Project, deployment_id: str
) -> Deployment:
    deployment = await session.get(Deployment, deployment_id)
    if deployment is None or deployment.project_id != project.id:
        raise NotFound(f'no deployment "{deployment_id}" for this project')
    return deployment


async def list_deployments(session: AsyncSession, ref: str) -> list[models.Deployment]:
    project = await resolve_project(session, ref)
    rows = await session.scalars(
        select(Deployment)
        .where(Deployment.project_id == project.id)
        .order_by(Deployment.created_at.desc())
        .limit(DEPLOYMENT_LIMIT)
    )
    return [models.Deployment.model_validate(d) for d in rows]


async def get_deployment(
    session: AsyncSession, ref: str, deployment_id: str
) -> models.DeploymentDetail:
    project = await resolve_project(session, ref)
    return models.DeploymentDetail.model_validate(
        await _deployment(session, project, deployment_id)
    )


async def deploy_image(
    session: AsyncSession,
    ref: str,
    image: str,
    git_ref: str | None = None,
    console_toml: str | None = None,
) -> models.Accepted:
    """Deploy an image that already exists in the registry. Nothing is built
    here; the console pulls what a laptop, or an earlier build, already
    pushed. The console.toml is read from the repo unless one is pasted."""
    project = await resolve_project(session, ref)
    deployment = await manual.deploy_image(
        session, project, image, git_ref, console_toml
    )
    return models.Accepted(id=deployment.id, status="queued")


async def build_project(
    session: AsyncSession, ref: str, git_ref: str | None = None
) -> models.Accepted:
    """Build the repo at a ref on the box, push the image, and deploy it.
    Takes minutes; poll the deployment for progress."""
    project = await resolve_project(session, ref)
    deployment = await builder.request_build(session, project, git_ref)
    return models.Accepted(id=deployment.id, status="building")


async def rollback(
    session: AsyncSession, ref: str, deployment_id: str
) -> models.Accepted:
    project = await resolve_project(session, ref)
    target = await _deployment(session, project, deployment_id)
    problem = history.rollback_error(target)
    if problem is not None:
        if target.status == "live":
            raise Conflict(problem)
        raise Invalid(problem)

    deployment = history.clone(target, f"rollback to {plan.short_sha(target.sha)}")
    session.add(deployment)
    await deploy_engine.queue(session, deployment)
    return models.Accepted(id=deployment.id, status="queued")


async def redeploy(
    session: AsyncSession, ref: str, deployment_id: str
) -> models.Accepted:
    project = await resolve_project(session, ref)
    target = await _deployment(session, project, deployment_id)
    problem = history.redeploy_error(target)
    if problem is not None:
        if target.status in history.IN_FLIGHT:
            raise Conflict(problem)
        raise Invalid(problem)

    deployment = history.clone(target, f"redeploy of {plan.short_sha(target.sha)}")
    session.add(deployment)
    await deploy_engine.queue(session, deployment)
    return models.Accepted(id=deployment.id, status="queued")


# --------------------------------------------------------------- container


async def _project_container(session: AsyncSession, ref: str):
    """The project's container, preferring a running one. Stopped containers
    count: a crashed app's container is exactly what a caller diagnosing an
    outage needs to reach."""
    project = await resolve_project(session, ref)
    found = await docker_containers.list_project_containers(
        project.id, include_stopped=True
    )
    running = [c for c in found if c.status == "running"]
    return (running or found or [None])[0]


async def get_container(session: AsyncSession, ref: str) -> models.Container:
    container = await _project_container(session, ref)
    if container is None:
        return models.Container(state="absent")

    shaped = docker_containers.shape_container(container.attrs)
    result = models.Container(**shaped)
    if shaped.get("state") == "running":
        # Stats are only meaningful for a running container, and cost a second
        # daemon round trip, so a stopped app skips them.
        try:
            stats = await docker_containers.get_stats(container.id)
        except Exception:
            stats = {}
        for field, value in stats.items():
            setattr(result, field, value)
    return result


async def get_logs(
    session: AsyncSession, ref: str, tail: int = config.LOG_TAIL_DEFAULT
) -> models.Logs:
    container = await _project_container(session, ref)
    if container is None:
        raise Conflict("no container for this app; deploy it first")
    text = await docker_containers.get_logs(container.id, tail)
    return models.Logs(
        container=container.name, tail=min(max(tail, 1), config.LOG_TAIL_MAX), logs=text
    )


async def control(session: AsyncSession, ref: str, action: str) -> models.Container:
    if action not in CONTROL_ACTIONS:
        raise Invalid(f'unknown action "{action}"; use one of {", ".join(CONTROL_ACTIONS)}')
    project = await resolve_project(session, ref)
    container = await docker_containers.sole_project_container(project.id)  # Conflict
    try:
        await run(getattr(container, action))
        # Reload so the answer reflects the state the action produced.
        refreshed = await run(get_client().containers.get, container.id)
    except Exception as exc:
        raise Unavailable(f"{action} failed: {exc}")
    return models.Container(**docker_containers.shape_container(refreshed.attrs))


# ---------------------------------------------------------------- secrets


async def list_secret_keys(session: AsyncSession, ref: str) -> list[models.SecretKey]:
    """Which secrets a project has, never their values. Enough for a caller to
    tell "DATABASE_URL is missing" from "DATABASE_URL is wrong"."""
    project = await resolve_project(session, ref)
    rows = await session.scalars(
        select(Secret).where(Secret.project_id == project.id).order_by(Secret.key)
    )
    return [models.SecretKey(key=s.key, updated_at=s.updated_at) for s in rows]


# --------------------------------------------------------------- commands


async def list_commands(session: AsyncSession, ref: str) -> list[models.CommandRun]:
    project = await resolve_project(session, ref)
    rows = await session.scalars(
        select(CommandRun)
        .where(CommandRun.project_id == project.id)
        .order_by(CommandRun.created_at.desc())
        .limit(COMMAND_LIMIT)
    )
    return [models.CommandRun.model_validate(r) for r in rows]


async def get_command(
    session: AsyncSession, ref: str, run_id: str
) -> models.CommandRunDetail:
    project = await resolve_project(session, ref)
    cmd_run = await session.get(CommandRun, run_id)
    if cmd_run is None or cmd_run.project_id != project.id:
        raise NotFound(f'no command run "{run_id}" for this project')
    return models.CommandRunDetail.model_validate(cmd_run)


async def run_command(session: AsyncSession, ref: str, command: str) -> models.Accepted:
    """Exec a one-off command in the app's live container. Output is polled back
    through get_command, the same poll-not-stream shape as a deploy log."""
    project = await resolve_project(session, ref)
    command = command.strip()
    if not command:
        raise Invalid("command cannot be empty")
    # Fail fast if nothing is live; the runner re-checks, since the container
    # can stop between here and the exec.
    if await docker_containers.find_project_container(project.id) is None:
        raise Conflict("app is not running; deploy it first")

    cmd_run = CommandRun(project_id=project.id, command=command, status="running")
    session.add(cmd_run)
    await session.commit()
    runner.enqueue(cmd_run.id)
    return models.Accepted(id=cmd_run.id, status="running")


# ---------------------------------------------------------------- backups


async def get_backups(session: AsyncSession) -> models.Backups:
    state = await backup_engine.configured(session)
    rows = await session.scalars(
        select(BackupRun).order_by(BackupRun.created_at.desc()).limit(BACKUP_LIMIT)
    )
    return models.Backups(
        **state, runs=[models.BackupRun.model_validate(r) for r in rows]
    )


async def trigger_backup(session: AsyncSession) -> models.Accepted:
    state = await backup_engine.configured(session)
    if not state["ready"]:
        missing = []
        if not state["passphrase"]:
            missing.append("a passphrase")
        if not state["destination"]:
            missing.append("a destination repo + token")
        raise Invalid(f"backup not configured: set {' and '.join(missing)}")
    backup = BackupRun(trigger="manual", status="running")
    session.add(backup)
    await session.commit()
    backup_engine.enqueue(backup.id)
    return models.Accepted(id=backup.id, status="running")


# ----------------------------------------------------------------- system


async def get_system(session: AsyncSession, today: date | None = None) -> models.System:
    """One call describing the whole install. Built to be the first thing an
    agent asks for, so it can decide what to look at next without a dozen
    round trips."""
    projects = await list_projects(session)
    backups = await backup_engine.configured(session)
    last = await session.scalar(
        select(BackupRun).order_by(BackupRun.created_at.desc()).limit(1)
    )
    return models.System(
        domains=await domains.available(session),
        primary_domain=config.DOMAIN,
        projects=len(projects),
        live=sum(1 for p in projects if p.is_live),
        down=sum(1 for p in projects if p.health == "down"),
        deploying=sum(1 for p in projects if p.deploy_status in history.IN_FLIGHT),
        credentials=[
            models.Credential(**c) for c in await credentials.status(session, today)
        ],
        backups_ready=backups["ready"],
        last_backup_at=last.created_at if last else None,
        last_backup_status=last.status if last else None,
    )
