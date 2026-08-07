"""Deploy history, and every way to start a deploy that is not a build
webhook: roll back to an older build, redeploy an existing one, or deploy an
image straight from GHCR.

All three produce the same thing, a fresh queued row that the engine runs
through the same pull / run-alongside / health-check / swap pipeline. History
is append-only; the target row is never mutated, and a bad deploy fails
without touching what is live.

Rollback targets are builds that actually served traffic (Sam's call):
the currently live row (rejected as pointless) or rows superseded after
going live. Rows superseded straight out of the queue never ran, which
shows as deploy_started_at being null.

Creating a deployment from an image is the path for a project whose CI has
never run or is broken: build and deploy are separate concerns, and an image
sitting in GHCR should be reachable without Actions being healthy. The
console.toml behind it is read from the repo, which stays its source of
truth; a pasted one is the fallback for when GitHub itself is unreachable."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from console.api.projects import get_project
from console.db.models import Deployment
from console.db.session import get_session
from console.deploy import engine as deploy_engine, history, manual
from console.errors import Invalid, Unavailable, Upstream

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


class DeploymentCreate(BaseModel):
    image: str  # full GHCR ref including a tag
    ref: str | None = None  # git ref to read console.toml from; defaults to the branch
    console_toml: str | None = None  # fallback for when GitHub cannot be reached


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


@router.post("", status_code=202)
async def create_deployment(
    project_id: str,
    body: DeploymentCreate,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Deploy an image that already exists in GHCR, with no build webhook
    involved. Nothing is built here: the console pulls what CI, or a laptop,
    already pushed."""
    project = await get_project(project_id, session)
    try:
        deployment = await manual.deploy_image(
            session, project, body.image, body.ref, body.console_toml
        )
    except Invalid as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Unavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Upstream as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return {"deployment_id": deployment.id, "status": "queued"}


@router.post("/{deployment_id}/rollback", status_code=202)
async def rollback(
    project_id: str,
    deployment_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict:
    target = await _get_deployment(session, project_id, deployment_id)
    problem = history.rollback_error(target)
    if problem is not None:
        # Already-live is a state conflict; everything else is a bad target.
        raise HTTPException(
            status_code=409 if target.status == "live" else 400, detail=problem
        )

    deployment = history.clone(target, f"rollback to {target.sha[:7]}")
    session.add(deployment)
    await deploy_engine.queue(session, deployment)
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
    problem = history.redeploy_error(target)
    if problem is not None:
        raise HTTPException(
            status_code=409 if target.status in history.IN_FLIGHT else 400,
            detail=problem,
        )

    deployment = history.clone(target, f"redeploy of {target.sha[:7]}")
    session.add(deployment)
    await deploy_engine.queue(session, deployment)
    return {"deployment_id": deployment.id, "status": "queued"}
