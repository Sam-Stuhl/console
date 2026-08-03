# Rehosting: moving the console to a different machine

How to move a running console to new hardware with seconds of downtime and no
change visible from the outside. Written from an actual move (Windows PC to a
headless Mac mini), so the traps below are ones that were actually hit, not
ones imagined at a whiteboard.

Read `server-setup.md` first if the target machine has nothing on it yet: this
document assumes you can already stand up a console and focuses on the move.

## What actually has to move

Less than people expect. Check this before planning anything:

```bash
docker inspect <app-container> --format '{{range .Mounts}}{{.Source}} -> {{.Destination}} {{end}}'
```

The deploy engine creates app containers with **no volumes and no bind
mounts**. Apps keep their state in an external database reached through an
opaque `DATABASE_URL` secret. So an app container holds nothing worth
preserving, and "migrating" the apps is just deploying them again on the new
box. If that command prints mounts for one of your apps, stop and handle that
app's data separately.

That leaves exactly two things that are irreplaceable:

| file | what it is |
| --- | --- |
| `data/console.db` | projects, encrypted secrets, settings, deploy history |
| `secrets/console_key` | the Fernet key those secrets are encrypted with |

One without the other is useless. Both together are everything.

## Phase 0: inventory, before you touch anything

Capture these from the running box while it is still healthy. Some are
surprisingly hard to reconstruct later.

- **The tunnel's ingress rules.** They live in Cloudflare, not on the box, but
  you want a copy to compare against afterwards. If the dashboard is awkward,
  `docker logs cloudflared | grep "Updated to new configuration"` prints the
  config the connector last pulled, including rule order.
- **`.env` keys.** Print key names only, never values.
- **Which images are running**, with tags: `docker ps --format '{{.Names}} {{.Image}}'`.
- **A fresh backup**, and confirm it succeeded rather than assuming the nightly
  one did.

## Phase 1: the new machine

Follow `server-setup.md`'s "Choose your host" for the engine and its
auto-start, then stop at the reboot test and actually run it. A box that cannot
come back unattended is not ready to receive production.

> **Verify the target is empty rather than believing it is.** A machine that
> has been used for anything may already have a container engine, running
> containers, and VMs you do not know about. Check `docker ps -a`, and on macOS
> `colima list`, before you start or stop anything.
>
> Be careful how you check. A non-interactive `ssh host 'which docker'` uses a
> minimal `PATH` that excludes `/usr/local/bin` and `/opt/homebrew/bin`, so it
> reports "not found" for software that is definitely installed. Export a sane
> `PATH` first, or you will conclude a busy machine is blank and act on it.

## Phase 2: move the state

**Do not copy `console.db` while the console is running.** SQLite is being
written to; a plain file copy can be torn. Use the backup, which takes a
consistent snapshot through SQLite's online backup API and bundles the key with
it.

1. **Get the bundle.** Clone the private backup repo rather than fetching
   through the GitHub contents API. The API returns base64-wrapped JSON, and
   naive decoding of it corrupts binary files in ways that look like a
   catastrophic backup failure when the backups are fine.

   ```bash
   git clone --depth 1 https://github.com/<you>/<backup-repo>.git
   ```

2. **Sanity-check it before trusting it.** A valid bundle is a 16-byte salt
   followed by a Fernet token, so byte 16 onwards begins `gAAAAA`:

   ```bash
   python3 -c "d=open('bundle.bin','rb').read(); print(len(d), d[16:22])"
   ```

3. **Restore it.** No Python setup needed on the new box; use the console image:

   ```bash
   docker run --rm -it -v "$PWD":/work -w /work \
     ghcr.io/<you>/console:latest \
     python -m console.backup.restore bundle.bin --out restored
   ```

   It prompts for the passphrase interactively.

4. **Put the files in place** at `data/console.db` and `secrets/console_key`
   (`chmod 600` the key).

5. **Verify the key matches the database**, which is the failure you do not
   want to discover later:

   ```bash
   docker exec console python -c "import sqlite3;from console.secrets import crypto;\
   r=sqlite3.connect('/data/console.db').execute('select value_encrypted from secrets').fetchall();\
   print(sum(1 for x in r if crypto.decrypt(x[0])), 'of', len(r), 'secrets decrypt')"
   ```

> ### The passphrase is the one thing with no recovery path
>
> The backup passphrase is stored in the console's own database, and the
> settings API is write-only by design, so **the console will never show it to
> you**. If it is not in your password manager, the only copy lives inside the
> box you are about to retire, encrypted by a key inside the bundle you cannot
> open without it.
>
> Recover it while the old box is still alive:
>
> ```bash
> docker exec console python -c "import sqlite3;from console.secrets import crypto;\
> r=sqlite3.connect('/data/console.db').execute(\
> \"select value_encrypted from settings where key='backup_passphrase'\").fetchone();\
> print(crypto.decrypt(r[0]))"
> ```
>
> Then put it in your password manager. Rotating to a new passphrase also
> works, but it permanently orphans every existing bundle.

