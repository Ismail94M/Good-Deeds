"""Admin photo-report queue, review, decisions and restore (§3.3 #11 to #14, §3.4b #3, #4, #6,
D42, D43, Q32, Q34).

Only a decision changes a photo. Every decision writes one append-only ``photo_decisions`` row,
one ``audit_log`` row and one ``PhotoModerationDecided`` event. A moderator or admin who is the
deed's doer or reporter, or the photo's uploader, can't decide or restore (403
``conflict_of_interest``).

Lock order: ``photo_case`` advisory lock -> photo row -> case row (the owner delete path takes
photo -> case too). The deed's feed visibility is read by the router (``feed.service``) and
passed in: this file never imports ``feed`` (D30).
"""

import uuid
from collections import defaultdict
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Literal, Protocol

from sqlalchemy import func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit
from app.core.db import advisory_xact_lock
from app.core.errors import Conflict, Forbidden, NotFound
from app.core.events import emit
from app.core.ratelimit import RateLimiter
from app.core.storage import ObjectStorage
from app.modules.deeds import service as deeds
from app.modules.identity import service as identity
from app.modules.media import _cursor
from app.modules.media._common import Invalid, photo_not_found, utcnow
from app.modules.media._limits import MediaLimits
from app.modules.media.events import PhotoModerationDecided
from app.modules.media.models import (
    PHOTO_REPORT_PRIORITY,
    CaseDecision,
    ObjectMove,
    Photo,
    PhotoDecision,
    PhotoDecisionAction,
    PhotoReport,
    PhotoReportCase,
    PhotoReportReason,
    PhotoStatus,
    PhotoTakedown,
    ReportCaseStatus,
)
from app.modules.media.schemas import (
    AdminDecision,
    AdminDeed,
    AdminDeedRef,
    AdminPhoto,
    AdminReport,
    CaseDetail,
    CaseListItem,
    CaseOut,
    CasePage,
    DecisionCreate,
    ReporterRef,
    RestoreCreate,
    UserRef,
)
from app.modules.media.service import admin_signed

type Queue = Literal["open", "resolved"]


def case_not_found() -> NotFound:
    return NotFound("Report not found.", code="case_not_found")


async def enforce_decision_limit(
    limiter: RateLimiter, limits: MediaLimits, user_id: uuid.UUID
) -> None:
    await limiter.hit(f"admindecision:u:{user_id}", limits.rate_admin_decision_user)


def _note(note: str, limits: MediaLimits) -> str:
    text = note.strip()
    if len(text) < 5:
        raise Invalid("Add a note of at least 5 characters.", code="note_required")
    if len(text) > limits.decision_note_max:
        raise Invalid("The note is too long.", code="note_too_long")
    return text


class _Named(Protocol):
    @property
    def handle(self) -> str: ...
    @property
    def display_name(self) -> str: ...


def _ref(summaries: Mapping[uuid.UUID, _Named], user_id: uuid.UUID | None) -> UserRef | None:
    summary = summaries.get(user_id) if user_id is not None else None
    return (
        None
        if summary is None
        else UserRef(handle=summary.handle, display_name=summary.display_name)
    )


def _top_reason(reports: list[PhotoReport]) -> PhotoReportReason:
    """The most serious reason, the earliest report among equals."""
    best = min(reports, key=lambda r: (PHOTO_REPORT_PRIORITY[r.reason], r.created_at, r.id))
    return best.reason


def _case_out(case: PhotoReportCase, reports: list[PhotoReport]) -> CaseOut:
    return CaseOut(
        case_id=case.id,
        status=case.status.value,
        priority=case.priority,
        top_reason=_top_reason(reports).value if reports else PhotoReportReason.OTHER.value,
        report_count=case.report_count,
        opened_at=case.opened_at,
        resolved_at=case.resolved_at,
        decision=case.decision.value if case.decision else None,
        removal_reason=case.removal_reason.value if case.removal_reason else None,
    )


async def _reports_by_case(
    session: AsyncSession, case_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[PhotoReport]]:
    out: dict[uuid.UUID, list[PhotoReport]] = defaultdict(list)
    if not case_ids:
        return out
    rows = await session.scalars(
        select(PhotoReport)
        .where(PhotoReport.case_id.in_(case_ids))
        .order_by(PhotoReport.created_at, PhotoReport.id)
    )
    for report in rows:
        out[report.case_id].append(report)
    return out


