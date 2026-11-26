"""The member *Profile -> Reviews* page (docs/M5-DESIGN.md §3.3b M1 to M4, D99 to D101, §6.2).

Own data only: another member's explanation request or notice is 404, never 403. Member wording
(D101): nothing here names a case type, a detector, the other people in a case or an admin, or
says "fraud". Reasons come from ``member_reason_labels``; each deed is one of the member's own,
shown with the other person on that deed only.

- An explanation request is ``waiting`` (no reply, due date ahead, look not finished),
  ``replied`` or ``closed`` (due date passed with no reply, or the look finished first). Once the
  look ended with nothing for this person (case dismissed, or confirmed with no adverse deed of
  theirs) it reads "We've finished looking. No changes."
- A notice lists the person's affected deeds with an effect text (§6.2), the reason text, and
  the one appeal they may make before ``appeal_until`` (``_appeals.create_appeal``).
- ``unread`` counts requests and notices new or changed (a finished look, a decided appeal)
  since ``review_views.seen_at``; ``POST /api/reviews/seen`` moves it forward.
"""

import uuid
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, NotFound
from app.modules.deeds import service as deeds
from app.modules.fraud import _notices
from app.modules.fraud._common import Invalid
from app.modules.fraud._limits import FraudConfig
from app.modules.fraud.models import (
    Appeal,
    AppealOutcome,
    DecisionNotice,
    ExplanationRequest,
    FlagCase,
    FlagCaseDeed,
    FlagCaseOutcome,
    FlagCaseStatus,
    NoticeKind,
    ReviewView,
)
from app.modules.fraud.schemas import (
    MyAppeal,
    MyDeedRef,
    MyExplanation,
    MyNotice,
    MyNoticeItem,
    MyReviewLimits,
    MyReviews,
    UserRef,
)
from app.modules.identity import service as identity

NO_CHANGES = "We've finished looking. No changes."
NOT_COUNTED = "Not counted for leaderboards"
REMOVED = "Removed after review"
RESTORED = "Restored after another review"
POINTS_RESTORED = "Points restored after another review"
WAITING_FOR_REVIEW = "Waiting for another review"
WILL_BE_REMOVED = "Will be removed after review"
APPEAL_OUTCOME_TEXT: dict[AppealOutcome, str] = {
    AppealOutcome.OVERTURNED: "We looked again and restored your deeds",
    AppealOutcome.UPHELD: "We looked again and the decision stays",
}
_GONE = UserRef(handle="", display_name="")


class _Named(Protocol):
    @property
    def handle(self) -> str: ...
    @property
    def display_name(self) -> str: ...


def explanation_not_found() -> NotFound:
    return NotFound("Not found.", code="not_found")


def notice_not_found() -> NotFound:
    return NotFound("Not found.", code="not_found")


def day_text(at: datetime, config: FraudConfig) -> str:
    """A date in the launch time zone, e.g. "26 December 2026"."""
    local = at.astimezone(config.zone)
    return f"{local.day} {local:%B %Y}"


def reason_text(reason_key: str, config: FraudConfig) -> str:
    labels = config.member_reason_labels
    return labels.get(reason_key) or labels.get("other") or labels["case"]


@dataclass(frozen=True)
class _Context:
    """What the texts depend on, loaded once per page."""

    views: Mapping[uuid.UUID, deeds.DeedView]
    labels: Mapping[str, str]
    people: Mapping[uuid.UUID, _Named]
    # (case, user) pairs whose appeal was overturned.
    overturned: set[tuple[uuid.UUID, uuid.UUID]]


def _ref(people: Mapping[uuid.UUID, _Named], user_id: uuid.UUID | None) -> UserRef:
    summary = people.get(user_id) if user_id is not None else None
    if summary is None:
        return _GONE
    return UserRef(handle=summary.handle, display_name=summary.display_name)


def _my_deed(ctx: _Context, me: uuid.UUID, deed_id: uuid.UUID, config: FraudConfig) -> MyDeedRef:
    view = ctx.views[deed_id]
    reporter = view.reporter_id == me
    other = view.doer_id if reporter else view.reporter_id
    return MyDeedRef(
        deed_id=view.id,
        role="reporter" if reporter else "doer",
        category_label=ctx.labels.get(view.category, view.category),
        area_name=deeds.area_name(view),
        occurred_on=view.occurred_at.astimezone(config.zone).date(),
        other_person=_ref(ctx.people, other),
    )


