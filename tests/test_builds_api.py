"""Starting a build from the API. The ref is resolved through the GitHub
connection, the row starts in "building", and the builder is handed its id.
Everything that can refuse does so before any row exists."""

import pytest

from conftest import FakeResponse
from console import config, settings_store
from console.db.models import Deployment, Project
from console.deploy import builder

SHA = "bc1c66d0f1e2d3c4b5a6978877665544332211aa"


def commit_response(sha=SHA, message="Ask for the school first"):
    return FakeResponse({"sha": sha, "commit": {"message": message}})


@pytest.fixture(autouse=True)
def enqueued(monkeypatch):
    calls = []
    monkeypatch.setattr(builder, "enqueue", calls.append)
    return calls


@pytest.fixture
def owner(monkeypatch):
    monkeypatch.setattr(config, "OIDC_OWNER", "example-owner")


async def seed_project(db, branch="main"):
    async with db() as session:
        project = Project(
            name="demo", repo="example-owner/demo", subdomain="demo", branch=branch
        )
        session.add(project)
        await session.commit()
        return project.id


async def connect_github(db):
    async with db() as session:
        await settings_store.set_value(session, settings_store.GITHUB_TOKEN, "gho_abc")
        await session.commit()


async def test_build_resolves_the_branch_and_starts_building(
    client, db, owner, github_http, enqueued
):
    project_id = await seed_project(db)
    await connect_github(db)
    github_http.routes["/commits/main"] = commit_response()

    response = await client.post(f"/api/projects/{project_id}/builds", json={})

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "building"
    assert enqueued == [body["deployment_id"]]
    async with db() as session:
        deployment = await session.get(Deployment, body["deployment_id"])
    assert deployment.sha == SHA
    assert deployment.commit_message == "Ask for the school first"
    assert deployment.status == "building"
    assert deployment.image is None  # the build decides that
    _, url, _ = github_http.requests[0]
    assert url.endswith("/repos/example-owner/demo/commits/main")


async def test_build_can_take_a_specific_ref(client, db, owner, github_http, enqueued):
    project_id = await seed_project(db)
    await connect_github(db)
    github_http.routes["/commits/release"] = commit_response(sha="a" * 40)

    response = await client.post(
        f"/api/projects/{project_id}/builds", json={"ref": "release"}
    )

    assert response.status_code == 202
    assert github_http.requests[0][1].endswith("/commits/release")


async def test_unknown_ref_is_400(client, db, owner, github_http, enqueued):
    project_id = await seed_project(db)
    await connect_github(db)
    github_http.routes["/commits/nope"] = FakeResponse({"message": "Not Found"}, 404)

    response = await client.post(f"/api/projects/{project_id}/builds", json={"ref": "nope"})

    assert response.status_code == 400
    assert response.json()["detail"] == 'no commit "nope" in example-owner/demo'
    assert enqueued == []


async def test_no_github_connection_is_503(client, db, owner, github_http, enqueued):
    project_id = await seed_project(db)

    response = await client.post(f"/api/projects/{project_id}/builds", json={})

    assert response.status_code == 503
    assert "not connected to GitHub" in response.json()["detail"]
    assert enqueued == []


async def test_github_error_is_502(client, db, owner, github_http, enqueued):
    project_id = await seed_project(db)
    await connect_github(db)
    github_http.routes["/commits/main"] = FakeResponse({"message": "boom"}, 500)

    response = await client.post(f"/api/projects/{project_id}/builds", json={})

    assert response.status_code == 502
    assert enqueued == []


async def test_a_sha_already_in_flight_is_409(client, db, owner, github_http, enqueued):
    project_id = await seed_project(db)
    await connect_github(db)
    github_http.routes["/commits/main"] = commit_response()
    async with db() as session:
        session.add(Deployment(project_id=project_id, sha=SHA, status="building"))
        await session.commit()

    response = await client.post(f"/api/projects/{project_id}/builds", json={})

    assert response.status_code == 409
    assert "already building" in response.json()["detail"]
    assert enqueued == []


async def test_a_sha_that_already_deployed_can_be_built_again(
    client, db, owner, github_http, enqueued
):
    # A finished row is history, not a claim: rebuilding the same commit is
    # how a Dockerfile-external change (a base image, a dependency) gets in.
    project_id = await seed_project(db)
    await connect_github(db)
    github_http.routes["/commits/main"] = commit_response()
    async with db() as session:
        session.add(Deployment(project_id=project_id, sha=SHA, status="live"))
        await session.commit()

    response = await client.post(f"/api/projects/{project_id}/builds", json={})

    assert response.status_code == 202
    assert len(enqueued) == 1


async def test_unknown_project_is_404(client, db, owner, github_http, enqueued):
    response = await client.post("/api/projects/nope/builds", json={})
    assert response.status_code == 404


# ------------------------------------------------------------ build on push


async def test_enabling_auto_build_baselines_at_the_current_head(
    client, db, owner, github_http, enqueued
):
    project_id = await seed_project(db)
    await connect_github(db)
    github_http.routes["/commits/main"] = commit_response()

    response = await client.put(
        f"/api/projects/{project_id}/auto-build", json={"enabled": True}
    )

    assert response.status_code == 200
    assert response.json()["auto_build"] is True
    async with db() as session:
        project = await session.get(Project, project_id)
    assert project.auto_build is True
    assert project.watched_sha == SHA
    assert enqueued == []  # enabling never builds what is already there


async def test_disabling_auto_build_clears_the_baseline(client, db, owner, github_http):
    project_id = await seed_project(db)
    async with db() as session:
        project = await session.get(Project, project_id)
        project.auto_build = True
        project.watched_sha = SHA
        await session.commit()

    response = await client.put(
        f"/api/projects/{project_id}/auto-build", json={"enabled": False}
    )

    assert response.status_code == 200
    assert response.json()["auto_build"] is False
    async with db() as session:
        project = await session.get(Project, project_id)
    assert project.auto_build is False
    assert project.watched_sha is None
    assert github_http.requests == []  # nothing to look up when switching off


async def test_enabling_without_github_is_503(client, db, owner, github_http):
    project_id = await seed_project(db)

    response = await client.put(
        f"/api/projects/{project_id}/auto-build", json={"enabled": True}
    )

    assert response.status_code == 503
    async with db() as session:
        project = await session.get(Project, project_id)
    assert project.auto_build is False


async def test_enabling_with_a_missing_branch_is_400(client, db, owner, github_http):
    project_id = await seed_project(db, branch="nope")
    await connect_github(db)
    github_http.routes["/commits/nope"] = FakeResponse({"message": "Not Found"}, 404)

    response = await client.put(
        f"/api/projects/{project_id}/auto-build", json={"enabled": True}
    )

    assert response.status_code == 400
    assert response.json()["detail"] == 'no branch "nope" in example-owner/demo'


async def test_project_out_carries_auto_build(client, db, owner, github_http):
    project_id = await seed_project(db)
    response = await client.get(f"/api/projects/{project_id}")
    assert response.status_code == 200
    assert response.json()["auto_build"] is False
