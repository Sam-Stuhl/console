"""Passphrase-based bundle encryption: round-trips, rejects a wrong passphrase,
and reports or raises when unconfigured."""

import pytest

from console import config
from console.backup import crypto


@pytest.fixture
def passphrase(tmp_path, monkeypatch):
    monkeypatch.setattr(crypto, "KDF_ITERATIONS", 1000)  # keep the test fast
    f = tmp_path / "pass"
    f.write_text("correct horse battery staple")
    monkeypatch.setattr(config, "BACKUP_PASSPHRASE_FILE", str(f))
    return f


def test_round_trip(passphrase):
    data = b"the whole console state"
    bundle = crypto.encrypt(data)
    assert bundle != data
    assert crypto.decrypt(bundle) == data


def test_fresh_salt_per_backup(passphrase):
    assert crypto.encrypt(b"same") != crypto.encrypt(b"same")


def test_wrong_passphrase_fails(passphrase, tmp_path, monkeypatch):
    bundle = crypto.encrypt(b"secret")
    other = tmp_path / "other"
    other.write_text("wrong passphrase")
    monkeypatch.setattr(config, "BACKUP_PASSPHRASE_FILE", str(other))
    with pytest.raises(crypto.BackupRestoreError):
        crypto.decrypt(bundle)


def test_not_configured(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BACKUP_PASSPHRASE_FILE", str(tmp_path / "missing"))
    assert crypto.passphrase_present() is False
    with pytest.raises(crypto.BackupPassphraseNotConfigured):
        crypto.encrypt(b"x")
