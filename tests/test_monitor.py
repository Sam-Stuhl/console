"""Monitor transitions: a project goes 'down' only after the failure threshold
and alerts once, recovery alerts once, and the sweep skips undeployed projects."""

import pytest
from sqlalchemy import select

from console import config, monitor
from console.db.models import Project, ProjectHealth


@pytest.fixture
def sent(monkeypatch):
    records: list[dict] = []

    async def fake_send(session, title, message, **kwargs):
        records.append({"title": title, **kwargs})
        return True

    monkeypatch.setattr(monitor.alerts, "send", fake_send)
    return records


async def make_project(db):
    async with db() as session:
        project = Project(name="demo", repo="example-owner/demo", subdomain="app-demo")
        session.add(project)
        await session.commit()
        return project


async def test_down_after_threshold_alerts_once_then_recovers(db, sent, monkeypatch):
    monkeypatch.setattr(config, "MONITOR_FAIL_THRESHOLD", 2)
    project = await make_project(db)

    async with db() as session:
        project = await session.get(Project, project.id)

        await monitor._record(session, project, False)  # fail 1: below threshold
        health = await session.get(ProjectHealth, project.id)
        assert health.fail_streak == 1
        assert health.state != "down"
        assert sent == []

        await monitor._record(session, project, False)  # fail 2: down + alert
        health = await session.get(ProjectHealth, project.id)
        assert health.state == "down"
        assert health.alerted is True
        assert len(sent) == 1
        assert sent[0]["priority"] == "high"

        await monitor._record(session, project, False)  # still down, no new alert
        assert len(sent) == 1

        await monitor._record(session, project, True)  # recovery
        health = await session.get(ProjectHealth, project.id)
        assert health.state == "up"
        assert health.alerted is False
        assert health.fail_streak == 0
        assert len(sent) == 2


async def test_transient_up_does_not_alert(db, sent):
    project = await make_project(db)
    async with db() as session:
        project = await session.get(Project, project.id)
        await monitor._record(session, project, True)
        health = await session.get(ProjectHealth, project.id)
    assert health.state == "up"
    assert sent == []


async def test_check_once_skips_undeployed(db, sent, monkeypatch):
    # No live deployment -> _check_project returns None -> nothing recorded.
    await make_project(db)
    async with db() as session:
        await monitor.check_once(session)
    async with db() as session:
        rows = list(await session.scalars(select(ProjectHealth)))
    assert rows == []
    assert sent == []
