import uuid
from datetime import UTC, datetime, timedelta

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
    # The handlers it was sent to are recorded (the finalization barrier reads them).
    assert row.dispatched_handlers == [HANDLER]

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


# --------------------------------------------------- unprocessed_before (M4 D64)


class BarrierEvent(DomainEvent):
    event_type = "test.barrier_event"
    thing_id: uuid.UUID


class UnwatchedEvent(DomainEvent):
    event_type = "test.unwatched_event"
    thing_id: uuid.UUID


@subscribe(BarrierEvent)
async def barrier_first(session: AsyncSession, event: BarrierEvent) -> None:
    pass


@subscribe(BarrierEvent)
async def barrier_second(session: AsyncSession, event: BarrierEvent) -> None:
    pass


@subscribe(UnwatchedEvent)
async def unwatched(session: AsyncSession, event: UnwatchedEvent) -> None:
    pass


FIRST = f"{__name__}.barrier_first"
SECOND = f"{__name__}.barrier_second"
UNWATCHED = f"{__name__}.unwatched"


async def _event_at(session: AsyncSession, event: DomainEvent, at: datetime) -> OutboxEvent:
    row = emit(session, event)
    row.occurred_at = at
    await session.flush()
    return row


def test_handler_names_with_prefix() -> None:
    names = events.handler_names_with_prefix(f"{__name__}.barrier_")
    assert names == [FIRST, SECOND]
    assert events.handler_names_with_prefix("no.such.prefix.") == []


async def test_unprocessed_before_counts_events_waiting_for_any_named_handler(
    session: AsyncSession,
) -> None:
    cutoff = datetime(2026, 12, 2, 1, 0, tzinfo=UTC)
    before = await _event_at(
        session, BarrierEvent(thing_id=uuid.uuid4()), cutoff - timedelta(microseconds=1)
    )
    await _event_at(session, BarrierEvent(thing_id=uuid.uuid4()), cutoff)  # at cutoff: excluded
    await _event_at(
        session, UnwatchedEvent(thing_id=uuid.uuid4()), cutoff - timedelta(days=1)
    )  # no named handler subscribes to it

    assert await events.unprocessed_before(session, [FIRST, SECOND], cutoff) == 1
    # Dispatched to both handlers but not yet handled still waits.
    before.dispatched_at = cutoff
    before.dispatched_handlers = [FIRST, SECOND]
    await session.flush()
    assert await events.unprocessed_before(session, [FIRST, SECOND], cutoff) == 1

    assert await events.run_handler(session, before.id, FIRST) is True
    # The second handler hasn't run yet.
    assert await events.unprocessed_before(session, [FIRST, SECOND], cutoff) == 1
    assert await events.unprocessed_before(session, [FIRST], cutoff) == 0

    assert await events.run_handler(session, before.id, SECOND) is True
    assert await events.unprocessed_before(session, [FIRST, SECOND], cutoff) == 0


async def test_unprocessed_before_ignores_unknown_handlers_and_other_subscribers(
    session: AsyncSession,
) -> None:
    cutoff = datetime(2026, 12, 2, 1, 0, tzinfo=UTC)
    await _event_at(session, UnwatchedEvent(thing_id=uuid.uuid4()), cutoff - timedelta(hours=1))
    assert await events.unprocessed_before(session, [], cutoff) == 0
    assert await events.unprocessed_before(session, ["no.such.handler"], cutoff) == 0
    assert await events.unprocessed_before(session, [FIRST, SECOND], cutoff) == 0
    assert await events.unprocessed_before(session, [UNWATCHED], cutoff) >= 1


async def test_an_event_dispatched_before_a_handler_existed_never_waits_for_it(
    session: AsyncSession,
) -> None:
    """SEC3 M1: an M2/M3 event sent only to the handlers of its day must not block a scoring
    handler added later (nor a new M4b handler on an old event type)."""
    cutoff = datetime(2026, 12, 2, 1, 0, tzinfo=UTC)
    old = await _event_at(session, BarrierEvent(thing_id=uuid.uuid4()), cutoff - timedelta(days=1))
    old.dispatched_at = cutoff - timedelta(days=1)
    old.dispatched_handlers = [FIRST]  # SECOND did not exist yet
    await session.flush()
    assert await events.unprocessed_before(session, [SECOND], cutoff) == 0
    assert await events.unprocessed_before(session, [FIRST], cutoff) == 1
    # Backfilled events that nothing ran: an empty list waits for nobody.
    old.dispatched_handlers = []
    await session.flush()
    assert await events.unprocessed_before(session, [FIRST, SECOND], cutoff) == 0


async def test_unprocessed_before_honours_since(session: AsyncSession) -> None:
    cutoff = datetime(2026, 12, 2, 1, 0, tzinfo=UTC)
    since = datetime(2026, 10, 1, tzinfo=UTC)
    await _event_at(session, BarrierEvent(thing_id=uuid.uuid4()), since - timedelta(seconds=1))
    assert await events.unprocessed_before(session, [FIRST], cutoff, since=since) == 0
    await _event_at(session, BarrierEvent(thing_id=uuid.uuid4()), since)
    assert await events.unprocessed_before(session, [FIRST], cutoff, since=since) == 1


async def test_redispatch_stale_sends_unprocessed_pairs_again(session: AsyncSession) -> None:
    """SEC3 M1: a job that ran out of retries is enqueued again, once per missing handler, and
    only for handlers still registered."""
    now = datetime(2026, 12, 2, 1, 0, tzinfo=UTC)
    stale = await _event_at(session, BarrierEvent(thing_id=uuid.uuid4()), now - timedelta(hours=3))
    stale.dispatched_at = now - timedelta(hours=3)
    stale.dispatched_handlers = [FIRST, SECOND, "gone.handler"]
    fresh = await _event_at(session, BarrierEvent(thing_id=uuid.uuid4()), now)
    fresh.dispatched_at = now
    fresh.dispatched_handlers = [FIRST]
    await session.flush()
    assert await events.run_handler(session, stale.id, FIRST) is True

    enqueued: list[tuple[uuid.UUID, str]] = []

    async def enqueue(event_id: uuid.UUID, handler: str) -> None:
        enqueued.append((event_id, handler))

    await events.redispatch_stale(session, enqueue, now - timedelta(hours=1), batch_size=100_000)
    mine = [pair for pair in enqueued if pair[0] in {stale.id, fresh.id}]
    assert mine == [(stale.id, SECOND)]
