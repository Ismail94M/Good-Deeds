"""Score overrides and big-cut requests (docs/M5-DESIGN.md D90, D91, Q77; §3.4 #13, #17, #18).

- An override sets one role's points for a deed (0 to ``fraud.override_max_points``) or clears
  the one in force (``points`` null). It is a ``score_overrides`` row; ``reconcile_deed`` applies
  it (``_ledger``), in the request transaction under the M4 day locks, so the ledger keeps one
  writer (D49). The role needs an award target (verified, counted, inside the daily cap), else
  409 ``nothing_to_override``.
- **Big cuts (Q77):** an override to 0 (when ``override_second_admin.cut_to_zero``) or one lowering
  points by more than ``override_second_admin.max_cut_points`` is stored as a pending
  ``override_requests`` row instead and changes nothing until a different, non-conflicted admin
  approves it. One pending request per (deed, role): while it waits, any other override of that
  role is 409 ``request_pending``. Requests expire after ``override_second_admin.expire_days``
  (job ``scoring.expire_override_requests``).
- Clearing never needs a second admin: it returns to the rules.
- Every applied override writes ``audit_log`` (``entity_type="deed_points"``, old and new points).
  One that lowers points emits ``PointsOverridden``, which ``fraud`` turns into a decision notice
  the person can appeal (D100). An overturned appeal writes a clearing override
  (``clear_for_appeal``, from the ``AppealDecided`` handler).
- Conflict of interest (D90): own deeds and deeds with a related person are 404.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import ColumnElement, and_, exists, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit
from app.core.errors import AppError, Conflict, NotFound
from app.core.events import emit
from app.modules.deeds import service as deeds
from app.modules.fraud.service import OverrideRules, case_standing, override_rules
from app.modules.identity import service as identity
from app.modules.scoring import _coi, _cursor
from app.modules.scoring._ledger import (
    RoleState,
    lock_deed_days,
    overrides_in_force,
    reconcile_deed,
    role_states,
)
from app.modules.scoring.events import PointsOverridden
from app.modules.scoring.models import (
    LedgerRole,
    OverrideRequest,
    OverrideRequestStatus,
    PointsLedger,
    ScoreOverride,
)
from app.modules.scoring.schemas import (
    OverrideDecisionIn,
    OverrideIn,
    OverrideRequestCounts,
    OverrideRequestOut,
    OverrideRequestPage,
    UserSummary,
)

AUDIT_ENTITY = "deed_points"
APPEAL_NOTE = "Appeal overturned"
APPEAL_REASON = "other"

RequestStatusFilter = Literal["pending", "decided"]


class Invalid(AppError):
    """422 for rules that depend on config (note length, reason codes, points range)."""

    status_code = 422


def _deed_not_found() -> NotFound:
    return NotFound("We couldn't find that deed.", code="deed_not_found")


def _request_not_found() -> NotFound:
    return NotFound("We couldn't find that request.", code="request_not_found")


def _nothing_to_override() -> Conflict:
    return Conflict(
        "This deed has no points for that role to change (it isn't verified, doesn't count, or "
        "is over the daily limit).",
        code="nothing_to_override",
    )


def clean_note(note: str, rules: OverrideRules) -> str:
    """The trimmed note, ``note_min_chars`` to ``note_max_chars`` long."""
    if "\x00" in note:
        raise Invalid("That text contains a character we can't accept.", code="invalid_text")
    text = note.strip()
    if len(text) < rules.note_min_chars:
        raise Invalid(
            f"Add a note of at least {rules.note_min_chars} characters.", code="note_required"
        )
    if len(text) > rules.note_max_chars:
        raise Invalid("The note is too long.", code="note_too_long")
    return text


def needs_second_admin(old: int, new: int | None, rules: OverrideRules) -> bool:
    """Q77: an override to 0, or a cut of more than ``max_cut_points``."""
    if new is None or new >= old:
        return False
    if new == 0 and rules.second_admin_cut_to_zero:
        return True
    return old - new > rules.second_admin_max_cut_points


async def _visible_inputs(
    session: AsyncSession,
    admin_id: uuid.UUID,
    deed_id: uuid.UUID,
    rules: OverrideRules,
    now: datetime,
) -> deeds.ScoringInputs:
    """The deed's inputs, or 404 for an unknown, pending or conflicted deed (D90)."""
    inputs = await deeds.scoring_inputs(session, deed_id)
    if inputs is None or inputs.doer_id is None:
        raise _deed_not_found()
    people = (inputs.doer_id, inputs.reporter_id)
    if await _coi.conflicted(session, admin_id, people, rules.coi_lookback_days, now):
        raise _deed_not_found()
    return inputs


