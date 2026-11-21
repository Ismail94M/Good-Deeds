"""Moderator and admin review of comment reports (docs/M3-DESIGN.md §3.4a #14 to #17, §3.4b #5,
#6, D45, Q34, Q38). Role checks are the router's (``identity.service.ReviewerUser``).

- Queue: open cases by priority then oldest; resolved ones newest first.
- Decide (``keep`` / ``remove``) only an open case, with a note of 5 to ``decision_note_max``
  characters. ``remove`` deletes the comment for everyone (``deleted_role = 'moderation'``,
  ``comment_count - 1``, and ``top_comment_count - 1`` unless the author is the doer or reporter,
  L1) and keeps its text for ``deleted_comment_retention_days``; the author's own delete no longer
  erases it (L3).
- Restore only a moderation removal whose text is still kept, within that period. If the author
  deleted the comment after the removal (``author_deleted_at``), the restore undoes only the
  moderation: the comment stays deleted as the author's own, its text erased, counts unchanged
  (L3).
- Conflict of interest: the comment's author and the deed's doer and reporter can't decide or
  restore (403 ``conflict_of_interest``).
- Every decision writes one append-only ``comment_decisions`` row, one ``audit_log`` row and one
  ``CommentModerationDecided``.
"""

import uuid
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import ColumnElement, func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit
from app.core.db import advisory_xact_lock
from app.core.errors import AppError, Conflict, Forbidden, NotFound
from app.core.events import emit
from app.modules.deeds import service as deeds
from app.modules.feed import _cursor
from app.modules.feed._comments import change_counts, comment_not_found
from app.modules.feed._limits import FeedLimits
from app.modules.feed._reports import EXCERPT_CHARS
from app.modules.feed._shared_limits import SharedReportLimits
from app.modules.feed.events import CommentModerationDecided
from app.modules.feed.models import (
    COMMENT_REPORT_PRIORITY,
    CaseDecision,
    CommentDecision,
    CommentDecisionAction,
    CommentDeletedRole,
    CommentReport,
    CommentReportCase,
    FeedComment,
    FeedPost,
    ReportCaseStatus,
)
from app.modules.feed.schemas import (
    AdminCommentDeed,
    AdminCommentOut,
    AdminDeedRef,
    CommentCaseDetail,
    CommentCaseItem,
    CommentCaseOut,
    CommentCasePage,
    CommentReportEntry,
    DecisionRecord,
    MemberReporterRef,
    UserRef,
)
from app.modules.identity import service as identity

Queue = Literal["open", "resolved"]
# The review page's "removed by moderation in the last 90 days" pattern hint (§3.4a #15).
AUTHOR_HISTORY_DAYS = 90


class Unprocessable(AppError):
    status_code = 422


def case_not_found() -> NotFound:
    return NotFound("Report not found.", code="case_not_found")


def _note(note: str, shared: SharedReportLimits) -> str:
    text = note.strip()
    if len(text) < 5:
        raise Unprocessable("Add a note of at least 5 characters.", code="note_required")
    if len(text) > shared.decision_note_max:
        raise Unprocessable("The note is too long.", code="note_too_long")
    return text


def _refs(summaries: Mapping[uuid.UUID, BaseModel]) -> dict[uuid.UUID, UserRef]:
    return {uid: UserRef(**s.model_dump()) for uid, s in summaries.items()}


def _top_reason(reports: list[CommentReport]) -> str:
    """The most serious reason, the earliest report among equals."""
    if not reports:
        return "other"
    best = min(reports, key=lambda r: (COMMENT_REPORT_PRIORITY[r.reason], r.created_at, r.id))
    return best.reason.value


def _case_out(case: CommentReportCase, reports: list[CommentReport]) -> CommentCaseOut:
    return CommentCaseOut(
        case_id=case.id,
        status=case.status.value,
        priority=case.priority,
        top_reason=_top_reason(reports),
        report_count=case.report_count,
        opened_at=case.opened_at,
        resolved_at=case.resolved_at,
        decision=case.decision.value if case.decision else None,
    )


async def _reports_by_case(
    session: AsyncSession, case_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[CommentReport]]:
    out: dict[uuid.UUID, list[CommentReport]] = {cid: [] for cid in case_ids}
    if case_ids:
        rows = await session.scalars(
            select(CommentReport)
            .where(CommentReport.case_id.in_(case_ids))
            .order_by(CommentReport.created_at, CommentReport.id)
        )
        for r in rows:
            out[r.case_id].append(r)
    return out


# --------------------------------------------------- #14 queue


