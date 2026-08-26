# Manual deploy runbook

Executed by hand on 2026-07-09 against Colima. These exact commands are the
spec for the deploy engine. `traefik/whoami` stands in for an app image:
`v1.10.1` is the currently-live release, `v1.11.0` is the release being
deployed. In production the image is `ghcr.io/<your-account>/<app>:<sha>` and
the name suffix is the short sha.

This cycle assumes the image is already in GHCR, which is normally CI's job. For
the case where CI cannot do it, see "Publishing an image by hand" below.

## One-time host setup

```bash
docker network create web
docker compose up -d          # traefik (v3.6+, see note 1) + whoami test container
```

## The deploy cycle

### 1. Pull the new image

```bash
docker pull traefik/whoami:v1.11.0
```

### 2. Start the new container alongside the old one

The old container (`app-demo-a1b2c3d`) keeps serving. Nothing is touched yet.

```bash
docker run -d \
  --name app-demo-e5f6a7b \
  --network web \
  --env-file demo.env \
  --memory 512m \
  --cpus 1.0 \
  --restart unless-stopped \
  --label traefik.enable=true \
  --label 'traefik.http.routers.app-demo-e5f6a7b.rule=Host(`app-demo.localhost`)' \
  --label traefik.http.routers.app-demo-e5f6a7b.entrypoints=web \
  --label traefik.http.services.app-demo-e5f6a7b.loadbalancer.server.port=80 \
  traefik/whoami:v1.11.0
```

Mapping from console.toml:

| flag / label                      | source                                  |
|-----------------------------------|-----------------------------------------|
| `--name` / router / service names | `app.name` + deployment short sha       |
| `Host(...)` rule                  | `app.subdomain` (+ real domain in prod) |
| `loadbalancer.server.port`        | `app.port`                              |
| `--env-file` (or `-e` pairs)      | `[env]` + decrypted secrets             |
| `--memory`, `--cpus`              | `[resources]`                           |

Router and service names embed the deployment id so consecutive deployments
never collide on a router name.

### 3. Health-check the new container directly

Container-to-container over the `web` network, by container name. The console
runs on this network, so it does the same with an HTTP client. Never through
Traefik: the point is to test the container before trusting it with traffic.

```bash
docker run --rm --network web curlimages/curl \
  -s -o /dev/null -w "%{http_code}" --max-time 5 \
  http://app-demo-e5f6a7b:80/health
# -> 200. Poll until 200 or health.timeout seconds, then give up.
```

On timeout: `docker rm -f app-demo-e5f6a7b`, mark deployment failed, stop.
The old container was never touched.

### 4. Swap

Only after the 200:

```bash
docker stop app-demo-a1b2c3d
docker rm   app-demo-a1b2c3d
```

Traefik notices the old container is gone within a second and the new router
is the only match for the Host rule. Verified:

```bash
curl -s -H "Host: app-demo.localhost" http://localhost   # answered by new container
```

### 5. Verify env injection

`docker exec <c> env` does not work on scratch images (no shell, no `env`
binary). Inspect instead:

```bash
docker inspect app-demo-e5f6a7b --format '{{range .Config.Env}}{{println .}}{{end}}'
```

## Publishing an image by hand (when CI is unavailable)

Everything above assumes the image is already in GHCR. Normally it is: each app
repo's `deploy.yml` calls the reusable build workflow, and that is the *only*
publish path. When GitHub Actions is down, that path is gone and nothing can be
deployed at all. This section is the fallback, verified end to end on the box on
2026-08-26.

It does not soften "no building on the server". The console still only pulls and
runs, none of this is wired into the engine, and nothing calls it automatically.
It is an operator running `docker` by hand during an outage.

### Why the obvious thing does not work

Plain `docker build` then `docker push` fails on the box, and it fails
confusingly enough to be worth naming. Colima's daemon uses the **containerd
image store**, which `docker info` reports as
`driver-type: io.containerd.snapshotter.v1`. Under that store a locally built
image's content lives as containerd snapshots, and this Docker/containerd
combination cannot materialize the blobs for a local export:

| attempt | result |
|---|---|
| `docker push` | `NotFound: content digest sha256:...: not found` |
| `docker push --platform linux/amd64` | "found but does not provide the specified platform", though `docker image inspect` says amd64/linux |
| `docker save` | an 8.5 KB tar carrying the manifest only, so any registry tool fed it reports the layer blobs missing |
| `DOCKER_BUILDKIT=0 docker build` | identical, so it is not a BuildKit artifact |

Auth is not the problem: `docker login ghcr.io` with a `write:packages` token
succeeds and pushes still fail. CI never meets this because buildx streams
straight to the registry and never touches a local export. That is also the fix.

### One-time setup, per account on the box

buildx is a CLI plugin. Homebrew on the mini belongs to the `sam` account, so
install it into the `console` account's own plugin directory instead of
system-wide. Take the asset matching the *host* architecture (the mini is Intel,
so `darwin-amd64`):

```bash
mkdir -p ~/.docker/cli-plugins
curl -fsSL -o ~/.docker/cli-plugins/docker-buildx \
  https://github.com/docker/buildx/releases/download/v0.36.1/buildx-v0.36.1.darwin-amd64
chmod +x ~/.docker/cli-plugins/docker-buildx
docker buildx version     # github.com/docker/buildx v0.36.1

docker buildx create --name console-manual --driver docker-container --use
```

