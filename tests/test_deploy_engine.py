"""Engine tests against a fake Docker client. The one that matters most:
on a failed health check the old container is never stopped."""

from datetime import datetime, timedelta

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
        self.stop_error = None
        self.stop_timeouts = []
        self.output = []
        self.logs_error = None
        self.attrs = {"State": {"Status": "running" if running else "exited"}}

    def crash(self, exit_code, output, status="exited"):
        """The process inside died, the way a bad entrypoint does."""
        self.running = False
        self.attrs = {"State": {"Status": status, "ExitCode": exit_code}}
        self.output = list(output)

    def reload(self):
        pass

    def logs(self, tail=None):
        if self.logs_error:
            raise docker.errors.APIError(self.logs_error)
        lines = self.output if tail is None else self.output[-tail:]
        return "".join(f"{line}\n" for line in lines).encode()

    def stop(self, timeout=None):
        if self.stop_error:
            raise docker.errors.APIError(self.stop_error)
        self.stop_timeouts.append(timeout)
        self.stopped = True
        self.running = False
        self.fake.events.append(("stop", self.name))

    def start(self):
        self.stopped = False
        self.running = True
        self.fake.events.append(("start", self.name))

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

    def remove(self, image):
        if image in self.fake.images_in_use:
            raise docker.errors.APIError(f"image is being used: {image}")
        if image in self.fake.images_missing:
            raise docker.errors.ImageNotFound(f"no such image: {image}")
        self.fake.removed_images.append(image)


class FakeApi:
    def create_endpoint_config(self, aliases=None):
        return {"Aliases": aliases}


class FakeDocker:
    def __init__(self):
        self.api = FakeApi()
        self.by_name = {}
        self.events = []
        self.run_calls = []
        self.pull_error = None
        self.removed_images = []
        self.images_in_use = set()
        self.images_missing = set()
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


@pytest.fixture
def crashed(monkeypatch, fake_docker):
    """The new container's process dies while the probe is still polling, so
    the probe only ever sees that nothing is listening."""

    async def probe(url, timeout):
        fake_docker.events.append(("probe", url))
        fake_docker.by_name[fake_docker.run_calls[-1]["name"]].crash(
            1, ["boot: two configs claim the same slot, refusing to pick"]
        )
        return False, "ConnectError: [Errno -2] Name or service not known"

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


async def test_live_deploy_syncs_the_subdomain_from_console_toml(sessions, fake_docker, healthy):
    """The router rule comes from console.toml; the record follows it."""
    project, deployment, _ = await seed(sessions, toml=TOML.replace('"app-demo"', '"cedar"'))
    await engine.run_deploy(deployment.id)

    async with sessions() as session:
        assert (await session.get(Project, project.id)).subdomain == "cedar"
    assert 'subdomain "app-demo" -> "cedar"' in (await reload(sessions, deployment.id)).log


async def test_subdomain_sync_never_takes_another_projects(sessions, fake_docker, healthy):
    async with sessions() as session:
        session.add(Project(name="other", repo="example-owner/other", subdomain="cedar"))
        await session.commit()
    project, deployment, _ = await seed(sessions, toml=TOML.replace('"app-demo"', '"cedar"'))
    await engine.run_deploy(deployment.id)

    async with sessions() as session:
        assert (await session.get(Project, project.id)).subdomain == "app-demo"
    assert "which other holds" in (await reload(sessions, deployment.id)).log


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


# Failure evidence. The probe can only report that nothing answered; the
# reason the process is not running is in the container's own output, and used
# to be thrown away with the container.


async def test_failed_deploy_records_the_container_it_removed(
    sessions, fake_docker, crashed
):
    _, deployment, _ = await seed(sessions)

    await engine.run_deploy(deployment.id)

    row = await reload(sessions, deployment.id)
    assert row.status == "failed"
    assert "container exited, exit code 1" in row.log
    assert "two configs claim the same slot" in row.log
    # The one-line summary is still the probe's, and the evidence precedes it
    assert "did not return 200" in row.failure_reason
    assert row.log.index("container exited") < row.log.index("failed: health check")
    # And the container is still gone
    assert "demo-e5f6a7b" not in fake_docker.by_name


