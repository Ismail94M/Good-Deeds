"""One appeal per decision notice, decided by a different admin (docs/M5-DESIGN.md D100, §3.3
#11, #12, §3.3b M3, PO Q78).

- **Member (M3):** the person a notice is for may ask for another review once, before
  ``appeal_until`` and before the notice is final, with a reason of ``appeal.reason_min_chars``
  to ``appeal.reason_max_chars``.
- **Who may decide (D90, D100):** an admin who is not the appellant, did not make the decision
  (the case's ``resolved_by``; the override's actor, from the notice's audit row) and is not
  conflicted: for a case notice the case conflict rule (``_coi``); for an override notice, a
  party to the deed or anyone with a verified deed with a party in ``coi_lookback_days``.
  Everyone else gets 404 and doesn't see the appeal in the queue or the counts.
- **Uphold:** the notice becomes final (``final_at``); a rejection then runs on the next
  ``fraud.finalize_decisions`` once everyone on the deed is final.
- **Overturn:** the person's holds for the case are released (``appeal_overturned``), which
  also cancels the pending rejection of those deeds (``finalize_decisions`` never rejects a deed
  with an overturned party), and the notice becomes final. For an override notice scoring clears
  the override on ``AppealDecided``. A resolved case row is frozen and isn't touched.
- Both write ``audit_log`` (``appeal.upheld`` / ``appeal.overturned``) and emit
  ``AppealDecided``.
- **Q78:** with no eligible admin an appeal just waits (permanent steps stay paused);
  ``waiting_for_second_admin`` counts those for the admin status page.
"""

import uuid
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit
from app.core.errors import Conflict, NotFound
from app.core.events import emit
from app.modules.deeds import service as deeds
from app.modules.fraud import _coi, _cursor, _member, _notices
from app.modules.fraud._common import clean_note
from app.modules.fraud._limits import FraudConfig
from app.modules.fraud.events import AppealDecided, DeedHoldChanged
from app.modules.fraud.models import (
    Appeal,
    AppealOutcome,
    DecisionNotice,
    FlagCase,
    FlagCaseUser,
    HoldReleaseReason,
    NoticeKind,
    PrizeHold,
)
from app.modules.fraud.schemas import (
    AppealCounts,
    AppealDecisionIn,
    AppealDetail,
    AppealNotice,
    AppealNoticeItem,
    AppealPage,
    AppealRow,
    UserRef,
)
from app.modules.identity import service as identity

type AppealQueue = Literal["open", "decided"]

AUDIT_ENTITY = "appeal"
_GONE = UserRef(handle="", display_name="")


class _Named(Protocol):
    @property
    def handle(self) -> str: ...
    @property
    def display_name(self) -> str: ...


def appeal_not_found() -> NotFound:
    """Also the answer for the decider and conflicted admins (D90, D100)."""
    return NotFound("Appeal not found.", code="appeal_not_found")


def _ref(people: Mapping[uuid.UUID, _Named], user_id: uuid.UUID | None) -> UserRef:
    summary = people.get(user_id) if user_id is not None else None
    if summary is None:
        return _GONE
    return UserRef(handle=summary.handle, display_name=summary.display_name)


# --------------------------------------------------- M3: the member's appeal


