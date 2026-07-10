"""The malicious cases, not just the happy path."""

import pytest

from console.schema.console_toml import ConfigError, parse_console_toml, parse_memory

# secrets sits above the first [section] header: in TOML, a bare key
# written after a header belongs to that section.
VALID = """
secrets = ["DATABASE_URL", "FERNET_KEY"]

[app]
name = "notion-sync"
subdomain = "notion-sync"
port = 8000

[health]
path = "/health"
timeout = 60

[resources]
memory = "512m"
cpus = 1.0

[env]
LOG_LEVEL = "info"
"""


def replacing(needle: str, replacement: str) -> str:
    assert needle in VALID
    return VALID.replace(needle, replacement)


def test_valid_full_config():
    cfg, warnings = parse_console_toml(VALID)
    assert warnings == []
    assert cfg.app.name == "notion-sync"
    assert cfg.app.dockerfile == "./Dockerfile"
    assert cfg.resources.memory_bytes == 512 * 1024**2
    assert cfg.secrets == ["DATABASE_URL", "FERNET_KEY"]


def test_minimal_config_applies_defaults():
    cfg, _ = parse_console_toml(
        '[app]\nname = "x"\nsubdomain = "x"\nport = 80\n'
    )
    assert cfg.health.path == "/health"
    assert cfg.health.timeout == 60
    assert cfg.resources.memory == "512m"
    assert cfg.resources.cpus == 1.0
    assert cfg.env == {}
    assert cfg.secrets == []


def test_not_toml_at_all():
    with pytest.raises(ConfigError, match="not valid TOML"):
        parse_console_toml("{json: maybe?}")


def test_missing_app_section():
    with pytest.raises(ConfigError, match="app"):
        parse_console_toml('[health]\npath = "/health"\n')


def test_subdomain_console_is_reserved():
    with pytest.raises(ConfigError, match="reserved"):
        parse_console_toml(replacing('subdomain = "notion-sync"', 'subdomain = "console"'))


@pytest.mark.parametrize(
    "bad",
    ["-leading", "trailing-", "UPPER", "under_score", "dots.bad", "a" * 33, ""],
)
def test_subdomain_rejects_bad_formats(bad):
    with pytest.raises(ConfigError, match="subdomain"):
        parse_console_toml(
            replacing('subdomain = "notion-sync"', f'subdomain = "{bad}"')
        )


def test_subdomain_boundary_lengths():
    ok32 = "a" * 32
    cfg, _ = parse_console_toml(replacing('subdomain = "notion-sync"', f'subdomain = "{ok32}"'))
    assert cfg.app.subdomain == ok32


@pytest.mark.parametrize("bad_port", ["0", "65536", "-1"])
def test_port_bounds(bad_port):
    with pytest.raises(ConfigError, match="port"):
        parse_console_toml(replacing("port = 8000", f"port = {bad_port}"))


@pytest.mark.parametrize(
    "bad_memory", ['"1t"', '"999999g"', '"512"', '"lots"', '"-1g"', '"1.5g"']
)
def test_memory_rejects_bad_and_over_cap(bad_memory):
    with pytest.raises(ConfigError, match="memory"):
        parse_console_toml(replacing('memory = "512m"', f"memory = {bad_memory}"))


def test_parse_memory_units():
    assert parse_memory("1g") == 1024**3
    assert parse_memory("512M") == 512 * 1024**2
    assert parse_memory("100k") == 100 * 1024


@pytest.mark.parametrize("bad_cpus", ["0", "-1.0", "99.0"])
def test_cpus_bounds(bad_cpus):
    with pytest.raises(ConfigError, match="cpus"):
        parse_console_toml(replacing("cpus = 1.0", f"cpus = {bad_cpus}"))


@pytest.mark.parametrize("bad_timeout", ["0", "301", "-5"])
def test_health_timeout_capped(bad_timeout):
    with pytest.raises(ConfigError, match="timeout"):
        parse_console_toml(replacing("timeout = 60", f"timeout = {bad_timeout}"))


def test_health_path_must_be_absolute():
    with pytest.raises(ConfigError, match="path"):
        parse_console_toml(replacing('path = "/health"', 'path = "health"'))


@pytest.mark.parametrize("bad_name", ["lowercase", "1STARTS_WITH_DIGIT", "HAS-DASH", ""])
def test_secret_names_rejected(bad_name):
    with pytest.raises(ConfigError, match="secret"):
        parse_console_toml(
            replacing(
                'secrets = ["DATABASE_URL", "FERNET_KEY"]',
                f'secrets = ["{bad_name}"]',
            )
        )


def test_secret_duplicates_rejected():
    with pytest.raises(ConfigError, match="duplicates"):
        parse_console_toml(
            replacing(
                'secrets = ["DATABASE_URL", "FERNET_KEY"]',
                'secrets = ["DATABASE_URL", "DATABASE_URL"]',
            )
        )


def test_secrets_inside_env_gets_pointed_error():
    # The layout from the original brief: secrets below [env] is env.secrets in TOML.
    with pytest.raises(ConfigError, match="above the first"):
        parse_console_toml(
            '[app]\nname = "x"\nsubdomain = "x"\nport = 80\n'
            '[env]\nLOG_LEVEL = "info"\nsecrets = ["DATABASE_URL"]\n'
        )


def test_unknown_top_level_key_warns_not_fails():
    cfg, warnings = parse_console_toml(VALID + '\n[buildpack]\nkind = "nope"\n')
    assert cfg.app.name == "notion-sync"
    assert warnings == ['unknown top-level key "buildpack" ignored']
