# Remote access

Reach the server from the Mac, from anywhere: a real shell on the box, and
`docker --context homebox ps` against the live engine. This closes the gap the
rest of the repo already assumes is closed. `build.yml` justifies its own
safety with "remote docker can always re-pin the last good tag", and CLAUDE.md
makes an independent way in part of the design, so that a bad console build can
never leave the console as the only way to reach the server.

SSH rides the **existing** Cloudflare tunnel and is gated by Access, so this
adds no inbound port, no router change, and no new always-on service on the PC.
The only thing that lands on the box is a **public** key, which is not a secret.

> **What this is not.** `cloudflared` runs as a container. If Docker Desktop is
> down, the tunnel is down and this SSH path is down with it, exactly when you
> most want a shell. Docker Desktop needs an interactive login session to start,
> so a reboot or a Windows Update can leave the box with everything stopped.
> Treat this as ad-hoc ops, not as disaster recovery. The break-glass path for
> that case is the ops button below, which rides the Actions runner instead (a
> native Windows service, a different failure domain).

The work splits in two. Part 1 needs only the Mac and a browser. Part 2 needs
you physically at the PC. Nothing in Part 1 works until Part 2 is done, so do
them in order and save the verifying for Part 3.

## Part 1: on the Mac

### 1. Generate a key

```
ssh-keygen -t ed25519 -f ~/.ssh/homebox_ed25519 -C "mac -> homebox"
```

Give it a passphrase. That encrypts the private key file at rest, so a stolen
laptop is not a stolen key. Load it into the agent once so the passphrase lives
in the macOS keychain instead of being retyped on every connection:

```
ssh-add --apple-use-keychain ~/.ssh/homebox_ed25519
```

This matters more than it looks: `docker --context` opens a fresh SSH
connection per command, so without the agent every `docker ps` would prompt.

Two files now exist. `homebox_ed25519` is the private key and never leaves this
Mac, not even during login. `homebox_ed25519.pub` is the public key and is the
only half that goes on the server.

### 2. Install cloudflared

```
brew install cloudflared
```

### 3. Write the SSH config

Add to `~/.ssh/config` (create it if absent):

```
Host homebox
  HostName ssh.samstuhl.com
  User Sam
  IdentityFile ~/.ssh/homebox_ed25519
  IdentitiesOnly yes
  AddKeysToAgent yes
  UseKeychain yes
  ProxyCommand cloudflared access ssh --hostname %h
```

`ProxyCommand` is the line doing the real work. Normally SSH opens a TCP
connection to port 22 on the target, which would fail here because the box has
no open ports. `ProxyCommand` instead tells SSH to run `cloudflared` and talk to
it over stdin/stdout as though it were the network. cloudflared authenticates to
Access and relays the bytes through the tunnel. SSH believes it is holding a
socket; it is really holding a relay.

The SSH session stays encrypted end to end inside that relay, so Cloudflare
carries the bytes but cannot read them. Access decides who may open the pipe;
SSH decides who may log in. Two independent locks.

`User Sam` is not cosmetic. See step 9.

### 4. Cloudflare: route and gate the hostname

Both are browser work, so you can do them now, but neither can be tested until
Part 2. The tunnel is token-managed, so its routes live in the dashboard and
the box is never touched.

**Route it.** Zero Trust -> **Networks -> Tunnels** -> your tunnel -> **Public
Hostname -> Add a public hostname**:

- Subdomain `ssh`, Domain `samstuhl.com`, no path
- **Service URL** `ssh://host.docker.internal:22`
- **Then move `*.samstuhl.com` back to the bottom of the list.** See below.

**The wildcard will eat this route if you let it.** cloudflared matches ingress
rules top to bottom, first match wins, and does *not* prefer the more specific
hostname. A new hostname is appended to the end, which puts it *under*
`*.samstuhl.com`, so it never matches its own rule: the wildcard hands it to
Traefik, which has no router for it and 404s. The dashboard has no reorder
control, so delete the `*.samstuhl.com` hostname and re-add it (`*` /
`samstuhl.com` / `http://traefik:80`), which appends it after the SSH rule. The
list must end up console, ssh, then the wildcard last. `console.samstuhl.com`
keeps working throughout, since it has its own rule above the wildcard.

The dashboard has a single **Service URL** field where the scheme *is* the
service type. Cloudflare's own docs and most screenshots still show the older
two-field form (a **Type** dropdown set to **SSH**, plus a bare `host:22`);
`ssh://host:22` is the same thing expressed in one field. `tcp://` works too,
but `ssh://` is the direct equivalent of the documented setup. (Verified against
the live dashboard 2026-07-16: `ssh://` accepted.)

