"""Deploy orchestration, executing docs/manual-deploy.md. The safety
invariant lives here: the currently live container is never stopped until
its replacement has answered a health check with 200. Old containers are
touched in exactly two places: listed in _find_live (read only) and
stopped in the swap, which runs only after the 200 and the swap guard.

Deploys for one project are serialized by a per-project asyncio.Lock;
deploys for different projects run concurrently. A queued row that gets
superseded or reaped while waiting for the lock is dropped by the claim
check. Tasks do not survive a restart: lifespan re-enqueues queued rows,
and rows orphaned mid-deploy are the reaper's job."""

import asyncio
import logging
import time

import docker.errors
import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from console import alerts, appicon, config, domains, settings_store
from console.db.models import Deployment, Project, Secret, utcnow
from console.db.session import SessionLocal
from console.deploy import plan
from console.docker.client import get_client, run
from console.schema.console_toml import ConsoleConfig
from console.secrets.crypto import KeyNotConfigured, decrypt

logger = logging.getLogger(__name__)

_locks: dict[str, asyncio.Lock] = {}
_tasks: set[asyncio.Task] = set()


class DeployFailed(Exception):
    """Mark the deployment failed with this message and stop."""


def enqueue(deployment_id: str) -> None:
    """Spawn a deploy task for a queued deployment."""
    task = asyncio.create_task(run_deploy(deployment_id))
    _tasks.add(task)  # strong ref; asyncio only keeps weak ones
    task.add_done_callback(_tasks.discard)


async def queue(session: AsyncSession, deployment: Deployment) -> None:
    """Hand a newly-added deployment row to the engine.

    Every way to start a deploy ends here: a build webhook, a rollback, a
    redeploy, and the same three driven over /v1. The caller has added the row
    and set its fields; this flushes so it has an id, supersedes any older
    queued row, commits, and spawns the deploy task."""
    await session.flush()
    await supersede_older_queued(session, deployment.project_id, deployment.id)
    await session.commit()
    enqueue(deployment.id)


async def supersede_older_queued(
    session: AsyncSession, project_id: str, keep_id: str
) -> None:
    """Exactly one queued deployment per project: newest wins. The caller
    must have flushed, so keep_id is a real row id."""
    stale = await session.scalars(
        select(Deployment).where(
            Deployment.project_id == project_id,
            Deployment.status == "queued",
            Deployment.id != keep_id,
        )
    )
    for old in stale:
        old.status = "superseded"
        old.finished_at = utcnow()


async def run_deploy(deployment_id: str) -> None:
    async with SessionLocal() as session:
        deployment = await session.get(Deployment, deployment_id)
        if deployment is None:
            return
        lock = _locks.setdefault(deployment.project_id, asyncio.Lock())
        async with lock:
            # Claim check: superseded or reaped while waiting for the lock
            await session.refresh(deployment)
            if deployment.status != "queued":
                return
            project = await session.get(Project, deployment.project_id)
            if project is None:
                return
            try:
                await _deploy(session, deployment, project)
            except DeployFailed as exc:
                await _fail(session, deployment, str(exc))
            except Exception as exc:  # tasks must never die silently
                await _fail(session, deployment, f"internal error: {exc!r}")
            else:
                await _refresh_icon(session, project)


async def _refresh_icon(session: AsyncSession, project: Project) -> None:
    """Best effort after a live deploy: pull the app's favicon from its now
    running container. Never lets an icon hiccup affect the deploy result."""
    try:
        if await appicon.fetch_and_store(session, project):
            await session.commit()
    except Exception:
        logger.warning("icon refresh failed for %s", project.name, exc_info=True)


async def _deploy(
    session: AsyncSession, deployment: Deployment, project: Project
) -> None:
    cfg = ConsoleConfig.model_validate_json(deployment.config_snapshot)
    deployment.status = "deploying"
    deployment.substate = "pulling"
    deployment.deploy_started_at = utcnow()
    await _log(session, deployment, f"deploying {deployment.image}")

    # Fail before touching Docker when the env cannot be assembled
    env = await _assemble_env(session, cfg, project.id)

    client = get_client()
    live = await _find_live(client, project.id)
    priority = _next_priority(live)
    name = await _pick_name(client, deployment, cfg, live)

    deployment.container_name = name
    deployment.router_priority = priority
    await session.commit()

    # Runbook step 1: pull. Private GHCR images need the read token from
    # Settings; public images pull with auth=None.
    await _log(session, deployment, f"pulling {deployment.image}")
    ghcr_token = await settings_store.get(session, settings_store.GHCR_TOKEN)
    auth = {"username": config.OIDC_OWNER, "password": ghcr_token} if ghcr_token else None
    try:
        await run(client.images.pull, deployment.image, auth_config=auth)
    except docker.errors.DockerException as exc:
        if "unauthorized" in str(exc).lower() and not ghcr_token:
            raise DeployFailed(
                "image pull failed: unauthorized. This looks like a private "
                "image. Add a GitHub read:packages token in Settings, then redeploy."
            )
        raise DeployFailed(f"image pull failed: {exc}")

    # Runbook step 2: run alongside; the old container keeps serving
    deployment.substate = "starting"
    await _log(session, deployment, f"starting {name} (router priority {priority})")
    labels = plan.build_labels(
        name=name,
        subdomain=cfg.app.subdomain,
        domain=domains.of(project),
        port=cfg.app.port,
        priority=priority,
        project_id=project.id,
        deployment_id=deployment.id,
        sha=deployment.sha,
    )
    try:
        new = await run(
            client.containers.run,
            deployment.image,
            detach=True,
            name=name,
            network="web",
            environment=env,
            mem_limit=cfg.resources.memory,
            nano_cpus=int(cfg.resources.cpus * 1_000_000_000),
            restart_policy={"Name": "unless-stopped"},
            labels=labels,
        )
    except docker.errors.DockerException as exc:
        raise DeployFailed(f"container start failed: {exc}")

    # Runbook step 3: health-check container-to-container, never through
    # Traefik. On timeout the new container goes away; the old one was
    # never touched.
    deployment.substate = "health_check"
    url = plan.health_url(name, cfg.app.port, cfg.health.path)
    await _log(session, deployment, f"health check {url}, timeout {cfg.health.timeout}s")
    healthy, last = await _probe(url, cfg.health.timeout)
    if not healthy:
        raise DeployFailed(
            f"health check did not return 200 within {cfg.health.timeout}s "
            f"(last attempt: {last})"
        )
    await _log(session, deployment, f"healthy ({last})")

    # Swap guard: the reaper may have failed this row during a long health
    # check. Last barrier before the invariant-sensitive step.
    await session.refresh(deployment)
    if deployment.status != "deploying":
        await run(new.remove, force=True)
        return

    # Runbook step 4: swap, only after the 200
    for old in live:
        try:
            await run(old.stop)
            await run(old.remove)
        except docker.errors.NotFound:
            pass
        await _log(session, deployment, f"removed {old.name}")

    previous = await session.scalar(
        select(Deployment).where(
            Deployment.project_id == project.id,
            Deployment.status == "live",
            Deployment.id != deployment.id,
        )
    )
    if previous is not None:
        previous.status = "superseded"
        previous.finished_at = utcnow()
    deployment.status = "live"
    deployment.substate = None
    deployment.finished_at = utcnow()
    await _log(session, deployment, "live")


