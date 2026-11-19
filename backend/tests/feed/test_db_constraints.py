"""Constraints on the feed tables hold even if service code is bypassed (M3-DESIGN §2.3,
migration 0015). Services map ``IntegrityError`` to error codes by constraint name, so each test
checks the name."""

import importlib.util
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Connection, create_engine, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.modules.deeds.models import Deed
from app.modules.feed.models import (
    COMMENT_REPORT_PRIORITY,
    CaseDecision,
    CommentDecision,
    CommentDecisionAction,
    CommentDeletedRole,
    CommentReport,
    CommentReportCase,
    CommentReportReason,
    FeedComment,
    FeedLike,
    FeedNoticeSeen,
    FeedPost,
    FeedSave,
    FeedShare,
    FeedTopEntry,
    FeedTopSnapshot,
    Follow,
    PostState,
    ReportCaseStatus,
    ShareTarget,
    TopPeriod,
)
from app.modules.identity.models import User
from tests.conftest import BACKEND_DIR
from tests.deeds.test_db_constraints import _users, _verified_qr_deed


def _now() -> datetime:
    return datetime.now(UTC)


async def _rejects(session: AsyncSession, constraint: str, *objects: Any) -> None:
    with pytest.raises(IntegrityError, match=constraint):
        async with session.begin_nested():
            session.add_all(objects)
            await session.flush()


def _post(deed: Deed, **overrides: Any) -> FeedPost:
    assert deed.doer_user_id is not None and deed.verified_at is not None
    values: dict[str, Any] = {
        "deed_id": deed.id,
        "doer_id": deed.doer_user_id,
        "reporter_id": deed.reporter_user_id,
        "published_at": deed.verified_at,
        "state": PostState.PUBLISHED,
        "synced_at": _now(),
    }
    values.update(overrides)
    return FeedPost(**values)


async def _published(session: AsyncSession) -> tuple[User, User, FeedPost]:
    reporter, doer = await _users(session, 2)
    deed = await _verified_qr_deed(session, reporter, doer)
    post = _post(deed)
    session.add(post)
    await session.flush()
    await session.refresh(post)
    return reporter, doer, post


# --- feed_posts ---------------------------------------------------------------------------------


async def test_listed_is_generated_from_state_and_both_hides(session: AsyncSession) -> None:
    _, _, post = await _published(session)
    assert post.listed is True
    assert (post.like_count, post.top_like_count, post.comment_count) == (0, 0, 0)

    async def listed_after(**changes: Any) -> bool:
        for key, value in changes.items():
            setattr(post, key, value)
        await session.flush()
        await session.refresh(post)
        return post.listed

    assert await listed_after(hidden_by_doer_at=_now()) is False
    assert await listed_after(hidden_by_reporter_at=_now()) is False
    assert await listed_after(hidden_by_doer_at=None) is False  # the reporter's hide still holds
    assert await listed_after(hidden_by_reporter_at=None) is True
    for state in (PostState.WITHDRAWN, PostState.REJECTED, PostState.REMOVED, PostState.HELD):
        assert await listed_after(state=state) is False
    assert await listed_after(state=PostState.PUBLISHED) is True


async def test_listed_cannot_be_written(session: AsyncSession) -> None:
    _, _, post = await _published(session)
    with pytest.raises(DBAPIError, match="listed"):
        async with session.begin_nested():
            await session.execute(
                text("UPDATE feed_posts SET listed = false WHERE deed_id = :id"),
                {"id": post.deed_id},
            )


@pytest.mark.parametrize(
    ("counts", "constraint"),
    [
        ({"like_count": -1}, "ck_feed_posts_like_count_non_negative"),
        ({"comment_count": -1}, "ck_feed_posts_comment_count_non_negative"),
        ({"top_like_count": -1}, "ck_feed_posts_top_like_count_valid"),
        ({"like_count": 2, "top_like_count": 3}, "ck_feed_posts_top_like_count_valid"),
    ],
)
async def test_post_counters_are_checked(
    session: AsyncSession, counts: dict[str, int], constraint: str
) -> None:
    reporter, doer = await _users(session, 2)
    deed = await _verified_qr_deed(session, reporter, doer)
    await _rejects(session, constraint, _post(deed, **counts))