`host.docker.internal`, **not `localhost`**. This is worth dwelling on, because
Cloudflare's docs say `localhost:22` and copying that will fail here. They assume
cloudflared is installed on the SSH machine itself. Ours runs in a container,
where `localhost` means the cloudflared container, which has no SSH server. So
this line is the tunnel deliberately hopping back out of Docker to the Windows
host, which is also why nothing needs publishing to the LAN. Docker Desktop
provides that name automatically.

**Gate it.** Zero Trust -> **Access -> Applications** -> **Add an application**
-> **Self-hosted**:

- Hostname `ssh.samstuhl.com`
- One policy, action **Allow**, include your email.
- Set a long **session duration** (a month is reasonable), or `docker` will
  bounce you to a browser login far too often.

**This gate is not optional.** Without it the tunnel publishes your SSH port to
the whole internet, gated only by your key. The Access app is what keeps the
tunnel-only posture intact. It is also the kill switch: delete the policy and
every route in dies instantly, from anywhere, without touching the box.

### Verify Part 1

`ssh homebox` cannot work yet, but three things are checkable now, and the third
is the one that matters:

```
ssh -G homebox | grep -E "hostname|user|proxycommand"   # config resolves
dig +short ssh.samstuhl.com                             # Cloudflare IPs, same as the console's
curl -s -o /dev/null -D - https://ssh.samstuhl.com | grep -i location
```

That last one must redirect to `cloudflareaccess.com/cdn-cgi/access/login/ssh.samstuhl.com`.
A redirect means the Access app is live and the route is **not** open to the
internet. Anything else (a timeout, a 502, no redirect) means the gate is not
on, and you should fix that before starting sshd in Part 2.

Then run `ssh homebox`. It authenticates in a browser once, caches the token,
and **fails**. That is correct. How it fails is the whole point:

`cloudflared` reports `websocket: bad handshake` for **any** non-101 response,
so the ssh-level error is identical whether the route is wrong or the origin is
simply absent. It tells you nothing. Read the HTTP status underneath instead:

```
TOK=$(cat ~/.cloudflared/ssh.samstuhl.com-*-token)
curl -s -o /dev/null -w "%{http_code}\n" -H "Cookie: CF_Authorization=$TOK" https://ssh.samstuhl.com/
curl -s https://a-hostname-that-does-not-exist.samstuhl.com/    # the wildcard's answer, for comparison
```

- **`404 page not found`, byte-identical to the nonexistent hostname**: the
  wildcard is swallowing the SSH route. Re-add the wildcard so it sits last.
- **`502`**: correct. The rule matched, the tunnel tried `host.docker.internal:22`,
  and nothing is listening because sshd does not exist yet. This is as far as
  Part 1 can go, and it means every hop before the box is proven.
- **`302` to a login**: the token expired. Rerun `ssh homebox` to re-auth.

## Part 2: at the Windows box

### 5. Install the SSH server

In an **admin** PowerShell:

```
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
Start-Service sshd
Set-Service -Name sshd -StartupType Automatic
```

This listens on port 22 on the LAN only. There is no router port-forward, so
nothing is reachable from the internet except through the tunnel, and the tunnel
is behind Access.

### 6. Install the public key

Copy the **contents** of `~/.ssh/homebox_ed25519.pub` from the Mac (one line,
starts `ssh-ed25519`). The private key stays on the Mac.

**Read this before you write the file anywhere else.** Because your account is
an administrator, sshd ignores `C:\Users\Sam\.ssh\authorized_keys` entirely.
The `Match Group administrators` block at the bottom of
`C:\ProgramData\ssh\sshd_config` redirects every admin to one shared file
instead. Put the key in the wrong place and sshd will silently ignore it and
fall back to asking for a password, with nothing in the output telling you why.

```
Add-Content C:\ProgramData\ssh\administrators_authorized_keys -Value "<paste the .pub line>"
icacls C:\ProgramData\ssh\administrators_authorized_keys /inheritance:r /grant "Administrators:F" /grant "SYSTEM:F"
```

The `icacls` line is required, not tidiness. sshd refuses to read that file if
anyone besides Administrators and SYSTEM can write it, and it refuses the same
silent way.

### 7. Leave the default shell as cmd.exe

Do nothing here. This step exists only to tell you not to do the tempting thing.

Windows OpenSSH launches `cmd.exe` by default. You will find registry snippets
online that set `HKLM:\SOFTWARE\OpenSSH\DefaultShell` to PowerShell. **Do not.**
`docker --context` works by running `docker system dial-stdio` over SSH, which
pipes raw binary through whatever shell SSH launches. Windows PowerShell 5.1
treats stdout as text and applies character-encoding conversion, which corrupts
that binary stream and breaks the remote context in a way that is miserable to
diagnose. cmd.exe passes bytes through untouched.

You lose nothing interactively: type `powershell` once connected, or run
`ssh homebox powershell` for a one-off.

### 8. Allow the account to reach Docker