async def create_appeal(
    session: AsyncSession,
    user_id: uuid.UUID,
    notice_id: uuid.UUID,
    raw_reason: str,
    *,
    config: FraudConfig,
    now: datetime,
) -> None:
    """Ask for another review of a notice. The caller commits."""
    notice = await session.scalar(
        select(DecisionNotice)
        .where(DecisionNotice.id == notice_id, DecisionNotice.user_id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if notice is None:
        raise _member.notice_not_found()
    existing = await session.scalar(select(Appeal.id).where(Appeal.notice_id == notice.id))
    if existing is not None:
        raise Conflict("You've already asked for another review.", code="already_appealed")
    if notice.final_at is not None or now >= notice.appeal_until:
        raise Conflict("The time to ask for another review has passed.", code="appeal_closed")
    reason = _member.clean_text(
        raw_reason,
        min_chars=config.appeal.reason_min_chars,
        max_chars=config.appeal.reason_max_chars,
        what="reason",
    )
    session.add(Appeal(notice_id=notice.id, user_id=user_id, reason=reason, created_at=now))
    try:
        async with session.begin_nested():
            await session.flush()
    except IntegrityError as exc:
        if "uq_appeals_notice_id" in str(exc.orig):
            raise Conflict(
                "You've already asked for another review.", code="already_appealed"
            ) from exc
        raise


# --------------------------------------------------- who may decide


@dataclass(frozen=True)
class _Facts:
    """What eligibility depends on, for a batch of appeals."""

    deciders: Mapping[uuid.UUID, uuid.UUID | None]  # notice id -> decider
    subjects: Mapping[uuid.UUID, set[uuid.UUID]]  # case id -> subjects
    parties: Mapping[uuid.UUID, set[uuid.UUID]]  # override notice id -> deed parties
    requesters: Mapping[uuid.UUID, uuid.UUID]  # override notice id -> requesting admin (Q77)


async def _facts(session: AsyncSession, notices: Sequence[DecisionNotice]) -> _Facts:
    case_ids = {n.case_id for n in notices if n.case_id is not None}
    case_deciders: dict[uuid.UUID, uuid.UUID] = {}
    subjects: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
    if case_ids:
        for case_id, resolved_by in await session.execute(
            select(FlagCase.id, FlagCase.resolved_by).where(FlagCase.id.in_(case_ids))
        ):
            if resolved_by is not None:
                case_deciders[case_id] = resolved_by
        for case_id, subject in await session.execute(
            select(FlagCaseUser.case_id, FlagCaseUser.user_id).where(
                FlagCaseUser.case_id.in_(case_ids)
            )
        ):
            subjects[case_id].add(subject)
    overrides = [n for n in notices if n.kind == NoticeKind.OVERRIDE]
    views = await deeds.get_deeds(
        session, {d for n in overrides for d in _notices.item_deed_ids(n)}
    )
    parties: dict[uuid.UUID, set[uuid.UUID]] = {}
    for notice in overrides:
        people: set[uuid.UUID] = {notice.user_id}
        for deed_id in _notices.item_deed_ids(notice):
            view = views.get(deed_id)
            if view is not None:
                people.add(view.reporter_id)
                if view.doer_id is not None:
                    people.add(view.doer_id)
        parties[notice.id] = people
    return _Facts(
        deciders=await _notices.deciders(session, list(notices), case_deciders),
        subjects=subjects,
        parties=parties,
        requesters=await _notices.override_requesters(session, list(notices)),
    )


def _eligible(
    admin_id: uuid.UUID,
    visibility: _coi.Visibility,
    appeal: Appeal,
    notice: DecisionNotice,
    facts: _Facts,
) -> bool:
    if admin_id == appeal.user_id or facts.deciders.get(notice.id) == admin_id:
        return False
    if facts.requesters.get(notice.id) == admin_id:
        return False
    if notice.kind == NoticeKind.CASE and notice.case_id is not None:
        if notice.case_id in visibility.blocked_cases:
            return False
        return not (facts.subjects.get(notice.case_id, set()) & visibility.blocked_users)
    return not (facts.parties.get(notice.id, {notice.user_id}) & visibility.blocked_users)


async def _all_appeals(
    session: AsyncSession, *, open_only: bool = False, appeal_id: uuid.UUID | None = None
) -> list[tuple[Appeal, DecisionNotice]]:
    stmt = (
        select(Appeal, DecisionNotice)
        .join(DecisionNotice, DecisionNotice.id == Appeal.notice_id)
        .execution_options(populate_existing=True)
    )
    if open_only:
        stmt = stmt.where(Appeal.outcome.is_(None))
    if appeal_id is not None:
        stmt = stmt.where(Appeal.id == appeal_id)
    return [(a, n) for a, n in await session.execute(stmt)]


async def _reviewable(
    session: AsyncSession,
    admin_id: uuid.UUID,
    pairs: Sequence[tuple[Appeal, DecisionNotice]],
    *,
    config: FraudConfig,
    now: datetime,
) -> tuple[list[tuple[Appeal, DecisionNotice]], _Facts]:
    facts = await _facts(session, [n for _, n in pairs])
    visibility = await _coi.visibility(session, admin_id, config, now)
    return [(a, n) for a, n in pairs if _eligible(admin_id, visibility, a, n, facts)], facts


async def waiting_for_second_admin(
    session: AsyncSession, *, config: FraudConfig, now: datetime
) -> int:
    """Open appeals that no active admin may decide (PO Q78)."""
    pairs = await _all_appeals(session, open_only=True)
    if not pairs:
        return 0
    facts = await _facts(session, [n for _, n in pairs])
    waiting = set(range(len(pairs)))
    for admin_id in sorted(await identity.active_admin_ids(session)):
        if not waiting:
            break
        visibility = await _coi.visibility(session, admin_id, config, now)
        waiting -= {i for i in waiting if _eligible(admin_id, visibility, *pairs[i], facts)}
    return len(waiting)


# --------------------------------------------------- #11 queue


async def list_appeals(
    session: AsyncSession,
    admin_id: uuid.UUID,
    *,
    queue: AppealQueue,
    cursor: str | None,
    config: FraudConfig,
    now: datetime,
) -> AppealPage:
    """Open appeals oldest first, decided ones newest first; only those this admin may decide.
    Appeals are few, so eligibility is checked in Python over all of them."""
    pairs, facts = await _reviewable(
        session, admin_id, await _all_appeals(session), config=config, now=now
    )
    open_pairs = [p for p in pairs if p[0].outcome is None]
    decided = [p for p in pairs if p[0].outcome is not None]
    if queue == "open":
        chosen = sorted(open_pairs, key=lambda p: (p[0].created_at, p[0].id))
        if cursor:
            at, last_id = _cursor.decode_desc(cursor, "appeals_open")
            chosen = [p for p in chosen if (p[0].created_at, p[0].id) > (at, last_id)]
    else:
        chosen = sorted(
            decided,
            key=lambda p: (p[0].decided_at or p[0].created_at, p[0].id),
            reverse=True,
        )
        if cursor:
            at, last_id = _cursor.decode_desc(cursor, "appeals_decided")
            chosen = [
                p for p in chosen if (p[0].decided_at or p[0].created_at, p[0].id) < (at, last_id)
            ]
    page = chosen[: config.page_size]
    next_cursor = None
    if len(chosen) > config.page_size and page:
        last = page[-1][0]
        if queue == "open":
            next_cursor = _cursor.encode_desc("appeals_open", last.created_at, last.id)
        else:
            next_cursor = _cursor.encode_desc(
                "appeals_decided", last.decided_at or last.created_at, last.id
            )
    people = await identity.public_summaries(
        session,
        {a.user_id for a, _ in page} | {d for d in facts.deciders.values() if d is not None},
    )
    return AppealPage(
        items=[
            AppealRow(
                appeal_id=a.id,
                user=_ref(people, a.user_id),
                kind=n.kind.value,
                case_id=n.case_id,
                deed_count=len(n.items),
                created_at=a.created_at,
                original_decider=_ref(people, facts.deciders.get(n.id)),
                outcome=a.outcome.value if a.outcome else None,
                decided_at=a.decided_at,
            )
            for a, n in page
        ],
        counts=AppealCounts(open=len(open_pairs), decided=len(decided)),
        next_cursor=next_cursor,
    )


# --------------------------------------------------- #12 detail and decision


async def _visible_appeal(
    session: AsyncSession,
    admin_id: uuid.UUID,
    appeal_id: uuid.UUID,
    *,
    config: FraudConfig,
    now: datetime,
    lock: bool = False,
) -> tuple[Appeal, DecisionNotice, _Facts]:
    if lock:
        # Appeal row first, then its notice (the decision's lock order).
        locked = await session.scalar(
            select(Appeal.id).where(Appeal.id == appeal_id).with_for_update()
        )
        if locked is None:
            raise appeal_not_found()
        await session.execute(
            select(DecisionNotice.id)
            .join(Appeal, Appeal.notice_id == DecisionNotice.id)
            .where(Appeal.id == appeal_id)
            .with_for_update(of=DecisionNotice)
        )
    pairs = await _all_appeals(session, appeal_id=appeal_id)
    if not pairs:
        raise appeal_not_found()
    allowed, facts = await _reviewable(session, admin_id, pairs, config=config, now=now)
    if not allowed:
        raise appeal_not_found()
    appeal, notice = allowed[0]
    return appeal, notice, facts


async def appeal_detail(
    session: AsyncSession,
    admin_id: uuid.UUID,
    appeal_id: uuid.UUID,
    *,
    config: FraudConfig,
    now: datetime,
) -> AppealDetail:
    appeal, notice, facts = await _visible_appeal(
        session, admin_id, appeal_id, config=config, now=now
    )
    deed_ids = _notices.item_deed_ids(notice)
    views = await deeds.get_deeds(session, deed_ids)
    labels = await deeds.category_labels(session)
    decider = facts.deciders.get(notice.id)
    people = await identity.public_summaries(
        session,
        {appeal.user_id}
        | ({decider} if decider else set())
        | ({appeal.reviewer_id} if appeal.reviewer_id else set())
        | {v.reporter_id for v in views.values()}
        | {v.doer_id for v in views.values() if v.doer_id is not None},
    )
    items: list[AppealNoticeItem] = []
    for item in notice.items:
        view = views.get(uuid.UUID(str(item["deed_id"])))
        old, new = item.get("old_points"), item.get("new_points")
        items.append(
            AppealNoticeItem(
                deed_id=uuid.UUID(str(item["deed_id"])),
                effect=str(item.get("effect")),
                old_points=int(old) if isinstance(old, int) else None,
                new_points=int(new) if isinstance(new, int) else None,
                reporter=_ref(people, view.reporter_id if view else None),
                doer=_ref(people, view.doer_id if view else None),
                category_label=labels.get(view.category, view.category) if view else "",
                area_name=deeds.area_name(view) if view else "",
                occurred_on=view.occurred_at.astimezone(config.zone).date() if view else None,
                status=view.status.value if view else None,
            )
        )
    return AppealDetail(
        appeal_id=appeal.id,
        user=_ref(people, appeal.user_id),
        kind=notice.kind.value,
        case_id=notice.case_id,
        override_id=notice.override_id,
        reason=appeal.reason,
        created_at=appeal.created_at,
        original_decider=_ref(people, decider),
        notice=AppealNotice(
            notice_id=notice.id,
            created_at=notice.created_at,
            reason_key=notice.reason_key,
            reason_text=_member.reason_text(notice.reason_key, config),
            appeal_until=notice.appeal_until,
            final_at=notice.final_at,
            items=items,
        ),
        outcome=appeal.outcome.value if appeal.outcome else None,
        reviewer=_ref(people, appeal.reviewer_id) if appeal.reviewer_id else None,
        reviewer_note=appeal.reviewer_note,
        decided_at=appeal.decided_at,
    )


async def _held(session: AsyncSession, deed_ids: Iterable[uuid.UUID]) -> set[uuid.UUID]:
    ids = set(deed_ids)
    if not ids:
        return set()
    rows = await session.scalars(
        select(PrizeHold.deed_id)
        .where(PrizeHold.deed_id.in_(ids), PrizeHold.released_at.is_(None))
        .distinct()
    )
    return set(rows)


async def decide_appeal(
    session: AsyncSession,
    admin_id: uuid.UUID,
    appeal_id: uuid.UUID,
    data: AppealDecisionIn,
    *,
    config: FraudConfig,
    now: datetime,
) -> None:
    """Uphold or overturn (final). The caller commits."""
    note = clean_note(data.note, config)
    appeal, notice, _ = await _visible_appeal(
        session, admin_id, appeal_id, config=config, now=now, lock=True
    )
    if appeal.outcome is not None:
        raise Conflict("This appeal has already been decided.", code="appeal_decided")
    outcome = AppealOutcome(data.outcome)
    deed_ids = _notices.item_deed_ids(notice)

    released: list[uuid.UUID] = []
    if outcome == AppealOutcome.OVERTURNED and notice.case_id is not None:
        before = await _held(session, deed_ids)
        await session.execute(
            update(PrizeHold)
            .where(
                PrizeHold.case_id == notice.case_id,
                PrizeHold.deed_id.in_(deed_ids),
                PrizeHold.released_at.is_(None),
            )
            .values(released_at=now, release_reason=HoldReleaseReason.APPEAL_OVERTURNED)
        )
        await session.flush()
        released = sorted(before - await _held(session, deed_ids))

    appeal.outcome = outcome
    appeal.reviewer_id = admin_id
    appeal.reviewer_note = note
    appeal.decided_at = now
    if notice.final_at is None:
        notice.final_at = now
    entry = record_audit(
        session,
        entity_type=AUDIT_ENTITY,
        entity_id=str(appeal.id),
        action=f"appeal.{outcome.value}",
        actor_id=admin_id,
        old_value={"outcome": None},
        new_value={
            "outcome": outcome.value,
            "notice_id": str(notice.id),
            "kind": notice.kind.value,
            "case_id": str(notice.case_id) if notice.case_id else None,
            "override_id": str(notice.override_id) if notice.override_id else None,
            "user_id": str(appeal.user_id),
            "released_deed_ids": [str(d) for d in released],
        },
        reason=note,
    )
    entry.created_at = now
    await session.flush()

    if notice.case_id is not None:
        for deed_id in released:
            emit(session, DeedHoldChanged(deed_id=deed_id, held=False, case_id=notice.case_id))
    emit(
        session,
        AppealDecided(
            appeal_id=appeal.id,
            notice_kind=notice.kind.value,
            case_id=notice.case_id,
            override_id=notice.override_id,
            user_id=appeal.user_id,
            outcome=outcome.value,
            reviewer_id=admin_id,
        ),
    )
