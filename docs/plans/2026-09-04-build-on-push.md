# Build on push, without GitHub Actions

Written 2026-09-04. GitHub stopped starting Actions jobs on the account on
2026-09-05 (billing), which removed the only publish path. Sam's decision: pay
nothing more, build an Actions-free system instead.

## Status

Phases 1 to 3 are on PR #41 and running on the mini since 2026-09-05 02:41
(console image `manual-83952f7`, shipped by hand). Milestones met the same
night: "build now" built notion-sync in 45 s and planner in 65 s, both live;
then planner #439 merged at 02:50:00 and the watcher opened its build at
02:50:32 with no button press and no Actions run, live at 02:51:22.
claude-quota's build fails on purpose until its repo carries a console.toml.
Phases 4 and 5 can start.

## Goal

A push to a project's tracked branch produces a GHCR image and a deployment
with no GitHub Actions run involved, visible and controllable from the console.

## Approach

The console notices a new branch head by polling GitHub through the connection
it already has, builds the image on the box in a one-shot `docker:28-cli`
container attached to a resource-capped BuildKit builder, pushes it to GHCR,
and hands the result to the existing deploy engine. Nothing new listens on the
network: no push webhook, no HMAC secret, no new Cloudflare Access bypass. The
30 second poll interval is the latency cost, and it is shorter than an Actions
job used to take to start.

Two house rules change and are rewritten, not quietly contradicted: "no
building on the server" becomes "the console builds app images on the server,
capped and serialized, and never its own", and the OIDC webhook receiver is
deleted once every app is off Actions, so "webhooks authenticate via OIDC"
ceases to describe anything.

## Files

Create:
- `src/console/deploy/builder.py`: runs one build container, streams its output into the deployment row, queues the engine on success
- `src/console/deploy/watcher.py`: the poll loop that turns a new branch head into a building deployment
- `alembic/versions/d0e1f2a3b4c5_project_auto_build.py`: `projects.auto_build`, `projects.watched_sha`
- `tests/test_builder.py`, `tests/test_watcher.py`

Modify:
- `src/console/config.py`: `BUILD_IMAGE`, `BUILD_BUILDER`, `BUILD_LOG_MAX`, `WATCH_INTERVAL`
- `src/console/github.py`: `resolve_commit(repo, ref) -> (sha, message)`
- `src/console/db/models.py`: two Project columns
- `src/console/api/projects.py`: `POST /{id}/builds`, `PUT /{id}/auto-build`
- `src/console/v1/service.py`, `rest.py`, `mcp.py`, `models.py`: `build_project`
- `src/console/main.py`: start `watch_loop` beside the reaper
- `src/console/starters.py`: stop handing out `deploy.yml`
- `frontend/src/pages/ProjectDetail.tsx`, `DeploymentDetail.tsx`, `Settings.tsx`, `frontend/src/api/client.ts`
- `docs/manual-deploy.md`, `docs/server-setup.md`, `docs/api.md`, `CLAUDE.md`
- Delete in the last phase: `.github/workflows/app-deploy.yml`, `src/console/api/hooks.py`, `src/console/oidc.py`, `tests/test_hooks_api.py`, `tests/test_oidc.py`

## Risks

- **The 2018 Intel mini builds while serving six containers.** The builder is
  created with `memory=2g` and two CPUs of the Colima VM's four, and builds are
  serialized by one lock. A build that needs more fails rather than starving
  the apps; that is the right failure.
- **The console's own image is still published by Actions.** Every phase whose
  milestone runs on the mini needs the console image shipped by hand first:
  `docs/manual-deploy.md` "Publishing an image by hand" with the console repo,
  then on the box `CONSOLE_IMAGE=ghcr.io/sam-stuhl/console:manual-<sha> docker
  compose -f compose.prod.yaml up -d console`. The console building itself
  stays forbidden (circular). Its standing publish path after this plan is a
  separate decision, recorded as a follow-up below.
