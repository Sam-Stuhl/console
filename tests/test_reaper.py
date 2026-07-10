from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from console.db.models import Deployment, Project
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
            project = Project(name="demo", repo="sam-stuhl/demo", subdomain="demo")
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
