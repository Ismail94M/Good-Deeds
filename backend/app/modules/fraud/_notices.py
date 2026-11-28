"""Decision notices for a confirmed case (docs/M5-DESIGN.md D100, D101, §3.3 "Decision
effects").

One ``decision_notices`` row per subject with an adverse item (``exclude`` or ``reject`` on a
deed they reported or did). Items hold ids and effect codes only: ``excluded`` (not counted for
leaderboards) and ``pending_removal`` (removed once the decision is final). The member text comes
from ``member_reason_labels[reason_key]`` when the notice is read (``_member.py``).

A score override that lowers someone's points (scoring's ``PointsOverridden``) gets a notice of
kind ``override`` with one ``points_changed`` item (``create_override_notice``). Its decider is
recorded in an ``audit_log`` row (``decision_notice.created``), because ``fraud`` can't read
scoring's overrides (D78) and an appeal must go to a different admin (D100). The row also
names the override's case (``case_id``, when it cites one), so the audit view hides it from
admins conflicted with that case (Q82, review N3).

When an appeal against an override notice is overturned, scoring answers with
``OverrideAppealApplied``; ``record_override_appeal`` keeps its ``restored`` flag in an
``audit_log`` row (``appeal.override_applied`` on the appeal), which the member page reads to
say whether points came back (review N1).
"""

import uuid
from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditLog, record_audit
from app.modules.deeds import service as deeds
from app.modules.fraud._limits import FraudConfig
from app.modules.fraud._updates import review_update
from app.modules.fraud.models import DecisionNotice, DeedOutcome, NoticeKind

CASE_REASON_KEY = "case"
EXCLUDED = "excluded"
PENDING_REMOVAL = "pending_removal"
POINTS_CHANGED = "points_changed"
EFFECTS: dict[DeedOutcome, str] = {
    DeedOutcome.EXCLUDE: EXCLUDED,
    DeedOutcome.REJECT: PENDING_REMOVAL,
}
NOTICE_AUDIT_ENTITY = "decision_notice"
NOTICE_CREATED = "decision_notice.created"
APPEAL_AUDIT_ENTITY = "appeal"
OVERRIDE_APPEAL_APPLIED = "appeal.override_applied"


def adverse_people(
    actions: Mapping[uuid.UUID, DeedOutcome],
    views: Mapping[uuid.UUID, deeds.DeedView],
    subjects: set[uuid.UUID],
) -> dict[uuid.UUID, list[uuid.UUID]]:
    """Subject -> their deeds with an adverse outcome, oldest verification first. Only subjects
    get a notice (a notice and an explanation request are for a person in the case)."""
    out: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
    ordered = sorted(
        actions,
        key=lambda d: (
            (v.verified_at.isoformat() if (v := views.get(d)) and v.verified_at else ""),
            str(d),
        ),
    )
    for deed_id in ordered:
        if actions[deed_id] == DeedOutcome.RELEASE:
            continue
        view = views.get(deed_id)
        if view is None:
            continue
        for person in {view.reporter_id, view.doer_id}:
            if person is not None and person in subjects:
                out[person].append(deed_id)
    return dict(out)


def create_case_notices(
    session: AsyncSession,
    case_id: uuid.UUID,
    actions: Mapping[uuid.UUID, DeedOutcome],
    people: Mapping[uuid.UUID, list[uuid.UUID]],
    *,
    config: FraudConfig,
    now: datetime,
) -> int:
    """Add one notice per person in ``people`` (from ``adverse_people``). Returns how many."""
    appeal_until = now + timedelta(days=config.appeal.window_days)
    for user_id in sorted(people):
        items: list[dict[str, Any]] = [
            {"deed_id": str(d), "effect": EFFECTS[actions[d]]} for d in people[user_id]
        ]
        notice_id = uuid.uuid4()
        review_update(session, user_id, "decision_notice", notice_id)
        session.add(
            DecisionNotice(
                id=notice_id,
                user_id=user_id,
                kind=NoticeKind.CASE,
                case_id=case_id,
                items=items,
                reason_key=CASE_REASON_KEY,
                created_at=now,
                appeal_until=appeal_until,
            )
        )
    return len(people)


async def create_override_notice(
    session: AsyncSession,
    *,
    override_id: uuid.UUID,
    deed_id: uuid.UUID,
    user_id: uuid.UUID,
    old_points: int,
    new_points: int,
    reason_code: str,
    actor_id: uuid.UUID,
    config: FraudConfig,
    now: datetime,
    requested_by_id: uuid.UUID | None = None,
    case_id: uuid.UUID | None = None,
) -> uuid.UUID | None:
    """The notice for a lowering override (D91, D100), once per (override, person). Returns its
    id, or None when the points didn't go down or the notice already exists."""
    if new_points >= old_points:
        return None
    notice_id: uuid.UUID | None = await session.scalar(
        insert(DecisionNotice)
        .values(
            id=uuid.uuid4(),
            user_id=user_id,
            kind=NoticeKind.OVERRIDE,
            override_id=override_id,
            items=[
                {
                    "deed_id": str(deed_id),
                    "effect": POINTS_CHANGED,
                    "old_points": old_points,
                    "new_points": new_points,
                }
            ],
            reason_key=reason_code,
            created_at=now,
            appeal_until=now + timedelta(days=config.appeal.window_days),
        )
        .on_conflict_do_nothing(constraint="uq_decision_notices_override_user")
        .returning(DecisionNotice.id)
    )
    if notice_id is None:
        return None
    review_update(session, user_id, "decision_notice", notice_id)
    entry = record_audit(
        session,
        entity_type=NOTICE_AUDIT_ENTITY,
        entity_id=str(notice_id),
        action=NOTICE_CREATED,
        actor_id=actor_id,
        new_value={
            "kind": NoticeKind.OVERRIDE.value,
            "override_id": str(override_id),
            "deed_id": str(deed_id),
            "user_id": str(user_id),
            "old_points": old_points,
            "new_points": new_points,
            "reason_code": reason_code,
            "requested_by": str(requested_by_id) if requested_by_id else None,
            "case_id": str(case_id) if case_id else None,
        },
        reason=reason_code,
    )
    entry.created_at = now
    await session.flush()
    return notice_id


