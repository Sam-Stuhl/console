import pytest
from cryptography.fernet import Fernet

from console import config
from console.secrets import crypto


def test_roundtrip(tmp_path, monkeypatch):
    key_file = tmp_path / "key"
    key_file.write_bytes(Fernet.generate_key())
    monkeypatch.setattr(config, "KEY_FILE", str(key_file))

    token = crypto.encrypt("postgres://user:hunter2@example.neon.tech/db")
    assert isinstance(token, bytes)
    assert b"hunter2" not in token
    assert crypto.decrypt(token) == "postgres://user:hunter2@example.neon.tech/db"


def test_missing_key_file(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "KEY_FILE", str(tmp_path / "nope"))
    with pytest.raises(crypto.KeyNotConfigured, match="console key not configured"):
        crypto.encrypt("x")


def test_garbage_key_file(tmp_path, monkeypatch):
    key_file = tmp_path / "key"
    key_file.write_bytes(b"not a fernet key")
    monkeypatch.setattr(config, "KEY_FILE", str(key_file))
    with pytest.raises(crypto.KeyNotConfigured):
        crypto.encrypt("x")
