"""Pure deploy planning: names, labels, env, priorities. No Docker SDK, no
database, no I/O, so every rule here is testable with plain dicts. The
shapes come from docs/manual-deploy.md; the engine executes them.

Router priorities: Traefik routes a request to the highest-priority router
whose rule matches. During a deploy two routers match the same Host rule,
so the new, unverified container's router must always lose. Labels cannot
change on a running container, which rules out a constant: instead each
deployment's priority is the live one's minus 1, counting down from
PRIORITY_START."""

import hashlib
import re

from console import config
from console.schema.console_toml import ConsoleConfig

ROUTER_PRIORITY_RE = re.compile(r"^traefik\.http\.routers\.[^.]+\.priority$")
# A docker image tag: what may follow the colon in a ref.
IMAGE_TAG_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")


def image_prefix() -> str:
    """Images must come from the configured owner's GHCR namespace. Derived
    from CONSOLE_OIDC_OWNER so a build cannot smuggle in a third party's image,
    and so this holds for whoever runs the console."""
    return f"ghcr.io/{config.OIDC_OWNER.lower()}/"


def validate_image(ref: str) -> str:
    """Check an image ref the console is asked to deploy, and return its tag.

    Both entry points use this: the build webhook, where the ref comes from a
    workflow, and the manual deploy, where it is typed. The namespace rule is
    the same for both, and the pull auth the engine builds only works there
    anyway."""
    ref = ref.strip()
    if not config.OIDC_OWNER:
        # Same stance as oidc.verify: with no owner set, no namespace is
        # trusted, and saying so beats rejecting everything against "ghcr.io//".
        raise ValueError(
            "no CONSOLE_OIDC_OWNER is set, so no image namespace is trusted. "
            "Set it to the GitHub account whose images this console may deploy."
        )
    if not ref.lower().startswith(image_prefix()):
        raise ValueError(f"image ref missing or not under {image_prefix()}")
    _, _, tag = ref.rpartition(":")
    # rpartition on a ref with no colon after the prefix returns the whole
    # string as the tail, so check the separator really was there.
    if ":" not in ref[len(image_prefix()) :] or not IMAGE_TAG_RE.match(tag):
        raise ValueError(f'image "{ref}" needs a tag, like "{image_prefix()}app:abc1234"')
    return tag


GIT_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")


def short_sha(ref: str) -> str:
    """A git sha shortened to 7 characters; any other tag left whole.

    The deployment's sha column holds whatever identified the build: a commit
    sha from CI, or the image tag of a manual deploy. Slicing a tag like
    "manual-bc1c66d" to seven characters gave "manual-", which names nothing."""
    return ref[:7] if GIT_SHA_RE.match(ref) else ref


def container_name(app_name: str, ref: str) -> str:
    """The container (and Traefik router) name for a deployment.

    A commit sha keeps the runbook's short-sha suffix. Any other tag gets a
    short digest of itself instead: it is unique per tag, so two manual deploys
    of different builds never share a name, and it is always label-safe, which
    the tag itself is not (a dot in a router name breaks the label key). The
    full tag is on the console.sha label and the deployment row."""
    if GIT_SHA_RE.match(ref):
        return f"{app_name}-{ref[:7]}"
    return f"{app_name}-{hashlib.sha256(ref.encode()).hexdigest()[:7]}"


def host_rule(subdomain: str, domain: str) -> str:
    return f"Host(`{subdomain}.{domain}`)"


def health_url(name: str, port: int, path: str) -> str:
    return f"http://{name}:{port}{path}"


def build_labels(
    name: str,
    subdomain: str,
    domain: str,
    port: int,
    priority: int,
    project_id: str,
    deployment_id: str,
    sha: str,
) -> dict[str, str]:
    return {
        "traefik.enable": "true",
        f"traefik.http.routers.{name}.rule": host_rule(subdomain, domain),
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
