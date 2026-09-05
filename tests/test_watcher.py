"""Build on push, by polling. A new branch head becomes exactly one building
deployment; an unchanged head, a project with the feature off, and a project
enabled a moment ago produce nothing; one broken repo never stops the rest."""

import pytest

from conftest import FakeResponse
from console import settings_store
from console.db.models import Deployment, Project
from console.deploy import builder, watcher
from sqlalchemy import select

OLD = "a" * 40
NEW = "b" * 40


def head(sha, message="a commit"):
    return FakeResponse({"sha": sha, "commit": {"message": message}})


@pytest.fixture(autouse=True)
def enqueued(monkeypatch):
    calls = []
    monkeypatch.setattr(builder, "enqueue", calls.append)
    return calls


async def connect_github(db):
    async with db() as session:
        await settings_store.set_value(session, settings_store.GITHUB_TOKEN, "gho_abc")
        await session.commit()


async def seed(db, name="demo", *, auto_build=True, watched_sha=OLD, branch="main"):
    async with db() as session:
        project = Project(
            name=name,
            repo=f"example-owner/{name}",
            subdomain=name,
            branch=branch,
            auto_build=auto_build,
            watched_sha=watched_sha,
        )
        session.add(project)
        await session.commit()
        return project.id


async def sweep(db):
    async with db() as session:
        await watcher.sweep(session)


async def deployments(db, project_id):
    async with db() as session:
        rows = await session.scalars(
            select(Deployment).where(Deployment.project_id == project_id)
        )
        return list(rows)


async def project(db, project_id):
    async with db() as session:
        return await session.get(Project, project_id)


async def test_a_new_head_starts_one_build(db, github_http, enqueued):
    project_id = await seed(db)
    await connect_github(db)
    github_http.routes["/commits/main"] = head(NEW, "Ask for the school first")

    await sweep(db)

    rows = await deployments(db, project_id)
    assert len(rows) == 1
    assert rows[0].sha == NEW
    assert rows[0].status == "building"
    assert rows[0].commit_message == "Ask for the school first"
    assert enqueued == [rows[0].id]
    assert (await project(db, project_id)).watched_sha == NEW
    assert github_http.requests[0][1].endswith("/repos/example-owner/demo/commits/main")


async def test_an_unchanged_head_builds_nothing(db, github_http, enqueued):
    project_id = await seed(db)
    await connect_github(db)
    github_http.routes["/commits/main"] = head(OLD)

    await sweep(db)
    await sweep(db)

    assert await deployments(db, project_id) == []
    assert enqueued == []


async def test_the_same_new_head_is_built_once(db, github_http, enqueued):
    project_id = await seed(db)
    await connect_github(db)
    github_http.routes["/commits/main"] = head(NEW)

    await sweep(db)
    await sweep(db)

    assert len(await deployments(db, project_id)) == 1
    assert len(enqueued) == 1


async def test_a_project_with_the_feature_off_is_never_polled(db, github_http, enqueued):
    await seed(db, auto_build=False, watched_sha=None)
    await connect_github(db)
    github_http.routes["/commits/main"] = head(NEW)

    await sweep(db)

    assert github_http.requests == []
    assert enqueued == []


async def test_no_baseline_takes_the_head_as_seen(db, github_http, enqueued):
    project_id = await seed(db, watched_sha=None)
    await connect_github(db)
    github_http.routes["/commits/main"] = head(NEW)

    await sweep(db)

    assert await deployments(db, project_id) == []
    assert (await project(db, project_id)).watched_sha == NEW
    assert enqueued == []


async def test_polls_the_tracked_branch(db, github_http, enqueued):
    await seed(db, branch="release")
    await connect_github(db)
    github_http.routes["/commits/release"] = head(NEW)

    await sweep(db)

    assert len(enqueued) == 1
    assert github_http.requests[0][1].endswith("/commits/release")


async def test_one_broken_repo_does_not_stop_the_rest(db, github_http, enqueued):
    broken = await seed(db, "broken")
    fine = await seed(db, "fine")
    await connect_github(db)
    github_http.routes["/repos/example-owner/broken/"] = FakeResponse({"message": "gone"}, 404)
    github_http.routes["/repos/example-owner/fine/"] = head(NEW)

    await sweep(db)

    assert await deployments(db, broken) == []
    assert len(await deployments(db, fine)) == 1
    # The broken one keeps its baseline, so it builds when the repo is back.
    assert (await project(db, broken)).watched_sha == OLD


async def test_a_head_already_in_flight_is_recorded_not_rebuilt(db, github_http, enqueued):
    # "build now" got there first: the watcher must not race it.
    project_id = await seed(db)
    await connect_github(db)
    github_http.routes["/commits/main"] = head(NEW)
    async with db() as session:
        session.add(Deployment(project_id=project_id, sha=NEW, status="building"))
        await session.commit()

    await sweep(db)

    assert len(await deployments(db, project_id)) == 1
    assert (await project(db, project_id)).watched_sha == NEW
    assert enqueued == []


async def test_not_connected_polls_nothing(db, github_http, enqueued):
    await seed(db)  # no connect_github
    github_http.routes["/commits/main"] = head(NEW)

    await sweep(db)

    assert github_http.requests == []
    assert enqueued == []
