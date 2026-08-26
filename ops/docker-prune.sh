#!/bin/sh
# Remove app images the console has superseded.
#
# The deploy engine pulls a new image per deploy and never removes the old one,
# so without this the Docker disk grows until dockerd cannot start and every
# container on the box stops. See docs/server-setup.md step 11.
#
# Images referenced by a container (running OR stopped) are never candidates, so
# this only removes builds nothing is using. Rollback is unaffected: the console
# re-pulls from GHCR, which is the real source of truth for old images.
#
# Run weekly from cron. Cron gets a minimal PATH, so set one first.

set -eu
PATH=/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin
export PATH

KEEP_HOURS="${CONSOLE_PRUNE_KEEP_HOURS:-336}"   # two weeks
LOG="${CONSOLE_OPS_LOG:-$HOME/ops/prune.log}"

mkdir -p "$(dirname "$LOG")"

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG"
}

if ! docker info >/dev/null 2>&1; then
    log "docker not responding, nothing pruned"
    exit 0
fi

before=$(docker system df --format '{{.Size}}' 2>/dev/null | head -1)
removed=$(docker image prune -af --filter "until=${KEEP_HOURS}h" 2>&1 | tail -1)
after=$(docker system df --format '{{.Size}}' 2>/dev/null | head -1)

log "images ${before:-?} -> ${after:-?} (${removed})"

# Keep the log from becoming the thing that fills the disk.
if [ "$(wc -l < "$LOG")" -gt 500 ]; then
    tail -200 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
fi
