import asyncio
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from console import config
from console.db.models import Deployment, Project, naive_utc
from console.deploy import reaper
from console.deploy.reaper import reap_once
from console.schema.console_toml import parse_console_toml

NOW = datetime(2026, 7, 9, 12, 0, 0)

TOML = """
[app]
name = "demo"
subdomain = "app-demo"
port = 80

[health]
timeout = 60
"""


def snapshot():
    parsed, _ = parse_console_toml(TOML)
    return parsed.model_dump_json()


async def seed(db, **fields):
    async with db() as session:
        project = await session.scalar(select(Project).where(Project.name == "demo"))
        if project is None:
            project = Project(name="demo", repo="example-owner/demo", subdomain="demo")
            session.add(project)
            await session.flush()
        deployment = Deployment(
            project_id=project.id,
            sha="e5f6a7b0d1c2",
            **{"created_at": NOW, **fields},
        )
        session.add(deployment)
        await session.commit()
        return deployment.id


async def statuses(db, ids):
    async with db() as session:
        return [
            (await session.get(Deployment, deployment_id)).status
            for deployment_id in ids
        ]


async def run_reaper(db, now=NOW):
    async with db() as session:
        return await reap_once(session, now)


async def test_fresh_rows_are_left_alone(db):
    ids = [
        await seed(db, status="building"),
        await seed(db, status="queued", build_finished_at=NOW),
        await seed(db, status="deploying", deploy_started_at=NOW,
                   config_snapshot=snapshot()),
    ]
    assert await run_reaper(db) == 0
    assert await statuses(db, ids) == ["building", "queued", "deploying"]


async def test_stuck_building_is_reaped(db):
    deployment_id = await seed(
        db, status="building", created_at=NOW - timedelta(minutes=31)
    )
    assert await run_reaper(db) == 1
    async with db() as session:
        row = await session.get(Deployment, deployment_id)
    assert row.status == "failed"
    assert "no build-finished after 30 minutes" in row.failure_reason
    assert row.finished_at == NOW


async def test_stuck_queued_is_reaped(db):
    deployment_id = await seed(
        db, status="queued", build_finished_at=NOW - timedelta(minutes=61)
    )
    assert await run_reaper(db) == 1
    async with db() as session:
        row = await session.get(Deployment, deployment_id)
    assert "not picked up" in row.failure_reason


async def test_deploying_uses_health_timeout_plus_margin(db):
    # health.timeout 60 + margin 600: stuck at 700s, fine at 500s
    stuck = await seed(
        db,
        status="deploying",
        deploy_started_at=NOW - timedelta(seconds=700),
        config_snapshot=snapshot(),
    )
    fine = await seed(
        db,
        status="deploying",
        deploy_started_at=NOW - timedelta(seconds=500),
        config_snapshot=snapshot(),
    )
    assert await run_reaper(db) == 1
    assert await statuses(db, [stuck, fine]) == ["failed", "deploying"]


async def test_deploying_without_snapshot_falls_back_to_cap(db):
    # cap 300 + margin 600 = 900
    stuck = await seed(
        db, status="deploying", deploy_started_at=NOW - timedelta(seconds=901)
    )
    fine = await seed(
        db, status="deploying", deploy_started_at=NOW - timedelta(seconds=899)
    )
    assert await run_reaper(db) == 1
    assert await statuses(db, [stuck, fine]) == ["failed", "deploying"]


async def test_terminal_rows_are_never_touched(db):
    ids = [
        await seed(db, status="live", created_at=NOW - timedelta(days=30)),
        await seed(db, status="failed", created_at=NOW - timedelta(days=30)),
        await seed(db, status="superseded", created_at=NOW - timedelta(days=30)),
    ]
    assert await run_reaper(db) == 0
    assert await statuses(db, ids) == ["live", "failed", "superseded"]


async def test_aware_now_is_normalized(db):
    deployment_id = await seed(
        db, status="building", created_at=NOW - timedelta(minutes=31)
    )
    aware_now = NOW.replace(tzinfo=timezone.utc)
    assert await run_reaper(db, now=aware_now) == 1
    async with db() as session:
        assert (await session.get(Deployment, deployment_id)).status == "failed"



async def wait_until(predicate, timeout=2.0):
    """Poll until predicate() is true, so a tick failure fails fast."""
    for _ in range(int(timeout / 0.01)):
        if await predicate():
            return True
        await asyncio.sleep(0.01)
    return False


@asynccontextmanager
async def running_loop(db, monkeypatch):
    """reaper_loop against the test database, ticking as fast as it can."""
    monkeypatch.setattr(config, "REAPER_INTERVAL", 0.01)
    monkeypatch.setattr(reaper, "SessionLocal", db)
    task = asyncio.create_task(reaper.reaper_loop())
    try:
        yield task
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


async def test_loop_reaps_a_stuck_row_end_to_end(db, monkeypatch):
    # The whole tick body, its clock included. reap_once on its own can never
    # catch a broken reaper_loop, because these tests supply `now` themselves.
    deployment_id = await seed(
        db,
        status="building",
        created_at=naive_utc(datetime.now(timezone.utc)) - timedelta(minutes=31),
    )

    async with running_loop(db, monkeypatch) as task:

        async def reaped():
            if task.done():
                await task  # re-raise whatever killed the tick
            async with db() as session:
                row = await session.get(Deployment, deployment_id)
                return row.status == "failed"

        assert await wait_until(reaped), "reaper_loop never reaped the stuck row"


async def test_loop_retries_after_a_transient_database_error(db, monkeypatch):
    calls = []

    async def flaky(session, now):
        calls.append(now)
        if len(calls) == 1:
            raise OperationalError("select 1", {}, Exception("database is locked"))
        return 0

    async def ticked_twice():
        return len(calls) >= 2

    monkeypatch.setattr(reaper, "reap_once", flaky)
    async with running_loop(db, monkeypatch):
        assert await wait_until(ticked_twice), "a transient error stopped the loop"
    assert calls[0].tzinfo is timezone.utc


async def test_loop_stops_on_a_programming_error(db, monkeypatch):
    async def buggy(session, now):
        raise AttributeError("'Deployment' object has no attribute 'nope'")

    monkeypatch.setattr(reaper, "reap_once", buggy)
    monkeypatch.setattr(config, "REAPER_INTERVAL", 0.01)
    monkeypatch.setattr(reaper, "SessionLocal", db)
    with pytest.raises(AttributeError):
        await asyncio.wait_for(reaper.reaper_loop(), timeout=2)
