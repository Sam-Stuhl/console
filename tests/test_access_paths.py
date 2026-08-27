"""Bypass paths: the Cloudflare Access exception that lets a machine reach one
path without the browser login."""

import pytest

from console import access_paths, cloudflare, config
from console.db.models import AccessPath, Project

PROJECT = {"name": "logbook", "repo": "example-owner/logbook", "subdomain": "logbook"}


async def make_project(client, **over):
    res = await client.post("/api/projects", json={**PROJECT, **over})
    assert res.status_code == 201
    return res.json()["id"]


# --- what counts as a path -------------------------------------------------


def test_normalize_strips_slashes_and_whitespace():
    assert access_paths.normalize(" /api/ingest/ ", console_own=False) == "api/ingest"


def test_empty_path_refused_because_it_would_open_the_whole_host():
    with pytest.raises(ValueError, match="whole hostname"):
        access_paths.normalize("/", console_own=False)


@pytest.mark.parametrize("bad", ["api/*", "api?key=1", "https://x.com/api", "a b", "../etc"])
def test_malformed_paths_refused(bad):
    with pytest.raises(ValueError, match="not a path"):
        access_paths.normalize(bad, console_own=False)


def test_console_refuses_a_bypass_on_its_own_api():
    # /api is the SPA's write surface and has no auth of its own: Access is it.
    with pytest.raises(ValueError, match="/v1"):
        access_paths.normalize("api/projects", console_own=True)


def test_an_app_may_open_its_own_api_path():
    # The same path is fine on an app, which authenticates it itself. That is
    # the whole point of the feature.
    assert access_paths.normalize("api", console_own=False) == "api"


# --- a project's paths -----------------------------------------------------


async def test_add_creates_the_bypass_then_records_it(client, fake_cf):
    project_id = await make_project(client)

    res = await client.post(
        f"/api/projects/{project_id}/access/paths", json={"path": "/api/ingest"}
    )

    assert res.status_code == 201
    body = res.json()
    assert body["path"] == "api/ingest"
    assert body["hostname"] == f"logbook.{config.DOMAIN}"
    assert body["url"] == f"https://logbook.{config.DOMAIN}/api/ingest"
    assert fake_cf.calls == [("create", f"logbook.{config.DOMAIN}", "api/ingest")]


async def test_listing_carries_the_hostname_even_when_empty(client, fake_cf):
    project_id = await make_project(client)

    body = (await client.get(f"/api/projects/{project_id}/access/paths")).json()

    assert body == {"hostname": f"logbook.{config.DOMAIN}", "paths": []}


async def test_duplicate_path_refused(client, fake_cf):
    project_id = await make_project(client)
    await client.post(f"/api/projects/{project_id}/access/paths", json={"path": "api"})

    res = await client.post(
        f"/api/projects/{project_id}/access/paths", json={"path": "/api/"}
    )

    assert res.status_code == 400
    assert "already open" in res.json()["detail"]


async def test_cloudflare_refusal_records_nothing(client, fake_cf):
    project_id = await make_project(client)
    fake_cf.fail_create = True

    res = await client.post(
        f"/api/projects/{project_id}/access/paths", json={"path": "api"}
    )

    assert res.status_code == 502
    assert (await client.get(f"/api/projects/{project_id}/access/paths")).json()["paths"] == []


async def test_remove_closes_the_bypass(client, fake_cf):
    project_id = await make_project(client)
    path_id = (
        await client.post(f"/api/projects/{project_id}/access/paths", json={"path": "api"})
    ).json()["id"]

    res = await client.delete(f"/api/projects/{project_id}/access/paths/{path_id}")

    assert res.status_code == 204
    assert ("delete", "cf-1") in fake_cf.calls
    assert (await client.get(f"/api/projects/{project_id}/access/paths")).json()["paths"] == []


async def test_a_failed_close_keeps_the_row(client, fake_cf):
    # Dropping the row here would leave the path open with nothing saying so.
    project_id = await make_project(client)
    path_id = (
        await client.post(f"/api/projects/{project_id}/access/paths", json={"path": "api"})
    ).json()["id"]
    fake_cf.fail_delete = True

    res = await client.delete(f"/api/projects/{project_id}/access/paths/{path_id}")

    assert res.status_code == 502
    listed = (await client.get(f"/api/projects/{project_id}/access/paths")).json()
    assert [p["path"] for p in listed["paths"]] == ["api"]


async def test_another_projects_path_is_not_deletable_through_this_one(client, fake_cf):
    a = await make_project(client)
    b = await make_project(client, name="other", repo="example-owner/other", subdomain="other")
    path_id = (
        await client.post(f"/api/projects/{a}/access/paths", json={"path": "api"})
    ).json()["id"]

    res = await client.delete(f"/api/projects/{b}/access/paths/{path_id}")

    assert res.status_code == 404


# --- the console's own paths -----------------------------------------------


async def test_console_paths_use_the_console_hostname(client, fake_cf):
    res = await client.post("/api/access/paths", json={"path": "hooks"})

    assert res.status_code == 201
    assert res.json()["hostname"] == config.HOSTNAME
    assert fake_cf.calls == [("create", config.HOSTNAME, "hooks")]
    listed = (await client.get("/api/access/paths")).json()
    assert listed["hostname"] == config.HOSTNAME
    assert [p["path"] for p in listed["paths"]] == ["hooks"]