`--push` needs a builder that can output to a registry, which the default
`docker` driver cannot do. The `docker-container` driver runs BuildKit as a
container (`buildx_buildkit_console-manual0`) inside the Colima VM.

Both halves survive a Colima restart. The plugin is a file in the `console`
account's home directory on the host, untouched by anything in the VM, and the
builder container is recreated or restarted by buildx on the next build.
Verified by stopping `buildx_buildkit_console-manual0` and building again: the
build succeeded and the container came back up on its own. That is the safe
proxy for the real test, because restarting Colima takes Traefik, cloudflared,
and every app container down with it.

### Publishing

```bash
git clone --depth 1 https://github.com/<owner>/<app>.git /tmp/<app>-build
cd /tmp/<app>-build && git rev-parse --short HEAD    # confirm the sha you meant

docker login ghcr.io -u <github-user>                # write:packages PAT, once
docker buildx build --builder console-manual \
  --platform linux/amd64 --provenance=false \
  -t ghcr.io/<owner>/<app>:manual-<sha> --push .
```

- `--platform linux/amd64` because prod is amd64. On the Intel mini that is
  native. From an arm64 Mac the same command works but emulates, so it is slow.
- `--provenance=false` keeps the push a plain single-platform image. Without it
  buildx publishes an image index with an attestation manifest, which shows up
  in GHCR as an extra `unknown/unknown` entry and is not what the engine's pull
  expects.
- buildx reads registry credentials from `~/.docker/config.json`, so the
  ordinary `docker login` is all the auth it needs.
- `docker buildx ls` prints a harmless "Cannot load builder default: failed to
  connect to the docker API at unix:///var/run/docker.sock" line. That is a
  stale context, not the builder you just made. Ignore it.

The push finishing is the proof. It ends with
`pushing manifest for ghcr.io/<owner>/<app>:manual-<sha>@sha256:...`.

### Deploying what you published

The console pulls from the registry and never uses a locally tagged image, so
the push has to land first. Then deploy the tag the ordinary way, through the
web UI or the `deploy_image` machine tool.

One trap: `deploy_image` reads `console.toml` from the repo, and an app whose
repo does not carry one fails with `no console.toml in <owner>/<repo> at
"<branch>"` before it ever pulls. Pass the config as the pasted fallback in that
case. The text to paste is in `config_snapshot` on the project's last good
deployment, as JSON, which converts back to TOML directly. Remember that
`secrets = [...]` has to sit above the first `[section]` header.

Verified on 2026-08-26 with `Sam-Stuhl/claude-quota` at `a9f6401`: built and
pushed as `ghcr.io/sam-stuhl/claude-quota:manual-a9f6401`, deployed through the
console (pull, run alongside, health check, swap, `live`), and the running
container reports `SCHEMA_VERSION 3` with the new `name` column present on the
`session` table. No GitHub Actions run was involved.

## Notes and open items discovered during the rehearsal

1. **Traefik must be v3.6+.** Docker engine 29 (what Colima ships now) dropped
   API versions below 1.40; Traefik v3.4 pins an older client version and its
   Docker provider fails with "client version 1.24 is too old". Symptom is a
   404 on every route with errors in `docker logs traefik`.

2. **Overlap window: resolved (2026-07-09, priority label).** Between step 2
   and step 4 two routers match the same Host rule, and which one Traefik
   picks must not be left to chance. Decision: every router the engine
   creates carries an explicit `priority` label, strictly lower than the
   live one's (live minus 1, counting down from 4000000000 on a project's
   first deploy). Traefik always routes to the highest-priority matching
   router, so the old container keeps all traffic until it is removed;
   labels cannot change on a running container, hence the countdown instead
   of a constant. The Docker `HEALTHCHECK` alternative was rejected because
   the health command runs inside the container and scratch images (see
   note 3) have nothing to run it with; it would have become a contract
   requirement on every app image.

3. **Reading env from a container**: use `docker inspect` (SDK: `attrs`), not
   exec. Works regardless of image contents.

4. **Dev health checks cannot succeed from the host.** The engine probes
   `http://<container>:<port><path>` container-to-container over `web`. In
   prod the console is itself a container on that network; in dev (uvicorn
   on the host, containers in a VM) container names do not resolve from
   the host, so an end-to-end deploy in dev always fails at step 3 and
   exercises the failure path instead. Deliberately no config escape hatch:
   a dev-only code path that never runs in prod would be worse. To see the
   happy path in dev, run the console in a container attached to `web`.

5. **The daemon's image store decides whether a local push can work.** Colima
   here runs the containerd image store, under which `docker push` and
   `docker save` of a locally built image both fail on missing content digests.
   Check with `docker info --format '{{.DriverStatus}}'`: a `driver-type` of
   `io.containerd.snapshotter.v1` means any hand publish has to go through
   `buildx build --push`, which streams to the registry instead of exporting
   locally. Switching the store back to the classic graph driver would also fix
   it, but that is a daemon restart and takes every container on the box down,
   so it was rejected in favour of installing buildx.
