from collections.abc import AsyncIterator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from console import config

engine = create_async_engine(f"sqlite+aiosqlite:///{config.DB_PATH}")


# Seconds a writer waits for the lock before "database is locked".
BUSY_TIMEOUT_MS = 30_000


def configure_connection(dbapi_connection) -> None:
    """Pragmas every connection to the console database gets.

    foreign_keys: SQLite ships with them off; cascade deletes need them on.

    journal_mode=WAL: in the default rollback-journal mode a session that has
    read anything holds a shared lock until it commits, and the console's
    sessions often await a GitHub call or a health probe in between. Every
    writer then queued behind that reader and gave up after five seconds with
    "database is locked" (a deploy, a build and an API call failed that way on
    2026-10-12). In WAL mode readers never block writers. Verified on the
    mini's Colima virtiofs mount, where the database lives. The mode is stored
    in the file, so setting it on every connect is a no-op after the first.

    synchronous=NORMAL is the recommended pairing with WAL: still crash-safe for
    the database, it can only lose the last transactions on a power cut.

    busy_timeout covers the remaining case, two writers at once."""
    dbapi_connection.execute("PRAGMA foreign_keys=ON")
    dbapi_connection.execute("PRAGMA journal_mode=WAL")
    dbapi_connection.execute("PRAGMA synchronous=NORMAL")
    dbapi_connection.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")


@event.listens_for(engine.sync_engine, "connect")
def _configure(dbapi_connection, _record):
    configure_connection(dbapi_connection)


SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