## Phase 3: bring the new box up, still invisible

Start **traefik and console only**, deliberately not cloudflared:

```bash
docker compose -f compose.prod.yaml up -d traefik console
```

Nothing is published to the host, so verify from inside the network, using the
real hostname so you are testing Traefik's routing and not just the app:

```bash
docker run --rm --network web curlimages/curl -s -o /dev/null -w "%{http_code}\n" \
  -H "Host: console.example.com" http://traefik:80/
```

Then redeploy each app through the console's own engine (redeploy the build
that is currently live). This exercises the real pipeline, including private
image pulls, which use the GHCR token from the restored database rather than
any host-level `docker login`.

Do not move on until every hostname answers `200` here. Everything so far is
invisible from the internet and fully reversible.

## Phase 4: the cutover

The tunnel is identified by its token. Move the connector and every public
hostname follows, with no DNS change and no Access reconfiguration. That is why
this approach beats creating a second tunnel: the URLs cannot break, because
nothing about them changes.

**Stop the old connector first, then start the new one. Never run both.**

One tunnel supports many connectors, and Cloudflare load-balances across them.
That is a real HA feature, and here it is a hazard: with both boxes connected,
roughly half your requests would hit each console, each writing to its own
separate database. A brief outage is much cheaper than a split brain.

```bash
# on the old box
docker stop cloudflared
# on the new box
docker compose -f compose.prod.yaml up -d cloudflared
```

Expect on the order of 15 to 30 seconds. Verify each public URL returns what it
returned before, including redirect codes: an Access-gated host answering `302`
is correct, and a `200` there would mean the gate is gone.

> ### The cutover severs your own rollback
>
> If you reach the old box over SSH through that same tunnel, `docker stop
> cloudflared` kills your connection mid-command, and the old box becomes
> unreachable by the very path you planned to roll back with. Meanwhile
> `ssh.<domain>` now resolves to the *new* machine, because that hostname is an
> ingress rule on the tunnel rather than a property of any host.
>
> Before cutting over, make sure you have a way to the old box that does not
> ride the tunnel: its LAN address, a VPN, or physical access. Test it first.

## Phase 5: repoint the deploy pipeline

The self-hosted runner lives on the old box and is now pointing at nothing.

1. Register a runner on the new machine, labelled `console-host`, and install
   it as a service.
2. Update the `CONSOLE_DEPLOY_DIR` repository variable to the new clone's path,
   and `CONSOLE_ENGINE_START` if the new host needs one.
3. Push a trivial commit to `main` and watch the deploy job run on the new
   runner. That is the end-to-end proof.

The workflows select the runner by role label, not by operating system, so
moving between platforms needs no workflow edits.

## Phase 6: the reboot test

Reboot the new machine and let it come back with nobody logged in. Verify:

- remote access returns **before** any login (a system daemon, not a user app)
- the container engine starts on its own
- every container returns (`restart: unless-stopped` covers this, but confirm)
- the tunnel reconnects
- the runner reports online to GitHub

On macOS specifically: a scripted restart via `osascript ... to restart`
silently does nothing when another session is logged in, because macOS raises a
confirmation dialog no one can answer. Use `sudo reboot`.

## Phase 7: decommission

Order matters, cheapest and most reversible first.

1. **Stop the containers** on the old box. `restart: unless-stopped` honors a
   manual stop, so they stay down through reboots. Instantly reversible.
2. **Leave the machine reachable** for a few days. It is a free extra copy of
   your state while the new box is still young.
3. **Remove the old runner registration** and disable the engine's auto-start,
   so nothing quietly comes back.
4. **Only then, erase the state.** The old `data/` and `secrets/` hold your
   Fernet key and every secret. If that machine will be sold, shared, or
   reinstalled, delete them properly rather than with a plain `rm`.

## Traps worth reading before you start

- **`ssh.<domain>` follows the tunnel, not the machine.** After a cutover it
  points at the new host. Name your SSH aliases for the role rather than the
  machine, or they become lies.
- **Two consoles pointed at one tunnel is a split brain.** Never overlap.
- **Fetch backup bundles over git**, not the GitHub contents API.
- **Newline-guard before appending to `.env`.** Without a trailing newline your
  `echo >>` lands on the end of the previous value and corrupts it silently.
- **Check whether alerting is actually configured.** If no ntfy topic is set,
  every alert path silently does nothing, so a quiet migration proves nothing
  about your monitoring.
- **The target machine may not be as empty as you were told.**
