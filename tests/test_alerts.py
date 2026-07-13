"""The ntfy alert sender: no-op when unconfigured, correct POST when set, and
the /api/alerts/test endpoint."""

import pytest

from console import alerts, settings_store


def fake_client(calls, status=200):
    class FakeResp:
        status_code = status
        text = ""

    class Fake:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, content=None, headers=None):
            calls.append({"url": url, "content": content, "headers": headers or {}})
            return FakeResp()

    return Fake


async def test_send_noop_without_topic(db):
    async with db() as session:
        assert await alerts.send(session, "t", "m") is False


async def test_send_posts_to_topic(db, monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(alerts.httpx, "AsyncClient", fake_client(calls))
    async with db() as session:
        await settings_store.set_value(session, settings_store.NTFY_TOPIC, "my-topic")
        await session.commit()
    async with db() as session:
        sent = await alerts.send(
            session, "Title", "Body", tags=["bell", "warning"], priority="high"
        )
    assert sent is True
    (call,) = calls
    assert call["url"] == "https://ntfy.sh/my-topic"
    assert call["content"] == b"Body"
    assert call["headers"]["Title"] == "Title"
    assert call["headers"]["Priority"] == "high"
    assert call["headers"]["Tags"] == "bell,warning"


async def test_send_custom_server(db, monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(alerts.httpx, "AsyncClient", fake_client(calls))
    async with db() as session:
        await settings_store.set_value(session, settings_store.NTFY_TOPIC, "topic")
        await settings_store.set_value(
            session, settings_store.NTFY_SERVER, "https://ntfy.example.com/"
        )
        await session.commit()
    async with db() as session:
        await alerts.send(session, "t", "m")
    assert calls[0]["url"] == "https://ntfy.example.com/topic"


async def test_send_returns_false_on_http_error(db, monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(alerts.httpx, "AsyncClient", fake_client(calls, status=500))
    async with db() as session:
        await settings_store.set_value(session, settings_store.NTFY_TOPIC, "topic")
        await session.commit()
    async with db() as session:
        assert await alerts.send(session, "t", "m") is False


async def test_alerts_test_endpoint(client, db, monkeypatch):
    unconfigured = await client.post("/api/alerts/test")
    assert unconfigured.status_code == 400

    calls: list[dict] = []
    monkeypatch.setattr(alerts.httpx, "AsyncClient", fake_client(calls))
    await client.put("/api/settings/ntfy_topic", json={"value": "topic"})
    ok = await client.post("/api/alerts/test")
    assert ok.status_code == 200
    assert ok.json() == {"sent": True}
    assert calls[0]["url"].endswith("/topic")
