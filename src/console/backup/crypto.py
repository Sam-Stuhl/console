"""Encrypts a backup bundle with a passphrase kept outside the database it
protects (CONSOLE_BACKUP_PASSPHRASE_FILE), the same reasoning as the Fernet key.

The passphrase is stretched into a Fernet key with PBKDF2-HMAC-SHA256 over a
random per-backup salt. The salt is not secret, so it is written as a fixed
16-byte header ahead of the Fernet token; restore reads it back to redrive the
key. Using a fresh salt per backup means two backups of identical bytes still
differ on disk. Everything here is from `cryptography`, already a dependency."""

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
            "backup passphrase not configured: no file at "
            f"{config.BACKUP_PASSPHRASE_FILE} (set CONSOLE_BACKUP_PASSPHRASE_FILE "
            "to a mounted secret). Keep a copy of the passphrase offline; without "
            "it a backup cannot be restored."
        )


class BackupRestoreError(Exception):
    """The bundle could not be decrypted (wrong passphrase or corrupt data)."""


def passphrase_present() -> bool:
    """Whether a usable passphrase file exists, without raising. For the UI and
    the scheduled-run guard."""
    try:
        return bool(Path(config.BACKUP_PASSPHRASE_FILE).read_bytes().strip())
    except OSError:
        return False


def _passphrase() -> bytes:
    try:
        value = Path(config.BACKUP_PASSPHRASE_FILE).read_bytes().strip()
    except OSError as exc:
        raise BackupPassphraseNotConfigured() from exc
    if not value:
        raise BackupPassphraseNotConfigured()
    return value


def _fernet(salt: bytes, passphrase: bytes) -> Fernet:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=KDF_ITERATIONS,
    )
    return Fernet(base64.urlsafe_b64encode(kdf.derive(passphrase)))


def encrypt(plaintext: bytes) -> bytes:
    """salt || Fernet(token). Raises BackupPassphraseNotConfigured if unset."""
    salt = os.urandom(SALT_SIZE)
    token = _fernet(salt, _passphrase()).encrypt(plaintext)
    return salt + token


def decrypt(bundle: bytes) -> bytes:
    """Inverse of encrypt(). Raises BackupRestoreError on a bad passphrase."""
    salt, token = bundle[:SALT_SIZE], bundle[SALT_SIZE:]
    try:
        return _fernet(salt, _passphrase()).decrypt(token)
    except InvalidToken as exc:
        raise BackupRestoreError(
            "could not decrypt the backup: wrong passphrase or corrupt bundle"
        ) from exc
