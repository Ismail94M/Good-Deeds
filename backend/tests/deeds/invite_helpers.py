"""Helpers for invite, edit and withdraw tests (B6, B11). Service-level calls take ``at`` so
tests control the clock."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditLog
from app.modules.deeds import _edits, _invites
from app.modules.deeds._limits import load_limits
from app.modules.deeds.models import Invite, InviteSentVia
from app.modules.deeds.schemas import DeedOut, InviteDeedCreate, Location
from app.modules.identity.models import User, UserDevice
from app.modules.identity.service import device_hash
from tests.deeds.helpers import Member, location


def token_of(out: DeedOut) -> str:
    assert out.invite is not None and out.invite.url is not None
    return out.invite.url.rsplit("/", 1)[1]


def invite_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "method": "invite",
        "category": "animals",
        "description": "Helped a stray dog across the road",
        "beneficiaries_count": 1,
        "occurred_at": (datetime.now(UTC) - timedelta(hours=3)).isoformat(),
        "location": None,
        "sent_via": "whatsapp",
    }
    body.update(overrides)
    return body


async def create_invite(
    session: AsyncSession,
    reporter: Member,
    *,
    at: datetime | None = None,
    occurred_at: datetime | None = None,
    north_m: float | None = None,
    east_m: float = 0.0,
) -> tuple[DeedOut, str]:
    """An invite deed created at ``at``; with a location when ``north_m`` is given."""
    at = at or datetime.now(UTC)
    version, limits = await load_limits(session)
    data = InviteDeedCreate(
        method="invite",
        category="animals",
        description="Helped a stray dog across the road",
        occurred_at=occurred_at or at - timedelta(minutes=1),
        location=(
            None
            if north_m is None
            else Location.model_validate(location(north_m=north_m, east_m=east_m))
        ),
        sent_via=InviteSentVia.WHATSAPP,
    )
    out = await _invites.create_invite_deed(
        session, reporter.user_id, data, limits=limits, limits_version=version, now=at
    )
    return out, token_of(out)


async def accept(
    session: AsyncSession,
    token: str,
    accepter: Member,
    *,
    at: datetime | None = None,
    device_id: str | None = None,
) -> DeedOut:
    version, limits = await load_limits(session)
    user = await session.get(User, accepter.user_id)
    assert user is not None
    return await _invites.accept(
        session,
        token,
        accepter_id=accepter.user_id,
        accepter_phone_e164=user.phone_e164,
        device_id=device_id,
        limits=limits,
        limits_version=version,
        now=at,
    )


async def withdraw(
    session: AsyncSession, reporter: Member, deed_id: uuid.UUID, *, at: datetime | None = None
) -> DeedOut:
    return await _edits.withdraw_deed(session, reporter.user_id, deed_id, now=at)


async def share_device(
    session: AsyncSession, *people: Member, device: str = "family-phone"
) -> None:
    """Record the same device for each member (what a sign-in on that device does)."""
    session.add_all(UserDevice(user_id=m.user_id, device_hash=device_hash(device)) for m in people)
    await session.flush()


async def invite_row(session: AsyncSession, deed_id: uuid.UUID) -> Invite:
    invite = (
        await session.scalars(
            select(Invite)
            .where(Invite.deed_id == deed_id)
            .execution_options(populate_existing=True)
        )
    ).one()
    return invite


async def audits(session: AsyncSession, deed_id: uuid.UUID, action: str) -> list[AuditLog]:
    rows = await session.scalars(
        select(AuditLog).where(
            AuditLog.entity_type == "deed",
            AuditLog.entity_id == str(deed_id),
            AuditLog.action == action,
        )
    )
    return list(rows)
