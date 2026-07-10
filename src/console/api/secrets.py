from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from console.api.projects import get_project
from console.db.models import Secret
from console.db.session import get_session
from console.schema.console_toml import SECRET_NAME_RE
from console.secrets import crypto
from console.secrets.dotenv import parse_dotenv, render_dotenv

router = APIRouter(prefix="/api/projects/{project_id}/secrets")


class SecretOut(BaseModel):
    key: str
    updated_at: datetime


class SecretValue(BaseModel):
    value: str


def _validate_key(key: str) -> None:
    if not SECRET_NAME_RE.match(key):
        raise HTTPException(
            status_code=400,
            detail=f'secret name "{key}" must match [A-Z_][A-Z0-9_]* (uppercase env-var style)',
        )


async def _get_secret(session: AsyncSession, project_id: str, key: str) -> Secret:
    secret = await session.scalar(
        select(Secret).where(Secret.project_id == project_id, Secret.key == key)
    )
    if secret is None:
        raise HTTPException(status_code=404, detail=f'no secret named "{key}"')
    return secret


@router.get("")
async def list_secrets(
    project_id: str, session: AsyncSession = Depends(get_session)
) -> list[SecretOut]:
    await get_project(project_id, session)
    result = await session.scalars(
        select(Secret).where(Secret.project_id == project_id).order_by(Secret.key)
    )
    # Names and timestamps only. Values require an explicit reveal.
    return [SecretOut(key=s.key, updated_at=s.updated_at) for s in result]


@router.put("/{key}", status_code=204)
async def put_secret(
    project_id: str,
    key: str,
    body: SecretValue,
    session: AsyncSession = Depends(get_session),
) -> None:
    _validate_key(key)
    await get_project(project_id, session)
    encrypted = crypto.encrypt(body.value)
    existing = await session.scalar(
        select(Secret).where(Secret.project_id == project_id, Secret.key == key)
    )
    if existing:
        existing.value_encrypted = encrypted
    else:
        session.add(Secret(project_id=project_id, key=key, value_encrypted=encrypted))
    await session.commit()


@router.post("/{key}/reveal")
async def reveal_secret(
    project_id: str,
    key: str,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> dict:
    secret = await _get_secret(session, project_id, key)
    response.headers["Cache-Control"] = "no-store"
    return {"key": key, "value": crypto.decrypt(secret.value_encrypted)}


@router.delete("/{key}", status_code=204)
async def delete_secret(
    project_id: str, key: str, session: AsyncSession = Depends(get_session)
) -> None:
    secret = await _get_secret(session, project_id, key)
    await session.delete(secret)
    await session.commit()


class DotenvText(BaseModel):
    text: str


@router.post("/import")
async def import_secrets(
    project_id: str,
    body: DotenvText,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Bulk import from .env contents. Existing keys are overwritten (that
    is what you want when rotating credentials); bad lines are skipped and
    reported, never fatal."""
    await get_project(project_id, session)
    pairs, skipped = parse_dotenv(body.text)

    existing = {
        s.key: s
        for s in await session.scalars(
            select(Secret).where(Secret.project_id == project_id)
        )
    }
    added, updated = [], []
    for key, value in pairs.items():
        encrypted = crypto.encrypt(value)
        if key in existing:
            existing[key].value_encrypted = encrypted
            updated.append(key)
        else:
            session.add(
                Secret(project_id=project_id, key=key, value_encrypted=encrypted)
            )
            added.append(key)
    await session.commit()
    return {"added": sorted(added), "updated": sorted(updated), "skipped": skipped}


@router.post("/export")
async def export_secrets(
    project_id: str,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Every secret for the project as .env text. Bulk reveal: same rules
    as the single reveal, never cached."""
    await get_project(project_id, session)
    rows = await session.scalars(
        select(Secret).where(Secret.project_id == project_id).order_by(Secret.key)
    )
    response.headers["Cache-Control"] = "no-store"
    return {
        "env": render_dotenv(
            {s.key: crypto.decrypt(s.value_encrypted) for s in rows}
        )
    }
