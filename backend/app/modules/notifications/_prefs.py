"""Preferences and mutes (docs/M6-DESIGN.md §3.2 N5 to N7, D107, D113).

- A category is on by its config default until the person chooses; *Account & safety* and
  *Reviews* are locked on (409 ``category_locked``), an unknown category is 422
  ``unknown_category``.
- Mutes are silent (the other person is never told), per person (by handle) or per deed. A
  person who doesn't exist or isn't active, and a deed the caller can't see, are 404. At most
  ``max_mutes`` per person (409 ``too_many_mutes``); muting again or unmuting something not
  muted changes nothing.
"""

import uuid
from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, Conflict, NotFound
from app.modules.deeds import service as deeds
from app.modules.feed.service import visible_deed_ids
from app.modules.identity import service as identity
from app.modules.notifications._catalogue import CATEGORY_INFO, CATEGORY_ORDER
from app.modules.notifications._limits import NotificationsConfig
from app.modules.notifications._render import UNAVAILABLE_DEED, names, user_refs
from app.modules.notifications.models import (
    MuteTarget,
    NotificationCategory,
    NotificationMute,
    NotificationPreference,
)
from app.modules.notifications.schemas import (
    CategoryOut,
    MutedDeed,
    MuteIn,
    Mutes,
    PreferenceIn,
    Preferences,
)


class Invalid(AppError):
    status_code = 422


def _person_not_found() -> NotFound:
    return NotFound("We couldn't find that person.", code="user_not_found")


def _deed_not_found() -> NotFound:
    return NotFound("We couldn't find that deed.", code="deed_not_found")


async def preferences(
    session: AsyncSession, user_id: uuid.UUID, config: NotificationsConfig
) -> Preferences:
    """N5: every category with its state, then the person's mutes (newest first)."""
    result = await session.execute(
        select(NotificationPreference.category, NotificationPreference.enabled).where(
            NotificationPreference.user_id == user_id
        )
    )
    chosen: dict[NotificationCategory, bool] = {row[0]: row[1] for row in result}
    categories = []
    for category in CATEGORY_ORDER:
        rule = config.categories[category]
        info = CATEGORY_INFO[category]
        on = True if rule.locked else chosen.get(category, rule.default)
        categories.append(
            CategoryOut(
                id=category.value,
                label=info.label,
                description=info.description,
                on=on,
                locked=rule.locked,
            )
        )
    return Preferences(categories=categories, mutes=await _mutes(session, user_id))


async def _mutes(session: AsyncSession, user_id: uuid.UUID) -> Mutes:
    rows = await session.execute(
        select(NotificationMute.target_type, NotificationMute.target_id)
        .where(NotificationMute.user_id == user_id)
        .order_by(NotificationMute.created_at.desc(), NotificationMute.target_id)
    )
    people_ids: list[uuid.UUID] = []
    deed_ids: list[uuid.UUID] = []
    for target_type, target_id in rows:
        (people_ids if target_type == MuteTarget.PERSON else deed_ids).append(target_id)
    refs = await user_refs(session, people_ids)
    return Mutes(
        people=[refs[p] for p in people_ids],
        deeds=[
            MutedDeed(deed_id=d, title_text=text)
            for d, text in (await _deed_titles(session, user_id, deed_ids)).items()
        ],
    )


async def _deed_titles(
    session: AsyncSession, viewer_id: uuid.UUID, deed_ids: list[uuid.UUID]
) -> dict[uuid.UUID, str]:
    """ "Animals: the deed about Ahmed" for a deed the viewer can still see, else "This deed is
    no longer available" (never says why)."""
    visible = await visible_deed_ids(session, viewer_id, deed_ids)
    views = await deeds.get_deeds(session, visible)
    labels = await deeds.category_labels(session) if views else {}
    doers = await user_refs(session, {v.doer_id for v in views.values() if v.doer_id})
    titles: dict[uuid.UUID, str] = {}
    for deed_id in deed_ids:
        view = views.get(deed_id)
        if view is None or view.doer_id is None:
            titles[deed_id] = UNAVAILABLE_DEED
            continue
        label = labels.get(view.category, view.category)
        who = "you" if view.doer_id == viewer_id else names([doers[view.doer_id]], 1)
        titles[deed_id] = f"{label}: the deed about {who}"
    return titles


