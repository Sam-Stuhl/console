import base64
from datetime import datetime, timedelta

import pytest

from conftest import FakeResponse
from console import config, settings_store
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
        project = Project(name=name, repo=f"example-owner/{name}", subdomain=name)
        session.add(project)
        await session.commit()
        return project.id


async def seed_deployment(db, project_id, sha, minutes_ago=0, **fields):
    async with db() as session:
        deployment = Deployment(
            project_id=project_id,
            sha=sha,
            created_at=NOW - timedelta(minutes=minutes_ago),
            **{"status": "live", "image": f"ghcr.io/example-owner/demo:{sha[:7]}",
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


async def test_redeploy_clones_failed_into_queued(client, db, enqueued):
    project_id = await seed_project(db)
    failed_id = await seed_deployment(
        db, project_id, "eeeeeee55555", status="failed", deploy_started_at=NOW
    )
    response = await client.post(
        f"/api/projects/{project_id}/deployments/{failed_id}/redeploy"
    )
    assert response.status_code == 202
    new_id = response.json()["deployment_id"]
    assert enqueued == [new_id]

    async with db() as session:
        new = await session.get(Deployment, new_id)
        failed = await session.get(Deployment, failed_id)
    assert new.status == "queued"
    assert new.image == failed.image
    assert new.config_snapshot == failed.config_snapshot
    assert new.commit_message == "redeploy of eeeeeee"
    assert failed.status == "failed"  # untouched


async def test_redeploy_live_is_allowed(client, db, enqueued):
    project_id = await seed_project(db)
    live_id = await seed_deployment(db, project_id, "bbbbbbb22222")  # default status live
    response = await client.post(
        f"/api/projects/{project_id}/deployments/{live_id}/redeploy"
    )
    assert response.status_code == 202


async def test_redeploy_without_image_is_400(client, db, enqueued):
    project_id = await seed_project(db)
    no_image = await seed_deployment(
        db, project_id, "fffffff66666", status="failed", image=None, config_snapshot=None
    )
    response = await client.post(
        f"/api/projects/{project_id}/deployments/{no_image}/redeploy"
    )
    assert response.status_code == 400
    assert enqueued == []


async def test_redeploy_in_progress_is_409(client, db, enqueued):
    project_id = await seed_project(db)
    building = await seed_deployment(db, project_id, "999999900000", status="building")
    response = await client.post(
        f"/api/projects/{project_id}/deployments/{building}/redeploy"
    )
    assert response.status_code == 409
    assert enqueued == []


# --- deploying an image that was built somewhere else -----------------------
#
# The gap this closes: a project whose CI has never run, or is broken, had no
# way in at all, because only a finished CI build ever created a row.


@pytest.fixture
def owner(monkeypatch):
    monkeypatch.setattr(config, "OIDC_OWNER", "example-owner")


async def connect_github(db):
    async with db() as session:
        await settings_store.set_value(session, settings_store.GITHUB_TOKEN, "gho_abc")
        await session.commit()


def toml_response(text=TOML):
    return FakeResponse(
        {
            "type": "file",
            "encoding": "base64",
            "size": len(text),
            "content": base64.encodebytes(text.encode()).decode(),
        }
    )


async def test_deploy_an_image_reads_console_toml_from_the_repo(
    client, db, owner, github_http, enqueued
):
    project_id = await seed_project(db)
    await connect_github(db)
    github_http.routes["/contents/console.toml"] = toml_response()

    response = await client.post(
        f"/api/projects/{project_id}/deployments",
        json={"image": "ghcr.io/example-owner/demo:abc1234"},
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "queued"
    assert enqueued == [body["deployment_id"]]
    async with db() as session:
        deployment = await session.get(Deployment, body["deployment_id"])
    assert deployment.image == "ghcr.io/example-owner/demo:abc1234"
    assert deployment.sha == "abc1234"  # the tag identifies the build
    assert deployment.commit_message == "manual deploy of abc1234"
    assert deployment.config_snapshot == snapshot()
    # Read from the project's tracked branch unless told otherwise
    _, url, kwargs = github_http.requests[0]
    assert url.endswith("/repos/example-owner/demo/contents/console.toml")
    assert kwargs["params"] == {"ref": "main"}


async def test_deploy_an_image_can_read_a_different_ref(
    client, db, owner, github_http, enqueued
):
    project_id = await seed_project(db)
    await connect_github(db)
    github_http.routes["/contents/console.toml"] = toml_response()

    response = await client.post(
        f"/api/projects/{project_id}/deployments",
        json={"image": "ghcr.io/example-owner/demo:v2", "ref": "release"},
    )

    assert response.status_code == 202
    assert github_http.requests[0][2]["params"] == {"ref": "release"}


async def test_deploy_an_image_supersedes_an_older_queued_row(
    client, db, owner, github_http, enqueued
):
    project_id = await seed_project(db)
    await connect_github(db)
    github_http.routes["/contents/console.toml"] = toml_response()
    stale = await seed_deployment(db, project_id, "aaaaaaa11111", status="queued")

    response = await client.post(
        f"/api/projects/{project_id}/deployments",
        json={"image": "ghcr.io/example-owner/demo:abc1234"},
    )

    assert response.status_code == 202
    async with db() as session:
        assert (await session.get(Deployment, stale)).status == "superseded"


async def test_deploy_an_image_outside_the_owner_s_namespace_is_400(
    client, db, owner, github_http, enqueued
):
    project_id = await seed_project(db)
    await connect_github(db)

    response = await client.post(
        f"/api/projects/{project_id}/deployments",
        json={"image": "ghcr.io/someone-else/demo:abc1234"},
    )

    assert response.status_code == 400
    assert "not under ghcr.io/example-owner/" in response.json()["detail"]
    assert enqueued == []


async def test_deploy_an_image_without_a_tag_is_400(client, db, owner, enqueued):
    project_id = await seed_project(db)

    response = await client.post(
        f"/api/projects/{project_id}/deployments",
        json={"image": "ghcr.io/example-owner/demo"},
    )

    assert response.status_code == 400
    assert "needs a tag" in response.json()["detail"]
    assert enqueued == []


async def test_deploy_an_image_with_invalid_toml_is_400(
    client, db, owner, github_http, enqueued
):
    project_id = await seed_project(db)
    await connect_github(db)
    github_http.routes["/contents/console.toml"] = toml_response("[app]\nname = 1\n")

    response = await client.post(
        f"/api/projects/{project_id}/deployments",
        json={"image": "ghcr.io/example-owner/demo:abc1234"},
    )

    assert response.status_code == 400
    assert "console.toml invalid" in response.json()["detail"]
    assert enqueued == []


async def test_deploy_an_image_with_no_console_toml_in_the_repo_is_400(
    client, db, owner, github_http, enqueued
):
    project_id = await seed_project(db)
    await connect_github(db)
    github_http.routes["/contents/console.toml"] = FakeResponse({"message": "Not Found"}, 404)

    response = await client.post(
        f"/api/projects/{project_id}/deployments",
        json={"image": "ghcr.io/example-owner/demo:abc1234"},
    )

    assert response.status_code == 400
    assert 'no console.toml in example-owner/demo at "main"' in response.json()["detail"]
    assert enqueued == []


async def test_deploy_an_image_without_a_github_connection_is_503(
    client, db, owner, github_http, enqueued
):
    project_id = await seed_project(db)

    response = await client.post(
        f"/api/projects/{project_id}/deployments",
        json={"image": "ghcr.io/example-owner/demo:abc1234"},
    )

    assert response.status_code == 503
    assert "not connected to GitHub" in response.json()["detail"]
    assert enqueued == []


async def test_a_pasted_console_toml_works_with_github_unreachable(
    client, db, owner, github_http, enqueued
):
    # The fallback that matters: the incident behind this feature was GitHub
    # being down, so a deploy must not depend on reading the repo.
    project_id = await seed_project(db)

    response = await client.post(
        f"/api/projects/{project_id}/deployments",
        json={"image": "ghcr.io/example-owner/demo:abc1234", "console_toml": TOML},
    )

    assert response.status_code == 202
    assert github_http.requests == []
    async with db() as session:
        deployment = await session.get(Deployment, response.json()["deployment_id"])
    assert deployment.config_snapshot == snapshot()


async def test_deploy_an_image_for_an_unknown_project_is_404(client, owner, enqueued):
    response = await client.post(
        "/api/projects/nope/deployments",
        json={"image": "ghcr.io/example-owner/demo:abc1234"},
    )

    assert response.status_code == 404
    assert enqueued == []