async def _assemble_env(
    session: AsyncSession, cfg: ConsoleConfig, project_id: str
) -> dict[str, str]:
    decrypted: dict[str, str] = {}
    if cfg.secrets:
        rows = await session.scalars(
            select(Secret).where(Secret.project_id == project_id)
        )
        stored = {row.key: row.value_encrypted for row in rows}
        for key in cfg.secrets:
            if key not in stored:
                raise DeployFailed(f'secret "{key}" is not set for this project')
            try:
                decrypted[key] = decrypt(stored[key])
            except KeyNotConfigured as exc:
                raise DeployFailed(str(exc))
    try:
        return plan.build_env(cfg, decrypted)
    except ValueError as exc:
        raise DeployFailed(str(exc))


async def _find_live(client, project_id: str) -> list:
    """Running containers the engine created for this project."""
    return await run(
        client.containers.list,
        filters={"label": f"console.project={project_id}"},
    )


def _next_priority(live: list) -> int:
    """Strictly below every live router so the old ones keep winning."""
    priorities = [plan.extract_router_priority(c.labels) for c in live]
    known = [p for p in priorities if p is not None]
    return plan.compute_priority(min(known) if known else None)


async def _pick_name(client, deployment: Deployment, cfg, live: list) -> str:
    name = plan.container_name(cfg.app.name, deployment.sha)
    if name in {c.name for c in live}:
        # Redeploying the sha that is already live: the old container
        # keeps its name until the swap removes it
        return f"{name}-{deployment.id[:6]}"
    try:
        leftover = await run(client.containers.get, name)
    except docker.errors.NotFound:
        return name
    # Leftover from an earlier failed deploy; it is not live (not in the
    # running list), so removing it touches nothing that serves traffic
    await run(leftover.remove, force=True)
    return name


async def _probe(url: str, timeout: int) -> tuple[bool, str]:
    """Poll until 200 or deadline. Always makes at least one attempt."""
    deadline = time.monotonic() + timeout
    last = "no attempt made"
    async with httpx.AsyncClient(timeout=5) as http:
        while True:
            try:
                response = await http.get(url)
                last = f"HTTP {response.status_code}"
                if response.status_code == 200:
                    return True, last
            except httpx.HTTPError as exc:
                last = f"{type(exc).__name__}: {exc}"
            if time.monotonic() >= deadline:
                return False, last
            await asyncio.sleep(config.HEALTH_POLL_INTERVAL)


async def _fail(session: AsyncSession, deployment: Deployment, reason: str) -> None:
    # Best-effort removal of the container this deployment created. The
    # ownership label check means an old container can never match.
    if deployment.container_name:
        try:
            container = await run(get_client().containers.get, deployment.container_name)
            if container.labels.get("console.deployment") == deployment.id:
                await run(container.remove, force=True)
        except Exception:
            pass
    deployment.status = "failed"
    deployment.substate = None
    deployment.failure_reason = reason
    deployment.finished_at = utcnow()
    deployment.log = (deployment.log or "") + f"failed: {reason}\n"
    await session.commit()

    # Best-effort deploy-failure alert; never let it break the fail path.
    try:
        project = await session.get(Project, deployment.project_id)
        name = project.name if project else deployment.project_id
        await alerts.send(
            session,
            f"{name} deploy failed",
            f"{deployment.sha[:7]}: {reason}",
            tags=["rotating_light"],
            priority="high",
        )
    except Exception:
        logger.warning("deploy-failure alert failed", exc_info=True)


async def _log(session: AsyncSession, deployment: Deployment, line: str) -> None:
    """Append a log line and commit, so the polling UI sees progress."""
    deployment.log = (deployment.log or "") + line + "\n"
    await session.commit()