- **The GitHub OAuth token (scope `repo`) now also flows into a build
  container's environment** for the length of one build, as BuildKit's
  `GIT_AUTH_TOKEN` for the private git context. The container is removed after
  each build. A fine-grained read-only token per repo would be tighter and is
  a follow-up, not a blocker.
- **`docker:28-cli` attaching to a BuildKit container with the remote driver**
  is the one mechanism not yet rehearsed. Phase 1 rehearses it by hand before
  any code. Fallback if it fails: a named volume at `/root/.docker` in the cli
  container so `docker-container` driver state survives between runs.
- **Layer cache grows on the box.** Each build ends with `docker buildx prune
  --reserved-space 5g`, so the cache stays warm and bounded.

## Phase 1: the builder

Runs a build for a deployment row that is already in `building`. No trigger
yet; only tests and the phase 2 button call it.

Rehearsal first, by hand over `ssh mac-mini-console`, before writing code:

```bash
docker buildx create --name console-build --driver docker-container \
  --driver-opt memory=2g --driver-opt cpu-quota=200000 --driver-opt cpu-period=100000
docker buildx inspect --bootstrap console-build      # creates buildx_buildkit_console-build0
docker run --rm -v /var/run/docker.sock:/var/run/docker.sock docker:28-cli sh -c '
  docker buildx create --name console-build --driver remote docker-container://buildx_buildkit_console-build0 &&
  docker buildx inspect console-build'
```

The third command proves a fresh cli container can attach to the builder the
host created. If it prints the builder as running, the design holds; if not,
switch to the named-volume fallback and record why here.

Rehearsed 2026-09-04: the attach printed the builder as running (buildx
v0.29.1 inside `docker:28-cli`), and a build of claude-quota at `a9f6401` from
its git context pushed `ghcr.io/sam-stuhl/claude-quota:rehearsal-a9f6401`
through it in about a minute, with the box's existing GHCR login mounted
read-only. The builder container carries `mem=2147483648 quota=200000
period=100000 restart=unless-stopped`. One correction: `--keep-storage` is
deprecated in this buildx, so the prune uses `--reserved-space 5g`.

`config.py`:

```python
BUILD_IMAGE = "docker:28-cli"
BUILD_BUILDER = "console-build"
BUILD_LOG_MAX = 512 * 1024
```

`builder.py`, mirroring `commands/runner.py`:

- `enqueue(deployment_id)` creates a task in a module task set, the same as `engine.enqueue`.
- `run_build(deployment_id)` takes one module-wide `asyncio.Lock` (builds are
  serialized console-wide, not per project), loads the deployment and project,
  and fails the row with a reason before touching Docker when: the GitHub
  connection is missing (`github.GitHubNotConnected`), `settings_store.GHCR_TOKEN`
  is unset ("no GitHub packages token in Settings; it needs write:packages
  now"), or `console.toml` at the sha does not parse (same `read_file` and
  `parse_console_toml` calls as `manual.resolve_config`).
- The image is `ghcr.io/{repo.lower()}:{sha[:7]}`, which is exactly what
  `app-deploy.yml` produced, so history reads the same and `plan.validate_image`
  accepts it unchanged.
- The build runs as `client.containers.run(config.BUILD_IMAGE, ["sh", "-c", SCRIPT],
  detach=True, environment={...}, volumes={"/var/run/docker.sock": {"bind": "/var/run/docker.sock", "mode": "rw"}},
  name=f"console-build-{deployment.id[:6]}")`, with environment `GHCR_USER=config.OIDC_OWNER`,
  `GHCR_TOKEN`, `GIT_AUTH_TOKEN` (the GitHub connection token), `IMAGE`,
  `CONTEXT=https://github.com/{repo}.git#{sha}`, `DOCKERFILE` from
  `cfg.app.dockerfile`. SCRIPT is a constant:

```sh
set -eu
printf '%s' "$GHCR_TOKEN" | docker login ghcr.io -u "$GHCR_USER" --password-stdin >/dev/null
docker buildx create --name console-build --driver remote docker-container://buildx_buildkit_console-build0 >/dev/null
docker buildx build --builder console-build --platform linux/amd64 --provenance=false \
  --secret id=GIT_AUTH_TOKEN,env=GIT_AUTH_TOKEN -f "$DOCKERFILE" -t "$IMAGE" --push "$CONTEXT"
docker buildx prune --builder console-build --reserved-space 5g -f >/dev/null
```

