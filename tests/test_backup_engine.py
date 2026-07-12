"""Engine tests: the bundle round-trips to a valid DB snapshot + key, the
passphrase resolves from a stored setting before the mounted file, a run records
its result, and pruning keeps the newest N of our own bundles."""

import io
import sqlite3
import tarfile

import pytest

from console import config, settings_store
from console.backup import crypto, engine
from console.db.models import BackupRun

PW = b"engine-test-passphrase"


class FakeStore:
    def __init__(self):
        self.objects: dict[str, bytes] = {}

    async def put(self, name, data):
        self.objects[name] = data
        return f"fake:{name}"

    async def names(self):
        return sorted(self.objects)

    async def delete(self, name):
        self.objects.pop(name, None)


@pytest.fixture(autouse=True)
def fast_kdf(monkeypatch):
    monkeypatch.setattr(crypto, "KDF_ITERATIONS", 1000)


@pytest.fixture
def env(tmp_path, monkeypatch):
    db = tmp_path / "console.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE t (x INTEGER)")
    con.execute("INSERT INTO t VALUES (42)")
    con.commit()
    con.close()
    (tmp_path / "key").write_bytes(b"fernet-key-bytes")
    (tmp_path / "pw").write_text("file-pass")
    monkeypatch.setattr(config, "DB_PATH", str(db))
    monkeypatch.setattr(config, "KEY_FILE", str(tmp_path / "key"))
    monkeypatch.setattr(config, "BACKUP_PASSPHRASE_FILE", str(tmp_path / "pw"))
    return tmp_path


def test_build_bundle_round_trips(env):
    tar_bytes = crypto.decrypt(engine._build_bundle(PW), PW)
    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:gz") as tar:
        assert set(tar.getnames()) == {"console.db", "console_key"}
        assert tar.extractfile("console_key").read() == b"fernet-key-bytes"
        db_bytes = tar.extractfile("console.db").read()

    restored = env / "restored.db"
    restored.write_bytes(db_bytes)
    con = sqlite3.connect(restored)
    assert con.execute("SELECT x FROM t").fetchone()[0] == 42


def test_missing_key_fails_clearly(env, monkeypatch):
    monkeypatch.setattr(config, "KEY_FILE", str(env / "no-such-key"))
    with pytest.raises(engine.BackupError, match="console key file not found"):
        engine._build_bundle(PW)


async def test_resolve_passphrase_prefers_setting(db, tmp_path, monkeypatch):
    (tmp_path / "pw").write_text("file-pass")
    monkeypatch.setattr(config, "BACKUP_PASSPHRASE_FILE", str(tmp_path / "pw"))
    async with db() as session:
        await settings_store.set_value(
            session, settings_store.BACKUP_PASSPHRASE, "setting-pass"
        )
        await session.commit()
    async with db() as session:
        assert await engine._resolve_passphrase(session) == b"setting-pass"


async def test_passphrase_available_setting_or_file(db, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BACKUP_PASSPHRASE_FILE", str(tmp_path / "none"))
    async with db() as session:
        assert await engine._passphrase_available(session) is False
        await settings_store.set_value(session, settings_store.BACKUP_PASSPHRASE, "x")
        await session.commit()
    async with db() as session:
        assert await engine._passphrase_available(session) is True


async def test_run_backup_succeeds_with_file_passphrase(env, db, monkeypatch):
    store = FakeStore()
    monkeypatch.setattr(engine, "SessionLocal", db)

    async def resolve(_session):
        return store

    monkeypatch.setattr(engine, "resolve_store", resolve)

    async with db() as session:
        backup = BackupRun(trigger="manual", status="running")
        session.add(backup)
        await session.commit()
        run_id = backup.id

    await engine.run_backup(run_id)

    async with db() as session:
        row = await session.get(BackupRun, run_id)
    assert row.status == "succeeded", row.failure_reason
    assert row.size_bytes and row.size_bytes > 0
    assert len(store.objects) == 1
    (data,) = store.objects.values()
    # decrypts with the file passphrase the run resolved
    assert crypto.decrypt(data, b"file-pass")


async def test_prune_keeps_newest_and_spares_others(monkeypatch):
    monkeypatch.setattr(config, "BACKUP_RETENTION", 2)
    store = FakeStore()
    for name in (
        "console-backup-20260101T000000Z.bin",
        "console-backup-20260102T000000Z.bin",
        "console-backup-20260103T000000Z.bin",
        "unrelated.txt",
    ):
        store.objects[name] = b"x"

    await engine._prune(store)

    remaining = set(store.objects)
    assert "unrelated.txt" in remaining
    assert "console-backup-20260101T000000Z.bin" not in remaining
    assert "console-backup-20260103T000000Z.bin" in remaining
    assert sum(n.startswith(engine.NAME_PREFIX) for n in remaining) == 2
