import pytest

from conftest import FakeResponse
from console import config, settings_store

pytestmark = pytest.mark.usefixtures("github_http")


async def connect(db, token="gho_abc"):
    async with db() as session:
        await settings_store.set_value(session, settings_store.GITHUB_TOKEN, token)
        await session.commit()


async def test_status_when_nothing_is_connected(client):
    response = await client.get("/api/github/status")

    assert response.status_code == 200
    assert response.json() == {
        "client_configured": True,
        "connected": False,
        "login": None,
        "error": None,
    }


async def test_status_reports_the_connected_account(client, db, github_http):
    await connect(db)
    github_http.routes["/user"] = FakeResponse({"login": "example-owner"})

    body = (await client.get("/api/github/status")).json()

    assert body["connected"] is True
    assert body["login"] == "example-owner"
    assert body["error"] is None


async def test_status_surfaces_a_revoked_token(client, db, github_http):
    await connect(db)
    github_http.routes["/user"] = FakeResponse({"message": "Bad credentials"}, 401)

    body = (await client.get("/api/github/status")).json()

    # Still "connected" (a token is stored), but it no longer works, and the
    # UI needs to say so rather than fail later at the point of use.
    assert body["connected"] is True
    assert body["login"] is None
    assert "Reconnect" in body["error"]


async def test_status_without_a_client_id_says_so(client, monkeypatch):
    monkeypatch.setattr(config, "GITHUB_CLIENT_ID", "")

    assert (await client.get("/api/github/status")).json()["client_configured"] is False


async def test_a_client_id_saved_in_settings_is_enough(client, db, monkeypatch):
    # The whole point: set it up in the browser, no file on the box to edit and
    # no restart.
    monkeypatch.setattr(config, "GITHUB_CLIENT_ID", "")

    assert (
        await client.put(
            "/api/settings/github_client_id", json={"value": "Iv1.saved"}
        )
    ).status_code == 204

    assert (await client.get("/api/github/status")).json()["client_configured"] is True


async def test_device_start_needs_a_client_id(client, monkeypatch):
    monkeypatch.setattr(config, "GITHUB_CLIENT_ID", "")

    response = await client.post("/api/github/device")

    assert response.status_code == 503
    assert "Settings" in response.json()["detail"]


async def test_device_start_hands_the_code_to_the_browser(client, github_http):
    github_http.routes["login/device/code"] = FakeResponse(
        {
            "device_code": "dev-123",
            "user_code": "ABCD-1234",
            "verification_uri": "https://github.com/login/device",
            "interval": 5,
            "expires_in": 900,
        }
    )

    body = (await client.post("/api/github/device")).json()

    assert body["user_code"] == "ABCD-1234"
    assert body["device_code"] == "dev-123"


async def test_polling_while_the_user_is_still_approving(client, db, github_http):
    github_http.routes["access_token"] = FakeResponse({"error": "authorization_pending"})

    response = await client.post(
        "/api/github/device/poll", json={"device_code": "dev-123"}
    )

    assert response.json() == {"status": "pending"}
    async with db() as session:
        assert await settings_store.get(session, settings_store.GITHUB_TOKEN) is None


async def test_a_successful_poll_stores_the_token(client, db, github_http):
    github_http.routes["access_token"] = FakeResponse({"access_token": "gho_new"})

    response = await client.post(
        "/api/github/device/poll", json={"device_code": "dev-123"}
    )

    assert response.json() == {"status": "connected"}
    async with db() as session:
        assert await settings_store.get(session, settings_store.GITHUB_TOKEN) == "gho_new"


async def test_disconnecting_forgets_the_token(client, db):
    await connect(db)

    assert (await client.delete("/api/github/connection")).status_code == 204
    async with db() as session:
        assert await settings_store.get(session, settings_store.GITHUB_TOKEN) is None


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