async def _pending(
    session: AsyncSession, deed_id: uuid.UUID, role: LedgerRole
) -> OverrideRequest | None:
    return await session.scalar(
        select(OverrideRequest).where(
            OverrideRequest.deed_id == deed_id,
            OverrideRequest.role == role,
            OverrideRequest.status == OverrideRequestStatus.PENDING,
        )
    )


async def _active_points(session: AsyncSession, deed_id: uuid.UUID, role: LedgerRole) -> int:
    states = await _states(session, deed_id)
    return 0 if states is None else states[role].points


async def _states(session: AsyncSession, deed_id: uuid.UUID) -> dict[LedgerRole, RoleState] | None:
    """Both roles of the deed (None if it was never verified), read live. The caller holds the
    deed's day locks."""
    inputs = await deeds.scoring_inputs(session, deed_id)
    if inputs is None or inputs.doer_id is None or inputs.verified_at is None:
        return None
    return await role_states(session, inputs)


async def _apply(
    session: AsyncSession,
    *,
    deed_id: uuid.UUID,
    role: LedgerRole,
    user_id: uuid.UUID,
    old_points: int,
    points: int | None,
    reason_code: str,
    note: str,
    case_id: uuid.UUID | None,
    actor_id: uuid.UUID,
    now: datetime,
    request: OverrideRequest | None = None,
) -> ScoreOverride:
    """Write the override, re-score the deed, audit it and tell ``fraud`` when it lowered the
    points. The caller holds the deed's day locks."""
    in_force = (await overrides_in_force(session, deed_id)).get(role)
    override = ScoreOverride(
        id=uuid.uuid4(),
        deed_id=deed_id,
        role=role,
        points=points,
        previous_points=old_points,
        reason_code=reason_code,
        note=note,
        case_id=case_id,
        request_id=None if request is None else request.id,
        actor_id=actor_id,
        # Set here, not by the server default: ``now()`` is the transaction's start time, and
        # the latest row of a (deed, role) is the one in force.
        created_at=now,
    )
    session.add(override)
    await session.flush()
    await reconcile_deed(session, deed_id)
    new_points = await _active_points(session, deed_id, role)
    record_audit(
        session,
        entity_type=AUDIT_ENTITY,
        entity_id=str(deed_id),
        action="deed_points.override_cleared" if points is None else "deed_points.overridden",
        actor_id=actor_id,
        old_value={
            "role": role.value,
            "points": old_points,
            "override_points": None if in_force is None else in_force.points,
        },
        new_value={
            "role": role.value,
            "points": new_points,
            "override_points": points,
            "override_id": str(override.id),
            "reason_code": reason_code,
            "case_id": None if case_id is None else str(case_id),
            "request_id": None if request is None else str(request.id),
            "requested_by": None if request is None else str(request.requested_by),
        },
        reason=note,
    )
    if new_points < old_points:
        emit(
            session,
            PointsOverridden(
                override_id=override.id,
                deed_id=deed_id,
                role=role.value,
                user_id=user_id,
                old_points=old_points,
                new_points=new_points,
                reason_code=reason_code,
                actor_id=actor_id,
                requested_by_id=None if request is None else request.requested_by,
            ),
        )
    return override