def _mine(view: deeds.DeedView | None, me: uuid.UUID) -> bool:
    return view is not None and me in (view.reporter_id, view.doer_id)


def _effect_text(
    ctx: _Context,
    notice: DecisionNotice,
    appeal: Appeal | None,
    item: Mapping[str, object],
    config: FraudConfig,
) -> str:
    effect = str(item.get("effect"))
    deed_id = uuid.UUID(str(item["deed_id"]))
    view = ctx.views.get(deed_id)
    if effect == _notices.POINTS_CHANGED:
        if appeal is not None and appeal.outcome == AppealOutcome.OVERTURNED:
            return POINTS_RESTORED
        return f"Points changed from {item.get('old_points')} to {item.get('new_points')}"
    # A case item: restored when anyone on the deed won their appeal (that releases the deed).
    parties = {view.reporter_id, view.doer_id} if view is not None else set()
    case_id = notice.case_id
    restored = case_id is not None and any((case_id, p) in ctx.overturned for p in parties)
    if restored:
        return RESTORED
    if effect == _notices.EXCLUDED:
        return NOT_COUNTED
    if view is not None and view.status == deeds.DeedStatus.REJECTED:
        return REMOVED
    if appeal is not None and appeal.outcome is None:
        return WAITING_FOR_REVIEW
    if notice.final_at is not None:
        return WILL_BE_REMOVED
    return (
        f"Will be removed on {day_text(notice.appeal_until, config)} "
        "unless you ask for another review"
    )


def _my_appeal(appeal: Appeal | None) -> MyAppeal | None:
    if appeal is None:
        return None
    return MyAppeal(
        created_at=appeal.created_at,
        reason=appeal.reason,
        outcome=appeal.outcome.value if appeal.outcome else None,
        outcome_text=APPEAL_OUTCOME_TEXT[appeal.outcome] if appeal.outcome else None,
        decided_at=appeal.decided_at,
    )


def can_appeal(notice: DecisionNotice, appeal: Appeal | None, now: datetime) -> bool:
    return appeal is None and notice.final_at is None and now < notice.appeal_until


def notice_out(
    ctx: _Context,
    me: uuid.UUID,
    notice: DecisionNotice,
    appeal: Appeal | None,
    *,
    config: FraudConfig,
    now: datetime,
) -> MyNotice:
    items = [
        MyNoticeItem(
            deed=_my_deed(ctx, me, uuid.UUID(str(item["deed_id"])), config),
            effect_text=_effect_text(ctx, notice, appeal, item, config),
        )
        for item in notice.items
        if _mine(ctx.views.get(uuid.UUID(str(item["deed_id"]))), me)
    ]
    return MyNotice(
        id=notice.id,
        created_at=notice.created_at,
        reason_text=reason_text(notice.reason_key, config),
        items=items,
        appeal_until=notice.appeal_until,
        can_appeal=can_appeal(notice, appeal, now),
        appeal=_my_appeal(appeal),
        final=notice.final_at is not None,
    )


def _explanation_status(request: ExplanationRequest, case: FlagCase, now: datetime) -> str:
    if request.replied_at is not None:
        return "replied"
    if case.status == FlagCaseStatus.RESOLVED or request.reply_due_at <= now:
        return "closed"
    return "waiting"


def _no_changes(case: FlagCase, noticed: bool) -> bool:
    """The look ended with nothing for this person."""
    if case.status != FlagCaseStatus.RESOLVED:
        return False
    return case.outcome == FlagCaseOutcome.DISMISSED or not noticed


def explanation_out(
    ctx: _Context,
    me: uuid.UUID,
    request: ExplanationRequest,
    case: FlagCase,
    case_deed_ids: Iterable[uuid.UUID],
    *,
    noticed: bool,
    config: FraudConfig,
    now: datetime,
) -> MyExplanation:
    mine = [d for d in case_deed_ids if _mine(ctx.views.get(d), me)]
    mine.sort(key=lambda d: (ctx.views[d].occurred_at, str(d)))
    return MyExplanation(
        id=request.id,
        requested_at=request.requested_at,
        reply_due_at=request.reply_due_at,
        replied_at=request.replied_at,
        reply=request.reply,
        status=_explanation_status(request, case, now),
        outcome_text=NO_CHANGES if _no_changes(case, noticed) else None,
        deeds=[_my_deed(ctx, me, d, config) for d in mine],
    )


