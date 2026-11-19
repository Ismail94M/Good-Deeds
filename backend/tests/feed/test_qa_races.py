"""QA5 (M3) concurrency and interleaving cases for feed writes (test plan §8 "Social", "Comment
deletion and reports"; role file: concurrent writes).

These commit for real in separate sessions, so the fixture deletes everything they created
afterwards. Assertions are by this test's own IDs only.

Interleavings are made deterministic by pausing one session just before it takes the advisory
lock (a wrapper around the module's ``advisory_xact_lock``), running the other session to its
commit, then letting the first one go on.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import SessionLocal, advisory_xact_lock, engine
from app.core.errors import AppError
from app.modules.feed import _comments, _follows, _reports
from app.modules.feed._limits import load_limits
from app.modules.feed._shared_limits import load_shared
from app.modules.feed.models import (
    CommentReport,
    CommentReportCase,
    FeedComment,
    Follow,
    ReportCaseStatus,
)
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

    async def deed(self, reporter: Member, doer: Member) -> uuid.UUID:
        async with SessionLocal() as s:
            deed_id = await verified_deed(s, reporter, doer)
            await s.commit()
        self.deed_ids.append(deed_id)
        return deed_id


async def cleanup(tracker: Committed) -> None:
    """Delete every row the tracked deeds and users own (children first)."""
    async with engine.begin() as conn:
        d = {"d": tracker.deed_ids}
        u = {"u": tracker.user_ids}
        await conn.execute(text("DELETE FROM comment_reports WHERE reporter_user_id = ANY(:u)"), u)
        await conn.execute(
            text(
                "DELETE FROM comment_reports WHERE case_id IN "
                "(SELECT id FROM comment_report_cases WHERE deed_id = ANY(:d))"
            ),
            d,
        )
        await conn.execute(text("DELETE FROM comment_report_cases WHERE deed_id = ANY(:d)"), d)
        await conn.execute(text("DELETE FROM feed_comments WHERE deed_id = ANY(:d)"), d)
        await conn.execute(
            text("DELETE FROM follows WHERE follower_id = ANY(:u) OR followed_id = ANY(:u)"), u
        )
        await conn.execute(text("DELETE FROM feed_likes WHERE deed_id = ANY(:d)"), d)
        await conn.execute(
            text(
                "DELETE FROM photo_reports WHERE case_id IN (SELECT c.id FROM photo_report_cases c"
                " JOIN photos p ON p.id = c.photo_id WHERE p.deed_id = ANY(:d))"
            ),
            d,
        )
        await conn.execute(
            text(
                "DELETE FROM photo_report_cases WHERE photo_id IN "
                "(SELECT id FROM photos WHERE deed_id = ANY(:d))"
            ),
            d,
        )
        await conn.execute(text("DELETE FROM photos WHERE deed_id = ANY(:d)"), d)
        await conn.execute(
            text(
                "DELETE FROM outbox_events WHERE (payload->>'deed_id')::uuid = ANY(:d)"
                " OR (payload->>'follower_id')::uuid = ANY(:u)"
            ),
            {**d, **u},
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


@pytest.fixture
async def committed(migrated_db: None) -> AsyncIterator[Committed]:
    tracker = Committed()
    yield tracker
    await cleanup(tracker)


class PauseBeforeLock:
    """Replaces a module's ``advisory_xact_lock``: the chosen session stops right before taking
    the lock until ``go`` is set; every other session passes straight through."""

    def __init__(self, real: Callable[..., Awaitable[None]]) -> None:
        self.real = real
        self.target: AsyncSession | None = None
        self.reached = asyncio.Event()
        self.go = asyncio.Event()

    async def __call__(self, session: AsyncSession, *parts: str) -> None:
        if session is self.target and not self.go.is_set():
            self.reached.set()
            await self.go.wait()
        await self.real(session, *parts)


async def _live_comment(tracker: Committed) -> tuple[Member, Member, Member, uuid.UUID, uuid.UUID]:
    reporter, doer, author = (
        await tracker.member(),
        await tracker.member(),
        await tracker.member(),
    )
    deed_id = await tracker.deed(reporter, doer)
    async with SessionLocal() as s:
        _, limits = await load_limits(s)
        out = await _comments.post_comment(
            s, deed_id, author.user_id, "Not a nice thing to say", limits=limits
        )
        await s.commit()
    return reporter, doer, author, deed_id, out.id


async def _open_cases(comment_id: uuid.UUID) -> list[CommentReportCase]:
    async with SessionLocal() as s:
        return list(
            await s.scalars(
                select(CommentReportCase).where(
                    CommentReportCase.comment_id == comment_id,
                    CommentReportCase.status == ReportCaseStatus.OPEN,
                )
            )
        )


# --------------------------------------------------- comment reports


async def test_concurrent_first_reports_open_one_case(committed: Committed) -> None:
    """Five members report the same comment at once (nobody has yet): exactly one open case,
    ``report_count`` 5 and the most serious priority; one member reporting three times at once
    adds one report."""
    _, _, _, _, comment_id = await _live_comment(committed)
    reporters = [await committed.member() for _ in range(5)]
    twice = reporters[0]
    reasons = ["spam", "other", "hate", "spam", "other"]
    calls: list[tuple[Member, str]] = [*zip(reporters, reasons, strict=True)]
    calls += [(twice, "spam"), (twice, "other")]
    barrier = asyncio.Barrier(len(calls))

    async def report(member: Member, reason: str) -> None:
        async with SessionLocal() as s:
            shared = await load_shared(s)
            await barrier.wait()
            await _reports.report_comment(
                s, comment_id, member.user_id, reason, None, shared=shared
            )
            await s.commit()

    async with asyncio.timeout(60):
        await asyncio.gather(*(report(m, r) for m, r in calls))

    [case] = await _open_cases(comment_id)
    assert (case.report_count, case.priority) == (5, 2)
    async with SessionLocal() as s:
        rows = await s.scalar(
            select(func.count())
            .select_from(CommentReport)
            .where(CommentReport.comment_id == comment_id)
        )
    assert rows == 5


async def test_report_racing_a_deletion_never_leaves_an_open_case(
    committed: Committed, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, author, _, comment_id = await _live_comment(committed)
    member = await committed.member()
    pause = PauseBeforeLock(advisory_xact_lock)
    monkeypatch.setattr(_reports, "advisory_xact_lock", pause)
    outcome: dict[str, Any] = {}

    async def report(s: AsyncSession) -> None:
        shared = await load_shared(s)
        try:
            await _reports.report_comment(
                s, comment_id, member.user_id, "spam", None, shared=shared
            )
            await s.commit()
            outcome["result"] = "accepted"
        except AppError as exc:
            await s.rollback()
            outcome["result"] = str(exc.code)

    async with asyncio.timeout(60), SessionLocal() as a, SessionLocal() as b:
        pause.target = a
        task = asyncio.create_task(report(a))
        await pause.reached.wait()  # the report has read the comment as live
        await _comments.delete_comment(b, comment_id, author.user_id)
        await b.commit()
        pause.go.set()
        await task

    async with SessionLocal() as s:
        comment = await s.get(FeedComment, comment_id)
        assert comment is not None and comment.deleted_at is not None
    # Either the late report is refused, or it never leaves an open case on a deleted comment.
    assert outcome["result"] in ("accepted", "comment_not_found")
    assert await _open_cases(comment_id) == []


# --------------------------------------------------- follows


async def test_concurrent_follows_never_pass_max_following(
    committed: Committed,
) -> None:
    """At ``max_following`` minus 1, two follows of different people at once: exactly one wins. The
    limit is passed in (not published), so nothing committed changes the shared config."""
    me = await committed.member()
    targets = [await committed.member() for _ in range(3)]
    async with SessionLocal() as s:
        _, current = await load_limits(s)
    limits = current.model_copy(update={"max_following": 2})
    async with SessionLocal() as s:
        await _follows.follow(s, me.user_id, targets[0].handle, limits=limits)
        await s.commit()

    barrier = asyncio.Barrier(2)
    results: list[str] = []

    async def follow(target: Member) -> None:
        async with SessionLocal() as s:
            await barrier.wait()
            try:
                await _follows.follow(s, me.user_id, target.handle, limits=limits)
                await s.commit()
                results.append("ok")
            except AppError as exc:
                await s.rollback()
                results.append(str(exc.code))

    async with asyncio.timeout(60):
        await asyncio.gather(follow(targets[1]), follow(targets[2]))

    assert sorted(results) == ["following_limit", "ok"]
    async with SessionLocal() as s:
        count = await s.scalar(
            select(func.count()).select_from(Follow).where(Follow.follower_id == me.user_id)
        )
    assert count == 2


async def test_concurrent_same_follow_is_one_row_and_one_event(committed: Committed) -> None:
    me, target = await committed.member(), await committed.member()
    async with SessionLocal() as s:
        _, limits = await load_limits(s)
    barrier = asyncio.Barrier(4)

    async def follow() -> None:
        async with SessionLocal() as s:
            await barrier.wait()
            await _follows.follow(s, me.user_id, target.handle, limits=limits)
            await s.commit()

    async with asyncio.timeout(60):
        await asyncio.gather(*(follow() for _ in range(4)))
    async with SessionLocal() as s:
        rows = await s.scalar(
            select(func.count()).select_from(Follow).where(Follow.follower_id == me.user_id)
        )
        events = await s.scalar(
            text(
                "SELECT count(*) FROM outbox_events WHERE event_type = 'feed.user_followed' "
                "AND payload->>'follower_id' = :f"
            ),
            {"f": str(me.user_id)},
        )
    assert (rows, events) == (1, 1)
