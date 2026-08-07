import pytest

from console import config
from console.deploy import plan
from console.schema.console_toml import parse_console_toml

TOML = """
secrets = ["DATABASE_URL"]

[app]
name = "demo"
subdomain = "app-demo"
port = 80

[env]
LOG_LEVEL = "info"
"""

CFG, _ = parse_console_toml(TOML)


def test_container_name_uses_short_sha():
    assert plan.container_name("demo", "e5f6a7b0d1c2") == "demo-e5f6a7b"


def test_labels_match_the_runbook():
    labels = plan.build_labels(
        name="app-demo-e5f6a7b",
        subdomain="app-demo",
        domain="example.com",
        port=80,
        priority=3_999_999_999,
        project_id="p1",
        deployment_id="d1",
        sha="e5f6a7b0d1c2",
    )
    assert labels == {
        "traefik.enable": "true",
        "traefik.http.routers.app-demo-e5f6a7b.rule": "Host(`app-demo.example.com`)",
        "traefik.http.routers.app-demo-e5f6a7b.entrypoints": "web",
        "traefik.http.routers.app-demo-e5f6a7b.priority": "3999999999",
        "traefik.http.services.app-demo-e5f6a7b.loadbalancer.server.port": "80",
        "console.managed": "true",
        "console.project": "p1",
        "console.deployment": "d1",
        "console.sha": "e5f6a7b0d1c2",
    }


def test_host_rule_uses_domain():
    assert plan.host_rule("app-demo", "example.com") == "Host(`app-demo.example.com`)"
    assert plan.host_rule("app-demo", "other.com") == "Host(`app-demo.other.com`)"


def test_priority_extracted_from_labels():
    labels = plan.build_labels("n", "s", "d.com", 80, 42, "p", "d", "sha0000")
    assert plan.extract_router_priority(labels) == 42


def test_priority_none_when_absent_or_garbage():
    assert plan.extract_router_priority({"traefik.enable": "true"}) is None
    assert (
        plan.extract_router_priority({"traefik.http.routers.x.priority": "high"})
        is None
    )


def test_first_deploy_gets_priority_start():
    assert plan.compute_priority(None) == config.PRIORITY_START


def test_next_deploy_counts_down():
    assert plan.compute_priority(config.PRIORITY_START) == config.PRIORITY_START - 1


def test_priority_never_reaches_zero():
    with pytest.raises(ValueError, match="refusing"):
        plan.compute_priority(1)


def test_env_merges_env_and_secrets():
    env = plan.build_env(CFG, {"DATABASE_URL": "postgres://x"})
    assert env == {"LOG_LEVEL": "info", "DATABASE_URL": "postgres://x"}


def test_env_secret_collision_fails_loudly():
    with pytest.raises(ValueError, match='"LOG_LEVEL" is set in both'):
        plan.build_env(CFG, {"LOG_LEVEL": "debug"})


def test_health_url_is_container_to_container():
    assert (
        plan.health_url("app-demo-e5f6a7b", 80, "/health")
        == "http://app-demo-e5f6a7b:80/health"
    )


# --- which images may be deployed ------------------------------------------


@pytest.fixture
def owner(monkeypatch):
    monkeypatch.setattr(config, "OIDC_OWNER", "Example-Owner")


def test_image_prefix_is_the_owner_s_lowercase_ghcr_namespace(owner):
    assert plan.image_prefix() == "ghcr.io/example-owner/"


def test_validate_image_returns_the_tag(owner):
    assert plan.validate_image("ghcr.io/example-owner/demo:abc1234") == "abc1234"


def test_validate_image_accepts_a_non_sha_tag(owner):
    assert plan.validate_image("ghcr.io/example-owner/demo:v1.2.3") == "v1.2.3"


def test_validate_image_rejects_another_namespace(owner):
    with pytest.raises(ValueError, match="not under ghcr.io/example-owner/"):
        plan.validate_image("ghcr.io/someone-else/demo:abc1234")


def test_validate_image_rejects_another_registry(owner):
    with pytest.raises(ValueError, match="not under"):
        plan.validate_image("docker.io/example-owner/demo:abc1234")


def test_validate_image_requires_a_tag(owner):
    with pytest.raises(ValueError, match="needs a tag"):
        plan.validate_image("ghcr.io/example-owner/demo")


def test_validate_image_without_an_owner_configured_trusts_nothing(monkeypatch):
    # Mirrors oidc.verify: an unset owner rejects everything, and says why
    # rather than complaining that the ref is not under "ghcr.io//".
    monkeypatch.setattr(config, "OIDC_OWNER", "")
    with pytest.raises(ValueError, match="no CONSOLE_OIDC_OWNER is set"):
        plan.validate_image("ghcr.io/example-owner/demo:abc1234")
