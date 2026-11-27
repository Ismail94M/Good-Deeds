"""Writing notifications (docs/M6-DESIGN.md §4.2, D106, D108, D109, D113, D115).

``notify`` runs in the event handler's transaction (the caller commits) and decides, in order:

1. Skip when the recipient is the actor (nobody is notified about their own action), is not an
   active account, has the category switched off (locked categories are always on, D107), or
   has muted the actor (Activity and Your network only) or the deed (likes, comments, replies).
2. A grouped kind (D108) counts each actor once per group: a ``notification_dedupe`` row
   ``{group_key}|{actor_id}`` is claimed first (unliking and liking again counts once). Then the
   unread row of the group takes the actor first (keeping ``actors_kept``), adds one to the count
   and moves ``updated_at``, so it rises to the top.
3. Otherwise (or when the group has no unread row) a new row is inserted; a non-grouped kind
   needs a ``dedupe_key``, and ``ON CONFLICT (user_id, dedupe_key) DO NOTHING`` makes a re-run
   harmless.
4. A new row of a capped category (Activity, Your network) first takes one from
   ``notification_daily_counts`` for the IST day; over the cap no row is created, but existing
   groups still grow (D115). The actor's dedupe claim is then given back, so they can count once
   the day's cap allows.

Rows hold ids, codes and numbers only (D106): the text is built on read.
"""

import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import and_, delete, exists, or_, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.identity import service as identity
from app.modules.notifications._catalogue import (
    DEED_MUTABLE_KINDS,
    PERSON_MUTABLE_CATEGORIES,
    category_of,
    group_key,
)
from app.modules.notifications._limits import NotificationsConfig, load_notifications
from app.modules.notifications.models import (
    GROUPED_KINDS,
    MuteTarget,
    NotificationCategory,
    NotificationDailyCount,
    NotificationDedupe,
    NotificationKind,
    NotificationMute,
    NotificationPreference,
)


class Outcome(StrEnum):
    CREATED = "created"
    GROUPED = "grouped"
    # The row (or this actor in this group) was already written.
    DUPLICATE = "duplicate"
    SELF = "self"
    INACTIVE = "inactive"
    CATEGORY_OFF = "category_off"
    MUTED = "muted"
    CAPPED = "capped"


@dataclass(frozen=True)
class Notice:
    """One notification to write for one recipient."""

    kind: NotificationKind
    recipient_id: uuid.UUID
    # Who caused it; None for system rows (moderation outcomes, badges, reviews).
    actor_id: uuid.UUID | None = None
    deed_id: uuid.UUID | None = None
    # The other object (comment, photo, badge award, title, review item).
    ref_id: uuid.UUID | None = None
    # Ids, codes and numbers only.
    data: Mapping[str, Any] = field(default_factory=dict)
    # Required for non-grouped kinds, e.g. ``recognised:{deed_id}``.
    dedupe_key: str | None = None


def ist_day(now: datetime, config: NotificationsConfig) -> date:
    return now.astimezone(config.zone).date()


async def category_enabled(
    session: AsyncSession,
    user_id: uuid.UUID,
    category: NotificationCategory,
    config: NotificationsConfig,
) -> bool:
    """The person's choice for ``category``, else the config default. Locked: always on."""
    rule = config.categories[category]
    if rule.locked:
        return True
    chosen = await session.scalar(
        select(NotificationPreference.enabled).where(
            NotificationPreference.user_id == user_id,
            NotificationPreference.category == category,
        )
    )
    return rule.default if chosen is None else chosen


async def _muted(session: AsyncSession, notice: Notice, category: NotificationCategory) -> bool:
    targets = []
    if notice.actor_id is not None and category in PERSON_MUTABLE_CATEGORIES:
        targets.append(
            and_(
                NotificationMute.target_type == MuteTarget.PERSON,
                NotificationMute.target_id == notice.actor_id,
            )
        )
    if notice.deed_id is not None and notice.kind in DEED_MUTABLE_KINDS:
        targets.append(
            and_(
                NotificationMute.target_type == MuteTarget.DEED,
                NotificationMute.target_id == notice.deed_id,
            )
        )
    if not targets:
        return False
    found = await session.scalar(
        select(exists().where(NotificationMute.user_id == notice.recipient_id, or_(*targets)))
    )
    return bool(found)


async def _take_daily_slot(
    session: AsyncSession, user_id: uuid.UUID, day: date, category: NotificationCategory, cap: int
) -> bool:
    """Add one to the day's count if it is under ``cap``; False when the cap is reached."""
    values = insert(NotificationDailyCount).values(
        user_id=user_id, day=day, category=category, count=1
    )
    stmt = values.on_conflict_do_update(
        index_elements=["user_id", "day", "category"],
        set_={"count": NotificationDailyCount.count + 1},
        where=NotificationDailyCount.count < cap,
    ).returning(NotificationDailyCount.count)
    return (await session.execute(stmt)).scalar_one_or_none() is not None


async def _claim(session: AsyncSession, user_id: uuid.UUID, key: str, now: datetime) -> bool:
    stmt = (
        insert(NotificationDedupe)
        .values(user_id=user_id, key=key, created_at=now)
        .on_conflict_do_nothing()
        .returning(NotificationDedupe.key)
    )
    return (await session.execute(stmt)).scalar_one_or_none() is not None


async def _release(session: AsyncSession, user_id: uuid.UUID, key: str) -> None:
    await session.execute(
        delete(NotificationDedupe).where(
            NotificationDedupe.user_id == user_id, NotificationDedupe.key == key
        )
    )


