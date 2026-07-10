"""console.toml is the only place the console parses input it did not
generate. It is untrusted even though Sam writes it, because it will be
typoed at midnight. Validate everything before touching Docker; fail the
deployment with a readable message."""

import re
import tomllib

from pydantic import BaseModel, Field, ValidationError, field_validator

from console import config

SUBDOMAIN_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,30}[a-z0-9])?$")
SECRET_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")
MEMORY_RE = re.compile(r"^(\d+)([kmg])$")
KNOWN_TOP_LEVEL_KEYS = {"app", "health", "resources", "env", "secrets"}

_MEMORY_UNITS = {"k": 1024, "m": 1024**2, "g": 1024**3}


class ConfigError(ValueError):
    """A console.toml that must fail the deployment, with a readable reason."""


def parse_memory(value: str) -> int:
    match = MEMORY_RE.match(value.strip().lower())
    if not match:
        raise ValueError(f'memory "{value}" must look like "512m" or "1g"')
    amount, unit = match.groups()
    return int(amount) * _MEMORY_UNITS[unit]


def validate_subdomain_format(value: str) -> str:
    if value == "console":
        raise ValueError(
            'subdomain "console" is reserved: a typo must not be able to take '
            "down the console itself"
        )
    if not SUBDOMAIN_RE.match(value):
        raise ValueError(
            f'subdomain "{value}" must be 1-32 chars of lowercase letters, '
            "digits, and inner hyphens"
        )
    return value


class AppSection(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    subdomain: str
    port: int = Field(ge=1, le=65535)
    dockerfile: str = "./Dockerfile"

    @field_validator("subdomain")
    @classmethod
    def _subdomain(cls, value: str) -> str:
        return validate_subdomain_format(value)


class HealthSection(BaseModel):
    path: str = "/health"
    timeout: int = Field(default=60, ge=1, le=config.HEALTH_TIMEOUT_CAP)

    @field_validator("path")
    @classmethod
    def _path(cls, value: str) -> str:
        if not value.startswith("/"):
            raise ValueError(f'health path "{value}" must start with "/"')
        return value


class ResourcesSection(BaseModel):
    memory: str = "512m"
    cpus: float = Field(default=1.0, gt=0, le=config.CPUS_CAP)

    @field_validator("memory")
    @classmethod
    def _memory(cls, value: str) -> str:
        if parse_memory(value) > config.MEMORY_CAP_BYTES:
            cap_g = config.MEMORY_CAP_BYTES // 1024**3
            raise ValueError(f'memory "{value}" exceeds the {cap_g}g cap')
        return value

    @property
    def memory_bytes(self) -> int:
        return parse_memory(self.memory)


class ConsoleConfig(BaseModel):
    app: AppSection
    health: HealthSection = HealthSection()
    resources: ResourcesSection = ResourcesSection()
    env: dict[str, str] = {}
    secrets: list[str] = []

    @field_validator("secrets")
    @classmethod
    def _secrets(cls, names: list[str]) -> list[str]:
        for name in names:
            if not SECRET_NAME_RE.match(name):
                raise ValueError(
                    f'secret name "{name}" must match [A-Z_][A-Z0-9_]* '
                    "(uppercase env-var style)"
                )
        if len(set(names)) != len(names):
            raise ValueError("secrets list contains duplicates")
        return names


def parse_console_toml(text: str) -> tuple[ConsoleConfig, list[str]]:
    """Parse and validate. Returns (config, warnings). Raises ConfigError
    with a human-readable message on anything that must fail the deploy."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"console.toml is not valid TOML: {exc}") from exc

    # TOML gotcha: a bare `secrets = [...]` written below the [env] header
    # belongs to [env], not the top level. Catch it with a pointed message.
    env_section = data.get("env")
    if isinstance(env_section, dict) and isinstance(env_section.get("secrets"), list):
        raise ConfigError(
            "secrets is inside [env]: in TOML a key written after a [section] "
            "header belongs to that section. Move the secrets = [...] line "
            "above the first [section] header."
        )

    warnings = [
        f'unknown top-level key "{key}" ignored'
        for key in data
        if key not in KNOWN_TOP_LEVEL_KEYS
    ]
    known = {key: value for key, value in data.items() if key in KNOWN_TOP_LEVEL_KEYS}

    try:
        parsed = ConsoleConfig.model_validate(known)
    except ValidationError as exc:
        reasons = "; ".join(
            f"{'.'.join(str(loc) for loc in err['loc'])}: {err['msg']}"
            for err in exc.errors()
        )
        raise ConfigError(f"console.toml invalid: {reasons}") from exc

    return parsed, warnings
