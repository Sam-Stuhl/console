#!/bin/sh
# Warn while there is still time to act, and shout if Docker is already down.
#
# This check cannot live in the console. The uptime monitor runs inside the
# console container, so the failure it would most need to report (a full disk
# stopping dockerd, which stops every container) is exactly the one that stops
# the monitor too. On 2026-08-26 that silence lasted 1d10h. So this runs on the
# host, outside Docker, from cron.
#
# Alerts go to the same ntfy topic the console uses. Put the topic in
# ~/.config/console-ops/ntfy-topic (chmod 600); without it this exits quietly.

set -eu
PATH=/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin
export PATH

THRESHOLD="${CONSOLE_DISK_THRESHOLD:-80}"
TOPIC_FILE="${CONSOLE_NTFY_TOPIC_FILE:-$HOME/.config/console-ops/ntfy-topic}"
STATE="${CONSOLE_OPS_STATE:-$HOME/ops/disk-watch.state}"
LOG="${CONSOLE_OPS_LOG:-$HOME/ops/disk-watch.log}"

mkdir -p "$(dirname "$STATE")" "$(dirname "$LOG")"

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG"
}

# One alert per condition per day, so a slow fill does not become 24 push
# notifications. The state file holds "condition:YYYY-MM-DD" lines.
already_alerted_today() {
    [ -f "$STATE" ] && grep -qx "$1:$(date '+%Y-%m-%d')" "$STATE"
}

mark_alerted() {
    echo "$1:$(date '+%Y-%m-%d')" >> "$STATE"
    tail -50 "$STATE" > "$STATE.tmp" && mv "$STATE.tmp" "$STATE"
}

notify() {
    condition="$1"; title="$2"; priority="$3"; body="$4"
    log "$body"
    already_alerted_today "$condition" && return 0
    [ -r "$TOPIC_FILE" ] || { log "no ntfy topic at $TOPIC_FILE, not alerting"; return 0; }
    topic=$(tr -d ' \n' < "$TOPIC_FILE")
    [ -n "$topic" ] || return 0
    if curl -fsS --max-time 20 -d "$body" \
        -H "Title: $title" -H "Priority: $priority" -H "Tags: warning" \
        "https://ntfy.sh/$topic" >/dev/null 2>&1; then
        mark_alerted "$condition"
    else
        log "ntfy POST failed"
    fi
}

host=$(hostname -s)

if ! docker info >/dev/null 2>&1; then
    notify docker-down "console: Docker is DOWN" urgent \
        "Docker is not responding on $host. Every container is stopped. Check the disk first: colima ssh -- df -h /var/lib/docker"
    exit 0
fi

# Where images actually live. Under colima that path is inside the VM, and the
# host's own free space says nothing about it.
if command -v colima >/dev/null 2>&1 && colima status >/dev/null 2>&1; then
    used=$(colima ssh -- df --output=pcent /var/lib/docker 2>/dev/null | tr -dc '0-9')
else
    root=$(docker info --format '{{.DockerRootDir}}' 2>/dev/null || echo /var/lib/docker)
    used=$(df -P "$root" 2>/dev/null | awk 'NR==2 {print $5}' | tr -dc '0-9')
fi

if [ -z "${used:-}" ]; then
    log "could not read disk usage"
    exit 0
fi

if [ "$used" -ge "$THRESHOLD" ]; then
    notify disk-high "console: disk filling" high \
        "Docker disk is ${used}% full on $host (threshold ${THRESHOLD}%). Run: docker image prune -af --filter until=336h"
else
    log "ok, ${used}%"
fi

if [ "$(wc -l < "$LOG")" -gt 500 ]; then
    tail -200 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
fi
