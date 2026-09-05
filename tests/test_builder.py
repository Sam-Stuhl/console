"""Builder tests against a fake Docker client. What matters: the build
container gets the right context, image, and tokens; its exit code decides
the row's fate; the tokens never outlive it; and nothing is run at all when
the build cannot possibly succeed."""

import time

import pytest

from console import config, settings_store
from console.db.models import Deployment, Project
from console.deploy import builder, engine

SHA = "bc1c66d0f1e2d3c4b5a6978877665544332211aa"
TOML = """
[app]
name = "demo"
subdomain = "app-demo"
port = 80
dockerfile = "./deploy/Dockerfile"
"""


class FakeBuildContainer:
    def __init__(self, fake):
        self.fake = fake
        self.killed = False
        self.removed = False

    def logs(self, stream=False, follow=False):
        return iter(self.fake.chunks)

    def wait(self):
        return {"StatusCode": self.fake.exit_code}

    def kill(self):
        self.killed = True

    def remove(self, force=False):
        self.removed = True


class FakeContainers:
    def __init__(self, fake):
        self.fake = fake

    def run(self, image, command, **kwargs):
        self.fake.runs.append({"image": image, "command": command, **kwargs})
        self.fake.container = FakeBuildContainer(self.fake)
        return self.fake.container


class FakeDocker:
    def __init__(self):
        self.runs = []
        self.chunks = [b"#1 building\n", b"#2 pushing\n"]
        self.exit_code = 0
        self.container = None
        self.containers = FakeContainers(self)


@pytest.fixture
def fake_docker(monkeypatch, db):
    fake = FakeDocker()
    monkeypatch.setattr(builder, "get_client", lambda: fake)
    monkeypatch.setattr(builder, "SessionLocal", db)
    monkeypatch.setattr(config, "OIDC_OWNER", "example-owner")
    return fake


@pytest.fixture
def queued(monkeypatch):
    """Replace the engine's queue so a successful build never deploys here."""
    calls = []

    async def fake_queue(session, deployment):
        calls.append(deployment.id)
        await session.commit()

    monkeypatch.setattr(engine, "queue", fake_queue)
    return calls


@pytest.fixture
def repo_file(monkeypatch):
    """What GitHub answers for console.toml at the sha."""
    state = {"text": TOML, "error": None}

    async def read_file(self, repo, path, ref):
        assert path == "console.toml"
        assert ref == SHA
        if state["error"] is not None:
            raise state["error"]
        return state["text"]

    monkeypatch.setattr(builder.github.GitHub, "read_file", read_file)
    return state


async def seed(db, *, repo="example-owner/demo", status="building", tokens=True):
    async with db() as session:
        if tokens:
            await settings_store.set_value(session, settings_store.GITHUB_TOKEN, "gh-token")
            await settings_store.set_value(session, settings_store.GHCR_TOKEN, "ghcr-token")
        project = Project(name="demo", repo=repo, subdomain="app-demo")
        session.add(project)
        await session.flush()
        deployment = Deployment(project_id=project.id, sha=SHA, status=status)
        session.add(deployment)
        await session.commit()
        return project, deployment


async def reload(db, deployment_id):
    async with db() as session:
        return await session.get(Deployment, deployment_id)


async def test_success_queues_the_pushed_image(db, fake_docker, queued, repo_file):
    _project, deployment = await seed(db)

    await builder.run_build(deployment.id)

    row = await reload(db, deployment.id)
    assert row.status == "queued"
    assert row.image == "ghcr.io/example-owner/demo:bc1c66d"
    assert row.build_finished_at is not None
    assert '"dockerfile":"./deploy/Dockerfile"' in row.config_snapshot
    assert "#1 building\n#2 pushing\n" in row.log
    assert queued == [deployment.id]

    run = fake_docker.runs[0]
    assert run["image"] == config.BUILD_IMAGE
    assert run["command"] == ["sh", "-c", builder.SCRIPT]
    env = run["environment"]
    assert env["CONTEXT"] == f"https://github.com/example-owner/demo.git#{SHA}"
    assert env["IMAGE"] == "ghcr.io/example-owner/demo:bc1c66d"
    assert env["DOCKERFILE"] == "./deploy/Dockerfile"
    assert env["GIT_AUTH_TOKEN"] == "gh-token"
    assert env["GHCR_TOKEN"] == "ghcr-token"
    assert env["GHCR_USER"] == "example-owner"
    assert env["BUILDER"] == config.BUILD_BUILDER
    assert run["volumes"] == {"/var/run/docker.sock": {"bind": "/var/run/docker.sock", "mode": "rw"}}
    assert fake_docker.container.removed


