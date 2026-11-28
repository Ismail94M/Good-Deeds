"""Notification tables (docs/M6-DESIGN.md §2.1, migration 0030).

Invariants live in the database (migration 0030); services map ``IntegrityError`` to error codes
by constraint name, so names here are part of the contract.

- ``notifications``: one row per recipient. ``kind`` is one of the catalogue kinds (§4.1) and
  its ``category`` is fixed by the kind (CHECK ``kind_category``); exactly the grouped kinds
  carry a ``group_key`` (CHECK ``group_key_iff_grouped``, D108). ``UNIQUE (user_id,
  dedupe_key)`` makes a re-run harmless; at most one *unread* row per ``(user_id, group_key)``
  (partial unique ``uq_notifications_unread_group``), so new activity folds into it. Rows hold
  ids, codes and numbers only (D106): no names, deed text or coordinates.
- ``notification_views``: when the bell was last opened (upserted, like ``review_views``).
- ``notification_preferences``: one row per (user, category); a missing row means the config
  default. The service refuses to switch off a locked category.
- ``notification_mutes``: per person or per deed (D113).
- ``notification_dedupe``: (group, actor) pairs and the per-actor daily fan-out counter keys;
  purged after ``dedupe_days``.
- ``notification_fanouts``: one row per verified deed whose network fan-out started (D110);
  lets ``fanout_network`` resume from ``cursor`` and records when a cap was hit.
- ``notification_daily_counts``: per (user, IST day, category), for the caps in D115.

No table is append-only: rows are grouped, read, unread and purged. Foreign keys to ``users``
and ``deeds`` use RESTRICT and are named by string, so this module imports nothing from
``deeds`` or ``identity``. ``ref_id`` has no foreign key (comment, photo, badge award, title or
review item, owned by other modules).
"""

import uuid
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    PrimaryKeyConstraint,
    SmallInteger,
    String,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


def _enum(cls: type[StrEnum], name: str, length: int = 20) -> Enum:
    """A non-native enum stored as a string, with a CHECK constraint named ``ck_<table>_<name>``."""
    return Enum(
        cls,
        create_constraint=True,
        name=name,
        native_enum=False,
        length=length,
        values_callable=lambda e: [m.value for m in e],
    )


def _users_fk() -> ForeignKey:
    return ForeignKey("users.id", ondelete="RESTRICT")


def _deeds_fk() -> ForeignKey:
    return ForeignKey("deeds.id", ondelete="RESTRICT")


class NotificationCategory(StrEnum):
    """D107. ``account`` and ``reviews`` are locked on (config ``categories.*.locked``)."""

    ABOUT_YOU = "about_you"
    NETWORK = "network"
    ACTIVITY = "activity"
    ACCOUNT = "account"
    REVIEWS = "reviews"


class NotificationKind(StrEnum):
    """The catalogue (§4.1). Texts and links live in code and are built on read."""

    RECOGNISED = "recognised"
    DEED_VERIFIED = "deed_verified"
    NETWORK = "network"
    LIKE = "like"
    COMMENT = "comment"
    REPLY = "reply"
    FOLLOW = "follow"
    BADGE_AWARDED = "badge_awarded"
    TITLE_AWARDED = "title_awarded"
    PHOTO_REMOVED = "photo_removed"
    PHOTO_KEPT = "photo_kept"
    PHOTO_REMOVED_BY_DOER = "photo_removed_by_doer"
    COMMENT_REMOVED = "comment_removed"
    AVATAR_REMOVED = "avatar_removed"
    PASSWORD_CHANGED = "password_changed"
    REVIEW_UPDATE = "review_update"


