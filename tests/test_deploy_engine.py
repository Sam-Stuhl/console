"""Engine tests against a fake Docker client. The one that matters most:
on a failed health check the old container is never stopped."""

import docker.errors
import pytest

from console import config
from console.db.models import Deployment, Project, Secret
from console.deploy import engine
from console.schema.console_toml import parse_console_toml
from console.secrets import crypto

TOML = """
[app]
name = "demo"
subdomain = "app-demo"
port = 80

[env]
LOG_LEVEL = "info"
"""

# secrets must sit above the first [section] header
TOML_WITH_SECRET = 'secrets = ["DATABASE_URL"]\n' + TOML
SHA = "e5f6a7b0d1c2"
IMAGE = "ghcr.io/example-owner/demo:e5f6a7b"


def snapshot(text=TOML):
    parsed, _ = parse_console_toml(text)
    return parsed.model_dump_json()


class FakeContainer:
    def __init__(self, fake, name, labels, running=True):
        self.fake = fake
        self.name = name
        self.labels = labels
        self.running = running
        self.stopped = False
        self.removed = False

    def stop(self):
        self.stopped = True
        self.fake.events.append(("stop", self.name))

    def remove(self, force=False):
        self.removed = True
        self.running = False
        self.fake.by_name.pop(self.name, None)
        self.fake.events.append(("remove", self.name))


class FakeContainers:
    def __init__(self, fake):
        self.fake = fake

    def list(self, filters=None):
        label = filters["label"]
        key, _, value = label.partition("=")
        return [
            c
            for c in self.fake.by_name.values()
            if c.running and c.labels.get(key) == value
        ]

    def get(self, name):
        try:
            return self.fake.by_name[name]
        except KeyError:
            raise docker.errors.NotFound(f"no such container: {name}")

    def run(self, image, **kwargs):
        self.fake.events.append(("run", kwargs["name"]))
        self.fake.run_calls.append({"image": image, **kwargs})
        container = FakeContainer(self.fake, kwargs["name"], kwargs["labels"])
        self.fake.by_name[kwargs["name"]] = container
        return container


class FakeImages:
    def __init__(self, fake):
        self.fake = fake

    def pull(self, image, auth_config=None):
        if self.fake.pull_error:
            raise docker.errors.APIError(self.fake.pull_error)
        self.fake.events.append(("pull", image, auth_config))


class FakeDocker:
    def __init__(self):
        self.by_name = {}
        self.events = []
        self.run_calls = []
        self.pull_error = None
        self.containers = FakeContainers(self)
        self.images = FakeImages(self)

    def add_live(self, name, project_id, priority=None, deployment_id="old-dep"):
        labels = {
            "console.managed": "true",
            "console.project": project_id,
            "console.deployment": deployment_id,
        }
        if priority is not None:
            labels[f"traefik.http.routers.{name}.priority"] = str(priority)
        container = FakeContainer(self, name, labels)
        self.by_name[name] = container
        return container


@pytest.fixture
def fake_docker(monkeypatch):
    fake = FakeDocker()
    monkeypatch.setattr(engine, "get_client", lambda: fake)
    return fake


@pytest.fixture
def sessions(db, monkeypatch):
    monkeypatch.setattr(engine, "SessionLocal", db)
    monkeypatch.setattr(engine, "_locks", {})
    return db


@pytest.fixture
def healthy(monkeypatch, fake_docker):
    async def probe(url, timeout):
        fake_docker.events.append(("probe", url))
        return True, "HTTP 200"

    monkeypatch.setattr(engine, "_probe", probe)


@pytest.fixture
def unhealthy(monkeypatch, fake_docker):
    async def probe(url, timeout):
        fake_docker.events.append(("probe", url))
        return False, "HTTP 503"

    monkeypatch.setattr(engine, "_probe", probe)


async def seed(db, *, toml=TOML, status="queued", with_live_row=False):
    async with db() as session:
        project = Project(name="demo", repo="example-owner/demo", subdomain="app-demo")
        session.add(project)
        await session.flush()
        rows = {}
        if with_live_row:
            live_row = Deployment(
                project_id=project.id, sha="a1b2c3d0aaaa", status="live"
            )
            session.add(live_row)
            rows["live"] = live_row
        deployment = Deployment(
            project_id=project.id,
            sha=SHA,
            image=IMAGE,
            status=status,
            config_snapshot=snapshot(toml),
        )
        session.add(deployment)
        await session.commit()
        return project, deployment, rows


async def reload(db, deployment_id):
    async with db() as session:
        return await session.get(Deployment, deployment_id)


async def test_first_deploy_goes_live(sessions, fake_docker, healthy):
    project, deployment, _ = await seed(sessions)
    await engine.run_deploy(deployment.id)

    row = await reload(sessions, deployment.id)
    assert row.status == "live", row.failure_reason
    assert row.substate is None
    assert row.container_name == "demo-e5f6a7b"
    assert row.router_priority == config.PRIORITY_START
    assert row.deploy_started_at is not None
    assert row.finished_at is not None
    assert "live" in row.log

    (call,) = fake_docker.run_calls
    assert call["image"] == IMAGE
    assert call["network"] == "web"
    assert call["environment"] == {"LOG_LEVEL": "info"}
    assert call["mem_limit"] == "512m"
    assert call["nano_cpus"] == 1_000_000_000
    assert call["restart_policy"] == {"Name": "unless-stopped"}
    labels = call["labels"]
    assert labels["traefik.http.routers.demo-e5f6a7b.priority"] == str(
        config.PRIORITY_START
    )
    assert labels["console.project"] == project.id
    assert labels["console.deployment"] == deployment.id


