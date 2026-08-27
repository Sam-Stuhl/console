"""The MCP server, driven over the real streamable-HTTP protocol.

These go through the transport rather than calling the tool functions directly,
because the parts most likely to break are exactly the ones a direct call skips:
the bearer check in the ASGI wrapper, the mounted path, and the session
manager's lifespan."""

import asyncio
import json

import pytest
from httpx import ASGITransport, AsyncClient

from console import config, tokens
from console.db.models import Project, Secret
from console.db.session import get_session
from console.main import app
from console.secrets import crypto
from console.v1 import mcp as v1_mcp

PROTOCOL = "2025-06-18"
HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}


def _rpc(method, params=None, request_id=1):
    body = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        body["params"] = params
    return body


def _parse(response):
    """Unwrap a JSON-RPC result from either a plain body or an SSE frame."""
    text = response.text
    if text.startswith("event:"):
        for line in text.splitlines():
            if line.startswith("data: "):
                return json.loads(line[6:])
        raise AssertionError(f"no data frame in {text!r}")
    return response.json()


class McpClient:
    """A minimal MCP client: initialize, then call tools."""

    def __init__(self, http, auth):
        self.http = http
        self.headers = {**HEADERS, **auth}

    async def initialize(self):
        res = await self.http.post(
            "/mcp",
            json=_rpc(
                "initialize",
                {
                    "protocolVersion": PROTOCOL,
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            ),
            headers=self.headers,
        )
        if res.status_code == 200 and "mcp-session-id" in res.headers:
            self.headers["mcp-session-id"] = res.headers["mcp-session-id"]
            await self.http.post(
                "/mcp",
                json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                headers=self.headers,
            )
        return res

    async def list_tools(self):
        res = await self.http.post(
            "/mcp", json=_rpc("tools/list", {}, 2), headers=self.headers
        )
        return _parse(res)["result"]["tools"]

    async def call(self, name, arguments=None):
        res = await self.http.post(
            "/mcp",
            json=_rpc("tools/call", {"name": name, "arguments": arguments or {}}, 3),
            headers=self.headers,
        )
        return _parse(res)


@pytest.fixture
async def mcp(db, monkeypatch):
    """The real app, with a per-test MCP transport running.

    The transport is rebuilt here because a session manager can only be run
    once per instance, and the one built at import has no running lifecycle
    under the test client. McpGate resolves the transport by name, so the
    middleware already installed on the app picks up the new one.

    The tools open their own database sessions rather than taking the REST
    layer's injected one, so session_factory is pointed at the test database
    the way dependency_overrides points the routes."""
    monkeypatch.setattr(v1_mcp, "session_factory", db)
    sessions = v1_mcp.build_transport()

    async def override_session():
        async with db() as session:
            yield session

    app.dependency_overrides[get_session] = override_session

    # The manager's lifecycle is owned by one dedicated task. anyio requires a
    # cancel scope to be exited by the task that entered it, and pytest-asyncio
    # may tear a fixture down in a different task than it set it up in, so
    # wrapping the yield directly errors on teardown.
    running, stop = asyncio.Event(), asyncio.Event()

    async def _own_sessions():
        async with sessions.run():
            running.set()
            await stop.wait()

    owner = asyncio.create_task(_own_sessions())
    await running.wait()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http

    stop.set()
    await owner
    app.dependency_overrides.clear()


@pytest.fixture
async def auth(mcp):
    async def _mint(scope=tokens.READ):
        res = await mcp.post("/api/tokens", json={"name": scope, "scope": scope})
        return {"Authorization": f"Bearer {res.json()['token']}"}

    return _mint


@pytest.fixture
async def project(db):
    async with db() as session:
        row = Project(name="Blog", repo="owner/blog", subdomain="blog")
        session.add(row)
        await session.commit()
        return {"id": row.id}


# ------------------------------------------------------------------- auth


async def test_mcp_requires_a_token(mcp):
    res = await mcp.post("/mcp", json=_rpc("initialize"), headers=HEADERS)
    assert res.status_code == 401


async def test_mcp_rejects_a_bad_token(mcp):
    res = await mcp.post(
        "/mcp",
        json=_rpc("initialize"),
        headers={**HEADERS, "Authorization": "Bearer csk_nope"},
    )
    assert res.status_code == 401
    assert "invalid token" in res.text


async def test_mcp_rejects_a_non_bearer_scheme(mcp):
    res = await mcp.post(
        "/mcp",
        json=_rpc("initialize"),
        headers={**HEADERS, "Authorization": "Basic abc"},
    )
    assert res.status_code == 401


async def test_a_valid_token_initializes(mcp, auth):
    client = McpClient(mcp, await auth())
    res = await client.initialize()
    assert res.status_code == 200
    assert _parse(res)["result"]["serverInfo"]["name"] == "console"


# ------------------------------------------------------------------ tools


async def test_tools_are_listed(mcp, auth):
    client = McpClient(mcp, await auth())
    await client.initialize()
    names = {t["name"] for t in await client.list_tools()}

    assert {"get_system", "list_projects", "get_app_logs"} <= names
    assert {"rollback_deployment", "control_app", "run_command"} <= names
    # Secret values and the terminal have no tool at all, by design.
    assert not {n for n in names if "reveal" in n or "terminal" in n}
    assert "list_secret_keys" in names


async def test_every_tool_is_documented(mcp, auth):
    """An undocumented tool is one an agent will misuse."""
    client = McpClient(mcp, await auth())
    await client.initialize()
    for tool in await client.list_tools():
        assert tool.get("description"), f"{tool['name']} has no description"


async def test_read_tool_returns_real_data(mcp, auth, project):
    client = McpClient(mcp, await auth())
    await client.initialize()
    result = await client.call("list_projects")

    payload = json.dumps(result)
    assert "Blog" in payload
    assert result.get("error") is None


async def test_get_project_accepts_a_name(mcp, auth, project):
    client = McpClient(mcp, await auth())
    await client.initialize()
    result = await client.call("get_project", {"project": "Blog"})
    assert project["id"] in json.dumps(result)


async def test_a_service_error_comes_back_as_a_tool_error(mcp, auth):
    client = McpClient(mcp, await auth())
    await client.initialize()
    result = await client.call("get_project", {"project": "does-not-exist"})
    assert result["result"]["isError"] is True
    assert "does-not-exist" in json.dumps(result)


# ------------------------------------------------------------------ scope


async def test_a_read_token_cannot_use_a_write_tool(mcp, auth, project):
    client = McpClient(mcp, await auth(tokens.READ))
    await client.initialize()
    result = await client.call("control_app", {"project": "Blog", "action": "restart"})

    assert result["result"]["isError"] is True
    assert "read-only" in json.dumps(result)


async def test_a_write_token_reaches_the_write_tool(mcp, auth):
    """It gets past the scope check and fails on the real precondition (no
    container), which is what proves the gate opened."""
    client = McpClient(mcp, await auth(tokens.WRITE))
    await client.initialize()
    result = await client.call(
        "create_project",
        {"name": "Docs", "repo": "owner/docs", "subdomain": "docs"},
    )
    assert result["result"]["isError"] is not True
    assert "docs" in json.dumps(result)


async def test_secret_values_never_reach_a_tool_result(mcp, auth, db, project):
    async with db() as session:
        session.add(
            Secret(
                project_id=project["id"],
                key="DATABASE_URL",
                value_encrypted=crypto.encrypt("postgres://super-secret-value"),
            )
        )
        await session.commit()

    client = McpClient(mcp, await auth())
    await client.initialize()
    result = await client.call("list_secret_keys", {"project": "Blog"})

    payload = json.dumps(result)
    assert "DATABASE_URL" in payload
    assert "super-secret-value" not in payload


# ------------------------------------------------------------ access paths


async def test_access_path_tools_are_listed(mcp, auth):
    client = McpClient(mcp, await auth())
    await client.initialize()
    names = {t["name"] for t in await client.list_tools()}

    assert {"list_access_paths", "open_access_path", "close_access_path"} <= names


async def test_an_agent_opens_and_closes_a_path(mcp, auth, project, fake_cf):
    client = McpClient(mcp, await auth(tokens.WRITE))
    await client.initialize()

    opened = await client.call(
        "open_access_path", {"project": "Blog", "path": "/api/ingest"}
    )
    assert opened["result"]["isError"] is not True
    assert "api/ingest" in json.dumps(opened)
    assert ("create", f"blog.{config.DOMAIN}", "api/ingest") in fake_cf.calls

    listed = await client.call("list_access_paths", {"project": "Blog"})
    assert "api/ingest" in json.dumps(listed)

    closed = await client.call(
        "close_access_path", {"project": "Blog", "path": "api/ingest"}
    )
    assert closed["result"]["isError"] is not True
    assert ("delete", "cf-1") in fake_cf.calls


async def test_an_agent_cannot_open_the_consoles_own_api(mcp, auth, fake_cf):
    # Omitting project targets the console itself, where this would hand over
    # the very surface the agent is talking to.
    client = McpClient(mcp, await auth(tokens.WRITE))
    await client.initialize()

    result = await client.call("open_access_path", {"path": "api"})

    assert result["result"]["isError"] is True
    # Only the refusal itself is pinned here. Whether its REASON reaches the
    # agent is the SDK behavior the branch above this one fixes; on main a tool
    # error still comes back as a bare "Error executing tool ...".
    assert fake_cf.calls == []


async def test_a_read_token_cannot_open_a_path(mcp, auth, project, fake_cf):
    client = McpClient(mcp, await auth(tokens.READ))
    await client.initialize()

    result = await client.call("open_access_path", {"project": "Blog", "path": "api"})

    assert result["result"]["isError"] is True
    assert fake_cf.calls == []  # refused before Cloudflare was touched


async def test_an_agent_adopts_what_cloudflare_already_has(mcp, auth, project, fake_cf):
    fake_cf.apps = {"hand-1": (f"blog.{config.DOMAIN}/api/ingest", "bypass")}
    client = McpClient(mcp, await auth(tokens.WRITE))
    await client.initialize()

    found = await client.call("list_unmanaged_access_paths", {"project": "Blog"})
    assert "hand-1" in json.dumps(found)

    adopted = await client.call("adopt_access_path", {"project": "Blog", "cf_app_id": "hand-1"})

    assert adopted["result"]["isError"] is not True
    assert "api/ingest" in json.dumps(adopted)
    assert not [c for c in fake_cf.calls if c[0] in ("create", "delete")]


async def test_a_read_token_cannot_adopt(mcp, auth, project, fake_cf):
    fake_cf.apps = {"hand-1": (f"blog.{config.DOMAIN}/api/ingest", "bypass")}
    client = McpClient(mcp, await auth(tokens.READ))
    await client.initialize()

    result = await client.call("adopt_access_path", {"project": "Blog", "cf_app_id": "hand-1"})

    assert result["result"]["isError"] is True