async def _context(
    session: AsyncSession,
    deed_ids: Iterable[uuid.UUID],
    case_ids: Iterable[uuid.UUID],
) -> _Context:
    views = await deeds.get_deeds(session, set(deed_ids))
    people = await identity.public_summaries(
        session,
        {v.reporter_id for v in views.values()}
        | {v.doer_id for v in views.values() if v.doer_id is not None},
    )
    overturned: set[tuple[uuid.UUID, uuid.UUID]] = set()
    ids = set(case_ids)
    if ids:
        rows = await session.execute(
            select(DecisionNotice.case_id, DecisionNotice.user_id)
            .join(Appeal, Appeal.notice_id == DecisionNotice.id)
            .where(DecisionNotice.case_id.in_(ids), Appeal.outcome == AppealOutcome.OVERTURNED)
        )
        overturned = {(c, u) for c, u in rows if c is not None}
    return _Context(
        views=views,
        labels=await deeds.category_labels(session),
        people=people,
        overturned=overturned,
    )


async def _case_deeds(
    session: AsyncSession, case_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, list[uuid.UUID]]:
    out: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
    ids = set(case_ids)
    if ids:
        for case_id, deed_id in await session.execute(
            select(FlagCaseDeed.case_id, FlagCaseDeed.deed_id).where(FlagCaseDeed.case_id.in_(ids))
        ):
            out[case_id].append(deed_id)
    return out


async def _notices_of(
    session: AsyncSession, user_id: uuid.UUID, notice_id: uuid.UUID | None = None
) -> list[tuple[DecisionNotice, Appeal | None]]:
    stmt = (
        select(DecisionNotice, Appeal)
        .outerjoin(Appeal, Appeal.notice_id == DecisionNotice.id)
        .where(DecisionNotice.user_id == user_id)
        .order_by(DecisionNotice.created_at.desc(), DecisionNotice.id.desc())
        .execution_options(populate_existing=True)
    )
    if notice_id is not None:
        stmt = stmt.where(DecisionNotice.id == notice_id)
    return [(n, a) for n, a in await session.execute(stmt)]


async def _requests_of(
    session: AsyncSession, user_id: uuid.UUID, request_id: uuid.UUID | None = None
) -> list[tuple[ExplanationRequest, FlagCase]]:
    stmt = (
        select(ExplanationRequest, FlagCase)
        .join(FlagCase, FlagCase.id == ExplanationRequest.case_id)
        .where(ExplanationRequest.user_id == user_id)
        .order_by(ExplanationRequest.requested_at.desc(), ExplanationRequest.id.desc())
        .execution_options(populate_existing=True)
    )
    if request_id is not None:
        stmt = stmt.where(ExplanationRequest.id == request_id)
    return [(r, c) for r, c in await session.execute(stmt)]


async def _noticed_cases(session: AsyncSession, user_id: uuid.UUID) -> set[uuid.UUID]:
    rows = await session.scalars(
        select(DecisionNotice.case_id).where(
            DecisionNotice.user_id == user_id, DecisionNotice.kind == NoticeKind.CASE
        )
    )
    return {c for c in rows if c is not None}


async def _build(
    session: AsyncSession,
    user_id: uuid.UUID,
    requests: list[tuple[ExplanationRequest, FlagCase]],
    notices: list[tuple[DecisionNotice, Appeal | None]],
    *,
    config: FraudConfig,
    now: datetime,
) -> tuple[list[MyExplanation], list[MyNotice]]:
    request_cases = {c.id for _, c in requests}
    case_deeds = await _case_deeds(session, request_cases)
    notice_cases = {n.case_id for n, _ in notices if n.case_id is not None}
    deed_ids = {d for ds in case_deeds.values() for d in ds}
    deed_ids |= {d for n, _ in notices for d in _notices.item_deed_ids(n)}
    ctx = await _context(session, deed_ids, notice_cases)
    noticed = await _noticed_cases(session, user_id) if requests else set()
    explanations = [
        explanation_out(
            ctx,
            user_id,
            request,
            case,
            case_deeds.get(case.id, []),
            noticed=case.id in noticed,
            config=config,
            now=now,
        )
        for request, case in requests
    ]
    notice_rows = [
        notice_out(ctx, user_id, notice, appeal, config=config, now=now)
        for notice, appeal in notices
    ]
    return explanations, notice_rows


