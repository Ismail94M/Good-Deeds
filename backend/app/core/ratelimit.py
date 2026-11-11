"""Fixed-window rate limits stored in Postgres (no Redis needed).

Each hit is an atomic upsert on (key, window_start), committed in its own short
transaction so that requests which fail later still count.
"""

import time
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Annotated, Protocol

from fastapi import Depends, Request
from sqlalchemy import DateTime, Integer, String, delete, func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import parse_rate
from app.core.db import Base, SessionLocal
from app.core.errors import TooManyRequests


class RateLimitCounter(Base):
    __tablename__ = "rate_limits"

    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


def _window_start(seconds: int, now: float) -> datetime:
    return datetime.fromtimestamp(now - (now % seconds), UTC)


async def increment(session: AsyncSession, key: str, seconds: int, now: float) -> int:
    """Add one hit to the key's current window and return the new count."""
    stmt = (
        insert(RateLimitCounter)
        .values(key=key, window_start=_window_start(seconds, now), count=1)
        .on_conflict_do_update(
            index_elements=["key", "window_start"],
            set_={"count": RateLimitCounter.count + 1},
        )
        .returning(RateLimitCounter.count)
    )
    return int((await session.execute(stmt)).scalar_one())


async def purge_old(session: AsyncSession, older_than: timedelta = timedelta(days=1)) -> None:
    await session.execute(
        delete(RateLimitCounter).where(
            RateLimitCounter.window_start < datetime.now(UTC) - older_than
        )
    )


class RateLimiter(Protocol):
    async def hit(self, key: str, spec: str) -> None:
        """Count a request against `key` with a '<count>/<seconds>' limit; raise when over."""
        ...


_MESSAGE = "Too many attempts. Please wait a while and try again."


class PostgresRateLimiter:
    async def hit(self, key: str, spec: str) -> None:
        limit, seconds = parse_rate(spec)
        async with SessionLocal() as session, session.begin():
            count = await increment(session, key, seconds, time.time())
        if count > limit:
            raise TooManyRequests(_MESSAGE, code="rate_limited")


class InMemoryRateLimiter:
    """For tests: same semantics, no database."""

    def __init__(self) -> None:
        self.counts: dict[tuple[str, int], int] = defaultdict(int)

    async def hit(self, key: str, spec: str) -> None:
        limit, seconds = parse_rate(spec)
        window = int(time.time() // seconds)
        self.counts[(key, window)] += 1
        if self.counts[(key, window)] > limit:
            raise TooManyRequests(_MESSAGE, code="rate_limited")


_postgres_limiter = PostgresRateLimiter()


def get_rate_limiter() -> RateLimiter:
    """FastAPI dependency (tests override it with InMemoryRateLimiter)."""
    return _postgres_limiter


Limiter = Annotated[RateLimiter, Depends(get_rate_limiter)]


def client_ip(request: Request) -> str:
    """Client address. Behind a proxy, run uvicorn with --proxy-headers and
    --forwarded-allow-ips=<proxy> so this is the real client, not a spoofable header."""
    return request.client.host if request.client else "unknown"
