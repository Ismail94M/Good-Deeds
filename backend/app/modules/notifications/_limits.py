"""Typed values of the ``notifications`` config namespace (docs/M6-DESIGN.md §2.5, D107 to D115).

Every key is required and has no default: the ``config_versions`` rows (seeded as
``notifications`` v1 by migration 0030; migration 0034 adds ``replies.max_recipients_per_comment``)
are the only source of these values. Change them by publishing a new version.
"""

from typing import Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.runtime_config import RuntimeConfig, load
from app.modules.notifications.models import NotificationCategory

# Rate specs are "<count>/<seconds>".
RATE_SPEC = r"^[1-9][0-9]*/[1-9][0-9]*$"
# The categories that can never be switched off (D107, D113).
ALWAYS_ON = frozenset({NotificationCategory.ACCOUNT, NotificationCategory.REVIEWS})

ReviewKind = Literal[
    "explanation_requested",
    "explanation_reminder",
    "decision_notice",
    "appeal_decided",
    "review_closed",
]
# Q89: who hears about a verified deed of someone they are connected to.
Audience = Literal["followers_of_actor", "mutual", "followers_of_actor_or_followed_by"]
# ``notification_daily_counts.count`` is a smallint.
_MAX_DAILY = 32767


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CategoryRule(_Frozen):
    """Whether a category is on for people who never chose, and whether they may switch it off."""

    default: bool
    locked: bool

    @model_validator(mode="after")
    def _valid(self) -> Self:
        if self.locked and not self.default:
            raise ValueError("a locked category must be on by default")
        return self


class NetworkRules(_Frozen):
    """D110: the network fan-out of a verified deed."""

    audience: Audience
    roles: tuple[Literal["reporter", "doer"], ...] = Field(min_length=1)
    fanout_batch: int = Field(ge=1, le=10_000)
    max_recipients_per_event: int = Field(ge=1)
    max_fanouts_per_actor_day: int = Field(ge=1)

    @model_validator(mode="after")
    def _valid(self) -> Self:
        if len(set(self.roles)) != len(self.roles):
            raise ValueError("network.roles must not repeat")
        return self


class ReplyRules(_Frozen):
    """D111: "also commented" for earlier commenters of the last ``lookback_days``."""

    enabled: bool
    lookback_days: int = Field(ge=1, le=365)
    # SEC6-M1: earlier commenters told per new comment (the most recent ones first).
    max_recipients_per_comment: int = Field(ge=1, le=5_000)


class DailyCaps(_Frozen):
    """D115: new rows per recipient and IST day. Beyond a cap, groups still grow."""

    activity: int = Field(ge=1, le=_MAX_DAILY)
    network: int = Field(ge=1, le=_MAX_DAILY)


class NotificationsConfig(RuntimeConfig):
    """Config namespace ``notifications`` (categories, grouping, caps, retention, rates)."""

    namespace = "notifications"

    # Day boundaries (per-day groups, daily caps) use this zone.
    timezone: str
    categories: dict[NotificationCategory, CategoryRule]
    # Which ``MemberReviewUpdate`` kinds notify (Q91).
    review_kinds: tuple[ReviewKind, ...]
    network: NetworkRules
    replies: ReplyRules
    daily_caps: DailyCaps
    # Names kept per grouped row, newest first (D108).
    actors_kept: int = Field(ge=1, le=10)
    dedupe_days: int = Field(ge=1, le=365)
    retention_days: int = Field(ge=1, le=3650)
    max_mutes: int = Field(ge=1, le=100_000)
    page_size: int = Field(ge=1, le=100)
    unread_poll_seconds: int = Field(ge=10, le=3600)
    rate_read: str = Field(pattern=RATE_SPEC)
    rate_write: str = Field(pattern=RATE_SPEC)

    @field_validator("timezone")
    @classmethod
    def _zone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown timezone {value!r}") from exc
        return value

    @model_validator(mode="after")
    def _valid(self) -> Self:
        missing = set(NotificationCategory) - set(self.categories)
        if missing:
            raise ValueError(f"categories missing: {sorted(c.value for c in missing)}")
        for category in ALWAYS_ON:
            if not self.categories[category].locked:
                raise ValueError(f"category {category.value!r} must be locked")
        if len(set(self.review_kinds)) != len(self.review_kinds):
            raise ValueError("review_kinds must not repeat")
        return self

    @property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def daily_cap(self, category: NotificationCategory) -> int | None:
        """The cap on new rows a day for ``category``, or None (no cap: About you, Account &
        safety, Reviews)."""
        if category == NotificationCategory.ACTIVITY:
            return self.daily_caps.activity
        if category == NotificationCategory.NETWORK:
            return self.daily_caps.network
        return None


async def load_notifications(session: AsyncSession) -> tuple[int, NotificationsConfig]:
    """``(version, config)`` of the active ``notifications`` config. Fails closed if missing."""
    return await load(session, NotificationsConfig)
