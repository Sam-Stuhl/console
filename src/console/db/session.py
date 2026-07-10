from collections.abc import AsyncIterator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from console import config

engine = create_async_engine(f"sqlite+aiosqlite:///{config.DB_PATH}")


# SQLite ships with foreign keys off; cascade deletes need them on.
@event.listens_for(engine.sync_engine, "connect")
def _enable_foreign_keys(dbapi_connection, _record):
    dbapi_connection.execute("PRAGMA foreign_keys=ON")


SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
