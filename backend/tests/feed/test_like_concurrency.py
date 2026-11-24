"""Concurrent likes in separate sessions (test plan §8 "Social": counters after a barrier run).

These commit for real, so the fixture deletes everything they created afterwards.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import pytest
from sqlalchemy import func, select, text

from app.core.db import SessionLocal, engine
from app.modules.feed import _writes
from app.modules.feed.models import FeedLike, FeedPost
from tests.deeds.helpers import Member, make_member
from tests.feed.helpers import verified_deed


@dataclass
class Committed:
    user_ids: list[uuid.UUID] = field(default_factory=list)
    deed_ids: list[uuid.UUID] = field(default_factory=list)

    async def member(self) -> Member:
        async with SessionLocal() as s:
            m = await make_member(s, phone_history=False)
        self.user_ids.append(m.user_id)
        return m


@pytest.fixture
async def committed(migrated_db: None) -> AsyncIterator[Committed]:
    tracker = Committed()
    yield tracker
    async with engine.begin() as conn:
        d = {"d": tracker.deed_ids}
        u = {"u": tracker.user_ids}
        await conn.execute(text("DELETE FROM feed_likes WHERE deed_id = ANY(:d)"), d)
        await conn.execute(
            text("DELETE FROM outbox_events WHERE (payload->>'deed_id')::uuid = ANY(:d)"), d
        )
        await conn.execute(text("DELETE FROM feed_posts WHERE deed_id = ANY(:d)"), d)
        groups = list(
            (
                await conn.execute(
                    text("SELECT same_act_group_id FROM deeds WHERE id = ANY(:d)"), d
                )
            ).scalars()
        )
        scans = list(
            (await conn.execute(text("SELECT scan_id FROM deeds WHERE id = ANY(:d)"), d)).scalars()
        )
        await conn.execute(text("DELETE FROM deeds WHERE id = ANY(:d)"), d)
        await conn.execute(text("DELETE FROM same_act_groups WHERE id = ANY(:g)"), {"g": groups})
        await conn.execute(text("DELETE FROM qr_scans WHERE id = ANY(:s)"), {"s": scans})
        for table in ("consents", "doer_codes"):
            await conn.execute(text(f"DELETE FROM {table} WHERE user_id = ANY(:u)"), u)
        await conn.execute(text("DELETE FROM users WHERE id = ANY(:u)"), u)


async def test_concurrent_likes_keep_counters_exact(committed: Committed) -> None:
    reporter, doer = await committed.member(), await committed.member()
    likers = [await committed.member() for _ in range(10)]
    async with SessionLocal() as s:
        deed_id = await verified_deed(s, reporter, doer)
        await s.commit()
    committed.deed_ids.append(deed_id)

    everyone = [*likers, doer, reporter]
    barrier = asyncio.Barrier(len(everyone))  # 11 single likers + the flipper

    async def like_once(member: Member) -> None:
        async with SessionLocal() as s:
            await barrier.wait()
            await _writes.like(s, deed_id, member.user_id)
            await s.commit()

    async def flip(member: Member) -> None:
        """One user liking and unliking repeatedly, alongside the others."""
        await barrier.wait()
        for i in range(6):
            async with SessionLocal() as s:
                if i % 2 == 0:
                    await _writes.like(s, deed_id, member.user_id)
                else:
                    await _writes.unlike(s, deed_id, member.user_id)
                await s.commit()

    flipper = likers[0]
    async with asyncio.timeout(60):
        await asyncio.gather(*(like_once(m) for m in everyone if m != flipper), flip(flipper))

    async with SessionLocal() as s:
        post = await s.get(FeedPost, deed_id)
        assert post is not None
        rows = await s.scalar(
            select(func.count()).select_from(FeedLike).where(FeedLike.deed_id == deed_id)
        )
        others = await s.scalar(
            select(func.count())
            .select_from(FeedLike)
            .where(
                FeedLike.deed_id == deed_id,
                FeedLike.user_id.not_in([doer.user_id, reporter.user_id]),
            )
        )
        assert (
            post.like_count == rows == 11
        )  # 9 likers + doer + reporter; the flipper ended unliked
        assert post.top_like_count == others == 9
