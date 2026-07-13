"""Read-only container queries. The shape_* / compute_* functions are pure
so they can be tested against canned payloads without a Docker daemon."""

from console import config
from console.docker.client import get_client, run

MASK = "••••••••"


def shape_container(attrs: dict) -> dict:
    state = attrs.get("State", {})
    status = state.get("Status", "unknown")
    return {
        "id": attrs["Id"][:12],
        "name": attrs.get("Name", "").lstrip("/"),
        "image": attrs.get("Config", {}).get("Image", ""),
        "state": status,
        "created_at": attrs.get("Created"),
        "started_at": state.get("StartedAt") if status == "running" else None,
        "finished_at": state.get("FinishedAt") if status == "exited" else None,
        "exit_code": state.get("ExitCode") if status == "exited" else None,
    }


def shape_detail(attrs: dict) -> dict:
    net = attrs.get("NetworkSettings", {})
    host_config = attrs.get("HostConfig", {})
    shaped = shape_container(attrs)
    shaped.update(
        {
            "env": [_mask_env(e) for e in attrs.get("Config", {}).get("Env") or []],
            "labels": attrs.get("Config", {}).get("Labels") or {},
            "networks": sorted((net.get("Networks") or {}).keys()),
            "ports": _shape_ports(net.get("Ports") or {}),
            "restart_policy": (host_config.get("RestartPolicy") or {}).get("Name", ""),
        }
    )
    return shaped


def _mask_env(entry: str) -> dict:
    key, _, _ = entry.partition("=")
    return {"key": key, "value": MASK}


def _shape_ports(ports: dict) -> list[dict]:
    # {"80/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8080"}], "9000/tcp": None}
    shaped = []
    for container_port, bindings in sorted(ports.items()):
        shaped.append(
            {
                "container_port": container_port,
                "host_ports": sorted({b["HostPort"] for b in bindings or []}),
            }
        )
    return shaped


def _total_usage(cpu_block: dict) -> int:
    return cpu_block.get("cpu_usage", {}).get("total_usage", 0)


def compute_stats(stats: dict) -> dict:
    cpu = stats.get("cpu_stats", {})
    precpu = stats.get("precpu_stats", {})
    cpu_delta = _total_usage(cpu) - _total_usage(precpu)
    system_delta = cpu.get("system_cpu_usage", 0) - precpu.get("system_cpu_usage", 0)
    online_cpus = (
        cpu.get("online_cpus")
        or len(cpu.get("cpu_usage", {}).get("percpu_usage") or [])
        or 1
    )
    if system_delta > 0 and cpu_delta >= 0:
        cpu_percent = cpu_delta / system_delta * online_cpus * 100.0
    else:
        cpu_percent = 0.0

    mem = stats.get("memory_stats", {})
    # Match `docker stats`: exclude page cache the kernel can reclaim.
    mem_usage = mem.get("usage", 0) - mem.get("stats", {}).get("inactive_file", 0)
    mem_usage = max(mem_usage, 0)
    mem_limit = mem.get("limit", 0)
    return {
        "cpu_percent": round(cpu_percent, 1),
        "mem_usage": mem_usage,
        "mem_limit": mem_limit,
        "mem_percent": round(mem_usage / mem_limit * 100.0, 1) if mem_limit else 0.0,
    }


async def list_containers() -> list[dict]:
    containers = await run(get_client().containers.list, all=True)
    return [shape_container(c.attrs) for c in containers]


async def find_project_container(project_id: str):
    """The running container the deploy engine created for this project, or
    None. Same label query as the engine's live-container lookup; the single
    container is what one-off commands exec into and app controls act on."""
    live = await run(
        get_client().containers.list,
        filters={"label": f"console.project={project_id}"},
    )
    return live[0] if live else None


async def list_project_containers(project_id: str, include_stopped: bool = False):
    """Containers the deploy engine created for this project. include_stopped is
    needed by app controls, since `start` acts on a container that is not
    running and so would be invisible to find_project_container."""
    return await run(
        get_client().containers.list,
        all=include_stopped,
        filters={"label": f"console.project={project_id}"},
    )


async def get_container(container_id: str) -> dict:
    container = await run(get_client().containers.get, container_id)
    return shape_detail(container.attrs)


async def get_stats(container_id: str) -> dict:
    container = await run(get_client().containers.get, container_id)
    stats = await run(container.stats, stream=False)
    return compute_stats(stats)


async def get_logs(container_id: str, tail: int) -> str:
    tail = min(max(tail, 1), config.LOG_TAIL_MAX)
    container = await run(get_client().containers.get, container_id)
    raw = await run(container.logs, tail=tail, timestamps=False)
    return raw.decode("utf-8", errors="replace")
