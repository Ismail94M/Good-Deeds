import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import events
from app.core.events import DomainEvent, OutboxEvent, emit, subscribe


class SomethingHappened(DomainEvent):
    event_type = "test.something_happened"
    thing_id: uuid.UUID


received: list[uuid.UUID] = []


@subscribe(SomethingHappened)
async def remember_thing(session: AsyncSession, event: SomethingHappened) -> None:
    received.append(event.thing_id)


HANDLER = f"{__name__}.remember_thing"


@pytest.fixture(autouse=True)
def _reset() -> None:
    received.clear()


async def test_emit_writes_to_outbox_in_callers_transaction(session: AsyncSession) -> None:
    thing_id = uuid.uuid4()
    row = emit(session, SomethingHappened(thing_id=thing_id))
    await session.flush()

    stored = await session.get(OutboxEvent, row.id)
    assert stored is not None
    assert stored.event_type == "test.something_happened"
    assert stored.payload == {"thing_id": str(thing_id)}
    assert stored.dispatched_at is None


async def test_dispatch_enqueues_each_handler_once(session: AsyncSession) -> None:
    row = emit(session, SomethingHappened(thing_id=uuid.uuid4()))
    await session.flush()
    enqueued: list[tuple[uuid.UUID, str]] = []

    async def enqueue(event_id: uuid.UUID, handler: str) -> None:
        enqueued.append((event_id, handler))

    assert await events.dispatch_pending(session, enqueue) >= 1
    assert (row.id, HANDLER) in enqueued
    assert row.dispatched_at is not None

    enqueued.clear()
    assert await events.dispatch_pending(session, enqueue) == 0
    assert enqueued == []


async def test_run_handler_is_idempotent(session: AsyncSession) -> None:
    thing_id = uuid.uuid4()
    row = emit(session, SomethingHappened(thing_id=thing_id))
    await session.flush()

    assert await events.run_handler(session, row.id, HANDLER) is True
    assert await events.run_handler(session, row.id, HANDLER) is False
    assert received == [thing_id]


async def test_run_handler_rejects_unknown_handler(session: AsyncSession) -> None:
    row = emit(session, SomethingHappened(thing_id=uuid.uuid4()))
    await session.flush()

    with pytest.raises(LookupError):
        await events.run_handler(session, row.id, "nope.handler")


def test_event_type_is_required() -> None:
    with pytest.raises(TypeError):

        class Missing(DomainEvent):
            value: int


def test_duplicate_event_type_is_rejected() -> None:
    with pytest.raises(TypeError):

        class Clash(DomainEvent):
            event_type = "test.something_happened"
