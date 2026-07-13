"""Controls API against a fake Docker client: each action drives the matching
container method and returns the refreshed state, and the guards (no container,
mid-deploy, unknown action) fire."""

import docker.errors
import pytest

from console.db.models import Project


class FakeContainer:
    def __init__(self, cid, name, labels, status="running"):
        self.id = cid
        self.name = name
        self.labels = labels
        self.status = status
        self.calls: list[str] = []

    @property
    def attrs(self):
        return {
            "Id": self.id,
            "Name": "/" + self.name,
            "Config": {"Image": "ghcr.io/sam-stuhl/demo:tag"},
            "State": {"Status": self.status},
            "Created": "2026-01-01T00:00:00Z",
        }

    def start(self):
        self.calls.append("start")
        self.status = "running"

    def stop(self):
        self.calls.append("stop")
        self.status = "exited"

    def restart(self):
        self.calls.append("restart")
        self.status = "running"


class FakeContainers:
    def __init__(self, items):
        self.items = items

    def list(self, all=False, filters=None):
        key, _, value = filters["label"].partition("=")
        res = [c for c in self.items if c.labels.get(key) == value]
        if not all:
            res = [c for c in res if c.status == "running"]
        return res

    def get(self, cid):
        for c in self.items:
            if c.id == cid:
                return c
        raise docker.errors.NotFound(f"no such container: {cid}")


class FakeDocker:
    def __init__(self):
        self.items: list[FakeContainer] = []
        self.containers = FakeContainers(self.items)

    def add(self, project_id, *, status="running", cid="c1", name="demo-abc1234"):
        self.items.append(
            FakeContainer(cid, name, {"console.project": project_id}, status)
        )


@pytest.fixture
def fake(monkeypatch):
    fd = FakeDocker()
    monkeypatch.setattr("console.docker.containers.get_client", lambda: fd)
    monkeypatch.setattr("console.api.controls.get_client", lambda: fd)
    return fd


async def make_project(db, subdomain="app-demo"):
    async with db() as session:
        project = Project(name="demo", repo="sam-stuhl/demo", subdomain=subdomain)
        session.add(project)
        await session.commit()
        return project.id


async def test_get_container_running(client, db, fake):
    pid = await make_project(db)
    fake.add(pid, status="running")
    res = await client.get(f"/api/projects/{pid}/container")
    assert res.status_code == 200
    assert res.json()["state"] == "running"


async def test_get_container_absent(client, db, fake):
    pid = await make_project(db)
    res = await client.get(f"/api/projects/{pid}/container")
    assert res.json() == {"state": "absent"}


async def test_stop_then_start(client, db, fake):
    pid = await make_project(db)
    fake.add(pid, status="running")

    stopped = await client.post(f"/api/projects/{pid}/controls/stop")
    assert stopped.status_code == 200
    assert stopped.json()["state"] == "exited"

    started = await client.post(f"/api/projects/{pid}/controls/start")
    assert started.json()["state"] == "running"
    assert fake.items[0].calls == ["stop", "start"]


async def test_restart(client, db, fake):
    pid = await make_project(db)
    fake.add(pid, status="running")
    res = await client.post(f"/api/projects/{pid}/controls/restart")
    assert res.status_code == 200
    assert res.json()["state"] == "running"
    assert fake.items[0].calls == ["restart"]


async def test_unknown_action_404(client, db, fake):
    pid = await make_project(db)
    fake.add(pid)
    res = await client.post(f"/api/projects/{pid}/controls/frobnicate")
    assert res.status_code == 404


async def test_no_container_409(client, db, fake):
    pid = await make_project(db)
    res = await client.post(f"/api/projects/{pid}/controls/restart")
    assert res.status_code == 409
    assert "deploy it first" in res.json()["detail"]


async def test_mid_deploy_409(client, db, fake):
    pid = await make_project(db)
    fake.add(pid, status="running", cid="new")
    fake.add(pid, status="running", cid="old")
    res = await client.post(f"/api/projects/{pid}/controls/restart")
    assert res.status_code == 409
    assert "deploy is in progress" in res.json()["detail"]
