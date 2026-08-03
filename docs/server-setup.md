# Server setup

Stand up the console on a machine you own, behind Cloudflare, with the deploy
pipeline live. When this is done, `console.<your-domain>` shows the console
(gated by Cloudflare Access), GitHub can POST to `/hooks`, and pushing an app
repo deploys it. Once it is up, **Settings** also configures the operational
extras in the browser, no files or restarts: private-image pulls, the
access-toggle credentials, off-box backups, uptime alerts, extra domains, and
credential-expiry warnings (steps 10-11).

The console is never deployed through its own engine (that would be circular).
It is built by CI (never on the server); the server only pulls. After this
runbook, pushing to `main` updates the console automatically: CI publishes the
image, then a deploy job on your box's self-hosted runner pulls and recreates
the container. See "Updating the console later" at the end for the manual
override, and `remote-access.md` for reaching the box from elsewhere.

Throughout, substitute your own values:

| placeholder | meaning | example |
| --- | --- | --- |
| `example.com` | the domain you will serve apps from | `yourdomain.com` |
| `your-account` | your GitHub account name | `octocat` |
| `/srv/console` | where the deploy clone lives on your box | see per-host below |

## Prerequisites

- **A machine that stays on**, with a container engine (see "Choose your host").
- **Your domain on Cloudflare**, with DNS managed by Cloudflare.
- **This repo cloned** on that machine, at a path you will reuse throughout.
  Keep this clone deploy-only: edit from elsewhere and push; the box pulls.

## Choose your host

Only four things differ by operating system: installing the container engine,
making it start unattended at boot, where the deploy clone lives, and how the
Actions runner installs itself. Everything else in this runbook is identical.

The single question that matters on any host: **after an unattended reboot,
with nobody logged in, does the engine come back?** Test it before you trust
the machine. That is step 8.

### Linux

> Not yet exercised end to end by the maintainer. The steps follow from how
> systemd and Docker Engine work, but treat them as a sketch and verify each.

Docker Engine with the systemd unit enabled is the least surprising option
here, because `dockerd` is a system service: it starts at boot with no user
session involved, which is exactly what an always-on server wants.

```bash
sudo systemctl enable --now docker
```

Put the clone somewhere outside a user home, for example `/srv/console`, so it
does not depend on any account surviving.

### macOS

Verified on an Intel Mac mini running macOS 15.

macOS has one structural quirk that shapes everything: **LaunchAgents load at
login, not at boot, and they die when that user logs out.** launchd has two
kinds of job, and the difference is the single most important thing to get
right here:

| | LaunchAgent | LaunchDaemon |
| --- | --- | --- |
| lives in | `~/Library/LaunchAgents` | `/Library/LaunchDaemons` |
| starts | when a user logs in | at boot |
| stops | when that user logs out | at shutdown |
| runs as | that user | root, or any user via `UserName` |

**Run your server's pieces as LaunchDaemons.** The tempting shortcut is a
LaunchAgent plus auto-login, and it appears to work: the machine reboots and
everything comes back. But production is then living inside a login session,
and anything that ends that session kills it. Logging into a *second* account
graphically is enough. That failure mode is not hypothetical: it took this
console's four sites down twice in one afternoon before the daemons went in.

A daemon can still run as your server account, which it must, since the VM
image, the docker socket, and the deploy clone all live in that home directory.
Add `UserName`, and give it `HOME` and `PATH` explicitly, because daemons
inherit almost no environment:

```xml
<key>UserName</key>  <string>console</string>
<key>RunAtLoad</key> <true/>
<key>KeepAlive</key> <dict><key>SuccessfulExit</key><false/></dict>
<key>EnvironmentVariables</key>
<dict>
  <key>HOME</key> <string>/Users/console</string>
  <key>PATH</key> <string>/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
</dict>
```

Install with `sudo launchctl bootstrap system /Library/LaunchDaemons/<label>.plist`.
The file must be owned `root:wheel` and mode `644`, or launchd refuses it.

Done this way, **auto-login is unnecessary and should stay off**, and the box
boots to no session at all while serving normally.

The same reasoning applies to remote access: install Tailscale or similar as a
*system* daemon, never as a per-user GUI app, or you lose your way in the
moment nobody is logged in. See `remote-access.md`.

> FileVault remains a separate obstacle for an unattended reboot: an encrypted
> boot volume wants an unlock before anything starts, which a headless box
> cannot provide. Check with `fdesetup status`.

Use a dedicated account for the server (`console` below), separate from your
own. The Actions runner executes whatever is on `main` of a public repo, so
giving it its own home directory keeps it away from your personal files.

