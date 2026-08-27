"""The console as an MCP server, so an AI agent can operate it directly.

Every tool is a thin call into v1/service.py, the same module the /v1 REST
routes use. That is the whole point of the split: an agent and a curl script get
identical behavior, and a rule fixed in one place is fixed for both.

Authentication is the same bearer token /v1 uses, checked by an ASGI wrapper in
front of the transport rather than by the MCP SDK's OAuth machinery: a
single-operator console has no authorization server, and the token already
exists. The authenticated token rides a ContextVar so write tools can check
scope, which mirrors require_write on the REST side.

Secret values are unreachable here for the same structural reason as in REST:
no tool exists that returns one."""

from contextvars import ContextVar
from typing import Any

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings

from console import config, tokens
from console.db.models import ApiToken
from console.db.session import SessionLocal
from console.errors import ConsoleError
from console.v1 import service

INSTRUCTIONS = """\
This is a self-hosted console: a small control plane for apps running in Docker
containers behind Traefik.

Start with get_system for an overview of the install, then drill in with
list_projects and get_project. A project can be named by its id, its name, or
its subdomain, so prefer the human name you saw in a previous result.

Diagnosing an app that is misbehaving usually goes: get_project (is it live?
what does the health ping say?), get_container (is it running, has it been
restarting?), get_app_logs (what did it say before it stopped), then
list_deployments (did this start after a deploy?).

Secret values are not available through this server by design. list_secret_keys
tells you which secrets a project has and when each changed, which distinguishes
a missing secret from a wrong one, but reading or changing a value has to happen
in the console's own web UI.

An app behind the Cloudflare Access login answers a script or a webhook with a
login page rather than its API. list_access_paths shows which paths are excepted
from that, and open_access_path adds one. Opening a path exposes it to the whole
internet with only the app's own authentication in front of it, so confirm with
the operator before calling it, and never open a path just because a caller got
a login page.
"""

# The authenticated token for the request being handled. Set by the ASGI
# wrapper below, read by the write tools.
_token: ContextVar[ApiToken | None] = ContextVar("console_mcp_token", default=None)

# Patchable so tests can point the tools at a throwaway database, the same job
# the get_session dependency override does for the REST routes.
session_factory = SessionLocal

server = MCPServer(name="console", version="1", instructions=INSTRUCTIONS)


def _require_write() -> None:
    token = _token.get()
    if token is None or token.scope != tokens.WRITE:
        raise ToolError(
            "this token is read-only; mint a token with write scope to do this"
        )


def _dump(value: Any) -> Any:
    """Pydantic models to plain JSON-able data, since that is what a tool
    result carries."""
    if isinstance(value, list):
        return [_dump(v) for v in value]
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else value


async def _call(fn, *args, **kwargs) -> Any:
    """Run a service call in its own session and translate domain errors.

    ConsoleError messages are written for the caller, so they are re-raised as
    the SDK's ToolError. That type is the only channel whose text reaches the
    agent: any other exception is treated as a crash and answered with a bare
    "Error executing tool <name>", with the reason left on the server. An agent
    told only that much cannot act, and the message it needed ("no console.toml
    in <repo> at <ref>") was readable only in the container log."""
    async with session_factory() as session:
        try:
            return _dump(await fn(session, *args, **kwargs))
        except ConsoleError as exc:
            raise ToolError(str(exc)) from exc


# ------------------------------------------------------------------- reads


@server.tool()
async def get_system() -> dict:
    """How the whole console install is doing: domains, how many projects exist
    and how many are live or down, credential expiry, and backup status. The
    best first call."""
    return await _call(service.get_system)


@server.tool()
async def list_projects() -> list[dict]:
    """Every project, with where it serves, its latest deploy status, and its
    live health."""
    return await _call(service.list_projects)


@server.tool()
async def get_project(project: str) -> dict:
    """One project by id, name, or subdomain."""
    return await _call(service.get_project, project)


@server.tool()
async def list_deployments(project: str) -> list[dict]:
    """A project's deploy history, newest first."""
    return await _call(service.list_deployments, project)


@server.tool()
async def get_deployment(project: str, deployment_id: str) -> dict:
    """One deployment, including the log the deploy engine wrote. Use this to
    find out why a deploy failed."""
    return await _call(service.get_deployment, project, deployment_id)


