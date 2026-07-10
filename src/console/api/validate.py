"""Paste-to-validate for console.toml. Same parser the deploy pipeline
uses, so a green check here means the file will not fail a deploy on
validation. Always answers 200; validity is the payload, not the status."""

from fastapi import APIRouter
from pydantic import BaseModel

from console.schema.console_toml import ConfigError, parse_console_toml

router = APIRouter(prefix="/api/validate")


class TomlText(BaseModel):
    text: str


@router.post("/console-toml")
async def validate_console_toml(body: TomlText) -> dict:
    try:
        cfg, warnings = parse_console_toml(body.text)
    except ConfigError as exc:
        return {"valid": False, "error": str(exc), "warnings": [], "summary": None}
    return {
        "valid": True,
        "error": None,
        "warnings": warnings,
        "summary": {
            "name": cfg.app.name,
            "subdomain": cfg.app.subdomain,
            "port": cfg.app.port,
            "health": f"GET {cfg.health.path}, {cfg.health.timeout}s timeout",
            "resources": f"{cfg.resources.memory} memory, {cfg.resources.cpus} cpus",
            "env_keys": sorted(cfg.env),
            "secrets": cfg.secrets,
        },
    }
