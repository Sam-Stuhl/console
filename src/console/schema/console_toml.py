"""console.toml is the only place the console parses input it did not
generate. It is untrusted even though Sam writes it, because it will be
typoed at midnight. Validate everything before touching Docker; fail the
deployment with a readable message."""

import re
import tomllib
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from console import config

SUBDOMAIN_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,30}[a-z0-9])?$")
SECRET_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")
MEMORY_RE = re.compile(r"^(\d+)([kmg])$")
KNOWN_TOP_LEVEL_KEYS = {
    "app", "health", "resources", "env", "secrets", "deploy", "volumes", "mounts",
}
# Volume names and the app names that prefix them become host path segments,
# so they are held to a shape that cannot escape the volume root.
VOLUME_NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,30}[a-z0-9])?$")
APP_NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,62}[a-z0-9])?$")
MOUNT_FROM_RE = re.compile(
    r"^[a-z0-9]([a-z0-9-]{0,62}[a-z0-9])?/[a-z0-9]([a-z0-9-]{0,30}[a-z0-9])?$"
)

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


def validate_target(value: str) -> str:
    """A path inside the container. Absolute, not the root, no dot segments."""
    if not value.startswith("/") or value.rstrip("/") == "":
        raise ValueError(f'target "{value}" must be an absolute path other than "/"')
    if any(part in (".", "..") for part in value.split("/")):
        raise ValueError(f'target "{value}" must not contain "." or ".." segments')
    if any(ch in value for ch in ":,"):
        # Docker's bind syntax is host:target:mode, so these would be misread
        raise ValueError(f'target "{value}" must not contain ":" or ","')
    return value.rstrip("/")


class DeploySection(BaseModel):
    # swap: run the new container beside the old one, swap after a 200.
    # recreate: stop the old one first. Required with volumes, because two
    # processes on one data directory (a database, a sync engine) corrupt it.
    strategy: Literal["swap", "recreate"] = "swap"


class VolumeEntry(BaseModel):
    """Persistent storage this app owns. The app names it; the install decides
    where it lives (CONSOLE_VOLUME_ROOT), so moving storage is a setting."""

    name: str
    target: str

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        if not VOLUME_NAME_RE.match(value):
            raise ValueError(
                f'volume name "{value}" must be 1-32 chars of lowercase letters, '
                "digits, and inner hyphens"
            )
        return value

    @field_validator("target")
    @classmethod
    def _target(cls, value: str) -> str:
        return validate_target(value)


class MountEntry(BaseModel):
    """Another app's volume, always read-only. There is no read-write form:
    a volume has exactly one writer, the app that declares it."""

    # "from" is the TOML key; snapshots round-trip by field name.
    model_config = ConfigDict(populate_by_name=True)

    source: str = Field(alias="from")
    target: str

    @field_validator("source")
    @classmethod
    def _source(cls, value: str) -> str:
        if not MOUNT_FROM_RE.match(value):
            raise ValueError(
                f'mount from "{value}" must look like "<app name>/<volume name>"'
            )
        return value

    @field_validator("target")
    @classmethod
    def _target(cls, value: str) -> str:
        return validate_target(value)

    @property
    def app(self) -> str:
        return self.source.partition("/")[0]

    @property
    def volume(self) -> str:
        return self.source.partition("/")[2]


class ConsoleConfig(BaseModel):
    app: AppSection
    health: HealthSection = HealthSection()
    resources: ResourcesSection = ResourcesSection()
    deploy: DeploySection = DeploySection()
    env: dict[str, str] = {}
    secrets: list[str] = []
    volumes: list[VolumeEntry] = []
    mounts: list[MountEntry] = []

    @model_validator(mode="after")
    def _storage(self) -> "ConsoleConfig":
        if self.volumes and not APP_NAME_RE.match(self.app.name):
            raise ValueError(
                f'app name "{self.app.name}" names its volumes on disk, so with '
                "[[volumes]] it must be lowercase letters, digits, and inner hyphens"
            )
        names = [v.name for v in self.volumes]
        if len(set(names)) != len(names):
            raise ValueError("volumes contains a duplicate name")
        targets = [v.target for v in self.volumes] + [m.target for m in self.mounts]
        if len(set(targets)) != len(targets):
            raise ValueError("two volumes or mounts share a target path")
        sources = [m.source for m in self.mounts]
        if len(set(sources)) != len(sources):
            raise ValueError("mounts names the same volume twice")
        for mount in self.mounts:
            if mount.app == self.app.name:
                raise ValueError(
                    f'mount from "{mount.source}" is this app\'s own volume; '
                    "declare it under [[volumes]] instead"
                )
        if self.volumes and self.deploy.strategy != "recreate":
            raise ValueError(
                'an app with [[volumes]] needs [deploy] strategy = "recreate": '
                "swap would run two containers on the same data at once"
            )
        return self

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
        # A cross-field rule has no location; it reads better without one.
        reasons = "; ".join(
            f"{'.'.join(str(loc) for loc in err['loc'])}: {err['msg']}"
            if err["loc"]
            else err["msg"].removeprefix("Value error, ")
            for err in exc.errors()
        )
        raise ConfigError(f"console.toml invalid: {reasons}") from exc

    return parsed, warnings