def _changed_at(request: ExplanationRequest, case: FlagCase, noticed: bool) -> datetime:
    """When the member last had something new to see on a request (not their own reply)."""
    if _no_changes(case, noticed) and case.resolved_at is not None:
        return max(request.requested_at, case.resolved_at)
    return request.requested_at


async def my_reviews(
    session: AsyncSession, user_id: uuid.UUID, *, config: FraudConfig, now: datetime
) -> MyReviews:
    requests = await _requests_of(session, user_id)
    notices = await _notices_of(session, user_id)
    explanations, notice_rows = await _build(
        session, user_id, requests, notices, config=config, now=now
    )
    seen_at = await session.scalar(select(ReviewView.seen_at).where(ReviewView.user_id == user_id))
    noticed = await _noticed_cases(session, user_id)
    changes = [_changed_at(r, c, c.id in noticed) for r, c in requests]
    changes += [
        max(n.created_at, a.decided_at) if a is not None and a.decided_at else n.created_at
        for n, a in notices
    ]
    unread = sum(1 for at in changes if seen_at is None or at > seen_at)
    return MyReviews(
        explanations=explanations,
        notices=notice_rows,
        unread=unread,
        limits=MyReviewLimits(
            reply_max_chars=config.explanation.reply_max_chars,
            appeal_reason_min_chars=config.appeal.reason_min_chars,
            appeal_reason_max_chars=config.appeal.reason_max_chars,
        ),
    )


async def my_notice(
    session: AsyncSession,
    user_id: uuid.UUID,
    notice_id: uuid.UUID,
    *,
    config: FraudConfig,
    now: datetime,
) -> MyNotice:
    notices = await _notices_of(session, user_id, notice_id)
    if not notices:
        raise notice_not_found()
    _, rows = await _build(session, user_id, [], notices, config=config, now=now)
    return rows[0]


async def my_explanation(
    session: AsyncSession,
    user_id: uuid.UUID,
    request_id: uuid.UUID,
    *,
    config: FraudConfig,
    now: datetime,
) -> MyExplanation:
    requests = await _requests_of(session, user_id, request_id)
    if not requests:
        raise explanation_not_found()
    rows, _ = await _build(session, user_id, requests, [], config=config, now=now)
    return rows[0]


def clean_text(raw: str, *, min_chars: int, max_chars: int, what: str) -> str:
    """Trimmed member text between the bounds (422 ``<what>_required`` / ``<what>_too_long``)."""
    if "\x00" in raw:
        raise Invalid("That text contains a character we can't accept.", code="invalid_text")
    text = raw.strip()
    if len(text) < max(1, min_chars):
        if min_chars > 1:
            message = f"Write at least {min_chars} characters."
        else:
            message = "Write something first."
        raise Invalid(message, code=f"{what}_required")
    if len(text) > max_chars:
        raise Invalid(f"Keep it under {max_chars} characters.", code=f"{what}_too_long")
    return text


async def reply(
    session: AsyncSession,
    user_id: uuid.UUID,
    request_id: uuid.UUID,
    raw: str,
    *,
    config: FraudConfig,
    now: datetime,
) -> None:
    """M2: the one reply to an explanation request, before it is due. The caller commits."""
    request = await session.scalar(
        select(ExplanationRequest)
        .where(ExplanationRequest.id == request_id, ExplanationRequest.user_id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if request is None:
        raise explanation_not_found()
    if request.replied_at is not None:
        raise Conflict("You've already replied.", code="already_replied")
    case_status = await session.scalar(
        select(FlagCase.status).where(FlagCase.id == request.case_id)
    )
    if request.reply_due_at <= now or case_status == FlagCaseStatus.RESOLVED:
        raise Conflict("This request is closed.", code="reply_closed")
    text = clean_text(raw, min_chars=1, max_chars=config.explanation.reply_max_chars, what="reply")
    request.reply = text
    request.replied_at = now
    await session.flush()


async def mark_seen(session: AsyncSession, user_id: uuid.UUID, now: datetime) -> None:
    """M4: upsert ``review_views`` (never moves back). The caller commits."""
    stmt = insert(ReviewView).values(user_id=user_id, seen_at=now)
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=[ReviewView.user_id],
            set_={"seen_at": func.greatest(ReviewView.seen_at, stmt.excluded.seen_at)},
        )
    )