- Output is drained with `container.logs(stream=True, follow=True)` through
  `asyncio.to_thread(next, stream, None)` and appended to `deployment.log`
  with the same room check as `runner._append`, capped at `BUILD_LOG_MAX`.
  Past `config.BUILD_TIMEOUT` the container is killed and removed and the row
  fails with "build outran 30 minutes".
- Exit 0: `deployment.image`, `config_snapshot`, `build_finished_at`, status
  `queued`, then `deploy_engine.queue`. Non-zero: status `failed`,
  `failure_reason = f"build exited {code}"`, log kept. The container is removed
  in a `finally`.

`tests/test_builder.py` with a `FakeDocker` whose `containers.run` records its
arguments and returns a container with scripted `logs`, `wait`, `kill`,
`remove`: image ref is lowercased and seven-character tagged; the command's
environment carries `CONTEXT` ending in `#<sha>` and the dockerfile from
console.toml; exit 1 fails with the log kept; a missing GHCR token fails before
`containers.run` is called; success sets `queued` and calls a monkeypatched
`engine.queue`; the log cap truncates with the marker.

Milestone: `PYTHONPATH=src ../../../.venv/bin/python -m pytest -q tests` green,
and the rehearsal above recorded as done.

## Phase 2: build now

A button and an API, so the builder can be driven end to end on the mini
before anything automatic exists.

- `github.resolve_commit(repo, ref) -> tuple[str, str]`: `GET /repos/{repo}/commits/{ref}`,
  which accepts a branch name or a sha, returning `(sha, commit.message)`.
  Missing ref maps to `FileNotFound` (the `_get` 404 rule), which the API turns
  into a 422 "no such ref".
- `POST /api/projects/{id}/builds` body `{"ref": str | null}` (null means
  `project.branch`). Resolves the commit, refuses with 409 if
  `hooks.find_open_deployment` finds one for that sha, else creates
  `Deployment(status="building", sha, commit_message)` and `builder.enqueue`.
  Answers 202 with the deployment id.
- v1: `build_project(project, ref=None)` in `service.py`, `POST /v1/projects/{project}/builds`
  in `rest.py`, and an MCP tool of the same name whose description says a
  build takes minutes on the box and to follow it with `get_deployment`.
- Frontend: a "build now" button beside "redeploy" on `ProjectDetail.tsx`,
  posting the tracked branch and navigating to the new deployment.
  `DeploymentDetail.tsx` keeps the "build run" row only when `run_url` is set
  (old rows) and otherwise shows nothing there; the build output is already in
  the log panel. `Settings.tsx` "github packages token" copy: scopes are
  `read:packages` and `write:packages`, and the text says why (the console
  pushes what it builds).
- `docs/api.md` gains the builds endpoint; `docs/server-setup.md` step for the
  token says `write:packages`, and gains the `docker buildx create` line from
  the phase 1 rehearsal as one-time host setup under the `console` account.

Milestone, on the mini after shipping the console image by hand: regenerate
the packages token with `write:packages` and paste it in Settings, press
"build now" on claude-quota, watch the row go `building`, `queued`,
`deploying`, `live`, and confirm `ghcr.io/sam-stuhl/claude-quota:<sha7>` exists
in GHCR with a single amd64 manifest. Then the same on planner.

## Phase 3: build on push

- Migration `d0e1f2a3b4c5`, `down_revision = "c9d0e1f2a3b4"` (the head; `a7b8c9d0e1f2` is already taken by project icon): `auto_build`
  Boolean not null server default `0`, `watched_sha` Text nullable. Model
  columns to match.
