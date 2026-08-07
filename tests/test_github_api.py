from urllib.parse import parse_qs, urlparse

import pytest

from conftest import FakeResponse
from console import config, settings_store

pytestmark = pytest.mark.usefixtures("github_http")


async def connect(db, token="gho_abc"):
    async with db() as session:
        await settings_store.set_value(session, settings_store.GITHUB_TOKEN, token)
        await session.commit()


async def configure_app(db):
    async with db() as session:
        await settings_store.set_value(session, settings_store.GITHUB_CLIENT_ID, "Iv1.abc")
        await settings_store.set_value(session, settings_store.GITHUB_CLIENT_SECRET, "sec")
        await session.commit()


@pytest.fixture(autouse=True)
def no_env_client_id(monkeypatch):
    """Tests decide what is configured; the environment must not leak in."""
    monkeypatch.setattr(config, "GITHUB_CLIENT_ID", "")


async def test_status_when_nothing_is_set_up(client):
    response = await client.get("/api/github/status")

    assert response.status_code == 200
    assert response.json() == {
        "app_configured": False,
        "connected": False,
        "login": None,
        "error": None,
    }


async def test_status_reports_the_connected_account(client, db, github_http):
    await configure_app(db)
    await connect(db)
    github_http.routes["/user"] = FakeResponse({"login": "example-owner"})

    body = (await client.get("/api/github/status")).json()

    assert body["app_configured"] is True
    assert body["connected"] is True
    assert body["login"] == "example-owner"
    assert body["error"] is None


async def test_status_surfaces_a_revoked_token(client, db, github_http):
    await configure_app(db)
    await connect(db)
    github_http.routes["/user"] = FakeResponse({"message": "Bad credentials"}, 401)

    body = (await client.get("/api/github/status")).json()

    # Still "connected" (a token is stored), but it no longer works, and the
    # UI needs to say so rather than fail later at the point of use.
    assert body["connected"] is True
    assert body["login"] is None
    assert "Reconnect" in body["error"]


# --- the redirect -----------------------------------------------------------


async def test_authorize_needs_a_configured_app(client):
    response = await client.get("/api/github/authorize")

    assert response.status_code == 503
    assert "client id and client secret" in response.json()["detail"]


async def test_authorize_redirects_to_github_with_a_state_cookie(client, db):
    await configure_app(db)

    response = await client.get("/api/github/authorize", follow_redirects=False)

    assert response.status_code == 303
    query = parse_qs(urlparse(response.headers["location"]).query)
    assert query["client_id"] == ["Iv1.abc"]
    assert query["scope"] == [config.GITHUB_SCOPE]
    # The callback is derived from the request, so it matches whatever hostname
    # the console is being used on, and both halves send the same value.
    assert query["redirect_uri"] == ["http://test/api/github/callback"]
    # The state is what ties the callback back to this request.
    assert response.cookies["console_github_state"] == query["state"][0]