# --------------------------------------------------- queue (#11)


async def list_cases(
    session: AsyncSession,
    storage: ObjectStorage,
    *,
    queue: Queue,
    limits: MediaLimits,
    cursor: str | None = None,
    limit: int = _cursor.DEFAULT_PAGE_SIZE,
    now: datetime | None = None,
) -> CasePage:
    now = now or utcnow()
    stmt = select(PhotoReportCase)
    if queue == "open":
        stmt = stmt.where(PhotoReportCase.status == ReportCaseStatus.OPEN)
        if cursor:
            priority, at, last_id = _cursor.decode(cursor, queue)
            stmt = stmt.where(
                tuple_(PhotoReportCase.priority, PhotoReportCase.opened_at, PhotoReportCase.id)
                > tuple_(priority, at, last_id)
            )
        stmt = stmt.order_by(
            PhotoReportCase.priority, PhotoReportCase.opened_at, PhotoReportCase.id
        )
    else:
        stmt = stmt.where(PhotoReportCase.status == ReportCaseStatus.RESOLVED)
        if cursor:
            _, at, last_id = _cursor.decode(cursor, queue)
            stmt = stmt.where(
                tuple_(PhotoReportCase.resolved_at, PhotoReportCase.id) < tuple_(at, last_id)
            )
        stmt = stmt.order_by(PhotoReportCase.resolved_at.desc(), PhotoReportCase.id.desc())
    cases = list(await session.scalars(stmt.limit(limit + 1)))
    more = len(cases) > limit
    cases = cases[:limit]

    count_rows = await session.execute(
        select(PhotoReportCase.status, func.count())
        .where(PhotoReportCase.status.in_((ReportCaseStatus.OPEN, ReportCaseStatus.RESOLVED)))
        .group_by(PhotoReportCase.status)
    )
    counts = {status: int(n) for status, n in count_rows}
    reports = await _reports_by_case(session, [c.id for c in cases])
    photos = {
        p.id: p
        for p in await session.scalars(
            select(Photo).where(Photo.id.in_([c.photo_id for c in cases]))
        )
    }
    views = await deeds.get_deeds(session, {p.deed_id for p in photos.values() if p.deed_id})
    people = await identity.public_summaries(
        session, {v.doer_id for v in views.values() if v.doer_id is not None}
    )

    items = []
    for case in cases:
        photo = photos[case.photo_id]
        view = views.get(photo.deed_id) if photo.deed_id else None
        signed = admin_signed(photo, storage, limits, now=now)
        case_reports = reports[case.id]
        items.append(
            CaseListItem(
                **_case_out(case, case_reports).model_dump(),
                oldest_report_at=case_reports[0].created_at if case_reports else case.opened_at,
                photo_thumb_url=signed.thumb_url if signed else None,
                deed=AdminDeedRef(
                    doer=_ref(people, view.doer_id) if view else None,
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
        next_cursor = _cursor.encode(queue, last.priority, key_at, last.id)
    return CasePage(
        items=items,
        next_cursor=next_cursor,
        open_count=int(counts.get(ReportCaseStatus.OPEN, 0)),
        resolved_count=int(counts.get(ReportCaseStatus.RESOLVED, 0)),
    )


# --------------------------------------------------- review (#12)


async def case_deed_id(session: AsyncSession, case_id: uuid.UUID) -> uuid.UUID:
    """The deed of a case's photo (for the router's visibility read); 404 if unknown."""
    deed_id = await session.scalar(
        select(Photo.deed_id)
        .join(PhotoReportCase, PhotoReportCase.photo_id == Photo.id)
        .where(PhotoReportCase.id == case_id)
    )
    if deed_id is None:
        raise case_not_found()
    return deed_id


async def case_detail(
    session: AsyncSession,
    storage: ObjectStorage,
    case_id: uuid.UUID,
    *,
    listed: bool,
    limits: MediaLimits,
    now: datetime | None = None,
) -> CaseDetail:
    now = now or utcnow()
    case = await session.scalar(
        select(PhotoReportCase)
        .where(PhotoReportCase.id == case_id)
        .execution_options(populate_existing=True)
    )
    if case is None:
        raise case_not_found()
    photo = await session.scalar(
        select(Photo).where(Photo.id == case.photo_id).execution_options(populate_existing=True)
    )
    assert photo is not None and photo.deed_id is not None
    view = await deeds.get_deed(session, photo.deed_id)
    assert view is not None
    reports = (await _reports_by_case(session, [case.id]))[case.id]
    decisions = list(
        await session.scalars(
            select(PhotoDecision)
            .where(PhotoDecision.photo_id == photo.id)
            .order_by(PhotoDecision.created_at, PhotoDecision.id)
        )
    )
    photo_count = await session.scalar(
        select(func.count())
        .select_from(Photo)
        .where(Photo.deed_id == photo.deed_id, Photo.status == PhotoStatus.READY)
    )
    people = await identity.public_summaries(
        session,
        {
            i
            for i in (
                view.doer_id,
                view.reporter_id,
                photo.uploader_user_id,
                *(r.reporter_user_id for r in reports),
                *(d.actor_user_id for d in decisions),
            )
            if i is not None
        },
    )
    signed = admin_signed(photo, storage, limits, now=now)
    removed = photo.takedown != PhotoTakedown.NONE
    restorable = (
        photo.takedown == PhotoTakedown.ADMIN_REMOVED
        and photo.objects_purged_at is None
        and photo.removed_at is not None
    )
    return CaseDetail(
        case=_case_out(case, reports),
        photo=AdminPhoto(
            id=photo.id,
            display_url=signed.display_url if signed else None,
            position=photo.position,
            photo_count=int(photo_count or 0),
            uploaded_at=photo.uploaded_at,
            uploader=_ref(people, photo.uploader_user_id),
            blurred_count=len(photo.blur_regions),
            state="removed" if removed else "visible",
            restorable_until=(
                photo.removed_at + timedelta(days=limits.removed_photo_retention_days)
                if restorable and photo.removed_at is not None
                else None
            ),
            purged_at=photo.objects_purged_at,
        ),
        deed=AdminDeed(
            doer=_ref(people, view.doer_id),
            reporter=_ref(people, view.reporter_id),
            category=view.category,
            area_name=deeds.area_name(view),
            status=view.status.value,
            listed=listed,
        ),
        reports=[
            AdminReport(
                reason=r.reason.value,
                details=r.details,
                reporter=(
                    ReporterRef(
                        kind="member", handle=(_ref(people, r.reporter_user_id) or _GONE).handle
                    )
                    if r.reporter_user_id is not None
                    else ReporterRef(kind="visitor")
                ),
                created_at=r.created_at,
            )
            for r in reports
        ],
        decisions=[
            AdminDecision(
                action=d.action.value,
                note=d.note,
                actor=_ref(people, d.actor_user_id),
                created_at=d.created_at,
            )
            for d in decisions
        ],
    )


_GONE = UserRef(handle="", display_name="")


# --------------------------------------------------- decisions (#13, #14)


def _check_conflict(actor_id: uuid.UUID, photo: Photo, view: deeds.DeedView | None) -> None:
    involved = {photo.uploader_user_id}
    if view is not None:
        involved |= {view.reporter_id, *([view.doer_id] if view.doer_id else [])}
    if actor_id in involved:
        raise Forbidden(
            "Another admin needs to review this one: you're part of this deed.",
            code="conflict_of_interest",
        )


async def _locked_photo(session: AsyncSession, photo_id: uuid.UUID) -> Photo | None:
    return await session.scalar(
        select(Photo)
        .where(Photo.id == photo_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def _record(
    session: AsyncSession,
    photo: Photo,
    *,
    case_id: uuid.UUID | None,
    action: PhotoDecisionAction,
    reason: PhotoReportReason | None,
    note: str,
    actor_id: uuid.UUID,
    now: datetime,
) -> None:
    assert photo.deed_id is not None
    session.add(
        PhotoDecision(
            photo_id=photo.id,
            case_id=case_id,
            action=action,
            removal_reason=reason,
            note=note,
            actor_user_id=actor_id,
            created_at=now,
        )
    )
    verb = {"keep": "kept", "remove": "removed", "restore": "restored"}[action.value]
    record_audit(
        session,
        entity_type="photo",
        entity_id=str(photo.id),
        action=f"photo.{verb}",
        actor_id=actor_id,
        new_value={
            "case_id": str(case_id) if case_id else None,
            "deed_id": str(photo.deed_id),
            "removal_reason": reason.value if reason else None,
        },
        reason=note,
    )
    emit(
        session,
        PhotoModerationDecided(
            photo_id=photo.id,
            case_id=case_id,
            deed_id=photo.deed_id,
            uploader_id=photo.uploader_user_id,
            action=action.value,
            removal_reason=reason.value if reason else None,
        ),
    )


async def decide(
    session: AsyncSession,
    actor_id: uuid.UUID,
    case_id: uuid.UUID,
    data: DecisionCreate,
    *,
    limits: MediaLimits,
    now: datetime | None = None,
) -> bool:
    """Keep or remove. Returns True when objects must move to ``held/`` after commit."""
    now = now or utcnow()
    note = _note(data.note, limits)
    photo_id = await session.scalar(
        select(PhotoReportCase.photo_id).where(PhotoReportCase.id == case_id)
    )
    if photo_id is None:
        raise case_not_found()
    await advisory_xact_lock(session, "photo_case", str(photo_id))
    photo = await _locked_photo(session, photo_id)
    case = await session.scalar(
        select(PhotoReportCase)
        .where(PhotoReportCase.id == case_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    assert photo is not None and case is not None
    if case.status != ReportCaseStatus.OPEN:
        raise Conflict("This report has already been decided.", code="case_not_open")
    view = await deeds.get_deed(session, photo.deed_id) if photo.deed_id else None
    _check_conflict(actor_id, photo, view)

    reason: PhotoReportReason | None = None
    if data.action == "remove":
        if data.removal_reason:
            reason = PhotoReportReason(data.removal_reason)
        else:
            reports = (await _reports_by_case(session, [case.id]))[case.id]
            reason = _top_reason(reports) if reports else PhotoReportReason.OTHER
        photo.takedown = PhotoTakedown.ADMIN_REMOVED
        photo.removed_at = now
        photo.object_move = ObjectMove.HOLD
        case.decision = CaseDecision.REMOVED
    else:
        case.decision = CaseDecision.KEPT
    case.status = ReportCaseStatus.RESOLVED
    case.resolved_at = now
    case.decided_by = actor_id
    case.removal_reason = reason
    _record(
        session,
        photo,
        case_id=case.id,
        action=PhotoDecisionAction(data.action),
        reason=reason,
        note=note,
        actor_id=actor_id,
        now=now,
    )
    await session.flush()
    return reason is not None


async def restore(
    session: AsyncSession,
    actor_id: uuid.UUID,
    photo_id: uuid.UUID,
    data: RestoreCreate,
    *,
    limits: MediaLimits,
    now: datetime | None = None,
) -> uuid.UUID:
    """Undo an admin removal while the held copies exist (Q32). The variants come back under a
    new version after commit, so old links stay dead. Returns the photo's latest case."""
    now = now or utcnow()
    note = _note(data.note, limits)
    await advisory_xact_lock(session, "photo_case", str(photo_id))
    photo = await _locked_photo(session, photo_id)
    if photo is None or photo.deed_id is None:
        raise photo_not_found()
    if photo.takedown != PhotoTakedown.ADMIN_REMOVED or photo.objects_purged_at is not None:
        raise Conflict("This photo can't be restored.", code="not_restorable")
    view = await deeds.get_deed(session, photo.deed_id)
    _check_conflict(actor_id, photo, view)
    last_case = await session.scalar(
        select(PhotoReportCase.id)
        .where(PhotoReportCase.photo_id == photo.id)
        .order_by(PhotoReportCase.opened_at.desc(), PhotoReportCase.id.desc())
        .limit(1)
    )
    if last_case is None:  # a removal only ever comes from a case
        raise Conflict("This photo can't be restored.", code="not_restorable")
    photo.takedown = PhotoTakedown.NONE
    photo.removed_at = None
    photo.object_move = ObjectMove.RELEASE
    _record(
        session,
        photo,
        case_id=last_case,
        action=PhotoDecisionAction.RESTORE,
        reason=None,
        note=note,
        actor_id=actor_id,
        now=now,
    )
    await session.flush()
    return last_case
