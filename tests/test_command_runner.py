"""Runner tests against a fake Docker exec API. Verifies output is streamed
into the row, the exit code decides the terminal status, a missing container
fails cleanly, and stored output is capped."""

import pytest

from console import config
from console.commands import runner
from console.db.models import CommandRun, Project


class FakeContainer:
    def __init__(self, name, labels):
        self.name = name
        self.labels = labels


class FakeContainers:
    def __init__(self, fake):
        self.fake = fake

    def list(self, filters=None):
        key, _, value = filters["label"].partition("=")
        return [c for c in self.fake.live if c.labels.get(key) == value]


class FakeExecApi:
    def __init__(self, fake):
        self.fake = fake

    def exec_create(self, name, cmd, **kwargs):
        self.fake.exec_calls.append({"name": name, "cmd": cmd, **kwargs})
        return {"Id": "exec-1"}

    def exec_start(self, exec_id, stream=False):
        return iter(self.fake.chunks)

    def exec_inspect(self, exec_id):
        return {"ExitCode": self.fake.exit_code}


class FakeDocker:
    def __init__(self):
        self.live = []
        self.chunks = []
        self.exit_code = 0
        self.exec_calls = []
        self.containers = FakeContainers(self)
        self.api = FakeExecApi(self)

    def add_live(self, name, project_id):
        c = FakeContainer(
            name, {"console.managed": "true", "console.project": project_id}
        )
        self.live.append(c)
        return c


@pytest.fixture
def fake_docker(monkeypatch, db):
    fake = FakeDocker()
    monkeypatch.setattr(runner, "get_client", lambda: fake)
    monkeypatch.setattr("console.docker.containers.get_client", lambda: fake)
    monkeypatch.setattr(runner, "SessionLocal", db)
    return fake


async def seed(db, *, command="echo hi"):
    async with db() as session:
        project = Project(name="demo", repo="example-owner/demo", subdomain="app-demo")
        session.add(project)
        await session.flush()
        cmd_run = CommandRun(
            project_id=project.id, command=command, status="running"
        )
        session.add(cmd_run)
        await session.commit()
        return project, cmd_run


async def reload(db, run_id):
    async with db() as session:
        return await session.get(CommandRun, run_id)


async def test_command_succeeds(db, fake_docker):
    project, cmd_run = await seed(db)
    fake_docker.add_live("demo-abc1234", project.id)
    fake_docker.chunks = [b"hello ", b"world\n"]
    fake_docker.exit_code = 0

    await runner.run_command(cmd_run.id)

    row = await reload(db, cmd_run.id)
    assert row.status == "succeeded"
    assert row.exit_code == 0
    assert row.output == "hello world\n"
    assert row.container_name == "demo-abc1234"
    assert row.finished_at is not None
    # A shell line, not an argv.
    assert fake_docker.exec_calls[0]["cmd"] == ["/bin/sh", "-lc", "echo hi"]


async def test_nonzero_exit_fails(db, fake_docker):
    project, cmd_run = await seed(db)
    fake_docker.add_live("demo-abc1234", project.id)
    fake_docker.chunks = [b"boom\n"]
    fake_docker.exit_code = 1

    await runner.run_command(cmd_run.id)

    row = await reload(db, cmd_run.id)
    assert row.status == "failed"
    assert row.exit_code == 1
    assert row.failure_reason == "exited 1"
    assert row.output == "boom\n"


async def test_no_live_container_fails(db, fake_docker):
    _project, cmd_run = await seed(db)  # no add_live

    await runner.run_command(cmd_run.id)

    row = await reload(db, cmd_run.id)
    assert row.status == "failed"
    assert row.failure_reason == "app is not running; deploy it first"
    assert row.output is None


async def test_output_is_truncated(db, fake_docker, monkeypatch):
    monkeypatch.setattr(config, "COMMAND_OUTPUT_MAX", 10)
    project, cmd_run = await seed(db)
    fake_docker.add_live("demo-abc1234", project.id)
    fake_docker.chunks = [b"0123456789ABCDEF", b"more"]
    fake_docker.exit_code = 0

    await runner.run_command(cmd_run.id)

    row = await reload(db, cmd_run.id)
    assert row.status == "succeeded"
    assert row.output.startswith("0123456789")
    assert "output truncated" in row.output
    # The second chunk never made it past the cap.
    assert "more" not in row.output