async def test_console_api_bypass_refused_over_the_api(client, fake_cf):
    res = await client.post("/api/access/paths", json={"path": "api"})

    assert res.status_code == 400
    assert "/v1" in res.json()["detail"]
    assert fake_cf.calls == []


async def test_console_and_project_paths_do_not_mix(client, fake_cf):
    project_id = await make_project(client)
    await client.post("/api/access/paths", json={"path": "hooks"})
    await client.post(f"/api/projects/{project_id}/access/paths", json={"path": "hooks"})

    console = (await client.get("/api/access/paths")).json()["paths"]
    project = (await client.get(f"/api/projects/{project_id}/access/paths")).json()["paths"]

    assert console[0]["hostname"] == config.HOSTNAME
    assert project[0]["hostname"] == f"logbook.{config.DOMAIN}"
    assert console[0]["id"] != project[0]["id"]


# --- following the hostname ------------------------------------------------


async def test_domain_change_moves_the_bypasses(client, db, fake_cf):
    project_id = await make_project(client)
    await client.put("/api/domains", json={"extras": ["elsewhere.com"]})
    await client.post(f"/api/projects/{project_id}/access/paths", json={"path": "api/ingest"})
    fake_cf.calls = []

    res = await client.put(
        f"/api/projects/{project_id}/domain",
        json={"domain": "elsewhere.com", "repoint": "auto"},
    )

    assert res.status_code == 200
    assert "Moved 1 bypass path" in res.json()["note"]
    assert ("create", "logbook.elsewhere.com", "api/ingest") in fake_cf.calls
    assert ("delete", "cf-1") in fake_cf.calls
    async with db() as session:
        row = (await access_paths.listing(session, project_id))[0]
        assert row.hostname == "logbook.elsewhere.com"
        assert row.cf_app_id == "cf-2"


async def test_a_bypass_that_cannot_move_does_not_block_the_domain_change(
    client, fake_cf
):
    project_id = await make_project(client)
    await client.put("/api/domains", json={"extras": ["elsewhere.com"]})
    await client.post(f"/api/projects/{project_id}/access/paths", json={"path": "api"})
    fake_cf.fail_create = True

    res = await client.put(
        f"/api/projects/{project_id}/domain",
        json={"domain": "elsewhere.com", "repoint": "auto"},
    )

    assert res.status_code == 200
    assert "1 could not be recreated" in res.json()["note"]
    assert (await client.get(f"/api/projects/{project_id}")).json()["domain"] == "elsewhere.com"


async def test_deleting_a_project_closes_its_bypasses(client, db, fake_cf):
    project_id = await make_project(client)
    await client.post(f"/api/projects/{project_id}/access/paths", json={"path": "api"})

    assert (await client.delete(f"/api/projects/{project_id}")).status_code == 204

    assert ("delete", "cf-1") in fake_cf.calls
    async with db() as session:
        assert await session.get(Project, project_id) is None
        assert await session.get(AccessPath, "cf-1") is None


# --- what actually goes to Cloudflare --------------------------------------


class FakeCfResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.content = b"{}"
        self.text = ""
        self.reason_phrase = ""

    @property
    def is_success(self):
        return 200 <= self.status_code < 300

    def json(self):
        return self._payload


class FakeCfClient:
    """Stands in for httpx.AsyncClient inside console.cloudflare."""

    sent: list = []
    policy_status = 200

    def __init__(self, *_a, **_kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def request(self, method, url, **kwargs):
        FakeCfClient.sent.append((method, url, kwargs.get("json")))
        if url.endswith("/policies"):
            if FakeCfClient.policy_status != 200:
                return FakeCfResponse(
                    {"success": False, "errors": [{"message": "nope"}]},
                    FakeCfClient.policy_status,
                )
            return FakeCfResponse({"success": True, "result": {"id": "pol-1"}})
        return FakeCfResponse({"success": True, "result": {"id": "app-1"}})


@pytest.fixture
def fake_cf_http(monkeypatch):
    FakeCfClient.sent = []
    FakeCfClient.policy_status = 200
    monkeypatch.setattr(cloudflare.httpx, "AsyncClient", FakeCfClient)
    return FakeCfClient


async def test_bypass_app_is_registered_on_the_path_not_the_host(fake_cf_http):
    # The path in `domain` is what makes Cloudflare prefer this app over the
    # login gate on the bare hostname. Without it the bypass would open the
    # whole site.
    app_id = await cloudflare.Access("tok", "acct").create_bypass(
        "logbook.example.com", "api/ingest"
    )

    assert app_id == "app-1"
    create, policy = fake_cf_http.sent
    assert create[2]["domain"] == "logbook.example.com/api/ingest"
    assert create[2]["type"] == "self_hosted"
    assert policy[2]["decision"] == "bypass"
    assert policy[2]["include"] == [{"everyone": {}}]


async def test_a_policyless_app_is_cleaned_up(fake_cf_http):
    # An app whose bypass policy never attached denies everyone, which is the
    # opposite of what was asked for, so it must not be left behind.
    fake_cf_http.policy_status = 403

    with pytest.raises(cloudflare.AccessApiError):
        await cloudflare.Access("tok", "acct").create_bypass("app.example.com", "api")

    assert [method for method, _url, _body in fake_cf_http.sent] == [
        "POST",
        "POST",
        "DELETE",
    ]
