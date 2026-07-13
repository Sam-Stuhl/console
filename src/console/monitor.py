"""Periodic liveness monitoring. Each sweep pings every deploy-live project's app
container on its health URL; a sustained failure flips the project to 'down' and
fires one ntfy alert, and recovery fires another. This is the only thing that
knows an app's real health after a deploy: deploy status says "we shipped it",
this says "it is still answering".

Like the deploy health check, the probe is container-to-container, so it only
resolves from inside the docker network. From host uvicorn every app reads down;
that is the dev limitation, not a bug."""

import asyncio
import logging

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from console import alerts, config, credentials
from console.db.models import Deployment, Project, ProjectHealth, utcnow
from console.db.session import SessionLocal
from console.deploy import plan
from console.docker.containers import find_project_container
from console.schema.console_toml import ConsoleConfig

logger = logging.getLogger(__name__)


async def _probe_once(url: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=config.MONITOR_TIMEOUT) as http:
            resp = await http.get(url)
    except httpx.HTTPError:
        return False
    return resp.status_code == 200


async def _check_project(session: AsyncSession, project: Project) -> str | None:
    """'up' / 'down', or None to skip a project that is not deployed."""
    live = await session.scalar(
        select(Deployment).where(
            Deployment.project_id == project.id, Deployment.status == "live"
        )
    )
    if live is None or not live.config_snapshot:
        return None
    container = await find_project_container(project.id)
    if container is None:
        return "down"  # deploy-live but the container is gone
    cfg = ConsoleConfig.model_validate_json(live.config_snapshot)
    url = plan.health_url(container.name, cfg.app.port, cfg.health.path)
    return "up" if await _probe_once(url) else "down"


async def _record(session: AsyncSession, project: Project, check_up: bool) -> None:
    now = utcnow()
    health = await session.get(ProjectHealth, project.id)
    if health is None:
        health = ProjectHealth(
            project_id=project.id, state="unknown", fail_streak=0, alerted=False
        )
        session.add(health)
    previous = health.state

    if check_up:
        if health.alerted:
            await alerts.send(
                session,
                f"{project.name} recovered",
                f"{project.name} is answering its health check again.",
                tags=["white_check_mark"],
            )
        health.state = "up"
        health.fail_streak = 0
        health.alerted = False
    else:
        health.fail_streak += 1
        if health.fail_streak >= config.MONITOR_FAIL_THRESHOLD:
            health.state = "down"
            if not health.alerted:
                await alerts.send(
                    session,
                    f"{project.name} is down",
                    f"{project.name} stopped answering its health check.",
                    tags=["rotating_light"],
                    priority="high",
                )
                health.alerted = True

    health.checked_at = now
    if health.state != previous:
        health.changed_at = now


async def check_once(session: AsyncSession) -> None:
    projects = await session.scalars(select(Project))
    for project in projects:
        try:
            result = await _check_project(session, project)
            if result is not None:
                await _record(session, project, result == "up")
        except Exception:
            logger.exception("health check failed for %s; skipping", project.name)
    await session.commit()


async def monitor_loop() -> None:
    while True:
        await asyncio.sleep(config.MONITOR_INTERVAL)
        try:
            async with SessionLocal() as session:
                await check_once(session)
                # Credential-expiry warnings dedupe by date, so running them on
                # the same tick is cheap and never spams.
                await credentials.check_expiries(session)
        except Exception:
            logger.exception("monitor sweep failed; will retry next interval")
