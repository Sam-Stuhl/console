"""Building from Forgejo, a per-project setting. What matters: a Forgejo
project is read and built from the instance in Settings, through a clone in
the build container on the apps' network with the token never an argument;
a GitHub project is untouched by any of it, even with Forgejo unconfigured;
and the migration that adds the column leaves every existing project on
GitHub and can be undone for a rollback."""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import select

from conftest import FakeResponse
from console import config, forgejo, settings_store, sources
from console.db.models import Deployment, Project
from console.deploy import builder, watcher
from console.errors import Conflict, Invalid, Unavailable

from test_builder import FakeDocker, SHA, TOML

ROOT = Path(__file__).resolve().parents[1]
OLD = "a" * 40
NEW = "b" * 40


class Raw(FakeResponse):
    """A raw file answer: Forgejo's raw endpoint returns bytes, not JSON."""

    def __init__(self, text, status_code=200):
        super().__init__(None, status_code)
        self.content = text.encode()
        self.text = text


class FakeHttp:
    routes: dict = {}
    requests: list = []

    def __init__(self, *_args, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def get(self, url, **kwargs):
        FakeHttp.requests.append((url, kwargs))
        for fragment, response in FakeHttp.routes.items():
            if fragment in url:
                return response
        return FakeResponse({"message": "not found"}, 404)


@pytest.fixture
def forgejo_http(monkeypatch):
    FakeHttp.routes = {}
    FakeHttp.requests = []
    monkeypatch.setattr(forgejo.httpx, "AsyncClient", FakeHttp)
    return FakeHttp


@pytest.fixture
def fake_docker(monkeypatch, db):
    fake = FakeDocker()
    monkeypatch.setattr(builder, "get_client", lambda: fake)
    monkeypatch.setattr(builder, "SessionLocal", db)
    monkeypatch.setattr(config, "OIDC_OWNER", "Example-Owner")
    return fake


@pytest.fixture
def queued(monkeypatch):
    calls = []

    async def fake_queue(session, deployment):
        calls.append(deployment.id)
        await session.commit()

    monkeypatch.setattr(builder.deploy_engine, "queue", fake_queue)
    return calls


@pytest.fixture
def enqueued(monkeypatch):
    calls = []
    monkeypatch.setattr(builder, "enqueue", calls.append)
    return calls


async def connect(db, *, github_token=True, forgejo_url="http://forgejo:3000/"):
    async with db() as session:
        await settings_store.set_value(session, settings_store.GHCR_TOKEN, "ghcr-token")
        if github_token:
            await settings_store.set_value(session, settings_store.GITHUB_TOKEN, "gh-token")
        if forgejo_url:
            await settings_store.set_value(session, settings_store.FORGEJO_URL, forgejo_url)
            await settings_store.set_value(session, settings_store.FORGEJO_TOKEN, "fj-token")
        await session.commit()


async def seed(db, name="canary", *, provider="forgejo", status=None, **fields):
    async with db() as session:
        project = Project(
            name=name,
            repo=f"sam/{name}",
            subdomain=name,
            git_provider=provider,
            **fields,
        )
        session.add(project)
        await session.flush()
        deployment = None
        if status is not None:
            deployment = Deployment(project_id=project.id, sha=SHA, status=status)
            session.add(deployment)
        await session.commit()
        return project, deployment


async def reload(db, model, row_id):
    async with db() as session:
        return await session.get(model, row_id)


# ------------------------------------------------------------------ client


async def test_resolve_commit_reads_the_commit_list(db, forgejo_http):
    forgejo_http.routes["/repos/sam/canary/commits"] = FakeResponse(
        [{"sha": NEW, "commit": {"message": "First canary"}}]
    )
    client = forgejo.Forgejo("http://forgejo:3000", "fj-token")

    assert await client.resolve_commit("sam/canary", "main") == (NEW, "First canary")

    url, kwargs = forgejo_http.requests[0]
    assert url == "http://forgejo:3000/api/v1/repos/sam/canary/commits"
    assert kwargs["params"]["sha"] == "main"
    assert kwargs["headers"]["Authorization"] == "token fj-token"


async def test_an_unknown_ref_is_file_not_found(db, forgejo_http):
    forgejo_http.routes["/commits"] = FakeResponse([])
    with pytest.raises(forgejo.FileNotFound):
        await forgejo.Forgejo("http://f", "t").resolve_commit("sam/canary", "nope")


async def test_read_file_returns_the_raw_text(db, forgejo_http):
    forgejo_http.routes["/raw/console.toml"] = Raw(TOML)
    text = await forgejo.Forgejo("http://f", "t").read_file("sam/canary", "console.toml", SHA)
    assert text == TOML
    assert forgejo_http.requests[0][1]["params"] == {"ref": SHA}


async def test_a_rejected_token_says_so(db, forgejo_http):
    forgejo_http.routes["/commits"] = FakeResponse({"message": "bad"}, 401)
    with pytest.raises(forgejo.ForgejoApiError, match="rejected the stored token"):
        await forgejo.Forgejo("http://f", "t").resolve_commit("sam/canary", "main")


async def test_a_bad_repo_never_reaches_the_request_path(db, forgejo_http):
    with pytest.raises(forgejo.ForgejoApiError):
        await forgejo.Forgejo("http://f", "t").resolve_commit("sam/canary?x=1", "main")
    assert forgejo_http.requests == []


async def test_unconfigured_forgejo_is_not_connected(db):
    async with db() as session:
        with pytest.raises(forgejo.ForgejoNotConnected):
            await sources.client(session, sources.FORGEJO)


# ------------------------------------------------------------------ build


async def test_a_forgejo_build_clones_on_the_apps_network(
    db, fake_docker, queued, forgejo_http
):
    await connect(db, github_token=False)
    forgejo_http.routes["/raw/console.toml"] = Raw(TOML)
    _project, deployment = await seed(db, name="Canary", status="building")

    await builder.run_build(deployment.id)

    row = await reload(db, Deployment, deployment.id)
    assert row.status == "queued", row.failure_reason
    assert row.image == "ghcr.io/example-owner/canary:bc1c66d"
    assert "from forgejo sam/Canary@bc1c66d" in row.log
    assert queued == [deployment.id]

    run = fake_docker.runs[0]
    assert run["command"] == ["sh", "-c", builder.FORGEJO_SCRIPT]
    assert run["network"] == "web"
    env = run["environment"]
    assert env["GIT_URL"] == "http://forgejo:3000/sam/Canary.git"
    assert env["GIT_SHA"] == SHA
    assert env["GIT_CONFIG_KEY_0"] == "http.http://forgejo:3000/.extraHeader"
    assert env["GIT_CONFIG_VALUE_0"] == "Authorization: token fj-token"
    assert env["DOCKERFILE"] == "./deploy/Dockerfile"
    # No GitHub credential and no remote context: nothing of the GitHub path.
    assert "GIT_AUTH_TOKEN" not in env and "CONTEXT" not in env
    # The token is only ever in the environment, never in the script.
    assert "fj-token" not in builder.FORGEJO_SCRIPT
    assert fake_docker.container.removed


async def test_a_forgejo_build_without_forgejo_settings_fails_before_docker(
    db, fake_docker, queued
):
    await connect(db, forgejo_url=None)
    _project, deployment = await seed(db, status="building")

    await builder.run_build(deployment.id)

    row = await reload(db, Deployment, deployment.id)
    assert row.status == "failed"
    assert "not connected to Forgejo" in row.failure_reason
    assert fake_docker.runs == []


async def test_a_github_build_is_unchanged_by_forgejo_settings(
    db, fake_docker, queued, monkeypatch
):
    await connect(db)

    async def read_file(self, repo, path, ref):
        return TOML

    monkeypatch.setattr(builder.github.GitHub, "read_file", read_file)
    _project, deployment = await seed(db, name="planner", provider="github", status="building")

    await builder.run_build(deployment.id)

    run = fake_docker.runs[0]
    assert run["command"] == ["sh", "-c", builder.SCRIPT]
    assert "network" not in run
    assert run["environment"]["CONTEXT"] == f"https://github.com/sam/planner.git#{SHA}"
    assert not any(k.startswith("GIT_CONFIG") for k in run["environment"])
    assert (await reload(db, Deployment, deployment.id)).image == (
        f"ghcr.io/sam/planner:{SHA[:7]}"
    )


# ------------------------------------------------------------------ watcher


async def test_watcher_builds_a_forgejo_push(db, forgejo_http, enqueued):
    await connect(db, github_token=False)
    project, _ = await seed(db, auto_build=True, watched_sha=OLD)
    forgejo_http.routes["/repos/sam/canary/commits"] = FakeResponse(
        [{"sha": NEW, "commit": {"message": "Push to Forgejo"}}]
    )

    async with db() as session:
        await watcher.sweep(session)

    async with db() as session:
        rows = list(await session.scalars(select(Deployment)))
    assert [(r.sha, r.status) for r in rows] == [(NEW, "building")]
    assert enqueued == [rows[0].id]


async def test_unconfigured_forgejo_never_stops_a_github_project(db, github_http, enqueued):
    await connect(db, forgejo_url=None)
    await seed(db, name="canary", auto_build=True, watched_sha=OLD)
    github_project, _ = await seed(
        db, name="planner", provider="github", auto_build=True, watched_sha=OLD
    )
    github_http.routes["/commits/main"] = FakeResponse(
        {"sha": NEW, "commit": {"message": "GitHub push"}}
    )

    async with db() as session:
        await watcher.sweep(session)

    async with db() as session:
        rows = list(await session.scalars(select(Deployment)))
    assert [(r.project_id, r.sha) for r in rows] == [(github_project.id, NEW)]
    # github and forgejo share one httpx, so this fake saw every request.
    assert [url for _m, url, _kw in github_http.requests if "forgejo" in url] == []


# ------------------------------------------------------------------ switching


async def test_switching_with_build_on_push_takes_the_new_head_as_seen(db, forgejo_http):
    await connect(db)
    project, _ = await seed(db, provider="github", auto_build=True, watched_sha=OLD)
    forgejo_http.routes["/commits"] = FakeResponse([{"sha": NEW, "commit": {}}])

    async with db() as session:
        row = await session.get(Project, project.id)
        await builder.set_git_provider(session, row, sources.FORGEJO)

    row = await reload(db, Project, project.id)
    assert (row.git_provider, row.watched_sha) == ("forgejo", NEW)


async def test_switching_to_an_unconfigured_provider_changes_nothing(db):
    await connect(db, forgejo_url=None)
    project, _ = await seed(db, provider="github", auto_build=True, watched_sha=OLD)

    async with db() as session:
        row = await session.get(Project, project.id)
        with pytest.raises(Unavailable):
            await builder.set_git_provider(session, row, sources.FORGEJO)

    row = await reload(db, Project, project.id)
    assert (row.git_provider, row.watched_sha) == ("github", OLD)


async def test_switching_onto_another_projects_image_path_is_refused(db, monkeypatch):
    monkeypatch.setattr(config, "OIDC_OWNER", "Example-Owner")
    async with db() as session:
        session.add(Project(name="app", repo="example-owner/app", subdomain="app"))
        await session.commit()
    project, _ = await seed(db, name="other", provider="github")
    async with db() as session:
        row = await session.get(Project, project.id)
        row.repo = "sam/app"
        await session.commit()

    async with db() as session:
        row = await session.get(Project, project.id)
        with pytest.raises(Conflict, match="already publishes to ghcr.io/example-owner/app:"):
            await builder.set_git_provider(session, row, sources.FORGEJO)
    assert (await reload(db, Project, project.id)).git_provider == "github"


async def test_an_unknown_provider_is_refused(db):
    project, _ = await seed(db, provider="github")
    async with db() as session:
        row = await session.get(Project, project.id)
        with pytest.raises(Invalid):
            await builder.set_git_provider(session, row, "gitlab")


# ------------------------------------------------------------------ migration


def _alembic(db_path, *args):
    env = {**os.environ, "CONSOLE_DB_PATH": str(db_path)}
    subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
    )


