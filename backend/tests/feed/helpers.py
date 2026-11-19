"""Shared helpers for feed tests (typed, so mypy strict covers them).

``verified_deed`` inserts a verified QR deed directly (scan, group and deed rows), which is much
faster than the real scan + report path and lets tests choose ``verified_at`` freely. Each call
gets its own IST report day, so one doer/reporter pair can have many deeds.
"""

import itertools
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import OutboxEvent, ProcessedEvent, handlers_for, run_handler
from app.modules.deeds.models import (
    Deed,
    DeedStatus,
    QrScan,
    SameActGroup,
    ScanMethod,
    VerificationMethod,
)
from app.modules.feed._projection import sync_post
from app.modules.feed.models import FeedPost, FeedSave, Follow
from tests.deeds.helpers import CENTRE, Member

POINT = f"SRID=4326;POINT({CENTRE[1]} {CENTRE[0]})"
FEED_EVENTS = ("deeds.deed_verified", "deeds.deed_withdrawn", "deeds.deed_rejected")
_days = itertools.count()


def _next_day() -> date:
    return date(2001, 1, 1) + timedelta(days=next(_days))


async def verified_deed(
    session: AsyncSession,
    reporter: Member,
    doer: Member,
    *,
    verified_at: datetime | None = None,
    publish: bool = True,
    **overrides: Any,
) -> uuid.UUID:
    """A verified QR deed (and, with ``publish``, its synced feed post). Returns the deed id."""
    at = verified_at or datetime.now(UTC)
    day = _next_day()
    scan = QrScan(
        witness_user_id=reporter.user_id,
        doer_user_id=doer.user_id,
        scan_method=ScanMethod.QR,
        token_window=uuid.uuid4().int % 10**9,
        scanned_at=at,
        scan_day=day,
        location=POINT,
        accuracy_m=20,
        expires_at=at + timedelta(minutes=15),
        consumed_at=at,
        limits_version=1,
    )
    group = SameActGroup(
        doer_user_id=doer.user_id,
        centre=POINT,
        anchor_at=at,
        radius_m=200,
        window_minutes=120,
        cap=3,
        verified_count=1,
        counted_count=1,
        distinct_reporters=1,
        limits_version=1,
    )
    session.add_all([scan, group])
    await session.flush()
    values: dict[str, Any] = {
        "reporter_user_id": reporter.user_id,
        "doer_user_id": doer.user_id,
        "category": "animals",
        "description": "Helped a stray dog across the road",
        "beneficiaries_count": 2,
        "location": POINT,
        "occurred_at": at - timedelta(minutes=5),
        "reported_at": at,
        "report_day": day,
        "status": DeedStatus.VERIFIED,
        "verification_method": VerificationMethod.QR_SCAN,
        "verified_at": at,
        "scan_id": scan.id,
        "same_act_group_id": group.id,
        "counts_for_points": True,
        "prize_rank_eligible": True,
        "limits_version": 1,
    }
    values.update(overrides)
    deed = Deed(**values)
    session.add(deed)
    await session.flush()
    if publish:
        await sync_post(session, deed.id)
    return deed.id


async def post_of(session: AsyncSession, deed_id: uuid.UUID) -> FeedPost | None:
    return (
        await session.scalars(
            select(FeedPost)
            .where(FeedPost.deed_id == deed_id)
            .execution_options(populate_existing=True)
        )
    ).first()


async def set_post(session: AsyncSession, deed_id: uuid.UUID, **values: Any) -> None:
    post = await post_of(session, deed_id)
    assert post is not None
    for key, value in values.items():
        setattr(post, key, value)
    await session.flush()


async def withdraw_directly(session: AsyncSession, deed_id: uuid.UUID) -> None:
    """Withdraw a deed without running any feed handler (the live-check window, D37)."""
    deed = await session.get(Deed, deed_id)
    assert deed is not None
    deed.status = DeedStatus.WITHDRAWN
    deed.withdrawn_at = datetime.now(UTC)
    await session.flush()


async def follow(session: AsyncSession, follower: Member, followed: Member) -> None:
    session.add(Follow(follower_id=follower.user_id, followed_id=followed.user_id))
    await session.flush()


async def save(
    session: AsyncSession, user: Member, deed_id: uuid.UUID, at: datetime | None = None
) -> None:
    session.add(FeedSave(user_id=user.user_id, deed_id=deed_id, created_at=at or datetime.now(UTC)))
    await session.flush()


async def feed_events(session: AsyncSession, deed_id: uuid.UUID) -> list[OutboxEvent]:
    rows = await session.scalars(
        select(OutboxEvent)
        .where(
            OutboxEvent.event_type.in_(FEED_EVENTS),
            OutboxEvent.payload["deed_id"].astext == str(deed_id),
        )
        .order_by(OutboxEvent.occurred_at)
    )
    return list(rows)


async def run_feed_handlers(session: AsyncSession, events: list[OutboxEvent]) -> int:
    """Run the feed's handlers for these events (as the worker would). Returns how many ran."""
    ran = 0
    for event in events:
        for name in handlers_for(event.event_type):
            if not name.startswith("app.modules.feed."):
                continue
            if await run_handler(session, event.id, name):
                ran += 1
    return ran


async def processed(session: AsyncSession, event: OutboxEvent) -> int:
    rows = await session.scalars(select(ProcessedEvent).where(ProcessedEvent.event_id == event.id))
    return len(list(rows))
