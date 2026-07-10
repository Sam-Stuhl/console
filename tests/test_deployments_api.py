from datetime import datetime, timedelta

import pytest

from console.db.models import Deployment, Project
from console.schema.console_toml import parse_console_toml

NOW = datetime(2026, 7, 9, 12, 0, 0)

TOML = """
[app]
name = "demo"
subdomain = "app-demo"
port = 80
"""


def snapshot():
    parsed, _ = parse_console_toml(TOML)
    return parsed.model_dump_json()


@pytest.fixture(autouse=True)
def enqueued(monkeypatch):
    calls = []
    monkeypatch.setattr("console.deploy.engine.enqueue", calls.append)
    return calls


async def seed_project(db, name="demo"):
    async with db() as session:
        project = Project(name=name, repo=f"sam-stuhl/{name}", subdomain=name)
        session.add(project)
        await session.commit()
        return project.id


async def seed_deployment(db, project_id, sha, minutes_ago=0, **fields):
    async with db() as session:
        deployment = Deployment(
            project_id=project_id,
            sha=sha,
            created_at=NOW - timedelta(minutes=minutes_ago),
            **{"status": "live", "image": f"ghcr.io/sam-stuhl/demo:{sha[:7]}",
               "config_snapshot": snapshot(), **fields},
        )
        session.add(deployment)
        await session.commit()
        return deployment.id


async def seed_served_superseded(db, project_id, sha, minutes_ago=10):
    return await seed_deployment(
        db,
        project_id,
        sha,
        minutes_ago=minutes_ago,
        status="superseded",
        deploy_started_at=NOW - timedelta(minutes=minutes_ago),
        finished_at=NOW - timedelta(minutes=minutes_ago - 1),
        log="deploying\nlive\n",
    )


async def test_list_is_newest_first_without_log(client, db):
    project_id = await seed_project(db)
    await seed_served_superseded(db, project_id, "aaaaaaa11111", minutes_ago=20)
    await seed_deployment(db, project_id, "bbbbbbb22222", minutes_ago=5)

    response = await client.get(f"/api/projects/{project_id}/deployments")
    assert response.status_code == 200
    rows = response.json()
    assert [r["sha"] for r in rows] == ["bbbbbbb22222", "aaaaaaa11111"]
    assert "log" not in rows[0]
    assert rows[0]["status"] == "live"


async def test_list_unknown_project_is_404(client):
    assert (await client.get("/api/projects/nope/deployments")).status_code == 404


async def test_detail_includes_log_and_snapshot(client, db):
    project_id = await seed_project(db)
    deployment_id = await seed_served_superseded(db, project_id, "aaaaaaa11111")

    response = await client.get(
        f"/api/projects/{project_id}/deployments/{deployment_id}"
    )
    assert response.status_code == 200
    body = response.json()
    assert body["log"] == "deploying\nlive\n"
    assert '"name":"demo"' in body["config_snapshot"]


async def test_detail_checks_project_ownership(client, db):
    project_id = await seed_project(db)
    other_id = await seed_project(db, name="other")
    deployment_id = await seed_deployment(db, project_id, "aaaaaaa11111")

    response = await client.get(
        f"/api/projects/{other_id}/deployments/{deployment_id}"
    )
    assert response.status_code == 404


async def test_rollback_clones_target_into_queued(client, db, enqueued):
    project_id = await seed_project(db)
    target_id = await seed_served_superseded(db, project_id, "aaaaaaa11111")
    await seed_deployment(db, project_id, "bbbbbbb22222")

    response = await client.post(
        f"/api/projects/{project_id}/deployments/{target_id}/rollback"
    )
    assert response.status_code == 202
    new_id = response.json()["deployment_id"]
    assert new_id != target_id
    assert enqueued == [new_id]

    async with db() as session:
        new = await session.get(Deployment, new_id)
        target = await session.get(Deployment, target_id)
    assert new.status == "queued"
    assert new.sha == "aaaaaaa11111"
    assert new.image == target.image
    assert new.config_snapshot == target.config_snapshot
    assert new.commit_message == "rollback to aaaaaaa"
    assert new.log is None  # fresh row, not a copy of the old log
    assert target.status == "superseded"  # untouched


async def test_rollback_supersedes_older_queued(client, db):
    project_id = await seed_project(db)
    target_id = await seed_served_superseded(db, project_id, "aaaaaaa11111")
    queued_id = await seed_deployment(db, project_id, "ccccccc33333", status="queued")

    await client.post(
        f"/api/projects/{project_id}/deployments/{target_id}/rollback"
    )
    async with db() as session:
        queued = await session.get(Deployment, queued_id)
    assert queued.status == "superseded"


async def test_rollback_to_live_is_409(client, db):
    project_id = await seed_project(db)
    live_id = await seed_deployment(db, project_id, "bbbbbbb22222")
    response = await client.post(
        f"/api/projects/{project_id}/deployments/{live_id}/rollback"
    )
    assert response.status_code == 409
    assert "already live" in response.json()["detail"]


async def test_rollback_requires_served_traffic(client, db, enqueued):
    project_id = await seed_project(db)
    never_ran = await seed_deployment(
        db, project_id, "ddddddd44444", status="superseded"
    )
    failed = await seed_deployment(
        db, project_id, "eeeeeee55555", status="failed",
        deploy_started_at=NOW,
    )
    for deployment_id in (never_ran, failed):
        response = await client.post(
            f"/api/projects/{project_id}/deployments/{deployment_id}/rollback"
        )
        assert response.status_code == 400
        assert "served traffic" in response.json()["detail"]
    assert enqueued == []