async def list_cases(
    session: AsyncSession,
    viewer_id: uuid.UUID,
    *,
    queue: Queue,
    cursor: str | None = None,
    limit: int = _cursor.DEFAULT_PAGE_SIZE,
) -> CommentCasePage:
    stmt = (
        select(CommentReportCase)
        .join(FeedComment, FeedComment.id == CommentReportCase.comment_id)
        .join(FeedPost, FeedPost.deed_id == CommentReportCase.deed_id)
        .where(*_not_party(viewer_id))
    )
    if queue == "open":
        stmt = stmt.where(CommentReportCase.status == ReportCaseStatus.OPEN)
        if cursor:
            priority, at, last_id = _cursor.decode_queue(cursor, queue)
            stmt = stmt.where(
                tuple_(
                    CommentReportCase.priority, CommentReportCase.opened_at, CommentReportCase.id
                )
                > tuple_(priority, at, last_id)
            )
        stmt = stmt.order_by(
            CommentReportCase.priority, CommentReportCase.opened_at, CommentReportCase.id
        )
    else:
        stmt = stmt.where(CommentReportCase.status == ReportCaseStatus.RESOLVED)
        if cursor:
            _, at, last_id = _cursor.decode_queue(cursor, queue)
            stmt = stmt.where(
                tuple_(CommentReportCase.resolved_at, CommentReportCase.id) < tuple_(at, last_id)
            )
        stmt = stmt.order_by(CommentReportCase.resolved_at.desc(), CommentReportCase.id.desc())
    cases = list(await session.scalars(stmt.limit(limit + 1)))
    more, cases = len(cases) > limit, cases[:limit]

    counts = {
        status: int(n)
        for status, n in await session.execute(
            select(CommentReportCase.status, func.count())
            .join(FeedComment, FeedComment.id == CommentReportCase.comment_id)
            .join(FeedPost, FeedPost.deed_id == CommentReportCase.deed_id)
            .where(
                CommentReportCase.status.in_((ReportCaseStatus.OPEN, ReportCaseStatus.RESOLVED)),
                *_not_party(viewer_id),
            )
            .group_by(CommentReportCase.status)
        )
    }
    reports = await _reports_by_case(session, [c.id for c in cases])
    comments = {
        c.id: c
        for c in await session.scalars(
            select(FeedComment).where(FeedComment.id.in_([c.comment_id for c in cases]))
        )
    }
    views = await deeds.get_deeds(session, {c.deed_id for c in cases})
    people = _refs(
        await identity.public_summaries(
            session,
            {c.author_id for c in comments.values()}
            | {v.doer_id for v in views.values() if v.doer_id is not None},
        )
    )
    items: list[CommentCaseItem] = []
    for case in cases:
        comment = comments[case.comment_id]
        view = views.get(case.deed_id)
        case_reports = reports[case.id]
        items.append(
            CommentCaseItem(
                **_case_out(case, case_reports).model_dump(),
                oldest_report_at=case_reports[0].created_at if case_reports else case.opened_at,
                comment_excerpt=(comment.body or "")[:EXCERPT_CHARS],
                author=people.get(comment.author_id),
                deed=AdminDeedRef(
                    doer=people.get(view.doer_id) if view and view.doer_id else None,
                    category=view.category if view else "",
                    area_name=deeds.area_name(view) if view else deeds.DEFAULT_AREA_NAME,
                ),
            )
        )
    next_cursor = None
    if more and cases:
        last = cases[-1]
        key_at = last.opened_at if queue == "open" else last.resolved_at
        assert key_at is not None
        next_cursor = _cursor.encode_queue(queue, last.priority, key_at, last.id)
    return CommentCasePage(
        items=items,
        next_cursor=next_cursor,
        open_count=counts.get(ReportCaseStatus.OPEN, 0),
        resolved_count=counts.get(ReportCaseStatus.RESOLVED, 0),
    )


# --------------------------------------------------- #15 review


def _restorable_until(comment: FeedComment, limits: FeedLimits) -> datetime | None:
    if (
        comment.deleted_role != CommentDeletedRole.MODERATION
        or comment.deleted_at is None
        or comment.body is None
    ):
        return None
    return comment.deleted_at + timedelta(days=limits.deleted_comment_retention_days)