- `watcher.py`: `watch_loop()` sleeps `config.WATCH_INTERVAL = 30` between
  `sweep()` calls, started in `main.lifespan` next to the reaper and cancelled
  with it. `sweep(session)` loads projects with `auto_build`, and per project,
  inside its own try/except so one bad repo never stops the others: resolves
  the branch head with `resolve_commit`; if `watched_sha` is None, records the
  head as the baseline and builds nothing; if the head differs from
  `watched_sha` and no open deployment exists for it, creates the building
  deployment and enqueues the builder; then records the head. A GitHub error
  is logged at warning and the project is skipped until the next sweep.
- `PUT /api/projects/{id}/auto-build` body `{"enabled": bool}`. Enabling sets
  `watched_sha` to the current head at that moment, so switching it on never
  builds what was pushed last week; "build now" is for that. Disabling clears
  `watched_sha`.
- `ProjectOut` and the frontend `Project` type gain `auto_build`; the project
  page shows a "build on push" toggle beside the branch.
- `tests/test_watcher.py` with the conftest `FakeGitHubClient` routes on
  `/commits/main`: first sweep baselines without a deployment; a changed head
  creates exactly one building deployment and enqueues once; an unchanged head
  creates none; `auto_build` off is never polled; a GitHub error on one project
  leaves the other project's deployment created.

Milestone: enable "build on push" on planner, merge a one-line PR there, and
see the deployment appear within a minute and go live with no Actions run in
`gh run list -R Sam-Stuhl/planner`.

## Phase 4: retire the Actions publish path

- `starters.py` returns `console_toml` and `dockerfile` only; the setup
  section in the frontend drops the workflow tab and its text says the
  console builds on push once the toggle is on. `config.WORKFLOW_REPO` goes.
- Delete `.github/workflows/app-deploy.yml` from this repo.
- Delete `.github/workflows/deploy.yml` from each app repo by PR, one per
  repo: `Sam-Stuhl/planner`, `Sam-Stuhl/banking-dash`, `Sam-Stuhl/notion-sync`,
  `Sam-Stuhl/resume-git`, `Sam-Stuhl/claude-quota`. Their `tests.yml`
  workflows are dead for the same billing reason; they stay, since they cost
  nothing while blocked and resume on their own if billing is ever fixed.
- `docs/manual-deploy.md`: "Publishing an image by hand" is now the path for
  when the console itself is down; when it is up, "build now" is the fallback.

Milestone: `auto_build` on for all five projects, each has had at least one
console-built deployment, and no app repo carries `deploy.yml`.

## Phase 5: delete the webhook receiver

- Delete `src/console/api/hooks.py`, `src/console/oidc.py`, their tests, the
  `/hooks` router registration in `main.py`, and `CONSOLE_OIDC_AUDIENCE` from
  `compose.prod.yaml`. `CONSOLE_OIDC_OWNER` stays: it is the image namespace
  and the GHCR login user, and the name is left alone so `.env` on the box
  keeps working.
- `find_open_deployment` moves to `deploy/plan.py`, where phase 2 and 3 call it.
- Remove the `/hooks/*` Bypass policy in Cloudflare Access from the Settings
  page's access section and from `docs/server-setup.md`.
- `CLAUDE.md`: replace the "No building on the server" non-goal and the
  "Webhooks authenticate via GitHub OIDC" and "The build workflow is reusable"
  architecture facts with: the console builds app images on the box, capped
  and serialized, from a git context it fetches with the GitHub connection;
  it never builds its own image; nothing but the browser and the machine API
  under `/v1` calls into it.

Milestone: tests green, `grep -rn "hooks\|oidc" src frontend/src docs` finds
nothing but history sections, and a push to planner still deploys.

## Follow-ups, not in this plan

- The console's own image. Its publish path was the `build console image`
  workflow plus the self-hosted runner's compose pull. Until decided, it ships
  by the hand runbook. The candidates are a launchd job under `console` on the
  mini that runs that runbook on a new `main`, or a laptop-side script; the
  console building itself stays out.
- A fine-grained read-only GitHub token per repo for the build context,
  replacing the OAuth `repo` token in the build container's environment.
- Pruning the `manual-*` tags from GHCR once every app has a console-built
  image.
