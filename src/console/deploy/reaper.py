"""Times out stuck deployments so nothing sits in a non-terminal state
forever: a webhook that never arrives, a deploy task lost to a restart, a
health check hung mid-poll. Table-only; it never touches containers.

SQLite round-trips datetimes naive, while utcnow() is timezone-aware, so
every comparison here normalizes both sides through naive_utc first."""

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from console import config, events
from console.db.models import Deployment, Project, naive_utc
from console.db.session import SessionLocal
from console.schema.console_toml import ConsoleConfig

logger = logging.getLogger(__name__)


def _deploy_limit(deployment: Deployment) -> int:
    """health.timeout plus a margin for slow pulls."""
    try:
        cfg = ConsoleConfig.model_validate_json(deployment.config_snapshot)
        health_timeout = cfg.health.timeout
    except Exception:
        health_timeout = config.HEALTH_TIMEOUT_CAP
    return health_timeout + config.DEPLOY_TIMEOUT_MARGIN


def _age_limit(deployment: Deployment) -> tuple[datetime | None, int, str]:
    """(clock start, allowed seconds, failure reason) for a stuck row."""
    if deployment.status == "building":
        limit = config.BUILD_TIMEOUT
        return (
            deployment.created_at,
            limit,
            f"build timed out: no build-finished after {limit // 60} minutes",
        )
    if deployment.status == "queued":
        limit = config.QUEUED_TIMEOUT
        return (
            deployment.build_finished_at or deployment.created_at,
            limit,
            f"queued deployment was not picked up within {limit // 60} minutes",
        )
    limit = _deploy_limit(deployment)
    return (
        deployment.deploy_started_at or deployment.created_at,
        limit,
        f"deploy timed out after {limit} seconds",
    )


async def reap_once(session: AsyncSession, now: datetime) -> int:
    now = naive_utc(now)
    rows = await session.scalars(
        select(Deployment).where(
            Deployment.status.in_(("building", "queued", "deploying"))
        )
    )
    reaped = []
    for deployment in rows:
        started, limit, reason = _age_limit(deployment)
        if started is None:
            continue
        if (now - naive_utc(started)).total_seconds() > limit:
            deployment.status = "failed"
            deployment.substate = None
            deployment.failure_reason = reason
            deployment.finished_at = now
            deployment.log = (deployment.log or "") + f"failed: {reason}\n"
            reaped.append(deployment)
    if reaped:
        await session.commit()
    for deployment in reaped:
        project = await session.get(Project, deployment.project_id)
        await events.post(
            session,
            "console.deploy.failed",
            "a deployment was stuck past its time limit, so the reaper failed it",
            {
                "project": project.name if project else deployment.project_id,
                "deployment": deployment.id,
                "sha": deployment.sha,
                "stage": "reaped",
            },
            at=deployment.finished_at,
        )
    return len(reaped)


async def reaper_loop() -> None:
    while True:
        await asyncio.sleep(config.REAPER_INTERVAL)
        try:
            async with SessionLocal() as session:
                count = await reap_once(session, datetime.now(timezone.utc))
            if count:
                logger.warning("reaped %d stuck deployment(s)", count)
        except (AttributeError, ImportError, NameError, TypeError):
            # A bug in the tick body fails the same way every time, so retrying
            # only hides a reaper that is no longer reaping. Die loudly instead.
            logger.critical("reaper hit a bug and is stopping", exc_info=True)
            raise
        except Exception:
            # Anything else (a locked database, a dropped connection) is worth
            # another tick.
            logger.exception("reaper tick failed; will retry")
