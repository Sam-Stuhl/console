"""Passphrase-based bundle encryption: round-trips, rejects a wrong passphrase,
and reads the mounted file when present."""

import pytest

from console import config
from console.backup import crypto

PW = b"correct horse battery staple"


@pytest.fixture(autouse=True)
def fast_kdf(monkeypatch):
    monkeypatch.setattr(crypto, "KDF_ITERATIONS", 1000)  # keep the tests fast


def test_round_trip():
    data = b"the whole console state"
    bundle = crypto.encrypt(data, PW)
    assert bundle != data
    assert crypto.decrypt(bundle, PW) == data


def test_fresh_salt_per_backup():
    assert crypto.encrypt(b"same", PW) != crypto.encrypt(b"same", PW)


def test_wrong_passphrase_fails():
    bundle = crypto.encrypt(b"secret", PW)
    with pytest.raises(crypto.BackupRestoreError):
        crypto.decrypt(bundle, b"a different passphrase")


def test_read_file_passphrase(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BACKUP_PASSPHRASE_FILE", str(tmp_path / "missing"))
    assert crypto.read_file_passphrase() is None

    f = tmp_path / "pass"
    f.write_text("  from-a-file  \n")  # stripped
    monkeypatch.setattr(config, "BACKUP_PASSPHRASE_FILE", str(f))
    assert crypto.read_file_passphrase() == b"from-a-file"