async def test_crash_looping_container_is_read_while_restarting(
    sessions, fake_docker, monkeypatch
):
    """unless-stopped keeps restarting a dying entrypoint for the whole
    timeout, so the container can be mid-restart when the deploy gives up."""

    async def probe(url, timeout):
        fake_docker.by_name[fake_docker.run_calls[-1]["name"]].crash(
            2, ["boot: missing setting"], status="restarting"
        )
        return False, "ConnectError: connection refused"

    monkeypatch.setattr(engine, "_probe", probe)
    _, deployment, _ = await seed(sessions)

    await engine.run_deploy(deployment.id)

    row = await reload(sessions, deployment.id)
    assert "container restarting, exit code 2" in row.log
    assert "boot: missing setting" in row.log


async def test_running_container_is_not_given_an_exit_code(
    sessions, fake_docker, unhealthy
):
    """A container that is up but never returns 200 has no exit code to
    report, and inventing one would point at the wrong bug."""
    _, deployment, _ = await seed(sessions)

    await engine.run_deploy(deployment.id)

    row = await reload(sessions, deployment.id)
    assert "container running" in row.log
    assert "exit code" not in row.log
    assert "container wrote no output" in row.log


async def test_unreadable_container_still_fails_and_is_removed(
    sessions, fake_docker, unhealthy, monkeypatch
):
    """Capture is best effort: it must never change a deploy's outcome."""

    def explode(self, *args, **kwargs):
        raise docker.errors.APIError("daemon is not answering")

    monkeypatch.setattr(FakeContainer, "reload", explode)
    monkeypatch.setattr(FakeContainer, "logs", explode)
    _, deployment, _ = await seed(sessions)

    await engine.run_deploy(deployment.id)

    row = await reload(sessions, deployment.id)
    assert row.status == "failed"
    assert "did not return 200" in row.failure_reason
    assert "demo-e5f6a7b" not in fake_docker.by_name


async def test_captured_output_is_capped(sessions, fake_docker, monkeypatch):
    """One runaway line must not become the whole deployment log."""
    monkeypatch.setattr(config, "FAILED_LOG_MAX_BYTES", 64)

    async def probe(url, timeout):
        fake_docker.by_name[fake_docker.run_calls[-1]["name"]].crash(
            1, ["x" * 500, "the last line names the cause"]
        )
        return False, "ConnectError: connection refused"

    monkeypatch.setattr(engine, "_probe", probe)
    _, deployment, _ = await seed(sessions)

    await engine.run_deploy(deployment.id)

    row = await reload(sessions, deployment.id)
    assert "truncated" in row.log
    assert "the last line names the cause" in row.log
    assert "x" * 100 not in row.log


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


# Image retention. The console creates one image per deploy and used to remove
# none, which filled the Docker disk until dockerd would not start and every
# container on the box stopped.


async def history(db, project_id, images):
    """Older deployments for a project, oldest first, as if already deployed."""
    async with db() as session:
        for n, image in enumerate(images):
            session.add(
                Deployment(
                    project_id=project_id,
                    sha=f"old{n:09d}",
                    image=image,
                    status="superseded",
                    created_at=datetime(2026, 1, 1) + timedelta(hours=n),
                )
            )
        await session.commit()


async def test_deploy_prunes_images_beyond_retention(
    sessions, fake_docker, healthy, monkeypatch
):
    monkeypatch.setattr(config, "IMAGE_RETENTION", 3)
    project, deployment, _ = await seed(sessions)
    await history(sessions, project.id, [f"ghcr.io/example-owner/demo:v{n}" for n in range(5)])

    await engine.run_deploy(deployment.id)

    row = await reload(sessions, deployment.id)
    assert row.status == "live", row.failure_reason
    # Newest three kept: the one just deployed plus v4 and v3.
    assert fake_docker.removed_images == [
        "ghcr.io/example-owner/demo:v2",
        "ghcr.io/example-owner/demo:v1",
        "ghcr.io/example-owner/demo:v0",
    ]
    assert "pruned 3 superseded image(s)" in row.log


async def test_prune_never_removes_the_image_just_deployed(
    sessions, fake_docker, healthy, monkeypatch
):
    monkeypatch.setattr(config, "IMAGE_RETENTION", 1)
    project, deployment, _ = await seed(sessions)
    # The same image deployed before, so it is old by date but currently live.
    await history(sessions, project.id, [IMAGE, "ghcr.io/example-owner/demo:v9"])

    await engine.run_deploy(deployment.id)

    assert IMAGE not in fake_docker.removed_images