# The category of each kind (§4.1); also the ``ck_notifications_kind_category`` CHECK.
KIND_CATEGORY: dict[NotificationKind, NotificationCategory] = {
    NotificationKind.RECOGNISED: NotificationCategory.ABOUT_YOU,
    NotificationKind.DEED_VERIFIED: NotificationCategory.ABOUT_YOU,
    NotificationKind.NETWORK: NotificationCategory.NETWORK,
    NotificationKind.LIKE: NotificationCategory.ACTIVITY,
    NotificationKind.COMMENT: NotificationCategory.ACTIVITY,
    NotificationKind.REPLY: NotificationCategory.ACTIVITY,
    NotificationKind.FOLLOW: NotificationCategory.ACTIVITY,
    NotificationKind.BADGE_AWARDED: NotificationCategory.ACCOUNT,
    NotificationKind.TITLE_AWARDED: NotificationCategory.ACCOUNT,
    NotificationKind.PHOTO_REMOVED: NotificationCategory.ACCOUNT,
    NotificationKind.PHOTO_KEPT: NotificationCategory.ACCOUNT,
    NotificationKind.PHOTO_REMOVED_BY_DOER: NotificationCategory.ACCOUNT,
    NotificationKind.COMMENT_REMOVED: NotificationCategory.ACCOUNT,
    NotificationKind.AVATAR_REMOVED: NotificationCategory.ACCOUNT,
    NotificationKind.PASSWORD_CHANGED: NotificationCategory.ACCOUNT,
    NotificationKind.REVIEW_UPDATE: NotificationCategory.REVIEWS,
}

# Kinds folded into one unread row per group (D108): per deed (like, comment, reply) or per day
# (follow, network). Exactly these carry a ``group_key``.
GROUPED_KINDS: frozenset[NotificationKind] = frozenset(
    {
        NotificationKind.NETWORK,
        NotificationKind.LIKE,
        NotificationKind.COMMENT,
        NotificationKind.REPLY,
        NotificationKind.FOLLOW,
    }
)


