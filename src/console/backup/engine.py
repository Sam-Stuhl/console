"""Snapshots the console's own state, encrypts it, and pushes it off-box.

A backup bundle is a gzip tar of a consistent SQLite snapshot (taken with
SQLite's online backup API, never a copy of a live file) plus the Fernet key,
encrypted with the mounted passphrase. The key rides along on purpose: without
it the snapshot's encrypted columns are unrecoverable, which is the exact
failure this feature exists to prevent. Restore is a documented manual step
(python -m console.backup.restore), never a UI button over live state.

Scheduled runs come from backup_loop; manual runs come from the API. Both create
a backup_runs row first (so the UI has an id to poll) and then execute it."""

import asyncio
import io
import logging
import os
import sqlite3
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from console import config
from console.backup import crypto
from console.backup.store import BackupStore, resolve_store
from console.db.models import BackupRun, utcnow
from console.db.session import SessionLocal
from console.docker.client import run

logger = logging.getLogger(__name__)

NAME_PREFIX = "console-backup-"
_tasks: set[asyncio.Task] = set()


class BackupError(Exception):
    """A backup could not be produced or stored."""


async def configured(session: AsyncSession) -> dict:
    """What is and isn't set up, for the UI and the scheduled-run guard."""
    passphrase = crypto.passphrase_present()
    destination = await resolve_store(session) is not None
    return {
        "passphrase": passphrase,
        "destination": destination,
        "ready": passphrase and destination,
    }


def enqueue(run_id: str) -> None:
    task = asyncio.create_task(run_backup(run_id))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def run_backup(run_id: str) -> None:
    async with SessionLocal() as session:
        backup = await session.get(BackupRun, run_id)
        if backup is None:
            return
        try:
            store = await resolve_store(session)
            if store is None:
                raise BackupError("no backup destination configured")
            name = NAME_PREFIX + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".bin"
            bundle = await run(_build_bundle)  # snapshot + tar + KDF encrypt, all blocking
            location = await store.put(name, bundle)
            backup.location = location
            backup.size_bytes = len(bundle)
            await _prune(store)
            backup.status = "succeeded"
        except Exception as exc:
            backup.status = "failed"
            backup.failure_reason = str(exc)
            logger.warning("backup %s failed: %s", run_id, exc)
        finally:
            backup.finished_at = utcnow()
            await session.commit()


def _build_bundle() -> bytes:
    db_bytes = _snapshot_bytes(config.DB_PATH)
    try:
        key_bytes = Path(config.KEY_FILE).read_bytes()
    except OSError as exc:
        raise BackupError(
            f"console key file not found at {config.KEY_FILE}; a backup without "
            "it could not restore secrets"
        ) from exc

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        _add(tar, "console.db", db_bytes)
        _add(tar, "console_key", key_bytes)
    return crypto.encrypt(buf.getvalue())


def _snapshot_bytes(db_path: str) -> bytes:
    """A consistent copy of the live SQLite database via the backup API."""
    handle, tmp_path = tempfile.mkstemp(suffix=".db")
    os.close(handle)
    try:
        src = sqlite3.connect(db_path)
        dst = sqlite3.connect(tmp_path)
        try:
            with dst:
                src.backup(dst)
        finally:
            dst.close()
            src.close()
        return Path(tmp_path).read_bytes()
    finally:
        os.unlink(tmp_path)


def _add(tar: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name=name)
    info.size = len(data)
    tar.addfile(info, io.BytesIO(data))


async def _prune(store: BackupStore) -> None:
    """Keep the newest BACKUP_RETENTION of our own bundles; delete older ones.
    Names sort chronologically, and we only ever touch console-backup-* so an
    unrelated file in the destination is never removed."""
    if config.BACKUP_RETENTION <= 0:
        return
    ours = sorted(n for n in await store.names() if n.startswith(NAME_PREFIX))
    for name in ours[: -config.BACKUP_RETENTION]:
        await store.delete(name)


async def backup_loop() -> None:
    while True:
        await asyncio.sleep(config.BACKUP_INTERVAL)
        try:
            async with SessionLocal() as session:
                if not (await configured(session))["ready"]:
                    continue
                backup = BackupRun(trigger="scheduled", status="running")
                session.add(backup)
                await session.commit()
                run_id = backup.id
            await run_backup(run_id)
        except Exception:
            logger.exception("backup tick failed; will retry next interval")