```bash
brew install colima docker docker-compose
colima start --cpu 4 --memory 6 --disk 20 --vm-type vz --mount-type virtiofs
```

colima is preferred over Docker Desktop here for one reason: it has no GUI.
Docker Desktop is an application tied to a graphical session, which adds a
second thing that can fail to start on a headless box.

Auto-start goes in a **LaunchDaemon**, per the table above, running
`/usr/local/bin/colima start -f`. `brew services start colima` is tempting and
does work, but it installs a LaunchAgent, which is the arrangement you are
trying to avoid. Keep the clone in that account's home, for example
`/Users/console/servers/console`.

Two things that will waste your time otherwise:

- **`colima start` adopts an existing profile** rather than creating a fresh
  one, and silently ignores `--disk` on a VM that already exists. Run
  `colima list` first to see what is already there.
- **`--save-config` is on by default**, so passing `--cpu`/`--memory`
  overwrites the saved values of whatever profile you named. Check before you
  resize someone else's VM.

### Windows

The console's original host. Still works; kept here because the details cost
real time to work out.

Docker Desktop with the **WSL2 backend** and **Linux containers**. Verify with
`docker version` and `docker compose version`. If the console later cannot
reach Docker, turn on WSL integration in Docker Desktop's settings.

Docker Desktop is a GUI app tied to an interactive session, so an unattended
reboot needs Windows auto-login as well as Docker Desktop's "start on login".
A service cannot start it into a session that does not exist.

Files in this repo use LF endings (enforced by `.gitattributes`), so cloning on
Windows will not corrupt anything that runs in a container. Run commands in
PowerShell or a WSL2 shell; `docker` works in both.

## 1. Create the shared network

Traefik and every app container live on one external network called `web`.

```bash
docker network create web
```

Ignore "network with name web already exists".

## 2. Generate the Fernet key

This key encrypts your stored secrets. It lives outside git, only on the
server, and losing it means losing every stored secret. Generate it **with a
container** so you need no local Python, and so the bytes are written directly
to the file:

```bash
docker run --rm -v "$PWD/secrets:/out" python:3.13-slim \
  python -c "import os,base64; open('/out/console_key','wb').write(base64.urlsafe_b64encode(os.urandom(32)))"
```

A Fernet key is 32 random bytes, url-safe-base64 encoded, so this needs no
extra library. Writing from inside the container also avoids PowerShell's
UTF-16 redirection, which would corrupt the key on Windows.

Verify it wrote a real 44-byte file (not empty, not a directory):

```bash
docker run --rm -v "$PWD/secrets:/out" busybox wc -c /out/console_key
```

Expect `44`. If it errors or shows `0`, delete anything at
`secrets/console_key` and rerun (a stale directory can appear there if you ran
`compose up` before generating the key). Never commit this file (it is already
gitignored).

## 3. Write the .env file

Create `.env` next to `compose.prod.yaml`:

```
TUNNEL_TOKEN=paste-in-step-4
CONSOLE_DOMAIN=example.com
CONSOLE_OIDC_OWNER=your-account
```

`CONSOLE_OIDC_OWNER` is the GitHub account whose repos may deploy here. It has
no default on purpose: unset, the console rejects every webhook rather than
trusting anyone who can mint a token.

> **Appending to this file later:** check it ends with a newline first, or your
> next `echo >>` lands on the end of the previous line and silently corrupts
> that value. `[ -n "$(tail -c1 .env)" ] && echo "" >> .env` first.

## 4. Create the Cloudflare tunnel

The tunnel is the only way in; nothing is exposed on the box or your LAN.

1. Cloudflare **Zero Trust** dashboard -> **Networks -> Tunnels -> Create a
   tunnel** -> connector **Cloudflared** -> name it (e.g. `home-server`).
2. Copy the **tunnel token** into `.env` as `TUNNEL_TOKEN`.
3. Add routes to the tunnel (type **Published application** in newer
   dashboards, **HTTP**, service **`traefik:80`**). cloudflared runs in the
   compose network and reaches Traefik by name; Traefik routes each request to
   the right container by its Host header.
   - `console.example.com` -> the console itself.
   - `*.example.com` (a wildcard) -> every future app becomes reachable with no
     further tunnel changes: register it in the console, deploy, and Traefik
     routes its subdomain by the label the deploy engine sets. The console
     never touches Cloudflare.
     If your plan refuses a proxied wildcard DNS record, skip the wildcard and
     add each app's hostname here when you register it (a fresh subdomain never
     conflicts).

   **Keep the wildcard LAST in the hostname list.** cloudflared evaluates these
   rules top to bottom and the first match wins; it does *not* prefer the more
   specific hostname. So `*.example.com` swallows every hostname listed below
   it, and the explicit `console` route only works because it sits above the
   wildcard. Any hostname you add later lands at the bottom, under the
   wildcard, and is silently dead on arrival: the wildcard sends it to Traefik,
   which has no router for it and returns `404 page not found`. There is no
   reorder control, so the fix is to delete the wildcard and re-add it, which
   appends it to the end again.

   The "Create a tunnel" wizard may not let you add routes until a connector is
   actually connected. If so, finish creating the tunnel, put its token in
   `.env`, bring the stack up (step 7) so the cloudflared container connects,
   then come back and add these routes.