@server.tool()
async def get_container(project: str) -> dict:
    """The app's container: whether it is running, its image, and CPU and
    memory use while it is up. state is "absent" if nothing is deployed."""
    return await _call(service.get_container, project)


@server.tool()
async def get_app_logs(project: str, tail: int = config.LOG_TAIL_DEFAULT) -> dict:
    """Recent stdout/stderr from the app's container. Works on a stopped
    container too, which is usually what you want after a crash."""
    return await _call(service.get_logs, project, tail)


@server.tool()
async def list_secret_keys(project: str) -> list[dict]:
    """Which secrets a project has, and when each last changed. Values are
    deliberately not available: this tells you whether DATABASE_URL is set, not
    what it is."""
    return await _call(service.list_secret_keys, project)


@server.tool()
async def list_commands(project: str) -> list[dict]:
    """History of one-off commands run in this project's container."""
    return await _call(service.list_commands, project)


@server.tool()
async def get_command(project: str, run_id: str) -> dict:
    """One command run, including its output. Poll this after run_command."""
    return await _call(service.get_command, project, run_id)


@server.tool()
async def get_backups() -> dict:
    """Whether off-box backup is configured, and recent backup runs."""
    return await _call(service.get_backups)


# ------------------------------------------------------------------ writes


@server.tool()
async def deploy_image(
    project: str,
    image: str,
    ref: str | None = None,
    console_toml: str | None = None,
) -> dict:
    """Deploy an image that already exists in the registry. Requires a write
    token. Nothing is built: the console pulls what CI, or a laptop, already
    pushed, so use this when CI is broken or was never set up. image is a full
    ref including a tag; get_project's image_hint gives you the prefix. The
    console.toml is read from the repo unless you pass one."""
    _require_write()
    return await _call(service.deploy_image, project, image, ref, console_toml)


@server.tool()
async def rollback_deployment(project: str, deployment_id: str) -> dict:
    """Roll back to an earlier build that served traffic. Requires a write
    token. Creates a fresh deployment; the live app is not touched until the
    replacement passes its health check."""
    _require_write()
    return await _call(service.rollback, project, deployment_id)


@server.tool()
async def redeploy(project: str, deployment_id: str) -> dict:
    """Re-run a build's image and config as a new deployment. Requires a write
    token. Use it to retry a failed deploy, or to pick up changed secrets on
    the live build."""
    _require_write()
    return await _call(service.redeploy, project, deployment_id)


@server.tool()
async def control_app(project: str, action: str) -> dict:
    """start, stop, or restart the app's container. Requires a write token.
    Refuses while a deploy is in flight, so it can never act on the wrong
    container."""
    _require_write()
    return await _call(service.control, project, action)


@server.tool()
async def run_command(project: str, command: str) -> dict:
    """Run a one-off command inside the app's live container. Requires a write
    token. Returns a run id; poll get_command for the output."""
    _require_write()
    return await _call(service.run_command, project, command)


@server.tool()
async def create_project(
    name: str, repo: str, subdomain: str, branch: str = "main", domain: str | None = None
) -> dict:
    """Register a new project. Requires a write token. repo is "owner/name" on
    GitHub; domain defaults to the console's primary domain."""
    _require_write()
    return await _call(
        service.create_project,
        name=name,
        repo=repo,
        subdomain=subdomain,
        branch=branch,
        domain=domain,
    )


@server.tool()
async def delete_project(project: str) -> str:
    """Delete a project and its history. Requires a write token. This does not
    stop a running container; stop it first if that matters."""
    _require_write()
    await _call(service.delete_project, project)
    return f"deleted {project}"


@server.tool()
async def set_project_domain(
    project: str, domain: str | None = None, repoint: str = "manual"
) -> dict:
    """Move a project to another configured base domain. Requires a write
    token. The new hostname only reaches Traefik on the next deploy."""
    _require_write()
    return await _call(service.set_domain, project, domain, repoint)


@server.tool()
async def set_project_access(
    project: str, protected: bool, emails: list[str] | None = None
) -> dict:
    """Turn the Cloudflare Access login gate on or off for an app's hostname.
    Requires a write token. Protecting an app needs at least one email."""
    _require_write()
    return await _call(service.set_access, project, protected, emails or [])