async def test_swap_happens_only_after_health(sessions, fake_docker, healthy):
    project, deployment, rows = await seed(sessions, with_live_row=True)
    old = fake_docker.add_live(
        "demo-a1b2c3d", project.id, priority=config.PRIORITY_START
    )

    await engine.run_deploy(deployment.id)

    row = await reload(sessions, deployment.id)
    assert row.status == "live"
    assert row.router_priority == config.PRIORITY_START - 1
    assert old.stopped and old.removed

    events = fake_docker.events
    assert events.index(("probe", "http://demo-e5f6a7b:80/health")) < events.index(
        ("stop", "demo-a1b2c3d")
    )

    previous = await reload(sessions, rows["live"].id)
    assert previous.status == "superseded"
    assert previous.finished_at is not None


async def test_failed_health_never_touches_old_container(
    sessions, fake_docker, unhealthy
):
    project, deployment, _ = await seed(sessions, with_live_row=True)
    old = fake_docker.add_live(
        "demo-a1b2c3d", project.id, priority=config.PRIORITY_START
    )

    await engine.run_deploy(deployment.id)

    row = await reload(sessions, deployment.id)
    assert row.status == "failed"
    assert "did not return 200" in row.failure_reason
    # The invariant
    assert not old.stopped
    assert not old.removed
    assert old.name in fake_docker.by_name
    # The new container is gone
    assert "demo-e5f6a7b" not in fake_docker.by_name


async def test_live_container_without_priority_label_gets_start(
    sessions, fake_docker, healthy
):
    project, deployment, _ = await seed(sessions)
    fake_docker.add_live("demo-a1b2c3d", project.id, priority=None)
    await engine.run_deploy(deployment.id)
    row = await reload(sessions, deployment.id)
    assert row.status == "live"
    assert row.router_priority == config.PRIORITY_START


async def test_superseded_row_never_runs(sessions, fake_docker, healthy):
    _, deployment, _ = await seed(sessions, status="superseded")
    await engine.run_deploy(deployment.id)
    row = await reload(sessions, deployment.id)
    assert row.status == "superseded"
    assert fake_docker.events == []


async def test_missing_secret_fails_before_docker(sessions, fake_docker, healthy):
    _, deployment, _ = await seed(sessions, toml=TOML_WITH_SECRET)
    await engine.run_deploy(deployment.id)
    row = await reload(sessions, deployment.id)
    assert row.status == "failed"
    assert row.failure_reason == 'secret "DATABASE_URL" is not set for this project'
    assert fake_docker.events == []


async def test_missing_key_file_fails_deploy(sessions, fake_docker, healthy, monkeypatch, tmp_path):
    project, deployment, _ = await seed(sessions, toml=TOML_WITH_SECRET)
    async with sessions() as session:
        session.add(
            Secret(
                project_id=project.id,
                key="DATABASE_URL",
                value_encrypted=crypto.encrypt("postgres://x"),
            )
        )
        await session.commit()
    monkeypatch.setattr(config, "KEY_FILE", str(tmp_path / "gone"))

    await engine.run_deploy(deployment.id)
    row = await reload(sessions, deployment.id)
    assert row.status == "failed"
    assert "console key not configured" in row.failure_reason
    assert fake_docker.events == []


async def test_secret_lands_in_environment(sessions, fake_docker, healthy):
    project, deployment, _ = await seed(sessions, toml=TOML_WITH_SECRET)
    async with sessions() as session:
        session.add(
            Secret(
                project_id=project.id,
                key="DATABASE_URL",
                value_encrypted=crypto.encrypt("postgres://x"),
            )
        )
        await session.commit()

    await engine.run_deploy(deployment.id)
    row = await reload(sessions, deployment.id)
    assert row.status == "live", row.failure_reason
    (call,) = fake_docker.run_calls
    assert call["environment"] == {
        "LOG_LEVEL": "info",
        "DATABASE_URL": "postgres://x",
    }


async def test_leftover_container_with_our_name_is_removed(
    sessions, fake_docker, healthy
):
    project, deployment, _ = await seed(sessions)
    leftover = FakeContainer(
        fake_docker, "demo-e5f6a7b", {"console.project": project.id}, running=False
    )
    fake_docker.by_name["demo-e5f6a7b"] = leftover

    await engine.run_deploy(deployment.id)
    row = await reload(sessions, deployment.id)
    assert row.status == "live"
    assert row.container_name == "demo-e5f6a7b"
    assert leftover.removed


async def test_same_sha_redeploy_gets_suffixed_name(sessions, fake_docker, healthy):
    project, deployment, _ = await seed(sessions)
    old = fake_docker.add_live(
        "demo-e5f6a7b", project.id, priority=config.PRIORITY_START
    )

    await engine.run_deploy(deployment.id)
    row = await reload(sessions, deployment.id)
    assert row.status == "live"
    assert row.container_name == f"demo-e5f6a7b-{deployment.id[:6]}"
    assert old.removed  # swapped out after health, as usual


async def test_pull_failure_marks_failed(sessions, fake_docker, healthy):
    _, deployment, _ = await seed(sessions)
    fake_docker.pull_error = "manifest unknown"
    await engine.run_deploy(deployment.id)
    row = await reload(sessions, deployment.id)
    assert row.status == "failed"
    assert "image pull failed" in row.failure_reason
    assert fake_docker.run_calls == []
