"""The outbound event hook: no-op when unconfigured, the right POST when set,
and never raising when the receiver is down or refuses."""

from datetime import datetime

import httpx

from console import events, settings_store


def fake_client(calls, status=201, raises=None):
    class FakeResp:
        status_code = status
        text = "refused"

    class Fake:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json=None, headers=None):
            if raises:
                raise raises
            calls.append({"url": url, "json": json, "headers": headers or {}})
            return FakeResp()

    return Fake


async def configure(db, url="http://timeline.test/timeline/events", token=None):
    async with db() as session:
        await settings_store.set_value(session, settings_store.EVENT_URL, url)
        if token:
            await settings_store.set_value(session, settings_store.EVENT_TOKEN, token)
        await session.commit()


async def test_noop_without_url(db, monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(events.httpx, "AsyncClient", fake_client(calls))
    async with db() as session:
        assert await events.post(session, "console.deploy.live", "why") is False
    assert calls == []


async def test_posts_the_event_with_the_token(db, monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(events.httpx, "AsyncClient", fake_client(calls))
    await configure(db, token="tok")
    async with db() as session:
        sent = await events.post(
            session,
            "console.deploy.live",
            "a deploy went live",
            {"project": "cedar", "sha": "abc1234", "stage": None},
            at=datetime(2026, 10, 15, 1, 2, 3),
        )
    assert sent is True
    (call,) = calls
    assert call["url"] == "http://timeline.test/timeline/events"
    assert call["headers"] == {"Authorization": "Bearer tok"}
    assert call["json"] == {
        "kind": "console.deploy.live",
        "because": "a deploy went live",
        "refs": {"project": "cedar", "sha": "abc1234"},  # None refs are dropped
        "at": "2026-10-15T01:02:03Z",
    }


async def test_no_token_sends_no_header(db, monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(events.httpx, "AsyncClient", fake_client(calls))
    await configure(db)
    async with db() as session:
        assert await events.post(session, "console.app.down", "why") is True
    assert calls[0]["headers"] == {}


async def test_refused_or_unreachable_is_false_not_raised(db, monkeypatch):
    await configure(db, token="tok")
    monkeypatch.setattr(events.httpx, "AsyncClient", fake_client([], status=400))
    async with db() as session:
        assert await events.post(session, "console.app.down", "why") is False
    monkeypatch.setattr(
        events.httpx, "AsyncClient", fake_client([], raises=httpx.ConnectError("down"))
    )
    async with db() as session:
        assert await events.post(session, "console.app.down", "why") is False