def _sql_list(values: list[str]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def _kind_category_sql() -> str:
    by_category: dict[str, list[str]] = {}
    for kind, category in KIND_CATEGORY.items():
        by_category.setdefault(category.value, []).append(kind.value)
    return " OR ".join(
        f"(category = '{c}' AND kind IN ({_sql_list(kinds)}))" for c, kinds in by_category.items()
    )


class MuteTarget(StrEnum):
    PERSON = "person"
    DEED = "deed"


class FanoutStatus(StrEnum):
    RUNNING = "running"
    DONE = "done"
    # Stopped at ``network.max_recipients_per_event``.
    CAPPED = "capped"


class Notification(Base):
    """One notification for one recipient (D106). Grouped kinds keep the newest actors first
    (at most ``actors_kept``) and count distinct actors in ``actor_count``."""

    __tablename__ = "notifications"
    __table_args__ = (
        CheckConstraint(_kind_category_sql(), name="kind_category"),
        CheckConstraint(
            f"(group_key IS NOT NULL) = "
            f"(kind IN ({_sql_list(sorted(k.value for k in GROUPED_KINDS))}))",
            name="group_key_iff_grouped",
        ),
        CheckConstraint(
            "actor_count >= 0 AND cardinality(actor_ids) <= actor_count",
            name="actor_count_valid",
        ),
        CheckConstraint("jsonb_typeof(data) = 'object'", name="data_object"),
        CheckConstraint("updated_at >= created_at", name="updated_after_created"),
        CheckConstraint("read_at IS NULL OR read_at >= created_at", name="read_after_created"),
        UniqueConstraint("user_id", "dedupe_key", name="uq_notifications_user_dedupe_key"),
        # One unread row per group: new activity folds into it (D108).
        Index(
            "uq_notifications_unread_group",
            "user_id",
            "group_key",
            unique=True,
            postgresql_where=text("read_at IS NULL AND group_key IS NOT NULL"),
        ),
        # The unread count (N2).
        Index("ix_notifications_unread", "user_id", postgresql_where=text("read_at IS NULL")),
        # The nightly purge (``retention_days`` after the last update).
        Index("ix_notifications_updated_at", "updated_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # The recipient.
    user_id: Mapped[uuid.UUID] = mapped_column(_users_fk())
    kind: Mapped[NotificationKind] = mapped_column(
        _enum(NotificationKind, "notification_kind", length=40)
    )
    category: Mapped[NotificationCategory] = mapped_column(
        _enum(NotificationCategory, "notification_category")
    )
    # Newest first; empty for system rows.
    actor_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(Uuid()), default=list, server_default=text("'{}'::uuid[]")
    )
    # Distinct actors in the group.
    actor_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    deed_id: Mapped[uuid.UUID | None] = mapped_column(_deeds_fk())
    # The other object (comment, photo, badge award, title, review item); no foreign key.
    ref_id: Mapped[uuid.UUID | None]
    # Ids, codes and numbers only (badge_slug, period_month, level, review_kind, reply_due_at,
    # day).
    data: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    # e.g. ``recognised:{deed_id}``.
    dedupe_key: Mapped[str | None] = mapped_column(String(200))
    # ``like:{deed_id}``, ``comment:{deed_id}``, ``reply:{deed_id}``, ``follow:{day}``,
    # ``network:{day}``.
    group_key: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# The list (N1), newest activity first, keyset on (updated_at, id).
Index(
    "ix_notifications_user_updated_at",
    Notification.user_id,
    Notification.updated_at.desc(),
    Notification.id,
)


class NotificationView(Base):
    """When the recipient last opened the bell (N3); rows newer than ``seen_at`` are "new"."""

    __tablename__ = "notification_views"

    user_id: Mapped[uuid.UUID] = mapped_column(_users_fk(), primary_key=True)
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class NotificationPreference(Base):
    """A person's choice for one category; a missing row means the config default."""

    __tablename__ = "notification_preferences"
    __table_args__ = (
        PrimaryKeyConstraint("user_id", "category", name="pk_notification_preferences"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(_users_fk())
    category: Mapped[NotificationCategory] = mapped_column(
        _enum(NotificationCategory, "notification_category")
    )
    enabled: Mapped[bool] = mapped_column(Boolean)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class NotificationMute(Base):
    """A silent mute of a person or a deed (D113); at most ``max_mutes`` per person (service)."""

    __tablename__ = "notification_mutes"
    __table_args__ = (
        PrimaryKeyConstraint("user_id", "target_type", "target_id", name="pk_notification_mutes"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(_users_fk())
    target_type: Mapped[MuteTarget] = mapped_column(_enum(MuteTarget, "mute_target"))
    # A user id or a deed id; no foreign key (one column for both).
    target_id: Mapped[uuid.UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class NotificationDedupe(Base):
    """A key seen once per recipient: (group, actor) pairs, so a person counts once per group,
    and the per-actor daily fan-out counter keys. Purged after ``dedupe_days``."""

    __tablename__ = "notification_dedupe"
    __table_args__ = (
        PrimaryKeyConstraint("user_id", "key", name="pk_notification_dedupe"),
        # The nightly purge.
        Index("ix_notification_dedupe_created_at", "created_at"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(_users_fk())
    key: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class NotificationFanout(Base):
    """The network fan-out of one verified deed (D110, ``notifications.fanout_network``)."""

    __tablename__ = "notification_fanouts"
    __table_args__ = (
        CheckConstraint("recipients >= 0", name="recipients_non_negative"),
        CheckConstraint("(status = 'running') = (finished_at IS NULL)", name="finished_fields"),
        CheckConstraint(
            "finished_at IS NULL OR finished_at >= created_at", name="finished_after_created"
        ),
        # Fan-outs to resume after a restart.
        Index(
            "ix_notification_fanouts_running",
            "created_at",
            postgresql_where=text("status = 'running'"),
        ),
    )

    deed_id: Mapped[uuid.UUID] = mapped_column(_deeds_fk(), primary_key=True)
    # The reporter and the doer whose followers are notified.
    actor_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(Uuid()))
    # The last follower id done; null before the first batch.
    cursor: Mapped[uuid.UUID | None]
    recipients: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    status: Mapped[FanoutStatus] = mapped_column(
        _enum(FanoutStatus, "fanout_status"),
        default=FanoutStatus.RUNNING,
        server_default=FanoutStatus.RUNNING.value,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class NotificationDailyCount(Base):
    """New rows per recipient, IST day and category, for the caps in D115. Purged after 7 days."""

    __tablename__ = "notification_daily_counts"
    __table_args__ = (
        PrimaryKeyConstraint("user_id", "day", "category", name="pk_notification_daily_counts"),
        CheckConstraint("count >= 0", name="count_non_negative"),
        # The nightly purge.
        Index("ix_notification_daily_counts_day", "day"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(_users_fk())
    day: Mapped[date] = mapped_column(Date)
    category: Mapped[NotificationCategory] = mapped_column(
        _enum(NotificationCategory, "notification_category")
    )
    count: Mapped[int] = mapped_column(SmallInteger, default=0, server_default="0")
