"""Domain events delivered through a transactional outbox.

Modules never call each other for side effects. Instead they `emit()` an event inside
the same transaction as their change. The worker dispatches each outbox row once per
subscribed handler, and `run_handler` records the (event, handler) pair in the same
transaction as the handler's work, so every handler's effect happens exactly once.
"""

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict
from sqlalchemy import DateTime, ForeignKey, String, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

_event_types: dict[str, type["DomainEvent"]] = {}


class DomainEvent(BaseModel):
    """Base class for events. Subclasses set a unique `event_type`."""

    model_config = ConfigDict(frozen=True)
    event_type: ClassVar[str]

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: Any) -> None:
        super().__pydantic_init_subclass__(**kwargs)
        name = cls.__dict__.get("event_type")
        if not isinstance(name, str):
            raise TypeError(f"{cls.__name__} must define event_type")
        existing = _event_types.get(name)
        if existing is not None and existing.__qualname__ != cls.__qualname__:
            raise TypeError(f"Duplicate event_type {name!r}")
        _event_types[name] = cls


class OutboxEvent(Base):
    __tablename__ = "outbox_events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    event_type: Mapped[str] = mapped_column(String(100), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class ProcessedEvent(Base):
    __tablename__ = "processed_events"

    event_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("outbox_events.id", ondelete="CASCADE"), primary_key=True
    )
    handler: Mapped[str] = mapped_column(String(200), primary_key=True)
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


type Handler = Callable[[AsyncSession, Any], Awaitable[None]]
type Enqueue = Callable[[uuid.UUID, str], Awaitable[None]]

_handlers: dict[str, dict[str, Handler]] = {}


def subscribe[E: DomainEvent](
    event_cls: type[E],
) -> Callable[
    [Callable[[AsyncSession, E], Awaitable[None]]], Callable[[AsyncSession, E], Awaitable[None]]
]:
    """Register an async handler `(session, event) -> None` for an event type."""

    def decorator(
        fn: Callable[[AsyncSession, E], Awaitable[None]],
    ) -> Callable[[AsyncSession, E], Awaitable[None]]:
        name = f"{fn.__module__}.{fn.__qualname__}"
        _handlers.setdefault(event_cls.event_type, {})[name] = fn
        return fn

    return decorator


def handlers_for(event_type: str) -> dict[str, Handler]:
    return dict(_handlers.get(event_type, {}))


def emit(session: AsyncSession, event: DomainEvent) -> OutboxEvent:
    """Add an event to the outbox. It is committed (or rolled back) with the caller's work."""
    row = OutboxEvent(event_type=event.event_type, payload=event.model_dump(mode="json"))
    session.add(row)
    return row


async def dispatch_pending(session: AsyncSession, enqueue: Enqueue, batch_size: int = 100) -> int:
    """Enqueue one job per (event, handler) for undispatched events. Returns events dispatched.

    Rows are locked with SKIP LOCKED so several workers can dispatch concurrently.
    Enqueueing is at-least-once; `run_handler` makes handling idempotent.
    """
    result = await session.execute(
        select(OutboxEvent)
        .where(OutboxEvent.dispatched_at.is_(None))
        .order_by(OutboxEvent.occurred_at)
        .limit(batch_size)
        .with_for_update(skip_locked=True)
    )
    rows = result.scalars().all()
    for row in rows:
        for handler_name in handlers_for(row.event_type):
            await enqueue(row.id, handler_name)
        row.dispatched_at = datetime.now(UTC)
    await session.flush()
    return len(rows)


async def run_handler(session: AsyncSession, event_id: uuid.UUID, handler_name: str) -> bool:
    """Run one handler for one event. Returns False if it already ran (idempotent).

    The caller owns the transaction: commit after this returns so the handler's work
    and the processed marker are saved together.
    """
    if await session.get(ProcessedEvent, (event_id, handler_name)) is not None:
        return False
    row = await session.get(OutboxEvent, event_id)
    if row is None:
        raise LookupError(f"Outbox event {event_id} not found")
    handler = _handlers.get(row.event_type, {}).get(handler_name)
    if handler is None:
        raise LookupError(f"No handler {handler_name!r} for {row.event_type!r}")
    event = _event_types[row.event_type].model_validate(row.payload)
    await handler(session, event)
    session.add(ProcessedEvent(event_id=event_id, handler=handler_name))
    await session.flush()
    return True