async def deciders(
    session: AsyncSession,
    notices: list[DecisionNotice],
    case_deciders: Mapping[uuid.UUID, uuid.UUID],
) -> dict[uuid.UUID, uuid.UUID | None]:
    """Notice id -> the admin who made the decision: the case's ``resolved_by`` for a case
    notice (``case_deciders``), the override's actor (from the audit row) for an override."""
    out: dict[uuid.UUID, uuid.UUID | None] = {}
    override_ids = [str(n.id) for n in notices if n.kind == NoticeKind.OVERRIDE]
    actors: dict[str, uuid.UUID | None] = {}
    if override_ids:
        for entity_id, actor_id in await session.execute(
            select(AuditLog.entity_id, AuditLog.actor_id).where(
                AuditLog.entity_type == NOTICE_AUDIT_ENTITY,
                AuditLog.action == NOTICE_CREATED,
                AuditLog.entity_id.in_(override_ids),
            )
        ):
            actors[entity_id] = actor_id
    for notice in notices:
        if notice.kind == NoticeKind.CASE and notice.case_id is not None:
            out[notice.id] = case_deciders.get(notice.case_id)
        else:
            out[notice.id] = actors.get(str(notice.id))
    return out


async def _override_notice_ids(
    session: AsyncSession, notices: list[DecisionNotice], key: str
) -> dict[uuid.UUID, uuid.UUID]:
    """Override notice id -> the id stored under ``key`` in its ``decision_notice.created``
    audit row (notices without one are left out)."""
    ids = [str(n.id) for n in notices if n.kind == NoticeKind.OVERRIDE]
    if not ids:
        return {}
    out: dict[uuid.UUID, uuid.UUID] = {}
    for entity_id, new_value in await session.execute(
        select(AuditLog.entity_id, AuditLog.new_value).where(
            AuditLog.entity_type == NOTICE_AUDIT_ENTITY,
            AuditLog.action == NOTICE_CREATED,
            AuditLog.entity_id.in_(ids),
        )
    ):
        value = (new_value or {}).get(key)
        if value:
            out[uuid.UUID(entity_id)] = uuid.UUID(str(value))
    return out


async def override_requesters(
    session: AsyncSession, notices: list[DecisionNotice]
) -> dict[uuid.UUID, uuid.UUID]:
    """Override notice id -> the admin who asked for a second-admin-approved cut (Q77). They
    took part in the decision, so they may not decide its appeal either."""
    return await _override_notice_ids(session, notices, "requested_by")


async def override_cases(
    session: AsyncSession, notices: list[DecisionNotice]
) -> dict[uuid.UUID, uuid.UUID]:
    """Override notice id -> the case its override cites (review N3)."""
    return await _override_notice_ids(session, notices, "case_id")


async def record_override_appeal(
    session: AsyncSession,
    *,
    appeal_id: uuid.UUID,
    override_id: uuid.UUID,
    deed_id: uuid.UUID | None,
    restored: bool,
    case_id: uuid.UUID | None,
) -> None:
    """Keep scoring's answer to an overturned override appeal (review N1), once per appeal
    (the handler runs once per event)."""
    record_audit(
        session,
        entity_type=APPEAL_AUDIT_ENTITY,
        entity_id=str(appeal_id),
        action=OVERRIDE_APPEAL_APPLIED,
        actor_id=None,
        new_value={
            "override_id": str(override_id),
            "deed_id": str(deed_id) if deed_id else None,
            "restored": restored,
            "case_id": str(case_id) if case_id else None,
        },
        reason="Appeal overturned",
    )
    await session.flush()


async def override_appeal_restored(
    session: AsyncSession, appeal_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, bool]:
    """Appeal id -> whether scoring gave points back when it was overturned. Appeals scoring
    hasn't answered yet are left out."""
    ids = [str(a) for a in appeal_ids]
    if not ids:
        return {}
    out: dict[uuid.UUID, bool] = {}
    for entity_id, new_value in await session.execute(
        select(AuditLog.entity_id, AuditLog.new_value).where(
            AuditLog.entity_type == APPEAL_AUDIT_ENTITY,
            AuditLog.action == OVERRIDE_APPEAL_APPLIED,
            AuditLog.entity_id.in_(ids),
        )
    ):
        out[uuid.UUID(entity_id)] = bool((new_value or {}).get("restored"))
    return out


def item_deed_ids(notice: DecisionNotice) -> list[uuid.UUID]:
    return [uuid.UUID(str(item["deed_id"])) for item in notice.items]
