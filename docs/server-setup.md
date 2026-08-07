# Server setup

Stand up the console on the Windows home server, behind Cloudflare, with the
deploy pipeline live. When this is done, `console.samstuhl.com` shows the
console (gated by Cloudflare Access), GitHub can POST to `/hooks`, and
pushing an app repo deploys it. Once it is up, **Settings** also configures the
operational extras in the browser, no files or restarts: private-image pulls,
the access-toggle credentials, off-box backups, uptime alerts, extra domains,
and credential-expiry warnings (steps 9-10).

The console is never deployed through its own engine (that would be circular).
It is built by CI (never on the server); the server only pulls. After this
runbook, pushing to `main` updates the console automatically: CI publishes the
image, then a deploy job on this box's self-hosted runner pulls and recreates
the container. See "Updating the console later" at the end for the manual
override, and `remote-access.md` for reaching the box from elsewhere once it
is up.

> Confirm the real domain first. CLAUDE.md flags `samstuhl.com` as an
> unverified spelling. Check it against your registrar before the Cloudflare
> steps; if it differs, adjust `CONSOLE_DOMAIN` and the Traefik label in
> `compose.prod.yaml` and the hostnames below.

## Prerequisites

- **Docker Desktop** on Windows with the **WSL2 backend** and **Linux
  containers**. Verify: `docker version` and `docker compose version` both
  respond. If the console later can't reach Docker, turn on WSL integration
  in Docker Desktop settings.
- The domain on **Cloudflare** (DNS managed by Cloudflare).
- This repo cloned, and a shell open in it:
  `git clone https://github.com/Sam-Stuhl/console` then `cd console`.
- Run commands in **PowerShell** or a **WSL2 shell**: `docker` works in
  both. Files in this repo use LF endings (enforced by `.gitattributes`),
  so cloning on Windows will not corrupt anything that runs in a container.

## 1. Create the shared network

Traefik and every app container live on one external network called `web`.

```
docker network create web
```

Ignore "network with name web already exists".

## 2. Generate the Fernet key

This key encrypts your stored secrets. It lives outside git, only on the
server, and losing it means losing every stored secret. Generate it **with a
container** so you do not need Python on Windows, and so the bytes are
written directly to the file (avoids PowerShell's UTF-16 redirection, which
would corrupt the key):

```
docker run --rm -v ${PWD}/secrets:/out python:3.13-slim \
  python -c "import os,base64; open('/out/console_key','wb').write(base64.urlsafe_b64encode(os.urandom(32)))"
```

A Fernet key is just 32 random bytes, url-safe-base64 encoded, so this needs
no extra library, and writing the bytes from inside the container avoids
PowerShell's UTF-16 redirection (which would corrupt the key). `${PWD}` works
in both PowerShell and WSL.

Verify it wrote a real 44-byte file (not empty, not a directory):

```
docker run --rm -v ${PWD}/secrets:/out busybox wc -c /out/console_key
```

Expect `44`. If it errors or shows `0`, delete anything at
`secrets/console_key` and rerun (a stale directory can appear there if you
ran `compose up` before generating the key). Never commit this file (it is
already gitignored).

## 3. Create the Cloudflare tunnel

The tunnel is the only way in; nothing is exposed on the PC or your LAN.

1. Cloudflare **Zero Trust** dashboard -> **Networks -> Tunnels -> Create a
   tunnel** -> connector **Cloudflared** -> name it (e.g. `home-server`).
2. Copy the **tunnel token** it shows.
3. Add routes to the tunnel (type **Published application** in newer
   dashboards, **HTTP**, service **`traefik:80`**). cloudflared runs in the
   compose network and reaches Traefik by name; Traefik routes each request
   to the right container by its Host header.
   - `console.samstuhl.com` -> the console itself.
   - `*.samstuhl.com` (a wildcard) -> every future app becomes reachable with
     no further tunnel changes: register it in the console, deploy, and
     Traefik routes its subdomain by the label the deploy engine sets. The
     console never touches Cloudflare.
     If your plan refuses a proxied wildcard DNS record, skip the wildcard and
     add each app's hostname here when you register it (a fresh subdomain
     never conflicts).

   **Keep the wildcard LAST in the hostname list.** cloudflared evaluates these
   rules top to bottom and the first match wins; it does *not* prefer the more
   specific hostname. So `*.samstuhl.com` swallows every hostname listed below
   it, and the explicit `console` route only works because it sits above the
   wildcard. Any hostname you add later lands at the bottom, under the wildcard,
   and is silently dead on arrival: the wildcard sends it to Traefik, which has
   no router for it and returns `404 page not found`. There is no reorder
   control, so the fix is to delete the wildcard and re-add it, which appends it
   to the end again. (Learned the hard way adding the SSH route on 2026-07-16;
   see remote-access.md.)

   The "Create a tunnel" wizard may not let you add routes until a connector
   is actually connected. If so, finish creating the tunnel, put its token in
   `.env` (below), bring the stack up (step 6) so the cloudflared container
   connects, then come back and add these routes.