async def test_prune_leaves_images_another_container_still_uses(
    sessions, fake_docker, healthy, monkeypatch
):
    monkeypatch.setattr(config, "IMAGE_RETENTION", 1)
    project, deployment, _ = await seed(sessions)
    await history(sessions, project.id, ["ghcr.io/example-owner/demo:busy"])
    fake_docker.images_in_use.add("ghcr.io/example-owner/demo:busy")

    await engine.run_deploy(deployment.id)

    row = await reload(sessions, deployment.id)
    assert row.status == "live", row.failure_reason
    assert fake_docker.removed_images == []
    assert "pruned" not in row.log


async def test_prune_ignores_images_already_gone(
    sessions, fake_docker, healthy, monkeypatch
):
    monkeypatch.setattr(config, "IMAGE_RETENTION", 1)
    project, deployment, _ = await seed(sessions)
    await history(sessions, project.id, ["ghcr.io/example-owner/demo:gone"])
    fake_docker.images_missing.add("ghcr.io/example-owner/demo:gone")

    await engine.run_deploy(deployment.id)

    row = await reload(sessions, deployment.id)
    assert row.status == "live", row.failure_reason
    assert fake_docker.removed_images == []


async def test_retention_zero_keeps_every_image(
    sessions, fake_docker, healthy, monkeypatch
):
    monkeypatch.setattr(config, "IMAGE_RETENTION", 0)
    project, deployment, _ = await seed(sessions)
    await history(sessions, project.id, [f"ghcr.io/example-owner/demo:v{n}" for n in range(4)])

    await engine.run_deploy(deployment.id)

    assert fake_docker.removed_images == []


async def test_prune_never_touches_another_project(
    sessions, fake_docker, healthy, monkeypatch
):
    monkeypatch.setattr(config, "IMAGE_RETENTION", 1)
    project, deployment, _ = await seed(sessions)
    async with sessions() as session:
        other = Project(name="other", repo="example-owner/other", subdomain="app-other")
        session.add(other)
        await session.flush()
        session.add(
            Deployment(
                project_id=other.id,
                sha="othersha0001",
                image="ghcr.io/example-owner/other:v1",
                status="superseded",
                created_at=datetime(2025, 1, 1),
            )
        )
        await session.commit()

    await engine.run_deploy(deployment.id)

    assert "ghcr.io/example-owner/other:v1" not in fake_docker.removed_images


async def test_prune_failure_never_fails_the_deploy(
    sessions, fake_docker, healthy, monkeypatch
):
    monkeypatch.setattr(config, "IMAGE_RETENTION", 1)

    calls = []

    def flaky():
        calls.append(1)
        if len(calls) > 1:  # the deploy gets a client, the prune does not
            raise RuntimeError("docker went away")
        return fake_docker

    project, deployment, _ = await seed(sessions)
    await history(sessions, project.id, ["ghcr.io/example-owner/demo:v1"])
    monkeypatch.setattr(engine, "get_client", flaky)

    await engine.run_deploy(deployment.id)

    row = await reload(sessions, deployment.id)
    assert row.status == "live", row.failure_reason


# Stateful apps: [[volumes]], [[mounts]], and the recreate strategy. The
# invariant flips: the old container stops first, and a failed deploy removes
# the new one before starting the old one again, so the two never share data.

STATEFUL = """
[app]
name = "demo"
subdomain = "app-demo"
port = 80

[deploy]
strategy = "recreate"

[[volumes]]
name = "data"
target = "/data"
"""

READER = """
[app]
name = "demo"
subdomain = "app-demo"
port = 80

[[mounts]]
from = "vault-db/data"
target = "/source"
"""


@pytest.fixture
def volume_root(monkeypatch):
    monkeypatch.setattr(config, "VOLUME_ROOT", "/srv/volumes")


async def add_live_project(db, app_name, toml_body):
    """Another project with a live deployment, the way a mount source exists.
    The project row is named apart from its app, as a rename leaves it."""
    async with db() as session:
        other = Project(
            name=f"other-{app_name}",
            repo=f"example-owner/other-{app_name}",
            subdomain=f"other-{app_name}",
        )
        session.add(other)
        await session.flush()
        session.add(
            Deployment(
                project_id=other.id,
                sha="0000000aaaa",
                status="live",
                config_snapshot=snapshot(toml_body),
            )
        )
        await session.commit()