async def test_top_like_count_may_equal_like_count(session: AsyncSession) -> None:
    reporter, doer = await _users(session, 2)
    deed = await _verified_qr_deed(session, reporter, doer)
    session.add(_post(deed, like_count=3, top_like_count=3))
    await session.flush()


async def test_post_state_is_checked(session: AsyncSession) -> None:
    _, _, post = await _published(session)
    with pytest.raises(IntegrityError, match="ck_feed_posts_post_state"):
        async with session.begin_nested():
            await session.execute(
                text("UPDATE feed_posts SET state = 'pending' WHERE deed_id = :id"),
                {"id": post.deed_id},
            )


async def test_one_post_per_deed_and_deed_must_exist(session: AsyncSession) -> None:
    _, _, post = await _published(session)
    deed = await session.get(Deed, post.deed_id)
    assert deed is not None
    with pytest.raises(IntegrityError, match="pk_feed_posts"):
        async with session.begin_nested():
            await session.execute(
                text(
                    "INSERT INTO feed_posts (deed_id, doer_id, reporter_id, published_at, state, "
                    "synced_at) SELECT deed_id, doer_id, reporter_id, published_at, state, "
                    "synced_at FROM feed_posts WHERE deed_id = :id"
                ),
                {"id": post.deed_id},
            )
    ghost = _post(deed, deed_id=uuid.uuid4())
    await _rejects(session, "fk_feed_posts_deed_id_deeds", ghost)


# --- likes, saves, shares, follows, notices -----------------------------------------------------


async def test_one_like_and_one_save_per_user(session: AsyncSession) -> None:
    _, _, post = await _published(session)
    (fan,) = await _users(session, 1)
    session.add_all(
        [
            FeedLike(deed_id=post.deed_id, user_id=fan.id),
            FeedSave(user_id=fan.id, deed_id=post.deed_id),
        ]
    )
    await session.flush()
    await _rejects(session, "pk_feed_likes", FeedLike(deed_id=post.deed_id, user_id=fan.id))
    await _rejects(session, "pk_feed_saves", FeedSave(user_id=fan.id, deed_id=post.deed_id))


async def test_likes_need_a_post(session: AsyncSession) -> None:
    (fan,) = await _users(session, 1)
    await _rejects(
        session,
        "fk_feed_likes_deed_id_feed_posts",
        FeedLike(deed_id=uuid.uuid4(), user_id=fan.id),
    )


async def test_share_target_is_checked(session: AsyncSession) -> None:
    _, _, post = await _published(session)
    share = FeedShare(deed_id=post.deed_id, user_id=None, target=ShareTarget.WHATSAPP)
    session.add(share)
    await session.flush()
    with pytest.raises(IntegrityError, match="ck_feed_shares_share_target"):
        async with session.begin_nested():
            await session.execute(
                text("UPDATE feed_shares SET target = 'fax' WHERE id = :id"), {"id": share.id}
            )


async def test_follows_are_unique_and_never_self(session: AsyncSession) -> None:
    a, b = await _users(session, 2)
    session.add(Follow(follower_id=a.id, followed_id=b.id))
    await session.flush()
    await _rejects(session, "pk_follows", Follow(follower_id=a.id, followed_id=b.id))
    await _rejects(session, "ck_follows_not_self", Follow(follower_id=a.id, followed_id=a.id))
    session.add(Follow(follower_id=b.id, followed_id=a.id))  # one-way: the reverse is separate
    await session.flush()


async def test_a_notice_is_seen_once(session: AsyncSession) -> None:
    (user,) = await _users(session, 1)
    session.add(FeedNoticeSeen(user_id=user.id, notice_key="public_feed_intro"))
    await session.flush()
    await _rejects(
        session,
        "pk_feed_notices_seen",
        FeedNoticeSeen(user_id=user.id, notice_key="public_feed_intro"),
    )


# --- comments -----------------------------------------------------------------------------------


# Placeholders in the parametrised cases, filled in by the test.
NOW = object()
AUTHOR = object()


