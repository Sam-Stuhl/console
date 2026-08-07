# The machine API

The console has a second, token-authenticated surface for callers that are not
a person in a browser: a REST API under `/v1`, and an MCP server at `/mcp` for
AI agents. Both do the same things and share the same tokens.

This exists because Cloudflare Access, which gates the web console, only knows
how to authenticate an interactive browser login. A `curl` or an agent gets a
login page, not JSON.

## The trade you are making

The console's normal rule is that there is **no auth inside the app**: Access at
the edge is the only gate. `/v1` and `/mcp` are the documented exception, and
they only work if you punch a Bypass hole in Access for those two paths (see
`docs/server-setup.md`, step 4).

That means those paths are exposed to the internet with a console-issued token
as the only thing protecting your projects, deploys, logs, and app controls. The
token is a 256-bit random string stored only as a SHA-256 hash, so guessing one
is not a realistic threat and a stolen copy of the database yields nothing
usable. Losing a token is the risk that matters, and revocation is the answer.

Secret values are unreachable from here. Not filtered: absent. No endpoint and
no tool returns one, so there is no check that can be got wrong. Reading or
changing a secret stays in the web console.

## Minting a token

Settings -> **api tokens**. Give it a name and a scope:

- `read` sees everything: projects, deploy history and logs, container state,
  app logs, which secrets exist, command history, backup status.
- `write` can also deploy, roll back, restart, run one-off commands, create and
  delete projects, and change domains and Access settings.

Prefer `read`. Mint `write` only for a caller that genuinely needs to change
something.

**The token is shown once.** The console stores only a hash and cannot show it
again. If you lose it, revoke it and mint another. Revoking takes effect on the
next request.

Name tokens after where they live (`laptop`, `claude-code`, `ci`) so the
last-used column tells you something when you are deciding what to revoke.

## Using it from a shell

```bash
curl -H "Authorization: Bearer csk_your_token" https://console.<your-domain>/v1/system
```

`/v1/system` is the overview: domains, how many projects exist and how many are
live or down, credential expiry, backup status. It is the call to lead with.

The full endpoint list, with request and response shapes, is served from the
console itself:

- `https://console.<your-domain>/v1/docs`: browsable, with an Authorize button
- `https://console.<your-domain>/v1/openapi.json`: the spec

Both are deliberately un-gated: they describe the shape of the API and carry no
data. Everything that returns anything real needs a token.

Anywhere an endpoint takes a project you can use its id, its name, or its
subdomain, so `/v1/projects/blog` works as well as the UUID.

### What is there

| | |
| --- | --- |
| `GET /v1/system` | how the whole install is doing |
| `GET /v1/projects`, `/v1/projects/{project}` | projects |
| `GET /v1/projects/{project}/deployments[/{id}]` | deploy history, and one deploy's log |
| `GET /v1/projects/{project}/container` | container state and resource use |
| `GET /v1/projects/{project}/logs?tail=N` | the app's logs, stopped container included |
| `GET /v1/projects/{project}/secrets` | secret **names** and when each changed |
| `GET /v1/projects/{project}/commands[/{id}]` | one-off command history and output |
| `GET /v1/backups` | backup status and history |
| `POST /v1/projects/{project}/deployments` | deploy an image that already exists in the registry |
| `POST /v1/projects/{project}/deployments/{id}/rollback` | roll back to a build that served traffic |
| `POST /v1/projects/{project}/deployments/{id}/redeploy` | re-run a build's image and config |
| `POST /v1/projects/{project}/controls/{start\|stop\|restart}` | container controls |
| `POST /v1/projects/{project}/commands` | run a one-off command |
| `POST /v1/projects`, `DELETE /v1/projects/{project}` | register and remove projects |
| `PUT /v1/projects/{project}/domain`, `/access` | move domains, toggle the Access gate |
| `POST /v1/backups` | back up the console now |

Writes need a `write` token; a `read` token gets a 403 that says so.

Deploying an image builds nothing: the console pulls what CI, or a laptop,
already pushed. The image's namespace must match `CONSOLE_OIDC_OWNER`, so the
API cannot be used to make the console pull a stranger's image. A project's
`image_hint` gives you the prefix to put a tag on.

### Errors

Plain JSON, `{"detail": "..."}`, with the message written to be read by whoever
called. 401 means the token is missing or wrong (it deliberately does not say
which). 403 means the token is read-only. 404, 400, 409, and 503 mean what they
usually do.

## Using it from an AI agent

`/mcp` is a Model Context Protocol server over streamable HTTP, with a tool per
endpoint. For Claude Code, in `.mcp.json`:

```json
{
  "mcpServers": {
    "console": {
      "type": "http",
      "url": "https://console.<your-domain>/mcp",
      "headers": { "Authorization": "Bearer csk_your_token" }
    }
  }
}
```

Settings -> **api tokens** -> **connect an agent** has this snippet prefilled
with your console's real URL.

The server ships instructions telling an agent how to work the console: start
at `get_system`, then `list_projects`, and the usual path for diagnosing a sick
app (`get_project` -> `get_container` -> `get_app_logs` -> `list_deployments`).
Twenty-one tools, named for what they do: `get_system`, `list_projects`,
`get_project`, `list_deployments`, `get_deployment`, `get_container`,
`get_app_logs`, `list_secret_keys`, `list_commands`, `get_command`,
`get_backups`, and the write tools `deploy_image`, `rollback_deployment`,
`redeploy`, `control_app`, `run_command`, `create_project`, `delete_project`,
`set_project_domain`, `set_project_access`, `trigger_backup`.

A `read` token calling a write tool gets a tool error saying so, rather than a
failure it has to guess at.

## Things worth knowing

- **A token cannot mint another token.** Token management is only in the web
  console, behind Access, so one leaked token cannot renew itself into
  permanent access.
- **Settings are not writable here.** The GHCR token, Cloudflare token, backup
  passphrase, and ntfy topic are all credential material and stay in the UI.
- **There is no terminal over the API.** The interactive container shell is a
  websocket in the browser and stays there.
- **`last_used_at` is approximate.** It is only rewritten once a minute, so a
  read-heavy caller does not turn every request into a database write. It is
  there to tell you a token is forgotten, not to audit calls.
