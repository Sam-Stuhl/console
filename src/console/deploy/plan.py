"""Pure deploy planning: names, labels, env, priorities. No Docker SDK, no
database, no I/O, so every rule here is testable with plain dicts. The
shapes come from docs/manual-deploy.md; the engine executes them.

Router priorities: Traefik routes a request to the highest-priority router
whose rule matches. During a deploy two routers match the same Host rule,
so the new, unverified container's router must always lose. Labels cannot
change on a running container, which rules out a constant: instead each
deployment's priority is the live one's minus 1, counting down from
PRIORITY_START."""

import re

from console import config
from console.schema.console_toml import ConsoleConfig

ROUTER_PRIORITY_RE = re.compile(r"^traefik\.http\.routers\.[^.]+\.priority$")


def container_name(app_name: str, sha: str) -> str:
    return f"{app_name}-{sha[:7]}"


def host_rule(subdomain: str) -> str:
    return f"Host(`{subdomain}.{config.DOMAIN}`)"


def health_url(name: str, port: int, path: str) -> str:
    return f"http://{name}:{port}{path}"


def build_labels(
    name: str,
    subdomain: str,
    port: int,
    priority: int,
    project_id: str,
    deployment_id: str,
    sha: str,
) -> dict[str, str]:
    return {
        "traefik.enable": "true",
        f"traefik.http.routers.{name}.rule": host_rule(subdomain),
        f"traefik.http.routers.{name}.entrypoints": "web",
        f"traefik.http.routers.{name}.priority": str(priority),
        f"traefik.http.services.{name}.loadbalancer.server.port": str(port),
        # Ownership labels: how the engine finds what it created. Traefik
        # ignores them.
        "console.managed": "true",
        "console.project": project_id,
        "console.deployment": deployment_id,
        "console.sha": sha,
    }


def extract_router_priority(labels: dict[str, str]) -> int | None:
    for key, value in labels.items():
        if ROUTER_PRIORITY_RE.match(key):
            try:
                return int(value)
            except ValueError:
                return None
    return None


def compute_priority(live_priority: int | None) -> int:
    if live_priority is None:
        return config.PRIORITY_START
    priority = live_priority - 1
    if priority < 1:
        # Traefik treats priority 0 as "unset", which would reintroduce
        # the nondeterministic overlap this scheme exists to prevent.
        raise ValueError(f"router priority would reach {priority}; refusing")
    return priority


def build_env(cfg: ConsoleConfig, decrypted_secrets: dict[str, str]) -> dict[str, str]:
    overlap = sorted(set(cfg.env) & set(decrypted_secrets))
    if overlap:
        raise ValueError(
            f'"{overlap[0]}" is set in both [env] and secrets; remove one'
        )
    return {**cfg.env, **decrypted_secrets}
