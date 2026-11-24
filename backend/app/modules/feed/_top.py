"""Top snapshots, one ranking per period (docs/M3-DESIGN.md §3.4 #1 "top", Q26, Q43, L1, D40).

``score = top_like_weight * top_like_count + top_comment_weight * top_comment_count`` over posts
that are ``listed`` and were published inside the period's **rolling** window (the last
``top_{period}_days`` days). The counts are the post's totals, not only engagement inside the
window. ``top_like_count`` and ``top_comment_count`` leave out the doer's and reporter's own likes
(Q43) and comments (L1). Ties: the newer deed first, then ``deed_id``. Display order only: nothing
here touches deeds, counts, points or prizes.

Reads never compute scores. The job keeps the newest two snapshots per period, so a reader holding
the previous snapshot's cursor can finish its pages.
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import advisory_xact_lock
from app.modules.feed._limits import FeedLimits
from app.modules.feed.models import FeedPost, FeedTopEntry, FeedTopSnapshot, TopPeriod

KEEP_SNAPSHOTS = 2


async def compute_snapshot(
    session: AsyncSession, period: TopPeriod, limits: FeedLimits, *, now: datetime | None = None
) -> FeedTopSnapshot:
    """Rank the period's window now, store it, and drop all but the newest two snapshots of the
    period."""
    now = now or datetime.now(UTC)
    # One computation per period at a time (two workers, or a manual run beside the job).
    await advisory_xact_lock(session, "feed_top", period.value)
    window_start = now - timedelta(days=limits.top_days(period.value))
    score = (
        limits.top_like_weight * FeedPost.top_like_count
        + limits.top_comment_weight * FeedPost.top_comment_count
    ).label("score")
    rows = (
        await session.execute(
            select(FeedPost.deed_id, score)
            .where(FeedPost.listed, FeedPost.published_at >= window_start)
            .order_by(score.desc(), FeedPost.published_at.desc(), FeedPost.deed_id.desc())
            .limit(limits.top_size)
        )
    ).all()
    snapshot = FeedTopSnapshot(
        id=uuid.uuid4(),
        period=period,
        computed_at=now,
        window_start=window_start,
        weights={"like": limits.top_like_weight, "comment": limits.top_comment_weight},
    )
    session.add(snapshot)
    await session.flush()
    if rows:
        await session.execute(
            insert(FeedTopEntry),
            [
                {"snapshot_id": snapshot.id, "rank": rank, "deed_id": deed_id, "score": value}
                for rank, (deed_id, value) in enumerate(rows, start=1)
            ],
        )
    keep = select(FeedTopSnapshot.id).where(FeedTopSnapshot.period == period)
    keep = keep.order_by(FeedTopSnapshot.computed_at.desc(), FeedTopSnapshot.id.desc())
    await session.execute(
        delete(FeedTopSnapshot).where(
            FeedTopSnapshot.period == period,
            FeedTopSnapshot.id.not_in(keep.limit(KEEP_SNAPSHOTS).scalar_subquery()),
        )
    )
    await session.flush()
    return snapshot


async def refresh_due(
    session: AsyncSession, limits: FeedLimits, *, now: datetime | None = None
) -> list[TopPeriod]:
    """Compute every period whose newest snapshot is at least ``top_{period}_refresh_minutes``
    old (or that has none). Returns the periods computed. Intervals are config, so the job runs
    every minute and decides here."""
    now = now or datetime.now(UTC)
    done: list[TopPeriod] = []
    for period in TopPeriod:
        latest = await session.scalar(
            select(func.max(FeedTopSnapshot.computed_at)).where(FeedTopSnapshot.period == period)
        )
        due = timedelta(minutes=limits.top_refresh_minutes(period.value))
        if latest is None or now - latest >= due:
            await compute_snapshot(session, period, limits, now=now)
            done.append(period)
    return done


async def newest_snapshot(session: AsyncSession, period: TopPeriod) -> FeedTopSnapshot | None:
    return await session.scalar(
        select(FeedTopSnapshot)
        .where(FeedTopSnapshot.period == period)
        .order_by(FeedTopSnapshot.computed_at.desc(), FeedTopSnapshot.id.desc())
        .limit(1)
    )
