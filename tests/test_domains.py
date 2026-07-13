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
    await client.put("/api/settings/domains", json={"value": "apps.example.com"})
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
