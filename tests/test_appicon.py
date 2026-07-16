"""Pulling an app's favicon from its container: the HTML link parser and
accept/type helpers, the fetch (link tag, fallback, size cap, undeployed), and
the icon API (serve bytes, 404, manual refresh)."""

import json

import httpx
import pytest

from console import appicon, config
from console.db.models import Deployment, Project

SNAP = json.dumps({"app": {"name": "demo", "subdomain": "app-demo", "port": 8000}})
BASE = "http://demo-abc1234:8000/"
ICO_MAGIC = b"\x00\x00\x01\x00\x00\x00"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


class FakeResp:
    def __init__(self, status_code=200, text="", content=b"", content_type=""):
        self.status_code = status_code
        self.text = text
        self.content = content
        self.headers = {"content-type": content_type} if content_type else {}


class FakeClient:
    routes: dict[str, FakeResp] = {}

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url):
        if url in FakeClient.routes:
            return FakeClient.routes[url]
        raise httpx.ConnectError(f"no route: {url}")


@pytest.fixture
def http(monkeypatch):
    class Cont:
        name = "demo-abc1234"

    async def fake_find(_pid):
        return Cont()

    monkeypatch.setattr(appicon, "find_project_container", fake_find)
    monkeypatch.setattr(appicon.httpx, "AsyncClient", FakeClient)
    FakeClient.routes = {}
    return FakeClient


async def make_project(db, *, live=True):
    async with db() as session:
        project = Project(name="demo", repo="sam-stuhl/demo", subdomain="app-demo")
        session.add(project)
        await session.flush()
        if live:
            session.add(
                Deployment(
                    project_id=project.id, sha="abc", status="live", config_snapshot=SNAP
                )
            )
        await session.commit()
        return project.id


# --- pure helpers ----------------------------------------------------------


def test_parser_collects_icon_links():
    parser = appicon._IconLinkParser()
    parser.feed(
        '<link rel="stylesheet" href="/a.css">'
        '<link rel="apple-touch-icon" href="/touch.png">'
        '<link rel="icon" href="/favicon.svg">'
    )
    assert parser.hrefs == ["/touch.png", "/favicon.svg"]


def test_acceptable_by_type_and_magic():
    assert appicon._acceptable("image/png", "/x.png", PNG_MAGIC)
    assert appicon._acceptable("", "/favicon.ico", ICO_MAGIC)  # mislabeled but real
    assert appicon._acceptable("text/plain", "/f.svg", b"<svg></svg>")
    assert not appicon._acceptable("text/html", "/f", b"<html>")
    assert not appicon._acceptable("", "/favicon.ico", b"not an ico")


def test_content_type_guessed_from_extension():
    assert appicon._content_type("image/png", "/x.png") == "image/png"
    assert appicon._content_type("", "/favicon.svg") == "image/svg+xml"
    assert appicon._content_type("application/octet-stream", "/favicon.ico") == "image/x-icon"


# --- fetch_and_store -------------------------------------------------------


async def test_fetch_follows_link_tag(db, http):
    pid = await make_project(db)
    http.routes = {
        BASE: FakeResp(text='<head><link rel="icon" href="/static/logo.svg"></head>'),
        "http://demo-abc1234:8000/static/logo.svg": FakeResp(
            content=b"<svg>x</svg>", content_type="image/svg+xml"
        ),
    }
    async with db() as session:
        project = await session.get(Project, pid)
        assert await appicon.fetch_and_store(session, project) is True
        assert project.icon_data == b"<svg>x</svg>"
        assert project.icon_content_type == "image/svg+xml"
        assert project.icon_fetched_at is not None


async def test_fetch_falls_back_to_favicon_ico(db, http):
    pid = await make_project(db)
    http.routes = {
        BASE: FakeResp(text="<head>no icon link here</head>"),
        "http://demo-abc1234:8000/favicon.ico": FakeResp(
            content=ICO_MAGIC, content_type="image/x-icon"
        ),
    }
    async with db() as session:
        project = await session.get(Project, pid)
        assert await appicon.fetch_and_store(session, project) is True
        assert project.icon_data == ICO_MAGIC


async def test_fetch_rejects_oversized(db, http, monkeypatch):
    monkeypatch.setattr(config, "ICON_MAX_BYTES", 8)
    pid = await make_project(db)
    http.routes = {
        BASE: FakeResp(text='<link rel="icon" href="/favicon.svg">'),
        "http://demo-abc1234:8000/favicon.svg": FakeResp(
            content=b"<svg>way too large</svg>", content_type="image/svg+xml"
        ),
    }
    async with db() as session:
        project = await session.get(Project, pid)
        assert await appicon.fetch_and_store(session, project) is False
        assert project.icon_data is None


async def test_fetch_skips_undeployed(db, http):
    pid = await make_project(db, live=False)
    async with db() as session:
        project = await session.get(Project, pid)
        assert await appicon.fetch_and_store(session, project) is False


# --- API -------------------------------------------------------------------


async def test_icon_404_when_none(client, db):
    pid = await make_project(db, live=False)
    res = await client.get(f"/api/projects/{pid}/icon")
    assert res.status_code == 404


async def test_icon_served_with_safety_headers(client, db):
    pid = await make_project(db, live=False)
    async with db() as session:
        project = await session.get(Project, pid)
        project.icon_data = b"<svg>hi</svg>"
        project.icon_content_type = "image/svg+xml"
        await session.commit()

    res = await client.get(f"/api/projects/{pid}/icon")
    assert res.status_code == 200
    assert res.content == b"<svg>hi</svg>"
    assert res.headers["content-type"] == "image/svg+xml"
    assert "default-src 'none'" in res.headers["content-security-policy"]
    assert res.headers["x-content-type-options"] == "nosniff"

    listed = {p["id"]: p for p in (await client.get("/api/projects")).json()}
    assert listed[pid]["has_icon"] is True


async def test_refresh_endpoint_fetches(client, db, http):
    pid = await make_project(db)
    http.routes = {
        BASE: FakeResp(text='<link rel="icon" href="/favicon.svg">'),
        "http://demo-abc1234:8000/favicon.svg": FakeResp(
            content=b"<svg>logo</svg>", content_type="image/svg+xml"
        ),
    }
    res = await client.post(f"/api/projects/{pid}/icon/refresh")
    assert res.status_code == 200
    assert res.json() == {"fetched": True}
    icon = await client.get(f"/api/projects/{pid}/icon")
    assert icon.content == b"<svg>logo</svg>"