def _category(raw: str) -> NotificationCategory:
    try:
        return NotificationCategory(raw)
    except ValueError as exc:
        raise Invalid("We don't know that kind of notification.", code="unknown_category") from exc


async def set_preference(
    session: AsyncSession,
    user_id: uuid.UUID,
    body: PreferenceIn,
    config: NotificationsConfig,
    now: datetime,
) -> None:
    """N6. The caller commits."""
    category = _category(body.category)
    rule = config.categories[category]
    if rule.locked:
        if body.on:
            return
        raise Conflict(
            "These notifications are always on, so you never miss something important.",
            code="category_locked",
        )
    stmt = insert(NotificationPreference).values(
        user_id=user_id, category=category, enabled=body.on, updated_at=now
    )
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=["user_id", "category"],
            set_={"enabled": stmt.excluded.enabled, "updated_at": stmt.excluded.updated_at},
        )
    )


async def _target(
    session: AsyncSession, user_id: uuid.UUID, body: MuteIn, *, must_be_visible: bool
) -> tuple[MuteTarget, uuid.UUID]:
    if body.type == "person":
        person = await identity.get_user_by_handle(session, body.target)
        if person is None:
            raise _person_not_found()
        # Muting needs an active account (``unmute`` removes an existing mute first).
        if must_be_visible and not await identity.is_active(session, person.id):
            raise _person_not_found()
        return MuteTarget.PERSON, person.id
    try:
        deed_id = uuid.UUID(body.target)
    except ValueError as exc:
        raise _deed_not_found() from exc
    if must_be_visible and deed_id not in await visible_deed_ids(session, user_id, [deed_id]):
        raise _deed_not_found()
    return MuteTarget.DEED, deed_id


async def mute(
    session: AsyncSession,
    user_id: uuid.UUID,
    body: MuteIn,
    config: NotificationsConfig,
    now: datetime,
) -> None:
    """N7 PUT. The caller commits."""
    target_type, target_id = await _target(session, user_id, body, must_be_visible=True)
    existing = await session.get(NotificationMute, (user_id, target_type, target_id))
    if existing is not None:
        return
    count = await session.scalar(select(func.count()).where(NotificationMute.user_id == user_id))
    if (count or 0) >= config.max_mutes:
        raise Conflict(
            "You've muted a lot already. Unmute someone or a deed first.",
            code="too_many_mutes",
        )
    await session.execute(
        insert(NotificationMute)
        .values(user_id=user_id, target_type=target_type, target_id=target_id, created_at=now)
        .on_conflict_do_nothing()
    )


async def unmute(session: AsyncSession, user_id: uuid.UUID, body: MuteIn) -> None:
    """N7 DELETE: a deed muted earlier can be unmuted even if it is no longer visible. The
    caller commits."""
    if body.type == "deed":
        try:
            deed_id = uuid.UUID(body.target)
        except ValueError as exc:
            raise _deed_not_found() from exc
        removed = await session.execute(
            delete(NotificationMute)
            .where(
                NotificationMute.user_id == user_id,
                NotificationMute.target_type == MuteTarget.DEED,
                NotificationMute.target_id == deed_id,
            )
            .returning(NotificationMute.target_id)
        )
        if removed.first() is None:
            await _target(session, user_id, body, must_be_visible=True)
        return
    # A person: 204 when a mute of that handle was removed, whatever the account's state now;
    # otherwise the same answer as muting (404 unless the account is active), so unknown and
    # suspended handles can't be told apart (SEC6-L4).
    person = await identity.get_user_by_handle(session, body.target)
    if person is not None:
        removed = await session.execute(
            delete(NotificationMute)
            .where(
                NotificationMute.user_id == user_id,
                NotificationMute.target_type == MuteTarget.PERSON,
                NotificationMute.target_id == person.id,
            )
            .returning(NotificationMute.target_id)
        )
        if removed.first() is not None:
            return
    await _target(session, user_id, body, must_be_visible=True)
