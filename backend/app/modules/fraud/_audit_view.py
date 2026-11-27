"""The audit-log view (docs/M5-DESIGN.md D96, §3.3 #8): read-only, admin-only, newest first.

Reads ``audit_log`` (``app.core.audit``) with exact-match filters, a ``from``/``to`` range
(both inclusive; a naive time is UTC) of at most 366 days, and a keyset cursor on
``(created_at, id)``. An admin sees their own actions too (§3.5). When only ``from`` is given,
the range ends now.

Conflicts (D90, PO Q82, SEC5-M1): rows about a case the admin is conflicted on are left out,
exactly like the case queue (the same ``_coi.visibility``): the case's own rows
(``flag_case``: park, explanation requests, decisions), its decision notices
(``decision_notice``), appeals of those notices (``appeal``) and any other row that names the
case in ``old_value``/``new_value`` ``case_id`` (deed rejections, overrides, revocations).
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import ColumnElement, String, and_, cast, false, func, or_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditLog
from app.core.errors import AppError
from app.modules.fraud import _appeals, _coi, _cursor, _notices
from app.modules.fraud._common import user_not_found
from app.modules.fraud._limits import FraudConfig
from app.modules.fraud.models import Appeal, DecisionNotice, FlagCase
from app.modules.fraud.schemas import AuditItem, AuditPage, UserRef
from app.modules.identity import service as identity

MAX_RANGE = timedelta(days=366)
_KIND = "audit"


def invalid_range() -> AppError:
    return AppError(
        "Choose a date range of at most 366 days, starting before it ends.", code="invalid_range"
    )


def _utc(at: datetime | None) -> datetime | None:
    if at is None:
        return None
    return at.replace(tzinfo=UTC) if at.tzinfo is None else at


def _hidden(visibility: _coi.Visibility) -> ColumnElement[bool]:
    """Audit rows about a case hidden from the admin (never NULL)."""
    hidden_cases = select(cast(FlagCase.id, String)).where(~visibility.condition())
    hidden_notices = (
        select(cast(DecisionNotice.id, String))
        .join(FlagCase, FlagCase.id == DecisionNotice.case_id)
        .where(~visibility.condition())
    )
    hidden_appeals = (
        select(cast(Appeal.id, String))
        .join(DecisionNotice, DecisionNotice.id == Appeal.notice_id)
        .join(FlagCase, FlagCase.id == DecisionNotice.case_id)
        .where(~visibility.condition())
    )
    named = func.coalesce(
        AuditLog.new_value["case_id"].astext, AuditLog.old_value["case_id"].astext
    )
    return func.coalesce(
        or_(
            and_(AuditLog.entity_type == "flag_case", AuditLog.entity_id.in_(hidden_cases)),
            and_(
                AuditLog.entity_type == _notices.NOTICE_AUDIT_ENTITY,
                AuditLog.entity_id.in_(hidden_notices),
            ),
            and_(
                AuditLog.entity_type == _appeals.AUDIT_ENTITY,
                AuditLog.entity_id.in_(hidden_appeals),
            ),
            named.in_(hidden_cases),
        ),
        false(),
    )


async def audit_page(
    session: AsyncSession,
    admin_id: uuid.UUID,
    *,
    entity_type: str | None,
    entity_id: str | None,
    actor_handle: str | None,
    action: str | None,
    since: datetime | None,
    until: datetime | None,
    cursor: str | None,
    config: FraudConfig,
    now: datetime,
) -> AuditPage:
    since, until = _utc(since), _utc(until)
    if since is not None:
        end = until or now
        if since > end or end - since > MAX_RANGE:
            raise invalid_range()

    visibility = await _coi.visibility(session, admin_id, config, now)
    stmt = select(AuditLog).where(~_hidden(visibility))
    if entity_type:
        stmt = stmt.where(AuditLog.entity_type == entity_type)
    if entity_id:
        stmt = stmt.where(AuditLog.entity_id == entity_id)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if actor_handle:
        actor = await identity.get_user_by_handle(session, actor_handle.strip().lstrip("@"))
        if actor is None:
            raise user_not_found()
        stmt = stmt.where(AuditLog.actor_id == actor.id)
    if since is not None:
        stmt = stmt.where(AuditLog.created_at >= since)
    if until is not None:
        stmt = stmt.where(AuditLog.created_at <= until)
    if cursor:
        at, last_id = _cursor.decode_desc(cursor, _KIND)
        stmt = stmt.where(tuple_(AuditLog.created_at, AuditLog.id) < tuple_(at, last_id))
    rows = list(
        await session.scalars(
            stmt.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(
                config.page_size + 1
            )
        )
    )
    more = len(rows) > config.page_size
    rows = rows[: config.page_size]

    actor_ids: set[uuid.UUID] = {r.actor_id for r in rows if r.actor_id is not None}
    people = await identity.public_summaries(session, actor_ids)

    def actor_ref(actor_id: uuid.UUID | None) -> UserRef | None:
        person = people.get(actor_id) if actor_id is not None else None
        if person is None:
            return None
        return UserRef(
            handle=person.handle, display_name=person.display_name, avatar=person.avatar_ref
        )

    items = [
        AuditItem(
            id=r.id,
            created_at=r.created_at,
            entity_type=r.entity_type,
            entity_id=r.entity_id,
            action=r.action,
            actor=actor_ref(r.actor_id),
            reason=r.reason,
            old_value=r.old_value,
            new_value=r.new_value,
        )
        for r in rows
    ]
    next_cursor = (
        _cursor.encode_desc(_KIND, rows[-1].created_at, rows[-1].id) if more and rows else None
    )
    return AuditPage(items=items, next_cursor=next_cursor)