@server.tool()
async def list_access_paths(project: str | None = None) -> dict:
    """Which paths skip the Cloudflare Access login: for one app, or for the
    console itself when project is omitted. Everything else on the hostname
    still needs an interactive browser login, which is why a script or an agent
    gets a login page from it."""
    return await _call(service.list_access_paths, project)


@server.tool()
async def open_access_path(path: str, project: str | None = None) -> dict:
    """Let anyone reach one path without the Access login, so a script, a
    Shortcut, or a webhook sender can call it. Requires a write token.

    This opens a hole: the path is then reachable from the whole internet, and
    whatever the app checks itself is the only thing in front of it. Open one
    only where the app authenticates its own callers, and confirm with the
    operator first. Omitting project targets the console's own hostname, where
    /api is refused (it has no authentication of its own; this /v1 surface with
    a token is the supported way in). Cloudflare rate limiting is a separate
    permission the console does not hold, so tell the operator to add a rate
    limit for the path in Cloudflare."""
    _require_write()
    return await _call(service.open_access_path, project, path)


@server.tool()
async def close_access_path(path: str, project: str | None = None) -> str:
    """Put the Access login back in front of one path. Requires a write token.
    Addressed by the path itself, as list_access_paths reports it.

    Some paths are load-bearing: closing hooks on the console's own hostname
    stops every deploy, because CI reports finished builds there and a runner
    cannot log in. Confirm with the operator before closing that one."""
    _require_write()
    await _call(service.close_access_path, project, path)
    return f"closed {path}"


@server.tool()
async def trigger_backup() -> dict:
    """Back up the console's own state now. Requires a write token."""
    _require_write()
    return await _call(service.trigger_backup)


# -------------------------------------------------------------------- ASGI


MOUNT_PATH = "/mcp"

# The streamable-HTTP transport currently in service. Rebuilt by build_app;
# the middleware below reaches it through this name rather than capturing it,
# because a session manager can only be run once per instance, so a test that
# wants its own has to be able to swap the transport underneath.
_transport = None


class McpGate:
    """Routes and authenticates MCP requests, in front of the whole app.

    This is ASGI middleware rather than a mount because Starlette's Mount only
    matches paths that continue past the prefix: "/mcp/…" matches, the bare
    "/mcp" does not, and the router answers it with a redirect instead. Clients
    are configured with the bare "/mcp", so the dispatch happens here and both
    spellings reach the transport directly.

    The bearer token is verified before the transport sees the request, so an
    unauthenticated caller never opens a session. The answer is a bare 401 that
    does not say which check failed, matching the REST side."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or scope["path"].rstrip("/") != MOUNT_PATH:
            await self.app(scope, receive, send)
            return

        token = await self._authenticate(scope)
        if token is None:
            await self._unauthorized(send)
            return

        # The transport is mounted at "/" inside its own little app.
        scope = {**scope, "path": "/", "raw_path": b"/"}
        reset = _token.set(token)
        try:
            await _transport(scope, receive, send)
        finally:
            _token.reset(reset)

    @staticmethod
    async def _authenticate(scope) -> ApiToken | None:
        # ASGI guarantees header names arrive lowercased.
        raw = dict(scope.get("headers") or []).get(b"authorization")
        if raw is None:
            return None
        try:
            scheme, _, presented = raw.decode("latin-1").partition(" ")
        except UnicodeDecodeError:
            return None
        if scheme.lower() != "bearer":
            return None
        async with session_factory() as session:
            return await tokens.verify(session, presented.strip())

    @staticmethod
    async def _unauthorized(send) -> None:
        body = b'{"error":"invalid token"}'
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"www-authenticate", b"Bearer"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


def build_transport():
    """Build a fresh streamable-HTTP transport and return the session manager
    the host app's lifespan must run.

    Called once at startup. A session manager can only be run once per
    instance, so anything wanting a second lifecycle (a test) calls this again
    to get a new one; McpGate reaches the result by name, so the already
    installed middleware picks it up.

    DNS-rebinding protection is off because its threat model does not apply: it
    exists to stop a browser being tricked into reaching an MCP server bound to
    localhost. This one is reached through Cloudflare and Traefik, which route
    by hostname already, and it authenticates with a bearer token rather than a
    cookie, so there is nothing for a hostile page to ride."""
    global _transport
    _transport = server.streamable_http_app(
        streamable_http_path="/",
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=False
        ),
    )
    return server.session_manager
