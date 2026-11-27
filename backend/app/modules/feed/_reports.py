"""Comment reports by members and the author's "comment removed" notices (docs/M3-DESIGN.md
§3.4a #13 and the notice follow-up, §3.4b #1, #2, D45, Q38).

A report **never hides the comment** and never tells its author. The comment must be live, on a deed
the caller may see, and not the caller's own; anything else is 404 ``comment_not_found`` with one
body. A report opens the comment's open case or joins it; the case's priority is the most serious
reason among its reports and never goes down. A duplicate (same member, same case) gets the same
202 and adds nothing.

Notices: the caller's comments removed by moderation within ``report_outcome_display_days`` and
not dismissed, oldest first. They never name the reporter, the moderator or the note. A restored
comment is no longer removed, so it drops out.
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import advisory_xact_lock
from app.core.errors import AppError
from app.modules.feed import _access
from app.modules.feed._comments import comment_not_found
from app.modules.feed._shared_limits import SharedReportLimits
from app.modules.feed.models import (
    COMMENT_REPORT_PRIORITY,
    CommentDeletedRole,
    CommentReport,
    CommentReportCase,
    CommentReportReason,
    FeedComment,
    FeedPost,
    ReportCaseStatus,
)
from app.modules.feed.schemas import CommentNotice, UserRef
from app.modules.identity import service as identity

EXCERPT_CHARS = 80


class Unprocessable(AppError):
    status_code = 422


def excerpt(body: str | None) -> str | None:
    return None if body is None else body[:EXCERPT_CHARS]


def parse_reason(reason: str) -> CommentReportReason:
    try:
        return CommentReportReason(reason)
    except ValueError as exc:
        raise Unprocessable("Please choose a reason from the list.", code="invalid_reason") from exc


async def report_comment(
    session: AsyncSession,
    comment_id: uuid.UUID,
    reporter_id: uuid.UUID,
    reason: str,
    details: str | None,
    *,
    shared: SharedReportLimits,
    now: datetime | None = None,
) -> None:
    now = now or datetime.now(UTC)
    why = parse_reason(reason)
    text = (details or "").strip() or None
    if text is not None and len(text) > shared.report_details_max:
        raise Unprocessable(
            f"Details can be at most {shared.report_details_max} characters.",
            code="details_too_long",
        )
    comment = await session.get(FeedComment, comment_id)
    if comment is None or comment.deleted_at is not None or comment.author_id == reporter_id:
        raise comment_not_found()
    found = await _access.access(session, comment.deed_id, reporter_id)
    if found is None or not found.viewable:
        raise comment_not_found()

    # One open case per comment: serialise reports of the same comment. The author, doer and
    # reporter delete under the same lock, so re-read the comment now that it's held: a deletion
    # that won the race must not be left with an open case.
    await advisory_xact_lock(session, "comment_case", str(comment_id))
    deleted_at = await session.scalar(
        select(FeedComment.deleted_at)
        .where(FeedComment.id == comment_id)
        .execution_options(populate_existing=True)
    )
    if deleted_at is not None:
        raise comment_not_found()
    priority = COMMENT_REPORT_PRIORITY[why]
    case = await session.scalar(
        select(CommentReportCase)
        .where(
            CommentReportCase.comment_id == comment_id,
            CommentReportCase.status == ReportCaseStatus.OPEN,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if case is None:
        case = CommentReportCase(
            comment_id=comment_id,
            deed_id=comment.deed_id,
            status=ReportCaseStatus.OPEN,
            priority=priority,
            report_count=0,
            opened_at=now,
        )
        session.add(case)
        await session.flush()
    inserted = await session.scalar(
        insert(CommentReport)
        .values(
            case_id=case.id,
            comment_id=comment_id,
            reporter_user_id=reporter_id,
            reason=why,
            details=text,
            created_at=now,
        )
        .on_conflict_do_nothing(constraint="uq_comment_reports_case_reporter")
        .returning(CommentReport.id)
    )
    if inserted is not None:
        case.report_count += 1
        case.priority = min(case.priority, priority)  # never lowers the seriousness
    await session.flush()


# --------------------------------------------------- the author's notices


async def comment_notices(
    session: AsyncSession,
    author_id: uuid.UUID,
    *,
    shared: SharedReportLimits,
    now: datetime | None = None,
) -> list[CommentNotice]:
    now = now or datetime.now(UTC)
    since = now - timedelta(days=shared.report_outcome_display_days)
    rows = (
        await session.execute(
            select(FeedComment, FeedPost.doer_id)
            .join(FeedPost, FeedPost.deed_id == FeedComment.deed_id)
            .where(
                FeedComment.author_id == author_id,
                FeedComment.deleted_role == CommentDeletedRole.MODERATION,
                FeedComment.deleted_at >= since,
                FeedComment.removal_notice_seen_at.is_(None),
            )
            .order_by(FeedComment.deleted_at, FeedComment.id)
        )
    ).all()
    names = await identity.public_summaries(session, {doer_id for _, doer_id in rows})
    out: list[CommentNotice] = []
    for comment, doer_id in rows:
        doer = names.get(doer_id)
        if doer is None or comment.deleted_at is None:
            continue
        out.append(
            CommentNotice(
                comment_id=comment.id,
                deed_id=comment.deed_id,
                doer=UserRef.of(doer),
                excerpt=excerpt(comment.body),
                removed_at=comment.deleted_at,
            )
        )
    return out


async def mark_notice_seen(
    session: AsyncSession,
    author_id: uuid.UUID,
    comment_id: uuid.UUID,
    *,
    now: datetime | None = None,
) -> None:
    """Idempotent. Someone else's (or a missing) comment is 404."""
    owner = await session.scalar(select(FeedComment.author_id).where(FeedComment.id == comment_id))
    if owner is None or owner != author_id:
        raise comment_not_found()
    await session.execute(
        update(FeedComment)
        .where(FeedComment.id == comment_id, FeedComment.removal_notice_seen_at.is_(None))
        .values(removal_notice_seen_at=now or datetime.now(UTC))
        .execution_options(synchronize_session=False)
    )