async def case_detail(
    session: AsyncSession,
    case_id: uuid.UUID,
    viewer_id: uuid.UUID,
    *,
    limits: FeedLimits,
    now: datetime | None = None,
) -> CommentCaseDetail:
    now = now or datetime.now(UTC)
    case = await session.scalar(
        select(CommentReportCase)
        .where(CommentReportCase.id == case_id)
        .execution_options(populate_existing=True)
    )
    if case is None:
        raise case_not_found()
    comment = await session.scalar(
        select(FeedComment)
        .where(FeedComment.id == case.comment_id)
        .execution_options(populate_existing=True)
    )
    post = await session.scalar(
        select(FeedPost)
        .where(FeedPost.deed_id == case.deed_id)
        .execution_options(populate_existing=True)
    )
    assert comment is not None and post is not None  # FKs
    reports = (await _reports_by_case(session, [case.id]))[case.id]
    # Parties to the case (author, doer, reporter, a filer) never see it (security review M1).
    parties = {comment.author_id, post.doer_id, post.reporter_id}
    if viewer_id in parties | {r.reporter_user_id for r in reports}:
        raise case_not_found()
    view = await deeds.get_deed(session, case.deed_id)
    decisions = list(
        await session.scalars(
            select(CommentDecision)
            .where(CommentDecision.comment_id == comment.id)
            .order_by(CommentDecision.created_at, CommentDecision.id)
        )
    )
    people = _refs(
        await identity.public_summaries(
            session,
            {comment.author_id, post.doer_id, post.reporter_id}
            | {r.reporter_user_id for r in reports}
            | {d.actor_user_id for d in decisions},
        )
    )
    removed_recently = await session.scalar(
        select(func.count())
        .select_from(FeedComment)
        .where(
            FeedComment.author_id == comment.author_id,
            FeedComment.deleted_role == CommentDeletedRole.MODERATION,
            FeedComment.deleted_at >= now - timedelta(days=AUTHOR_HISTORY_DAYS),
        )
    )
    if comment.deleted_at is None:
        state: Literal["visible", "removed", "deleted"] = "visible"
    elif comment.deleted_role == CommentDeletedRole.MODERATION:
        state = "removed"
    else:
        state = "deleted"
    return CommentCaseDetail(
        case=_case_out(case, reports),
        comment=AdminCommentOut(
            id=comment.id,
            body=comment.body,
            author=people.get(comment.author_id),
            created_at=comment.created_at,
            state=state,
            restorable_until=_restorable_until(comment, limits),
        ),
        deed=AdminCommentDeed(
            doer=people.get(post.doer_id),
            reporter=people.get(post.reporter_id),
            category=view.category if view else "",
            area_name=deeds.area_name(view) if view else deeds.DEFAULT_AREA_NAME,
            listed=post.listed,
        ),
        reports=[
            CommentReportEntry(
                reason=r.reason.value,
                details=r.details,
                reporter=MemberReporterRef(
                    handle=people[r.reporter_user_id].handle if r.reporter_user_id in people else ""
                ),
                created_at=r.created_at,
            )
            for r in reports
        ],
        decisions=[
            DecisionRecord(
                action=d.action.value,
                note=d.note,
                actor=people.get(d.actor_user_id),
                created_at=d.created_at,
            )
            for d in decisions
        ],
        author_removed_last_90_days=int(removed_recently or 0),
    )


# --------------------------------------------------- #16 decide, #17 restore


def _not_party(viewer_id: uuid.UUID) -> list[ColumnElement[bool]]:
    """SQL conditions for a queue: the viewer is not the comment's author, the deed's doer or
    reporter, or a filer of a report in the case (security review M1). Needs ``FeedComment`` and
    ``FeedPost`` joined to ``CommentReportCase``."""
    filed = (
        select(CommentReport.id)
        .where(
            CommentReport.case_id == CommentReportCase.id,
            CommentReport.reporter_user_id == viewer_id,
        )
        .exists()
    )
    return [
        FeedComment.author_id != viewer_id,
        FeedPost.doer_id != viewer_id,
        FeedPost.reporter_id != viewer_id,
        ~filed,
    ]


async def _filers(session: AsyncSession, *case_ids: uuid.UUID) -> set[uuid.UUID]:
    return set(
        await session.scalars(
            select(CommentReport.reporter_user_id).where(CommentReport.case_id.in_(case_ids))
        )
    )


def _check_conflict(
    actor_id: uuid.UUID, comment: FeedComment, post: FeedPost, filers: set[uuid.UUID]
) -> None:
    if actor_id in (comment.author_id, post.doer_id, post.reporter_id) or actor_id in filers:
        raise Forbidden(
            "Another moderator needs to review this one: you're part of this deed.",
            code="conflict_of_interest",
        )


