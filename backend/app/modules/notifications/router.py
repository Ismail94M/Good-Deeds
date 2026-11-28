"""Notifications HTTP routes (docs/M6-DESIGN.md §3.2, N1 to N7), mounted under /api.

Every route needs ``CurrentUser`` and touches the caller's own data only (another person's
notification is 404). Reads (N1, N2, N3, N5) are limited per account by
``notifications.rate_read``, writes (N4, N6, N7) by ``notifications.rate_write``. Routers stay
thin: limit, call the service, commit.
"""

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.ratelimit import Limiter
from app.modules.identity.service import CurrentUser
from app.modules.notifications import service
from app.modules.notifications._limits import NotificationsConfig
from app.modules.notifications.schemas import (
    MuteIn,
    NotificationPage,
    PreferenceIn,
    Preferences,
    UnreadCount,
)

router = APIRouter(prefix="/notifications", tags=["notifications"])
Session = Annotated[AsyncSession, Depends(get_session)]


def _now() -> datetime:
    return datetime.now(UTC)


async def _read(session: AsyncSession, limiter: Limiter, user_id: uuid.UUID) -> NotificationsConfig:
    _, config = await service.load_notifications(session)
    await limiter.hit(f"notifications:read:u:{user_id}", config.rate_read)
    return config


async def _write(
    session: AsyncSession, limiter: Limiter, user_id: uuid.UUID
) -> NotificationsConfig:
    _, config = await service.load_notifications(session)
    await limiter.hit(f"notifications:write:u:{user_id}", config.rate_write)
    return config


@router.get("")
async def list_notifications(
    user: CurrentUser,
    session: Session,
    limiter: Limiter,
    cursor: Annotated[str | None, Query(max_length=300)] = None,
    unread: bool = False,
) -> NotificationPage:
    config = await _read(session, limiter, user.id)
    return await service.list_page(
        session,
        viewer_id=user.id,
        viewer_handle=user.handle,
        unread_only=unread,
        cursor=cursor,
        config=config,
    )


@router.get("/unread-count")
async def unread_count(user: CurrentUser, session: Session, limiter: Limiter) -> UnreadCount:
    config = await _read(session, limiter, user.id)
    return await service.unread_count(session, user.id, config)


@router.post("/seen", status_code=204)
async def seen(user: CurrentUser, session: Session, limiter: Limiter) -> Response:
    await _read(session, limiter, user.id)
    await service.mark_seen(session, user.id, _now())
    await session.commit()
    return Response(status_code=204)


@router.post("/read-all", status_code=204)
async def read_all(user: CurrentUser, session: Session, limiter: Limiter) -> Response:
    await _write(session, limiter, user.id)
    await service.mark_all_read(session, user.id, _now())
    await session.commit()
    return Response(status_code=204)


@router.post("/{notification_id}/read", status_code=204)
async def read_one(
    notification_id: uuid.UUID, user: CurrentUser, session: Session, limiter: Limiter
) -> Response:
    await _write(session, limiter, user.id)
    await service.mark_read(session, user.id, notification_id, _now())
    await session.commit()
    return Response(status_code=204)


@router.post("/{notification_id}/unread", status_code=204)
async def unread_one(
    notification_id: uuid.UUID, user: CurrentUser, session: Session, limiter: Limiter
) -> Response:
    await _write(session, limiter, user.id)
    await service.mark_unread(session, user.id, notification_id)
    await session.commit()
    return Response(status_code=204)


@router.get("/preferences")
async def get_preferences(user: CurrentUser, session: Session, limiter: Limiter) -> Preferences:
    config = await _read(session, limiter, user.id)
    return await service.preferences(session, user.id, config)


@router.put("/preferences")
async def put_preference(
    body: PreferenceIn, user: CurrentUser, session: Session, limiter: Limiter
) -> Preferences:
    config = await _write(session, limiter, user.id)
    await service.set_preference(session, user.id, body, config, _now())
    await session.commit()
    return await service.preferences(session, user.id, config)


@router.put("/mutes", status_code=204)
async def put_mute(body: MuteIn, user: CurrentUser, session: Session, limiter: Limiter) -> Response:
    config = await _write(session, limiter, user.id)
    await service.mute(session, user.id, body, config, _now())
    await session.commit()
    return Response(status_code=204)


@router.delete("/mutes", status_code=204)
async def delete_mute(
    body: MuteIn, user: CurrentUser, session: Session, limiter: Limiter
) -> Response:
    await _write(session, limiter, user.id)
    await service.unmute(session, user.id, body)
    await session.commit()
    return Response(status_code=204)