def other_app(name, volumes=("data",)):
    lines = [f'[app]\nname = "{name}"\nsubdomain = "{name}"\nport = 80\n']
    if volumes:
        lines.append('[deploy]\nstrategy = "recreate"\n')
    for v in volumes:
        lines.append(f'[[volumes]]\nname = "{v}"\ntarget = "/{v}"\n')
    return "\n".join(lines)


async def test_recreate_stops_old_before_starting_new(
    sessions, fake_docker, healthy, volume_root
):
    project, deployment, rows = await seed(sessions, toml=STATEFUL, with_live_row=True)
    old = fake_docker.add_live("demo-a1b2c3d", project.id, priority=config.PRIORITY_START)

    await engine.run_deploy(deployment.id)

    row = await reload(sessions, deployment.id)
    assert row.status == "live", row.failure_reason
    events = fake_docker.events
    assert events.index(("stop", "demo-a1b2c3d")) < events.index(("run", "demo-e5f6a7b"))
    assert old.removed
    (call,) = fake_docker.run_calls
    assert call["volumes"] == {"/srv/volumes/demo/data": {"bind": "/data", "mode": "rw"}}


async def test_failed_recreate_removes_new_then_restarts_old(
    sessions, fake_docker, unhealthy, volume_root
):
    project, deployment, _ = await seed(sessions, toml=STATEFUL, with_live_row=True)
    old = fake_docker.add_live("demo-a1b2c3d", project.id, priority=config.PRIORITY_START)

    await engine.run_deploy(deployment.id)

    row = await reload(sessions, deployment.id)
    assert row.status == "failed"
    assert "did not return 200" in row.failure_reason
    assert old.running and not old.removed
    assert "demo-e5f6a7b" not in fake_docker.by_name
    events = fake_docker.events
    # The invariant: never two containers on the volume at once
    assert events.index(("remove", "demo-e5f6a7b")) < events.index(("start", "demo-a1b2c3d"))
    assert "restarted demo-a1b2c3d" in row.log


async def test_old_stays_stopped_if_new_cannot_be_removed(
    sessions, fake_docker, unhealthy, volume_root
):
    project, deployment, _ = await seed(sessions, toml=STATEFUL, with_live_row=True)
    old = fake_docker.add_live("demo-a1b2c3d", project.id, priority=config.PRIORITY_START)
    real_run = fake_docker.containers.run

    def run_unremovable(image, **kwargs):
        container = real_run(image, **kwargs)

        def refuse(force=False):
            raise docker.errors.APIError("device or resource busy")

        container.remove = refuse
        return container

    fake_docker.containers.run = run_unremovable

    await engine.run_deploy(deployment.id)

    row = await reload(sessions, deployment.id)
    assert row.status == "failed"
    assert not old.running
    assert "old container left stopped" in row.log


async def test_reaped_recreate_restarts_old_and_keeps_reapers_reason(
    sessions, fake_docker, monkeypatch, volume_root
):
    project, deployment, _ = await seed(sessions, toml=STATEFUL, with_live_row=True)
    old = fake_docker.add_live("demo-a1b2c3d", project.id, priority=config.PRIORITY_START)

    async def probe_then_reaped(url, timeout):
        async with sessions() as session:
            row = await session.get(Deployment, deployment.id)
            row.status = "failed"
            row.failure_reason = "deploy timed out after 90 seconds"
            await session.commit()
        return True, "HTTP 200"

    monkeypatch.setattr(engine, "_probe", probe_then_reaped)

    await engine.run_deploy(deployment.id)

    row = await reload(sessions, deployment.id)
    assert row.failure_reason == "deploy timed out after 90 seconds"
    assert old.running and not old.removed
    assert "demo-e5f6a7b" not in fake_docker.by_name


async def test_volumes_without_a_root_fail_before_touching_docker(
    sessions, fake_docker, healthy, monkeypatch
):
    monkeypatch.setattr(config, "VOLUME_ROOT", "")
    project, deployment, _ = await seed(sessions, toml=STATEFUL, with_live_row=True)
    old = fake_docker.add_live("demo-a1b2c3d", project.id, priority=config.PRIORITY_START)

    await engine.run_deploy(deployment.id)

    row = await reload(sessions, deployment.id)
    assert row.status == "failed"
    assert "CONSOLE_VOLUME_ROOT" in row.failure_reason
    assert not old.stopped
    assert fake_docker.run_calls == []