# --------------------------------------------------- #13


async def create_override(
    session: AsyncSession,
    admin_id: uuid.UUID,
    deed_id: uuid.UUID,
    body: OverrideIn,
    *,
    now: datetime | None = None,
) -> ScoreOverride | OverrideRequest:
    """#13: apply the override (a ``ScoreOverride``), or store a big cut as a pending
    ``OverrideRequest`` (Q77). The caller commits."""
    now = now or datetime.now(UTC)
    rules = await override_rules(session)
    if body.reason_code not in rules.reason_codes:
        raise Invalid("Choose one of the listed reasons.", code="invalid_reason_code")
    note = clean_note(body.note, rules)
    if body.points is not None and not 0 <= body.points <= rules.max_points:
        raise Invalid(
            f"Points must be between 0 and {rules.max_points}.", code="points_out_of_range"
        )
    inputs = await _visible_inputs(session, admin_id, deed_id, rules, now)
    role = LedgerRole(body.role)
    user_id = inputs.doer_id if role == LedgerRole.DOER else inputs.reporter_id
    assert user_id is not None
    if body.case_id is not None and await case_standing(session, body.case_id, user_id) == (
        "unknown"
    ):
        raise Invalid("We couldn't find that case.", code="unknown_case")

    if await lock_deed_days(session, deed_id) is None:
        raise _nothing_to_override()
    states = await _states(session, deed_id)
    if states is None or states[role].rule_points is None:
        raise _nothing_to_override()
    state = states[role]
    if await _pending(session, deed_id, role) is not None:
        raise Conflict(
            "A change to these points is already waiting for a second admin.",
            code="request_pending",
        )
    in_force = (await overrides_in_force(session, deed_id)).get(role)
    current_override = None if in_force is None else in_force.points
    if (body.points is None and current_override is None) or body.points == state.points:
        raise Conflict("That wouldn't change anything.", code="override_unchanged")

    if needs_second_admin(state.points, body.points, rules):
        assert body.points is not None
        request = OverrideRequest(
            id=uuid.uuid4(),
            deed_id=deed_id,
            role=role,
            points=body.points,
            previous_points=state.points,
            reason_code=body.reason_code,
            note=note,
            case_id=body.case_id,
            requested_by=admin_id,
            created_at=now,
            expires_at=now + timedelta(days=rules.second_admin_expire_days),
            status=OverrideRequestStatus.PENDING,
        )
        session.add(request)
        await session.flush()
        record_audit(
            session,
            entity_type=AUDIT_ENTITY,
            entity_id=str(deed_id),
            action="deed_points.override_requested",
            actor_id=admin_id,
            old_value={"role": role.value, "points": state.points},
            new_value={
                "role": role.value,
                "points": body.points,
                "request_id": str(request.id),
                "reason_code": body.reason_code,
                "case_id": None if body.case_id is None else str(body.case_id),
            },
            reason=note,
        )
        return request

    return await _apply(
        session,
        deed_id=deed_id,
        role=role,
        user_id=state.user_id,
        old_points=state.points,
        points=body.points,
        reason_code=body.reason_code,
        note=note,
        case_id=body.case_id,
        actor_id=admin_id,
        now=now,
    )


# --------------------------------------------------- #17


async def _visibility(
    session: AsyncSession, admin_id: uuid.UUID, rules: OverrideRules, now: datetime
) -> ColumnElement[bool]:
    """Requests this admin may see and decide: not their own, and none about a deed of a person
    they are conflicted with (D90). Deeds with a request always have ledger rows (an award
    target existed), so the people are read from ``points_ledger``."""
    blocked = await _coi.blocked_users(session, admin_id, rules.coi_lookback_days, now)
    involved = exists().where(
        PointsLedger.deed_id == OverrideRequest.deed_id,
        or_(PointsLedger.user_id.in_(blocked), PointsLedger.counterpart_user_id.in_(blocked)),
    )
    return and_(OverrideRequest.requested_by != admin_id, ~involved)


