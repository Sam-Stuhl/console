# Manual deploy runbook

Executed by hand on 2026-07-09 against Colima. These exact commands are the
spec for the deploy engine. `traefik/whoami` stands in for an app image:
`v1.10.1` is the currently-live release, `v1.11.0` is the release being
deployed. In production the image is `ghcr.io/sam-stuhl/<app>:<sha>` and the
name suffix is the short sha.

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
   on the Mac, containers in Colima) container names do not resolve from
   the host, so an end-to-end deploy in dev always fails at step 3 and
   exercises the failure path instead. Deliberately no config escape hatch:
   a dev-only code path that never runs in prod would be worse. To see the
   happy path in dev, run the console in a container attached to `web`.