4. Create a `.env` file next to `compose.prod.yaml`:
   ```
   TUNNEL_TOKEN=paste-the-token-here
   ```

## 4. Cloudflare Access: protect the UI, bypass the webhooks

GitHub's runners cannot log in to Access, and the `/hooks` endpoints
authenticate themselves with a signed GitHub OIDC token, so that path must
bypass Access while everything else requires your login.

Zero Trust -> **Access -> Applications**. Create **two** self-hosted apps
(Cloudflare evaluates the more specific path first):

- **App A, the webhooks**: hostname `console.samstuhl.com`, path `hooks`.
  One policy, action **Bypass**, include **Everyone**. (Safe: the console
  rejects any call whose OIDC token is not owned by Sam-Stuhl.)
- **App B, the console UI**: hostname `console.samstuhl.com`, no path.
  One policy, action **Allow**, include your email.

Every other app subdomain is public by default (the wildcard route sends it
to Traefik). To put the same login in front of a specific app, flip the
**access** toggle on its project page in the console (enable that with the
Cloudflare Access token in step 9), or add the Access app by hand exactly like
App B. Leave apps that do their own auth, or that must receive third-party
webhooks, public.

Then add a rate limit so nobody can flood `/hooks` with wrong-owner calls
(each costs the console a token verification):

- Security -> **WAF -> Rate limiting rules -> Create**:
  - When: `hostname` equals `console.samstuhl.com` **and** URI path starts
    with `/hooks`
  - Then: **Block**, at **20 requests per 1 minute** per client IP.
  (Far above your real volume; tune later if needed.)

## 5. Make the console image pullable