async def test_mount_of_a_live_volume_is_read_only(
    sessions, fake_docker, healthy, volume_root
):
    await add_live_project(sessions, "vault-db", other_app("vault-db"))
    _, deployment, _ = await seed(sessions, toml=READER)

    await engine.run_deploy(deployment.id)

    row = await reload(sessions, deployment.id)
    assert row.status == "live", row.failure_reason
    (call,) = fake_docker.run_calls
    assert call["volumes"] == {
        "/srv/volumes/vault-db/data": {"bind": "/source", "mode": "ro"}
    }


async def test_mount_of_an_undeclared_volume_fails(
    sessions, fake_docker, healthy, volume_root
):
    await add_live_project(sessions, "vault-db", other_app("vault-db", volumes=("other",)))
    _, deployment, _ = await seed(sessions, toml=READER)

    await engine.run_deploy(deployment.id)

    row = await reload(sessions, deployment.id)
    assert row.status == "failed"
    assert 'declares a volume "data"' in row.failure_reason
    assert fake_docker.run_calls == []


async def test_two_projects_cannot_share_a_volume_directory(
    sessions, fake_docker, healthy, volume_root
):
    await add_live_project(sessions, "demo", other_app("demo"))
    _, deployment, _ = await seed(sessions, toml=STATEFUL)

    await engine.run_deploy(deployment.id)

    row = await reload(sessions, deployment.id)
    assert row.status == "failed"
    assert "must be unique" in row.failure_reason


NAS_STATEFUL = STATEFUL.replace('name = "data"\n', 'name = "data"\nstorage = "nas"\n')


async def test_nas_volume_lives_under_the_nas_root(
    sessions, fake_docker, healthy, volume_root, monkeypatch
):
    monkeypatch.setattr(config, "NAS_ROOT", "/Volumes/tank/console-volumes")
    _, deployment, _ = await seed(sessions, toml=NAS_STATEFUL)

    await engine.run_deploy(deployment.id)

    row = await reload(sessions, deployment.id)
    assert row.status == "live", row.failure_reason
    (call,) = fake_docker.run_calls
    assert call["volumes"] == {
        "/Volumes/tank/console-volumes/demo/data": {"bind": "/data", "mode": "rw"}
    }


async def test_nas_volume_without_a_nas_root_fails(
    sessions, fake_docker, healthy, volume_root, monkeypatch
):
    monkeypatch.setattr(config, "NAS_ROOT", "")
    _, deployment, _ = await seed(sessions, toml=NAS_STATEFUL)

    await engine.run_deploy(deployment.id)

    row = await reload(sessions, deployment.id)
    assert row.status == "failed"
    assert "CONSOLE_NAS_ROOT" in row.failure_reason
    assert fake_docker.run_calls == []


async def test_mount_of_a_nas_volume_follows_it(
    sessions, fake_docker, healthy, volume_root, monkeypatch
):
    monkeypatch.setattr(config, "NAS_ROOT", "/Volumes/tank/console-volumes")
    source = other_app("vault-db").replace(
        'name = "data"\n', 'name = "data"\nstorage = "nas"\n'
    )
    await add_live_project(sessions, "vault-db", source)
    _, deployment, _ = await seed(sessions, toml=READER)

    await engine.run_deploy(deployment.id)

    row = await reload(sessions, deployment.id)
    assert row.status == "live", row.failure_reason
    (call,) = fake_docker.run_calls
    assert call["volumes"] == {
        "/Volumes/tank/console-volumes/vault-db/data": {"bind": "/source", "mode": "ro"}
    }


async def test_stateless_app_gets_no_volumes(sessions, fake_docker, healthy):
    _, deployment, _ = await seed(sessions)
    await engine.run_deploy(deployment.id)
    (call,) = fake_docker.run_calls
    assert "volumes" not in call


async def test_recreate_gives_the_old_container_time_to_flush(
    sessions, fake_docker, healthy, volume_root
):
    project, deployment, _ = await seed(sessions, toml=STATEFUL, with_live_row=True)
    old = fake_docker.add_live("demo-a1b2c3d", project.id, priority=config.PRIORITY_START)

    await engine.run_deploy(deployment.id)

    assert old.stop_timeouts[0] == config.RECREATE_STOP_TIMEOUT