async def test_the_callback_stores_the_token(client, db, github_http):
    await configure_app(db)
    github_http.routes["login/oauth/access_token"] = FakeResponse(
        {"access_token": "gho_new"}
    )
    started = await client.get("/api/github/authorize", follow_redirects=False)
    state = parse_qs(urlparse(started.headers["location"]).query)["state"][0]

    response = await client.get(
        f"/api/github/callback?code=abc123&state={state}", follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/settings?github=connected"
    async with db() as session:
        assert await settings_store.get(session, settings_store.GITHUB_TOKEN) == "gho_new"


async def test_the_callback_refuses_a_state_it_did_not_issue(client, db, github_http):
    await configure_app(db)
    await client.get("/api/github/authorize", follow_redirects=False)

    response = await client.get(
        "/api/github/callback?code=abc123&state=not-the-one", follow_redirects=False
    )

    assert response.headers["location"] == "/settings?github=failed&detail=state_mismatch"
    assert github_http.requests == []  # never even tried to exchange it
    async with db() as session:
        assert await settings_store.get(session, settings_store.GITHUB_TOKEN) is None


async def test_the_callback_without_a_cookie_is_refused(client, db):
    await configure_app(db)

    response = await client.get(
        "/api/github/callback?code=abc123&state=whatever", follow_redirects=False
    )

    assert response.headers["location"] == "/settings?github=failed&detail=state_mismatch"


async def test_a_cancelled_authorization_comes_back_saying_so(client, db):
    await configure_app(db)

    response = await client.get(
        "/api/github/callback?error=access_denied", follow_redirects=False
    )

    assert response.headers["location"] == "/settings?github=failed&detail=access_denied"


async def test_a_failed_exchange_does_not_look_like_success(client, db, github_http):
    await configure_app(db)
    github_http.routes["login/oauth/access_token"] = FakeResponse(
        {"error": "bad_verification_code"}
    )
    started = await client.get("/api/github/authorize", follow_redirects=False)
    state = parse_qs(urlparse(started.headers["location"]).query)["state"][0]

    response = await client.get(
        f"/api/github/callback?code=stale&state={state}", follow_redirects=False
    )

    assert response.headers["location"] == "/settings?github=failed&detail=exchange_failed"
    async with db() as session:
        assert await settings_store.get(session, settings_store.GITHUB_TOKEN) is None


async def test_disconnecting_forgets_the_token(client, db):
    await connect(db)

    assert (await client.delete("/api/github/connection")).status_code == 204
    async with db() as session:
        assert await settings_store.get(session, settings_store.GITHUB_TOKEN) is None


# --- what the token is for --------------------------------------------------


async def test_repos_need_a_connection(client):
    response = await client.get("/api/github/repos")

    assert response.status_code == 503
    assert "not connected" in response.json()["detail"]


async def test_repos_lists_the_owner_s_repos(client, db, github_http, monkeypatch):
    monkeypatch.setattr(config, "OIDC_OWNER", "example-owner")
    await connect(db)
    github_http.routes["/user/repos"] = FakeResponse(
        [
            {"full_name": "example-owner/blog", "default_branch": "trunk", "private": True},
            {"full_name": "someone-else/thing", "default_branch": "main"},
        ]
    )

    body = (await client.get("/api/github/repos")).json()

    assert body["repos"] == [
        {"full_name": "example-owner/blog", "default_branch": "trunk", "private": True}
    ]


async def test_branches_need_a_connection(client):
    response = await client.get("/api/github/branches?repo=example-owner/blog")

    assert response.status_code == 503


async def test_branches_lists_the_repo_s_branches(client, db, github_http):
    await connect(db)
    github_http.routes["/repos/example-owner/blog/branches"] = FakeResponse(
        [{"name": "main"}, {"name": "release"}]
    )

    body = (await client.get("/api/github/branches?repo=example-owner/blog")).json()

    assert body["branches"] == ["main", "release"]


async def test_branches_for_an_unknown_repo_is_404(client, db, github_http):
    await connect(db)
    github_http.routes["/branches"] = FakeResponse({"message": "Not Found"}, 404)

    response = await client.get("/api/github/branches?repo=example-owner/gone")

    assert response.status_code == 404
    assert 'no repo "example-owner/gone"' in response.json()["detail"]


async def test_branches_rejects_a_malformed_repo(client, db, github_http):
    await connect(db)

    response = await client.get("/api/github/branches?repo=../../user")

    assert response.status_code == 502
    assert "not a valid owner/repo" in response.json()["detail"]
    assert github_http.requests == []


async def test_a_github_outage_is_a_502_not_a_500(client, db, github_http):
    await connect(db)
    # No route matches, so the fake raises a connection error like the real one

    response = await client.get("/api/github/repos")

    assert response.status_code == 502
    assert "could not reach GitHub" in response.json()["detail"]