def test_migration_keeps_existing_projects_on_github_and_downgrades(tmp_path):
    db_path = tmp_path / "console.db"
    _alembic(db_path, "upgrade", "d0e1f2a3b4c5")
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO projects (id, name, repo, branch, subdomain, created_at,"
            " protected, auto_build) VALUES ('p1', 'Cedar', 'o/planner', 'main',"
            " 'cedar', '2026-01-01', 0, 1)"
        )

    _alembic(db_path, "upgrade", "head")
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT git_provider, auto_build FROM projects").fetchall() == [
            ("github", 1)
        ]

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO projects (id, name, repo, branch, subdomain, created_at,"
            " protected, auto_build, git_provider) VALUES ('p2', 'Canary', 'sam/canary',"
            " 'main', 'canary', '2026-01-01', 0, 1, 'forgejo')"
        )

    # The rollback path: back to the revision an older console image knows.
    _alembic(db_path, "downgrade", "d0e1f2a3b4c5")
    with sqlite3.connect(db_path) as conn:
        columns = [r[1] for r in conn.execute("PRAGMA table_info(projects)")]
        assert "git_provider" not in columns
        # Cedar keeps building on push; the Forgejo project stops, since an
        # older console would look for it on GitHub.
        assert conn.execute("SELECT name, auto_build FROM projects ORDER BY id").fetchall() == [
            ("Cedar", 1),
            ("Canary", 0),
        ]
