"""Backup API: status shape + history, and the trigger's configured guard."""

import pytest

from console.backup import engine as backup_engine
from console.db.models import BackupRun


@pytest.fixture
def enqueued(monkeypatch):
    ids: list[str] = []
    monkeypatch.setattr(backup_engine, "enqueue", ids.append)
    return ids


async def test_status_shape_and_history(client, db):
    async with db() as session:
        session.add(
            BackupRun(
                trigger="manual", status="succeeded", size_bytes=1024, location="fake:x"
            )
        )
        await session.commit()

    res = await client.get("/api/backups")
    assert res.status_code == 200
    body = res.json()
    assert set(body) == {"passphrase", "destination", "ready", "runs"}
    # Nothing is configured in a bare test env.
    assert body["ready"] is False
    assert len(body["runs"]) == 1
    assert body["runs"][0]["status"] == "succeeded"


async def test_trigger_rejected_when_not_configured(client, enqueued):
    res = await client.post("/api/backups")
    assert res.status_code == 400
    assert "not configured" in res.json()["detail"]
    assert enqueued == []


async def test_trigger_creates_row_when_configured(client, db, enqueued, monkeypatch):
    async def ready(_session):
        return {"passphrase": True, "destination": True, "ready": True}

    monkeypatch.setattr(backup_engine, "configured", ready)

    res = await client.post("/api/backups")
    assert res.status_code == 202
    run_id = res.json()["run_id"]
    assert enqueued == [run_id]

    async with db() as session:
        row = await session.get(BackupRun, run_id)
    assert row.status == "running"
    assert row.trigger == "manual"