```
Add-LocalGroupMember -Group docker-users -Member Sam
```

Almost certainly already true, since installing Docker Desktop does it. It
errors harmlessly if so. Log out and back in if you had to add it.

### 9. Confirm Docker answers over SSH

From the **Mac**, once steps 5 to 8 are done:

```
ssh homebox
```

The first connection opens a browser for the Access login, then drops you at a
`C:\Users\Sam>` prompt. Then:

```
ssh homebox "docker --version"
ssh homebox "docker ps"
```

Two things decide whether that second command works:

- **Docker Desktop's engine is a per-user named pipe.** It only answers to the
  account Docker Desktop is running under, and only while it is running. This is
  why the config says `User Sam`. It is also the reason for the caveat at the
  top of this page: no interactive session means no Docker Desktop means no
  engine, and no tunnel either.
- **PATH.** If `docker` is not found, the machine PATH that non-interactive SSH
  sessions inherit is missing
  `C:\Program Files\Docker\Docker\resources\bin`. Add it to the **system**
  PATH (not the user PATH) and restart sshd.

## Part 3: the Docker context

### 10. Create it

Back on the Mac:

```
docker context create homebox --docker "host=ssh://homebox"
```

`ssh://homebox` resolves through `~/.ssh/config`, so the ProxyCommand, the key,
and the username all apply. No credentials are stored in the context itself.

### 11. Verify

```
docker --context homebox ps
```

Expect three containers: `traefik`, `console`, `cloudflared`. Then the things
you actually wanted:

```
docker --context homebox logs --tail 50 console
docker --context homebox restart console
docker --context homebox exec -it console sh
```

### 12. Never run `docker context use`

```
docker context use homebox     # <- do not
```

That makes the box the default for **every** docker command in every terminal,
including tomorrow, when you have forgotten. A `docker compose up` in this repo
would then hit production. The next section explains why that particular mistake
is worse than it sounds.

Pass `--context homebox` explicitly every time. An alias keeps it short while
keeping it deliberate:

```
alias homebox='docker --context homebox'
```

## Do not run compose from the Mac

Use the context for docker commands. Do **not** use it for
`docker compose -f compose.prod.yaml`. SSH in and run compose on the box, in
`C:\Users\Sam\servers\console`.

The reason is not obvious, and the failure is quiet rather than loud. Compose
resolves paths **client-side**, on the machine you type on, and then hands the
resulting absolute paths to the remote daemon. `compose.prod.yaml` has a
relative bind mount and a file secret:

```
volumes:
  - ./data:/data
secrets:
  console_key:
    file: ./secrets/console_key
```

Run that from the Mac and `./data` resolves to
`/Users/sam/Desktop/repos/console/data`, which is then sent to the **Windows**
daemon. Docker Desktop creates that path as an empty directory and starts the
console on a **blank database**, while the real
`C:\Users\Sam\servers\console\data` sits untouched and unused. Same for the
Fernet key.

In practice the `TUNNEL_TOKEN` interpolation (also read client-side, from a
`.env` that only exists on the box) will probably hard-error first. That is
luck, not a safeguard. Do not rely on it.

So the remote context is for path-free API calls, which is most of what you
want: `ps`, `logs`, `restart`, `stop`, `start`, `inspect`, `exec`, `stats`,
`pull`. Anything that reads `compose.prod.yaml` runs over SSH, on the box:

```
ssh homebox
cd C:\Users\Sam\servers\console
docker compose -f compose.prod.yaml pull console
docker compose -f compose.prod.yaml up -d console
```

## The ops button (not built yet)

Everything above dies when Docker Desktop does, because cloudflared is a
container. The Actions runner is not: it is a native Windows service that starts
on boot with no login and polls GitHub outbound, so it survives exactly the
failure that strands you. Adding `workflow_dispatch` to a workflow puts a **Run
workflow** button in the Actions tab, which works from a phone.

Not built yet, deliberately. Paste this into
`.github/workflows/ops.yml` when you want it:

```yaml
name: server ops

on:
  workflow_dispatch:
    inputs:
      action:
        description: what to do on the box
        type: choice
        required: true
        options:
          - start-docker
          - restart-console
          - repin-console
          - compose-up
      tag:
        description: image tag for repin-console (e.g. a short sha)
        required: false
        default: latest

jobs:
  ops:
    runs-on: [self-hosted, Windows]
    steps:
      - name: ${{ inputs.action }}
        shell: powershell
        working-directory: C:\Users\Sam\servers\console
        run: |
          switch ("${{ inputs.action }}") {
            "start-docker" {
              Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe"
              Start-Sleep -Seconds 60
            }
            "restart-console" {
              docker restart console
            }
            "repin-console" {
              $env:CONSOLE_IMAGE = "ghcr.io/sam-stuhl/console:${{ inputs.tag }}"
              docker compose -f compose.prod.yaml up -d console
            }
            "compose-up" {
              git pull --ff-only
              docker compose -f compose.prod.yaml up -d
            }
          }
          # Scalar only. Never echo container output here: see below.
          docker inspect -f '{{.State.Status}}' console
```