async def _locked(session: AsyncSession, comment_id: uuid.UUID) -> FeedComment | None:
    return await session.scalar(
        select(FeedComment)
        .where(FeedComment.id == comment_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def _record(
    session: AsyncSession,
    comment: FeedComment,
    *,
    case_id: uuid.UUID | None,
    action: CommentDecisionAction,
    note: str,
    actor_id: uuid.UUID,
    now: datetime,
) -> None:
    session.add(
        CommentDecision(
            comment_id=comment.id,
            case_id=case_id,
            action=action,
            note=note,
            actor_user_id=actor_id,
            created_at=now,
        )
    )
    verb = {"keep": "kept", "remove": "removed", "restore": "restored"}[action.value]
    record_audit(
        session,
        entity_type="comment",
        entity_id=str(comment.id),
        action=f"comment.{verb}",
        actor_id=actor_id,
        new_value={"case_id": str(case_id) if case_id else None, "deed_id": str(comment.deed_id)},
        reason=note,
    )
    emit(
        session,
        CommentModerationDecided(
            comment_id=comment.id,
            case_id=case_id,
            deed_id=comment.deed_id,
            author_id=comment.author_id,
            action=action.value,
        ),
    )


async def decide(
    session: AsyncSession,
    actor_id: uuid.UUID,
    case_id: uuid.UUID,
    action: Literal["keep", "remove"],
    note: str,
    *,
    shared: SharedReportLimits,
    now: datetime | None = None,
) -> None:
    now = now or datetime.now(UTC)
    text = _note(note, shared)
    comment_id = await session.scalar(
        select(CommentReportCase.comment_id).where(CommentReportCase.id == case_id)
    )
    if comment_id is None:
        raise case_not_found()
    # The same lock as reports, then the comment row (as delete does), then the case.
    await advisory_xact_lock(session, "comment_case", str(comment_id))
    comment = await _locked(session, comment_id)
    case = await session.scalar(
        select(CommentReportCase)
        .where(CommentReportCase.id == case_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    assert comment is not None and case is not None
    post = await session.get(FeedPost, comment.deed_id)
    assert post is not None
    if case.status != ReportCaseStatus.OPEN:
        raise Conflict("This report has already been decided.", code="case_not_open")
    _check_conflict(actor_id, comment, post, await _filers(session, case.id))
    if action == "remove":
        if comment.deleted_at is None:
            comment.deleted_at = now
            comment.deleted_by = actor_id
            comment.deleted_role = CommentDeletedRole.MODERATION
            comment.removal_notice_seen_at = None
            await session.flush()
            await change_counts(session, comment.deed_id, comment.author_id, -1)
        case.decision = CaseDecision.REMOVED
    else:
        case.decision = CaseDecision.KEPT
    case.status = ReportCaseStatus.RESOLVED
    case.resolved_at = now
    case.decided_by = actor_id
    _record(
        session,
        comment,
        case_id=case.id,
        action=CommentDecisionAction(action),
        note=text,
        actor_id=actor_id,
        now=now,
    )
    await session.flush()


async def restore(
    session: AsyncSession,
    actor_id: uuid.UUID,
    comment_id: uuid.UUID,
    note: str,
    *,
    limits: FeedLimits,
    shared: SharedReportLimits,
    now: datetime | None = None,
) -> uuid.UUID:
    """Undo a moderation removal while its text is kept. Returns the comment's latest case."""
    now = now or datetime.now(UTC)
    text = _note(note, shared)
    await advisory_xact_lock(session, "comment_case", str(comment_id))
    comment = await _locked(session, comment_id)
    if comment is None:
        raise comment_not_found()
    post = await session.get(FeedPost, comment.deed_id)
    assert post is not None
    until = _restorable_until(comment, limits)
    if until is None or now > until:
        raise Conflict("This comment can't be restored.", code="not_restorable")
    case_ids = list(
        await session.scalars(
            select(CommentReportCase.id).where(CommentReportCase.comment_id == comment.id)
        )
    )
    _check_conflict(actor_id, comment, post, await _filers(session, *case_ids))
    last_case = await session.scalar(
        select(CommentReportCase.id)
        .where(CommentReportCase.comment_id == comment.id)
        .order_by(CommentReportCase.opened_at.desc(), CommentReportCase.id.desc())
        .limit(1)
    )
    if last_case is None:  # a moderation removal only ever comes from a case
        raise Conflict("This comment can't be restored.", code="not_restorable")
    comment.removal_notice_seen_at = None
    if comment.author_deleted_at is not None:
        # The author deleted it after the removal (L3): undoing the moderation leaves the
        # author's own deletion, erased now. Still not live, so the counts stay.
        comment.deleted_at = comment.author_deleted_at
        comment.deleted_by = comment.author_id
        comment.deleted_role = CommentDeletedRole.AUTHOR
        comment.body = None
        await session.flush()
    else:
        comment.deleted_at = None
        comment.deleted_by = None
        comment.deleted_role = None
        await session.flush()
        await change_counts(session, comment.deed_id, comment.author_id, +1)
    _record(
        session,
        comment,
        case_id=last_case,
        action=CommentDecisionAction.RESTORE,
        note=text,
        actor_id=actor_id,
        now=now,
    )
    await session.flush()
    return last_case
