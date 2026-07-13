# Server setup

Stand up the console on the Windows home server, behind Cloudflare, with the
deploy pipeline live. When this is done, `console.samstuhl.com` shows the
console (gated by Cloudflare Access), GitHub can POST to `/hooks`, and
pushing an app repo deploys it.

The console is run **by hand** here and is never deployed through its own
engine. It is built by CI (never on the server); the server only pulls.

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
- Run commands in **PowerShell** or a **WSL2 shell** — `docker` works in
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
     console never touches Cloudflare. Cloudflare matches the most specific
     hostname first, so the explicit `console` route and the wildcard coexist.
     If your plan refuses a proxied wildcard DNS record, skip the wildcard and
     add each app's hostname here when you register it (a fresh subdomain
     never conflicts).

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

- **App A, the webhooks** — hostname `console.samstuhl.com`, path `hooks`.
  One policy, action **Bypass**, include **Everyone**. (Safe: the console
  rejects any call whose OIDC token is not owned by Sam-Stuhl.)
- **App B, the console UI** — hostname `console.samstuhl.com`, no path.
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

## 9. Console Settings: private-image pulls and Access logins

Two server-level credentials live in the console's **Settings** page (top nav),
stored encrypted with the same key as your secrets. Add each once, in the
browser: no files, no compose edits.

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

## Adding another domain

Apps serve at `{subdomain}.{domain}`. The primary domain is `CONSOLE_DOMAIN`;
to host apps under a second domain, give that domain the same one-time setup as
the primary, then tell the console about it:

1. In Cloudflare, add a **wildcard tunnel route** `*.newdomain.com` -> service
   `traefik:80` on the same tunnel (exactly like the primary's wildcard in step
   3), and make sure its DNS is managed by Cloudflare. If your plan refuses a
   proxied wildcard, add each app's hostname on the new domain by hand when you
   register it.
2. In the console: **Settings -> domains**, add `newdomain.com` (comma-separated
   for more than one).

That is all. The console never touches Cloudflare DNS or the tunnel; it only
records which domains exist so a project's create form can offer them, sets the
Traefik `Host` rule to the chosen domain, and gates the right hostname when you
flip a project's access toggle. Registering a project then shows a **domain**
picker; pick the new domain and deploy.

## Updating the console later

```
git pull
docker compose -f compose.prod.yaml pull console
docker compose -f compose.prod.yaml up -d console
```

The database is on the `console-data` volume and survives updates. The
console is always updated this way, by hand, never through its own engine,
so a bad console build can never take the console offline as the only way to
reach the server.

## Notes

- **Docker socket on Windows:** the `/var/run/docker.sock` bind mount works
  under the WSL2 backend. It gives the console full control of Docker, which
  is by design (it is the control plane), the same access Traefik needs.
- **No host ports:** nothing is published to the PC. To debug without the
  tunnel, temporarily add `ports: ["80:80"]` to the Traefik service, or curl
  from inside the `web` network as in step 7.
