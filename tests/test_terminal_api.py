"""Terminal websocket: the reachable-without-Docker branch. When no container
is live the endpoint tells the client and closes cleanly. The interactive
bridge itself needs a real TTY exec socket and is verified end to end by hand."""

from starlette.testclient import TestClient

from console.main import app


class FakeContainers:
    def list(self, filters=None):
        return []  # nothing live for any project


class FakeDocker:
    containers = FakeContainers()


def test_terminal_refuses_when_not_live(monkeypatch):
    monkeypatch.setattr("console.docker.containers.get_client", lambda: FakeDocker())
    # No `with` block: skip the app lifespan (migrations/reaper) in this test.
    client = TestClient(app)
    with client.websocket_connect("/api/projects/whatever/terminal") as ws:
        assert "not running" in ws.receive_text()
