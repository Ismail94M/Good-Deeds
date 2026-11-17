from collections.abc import AsyncIterator

from sqlalchemy import MetaData, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


# Isolation stays at Postgres's default, READ COMMITTED. The monthly-limit checks (deeds
# ``_reporter_limit``, invite accept) do unlocked pre-reads and then count again after taking the
# ``reporter_month`` advisory lock; under READ COMMITTED each statement sees rows committed by the
# previous lock holder. REPEATABLE READ or SERIALIZABLE would keep the transaction's first
# snapshot, so the locked count could miss them. Don't raise the isolation level.
engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one session per request."""
    async with SessionLocal() as session:
        yield session


async def advisory_xact_lock(session: AsyncSession, *parts: str) -> None:
    """Take a Postgres advisory lock held until the current transaction ends.

    ``parts`` are joined into one key (e.g. ``("same_act", doer_id)``) and hashed to 64 bits.
    """
    key = ":".join(parts)
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": key})
