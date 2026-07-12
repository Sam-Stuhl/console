"""Backup status, history, and manual trigger. Configuring the destination
(a repo-scoped PAT and a private repo) goes through the settings API; the
passphrase is a mounted secret, not settable over HTTP, so this only reports
whether it is present."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from console.backup import engine as backup_engine
from console.db.models import BackupRun
from console.db.session import get_session

router = APIRouter(prefix="/api/backups")

LIST_LIMIT = 20


class BackupRunOut(BaseModel):
    id: str
    trigger: str
    status: str
    location: str | None
    size_bytes: int | None
    failure_reason: str | None
    created_at: datetime
    finished_at: datetime | None

    model_config = {"from_attributes": True}


class BackupStatus(BaseModel):
    passphrase: bool
    destination: bool
    ready: bool
    runs: list[BackupRunOut]


@router.get("")
async def get_status(session: AsyncSession = Depends(get_session)) -> BackupStatus:
    state = await backup_engine.configured(session)
    result = await session.scalars(
        select(BackupRun).order_by(BackupRun.created_at.desc()).limit(LIST_LIMIT)
    )
    return BackupStatus(**state, runs=[BackupRunOut.model_validate(r) for r in result])


@router.post("", status_code=202)
async def trigger_backup(session: AsyncSession = Depends(get_session)) -> dict:
    state = await backup_engine.configured(session)
    if not state["ready"]:
        missing = []
        if not state["passphrase"]:
            missing.append("a passphrase file")
        if not state["destination"]:
            missing.append("a destination repo + token")
        raise HTTPException(
            status_code=400, detail=f"backup not configured: set {' and '.join(missing)}"
        )
    backup = BackupRun(trigger="manual", status="running")
    session.add(backup)
    await session.commit()
    backup_engine.enqueue(backup.id)
    return {"run_id": backup.id, "status": "running"}