def _comment(post: FeedPost, author: User, **overrides: Any) -> FeedComment:
    values: dict[str, Any] = {"deed_id": post.deed_id, "author_id": author.id, "body": "So kind!"}
    values.update(overrides)
    return FeedComment(**values)


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        ({"deleted_at": NOW}, "ck_feed_comments_deleted_by_set"),
        ({"deleted_by": AUTHOR}, "ck_feed_comments_deleted_by_set"),
        (
            {"deleted_at": NOW, "deleted_by": AUTHOR},
            "ck_feed_comments_deleted_role_set",
        ),
        ({"deleted_role": CommentDeletedRole.DOER}, "ck_feed_comments_deleted_role_set"),
        (
            {
                "deleted_at": NOW,
                "deleted_by": AUTHOR,
                "deleted_role": CommentDeletedRole.AUTHOR,
            },
            "ck_feed_comments_author_delete_erases",
        ),
    ],
)
async def test_comment_deletion_fields_go_together(
    session: AsyncSession, overrides: dict[str, Any], constraint: str
) -> None:
    _, _, post = await _published(session)
    (author,) = await _users(session, 1)
    resolved = {
        k: (_now() if v is NOW else author.id if v is AUTHOR else v) for k, v in overrides.items()
    }
    await _rejects(session, constraint, _comment(post, author, **resolved))


async def test_valid_comment_deletions(session: AsyncSession) -> None:
    reporter, doer, post = await _published(session)
    (author,) = await _users(session, 1)
    now = _now()
    session.add_all(
        [
            # The author's deletion erases the text at once.
            _comment(
                post,
                author,
                body=None,
                deleted_at=now,
                deleted_by=author.id,
                deleted_role=CommentDeletedRole.AUTHOR,
            ),
            # Doer, reporter and moderation deletions keep the text for the retention period.
            _comment(
                post,
                author,
                deleted_at=now,
                deleted_by=doer.id,
                deleted_role=CommentDeletedRole.DOER,
            ),
            _comment(
                post,
                author,
                deleted_at=now,
                deleted_by=reporter.id,
                deleted_role=CommentDeletedRole.REPORTER,
                removal_notice_seen_at=None,
            ),
            _comment(
                post,
                author,
                deleted_at=now,
                deleted_by=reporter.id,
                deleted_role=CommentDeletedRole.MODERATION,
                removal_notice_seen_at=now,
            ),
        ]
    )
    await session.flush()


def _comment_case(comment: FeedComment, **overrides: Any) -> CommentReportCase:
    values: dict[str, Any] = {
        "comment_id": comment.id,
        "deed_id": comment.deed_id,
        "status": ReportCaseStatus.OPEN,
        "priority": 1,
        "opened_at": _now(),
    }
    values.update(overrides)
    return CommentReportCase(**values)


async def _comment_with_case(
    session: AsyncSession,
) -> tuple[User, FeedComment, CommentReportCase]:
    _, _, post = await _published(session)
    (author,) = await _users(session, 1)
    comment = _comment(post, author)
    session.add(comment)
    await session.flush()
    case = _comment_case(comment)
    session.add(case)
    await session.flush()
    return author, comment, case


def test_comment_priorities_follow_q38() -> None:
    assert COMMENT_REPORT_PRIORITY == {
        CommentReportReason.THREAT_OR_HARASSMENT: 1,
        CommentReportReason.PERSONAL_INFO: 1,
        CommentReportReason.HATE: 2,
        CommentReportReason.SPAM: 3,
        CommentReportReason.OTHER: 3,
    }


async def test_one_open_case_per_comment(session: AsyncSession) -> None:
    author, comment, case = await _comment_with_case(session)
    await _rejects(session, "uq_comment_report_cases_open", _comment_case(comment))
    case.status = ReportCaseStatus.RESOLVED
    case.decision = CaseDecision.KEPT
    case.resolved_at = _now()
    case.decided_by = author.id
    await session.flush()
    session.add(_comment_case(comment))
    await session.flush()


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        ({"status": ReportCaseStatus.RESOLVED}, "ck_comment_report_cases_resolved_has_decision"),
        ({"decision": CaseDecision.REMOVED}, "ck_comment_report_cases_resolved_has_decision"),
        ({"priority": 0}, "ck_comment_report_cases_priority_range"),
        ({"priority": 4}, "ck_comment_report_cases_priority_range"),
        ({"report_count": -1}, "ck_comment_report_cases_report_count_non_negative"),
    ],
)
async def test_comment_case_checks(
    session: AsyncSession, overrides: dict[str, Any], constraint: str
) -> None:
    _, comment, _ = await _comment_with_case(session)
    await _rejects(
        session,
        constraint,
        _comment_case(comment, **{"status": ReportCaseStatus.CLOSED, **overrides}),
    )