async def test_a_stop_that_raises_still_restarts_what_was_stopped(
    sessions, fake_docker, healthy, volume_root
):
    """Two live containers: the first stops, the second's stop errors. The
    first must not be left down by a deploy that never started anything."""
    project, deployment, _ = await seed(sessions, toml=STATEFUL, with_live_row=True)
    first = fake_docker.add_live("demo-a1b2c3d", project.id, priority=config.PRIORITY_START)
    second = fake_docker.add_live("demo-a1b2c3e", project.id, priority=config.PRIORITY_START)
    second.stop_error = "client read timeout"

    await engine.run_deploy(deployment.id)

    row = await reload(sessions, deployment.id)
    assert row.status == "failed"
    assert "client read timeout" in row.failure_reason
    assert fake_docker.run_calls == []
    assert first.running and second.running
    assert "restarted demo-a1b2c3d" in row.log


async def test_a_mid_deploy_project_claims_its_app_name(
    sessions, fake_docker, healthy, volume_root
):
    async with sessions() as session:
        other = Project(name="other", repo="example-owner/other", subdomain="other")
        session.add(other)
        await session.flush()
        session.add(
            Deployment(
                project_id=other.id,
                sha="0000000aaaa",
                status="deploying",
                config_snapshot=snapshot(other_app("demo")),
            )
        )
        await session.commit()
    _, deployment, _ = await seed(sessions, toml=STATEFUL)

    await engine.run_deploy(deployment.id)

    row = await reload(sessions, deployment.id)
    assert row.status == "failed"
    assert "must be unique" in row.failure_reason


# A recreate app gets its app name as a network alias, so other apps can reach
# it at the same address across deploys (vault-bridge -> http://vault-db:5984).


async def test_recreate_app_gets_its_name_as_a_network_alias(
    sessions, fake_docker, healthy, volume_root
):
    _, deployment, _ = await seed(sessions, toml=STATEFUL)
    await engine.run_deploy(deployment.id)

    row = await reload(sessions, deployment.id)
    assert row.status == "live", row.failure_reason
    (call,) = fake_docker.run_calls
    assert call["networking_config"] == {"web": {"Aliases": ["demo"]}}
    assert "network alias demo" in row.log


async def test_swap_app_gets_no_alias(sessions, fake_docker, healthy):
    _, deployment, _ = await seed(sessions)
    await engine.run_deploy(deployment.id)
    (call,) = fake_docker.run_calls
    assert "networking_config" not in call


async def test_alias_may_not_shadow_another_container(
    sessions, fake_docker, healthy, volume_root
):
    fake_docker.add_live("demo", "not-this-project")  # e.g. traefik, console
    _, deployment, _ = await seed(sessions, toml=STATEFUL)

    await engine.run_deploy(deployment.id)

    row = await reload(sessions, deployment.id)
    assert row.status == "failed"
    assert 'name of container "demo"' in row.failure_reason
    assert fake_docker.run_calls == []


async def test_recreate_without_volumes_still_claims_a_unique_name(
    sessions, fake_docker, healthy, volume_root
):
    await add_live_project(sessions, "demo", other_app("demo", volumes=()))
    stateless_recreate = TOML + '\n[deploy]\nstrategy = "recreate"\n'
    _, deployment, _ = await seed(sessions, toml=stateless_recreate)

    await engine.run_deploy(deployment.id)

    row = await reload(sessions, deployment.id)
    assert row.status == "failed"
    assert "must be unique" in row.failure_reason


async def test_database_error_mid_recreate_still_cleans_up(
    sessions, fake_docker, healthy, monkeypatch, volume_root
):
    """Seen live: SQLite "database is locked" on a log write during the health
    check left the session needing a rollback, so the fail path itself raised
    and nothing was cleaned up. A real failed flush (a foreign key violation)
    puts the session in that same state."""
    project, deployment, _ = await seed(sessions, toml=STATEFUL, with_live_row=True)
    old = fake_docker.add_live("demo-a1b2c3d", project.id, priority=config.PRIORITY_START)
    real_log = engine._log

    async def failing_log(session, row, line):
        if line.startswith("health check"):
            row.project_id = "no-such-project"
        await real_log(session, row, line)

    monkeypatch.setattr(engine, "_log", failing_log)

    await engine.run_deploy(deployment.id)

    row = await reload(sessions, deployment.id)
    assert row.status == "failed"
    assert "internal error" in row.failure_reason
    assert "demo-e5f6a7b" not in fake_docker.by_name
    assert old.running and not old.removed
    events = fake_docker.events
    assert events.index(("remove", "demo-e5f6a7b")) < events.index(("start", "demo-a1b2c3d"))