def _status_condition(status: RequestStatusFilter) -> ColumnElement[bool]:
    if status == "pending":
        return OverrideRequest.status == OverrideRequestStatus.PENDING
    return OverrideRequest.status != OverrideRequestStatus.PENDING


async def requests_out(
    session: AsyncSession, rows: list[OverrideRequest]
) -> list[OverrideRequestOut]:
    views = await deeds.get_deeds(session, {r.deed_id for r in rows})

    def owner(r: OverrideRequest) -> uuid.UUID | None:
        view = views.get(r.deed_id)
        if view is None:
            return None
        return view.doer_id if r.role == LedgerRole.DOER else view.reporter_id

    owners = {r.id: owner(r) for r in rows}
    ids = {u for u in owners.values() if u is not None}
    ids |= {r.requested_by for r in rows} | {r.decided_by for r in rows if r.decided_by}
    found = await identity.public_summaries(session, ids)
    people = {
        uid: UserSummary(handle=s.handle, display_name=s.display_name) for uid, s in found.items()
    }

    def person(uid: uuid.UUID | None) -> UserSummary | None:
        return None if uid is None else people.get(uid)

    return [
        OverrideRequestOut(
            id=r.id,
            deed_id=r.deed_id,
            role=r.role.value,
            user=person(owners[r.id]),
            previous_points=r.previous_points,
            points=r.points,
            reason_code=r.reason_code,
            note=r.note,
            case_id=r.case_id,
            requested_by=person(r.requested_by),
            created_at=r.created_at,
            expires_at=r.expires_at,
            status=r.status.value,
            decided_by=person(r.decided_by),
            decision_note=r.decision_note,
            decided_at=r.decided_at,
            override_id=r.override_id,
        )
        for r in rows
    ]


async def list_requests(
    session: AsyncSession,
    admin_id: uuid.UUID,
    *,
    status: RequestStatusFilter,
    cursor: str | None,
    now: datetime | None = None,
) -> OverrideRequestPage:
    """#17: newest first, ``fraud.page_size`` per page."""
    now = now or datetime.now(UTC)
    rules = await override_rules(session)
    visible = await _visibility(session, admin_id, rules, now)
    stmt = select(OverrideRequest).where(visible, _status_condition(status))
    if cursor is not None:
        at, last_id = _cursor.decode_request(cursor)
        stmt = stmt.where(
            or_(
                OverrideRequest.created_at < at,
                and_(OverrideRequest.created_at == at, OverrideRequest.id < last_id),
            )
        )
    size = rules.page_size
    rows = list(
        await session.scalars(
            stmt.order_by(OverrideRequest.created_at.desc(), OverrideRequest.id.desc()).limit(
                size + 1
            )
        )
    )
    more = len(rows) > size
    rows = rows[:size]
    counts = {
        name: int(
            await session.scalar(
                select(func.count())
                .select_from(OverrideRequest)
                .where(visible, _status_condition(name))
            )
            or 0
        )
        for name in ("pending", "decided")
    }
    last = rows[-1] if rows else None
    return OverrideRequestPage(
        items=await requests_out(session, rows),
        counts=OverrideRequestCounts(pending=counts["pending"], decided=counts["decided"]),
        next_cursor=(
            _cursor.encode_request(last.created_at, last.id) if more and last is not None else None
        ),
    )


# --------------------------------------------------- #18