async def test_one_comment_report_per_member(session: AsyncSession) -> None:
    _, comment, case = await _comment_with_case(session)
    (member,) = await _users(session, 1)

    def report() -> CommentReport:
        return CommentReport(
            case_id=case.id,
            comment_id=comment.id,
            reporter_user_id=member.id,
            reason=CommentReportReason.HATE,
        )

    session.add(report())
    await session.flush()
    await _rejects(session, "uq_comment_reports_case_reporter", report())


@pytest.mark.parametrize("note", ["", "abcd", "   ab   "])
async def test_comment_decision_note_needs_five_characters(
    session: AsyncSession, note: str
) -> None:
    author, comment, _ = await _comment_with_case(session)
    await _rejects(
        session,
        "ck_comment_decisions_note_min_length",
        CommentDecision(
            comment_id=comment.id,
            action=CommentDecisionAction.KEEP,
            note=note,
            actor_user_id=author.id,
        ),
    )


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE comment_decisions SET note = 'changed note' WHERE id = :id",
        "DELETE FROM comment_decisions WHERE id = :id",
    ],
)
async def test_comment_decisions_are_append_only(session: AsyncSession, statement: str) -> None:
    author, comment, case = await _comment_with_case(session)
    decision = CommentDecision(
        comment_id=comment.id,
        case_id=case.id,
        action=CommentDecisionAction.REMOVE,
        note="Harassment of the doer",
        actor_user_id=author.id,
    )
    session.add(decision)
    await session.flush()
    with pytest.raises(DBAPIError, match="append-only"):
        async with session.begin_nested():
            await session.execute(text(statement), {"id": decision.id})


# --- Top snapshots ------------------------------------------------------------------------------


async def test_top_snapshot_period_and_entries(session: AsyncSession) -> None:
    _, _, post = await _published(session)
    now = _now()
    snapshot = FeedTopSnapshot(
        period=TopPeriod.WEEK,
        computed_at=now,
        window_start=now - timedelta(days=7),
        weights={"like": 1, "comment": 3},
    )
    session.add(snapshot)
    await session.flush()
    entry = FeedTopEntry(snapshot_id=snapshot.id, rank=1, deed_id=post.deed_id, score=5)
    session.add(entry)
    await session.flush()
    with pytest.raises(IntegrityError, match="pk_feed_top_entries"):
        async with session.begin_nested():
            await session.execute(
                text(
                    "INSERT INTO feed_top_entries (snapshot_id, rank, deed_id, score) "
                    "VALUES (:s, 1, :d, 4)"
                ),
                {"s": snapshot.id, "d": post.deed_id},
            )
    await _rejects(
        session,
        "uq_feed_top_entries_snapshot_deed",
        FeedTopEntry(snapshot_id=snapshot.id, rank=2, deed_id=post.deed_id, score=4),
    )
    await _rejects(
        session,
        "ck_feed_top_entries_rank_positive",
        FeedTopEntry(snapshot_id=snapshot.id, rank=0, deed_id=post.deed_id, score=4),
    )
    with pytest.raises(IntegrityError, match="ck_feed_top_snapshots_top_period"):
        async with session.begin_nested():
            await session.execute(
                text("UPDATE feed_top_snapshots SET period = 'day' WHERE id = :id"),
                {"id": snapshot.id},
            )
    # Pruning a snapshot removes its entries.
    await session.execute(
        text("DELETE FROM feed_top_snapshots WHERE id = :id"), {"id": snapshot.id}
    )
    left = await session.scalar(
        select(FeedTopEntry.rank).where(FeedTopEntry.snapshot_id == snapshot.id)
    )
    assert left is None


# --- backfill -----------------------------------------------------------------------------------


@pytest.fixture
def sync_conn(migrated_db: None) -> Iterator[Connection]:
    """A sync connection in a transaction that is always rolled back."""
    engine = create_engine(get_settings().database_url)
    try:
        with engine.connect() as conn:
            trans = conn.begin()
            try:
                yield conn
            finally:
                trans.rollback()
    finally:
        engine.dispose()


