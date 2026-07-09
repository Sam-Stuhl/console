"""Shaping and math against canned Docker payloads. Fixtures are trimmed
copies of real `docker inspect` / stats API output."""

from console.docker.containers import (
    MASK,
    compute_stats,
    shape_container,
    shape_detail,
)

RUNNING_ATTRS = {
    "Id": "aa2ee0fb7879f0f66391f126576a76a451a60bfd7a4bf8bb1dae884b7751abf8",
    "Name": "/app-demo-e5f6a7b",
    "Created": "2026-07-09T23:32:01.123456789Z",
    "State": {
        "Status": "running",
        "StartedAt": "2026-07-09T23:32:02.5Z",
        "FinishedAt": "0001-01-01T00:00:00Z",
        "ExitCode": 0,
    },
    "Config": {
        "Image": "traefik/whoami:v1.11.0",
        "Env": [
            "LOG_LEVEL=info",
            "DATABASE_URL=postgres://user:hunter2@example.neon.tech/demo",
            "EMPTY=",
            "NOEQUALS",
        ],
        "Labels": {"traefik.enable": "true"},
    },
    "HostConfig": {"RestartPolicy": {"Name": "unless-stopped"}},
    "NetworkSettings": {
        "Networks": {"web": {}},
        "Ports": {
            "80/tcp": [
                {"HostIp": "0.0.0.0", "HostPort": "8080"},
                {"HostIp": "::", "HostPort": "8080"},
            ],
            "9000/tcp": None,
        },
    },
}

EXITED_ATTRS = {
    "Id": "2dc1689ca65d022729eb1a7018340c39949944b9c146008a3e64668ee89db0e1",
    "Name": "/app-demo-a1b2c3d",
    "Created": "2026-07-09T23:30:00Z",
    "State": {
        "Status": "exited",
        "StartedAt": "2026-07-09T23:30:01Z",
        "FinishedAt": "2026-07-09T23:40:00Z",
        "ExitCode": 137,
    },
    "Config": {"Image": "traefik/whoami:v1.10.1", "Env": None, "Labels": None},
    "HostConfig": {},
    "NetworkSettings": {},
}

STATS = {
    "cpu_stats": {
        "cpu_usage": {"total_usage": 400_000_000},
        "system_cpu_usage": 10_000_000_000,
        "online_cpus": 4,
    },
    "precpu_stats": {
        "cpu_usage": {"total_usage": 300_000_000},
        "system_cpu_usage": 8_000_000_000,
    },
    "memory_stats": {
        "usage": 100 * 1024 * 1024,
        "limit": 512 * 1024 * 1024,
        "stats": {"inactive_file": 20 * 1024 * 1024},
    },
}


def test_shape_running_container():
    shaped = shape_container(RUNNING_ATTRS)
    assert shaped["id"] == "aa2ee0fb7879"
    assert shaped["name"] == "app-demo-e5f6a7b"
    assert shaped["image"] == "traefik/whoami:v1.11.0"
    assert shaped["state"] == "running"
    assert shaped["started_at"] == "2026-07-09T23:32:02.5Z"
    assert shaped["exit_code"] is None
    assert shaped["finished_at"] is None


def test_shape_exited_container():
    shaped = shape_container(EXITED_ATTRS)
    assert shaped["state"] == "exited"
    assert shaped["exit_code"] == 137
    assert shaped["finished_at"] == "2026-07-09T23:40:00Z"
    assert shaped["started_at"] is None


def test_env_masking_never_leaks_values():
    detail = shape_detail(RUNNING_ATTRS)
    keys = [e["key"] for e in detail["env"]]
    assert keys == ["LOG_LEVEL", "DATABASE_URL", "EMPTY", "NOEQUALS"]
    assert all(e["value"] == MASK for e in detail["env"])
    assert "hunter2" not in str(detail)


def test_detail_ports_and_networks():
    detail = shape_detail(RUNNING_ATTRS)
    assert detail["networks"] == ["web"]
    assert detail["ports"] == [
        {"container_port": "80/tcp", "host_ports": ["8080"]},
        {"container_port": "9000/tcp", "host_ports": []},
    ]
    assert detail["restart_policy"] == "unless-stopped"


def test_detail_handles_null_env_and_labels():
    detail = shape_detail(EXITED_ATTRS)
    assert detail["env"] == []
    assert detail["labels"] == {}
    assert detail["ports"] == []


def test_cpu_percent_math():
    stats = compute_stats(STATS)
    # (0.1e9 / 2e9) * 4 cpus * 100 = 20%
    assert stats["cpu_percent"] == 20.0
    assert stats["mem_usage"] == 80 * 1024 * 1024
    assert stats["mem_percent"] == 15.6


def test_cpu_percent_zero_system_delta():
    broken = {
        "cpu_stats": {"cpu_usage": {"total_usage": 5}, "system_cpu_usage": 100},
        "precpu_stats": {"cpu_usage": {"total_usage": 1}, "system_cpu_usage": 100},
        "memory_stats": {},
    }
    stats = compute_stats(broken)
    assert stats["cpu_percent"] == 0.0
    assert stats["mem_percent"] == 0.0


def test_cpu_count_falls_back_to_percpu_then_one():
    stats_dict = {
        "cpu_stats": {
            "cpu_usage": {"total_usage": 200, "percpu_usage": [1, 2]},
            "system_cpu_usage": 1100,
        },
        "precpu_stats": {"cpu_usage": {"total_usage": 100}, "system_cpu_usage": 100},
        "memory_stats": {},
    }
    assert compute_stats(stats_dict)["cpu_percent"] == 20.0
    stats_dict["cpu_stats"]["cpu_usage"].pop("percpu_usage")
    assert compute_stats(stats_dict)["cpu_percent"] == 10.0
