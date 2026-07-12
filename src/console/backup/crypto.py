"""Encrypts a backup bundle with a passphrase the caller supplies.

The passphrase is stretched into a Fernet key with PBKDF2-HMAC-SHA256 over a
random per-backup salt. The salt is not secret, so it is written as a fixed
16-byte header ahead of the Fernet token; restore reads it back to redrive the
key. A fresh salt per backup means two backups of identical bytes still differ
on disk. Everything here is from `cryptography`, already a dependency.

This module is deliberately ignorant of where the passphrase comes from: the
engine resolves it (a stored setting, or the mounted CONSOLE_BACKUP_PASSPHRASE_FILE)
and passes the bytes in. read_file_passphrase() is the file source."""

import base64
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from console import config

SALT_SIZE = 16
KDF_ITERATIONS = 600_000


class BackupPassphraseNotConfigured(Exception):
    def __init__(self) -> None:
        super().__init__(
            "backup passphrase not configured: set one in Settings, or mount a "
            f"file at {config.BACKUP_PASSPHRASE_FILE}. Keep a copy offline; "
            "without it a backup cannot be restored."
        )


class BackupRestoreError(Exception):
    """The bundle could not be decrypted (wrong passphrase or corrupt data)."""


def read_file_passphrase() -> bytes | None:
    """The passphrase from the mounted file, or None if absent/empty."""
    try:
        value = Path(config.BACKUP_PASSPHRASE_FILE).read_bytes().strip()
    except OSError:
        return None
    return value or None


def _fernet(salt: bytes, passphrase: bytes) -> Fernet:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=KDF_ITERATIONS,
    )
    return Fernet(base64.urlsafe_b64encode(kdf.derive(passphrase)))


def encrypt(plaintext: bytes, passphrase: bytes) -> bytes:
    """salt || Fernet(token)."""
    salt = os.urandom(SALT_SIZE)
    return salt + _fernet(salt, passphrase).encrypt(plaintext)


def decrypt(bundle: bytes, passphrase: bytes) -> bytes:
    """Inverse of encrypt(). Raises BackupRestoreError on a bad passphrase."""
    salt, token = bundle[:SALT_SIZE], bundle[SALT_SIZE:]
    try:
        return _fernet(salt, passphrase).decrypt(token)
    except InvalidToken as exc:
        raise BackupRestoreError(
            "could not decrypt the backup: wrong passphrase or corrupt bundle"
        ) from exc
