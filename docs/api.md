# The machine API

The console has a second, token-authenticated surface for callers that are not
a person in a browser: a REST API under `/v1`, and an MCP server at `/mcp` for
AI agents. Both do the same things and share the same tokens.

It exists because the console has no login of its own. The web UI is protected
by whatever gate you put in front of it, and an edge gate authenticates a person
clicking through a browser. A `curl` or an agent cannot do that, so it needs a
credential the console itself understands.

## Reaching it, and the trade that can involve

The token check is always on. Everything past that depends on how your install
is reached, and there are two shapes:

**If the console is only reachable on a network you trust** (a LAN, a VPN or
tailnet, an SSH tunnel, a published port on the box itself), there is nothing to
configure. Mint a token, point your script or agent at
`http://<your-console-host>/v1`, and you are done. The API is no more exposed
than the web console already is.

**If the console sits behind an edge gate that requires an interactive login**
(the documented setup uses Cloudflare Access), that gate will turn a `curl` away
with a login page. Machine callers cannot complete a browser login, so those two
paths have to be excepted from it: `docs/server-setup.md`, step 4, covers the
Cloudflare version, and once the console holds a Cloudflare token you can add
the same exception from Settings -> cloudflare access instead of the Cloudflare
dashboard. Understand what that buys before you do it. Excepting a path
from the edge gate means it is reachable from the whole internet, with your
token as the only thing protecting your projects, deploys, logs, and app
controls.

Either way the token itself is the same: a 256-bit random string stored only as
a SHA-256 hash, so guessing one is not a realistic threat and a stolen copy of
the database yields nothing usable. Losing a token is the risk that matters, and
revocation is the answer.

The console's normal rule is that there is **no auth inside the app**, because
the edge gate handles it. `/v1` and `/mcp` are the one documented exception, for
callers that cannot log in through a browser.

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
| `GET /v1/projects/{project}/access/paths`, `GET /v1/access/paths` | which paths skip the Access login, for an app or for the console |
| `GET /v1/backups` | backup status and history |
| `POST /v1/projects/{project}/deployments` | deploy an image that already exists in the registry |
| `POST /v1/projects/{project}/deployments/{id}/rollback` | roll back to a build that served traffic |
| `POST /v1/projects/{project}/deployments/{id}/redeploy` | re-run a build's image and config |
| `POST /v1/projects/{project}/controls/{start\|stop\|restart}` | container controls |
| `POST /v1/projects/{project}/commands` | run a one-off command |
| `POST /v1/projects`, `DELETE /v1/projects/{project}` | register and remove projects |
| `PUT /v1/projects/{project}/domain`, `/access` | move domains, toggle the Access gate |
| `POST`/`DELETE /v1/projects/{project}/access/paths[/{path}]` | open or close one path on an app, without the login |
| `POST`/`DELETE /v1/access/paths[/{path}]` | the same on the console's own hostname |
| `POST /v1/backups` | back up the console now |

Writes need a `write` token; a `read` token gets a 403 that says so.

Opening a path is how a caller that cannot log in reaches an app: it creates
the Cloudflare Access Bypass app for `<host>/<path>`, so a Shortcut or a cron
job gets the app instead of a login page while the rest of the hostname keeps
its gate. It is the same trade this surface itself is behind, one path at a
time, so open one only where the app authenticates its own callers. Two are
refused: an empty path, and `/api` on the console's own hostname, which has no
authentication of its own (that is what this token surface is for). The
Cloudflare rate limit that should accompany a bypass is a permission the console
deliberately does not hold, so add it in the Cloudflare dashboard.

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
endpoint. For Claude Code:

```bash
claude mcp add --transport http --scope user console \
  https://console.<your-domain>/mcp --header "Authorization: Bearer csk_your_token"
```

`--scope user` registers it once for every project on that machine, which is
usually what you want: the console is *about* your projects, so the tools are
most useful from inside one of them. It also keeps the token in your own config
rather than in a repo. Project scope writes `.mcp.json`, which is meant to be
committed, so it is the wrong home for a credential. Drop the flag to add the
server to just the current project, and check either with `claude mcp list`.

Settings -> **api tokens** -> **connect an agent** has this command prefilled
with your console's real URL.

The server ships instructions telling an agent how to work the console: start
at `get_system`, then `list_projects`, and the usual path for diagnosing a sick
app (`get_project` -> `get_container` -> `get_app_logs` -> `list_deployments`).
Twenty-four tools, named for what they do: `get_system`, `list_projects`,
`get_project`, `list_deployments`, `get_deployment`, `get_container`,
`get_app_logs`, `list_secret_keys`, `list_commands`, `get_command`,
`get_backups`, `list_access_paths`, and the write tools `deploy_image`,
`rollback_deployment`, `redeploy`, `control_app`, `run_command`,
`create_project`, `delete_project`, `set_project_domain`, `set_project_access`,
`open_access_path`, `close_access_path`, `trigger_backup`.

A `read` token calling a write tool gets a tool error saying so, rather than a
failure it has to guess at.

## Things worth knowing

- **A token cannot mint another token.** Token management exists only in the web
  UI, behind whatever gate fronts it, so one leaked token cannot renew itself
  into permanent access.
- **Settings are not writable here.** The GHCR token, Cloudflare token, backup
  passphrase, and ntfy topic are all credential material and stay in the UI.
- **There is no terminal over the API.** The interactive container shell is a
  websocket in the browser and stays there.
- **`last_used_at` is approximate.** It is only rewritten once a minute, so a
  read-heavy caller does not turn every request into a database write. It is
  there to tell you a token is forgotten, not to audit calls.
