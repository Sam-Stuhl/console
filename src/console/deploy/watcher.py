"""Build on push, by polling.

Every WATCH_INTERVAL seconds, ask GitHub for the branch head of each project
that has auto_build on, and start a build for a head not seen before. This is
what a push webhook would do, without one: nothing new listens on the
network, there is no shared secret to verify, and the GitHub connection the
console already holds is the only credential involved. The cost is up to one
interval of latency, which is less than an Actions job took to start.

Enabling auto_build records the head at that moment (see
builder.set_auto_build), so the first sweep after enabling builds nothing:
only what is pushed from then on."""

import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from console import config, github
from console.db.models import Project
from console.db.session import SessionLocal
from console.deploy import builder
from console.errors import Conflict

logger = logging.getLogger(__name__)


async def sweep(session: AsyncSession) -> None:
    projects = list(
        await session.scalars(select(Project).where(Project.auto_build.is_(True)))
    )
    if not projects:
        return
    try:
        token = await github.resolve_token(session)
    except github.GitHubNotConnected:
        # Every project needs the same connection; one line, not one per project.
        logger.warning("build on push is on but the console is not connected to GitHub")
        return
    client = github.GitHub(token)
    for project in projects:
        try:
            await _check(session, project, client)
        except Exception:
            logger.exception("build-on-push check failed for %s; skipping", project.name)
    await session.commit()


async def _check(session: AsyncSession, project: Project, client: github.GitHub) -> None:
    sha, message = await client.resolve_commit(project.repo, project.branch)
    if project.watched_sha is None:
        # No baseline (a project enabled before the head could be read):
        # take this head as seen rather than build whatever is there.
        project.watched_sha = sha
        return
    if sha == project.watched_sha:
        return
    project.watched_sha = sha
    try:
        deployment = await builder.start_build(session, project, sha, message)
    except Conflict as exc:
        # Someone pressed "build now" for the same head first. Fine.
        logger.info("%s: %s", project.name, exc)
        return
    logger.info("%s: building %s (%s)", project.name, sha[:7], deployment.id)


async def watch_loop() -> None:
    while True:
        await asyncio.sleep(config.WATCH_INTERVAL)
        try:
            async with SessionLocal() as session:
                await sweep(session)
        except Exception:
            logger.exception("build-on-push sweep failed; will retry next interval")
