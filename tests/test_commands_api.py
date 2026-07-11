"""Command API tests: running is refused when the app is not live, a live app
creates a running row and enqueues it, and runs are readable back."""

import pytest

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


class FakeDocker:
    def __init__(self):
        self.live = []
        self.containers = FakeContainers(self)

    def add_live(self, name, project_id):
        self.live.append(
            FakeContainer(name, {"console.project": project_id})
        )


@pytest.fixture
def fake_docker(monkeypatch):
    fake = FakeDocker()
    monkeypatch.setattr("console.docker.containers.get_client", lambda: fake)
    return fake


@pytest.fixture
def enqueued(monkeypatch):
    """Capture enqueue instead of spawning a real background task (which would
    talk to the wrong database)."""
    ids: list[str] = []
    monkeypatch.setattr(runner, "enqueue", ids.append)
    return ids


async def make_project(db, subdomain="app-demo", name="demo", repo="sam-stuhl/demo"):
    async with db() as session:
        project = Project(name=name, repo=repo, subdomain=subdomain)
        session.add(project)
        await session.commit()
        return project.id


async def test_run_rejected_when_not_live(client, db, fake_docker, enqueued):
    project_id = await make_project(db)
    res = await client.post(
        f"/api/projects/{project_id}/commands", json={"command": "echo hi"}
    )
    assert res.status_code == 409
    assert "not running" in res.json()["detail"]
    assert enqueued == []


async def test_run_creates_row_and_enqueues(client, db, fake_docker, enqueued):
    project_id = await make_project(db)
    fake_docker.add_live("demo-abc1234", project_id)

    res = await client.post(
        f"/api/projects/{project_id}/commands", json={"command": "  ls -la  "}
    )
    assert res.status_code == 202
    run_id = res.json()["run_id"]
    assert enqueued == [run_id]

    async with db() as session:
        row = await session.get(CommandRun, run_id)
    assert row.status == "running"
    assert row.command == "ls -la"  # trimmed


async def test_whitespace_command_rejected(client, db, fake_docker, enqueued):
    project_id = await make_project(db)
    fake_docker.add_live("demo-abc1234", project_id)
    res = await client.post(
        f"/api/projects/{project_id}/commands", json={"command": "   "}
    )
    assert res.status_code == 400
    assert enqueued == []


async def test_run_unknown_project(client, fake_docker, enqueued):
    res = await client.post(
        "/api/projects/nope/commands", json={"command": "echo hi"}
    )
    assert res.status_code == 404


async def test_get_and_list_runs(client, db, fake_docker):
    project_id = await make_project(db)
    async with db() as session:
        cmd_run = CommandRun(
            project_id=project_id,
            command="echo hi",
            status="succeeded",
            exit_code=0,
            output="hi\n",
        )
        session.add(cmd_run)
        await session.commit()
        run_id = cmd_run.id

    detail = await client.get(f"/api/projects/{project_id}/commands/{run_id}")
    assert detail.status_code == 200
    assert detail.json()["output"] == "hi\n"

    listed = await client.get(f"/api/projects/{project_id}/commands")
    assert listed.status_code == 200
    rows = listed.json()
    assert len(rows) == 1
    assert rows[0]["id"] == run_id
    assert "output" not in rows[0]  # summaries stay light


async def test_get_run_wrong_project(client, db, fake_docker):
    project_a = await make_project(db, subdomain="a", name="a", repo="sam-stuhl/a")
    project_b = await make_project(db, subdomain="b", name="b", repo="sam-stuhl/b")
    async with db() as session:
        cmd_run = CommandRun(project_id=project_a, command="x", status="running")
        session.add(cmd_run)
        await session.commit()
        run_id = cmd_run.id

    res = await client.get(f"/api/projects/{project_b}/commands/{run_id}")
    assert res.status_code == 404