CI publishes `ghcr.io/sam-stuhl/console:latest` on every push to `main`
(check the repo's **Actions** tab for a green "build console image" run).
GHCR packages start **private**, so either:

- **Make it public** (simplest, no auth for the console): GitHub -> your
  profile -> **Packages -> console -> Package settings -> Change visibility
  -> Public**. Do this once, or
- **Keep it private** and log in on the server: `docker login ghcr.io -u
  Sam-Stuhl` and paste a `read:packages` PAT. (This covers only the console's
  own image; private *app* images use the console Settings token in step 9.)

## 6. Bring it up

```
docker compose -f compose.prod.yaml up -d
docker compose -f compose.prod.yaml logs -f console
```

On first start the console runs its database migrations, then serves. Wait
for the uvicorn "Application startup complete" line.

## 7. Verify

- **Inside the network** (works even before DNS propagates):
  ```
  docker run --rm --network web curlimages/curl -s -o /dev/null -w "%{http_code}\n" http://console:8000/api/projects
  ```
  Expect `200`.
- **Through the tunnel:** open `https://console.samstuhl.com`. Cloudflare
  Access prompts for your login, then the console loads.
- **The webhook path bypasses Access:** the webhooks are POST-only, so send
  a POST (a GET has no route and falls through to the SPA, returning `200`
  index.html, which tells you nothing):
  ```
  curl -s -o /dev/null -w "%{http_code}\n" -X POST -H "Content-Type: application/json" -d "{}" https://console.samstuhl.com/hooks/build-started
  ```
  Expect `401` (the console reached it and rejected it for no token). If you
  get a `302` to an Access **login page** instead, App A's path/Bypass is
  misconfigured (its policy action must be **Bypass**, include **Everyone**).

## 8. First real deploy

1. In the console UI: **projects -> new**, register the app (repo, subdomain).
2. In the app repo, add the three files from the project's "next steps"
   checklist: `console.toml`, `Dockerfile`, `.github/workflows/deploy.yml`.
3. Add the app's secrets in the console.
4. **Private app images need a pull token.** In the console's **Settings**
   page, add a GitHub `read:packages` token once (see step 9). One token
   covers every private app; public images need nothing.
5. Push the app repo. Watch the deploy appear and go live in the console.

**No Actions, or a broken build?** You do not need any of this to deploy an
image that already exists. On the project page, under **deployments**, use
**deploy an image**: give it a full GHCR ref including a tag, and the console
pulls and runs it through the same health-checked swap. It reads the repo's
`console.toml` for you if a GitHub account is connected (step 9), and takes a
pasted one if GitHub is unreachable. Nothing is ever built on the server.

## 9. Console Settings: GitHub, private-image pulls, and Access logins

The server-level credentials live in the console's **Settings** page (top nav),
stored encrypted with the same key as your secrets. Add each once, in the
browser: no files, no compose edits.

**GitHub connection** (optional). Lets you pick a repo from a list when
registering a project instead of typing `owner/repo`, and lets the console read
a repo's `console.toml` when you deploy an image yourself. It is an outbound
credential: the console calls GitHub with it, and it gives nobody access to the
console, which is still Cloudflare Access's job alone.

1. GitHub -> **Settings -> Developer settings -> OAuth Apps -> New OAuth App**.
   Name it anything, and set the homepage URL to your console.
2. Set **Authorization callback URL** to exactly
   `https://console.<your-domain>/api/github/callback`. GitHub refuses to
   return to any other address, so a typo here is the one thing that will
   stop this working.
3. **Register application**, then **Generate a new client secret**.
4. Put the **Client ID** and **Client secret** into Console -> **Settings ->
   github connection**. Nothing to edit on the server, and no restart.
   (`CONSOLE_GITHUB_CLIENT_ID` in `.env` still works for the id if you would
   rather keep it in the compose file; the saved one wins, the same as the
   Cloudflare account id.)
5. Same section -> **connect github**. GitHub asks you to approve, sends you
   straight back, and the page says who it connected as.

The callback needs no Access bypass: GitHub redirects your *browser* to it, so
it arrives with your Access session, unlike `/hooks`, which GitHub's servers
call directly.

There is deliberately no default client id: this is a public project, and a
shipped one would make its author the OAuth trust anchor for every install.
Without an app configured the feature is simply off, and the repo field stays
free text. Note the scope: an OAuth app can only ask for the coarse `repo`
scope, so the stored token can read and write your repos. Disconnecting forgets
the token here; revoke the authorization on GitHub to end it there too.

**GitHub packages token** (pull private app images). A private repo's GHCR
package is private, so the console needs a read token to pull it:

1. GitHub -> **Settings -> Developer settings -> Personal access tokens ->
   Tokens (classic) -> Generate new token**. Check only **`read:packages`**.
2. Console -> **Settings -> github packages token** -> paste it -> save.

One token covers every private app; public images need nothing. A deploy that
fails with "unauthorized ... add a GitHub read:packages token in Settings" is
telling you exactly this.

**Cloudflare Access** (gate an app's hostname with a login). A project's
**access** toggle then creates or removes a self-hosted Access app for
`{subdomain}.samstuhl.com` with an Allow policy for the emails you list;
turning it off deletes the app (the site goes public again). It manages Access
apps only, never DNS, the tunnel, or routing.

1. Cloudflare dashboard -> **My Profile -> API Tokens -> Create Token ->
   Custom token**. One permission: **Account -> Access: Apps and Policies ->
   Edit**, scoped to your account. Copy the token.
2. Find your **account id** (any domain -> Overview -> API on the right, or the
   dashboard URL).
3. Console -> **Settings -> cloudflare access** -> paste the API token and the
   account id -> save.

Without these the access toggle returns 503 and everything else keeps working.
The token is scoped to Access apps and policies alone, so even a console
compromise cannot touch DNS, the tunnel, or the rest of your account. (Mounting
`secrets/cf_api_token` and setting `CF_ACCOUNT_ID` in `.env` still works as a
fallback, but Settings is simpler.)

## 10. Backups, alerts, and credential expiry (recommended)

The rest of the console's operational features are configured the same way, in
**Settings**, in the browser. None are required for deploys to work, but the
first two are worth doing on day one.

**Backups** (an off-box, encrypted copy of the console's own database plus the
Fernet key). Lose the key and every stored secret is gone, so this is the backup
that matters.

1. Create a **private** GitHub repo for backups, e.g. `Sam-Stuhl/console-backups`.
2. Make a fine-grained PAT scoped to that one repo with **Contents: Read and
   write** (a classic token with `repo` also works).
3. Console -> **Settings -> backups**: set a **passphrase** (generate one and
   save it in your password manager, it cannot be recovered and is required to
   restore), then the repo (`owner/name`) and the token. Click **back up now**
   and confirm a bundle lands in the repo.

Backups then run nightly and prune to the retention count. Restore with
`python -m console.backup.restore <bundle>` (it prompts for the passphrase); pull
the backups repo with `git clone`, not a browser or `gh` download, which mangles
the binary. The passphrase can instead be a mounted secret
(`CONSOLE_BACKUP_PASSPHRASE_FILE`) if you prefer files over Settings; either way,
keep a copy off the box.

**Alerts** (a push notification when an app goes down or recovers, or a deploy
fails). The console publishes to an [ntfy](https://ntfy.sh) topic.

1. Install the ntfy app, pick a hard-to-guess topic name, and subscribe to it.
2. Console -> **Settings -> alerts**: paste the topic and click **send test** to
   confirm it reaches your phone.

**Credential expiry** (warn before a token lapses). Console -> **Settings ->
credential expiry**: set the expiry date for the GHCR, Cloudflare, and backup
tokens. About two weeks out the console warns through your ntfy topic, so an
expired token never silently breaks a deploy.

## Adding another domain

Apps serve at `{subdomain}.{domain}`. The primary domain is `CONSOLE_DOMAIN`;
to host apps under a second domain, give that domain the same one-time setup as
the primary, then tell the console about it:

1. Add the domain to Cloudflare as a **zone**: dashboard -> **Add a site**,
   enter `newdomain.com`, and switch its nameservers to Cloudflare at your
   registrar. Wait until the zone shows **Active**.
2. Route it through the same tunnel: **Zero Trust -> Networks -> Tunnels -> your
   tunnel -> Public Hostname -> Add a public hostname**. Set **Subdomain** `*`,
   **Domain** `newdomain.com`, **Type** `HTTP`, **URL** `traefik:80`, save. The
   wildcard covers every app's subdomain and creates the DNS for you. (If your
   plan refuses a proxied wildcard, add each app's hostname by hand instead.)
3. In the console: **Settings -> domains**, type `newdomain.com` and **add**. It
   appears in the list with a **remove** control; the primary is shown but
   fixed.

That is all. The console never touches Cloudflare DNS or the tunnel; it only
records which domains exist so a project's create form can offer them, sets the
Traefik `Host` rule to the chosen domain, and gates the right hostname when you
flip a project's access toggle. Registering a project then shows a **domain**
picker. To move an existing app, open its page -> **website -> domain ->
change**, pick the domain, and (if the app is protected) choose whether the
console moves its Cloudflare Access gate for you or you move it yourself; then
redeploy to route the new hostname.

## Updating the console later

Normally you do nothing: push to `main` and the console updates itself. CI
builds and publishes the image, then the `deploy` job in `build.yml` runs on
this box's self-hosted runner and does the pull and recreate for you.

That is out-of-band, not self-deployment: it is a plain `docker compose pull`
and `up`, never the console's own deploy engine, so a bad console build can
restart-loop the console but can never wedge the thing you would fix it with.

Run it by hand when you need to override that, most often to roll back to a
known-good image:

```
git pull
docker compose -f compose.prod.yaml pull console
docker compose -f compose.prod.yaml up -d console
```

**The deploy job only recreates the console.** A change to Traefik's or
cloudflared's section of `compose.prod.yaml` reaches the box on the next
`git pull` but does nothing until you recreate that service yourself, on the
box:

```
docker compose -f compose.prod.yaml up -d traefik
```

Traefik restarts in a second or two, and every site is briefly unreachable
while it does, so it is worth doing deliberately rather than by surprise.

To roll back, pin an older image instead of `latest`. CI tags every build with
the short sha of its commit to `main`, so pick one from the **Actions** tab or
the package's version list. In PowerShell:

```
$env:CONSOLE_IMAGE = "ghcr.io/sam-stuhl/console:1a2b3c4"
docker compose -f compose.prod.yaml up -d console
```

That pin is a stopgap, not a state change: `$env:` lasts only for that shell,
and the next push to `main` recreates the container on `latest` again. Use it
to get the console back, then fix forward or revert the commit.

The database survives updates: it is the `./data` bind mount next to
`compose.prod.yaml` (so the whole state is `./data` plus `./secrets/console_key`,
copyable to a new machine). Run these **on the box**, not over a remote Docker
context: compose resolves `./data` and the key secret on whichever machine you
type on, so running it remotely would start the console on a blank database.
`remote-access.md` explains that trap in full.

## Notes

- **Docker socket on Windows:** the `/var/run/docker.sock` bind mount works
  under the WSL2 backend. It gives the console full control of Docker, which
  is by design (it is the control plane), the same access Traefik needs.
- **No host ports:** nothing is published to the PC. To debug without the
  tunnel, temporarily add `ports: ["80:80"]` to the Traefik service, or curl
  from inside the `web` network as in step 7.
- **Reaching the box from elsewhere:** `remote-access.md` sets up SSH over this
  same tunnel plus a remote Docker context, so you can inspect and restart
  containers from the Mac without being at the PC. It needs no new inbound port
  and no new service here.
- **Container terminal:** the per-app terminal is the one websocket in the app
  (everything else polls). Cloudflare tunnels proxy websockets by default, and it
  sits behind the same Access login as the console UI, so no extra setup.
- **Liveness checks:** the uptime monitor pings each app container-to-container
  on the `web` network, so it only works with the console running in-container
  (as here); from a host-run dev server every app reads down.
