"""Webhook endpoints called by GitHub Actions. Machine endpoints, so they
live under /hooks rather than /api. Authentication is the OIDC bearer
token alone; the payload's repo must match the token's repository claim so
a token minted for one repo cannot act on another.

Response codes are for the workflow's curl step: 4xx means the request
itself was bad. A deployment that fails console-side (bad console.toml,
missing image) still answers 200; the console UI is where that failure
surfaces."""

import asyncio

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from console import oidc
from console.db.models import Deployment, Project, utcnow
from console.db.session import get_session
from console.deploy import engine as deploy_engine
from console.schema.console_toml import ConfigError, parse_console_toml

router = APIRouter(prefix="/hooks")

NON_TERMINAL = ("building", "queued", "deploying")


class BuildStarted(BaseModel):
    repo: str
    sha: str
    commit_message: str | None = None
    run_url: str | None = None


class BuildFinished(BaseModel):
    repo: str
    sha: str
    conclusion: str
    image: str | None = None
    commit_message: str | None = None
    run_url: str | None = None
    console_toml: str | None = None


async def verified_claims(authorization: str = Header(default="")) -> dict:
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="missing bearer token")
    try:
        # verify() hits the network on a JWKS cache miss; keep it off the loop
        return await asyncio.to_thread(oidc.verify, token)
    except oidc.WrongOwner as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except oidc.OidcError as exc:
        raise HTTPException(status_code=401, detail=str(exc))


async def resolve_project(
    repo: str, claims: dict, session: AsyncSession
) -> tuple[Project, str | None]:
    """Returns (project, ignore_reason). A non-None reason means the event
    is for a branch the project does not track: answer 200 and do nothing,
    so workflows on side branches never go red."""
    token_repo = claims.get("repository")
    if repo != token_repo:
        raise HTTPException(
            status_code=403,
            detail=f'token was issued for "{token_repo}", payload says "{repo}"',
        )
    project = await session.scalar(select(Project).where(Project.repo == repo))
    if project is None:
        raise HTTPException(status_code=404, detail=f'no project for repo "{repo}"')
    ref = claims.get("ref")
    if ref != f"refs/heads/{project.branch}":
        return project, f'ref "{ref}" is not the tracked branch "{project.branch}"'
    return project, None


async def find_open_deployment(
    session: AsyncSession, project_id: str, sha: str
) -> Deployment | None:
    return await session.scalar(
        select(Deployment)
        .where(
            Deployment.project_id == project_id,
            Deployment.sha == sha,
            Deployment.status.in_(NON_TERMINAL),
        )
        .order_by(Deployment.created_at.desc())
    )


@router.post("/build-started")
async def build_started(
    body: BuildStarted,
    response: Response,
    claims: dict = Depends(verified_claims),
    session: AsyncSession = Depends(get_session),
) -> dict:
    project, ignore = await resolve_project(body.repo, claims, session)
    if ignore:
        return {"ignored": ignore}

    # Duplicate deliveries and workflow retries must be harmless
    existing = await find_open_deployment(session, project.id, body.sha)
    if existing is not None:
        existing.run_url = body.run_url or existing.run_url
        await session.commit()
        return {"deployment_id": existing.id, "status": existing.status}

    deployment = Deployment(
        project_id=project.id,
        sha=body.sha,
        commit_message=body.commit_message,
        run_url=body.run_url,
        status="building",
    )
    session.add(deployment)
    await session.commit()
    response.status_code = 201
    return {"deployment_id": deployment.id, "status": "building"}


@router.post("/build-finished")
async def build_finished(
    body: BuildFinished,
    response: Response,
    claims: dict = Depends(verified_claims),
    session: AsyncSession = Depends(get_session),
) -> dict:
    project, ignore = await resolve_project(body.repo, claims, session)
    if ignore:
        return {"ignored": ignore}

    deployment = await find_open_deployment(session, project.id, body.sha)
    if deployment is None:
        # build-started was missed; start the record here
        deployment = Deployment(project_id=project.id, sha=body.sha, status="building")
        session.add(deployment)
    if deployment.status != "building":
        # Duplicate delivery after the deploy already kicked off
        return {"deployment_id": deployment.id, "status": deployment.status}

    deployment.build_finished_at = utcnow()
    deployment.run_url = body.run_url or deployment.run_url
    deployment.commit_message = body.commit_message or deployment.commit_message

    failure = None
    parsed = None
    if body.conclusion != "success":
        failure = f"build failed (conclusion: {body.conclusion})"
    elif not body.image or not body.image.startswith("ghcr.io/sam-stuhl/"):
        failure = "image ref missing or not under ghcr.io/sam-stuhl/"
    elif not body.console_toml:
        failure = "build succeeded but no console.toml was sent"
    else:
        try:
            parsed, warnings = parse_console_toml(body.console_toml)
        except ConfigError as exc:
            failure = str(exc)

    if failure is not None:
        deployment.status = "failed"
        deployment.failure_reason = failure
        deployment.finished_at = utcnow()
        await session.commit()
        return {"deployment_id": deployment.id, "status": "failed"}

    deployment.image = body.image
    deployment.config_snapshot = parsed.model_dump_json()
    deployment.status = "queued"
    for warning in warnings:
        deployment.log = (deployment.log or "") + f"warning: {warning}\n"

    # Flush so a row created in this request has its id assigned; the
    # sweep below must be able to exclude it.
    await session.flush()

    # Exactly one queued deployment per project: newer wins
    stale = await session.scalars(
        select(Deployment).where(
            Deployment.project_id == project.id,
            Deployment.status == "queued",
            Deployment.id != deployment.id,
        )
    )
    for old in stale:
        old.status = "superseded"
        old.finished_at = utcnow()

    await session.commit()
    deploy_engine.enqueue(deployment.id)
    response.status_code = 202
    return {"deployment_id": deployment.id, "status": "queued"}