**Why the fixed menu instead of a command box.** This repo is public, so Actions
run logs are a public web page that anyone can read without logging in. Only you
can press the button (that needs write access), but everyone can read what it
printed. GitHub masks Actions secrets in logs, but it cannot mask yours: app
secrets live encrypted in the console's own database and are injected as
container env by the deploy engine, so GitHub has never seen them and has
nothing to match against. A single `docker inspect banking-dash` would put the
Neon `DATABASE_URL`, password and all, on a public page permanently.

Scalar output is fine, whole objects are not:

```
docker inspect -f '{{.State.Status}}' console    # "running". Safe.
docker inspect console                           # dumps Env. Never.
```

`repin-console` is the one that matters most: it is the recovery from a bad
console image, and it is what makes `build.yml`'s "remote docker can always
re-pin the last good tag" true.

## Security

**Nothing new is stored on the box.** Only the public key lands there, and a
public key is not a secret: it lets the server verify a signature, and cannot
produce one. The private key never leaves the Mac, not even during login, since
SSH proves possession by signing a challenge rather than sending the key.

**No new inbound exposure.** No port is opened, no router rule changes. Port 22
listens on the LAN only. The one new path in is `ssh.samstuhl.com`, which exists
only inside the tunnel and only behind the Access policy from step 4. Without
that policy you would have published SSH to the internet, which is why that step
says what it says.

**No long-lived shared secret.** Access auth is your browser SSO, cached as a
short-lived token under `~/.cloudflared` on the Mac. There is no service token,
which is consistent with the same call made for `/hooks` (see CLAUDE.md).

**What to rotate, and when.**

| Thing | Where | Expires? | Do what |
|---|---|---|---|
| SSH private key | Mac only | No | Rotate if the Mac is lost or compromised: `ssh-keygen` a new pair, replace the line in `administrators_authorized_keys`. |
| SSH public key | Box | No | Delete its line to revoke that one Mac. |
| Access session token | Mac, `~/.cloudflared` | Yes, per session duration | Nothing. It re-auths in a browser by itself. |
| Access policy | Cloudflare | No | This is the kill switch. Delete it to revoke everything at once, from anywhere, without touching the box. |

**Not in the console's credential-expiry tracking, on purpose.** Nothing here
has an expiry date to warn about. It is also not a fit as things stand:
`credentials.py` alerts only for tokens the console itself stores, since
`check_expiries` skips any key absent from `settings_store.keys_set`. An SSH key
added to `TRACKED` would be silently ignored forever. If a Cloudflare Access
**service token** is ever added here (for scripting, no browser), that does have
a real expiry and would be worth tracking, but it would need `credentials.py`
taught to handle expiry-only entries first.

## Notes

- **The key file location is the gotcha that will get you.** Admin accounts read
  `C:\ProgramData\ssh\administrators_authorized_keys`, never
  `~/.ssh/authorized_keys`, and a wrong ACL on it fails just as silently. If key
  auth is being ignored and you are being asked for a password, it is this.
  `Get-Content C:\ProgramData\ssh\logs\sshd.log` is the confirmation.
- **cmd.exe vs dial-stdio.** `docker --context` pipes raw binary through the
  login shell. PowerShell 5.1 re-encodes it and corrupts it. Leave `DefaultShell`
  unset. Symptom if you ignore this: `ssh homebox` is fine, but
  `docker --context homebox ps` hangs or returns a protocol error.
- **Docker Desktop's engine is per-user and per-session.** SSH in as the same
  account Docker Desktop runs under, and expect nothing from Docker while nobody
  is logged in on the box.
- **PATH.** Non-interactive SSH sessions inherit the machine PATH, not your user
  PATH. `C:\Program Files\Docker\Docker\resources\bin` must be on the system one.
- **host.docker.internal.** Docker Desktop provides it automatically. If it ever
  fails to resolve from the cloudflared container, add
  `extra_hosts: ["host.docker.internal:host-gateway"]` to that service in
  `compose.prod.yaml`.
- **Never `docker context use`.** Always `--context homebox`. See the compose
  section for what a misfire costs.
- **Compose is client-side.** Paths and `.env` resolve on the Mac and are handed
  to the remote daemon. Run compose over SSH, on the box, always.
- **This path is not disaster recovery.** cloudflared is a container. Docker down
  means tunnel down means no SSH. The ops button above is the answer to that; the
  runner is a native service in a different failure domain.
- **Related:** `server-setup.md` (standing the box up in the first place),
  `manual-deploy.md` (what the deploy engine does by hand).