async def decide_request(
    session: AsyncSession,
    admin_id: uuid.UUID,
    request_id: uuid.UUID,
    body: OverrideDecisionIn,
    *,
    now: datetime | None = None,
) -> OverrideRequest:
    """#18: approve (apply the override) or decline (record the note, change nothing). 404 for
    the requester, a conflicted admin and an unknown request. The caller commits."""
    now = now or datetime.now(UTC)
    rules = await override_rules(session)
    note = clean_note(body.note, rules)
    request = await session.get(
        OverrideRequest, request_id, with_for_update=True, populate_existing=True
    )
    if request is None or request.requested_by == admin_id:
        raise _request_not_found()
    try:
        await _visible_inputs(session, admin_id, request.deed_id, rules, now)
    except NotFound as exc:
        raise _request_not_found() from exc
    if request.status == OverrideRequestStatus.EXPIRED or (
        request.status == OverrideRequestStatus.PENDING and request.expires_at <= now
    ):
        raise Conflict("This request has expired.", code="request_expired")
    if request.status != OverrideRequestStatus.PENDING:
        raise Conflict("This request has already been decided.", code="request_decided")

    if body.decision == "decline":
        request.status = OverrideRequestStatus.DECLINED
        request.decided_by = admin_id
        request.decision_note = note
        request.decided_at = now
        await session.flush()
        record_audit(
            session,
            entity_type=AUDIT_ENTITY,
            entity_id=str(request.deed_id),
            action="deed_points.override_declined",
            actor_id=admin_id,
            new_value={"role": request.role.value, "request_id": str(request.id)},
            reason=note,
        )
        return request

    if await lock_deed_days(session, request.deed_id) is None:
        raise _nothing_to_override()
    states = await _states(session, request.deed_id)
    if states is None or states[request.role].rule_points is None:
        raise _nothing_to_override()
    state = states[request.role]
    override = await _apply(
        session,
        deed_id=request.deed_id,
        role=request.role,
        user_id=state.user_id,
        old_points=state.points,
        points=request.points,
        reason_code=request.reason_code,
        note=request.note,
        case_id=request.case_id,
        actor_id=admin_id,
        now=now,
        request=request,
    )
    request.status = OverrideRequestStatus.APPROVED
    request.decided_by = admin_id
    request.decision_note = note
    request.decided_at = now
    request.override_id = override.id
    await session.flush()
    return request


async def expire_requests(session: AsyncSession, *, now: datetime | None = None) -> int:
    """Mark pending requests past ``expires_at`` as ``expired`` (job, hourly)."""
    now = now or datetime.now(UTC)
    result = await session.execute(
        update(OverrideRequest)
        .where(
            OverrideRequest.status == OverrideRequestStatus.PENDING,
            OverrideRequest.expires_at <= now,
        )
        .values(status=OverrideRequestStatus.EXPIRED, decided_at=now)
        .returning(OverrideRequest.id)
    )
    return len(result.all())


# --------------------------------------------------- appeals (D100)


async def clear_for_appeal(
    session: AsyncSession, override_id: uuid.UUID, reviewer_id: uuid.UUID
) -> ScoreOverride | None:
    """An appeal against ``override_id`` was overturned: clear it (a new row with ``points``
    null, reason ``other``, actor the reviewer) and re-score. Nothing happens when the override
    is not the one in force any more (cleared already, or replaced by a later override, which
    has its own notice), so a repeated event changes nothing."""
    appealed = await session.get(ScoreOverride, override_id)
    if appealed is None:
        return None
    await lock_deed_days(session, appealed.deed_id)
    in_force = (await overrides_in_force(session, appealed.deed_id)).get(appealed.role)
    if in_force is None or in_force.id != appealed.id or in_force.points is None:
        return None
    states = await _states(session, appealed.deed_id)
    old_points = 0 if states is None else states[appealed.role].points
    inputs = await deeds.scoring_inputs(session, appealed.deed_id)
    assert inputs is not None and inputs.doer_id is not None
    user_id = inputs.doer_id if appealed.role == LedgerRole.DOER else inputs.reporter_id
    return await _apply(
        session,
        deed_id=appealed.deed_id,
        role=appealed.role,
        user_id=user_id,
        old_points=old_points,
        points=None,
        reason_code=APPEAL_REASON,
        note=APPEAL_NOTE,
        case_id=appealed.case_id,
        actor_id=reviewer_id,
        now=datetime.now(UTC),
    )
