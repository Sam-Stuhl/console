"""The console's SQLite connections. The case that matters: a session that has
read something keeps a transaction open while it awaits a network call, and a
writer must still be able to commit. In the default rollback-journal mode it
failed with "database is locked" after the timeout (seen live 2026-10-12)."""

import sqlite3
import threading

from console.db import session as db_session


def connect(path, timeout=0.5):
    conn = sqlite3.connect(path, timeout=timeout, isolation_level=None, check_same_thread=False)
    db_session.configure_connection(conn)
    return conn


def test_connections_use_wal_and_a_long_busy_timeout(tmp_path):
    conn = connect(tmp_path / "console.db")
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == db_session.BUSY_TIMEOUT_MS
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_a_writer_commits_while_a_reader_holds_a_transaction(tmp_path):
    path = tmp_path / "console.db"
    setup = connect(path)
    setup.execute("CREATE TABLE t (x)")
    setup.execute("INSERT INTO t VALUES (0)")

    reader = connect(path)
    reader.execute("BEGIN")
    reader.execute("SELECT count(*) FROM t").fetchone()

    writer = connect(path)
    writer.execute("BEGIN IMMEDIATE")
    writer.execute("INSERT INTO t VALUES (1)")
    writer.execute("COMMIT")  # raised "database is locked" before WAL

    reader.execute("COMMIT")
    assert setup.execute("SELECT count(*) FROM t").fetchone()[0] == 2


def test_without_wal_the_same_write_is_locked_out(tmp_path):
    """Pins the cause: the same sequence fails in the old journal mode."""
    path = tmp_path / "old.db"
    setup = sqlite3.connect(path, isolation_level=None)
    setup.execute("PRAGMA journal_mode=DELETE")
    setup.execute("CREATE TABLE t (x)")
    reader = sqlite3.connect(path, isolation_level=None)
    reader.execute("BEGIN")
    reader.execute("SELECT count(*) FROM t").fetchone()
    writer = sqlite3.connect(path, timeout=0.2, isolation_level=None)
    writer.execute("BEGIN IMMEDIATE")
    writer.execute("INSERT INTO t VALUES (1)")
    try:
        writer.execute("COMMIT")
    except sqlite3.OperationalError as exc:
        assert "locked" in str(exc)
    else:
        raise AssertionError("expected the rollback-journal mode to lock the writer out")
    finally:
        reader.execute("COMMIT")
