"""Reading the deployments table, and deriving a new deployment from an old one.

This lived inside the /api route bodies until the machine-facing /v1 surface
needed the same rules. Duplicating them would have been the wrong kind of
copy: whether a build may be rolled back is a fact about the deploy model, not
about who is asking, and two copies would eventually disagree.

History is append-only. Nothing here mutates a target row; a rollback or a
redeploy is a fresh queued row that the engine runs through the same pipeline,
so a bad deploy fails without touching what is live."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from console.db.models import Deployment

IN_FLIGHT = ("queued", "building", "deploying")


async def deploy_state(session: AsyncSession) -> tuple[dict[str, str], set[str]]:
    """Per project: the latest deployment's status, and which projects have a
    live one. One pass over the rows newest-first, so the first status seen for
    a project is its latest."""
    rows = await session.execute(
        select(Deployment.project_id, Deployment.status).order_by(
            Deployment.created_at.desc()
        )
    )
    latest: dict[str, str] = {}
    live: set[str] = set()
    for project_id, status in rows:
        latest.setdefault(project_id, status)
        if status == "live":
            live.add(project_id)
    return latest, live


def clone(target: Deployment, commit_message: str) -> Deployment:
    """A fresh queued row reusing a past build's image and config. The target is
    never mutated, so history stays append-only."""
    return Deployment(
        project_id=target.project_id,
        sha=target.sha,
        commit_message=commit_message,
        image=target.image,
        config_snapshot=target.config_snapshot,
        run_url=target.run_url,
        status="queued",
    )


def rollback_error(target: Deployment) -> str | None:
    """Why this build cannot be rolled back to, or None if it can.

    Only builds that actually served traffic are targets: the live row (rolling
    back to it is a no-op) and rows superseded out of the queue (which never
    ran, shown by a null deploy_started_at) are both refused."""
    if target.status == "live":
        return "this build is already live"
    served = target.status == "superseded" and target.deploy_started_at is not None
    if not served:
        return "can only roll back to a build that served traffic"
    if not target.image or not target.config_snapshot:
        return "target has no image or config snapshot"
    return None


def redeploy_error(target: Deployment) -> str | None:
    """Why this build cannot be redeployed, or None if it can.

    Looser than rollback: a build that failed after its image was built can be
    retried once the cause is fixed, and the live one can be redeployed to pick
    up changed secrets (env is reassembled from current secrets at run time)."""
    if target.status in IN_FLIGHT:
        return "this deployment is still in progress"
    if not target.image or not target.config_snapshot:
        return "this build produced no image to redeploy"
    return None
