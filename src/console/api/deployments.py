"""Deploy history and rollback. A rollback is an ordinary deploy of an
older build: it creates a fresh queued row cloning the target's sha,
image, and config snapshot, and the engine runs it through the same
pull / run-alongside / health-check / swap pipeline. History is
append-only; the target row is never mutated, and a bad rollback fails
without touching what is live.

Rollback targets are builds that actually served traffic (Sam's call):
the currently live row (rejected as pointless) or rows superseded after
going live. Rows superseded straight out of the queue never ran, which
shows as deploy_started_at being null."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from console.api.projects import get_project
from console.db.models import Deployment
from console.db.session import get_session
from console.deploy import engine as deploy_engine

router = APIRouter(prefix="/api/projects/{project_id}/deployments")

LIST_LIMIT = 50


class DeploymentOut(BaseModel):
    id: str
    sha: str
    commit_message: str | None
    image: str | None
    status: str
    substate: str | None
    run_url: str | None
    failure_reason: str | None
    created_at: datetime
    build_finished_at: datetime | None
    deploy_started_at: datetime | None
    finished_at: datetime | None

    model_config = {"from_attributes": True}


class DeploymentDetail(DeploymentOut):
    log: str | None
    config_snapshot: str | None
    container_name: str | None
    router_priority: int | None


async def _get_deployment(
    session: AsyncSession, project_id: str, deployment_id: str
) -> Deployment:
    deployment = await session.get(Deployment, deployment_id)
    if deployment is None or deployment.project_id != project_id:
        raise HTTPException(status_code=404, detail="no such deployment")
    return deployment


@router.get("")
async def list_deployments(
    project_id: str, session: AsyncSession = Depends(get_session)
) -> list[DeploymentOut]:
    await get_project(project_id, session)
    result = await session.scalars(
        select(Deployment)
        .where(Deployment.project_id == project_id)
        .order_by(Deployment.created_at.desc())
        .limit(LIST_LIMIT)
    )
    return [DeploymentOut.model_validate(d) for d in result]


@router.get("/{deployment_id}")
async def get_deployment(
    project_id: str,
    deployment_id: str,
    session: AsyncSession = Depends(get_session),
) -> DeploymentDetail:
    deployment = await _get_deployment(session, project_id, deployment_id)
    return DeploymentDetail.model_validate(deployment)


@router.post("/{deployment_id}/rollback", status_code=202)
async def rollback(
    project_id: str,
    deployment_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict:
    target = await _get_deployment(session, project_id, deployment_id)
    if target.status == "live":
        raise HTTPException(status_code=409, detail="this build is already live")
    served = target.status == "superseded" and target.deploy_started_at is not None
    if not served:
        raise HTTPException(
            status_code=400,
            detail="can only roll back to a build that served traffic",
        )
    if not target.image or not target.config_snapshot:
        raise HTTPException(
            status_code=400, detail="target has no image or config snapshot"
        )

    deployment = Deployment(
        project_id=project_id,
        sha=target.sha,
        commit_message=f"rollback to {target.sha[:7]}",
        image=target.image,
        config_snapshot=target.config_snapshot,
        run_url=target.run_url,
        status="queued",
    )
    session.add(deployment)
    await session.flush()
    await deploy_engine.supersede_older_queued(session, project_id, deployment.id)
    await session.commit()
    deploy_engine.enqueue(deployment.id)
    return {"deployment_id": deployment.id, "status": "queued"}


@router.post("/{deployment_id}/redeploy", status_code=202)
async def redeploy(
    project_id: str,
    deployment_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Re-run a build's own image + config as a fresh deployment. Unlike
    rollback, this works for a deployment that failed after its image was
    built (retry once the cause is fixed) or the live one (pick up changed
    secrets/env). Env is reassembled from current secrets at run time."""
    target = await _get_deployment(session, project_id, deployment_id)
    if target.status in ("queued", "building", "deploying"):
        raise HTTPException(status_code=409, detail="this deployment is still in progress")
    if not target.image or not target.config_snapshot:
        raise HTTPException(
            status_code=400, detail="this build produced no image to redeploy"
        )

    deployment = Deployment(
        project_id=project_id,
        sha=target.sha,
        commit_message=f"redeploy of {target.sha[:7]}",
        image=target.image,
        config_snapshot=target.config_snapshot,
        run_url=target.run_url,
        status="queued",
    )
    session.add(deployment)
    await session.flush()
    await deploy_engine.supersede_older_queued(session, project_id, deployment.id)
    await session.commit()
    deploy_engine.enqueue(deployment.id)
    return {"deployment_id": deployment.id, "status": "queued"}
