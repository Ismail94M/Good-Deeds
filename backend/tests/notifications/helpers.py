"""Helpers for notifications tests (typed, so mypy strict covers them)."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.runtime_config import publish
from app.modules.identity.models import User, UserStatus
from app.modules.notifications._limits import NotificationsConfig, load_notifications
from app.modules.notifications._write import Notice, Outcome, notify
from app.modules.notifications.models import Notification, NotificationKind
from tests.deeds.helpers import Member


async def publish_notifications(session: AsyncSession, **changes: Any) -> NotificationsConfig:
    """Publish a new ``notifications`` config version (validated, rolled back with the test).
    Nested sections are merged: ``daily_caps={"activity": 2}`` changes that key only."""
    _, current = await load_notifications(session)
    values = current.model_dump(mode="json")
    for key, value in changes.items():
        if isinstance(value, dict) and isinstance(values.get(key), dict):
            values[key] = {**values[key], **value}
        else:
            values[key] = value
    new = NotificationsConfig.model_validate(values)
    await publish(session, new, actor_id=None, reason="test")
    await session.flush()
    return new


async def send(
    session: AsyncSession,
    kind: NotificationKind,
    to: Member,
    *,
    actor: Member | None = None,
    deed_id: uuid.UUID | None = None,
    ref_id: uuid.UUID | None = None,
    data: dict[str, Any] | None = None,
    dedupe_key: str | None = None,
    now: datetime | None = None,
) -> Outcome:
    if dedupe_key is None and kind in {
        NotificationKind.RECOGNISED,
        NotificationKind.DEED_VERIFIED,
        NotificationKind.BADGE_AWARDED,
        NotificationKind.TITLE_AWARDED,
        NotificationKind.PHOTO_REMOVED,
        NotificationKind.PHOTO_KEPT,
        NotificationKind.PHOTO_REMOVED_BY_DOER,
        NotificationKind.COMMENT_REMOVED,
        NotificationKind.AVATAR_REMOVED,
        NotificationKind.PASSWORD_CHANGED,
        NotificationKind.REVIEW_UPDATE,
    }:
        dedupe_key = f"{kind.value}:{uuid.uuid4()}"
    outcome = await notify(
        session,
        Notice(
            kind=kind,
            recipient_id=to.user_id,
            actor_id=None if actor is None else actor.user_id,
            deed_id=deed_id,
            ref_id=ref_id,
            data=data or {},
            dedupe_key=dedupe_key,
        ),
        now=now,
    )
    await session.flush()
    return outcome


async def rows_for(session: AsyncSession, member: Member) -> list[Notification]:
    found = await session.scalars(
        select(Notification)
        .where(Notification.user_id == member.user_id)
        .order_by(Notification.updated_at.desc(), Notification.id.desc())
        .execution_options(populate_existing=True)
    )
    return list(found)


async def set_status(session: AsyncSession, member: Member, status: UserStatus) -> None:
    user = await session.get(User, member.user_id)
    assert user is not None
    user.status = status
    await session.flush()


async def rename(session: AsyncSession, member: Member, name: str) -> Member:
    user = await session.get(User, member.user_id)
    assert user is not None
    user.display_name = name
    await session.flush()
    return Member(
        user_id=member.user_id,
        handle=member.handle,
        display_name=name,
        short_id=member.short_id,
        key_version=member.key_version,
        phone_hash=member.phone_hash,
        access=member.access,
    )
