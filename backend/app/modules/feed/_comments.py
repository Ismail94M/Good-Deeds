"""Comments (docs/M3-DESIGN.md §3.4 #5, #6, #7; §2.3 text retention, Q38).

- List: live comments, oldest first, on a deed the caller may see.
- Post: trimmed; empty, too long, unkind, a link or a phone number is refused (422) with a message
  that never names the matched word; ``comment_count += 1``; ``CommentPosted``.
- Delete: the author, or the deed's doer or reporter (anyone else 404, moderators and admins
  included: they remove comments only through a report decision, B27). An author's deletion
  erases the text at once; a doer's or reporter's keeps it for ``deleted_comment_retention_days``
  (moderators only), then ``erase_deleted_batch`` erases it. ``comment_count`` drops once, even if
  deleted twice. An open report case on the comment is closed.
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import advisory_xact_lock
from app.core.errors import AppError, NotFound
from app.core.events import emit
from app.modules.feed import _access, _cursor, _kindness
from app.modules.feed._limits import FeedLimits
from app.modules.feed.events import CommentPosted
from app.modules.feed.models import (
    CommentDeletedRole,
    CommentReportCase,
    FeedComment,
    FeedPost,
    ReportCaseStatus,
)
from app.modules.feed.schemas import CommentOut, CommentPage, UserRef
from app.modules.identity import service as identity

ERASE_BATCH_SIZE = 500


class Unprocessable(AppError):
    status_code = 422


_PROBLEMS: dict[str, tuple[str, str]] = {
    "not_kind": (
        "Please keep it kind. Your comment includes a word we don't allow. Try rewording it.",
        "comment_not_kind",
    ),
    "link": ("Comments can't include links.", "comment_has_link"),
    "phone": ("Comments can't include phone numbers.", "comment_has_phone"),
}


def comment_not_found() -> NotFound:
    return NotFound("We couldn't find that comment.", code="comment_not_found")


async def _outs(
    session: AsyncSession, comments: list[FeedComment], viewer_id: uuid.UUID, post: FeedPost
) -> list[CommentOut]:
    names = await identity.public_summaries(session, {c.author_id for c in comments})
    moderates = viewer_id in (post.doer_id, post.reporter_id)
    out: list[CommentOut] = []
    for c in comments:
        author = names.get(c.author_id)
        if author is None or c.body is None:
            continue
        mine = c.author_id == viewer_id
        out.append(
            CommentOut(
                id=c.id,
                author=UserRef(**author.model_dump()),
                body=c.body,
                created_at=c.created_at,
                mine=mine,
                can_delete=mine or moderates,
            )
        )
    return out


async def list_comments(
    session: AsyncSession,
    deed_id: uuid.UUID,
    viewer_id: uuid.UUID,
    *,
    cursor: str | None = None,
    limit: int = _cursor.DEFAULT_PAGE_SIZE,
) -> CommentPage:
    found = await _access.viewable(session, deed_id, viewer_id)
    stmt = select(FeedComment).where(
        FeedComment.deed_id == deed_id, FeedComment.deleted_at.is_(None)
    )
    if cursor is not None:
        at, last_id = _cursor.decode(cursor)
        stmt = stmt.where(tuple_(FeedComment.created_at, FeedComment.id) > tuple_(at, last_id))
    rows = list(
        await session.scalars(
            stmt.order_by(FeedComment.created_at, FeedComment.id).limit(limit + 1)
        )
    )
    page, more = rows[:limit], len(rows) > limit
    return CommentPage(
        items=await _outs(session, page, viewer_id, found.post),
        next_cursor=_cursor.encode(page[-1].created_at, page[-1].id) if more else None,
    )


def validate_body(body: str, limits: FeedLimits) -> str:
    text = body.strip()
    if not text:
        raise Unprocessable("Please write a comment first.", code="comment_empty")
    if len(text) > limits.comment_max_chars:
        raise Unprocessable(
            f"Comments can be at most {limits.comment_max_chars} characters.",
            code="comment_too_long",
        )
    problem = _kindness.check_comment(text, limits)
    if problem is not None:
        detail, code = _PROBLEMS[problem]
        raise Unprocessable(detail, code=code)
    return text


async def post_comment(
    session: AsyncSession,
    deed_id: uuid.UUID,
    author_id: uuid.UUID,
    body: str,
    *,
    limits: FeedLimits,
    now: datetime | None = None,
) -> CommentOut:
    now = now or datetime.now(UTC)
    found = await _access.viewable(session, deed_id, author_id)
    text = validate_body(body, limits)
    comment = FeedComment(deed_id=deed_id, author_id=author_id, body=text, created_at=now)
    session.add(comment)
    await session.flush()
    await session.execute(
        update(FeedPost)
        .where(FeedPost.deed_id == deed_id)
        .values(comment_count=FeedPost.comment_count + 1)
        .execution_options(synchronize_session=False)
    )
    emit(session, CommentPosted(deed_id=deed_id, comment_id=comment.id, author_id=author_id))
    return (await _outs(session, [comment], author_id, found.post))[0]


async def close_open_report_cases(
    session: AsyncSession, comment_id: uuid.UUID, now: datetime
) -> None:
    """A comment deleted by its author, doer or reporter needs no moderation decision any more
    (B27 owns reports; this is the hook it relies on)."""
    await session.execute(
        update(CommentReportCase)
        .where(
            CommentReportCase.comment_id == comment_id,
            CommentReportCase.status == ReportCaseStatus.OPEN,
        )
        .values(status=ReportCaseStatus.CLOSED, resolved_at=now)
        .execution_options(synchronize_session=False)
    )


async def delete_comment(
    session: AsyncSession,
    comment_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    now: datetime | None = None,
) -> None:
    now = now or datetime.now(UTC)
    # The lock comment reports and moderation decisions take first, so the case closing below
    # can't interleave with a report opening a case or a decision on it.
    await advisory_xact_lock(session, "comment_case", str(comment_id))
    comment = (
        await session.scalars(
            select(FeedComment)
            .where(FeedComment.id == comment_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).first()
    if comment is None:
        raise comment_not_found()
    post = await session.get(FeedPost, comment.deed_id)
    assert post is not None  # FK
    if user_id == comment.author_id:
        role = CommentDeletedRole.AUTHOR
    elif user_id == post.doer_id:
        role = CommentDeletedRole.DOER
    elif user_id == post.reporter_id:
        role = CommentDeletedRole.REPORTER
    else:
        raise comment_not_found()
    if comment.deleted_at is not None:
        # Deleting twice is fine; an author may still erase text a doer or reporter deleted.
        if role == CommentDeletedRole.AUTHOR and comment.body is not None:
            comment.body = None  # who deleted it first stays recorded
            await session.flush()
        return
    comment.deleted_at = now
    comment.deleted_by = user_id
    comment.deleted_role = role
    if role == CommentDeletedRole.AUTHOR:
        comment.body = None
    await session.flush()
    await session.execute(
        update(FeedPost)
        .where(FeedPost.deed_id == comment.deed_id)
        .values(comment_count=FeedPost.comment_count - 1)
        .execution_options(synchronize_session=False)
    )
    await close_open_report_cases(session, comment.id, now)


async def erase_deleted_batch(
    session: AsyncSession, limits: FeedLimits, *, now: datetime | None = None
) -> int:
    """Erase the text of comments deleted more than ``deleted_comment_retention_days`` ago (by
    the doer, the reporter or moderation). One batch; returns how many were erased."""
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(days=limits.deleted_comment_retention_days)
    due = (
        select(FeedComment.id)
        .where(
            FeedComment.body.is_not(None),
            FeedComment.deleted_at.is_not(None),
            FeedComment.deleted_at < cutoff,
        )
        .limit(ERASE_BATCH_SIZE)
        .with_for_update(skip_locked=True)
        .scalar_subquery()
    )
    result = await session.execute(
        update(FeedComment)
        .where(FeedComment.id.in_(due))
        .values(body=None)
        .returning(FeedComment.id)
        .execution_options(synchronize_session=False)
    )
    return len(result.all())
