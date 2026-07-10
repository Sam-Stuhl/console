"""Fernet encryption for secret values. The key lives in a file outside git
(CONSOLE_KEY_FILE) because the console's own key cannot live in the thing it
protects. Losing that file means losing every stored secret.

The key file is read per operation, not cached: secret writes are rare, and
this keeps behavior obvious when the file appears or changes at runtime."""

from pathlib import Path

from cryptography.fernet import Fernet

from console import config


class KeyNotConfigured(Exception):
    def __init__(self) -> None:
        super().__init__(
            f"console key not configured: no usable Fernet key at {config.KEY_FILE} "
            "(set CONSOLE_KEY_FILE, generate one with: python -m console.keygen <path>)"
        )


def _fernet() -> Fernet:
    try:
        return Fernet(Path(config.KEY_FILE).read_bytes().strip())
    except (OSError, ValueError) as exc:
        raise KeyNotConfigured() from exc


def encrypt(value: str) -> bytes:
    return _fernet().encrypt(value.encode())


def decrypt(token: bytes) -> str:
    return _fernet().decrypt(token).decode()
