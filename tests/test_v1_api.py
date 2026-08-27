"""The machine-facing /v1 surface.

/v1 is reached through a Cloudflare Access Bypass rule, so it is the one path in
this app exposed to the raw internet with only its own token check in front. The
tests that matter most are therefore the negative ones: no token, a bad token, a
read-only token reaching a write, and secret values leaking.

The write-scope test enumerates every write route deliberately. A new write
endpoint added without a scope dependency should fail here rather than ship."""

import docker.errors
import pytest

from console import config, tokens
from console.db.models import Deployment, Project, Secret, utcnow
from console.secrets import crypto
from console.v1.rest import router as v1_router


@pytest.fixture
async def mint(client):
    """Mint a token through the real endpoint and return its auth header."""

    async def _mint(scope=tokens.READ, name="test"):
        res = await client.post("/api/tokens", json={"name": name, "scope": scope})
        assert res.status_code == 201
        return {"Authorization": f"Bearer {res.json()['token']}"}

    return _mint


@pytest.fixture
async def read_auth(mint):
    return await mint(tokens.READ, "reader")


@pytest.fixture
async def write_auth(mint):
    return await mint(tokens.WRITE, "writer")


@pytest.fixture
async def project(db):
    async with db() as session:
        row = Project(name="Blog", repo="owner/blog", subdomain="blog")
        session.add(row)
        await session.commit()
        return {"id": row.id, "name": row.name, "subdomain": row.subdomain}