## 5. Cloudflare Access: protect the UI, bypass the webhooks

GitHub's runners cannot log in to Access, and the `/hooks` endpoints
authenticate themselves with a signed GitHub OIDC token, so that path must
bypass Access while everything else requires your login.

Zero Trust -> **Access -> Applications**. Create **two** self-hosted apps
(Cloudflare evaluates the more specific path first):

- **App A, the webhooks**: hostname `console.example.com`, path `hooks`. One
  policy, action **Bypass**, include **Everyone**. (Safe: the console rejects
  any call whose OIDC token is not owned by `CONSOLE_OIDC_OWNER`.)
- **App B, the console UI**: hostname `console.example.com`, no path. One
  policy, action **Allow**, include your email.

Every other app subdomain is public by default (the wildcard route sends it to
Traefik). To put the same login in front of a specific app, flip the **access**
toggle on its project page in the console (enable that with the Cloudflare
Access token in step 10), or add the Access app by hand exactly like App B.
Leave apps that do their own auth, or that must receive third-party webhooks,
public.

Then add a rate limit so nobody can flood `/hooks` with wrong-owner calls (each
costs the console a token verification):

- Security -> **WAF -> Rate limiting rules -> Create**:
  - When: `hostname` equals `console.example.com` **and** URI path starts with
    `/hooks`
  - Then: **Block**, at **20 requests per 1 minute** per client IP.

## 6. Make the console image pullable