async def test_image_path_is_lowercased_like_ghcr_wants(db, fake_docker, queued, repo_file):
    _project, deployment = await seed(db, repo="Example-Owner/Demo")

    await builder.run_build(deployment.id)

    row = await reload(db, deployment.id)
    assert row.image == "ghcr.io/example-owner/demo:bc1c66d"
    # The git context keeps the registered case; GitHub does not care, and
    # the image name is the only place GHCR does.
    assert fake_docker.runs[0]["environment"]["CONTEXT"].startswith(
        "https://github.com/Example-Owner/Demo.git#"
    )


async def test_nonzero_exit_fails_and_keeps_the_log(db, fake_docker, queued, repo_file):
    _project, deployment = await seed(db)
    fake_docker.chunks = [b"ERROR: failed to solve\n"]
    fake_docker.exit_code = 1

    await builder.run_build(deployment.id)

    row = await reload(db, deployment.id)
    assert row.status == "failed"
    assert row.failure_reason == "build exited 1"
    assert "ERROR: failed to solve\n" in row.log
    assert row.image is None
    assert queued == []
    assert fake_docker.container.removed


async def test_missing_packages_token_fails_before_docker(db, fake_docker, queued, repo_file):
    _project, deployment = await seed(db, tokens=False)
    async with db() as session:
        await settings_store.set_value(session, settings_store.GITHUB_TOKEN, "gh-token")
        await session.commit()

    await builder.run_build(deployment.id)

    row = await reload(db, deployment.id)
    assert row.status == "failed"
    assert "write:packages" in row.failure_reason
    assert fake_docker.runs == []


async def test_github_not_connected_fails_before_docker(db, fake_docker, queued, repo_file):
    _project, deployment = await seed(db, tokens=False)

    await builder.run_build(deployment.id)

    row = await reload(db, deployment.id)
    assert row.status == "failed"
    assert "not connected to GitHub" in row.failure_reason
    assert fake_docker.runs == []


async def test_missing_console_toml_fails_before_docker(db, fake_docker, queued, repo_file):
    _project, deployment = await seed(db)
    repo_file["error"] = builder.github.FileNotFound("console.toml")

    await builder.run_build(deployment.id)

    row = await reload(db, deployment.id)
    assert row.status == "failed"
    assert row.failure_reason == "no console.toml in example-owner/demo at bc1c66d"
    assert fake_docker.runs == []


async def test_bad_console_toml_fails_before_docker(db, fake_docker, queued, repo_file):
    _project, deployment = await seed(db)
    repo_file["text"] = "[app]\nname = 'demo'\n"  # no subdomain, no port

    await builder.run_build(deployment.id)

    row = await reload(db, deployment.id)
    assert row.status == "failed"
    assert fake_docker.runs == []


async def test_output_is_capped_but_the_build_still_finishes(
    db, fake_docker, queued, repo_file, monkeypatch
):
    monkeypatch.setattr(config, "BUILD_LOG_MAX", 80)
    _project, deployment = await seed(db)
    fake_docker.chunks = [b"x" * 60, b"y" * 60, b"z" * 60]

    await builder.run_build(deployment.id)

    row = await reload(db, deployment.id)
    assert row.status == "queued"
    assert builder._TRUNCATED in row.log
    assert "z" not in row.log
    assert queued == [deployment.id]


async def test_timeout_kills_the_build(db, fake_docker, queued, repo_file, monkeypatch):
    monkeypatch.setattr(config, "BUILD_TIMEOUT", 0.2)
    _project, deployment = await seed(db)

    def slow():
        yield b"step 1\n"
        time.sleep(0.6)
        yield b"never seen\n"

    fake_docker.chunks = slow()

    await builder.run_build(deployment.id)

    row = await reload(db, deployment.id)
    assert row.status == "failed"
    assert row.failure_reason == "build outran 0 minutes"
    assert fake_docker.container.killed
    assert fake_docker.container.removed
    assert queued == []


async def test_only_a_building_row_is_built(db, fake_docker, queued, repo_file):
    _project, deployment = await seed(db, status="failed")

    await builder.run_build(deployment.id)

    row = await reload(db, deployment.id)
    assert row.status == "failed"
    assert fake_docker.runs == []
