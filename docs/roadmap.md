# Console roadmap

> **All six items below shipped.** Kept as a record of what was planned and
> why, not as outstanding work.

Features to add, in priority order. The console's job is to handle the non-code
aspects of running my projects, so everything here is about doing more of that
from the web tool instead of the shell.

## 1. Run a one-off command in an app's container

Run app maintenance/setup commands from the console instead of a hand-rolled
`docker run`: one-time logins (e.g. the Robinhood device approval),
`simplefin_setup`, database migrations, backfills, ad-hoc scripts. A per-app
"run command" panel that executes in the app's container (or a fresh one from
its image) and streams the output. Non-interactive first; interactive stdin
(e.g. typing an SMS code) is a stretch goal. This is the biggest workflow gap
today, and it's what forced the manual `docker run` dance to log an app into
Robinhood.

## 2. Back up the console's own state

`./data` (the SQLite DB, holding every secret and setting) plus
`./secrets/console_key` (the Fernet key) are the single point of failure: lose
the key and everything in the DB is unrecoverable. Automated, encrypted backup
on a schedule to somewhere off the box (an object bucket, or a private repo).
Pairs with portability: a backup is effectively a portable snapshot.

## 3. Live uptime + alerts

Today the console only health-checks an app *during* a deploy; after that a
crash is silent, and the project "website" status dot is deploy-derived, not a
live ping. Add a periodic liveness check per app, make the dot reflect reality,
and alert (email / Discord / ntfy) when an app goes down or a deploy fails.

## 4. App controls (restart / stop / start)

Restart a wedged app, or stop/start it, from the UI without a full redeploy.
Small container ops that currently need the shell.

## 5. Credential expiry tracking

The GHCR PAT and Cloudflare API token can expire and silently break deploys or
the access toggle. Track expiry where the API exposes it, or let me set a
reminder date per credential in Settings, and warn before it lapses.

## 6. Multiple domains

Today apps serve at `{subdomain}.<domain>`, one configured domain. Support
more than one, so a future second domain can host its own apps:

- let a project pick its domain (or full hostname), not just a subdomain
- the Traefik Host rule and the tunnel route follow the chosen domain
- each domain needs a wildcard tunnel route plus Cloudflare DNS; the console
  could add these via the Cloudflare API, or document them as a one-time
  per-domain step
- the access toggle already gates per hostname, so gating carries over