CI publishes `ghcr.io/<your-account>/console:latest` on every push to `main`
(check the repo's **Actions** tab for a green "build console image" run). GHCR
packages start **private**, so either:

- **Make it public** (simplest, no auth for the console): GitHub -> your
  profile -> **Packages -> console -> Package settings -> Change visibility ->
  Public**, or
- **Keep it private** and log in on the server: `docker login ghcr.io -u
  your-account` and paste a `read:packages` PAT. (This covers only the
  console's own image; private *app* images use the console Settings token in
  step 10.)

If you run a fork, also set `CONSOLE_IMAGE` in `.env` so compose pulls your
image rather than upstream's.

## 7. Bring it up

```bash
docker compose -f compose.prod.yaml up -d
docker compose -f compose.prod.yaml logs -f console
```

On first start the console runs its database migrations, then serves. Wait for
the uvicorn "Application startup complete" line.

Compose interpolates `.env` when it *parses* the file, so a missing
`CONSOLE_DOMAIN` or `CONSOLE_OIDC_OWNER` stops the whole command, even if you
asked for one service. That is deliberate: both are load bearing.

## 8. Verify, including a reboot

- **Inside the network** (works even before DNS propagates):
  ```bash
  docker run --rm --network web curlimages/curl -s -o /dev/null -w "%{http_code}\n" http://console:8000/api/projects
  ```
  Expect `200`.
- **Through Traefik on the real hostname**, which is how cloudflared reaches
  it. Nothing is published to the host, so test from inside the network:
  ```bash
  docker run --rm --network web curlimages/curl -s -o /dev/null -w "%{http_code}\n" \
    -H "Host: console.example.com" http://traefik:80/
  ```
- **Through the tunnel:** open `https://console.example.com`. Cloudflare Access
  prompts for your login, then the console loads.
- **The webhook path bypasses Access:** the webhooks are POST-only, so send a
  POST (a GET has no route and falls through to the SPA, returning `200`
  index.html, which tells you nothing):
  ```bash
  curl -s -o /dev/null -w "%{http_code}\n" -X POST -H "Content-Type: application/json" \
    -d "{}" https://console.example.com/hooks/build-started
  ```
  Expect `401` (the console reached it and rejected it for no token). A `302`
  to an Access **login page** means App A's path or action is wrong.
- **Reboot the machine and walk away.** This is the test people skip and later
  regret. With nobody logged in, everything must return on its own: the engine,
  all containers, the tunnel, and your remote access. If any of those needed a
  human, fix that now rather than discovering it during an outage.
- **Then log in and out of a second account** while watching the stack. A
  reboot test alone passes even on a fragile LaunchAgent setup, because
  auto-login hides the problem. Logging in as someone else is what exposes it.

## 9. First real deploy

1. In the console UI: **projects -> new**, register the app (repo, subdomain).
2. In the app repo, add the three files from the project's "next steps"
   checklist: `console.toml`, `Dockerfile`, `.github/workflows/deploy.yml`.
3. Add the app's secrets in the console.
4. **Private app images need a pull token.** In **Settings**, add a GitHub
   `read:packages` token once (step 10). One token covers every private app;
   public images need nothing.
5. Push the app repo. Watch the deploy appear and go live in the console.

## 10. Console Settings: private-image pulls and Access logins

Two server-level credentials live in the console's **Settings** page (top nav),
stored encrypted with the same key as your secrets. Add each once, in the
browser: no files, no compose edits.

- **GHCR token**: a GitHub PAT with `read:packages`, so the deploy engine can
  pull private app images.
- **Cloudflare Access**: an API token scoped to "Access: Apps and Policies ->
  Edit" plus your account id, which enables the per-project access toggle.

## 11. Backups, alerts, and credential expiry (recommended)

- **Backups**: set a passphrase and a private destination repo + token in
  Settings. **Keep the passphrase in your password manager.** The settings API
  is write-only by design, so the console will never show it to you again, and
  without it every stored bundle is unrecoverable. See `rehosting.md`.
- **Alerts**: set an ntfy topic in Settings. Until you do, the monitor still
  records health but every alert silently does nothing, because `alerts.send()`
  returns `False` with no topic configured. Uptime alerts, deploy-failure
  alerts, and expiry warnings all share that channel.
- **Credential expiry**: record an expiry date for each token that can lapse,
  and the console warns you through the same channel before it does.

## 12. The self-hosted runner

The console updates itself out-of-band: CI builds the image, then a deploy job
on your box pulls and recreates the container. That job needs a runner.

1. Register a self-hosted runner on this repo, on the server machine, under
   whichever account owns the deploy clone.
2. **Give it the label `console-host`.** The workflows select by that role
   label rather than by operating system.
3. Install it as a service so it survives reboots (`./svc.sh install` on
   macOS/Linux). **On macOS `svc.sh` writes a LaunchAgent**, which dies with the
   login session: a logout stops your deploys. Take the plist it generates,
   convert it to a LaunchDaemon (add `HOME` and `PATH`, drop `SessionCreate` and
   `ProcessType`, which are login-session concepts), and bootstrap it into the
   system domain instead.
4. Set repository variables under **Settings -> Secrets and variables ->
   Actions -> Variables**:
   - `CONSOLE_DEPLOY_DIR`: absolute path of the deploy clone on the box.
   - `CONSOLE_ENGINE_START`: a command to start your container engine, only if
     it is not already a boot-time service (for example `colima start`). Leave
     unset on Linux with systemd.

Service managers commonly start runners with a minimal `PATH` that omits where
docker was installed, which is why the workflows add the usual prefixes
themselves.

## Adding another domain

Each extra domain is a one-time manual Cloudflare setup, exactly like the
primary: add the zone, create a wildcard public hostname on the same tunnel
(remembering the ordering rule in step 4), then add the domain in the console's
Settings so projects can choose it. The console records which domains exist and
sets the Traefik host rule; it never touches Cloudflare DNS or the tunnel.

Changing an existing project's domain always requires a redeploy, because the
Traefik host label is set when the container is created.

## Updating the console later

Pushing to `main` rebuilds and redeploys automatically. To do it by hand:

```bash
cd "$CONSOLE_DEPLOY_DIR"
git pull --ff-only
docker compose -f compose.prod.yaml pull console
docker compose -f compose.prod.yaml up -d console
```

To roll back to a known-good image without waiting for a build, pin the tag for
one run:

```bash
CONSOLE_IMAGE=ghcr.io/your-account/console:1a2b3c4 \
  docker compose -f compose.prod.yaml up -d console
```

The `server ops` workflow does the same thing from a browser or phone, which is
useful when a bad image is restart-looping the console.

## Notes

- **Traefik must be v3.6+.** Docker Engine 29 dropped API versions older than
  1.40, and older Traefik fails with "client version 1.24 is too old". The
  symptom is a 404 on every route.
- **Container names do not resolve from the host**, only from inside the docker
  network. A deploy run from a host-side dev server always fails its health
  check for this reason, and the uptime monitor reads every app as down. That
  is the failure path working, not a bug.
- **Nothing is published to the host.** The only way in is the tunnel, which
  reaches Traefik over the docker network. To test locally, run curl inside the
  `web` network rather than against a host port.
