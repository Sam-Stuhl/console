# Remote access

How to reach the server from elsewhere: a real shell on the box, and the
ability to drive its Docker engine from your laptop.

## The requirement this exists to satisfy

The console must never be the only way to reach the server. If a bad deploy or
a wedged engine takes the console down, you need a way in that does not depend
on it. `build.yml` justifies its own safety with "remote docker can always
re-pin the last good tag", which is only true if that path actually exists.

There is a sharper corollary that is easy to get wrong: **your access path must
not depend on Docker either.** cloudflared runs as a container, so any route
through the tunnel dies exactly when Docker does, which is precisely when you
want a shell. Design for that, or discover it during an outage.

So prefer a path that starts at boot, as a system service, independent of both
Docker and of anyone being logged in.

## Option A: a mesh VPN (recommended)

Tailscale, or any equivalent, installed as a **system daemon**. This is the
better primary path: it starts at boot before any login, it survives Docker
being down, and it needs no inbound ports.

**Install it as a system service, not as a per-user desktop app.** This matters
more than it sounds. A desktop VPN client runs inside a logged-in user's
session, so the machine drops off the network whenever that user is logged out:
after a reboot that lands at a login screen, or the moment you change which
account auto-logs-in. On a headless box that means losing remote access with no
remote way to restore it.

On macOS the App Store build cannot do this. Use a package that installs a
`LaunchDaemon` under `/Library/LaunchDaemons`, or run the open-source
`tailscaled` under one. On Linux the packaged systemd unit already does the
right thing.

Verify with a reboot: the box must be reachable **before** anyone logs in. That
is the whole point, and it is the one thing worth testing deliberately.

> If both a desktop client and a system daemon are installed, they fight over
> the interface, and CLI tools may auto-discover the wrong one. Symptoms
> include a version mismatch warning and commands that appear to succeed while
> reporting another daemon's state. Remove one, or point the CLI at the right
> socket explicitly.

## Option B: SSH over the Cloudflare tunnel

Useful when you already run the tunnel and want neither a second vendor nor
another always-on service. It adds no inbound port, and only a public key ever
lands on the box.

Know its limits before relying on it:

- **It dies with Docker**, because cloudflared is a container.
- **It follows the tunnel, not the machine.** The hostname is an ingress rule,
  so after a rehost it points at whatever now runs the connector.
- **Non-HTTP routes cannot ride a wildcard.** SSH carries no hostname to route
  on (no Host header, no SNI), so it needs its own explicit rule, placed
  **above** any wildcard. A rule added below one is dead on arrival. See
  `server-setup.md` on ordering.

### Setting it up

1. **Generate a key** and put the public half on the server, for the account
   that owns the Docker socket.
2. **Add a tunnel route**: hostname `ssh.example.com`, type **SSH**, service
   `ssh://host.docker.internal:22`, positioned above the wildcard.
3. **Gate it with Access**, so the hostname is not open to anyone who learns it.
4. **Configure the client:**

   ```
   Host console-tunnel
     HostName ssh.example.com
     User <account that owns the docker socket>
     IdentityFile ~/.ssh/your_key
     IdentitiesOnly yes
     ProxyCommand cloudflared access ssh --hostname %h
   ```

`ProxyCommand` replaces the usual TCP connect: ssh talks to cloudflared over
stdin/stdout, and cloudflared relays through the tunnel after authenticating to
Access. The box keeps no open ports.

## The SSH server, by platform

### Linux and macOS

Enable the built-in SSH server (`sshd`, or Remote Login on macOS) and put your
public key in `~/.ssh/authorized_keys` for the account that owns the Docker
socket. Nothing unusual.

If the server runs under a dedicated account, remember that account needs its
own `authorized_keys`; copying the file requires fixing ownership and modes
(`700` on `.ssh`, `600` on the file) or sshd ignores it silently.

### Windows

Three silent failure modes, each of which costs an afternoon:

- **Administrator accounts do not read `~/.ssh/authorized_keys`.** They read
  `C:\ProgramData\ssh\administrators_authorized_keys`, and that file needs
  strict ACLs or sshd ignores it without logging why.
- **Leave OpenSSH's `DefaultShell` as `cmd.exe`.** `docker system dial-stdio`
  pipes raw binary, and PowerShell 5.1 re-encodes it, corrupting the stream.
  The remote Docker context then fails in baffling ways.
- **Docker Desktop's engine is a per-user named pipe.** SSH in as the account
  it runs under, and expect nothing to answer while nobody is logged in.

## Driving Docker from your laptop

Once SSH works, a Docker context runs local docker commands against the
server's engine:

```bash
docker context create server --docker "host=ssh://<your-ssh-alias>"
docker --context server ps
```

### Two rules

**Never run `docker compose` over a remote context.** Compose resolves relative
paths, file-based secrets, and `.env` on the **client** side, then hands the
results to the remote daemon. Your laptop's paths do not exist on the server,
so the console would come up on a blank database while the real one sat
untouched beside it. Remote contexts are for path-free API calls: `ps`, `logs`,
`inspect`, `restart`, `exec`. Compose runs on the box.

**Never `docker context use`.** Always pass `--context` explicitly. A default
context silently pointing at production is how you restart the wrong thing.

## Naming your aliases

Name each alias for what it actually tracks:

- A VPN address points at **one specific machine**, so name it after that
  machine: `mac-mini-console`, `mac-mini-personal`.
- A tunnel hostname points at **whatever host runs the console**, so name it
  for the role: `console-tunnel`. It stays correct across a rehost, where a
  machine-named alias would quietly become a lie.

If one machine hosts both a server account and a personal one, give each its
own alias. It makes "which account am I about to break something as" obvious at
a glance.

## The ops button

`.github/workflows/ops.yml` offers a small fixed menu (status, restart the
console, re-pin a known-good image, compose up, start the engine) that runs on
the server's self-hosted runner and can be dispatched from a browser or phone.

Its value is that the runner polls GitHub **outbound**, so it keeps working
when inbound paths are unhealthy. Its real job is re-pinning a known-good image
when a bad one is restart-looping the console.

What it cannot do is help when the runner itself is not running. If the machine
rebooted and the runner never started, nobody is home to press the button.

So install the runner as a **system service too**, not just your VPN. On macOS
the runner's own `svc.sh` writes a LaunchAgent, which stops the moment that
account logs out: a logout silently disables your deploys, and the button you
would reach for is gone with them. Convert it to a LaunchDaemon. See
`server-setup.md` for the shape.

**If the repo is public, the run logs are a public web page.** GitHub masks its
own Actions secrets; it cannot mask yours, because app secrets live encrypted in
the console's database and are injected as container environment, so GitHub has
never seen them and has nothing to match against. Hence a fixed action menu
rather than a free-form command box, and scalar-only output. `docker inspect -f
'{{.State.Status}}' console` is fine. A bare `docker inspect`, `docker logs`,
`docker ps`, or anything echoing an environment variable would publish your
secrets permanently.

## Security notes

- Only a public key lands on the box. Nothing here expires, so there is nothing
  for the console's credential-expiry tracking to warn about.
- Gate any tunnel-exposed hostname with Access.
- Keep the deploy clone on the box deploy-only: edit elsewhere, push, let the
  box pull.
- A self-hosted runner executes whatever is on `main`. Give it a dedicated
  account rather than your personal one, so its blast radius is a home
  directory of server state rather than everything you own.