# The newest actor first, without repeating them, at most ``keep`` ids.
_FOLD = (
    "actor_ids = (ARRAY[CAST(:actor AS uuid)] || "
    "array_remove({table}actor_ids, CAST(:actor AS uuid)))[1:CAST(:keep AS integer)], "
    "actor_count = {table}actor_count + 1, "
    "updated_at = GREATEST({table}updated_at, CAST(:now AS timestamptz))"
)
_FOLD_UNREAD = text(
    "UPDATE notifications SET "
    + _FOLD.format(table="")
    + " WHERE user_id = :user_id AND group_key = :group_key AND read_at IS NULL RETURNING id"
)
_INSERT_GROUPED = text(
    "INSERT INTO notifications (id, user_id, kind, category, actor_ids, actor_count, deed_id, "
    "ref_id, data, group_key, created_at, updated_at) VALUES (:id, :user_id, :kind, :category, "
    "ARRAY[CAST(:actor AS uuid)], 1, :deed_id, :ref_id, CAST(:data AS jsonb), :group_key, "
    "CAST(:now AS timestamptz), CAST(:now AS timestamptz)) "
    "ON CONFLICT (user_id, group_key) WHERE read_at IS NULL AND group_key IS NOT NULL "
    "DO UPDATE SET " + _FOLD.format(table="notifications.") + " RETURNING (xmax = 0) AS inserted"
)
_INSERT_SINGLE = text(
    "INSERT INTO notifications (id, user_id, kind, category, actor_ids, actor_count, deed_id, "
    "ref_id, data, dedupe_key, created_at, updated_at) VALUES (:id, :user_id, :kind, :category, "
    "CAST(:actor_ids AS uuid[]), :actor_count, :deed_id, :ref_id, CAST(:data AS jsonb), "
    ":dedupe_key, CAST(:now AS timestamptz), CAST(:now AS timestamptz)) "
    "ON CONFLICT (user_id, dedupe_key) DO NOTHING RETURNING id"
)


async def _write_grouped(
    session: AsyncSession,
    notice: Notice,
    category: NotificationCategory,
    config: NotificationsConfig,
    now: datetime,
) -> Outcome:
    actor = notice.actor_id
    if actor is None:
        raise ValueError(f"{notice.kind.value} notifications need an actor")
    day = ist_day(now, config)
    group = group_key(notice.kind, notice.deed_id, day)
    assert group is not None
    claim = f"{group}|{actor}"
    if not await _claim(session, notice.recipient_id, claim, now):
        return Outcome.DUPLICATE
    params: dict[str, Any] = {
        "user_id": notice.recipient_id,
        "group_key": group,
        "actor": actor,
        "keep": config.actors_kept,
        "now": now,
    }
    if (await session.execute(_FOLD_UNREAD, params)).first() is not None:
        return Outcome.GROUPED
    cap = config.daily_cap(category)
    if cap is not None and not await _take_daily_slot(
        session, notice.recipient_id, day, category, cap
    ):
        await _release(session, notice.recipient_id, claim)
        return Outcome.CAPPED
    inserted = (
        await session.execute(
            _INSERT_GROUPED,
            {
                **params,
                "id": uuid.uuid4(),
                "kind": notice.kind.value,
                "category": category.value,
                "deed_id": notice.deed_id,
                "ref_id": notice.ref_id,
                "data": json.dumps(dict(notice.data)),
            },
        )
    ).scalar_one()
    return Outcome.CREATED if inserted else Outcome.GROUPED


async def _write_single(
    session: AsyncSession,
    notice: Notice,
    category: NotificationCategory,
    config: NotificationsConfig,
    now: datetime,
) -> Outcome:
    if notice.dedupe_key is None:
        raise ValueError(f"{notice.kind.value} notifications need a dedupe_key")
    cap = config.daily_cap(category)
    if cap is not None and not await _take_daily_slot(
        session, notice.recipient_id, ist_day(now, config), category, cap
    ):
        return Outcome.CAPPED
    actors = [] if notice.actor_id is None else [notice.actor_id]
    row = (
        await session.execute(
            _INSERT_SINGLE,
            {
                "id": uuid.uuid4(),
                "user_id": notice.recipient_id,
                "kind": notice.kind.value,
                "category": category.value,
                "actor_ids": "{" + ",".join(str(a) for a in actors) + "}",
                "actor_count": len(actors),
                "deed_id": notice.deed_id,
                "ref_id": notice.ref_id,
                "data": json.dumps(dict(notice.data)),
                "dedupe_key": notice.dedupe_key,
                "now": now,
            },
        )
    ).first()
    return Outcome.CREATED if row is not None else Outcome.DUPLICATE


async def notify(
    session: AsyncSession,
    notice: Notice,
    *,
    config: NotificationsConfig | None = None,
    now: datetime | None = None,
) -> Outcome:
    """Write (or fold) one notification, or skip it; see the module docstring. The caller
    commits. ``config`` and ``now`` let a batch (the network fan-out) load them once."""
    if config is None:
        _, config = await load_notifications(session)
    now = now or datetime.now(UTC)
    category = category_of(notice.kind)
    if notice.actor_id is not None and notice.actor_id == notice.recipient_id:
        return Outcome.SELF
    if not await identity.is_active(session, notice.recipient_id):
        return Outcome.INACTIVE
    if not await category_enabled(session, notice.recipient_id, category, config):
        return Outcome.CATEGORY_OFF
    if await _muted(session, notice, category):
        return Outcome.MUTED
    if notice.kind in GROUPED_KINDS:
        return await _write_grouped(session, notice, category, config, now)
    return await _write_single(session, notice, category, config, now)