def _insert_user(conn: Connection) -> uuid.UUID:
    user_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO users (id, display_name, handle, city, role, status) "
            "VALUES (:id, 'Feed Test', :h, 'Bangalore', 'member', 'active')"
        ),
        {"id": user_id, "h": f"fd{user_id.hex[:12]}"},
    )
    return user_id


def _insert_deed(
    conn: Connection, reporter: uuid.UUID, doer: uuid.UUID, *, verified: bool
) -> uuid.UUID:
    """A QR deed (verified) or a pending invite deed, written with plain SQL."""
    now = _now()
    deed_id = uuid.uuid4()
    point = "SRID=4326;POINT(77.5946 12.9716)"
    if not verified:
        conn.execute(
            text(
                "INSERT INTO deeds (id, reporter_user_id, category, description, occurred_at, "
                "report_day, status, verification_method, limits_version) VALUES (:id, :r, "
                "'animals', 'Fed a stray dog', :at, :day, 'pending', 'invite_accept', 1)"
            ),
            {"id": deed_id, "r": reporter, "at": now, "day": now.date()},
        )
        return deed_id
    scan_id, group_id = uuid.uuid4(), uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO qr_scans (id, witness_user_id, doer_user_id, scan_method, token_window, "
            "scanned_at, scan_day, location, accuracy_m, expires_at, limits_version) VALUES "
            "(:id, :r, :d, 'qr', 1, :at, :day, CAST(:p AS geography), 30, :at, 1)"
        ),
        {"id": scan_id, "r": reporter, "d": doer, "at": now, "day": now.date(), "p": point},
    )
    conn.execute(
        text(
            "INSERT INTO same_act_groups (id, doer_user_id, centre, anchor_at, radius_m, "
            "window_minutes, cap, limits_version) VALUES "
            "(:id, :d, CAST(:p AS geography), :at, 200, 120, 5, 1)"
        ),
        {"id": group_id, "d": doer, "at": now, "p": point},
    )
    conn.execute(
        text(
            "INSERT INTO deeds (id, reporter_user_id, doer_user_id, category, description, "
            "location, occurred_at, report_day, status, verification_method, verified_at, "
            "scan_id, same_act_group_id, limits_version) VALUES (:id, :r, :d, 'animals', "
            "'Fed a stray dog', CAST(:p AS geography), :at, :day, 'verified', 'qr_scan', :at, "
            ":scan, :group, 1)"
        ),
        {
            "id": deed_id,
            "r": reporter,
            "d": doer,
            "p": point,
            "at": now,
            "day": now.date(),
            "scan": scan_id,
            "group": group_id,
        },
    )
    return deed_id


def test_backfill_publishes_verified_deeds_only(sync_conn: Connection) -> None:
    path = BACKEND_DIR / "alembic" / "versions" / "2026_11_19_0200-0015_feed.py"
    spec = importlib.util.spec_from_file_location("migration_0015", path)
    assert spec is not None and spec.loader is not None
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)

    reporter, doer = _insert_user(sync_conn), _insert_user(sync_conn)
    verified = _insert_deed(sync_conn, reporter, doer, verified=True)
    pending = _insert_deed(sync_conn, reporter, doer, verified=False)

    with Operations.context(MigrationContext.configure(sync_conn)):
        m.backfill_feed_posts()
        m.backfill_feed_posts()  # safe to re-run

    def count(where: str, **params: Any) -> int:
        return int(sync_conn.execute(text(f"SELECT count(*) FROM {where}"), params).scalar_one())

    assert count("feed_posts WHERE deed_id = :id", id=pending) == 0
    row = sync_conn.execute(
        text(
            "SELECT p.state, p.listed, p.published_at = d.verified_at, p.doer_id, p.reporter_id, "
            "p.like_count, p.top_like_count, p.comment_count "
            "FROM feed_posts AS p JOIN deeds AS d ON d.id = p.deed_id WHERE p.deed_id = :id"
        ),
        {"id": verified},
    ).one()
    assert tuple(row) == ("published", True, True, doer, reporter, 0, 0, 0)
    # Every verified deed has a post, and no other deed has one from the backfill.
    assert (
        count(
            "deeds AS d WHERE d.status = 'verified' "
            "AND NOT EXISTS (SELECT 1 FROM feed_posts AS p WHERE p.deed_id = d.id)"
        )
        == 0
    )
