"""Reporter edit and withdrawal of a deed (docs/M2-DESIGN.md §3.2 #14, #15; §3.4b; D7, D13).

Only the content fields can be edited; the verification evidence (``occurred_at``, location,
doer, method, status) never changes. Every edit writes an append-only ``deed_revisions`` row.

Withdrawal is final and keeps the doer-reporter pair-day occupied. A pending deed's invite is
cancelled (at any time); a verified deed can be withdrawn only before ``verified_at +
withdraw_window_hours`` (M4 D64, checked under the deed row lock with server time) and leaves its
same-act group (``_grouping.withdraw_from_group``), which may promote the next report and revoke
invite deeds' prize-rank eligibility. Edits stay allowed after the window.

Lock order: invite row, then (withdrawal of a verified deed) ``same_act``, then deed row.
Acceptance takes invite row, deed row (still pending, so no grouping code touches it), then
``same_act``.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, Conflict, NotFound
from app.core.events import emit
from app.core.ratelimit import RateLimiter
from app.modules.deeds import _grouping
from app.modules.deeds._common import utcnow
from app.modules.deeds._invites import invite_out
from app.modules.deeds._limits import DeedLimits
from app.modules.deeds._views import can_withdraw_at, deed_out
from app.modules.deeds.events import DeedEdited, DeedWithdrawn
from app.modules.deeds.models import (
    Deed,
    DeedRevision,
    DeedStatus,
    Invite,
    InviteStatus,
    SameActGroup,
    VerificationEvent,
    VerificationEventKind,
)
from app.modules.deeds.schemas import DeedEdit, DeedOut

_CHANGEABLE = frozenset({DeedStatus.PENDING, DeedStatus.VERIFIED})


def _deed_not_found() -> NotFound:
    return NotFound("We couldn't find that deed.", code="deed_not_found")


async def limit_edit(limiter: RateLimiter, limits: DeedLimits, user_id: uuid.UUID, ip: str) -> None:
    """Edit and withdraw share one account key and one IP key."""
    await limiter.hit(f"deed:edit:u:{user_id}", limits.rate_deed_edit_user)
    await limiter.hit(f"deed:edit:ip:{ip}", limits.rate_deed_edit_ip)


async def _lock_own_deed(session: AsyncSession, reporter_id: uuid.UUID, deed_id: uuid.UUID) -> Deed:
    """The caller's own deed, row-locked. Anyone else's (the doer's too) is a 404."""
    deed = (
        await session.scalars(
            select(Deed)
            .where(Deed.id == deed_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).first()
    if deed is None or deed.reporter_user_id != reporter_id:
        raise _deed_not_found()
    return deed


async def _response(
    session: AsyncSession, deed: Deed, viewer_id: uuid.UUID, limits: DeedLimits, now: datetime
) -> DeedOut:
    invite = await session.scalar(select(Invite).where(Invite.deed_id == deed.id))
    return await deed_out(
        session,
        deed,
        viewer_id,
        invite=None if invite is None else invite_out(invite),
        window_hours=limits.withdraw_window_hours,
        now=now,
    )


# --------------------------------------------------- edit (§3.2 #14)


def _validated_changes(limits: DeedLimits, deed: Deed, data: DeedEdit) -> dict[str, Any]:
    """The sent fields that differ from the deed, checked against the config. Only changed
    fields are validated, so a later config change doesn't block unrelated edits."""
    sent = data.model_fields_set
    changes: dict[str, Any] = {}
    if "category" in sent and data.category is not None and data.category != deed.category:
        if not limits.has_category(data.category):
            raise AppError("Please choose a category from the list.", code="invalid_category")
        changes["category"] = data.category
    if "description" in sent and data.description is not None:
        text = data.description.strip()
        if text != deed.description:
            if not limits.description_min <= len(text) <= limits.description_max:
                raise AppError(
                    f"Please describe the deed in {limits.description_min} to "
                    f"{limits.description_max} characters.",
                    code="invalid_description",
                )
            changes["description"] = text
    if "beneficiaries_count" in sent and data.beneficiaries_count != deed.beneficiaries_count:
        count = data.beneficiaries_count
        if count is not None and count > limits.beneficiaries_max:
            raise AppError(
                f"People helped can be at most {limits.beneficiaries_max}.",
                code="invalid_beneficiaries",
            )
        changes["beneficiaries_count"] = count
    return changes


async def edit_deed(
    session: AsyncSession,
    reporter_id: uuid.UUID,
    deed_id: uuid.UUID,
    data: DeedEdit,
    *,
    limits: DeedLimits,
    now: datetime | None = None,
) -> DeedOut:
    now = now or utcnow()
    deed = await _lock_own_deed(session, reporter_id, deed_id)
    if deed.status not in _CHANGEABLE:
        raise Conflict("This deed can no longer be edited.", code="deed_not_editable")
    changes = _validated_changes(limits, deed, data)
    if not changes:
        return await _response(session, deed, reporter_id, limits, now)

    old = {field: getattr(deed, field) for field in changes}
    for field, value in changes.items():
        setattr(deed, field, value)
    deed.last_edited_at = now
    revision = DeedRevision(
        deed_id=deed.id, editor_user_id=reporter_id, old_values=old, new_values=changes
    )
    session.add(revision)
    await session.flush()
    emit(
        session,
        DeedEdited(deed_id=deed.id, revision_id=revision.id, changed_fields=sorted(changes)),
    )
    await session.flush()
    return await _response(session, deed, reporter_id, limits, now)


# --------------------------------------------------- withdraw (§3.2 #15, §3.4b)


async def withdraw_deed(
    session: AsyncSession,
    reporter_id: uuid.UUID,
    deed_id: uuid.UUID,
    *,
    limits: DeedLimits,
    now: datetime | None = None,
) -> DeedOut:
    # The invite row first (if any): accept and the expiry job lock it before the deed.
    invite = (
        await session.scalars(
            select(Invite)
            .where(Invite.deed_id == deed_id, Invite.reporter_user_id == reporter_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).first()
    # A verified deed's doer never changes, and a pending deed can't gain one while we hold its
    # invite. Take ``same_act`` before the deed's row lock: grouping holds ``same_act`` while it
    # updates other deeds in the group (prize-rank upgrade, promotion), so the reverse order
    # could deadlock.
    peek = await session.get(Deed, deed_id, populate_existing=True)
    if peek is not None and peek.reporter_user_id == reporter_id and peek.doer_user_id:
        await _grouping.lock_doer(session, peek.doer_user_id)
    deed = await _lock_own_deed(session, reporter_id, deed_id)
    # The clock is read only now, after every lock wait (SEC3 L4): a request that waited on a
    # lock past the window end is refused, keeping the window strictly half-open.
    now = now or utcnow()
    if deed.status not in _CHANGEABLE:
        raise Conflict("This deed can no longer be withdrawn.", code="deed_not_withdrawable")
    # The window (D64) is checked here, under the deed row lock, so it can't be raced.
    if not can_withdraw_at(deed, limits.withdraw_window_hours, now):
        raise Conflict(
            "You can no longer withdraw this deed. It was verified more than "
            f"{limits.withdraw_window_hours} hours ago.",
            code="withdraw_window_closed",
        )

    previous = deed.status
    deed.status = DeedStatus.WITHDRAWN
    deed.withdrawn_at = now
    group: SameActGroup | None = None
    if previous == DeedStatus.PENDING:
        # The link stops working: preview and accept return 410 invite_cancelled.
        if invite is not None and invite.status == InviteStatus.PENDING:
            invite.status = InviteStatus.CANCELLED
    else:
        group = await _grouping.withdraw_from_group(session, deed)

    session.add(
        VerificationEvent(
            deed_id=deed.id,
            kind=VerificationEventKind.WITHDRAWN,
            actor_user_id=reporter_id,
            evidence={"previous_status": previous.value},
        )
    )
    emit(
        session,
        DeedWithdrawn(
            deed_id=deed.id,
            reporter_id=reporter_id,
            doer_id=deed.doer_user_id,
            previous_status="pending" if previous == DeedStatus.PENDING else "verified",
            same_act_group_id=deed.same_act_group_id,
        ),
    )
    await session.flush()
    return await deed_out(
        session,
        deed,
        reporter_id,
        group=group,
        invite=None if invite is None else invite_out(invite),
        window_hours=limits.withdraw_window_hours,
        now=now,
    )