class FakeContainer:
    def __init__(self, cid, name, labels, status="running"):
        self.id = cid
        self.name = name
        self.labels = labels
        self.status = status
        self.calls: list[str] = []
        self.log_text = "boot ok\nserving on :8000\n"

    @property
    def attrs(self):
        return {
            "Id": self.id,
            "Name": "/" + self.name,
            "Config": {"Image": "ghcr.io/owner/blog:abc1234"},
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

    def logs(self, tail=None, timestamps=False):
        return self.log_text.encode()

    def stats(self, stream=False):
        return {}


class FakeContainers:
    def __init__(self, items):
        self.items = items

    def list(self, all=False, filters=None):
        key, _, value = filters["label"].partition("=")
        found = [c for c in self.items if c.labels.get(key) == value]
        return found if all else [c for c in found if c.status == "running"]

    def get(self, cid):
        for c in self.items:
            if c.id == cid:
                return c
        raise docker.errors.NotFound(f"no such container: {cid}")


class FakeDocker:
    def __init__(self):
        self.items: list[FakeContainer] = []
        self.containers = FakeContainers(self.items)

    def add(self, project_id, *, status="running", cid="c1", name="blog-abc1234"):
        self.items.append(
            FakeContainer(cid, name, {"console.project": project_id}, status)
        )


@pytest.fixture(autouse=True)
def fake_docker(monkeypatch):
    """Autouse so no /v1 test can silently reach a real Docker daemon: without
    it these would pass only on a machine that happens to be running one."""
    fd = FakeDocker()
    monkeypatch.setattr("console.docker.containers.get_client", lambda: fd)
    monkeypatch.setattr("console.v1.service.get_client", lambda: fd)
    return fd


# Every route that changes something, as (method, path). Kept explicit so an
# unguarded new write endpoint shows up as a failing test.
WRITE_ROUTES = [
    ("post", "/v1/projects"),
    ("post", "/v1/projects/blog/deployments"),
    ("delete", "/v1/projects/blog"),
    ("put", "/v1/projects/blog/access"),
    ("put", "/v1/projects/blog/domain"),
    ("post", "/v1/projects/blog/deployments/d1/rollback"),
    ("post", "/v1/projects/blog/deployments/d1/redeploy"),
    ("post", "/v1/projects/blog/controls/restart"),
    ("post", "/v1/projects/blog/commands"),
    ("post", "/v1/projects/blog/access/paths"),
    ("post", "/v1/projects/blog/access/paths/adopt"),
    ("delete", "/v1/projects/blog/access/paths/api"),
    ("post", "/v1/access/paths"),
    ("post", "/v1/access/paths/adopt"),
    ("delete", "/v1/access/paths/api"),
    ("post", "/v1/backups"),
]

READ_ROUTES = [
    "/v1/system",
    "/v1/projects",
    "/v1/projects/blog",
    "/v1/projects/blog/deployments",
    "/v1/projects/blog/container",
    "/v1/projects/blog/secrets",
    "/v1/projects/blog/commands",
    "/v1/projects/blog/access/paths",
    "/v1/access/paths",
    "/v1/backups",
]

# Reads that call Cloudflare, so without credentials they answer 503 rather than
# 200. They still need a token, so they ride the auth test but not the
# happy-path one; test_a_read_token_can_discover covers them with a fake.
CLOUDFLARE_READ_ROUTES = [
    "/v1/projects/blog/access/paths/unmanaged",
    "/v1/access/paths/unmanaged",
]


# ------------------------------------------------------------------- auth


@pytest.mark.parametrize("path", READ_ROUTES + CLOUDFLARE_READ_ROUTES)
async def test_reads_require_a_token(client, project, path):
    assert (await client.get(path)).status_code == 401


@pytest.mark.parametrize("method,path", WRITE_ROUTES)
async def test_writes_require_a_token(client, project, method, path):
    # request() rather than the per-verb helpers: httpx's delete() takes no body.
    res = await client.request(method, path, json={})
    assert res.status_code == 401


async def test_a_bad_token_is_rejected(client, project):
    for header in (
        "Bearer csk_not-a-real-token",
        "Bearer ",
        "Basic csk_whatever",
        "csk_no-scheme",
    ):
        res = await client.get("/v1/system", headers={"Authorization": header})
        assert res.status_code == 401, header


async def test_rejection_does_not_say_which_check_failed(client):
    """A caller learns "invalid token" and nothing more: which check failed is
    useful to an attacker and useless to a legitimate client."""
    missing = await client.get("/v1/system")
    wrong = await client.get(
        "/v1/system", headers={"Authorization": "Bearer csk_wrong"}
    )
    assert missing.json()["detail"] == wrong.json()["detail"] == "invalid token"


async def test_a_revoked_token_stops_working(client, project):
    created = (await client.post("/api/tokens", json={"name": "t"})).json()
    auth = {"Authorization": f"Bearer {created['token']}"}
    assert (await client.get("/v1/system", headers=auth)).status_code == 200

    await client.delete(f"/api/tokens/{created['id']}")
    assert (await client.get("/v1/system", headers=auth)).status_code == 401


# ------------------------------------------------------------------ scope


@pytest.mark.parametrize("method,path", WRITE_ROUTES)
async def test_a_read_token_cannot_write(client, project, read_auth, method, path):
    res = await client.request(method, path, json={}, headers=read_auth)
    assert res.status_code == 403, f"{method} {path} was reachable with a read token"
    assert "read-only" in res.json()["detail"]


@pytest.mark.parametrize("path", READ_ROUTES)
async def test_a_read_token_can_read(client, project, read_auth, path):
    assert (await client.get(path, headers=read_auth)).status_code == 200


async def test_a_write_token_can_also_read(client, project, write_auth):
    assert (await client.get("/v1/system", headers=write_auth)).status_code == 200


async def test_every_write_route_is_covered_by_the_scope_test():
    """Guards the list above: a write endpoint added without a matching entry
    in WRITE_ROUTES would otherwise never be scope-tested."""
    declared = {
        (method.lower(), route.path)
        for route in v1_router.routes
        for method in route.methods
        if method in ("POST", "PUT", "DELETE", "PATCH")
    }
    tested = {
        (method, path.replace("blog", "{project}").replace("d1", "{deployment_id}"))
        for method, path in WRITE_ROUTES
    }
    # /v1/projects/{project}/controls/{action} is tested via a concrete action.
    tested = {(m, p.replace("/restart", "/{action}")) for m, p in tested}
    # A bypass path is addressed by the path itself, so it is tested with one.
    tested = {
        (m, p.replace("/access/paths/api", "/access/paths/{path:path}"))
        for m, p in tested
    }
    assert declared == tested, f"untested writes: {declared - tested}"


# ------------------------------------------------------------------ reads


async def test_project_is_addressable_by_id_name_and_subdomain(
    client, project, read_auth
):
    for ref in (project["id"], project["name"], project["subdomain"]):
        res = await client.get(f"/v1/projects/{ref}", headers=read_auth)
        assert res.status_code == 200, ref
        assert res.json()["id"] == project["id"]


async def test_project_lookup_by_name_is_case_insensitive(client, project, read_auth):
    res = await client.get("/v1/projects/BLOG", headers=read_auth)
    assert res.status_code == 200
    assert res.json()["id"] == project["id"]


async def test_an_unknown_project_is_404(client, read_auth):
    res = await client.get("/v1/projects/nope", headers=read_auth)
    assert res.status_code == 404
    assert "nope" in res.json()["detail"]


async def test_project_carries_its_url_and_status(client, project, read_auth):
    body = (await client.get("/v1/projects/blog", headers=read_auth)).json()
    assert body["url"].startswith("https://blog.")
    assert body["health"] == "unknown"
    assert body["is_live"] is False
    assert body["deploy_status"] is None


async def test_system_summarizes_the_install(client, project, read_auth):
    body = (await client.get("/v1/system", headers=read_auth)).json()
    assert body["projects"] == 1
    assert body["live"] == 0
    assert body["primary_domain"] in body["domains"]
    assert isinstance(body["credentials"], list)


async def test_deployments_are_listed_newest_first(client, db, project, read_auth):
    async with db() as session:
        for sha in ("aaa", "bbb"):
            session.add(
                Deployment(project_id=project["id"], sha=sha, status="superseded")
            )
        await session.commit()

    body = (await client.get("/v1/projects/blog/deployments", headers=read_auth)).json()
    assert [d["sha"] for d in body] == ["bbb", "aaa"]
    assert body[0]["project_id"] == project["id"]


async def test_a_deployment_from_another_project_is_404(client, db, project, read_auth):
    async with db() as session:
        other = Project(name="Other", repo="owner/other", subdomain="other")
        session.add(other)
        await session.commit()
        stray = Deployment(project_id=other.id, sha="ccc", status="live")
        session.add(stray)
        await session.commit()
        stray_id = stray.id

    res = await client.get(
        f"/v1/projects/blog/deployments/{stray_id}", headers=read_auth
    )
    assert res.status_code == 404


# ---------------------------------------------------------------- secrets


async def test_secrets_expose_names_but_never_values(client, db, project, read_auth):
    async with db() as session:
        session.add(
            Secret(
                project_id=project["id"],
                key="DATABASE_URL",
                value_encrypted=crypto.encrypt("postgres://super-secret-value"),
            )
        )
        await session.commit()

    res = await client.get("/v1/projects/blog/secrets", headers=read_auth)
    assert res.status_code == 200
    assert [s["key"] for s in res.json()] == ["DATABASE_URL"]
    assert "super-secret-value" not in res.text


async def test_there_is_no_route_that_reveals_a_secret_value():
    """The exclusion is structural: no endpoint exists, rather than an endpoint
    existing behind a check that could be got wrong."""
    paths = {r.path for r in v1_router.routes}
    assert "/v1/projects/{project}/secrets" in paths
    for path in paths:
        assert "reveal" not in path
        assert "export" not in path
        assert "import" not in path
        assert "terminal" not in path
    # The only secrets route is the read-only listing.
    secret_routes = {
        (m, r.path) for r in v1_router.routes for m in r.methods if "secrets" in r.path
    }
    assert secret_routes == {("GET", "/v1/projects/{project}/secrets")}


async def test_no_settings_or_token_routes_are_exposed():
    """Credential material and token minting stay behind Cloudflare Access. A
    token that could mint another token would make one leak permanent."""
    for route in v1_router.routes:
        assert "settings" not in route.path
        assert "tokens" not in route.path


# ----------------------------------------------------------------- writes


async def test_write_token_can_create_and_delete_a_project(client, write_auth):
    res = await client.post(
        "/v1/projects",
        json={"name": "Docs", "repo": "owner/docs", "subdomain": "docs"},
        headers=write_auth,
    )
    assert res.status_code == 201
    assert res.json()["subdomain"] == "docs"

    assert (
        await client.delete("/v1/projects/docs", headers=write_auth)
    ).status_code == 204
    assert (await client.get("/v1/projects/docs", headers=write_auth)).status_code == 404


async def test_create_rejects_a_malformed_repo(client, write_auth):
    res = await client.post(
        "/v1/projects",
        json={"name": "Bad", "repo": "not-a-repo", "subdomain": "bad"},
        headers=write_auth,
    )
    assert res.status_code == 400
    assert "owner/name" in res.json()["detail"]


async def test_create_rejects_a_duplicate_subdomain(client, project, write_auth):
    res = await client.post(
        "/v1/projects",
        json={"name": "Other", "repo": "owner/other", "subdomain": "blog"},
        headers=write_auth,
    )
    assert res.status_code == 409


async def test_rollback_refuses_a_build_that_never_served(
    client, db, project, write_auth
):
    async with db() as session:
        never_ran = Deployment(
            project_id=project["id"], sha="aaa", status="superseded", image="img"
        )
        session.add(never_ran)
        await session.commit()
        target = never_ran.id

    res = await client.post(
        f"/v1/projects/blog/deployments/{target}/rollback", headers=write_auth
    )
    assert res.status_code == 400
    assert "served traffic" in res.json()["detail"]


async def test_rollback_refuses_the_live_build(client, db, project, write_auth):
    async with db() as session:
        live = Deployment(
            project_id=project["id"],
            sha="aaa",
            status="live",
            image="img",
            config_snapshot="{}",
            deploy_started_at=utcnow(),
        )
        session.add(live)
        await session.commit()
        target = live.id

    res = await client.post(
        f"/v1/projects/blog/deployments/{target}/rollback", headers=write_auth
    )
    assert res.status_code == 409
    assert "already live" in res.json()["detail"]


async def test_redeploy_refuses_an_in_flight_deployment(
    client, db, project, write_auth
):
    async with db() as session:
        building = Deployment(project_id=project["id"], sha="aaa", status="building")
        session.add(building)
        await session.commit()
        target = building.id

    res = await client.post(
        f"/v1/projects/blog/deployments/{target}/redeploy", headers=write_auth
    )
    assert res.status_code == 409
    assert "still in progress" in res.json()["detail"]


async def test_control_rejects_an_unknown_action(client, project, write_auth):
    res = await client.post("/v1/projects/blog/controls/explode", headers=write_auth)
    assert res.status_code == 400
    assert "unknown action" in res.json()["detail"]


# ------------------------------------------------------- container and logs


async def test_container_is_absent_when_nothing_runs(client, project, read_auth):
    body = (await client.get("/v1/projects/blog/container", headers=read_auth)).json()
    assert body["state"] == "absent"
    assert body["name"] is None


async def test_container_reports_a_running_app(
    client, project, read_auth, fake_docker
):
    fake_docker.add(project["id"], status="running")
    body = (await client.get("/v1/projects/blog/container", headers=read_auth)).json()
    assert body["state"] == "running"
    assert body["name"] == "blog-abc1234"
    assert body["image"] == "ghcr.io/owner/blog:abc1234"


async def test_logs_come_back_for_a_stopped_container(
    client, project, read_auth, fake_docker
):
    """A crashed app's logs are exactly what a caller diagnosing an outage
    needs, so a stopped container must still be reachable."""
    fake_docker.add(project["id"], status="exited")
    res = await client.get("/v1/projects/blog/logs?tail=50", headers=read_auth)
    assert res.status_code == 200
    assert "serving on :8000" in res.json()["logs"]
    assert res.json()["container"] == "blog-abc1234"


async def test_logs_are_a_conflict_when_nothing_was_ever_deployed(
    client, project, read_auth
):
    res = await client.get("/v1/projects/blog/logs", headers=read_auth)
    assert res.status_code == 409
    assert "deploy it first" in res.json()["detail"]


async def test_logs_reject_an_out_of_range_tail(client, project, read_auth):
    assert (
        await client.get("/v1/projects/blog/logs?tail=0", headers=read_auth)
    ).status_code == 422


async def test_control_drives_the_container(client, project, write_auth, fake_docker):
    fake_docker.add(project["id"], status="running")
    res = await client.post("/v1/projects/blog/controls/restart", headers=write_auth)
    assert res.status_code == 200
    assert fake_docker.items[0].calls == ["restart"]


async def test_control_refuses_mid_deploy(client, project, write_auth, fake_docker):
    """Two containers means a deploy is in flight; acting could hit the wrong
    one, so the console refuses rather than guess."""
    fake_docker.add(project["id"], cid="c1")
    fake_docker.add(project["id"], cid="c2", name="blog-def5678")
    res = await client.post("/v1/projects/blog/controls/stop", headers=write_auth)
    assert res.status_code == 409
    assert "deploy is in progress" in res.json()["detail"]


async def test_run_command_refuses_when_the_app_is_not_running(
    client, project, write_auth
):
    res = await client.post(
        "/v1/projects/blog/commands", json={"command": "ls"}, headers=write_auth
    )
    assert res.status_code == 409
    assert "not running" in res.json()["detail"]


async def test_run_command_rejects_a_blank_command(client, project, write_auth):
    res = await client.post(
        "/v1/projects/blog/commands", json={"command": "   "}, headers=write_auth
    )
    assert res.status_code == 400


async def test_deploy_image_rejects_a_malformed_image(client, project, write_auth):
    res = await client.post(
        "/v1/projects/blog/deployments",
        json={"image": "no-tag-here", "console_toml": "[app]\nname = 'blog'\n"},
        headers=write_auth,
    )
    assert res.status_code == 400


async def test_deploy_image_refuses_an_untrusted_namespace(
    client, project, write_auth, monkeypatch
):
    """The image namespace is pinned to the configured owner, so /v1 cannot be
    used to make the console pull a stranger's image."""
    monkeypatch.setattr(config, "OIDC_OWNER", "example-owner")
    res = await client.post(
        "/v1/projects/blog/deployments",
        json={"image": "ghcr.io/someone-else/blog:abc1234"},
        headers=write_auth,
    )
    assert res.status_code == 400


async def test_deploy_image_queues_with_a_pasted_toml(
    client, project, write_auth, monkeypatch
):
    """A pasted console.toml is the path that does not need GitHub, so this
    exercises the whole queueing path without a network stub."""
    monkeypatch.setattr(config, "OIDC_OWNER", "owner")
    toml = '[app]\nname = "blog"\nsubdomain = "blog"\nport = 8000\n'
    res = await client.post(
        "/v1/projects/blog/deployments",
        json={"image": "ghcr.io/owner/blog:abc1234", "console_toml": toml},
        headers=write_auth,
    )
    assert res.status_code == 202, res.text
    assert res.json()["status"] == "queued"

    listed = (await client.get("/v1/projects/blog/deployments", headers=write_auth)).json()
    assert listed[0]["sha"] == "abc1234"
    assert listed[0]["image"] == "ghcr.io/owner/blog:abc1234"


async def test_project_carries_an_image_hint(client, project, read_auth):
    """So a caller can build the image ref for a deploy without guessing."""
    body = (await client.get("/v1/projects/blog", headers=read_auth)).json()
    assert body["image_hint"] == "ghcr.io/owner/blog:"


async def test_backup_trigger_reports_when_unconfigured(client, write_auth):
    res = await client.post("/v1/backups", headers=write_auth)
    assert res.status_code == 400
    assert "not configured" in res.json()["detail"]


# ------------------------------------------------------------------- spec


async def test_the_spec_is_public_and_describes_only_v1(client):
    """Un-gated on purpose: it describes shape, not data, so an agent can be
    pointed straight at it."""
    res = await client.get("/v1/openapi.json")
    assert res.status_code == 200
    spec = res.json()
    assert spec["info"]["version"] == "1"
    assert spec["paths"]
    assert all(path.startswith("/v1") for path in spec["paths"])


async def test_the_docs_page_renders(client):
    res = await client.get("/v1/docs")
    assert res.status_code == 200
    assert "swagger" in res.text.lower()


# --------------------------------------------------------- access paths


async def test_open_and_list_a_bypass_path(client, project, write_auth, fake_cf):
    opened = await client.post(
        f"/v1/projects/{project['subdomain']}/access/paths",
        json={"path": "/api/ingest"},
        headers=write_auth,
    )

    assert opened.status_code == 201
    assert opened.json()["adopted"] is False
    body = opened.json()["path"]
    assert body["path"] == "api/ingest"
    assert body["url"] == f"https://blog.{config.DOMAIN}/api/ingest"
    assert ("create", f"blog.{config.DOMAIN}", "api/ingest") in fake_cf.calls

    listed = await client.get(
        f"/v1/projects/{project['name']}/access/paths", headers=write_auth
    )
    assert listed.json()["hostname"] == f"blog.{config.DOMAIN}"
    assert [p["path"] for p in listed.json()["paths"]] == ["api/ingest"]


async def test_close_a_bypass_path_by_its_path(client, project, write_auth, fake_cf):
    await client.post(
        f"/v1/projects/blog/access/paths", json={"path": "api"}, headers=write_auth
    )

    closed = await client.delete(
        "/v1/projects/blog/access/paths/api", headers=write_auth
    )

    assert closed.status_code == 204
    assert ("delete", "cf-1") in fake_cf.calls


async def test_closing_a_path_that_is_not_open_is_a_404(client, project, write_auth, fake_cf):
    res = await client.delete("/v1/projects/blog/access/paths/api", headers=write_auth)

    assert res.status_code == 404


async def test_console_scope_lives_beside_the_project_one(client, write_auth, fake_cf):
    opened = await client.post(
        "/v1/access/paths", json={"path": "hooks"}, headers=write_auth
    )

    assert opened.status_code == 201
    assert opened.json()["path"]["hostname"] == config.HOSTNAME
    assert opened.json()["path"]["project_id"] is None
    listed = await client.get("/v1/access/paths", headers=write_auth)
    assert [p["path"] for p in listed.json()["paths"]] == ["hooks"]


async def test_v1_refuses_to_open_the_consoles_own_api(client, write_auth, fake_cf):
    # The one bypass that would hand this very surface's console to anyone.
    res = await client.post(
        "/v1/access/paths", json={"path": "api"}, headers=write_auth
    )

    assert res.status_code == 400
    assert "/v1" in res.json()["detail"]
    assert fake_cf.calls == []


async def test_a_read_token_cannot_open_a_path(client, project, read_auth, fake_cf):
    res = await client.post(
        "/v1/projects/blog/access/paths", json={"path": "api"}, headers=read_auth
    )

    assert res.status_code == 403
    assert fake_cf.calls == []


async def test_an_agent_can_adopt_a_hand_made_bypass(client, project, write_auth, fake_cf):
    fake_cf.apps = {"hand-1": (f"blog.{config.DOMAIN}/api/ingest", "bypass")}

    found = (
        await client.get("/v1/projects/blog/access/paths/unmanaged", headers=write_auth)
    ).json()
    assert [(p["cf_app_id"], p["path"]) for p in found] == [("hand-1", "api/ingest")]

    adopted = await client.post(
        "/v1/projects/blog/access/paths/adopt",
        json={"cf_app_id": "hand-1"},
        headers=write_auth,
    )

    assert adopted.status_code == 201
    assert adopted.json()["path"] == "api/ingest"
    assert not [c for c in fake_cf.calls if c[0] in ("create", "delete")]
    listed = await client.get("/v1/projects/blog/access/paths", headers=write_auth)
    assert [p["path"] for p in listed.json()["paths"]] == ["api/ingest"]


async def test_a_read_token_cannot_adopt(client, project, read_auth, fake_cf):
    fake_cf.apps = {"hand-1": (f"blog.{config.DOMAIN}/api/ingest", "bypass")}

    res = await client.post(
        "/v1/projects/blog/access/paths/adopt",
        json={"cf_app_id": "hand-1"},
        headers=read_auth,
    )

    assert res.status_code == 403


async def test_deleting_a_project_over_v1_closes_its_bypasses(
    client, project, write_auth, fake_cf
):
    await client.post(
        "/v1/projects/blog/access/paths", json={"path": "api"}, headers=write_auth
    )

    assert (
        await client.delete("/v1/projects/blog", headers=write_auth)
    ).status_code == 204
    assert ("delete", "cf-1") in fake_cf.calls


@pytest.mark.parametrize("path", CLOUDFLARE_READ_ROUTES)
async def test_a_read_token_can_discover(client, project, read_auth, fake_cf, path):
    """Discovery reads Cloudflare but changes nothing, here or there, so a
    read-only token is allowed to run it."""
    assert (await client.get(path, headers=read_auth)).status_code == 200
