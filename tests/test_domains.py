"""Multiple domains: the available list, and creating a project on the primary
or a configured extra domain (or rejecting an unconfigured one)."""

import pytest

from console import config, domains, settings_store


async def test_available_primary_only(db):
    async with db() as session:
        assert await domains.available(session) == [config.DOMAIN]


async def test_available_with_extras_deduped(db):
    async with db() as session:
        await settings_store.set_value(
            session, settings_store.DOMAINS, "b.com, a.com , b.com"
        )
        await session.commit()
    async with db() as session:
        assert await domains.available(session) == [config.DOMAIN, "b.com", "a.com"]


async def test_list_domains_endpoint(client):
    res = await client.get("/api/projects/domains")
    assert res.status_code == 200
    assert res.json() == {"domains": [config.DOMAIN]}


async def test_create_defaults_to_primary_domain(client):
    res = await client.post(
        "/api/projects",
        json={"name": "a", "repo": "sam-stuhl/a", "subdomain": "app-a"},
    )
    assert res.status_code == 201
    body = res.json()
    assert body["domain"] == config.DOMAIN
    assert body["url"] == f"https://app-a.{config.DOMAIN}"


async def test_create_on_configured_extra_domain(client):
    await client.put("/api/domains", json={"extras": ["apps.example.com"]})
    res = await client.post(
        "/api/projects",
        json={
            "name": "b",
            "repo": "sam-stuhl/b",
            "subdomain": "app-b",
            "domain": "apps.example.com",
        },
    )
    assert res.status_code == 201
    body = res.json()
    assert body["domain"] == "apps.example.com"
    assert body["url"] == "https://app-b.apps.example.com"


async def test_create_rejects_unconfigured_domain(client):
    res = await client.post(
        "/api/projects",
        json={
            "name": "c",
            "repo": "sam-stuhl/c",
            "subdomain": "app-c",
            "domain": "nope.example.com",
        },
    )
    assert res.status_code == 400
    assert "not configured" in res.json()["detail"]


# --- the readable domains API ---------------------------------------------


async def test_domains_api_get_and_put(client):
    res = await client.get("/api/domains")
    assert res.json() == {"primary": config.DOMAIN, "extras": []}

    put = await client.put(
        "/api/domains",
        json={"extras": ["a.com", " a.com ", "b.com", config.DOMAIN, ""]},
    )
    # deduped, trimmed, primary and blanks dropped
    assert put.status_code == 200
    assert put.json() == {"primary": config.DOMAIN, "extras": ["a.com", "b.com"]}

    # persisted and reflected by the create-form endpoint too
    assert (await client.get("/api/domains")).json()["extras"] == ["a.com", "b.com"]
    listed = (await client.get("/api/projects/domains")).json()["domains"]
    assert listed == [config.DOMAIN, "a.com", "b.com"]


async def test_domains_api_put_rejects_malformed(client):
    bad = await client.put("/api/domains", json={"extras": ["not a domain"]})
    assert bad.status_code == 400
    assert (await client.get("/api/domains")).json()["extras"] == []


async def test_domains_api_put_empty_clears(client):
    await client.put("/api/domains", json={"extras": ["a.com"]})
    cleared = await client.put("/api/domains", json={"extras": []})
    assert cleared.json()["extras"] == []
    assert (await client.get("/api/domains")).json()["extras"] == []


async def test_settings_api_no_longer_manages_domains(client):
    # domains moved off the write-only settings API to its own readable one
    res = await client.put("/api/settings/domains", json={"value": "x.com"})
    assert res.status_code == 404


# --- changing a project's domain ------------------------------------------


class FakeAccess:
    """Records Cloudflare Access calls without touching the network."""

    calls: list[tuple] = []

    def __init__(self, token, account_id):
        pass

    async def reconcile(self, hostname, protected, emails, cf_app_id):
        FakeAccess.calls.append(("reconcile", hostname, protected, tuple(emails), cf_app_id))
        return "new-app-id"

    async def delete_app(self, cf_app_id):
        FakeAccess.calls.append(("delete", cf_app_id))


@pytest.fixture
def fake_cf(monkeypatch):
    from console import cloudflare

    FakeAccess.calls = []

    async def fake_resolve(session):
        return ("tok", "acct")

    monkeypatch.setattr(cloudflare, "resolve_credentials", fake_resolve)
    monkeypatch.setattr(cloudflare, "Access", FakeAccess)
    return FakeAccess


async def _make_project(client, **over):
    body = {"name": "app", "repo": "sam-stuhl/app", "subdomain": "app", **over}
    res = await client.post("/api/projects", json=body)
    assert res.status_code == 201
    return res.json()["id"]


async def _protect(db, project_id):
    from console.db.models import Project

    async with db() as session:
        project = await session.get(Project, project_id)
        project.protected = True
        project.cf_app_id = "old-app"
        project.access_emails = "me@example.com"
        await session.commit()


async def test_change_domain_public_no_cf(client, db, fake_cf):
    await client.put("/api/domains", json={"extras": ["apps.example.com"]})
    pid = await _make_project(client)

    res = await client.put(f"/api/projects/{pid}/domain", json={"domain": "apps.example.com"})
    assert res.status_code == 200
    body = res.json()
    assert body["project"]["domain"] == "apps.example.com"
    assert body["project"]["url"] == "https://app.apps.example.com"
    assert body["redeploy_required"] is True
    assert fake_cf.calls == []  # public app: Cloudflare untouched


async def test_change_domain_rejects_unconfigured(client, fake_cf):
    pid = await _make_project(client)
    res = await client.put(f"/api/projects/{pid}/domain", json={"domain": "nope.example.com"})
    assert res.status_code == 400
    assert "not configured" in res.json()["detail"]


async def test_change_domain_noop_when_same(client, fake_cf):
    pid = await _make_project(client)
    res = await client.put(f"/api/projects/{pid}/domain", json={"domain": None})
    assert res.status_code == 200
    assert res.json()["redeploy_required"] is False
    assert fake_cf.calls == []


async def test_change_domain_protected_auto_repoints(client, db, fake_cf):
    await client.put("/api/domains", json={"extras": ["apps.example.com"]})
    pid = await _make_project(client)
    await _protect(db, pid)

    res = await client.put(
        f"/api/projects/{pid}/domain",
        json={"domain": "apps.example.com", "repoint": "auto"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["project"]["domain"] == "apps.example.com"
    assert "Moved the Cloudflare Access gate" in body["note"]
    # created the new-hostname gate first, then deleted the old one
    assert fake_cf.calls == [
        ("reconcile", "app.apps.example.com", True, ("me@example.com",), None),
        ("delete", "old-app"),
    ]
    # cf_app_id now points at the freshly created app
    from console.db.models import Project

    async with db() as session:
        assert (await session.get(Project, pid)).cf_app_id == "new-app-id"


async def test_change_domain_protected_manual_leaves_cf(client, db, fake_cf):
    await client.put("/api/domains", json={"extras": ["apps.example.com"]})
    pid = await _make_project(client)
    await _protect(db, pid)

    res = await client.put(
        f"/api/projects/{pid}/domain",
        json={"domain": "apps.example.com", "repoint": "manual"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["project"]["domain"] == "apps.example.com"
    assert "Move it in Cloudflare" in body["note"]
    assert fake_cf.calls == []  # manual: console does not touch Cloudflare

    from console.db.models import Project

    async with db() as session:
        assert (await session.get(Project, pid)).cf_app_id == "old-app"
