"""Typed values of the ``feed`` config namespace (docs/M3-DESIGN.md §2.5, §3.6, D41).

Every key is required and has no default: the ``config_versions`` row (seeded as ``feed`` v1 by
migration 0015) is the only source of these values. Change them by publishing a new version.

Kind-words filter languages (Q35): ``comment_filter_languages`` holds one independent pack per
ISO 639-1 code; ``comment_filter_enabled_languages`` lists the packs in force. Nothing outside
``feed/_kindness.py`` and this config knows which languages exist.
"""

import re
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.runtime_config import RuntimeConfig, load

# Rate specs are "<count>/<seconds>".
RATE_SPEC = r"^[1-9][0-9]*/[1-9][0-9]*$"
LANGUAGE_CODE = r"^[a-z]{2}$"


class LanguagePack(BaseModel):
    """One language's kind-words rules. Packs never affect each other."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    blocked_terms: tuple[str, ...]
    # Look-alike characters mapped to letters, one character each (e.g. "0" -> "o").
    substitutions: dict[str, str]
    # Combining marks the script needs kept (e.g. Devanagari and Kannada vowel signs).
    keep_marks: tuple[str, ...]
    # Terms at least this long are also matched with spaces and punctuation removed.
    squashed_min_letters: int = Field(gt=0)

    @model_validator(mode="after")
    def _valid(self) -> Self:
        if any(not t.strip() for t in self.blocked_terms):
            raise ValueError("blocked_terms must not contain empty terms")
        for src, dst in self.substitutions.items():
            if len(src) != 1 or len(dst) != 1:
                raise ValueError("substitutions map one character to one character")
        if any(len(m) != 1 for m in self.keep_marks):
            raise ValueError("keep_marks are single characters")
        return self


class FeedLimits(RuntimeConfig):
    """Config namespace ``feed``."""

    namespace = "feed"

    # Comments
    comment_max_chars: int = Field(gt=0, le=1000)  # the column is varchar(1000)
    comment_block_links: bool
    comment_block_phone_numbers: bool
    comment_filter_enabled_languages: tuple[str, ...]
    comment_filter_languages: dict[str, LanguagePack]
    # Text of comments deleted by the doer, reporter or moderation is kept this long (Q38)
    deleted_comment_retention_days: int = Field(gt=0)
    # Top (Q26, Q43): rolling windows, score weights, size and refresh per period
    top_week_days: int = Field(gt=0)
    top_month_days: int = Field(gt=0)
    top_year_days: int = Field(gt=0)
    top_like_weight: int = Field(ge=0)
    top_comment_weight: int = Field(ge=0)
    top_size: int = Field(gt=0, le=32767)  # rank is a smallint
    top_badge_ranks: int = Field(ge=0)
    top_week_refresh_minutes: int = Field(gt=0)
    top_month_refresh_minutes: int = Field(gt=0)
    top_year_refresh_minutes: int = Field(gt=0)
    # Follows
    max_following: int = Field(gt=0)
    # Rate limits ("<count>/<seconds>")
    rate_comment_user: str = Field(pattern=RATE_SPEC)
    rate_comment_user_daily: str = Field(pattern=RATE_SPEC)
    rate_comment_ip: str = Field(pattern=RATE_SPEC)
    rate_comment_report_user: str = Field(pattern=RATE_SPEC)
    rate_like_user: str = Field(pattern=RATE_SPEC)
    rate_like_ip: str = Field(pattern=RATE_SPEC)
    rate_save_user: str = Field(pattern=RATE_SPEC)
    rate_follow_user: str = Field(pattern=RATE_SPEC)
    rate_follow_ip: str = Field(pattern=RATE_SPEC)
    rate_hide_user: str = Field(pattern=RATE_SPEC)
    rate_share_user: str = Field(pattern=RATE_SPEC)
    rate_public_deed_ip: str = Field(pattern=RATE_SPEC)
    rate_feed_read_user: str = Field(pattern=RATE_SPEC)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        codes = [*self.comment_filter_enabled_languages, *self.comment_filter_languages]
        if any(re.fullmatch(LANGUAGE_CODE, c) is None for c in codes):
            raise ValueError("language codes must be ISO 639-1 (two lowercase letters)")
        enabled = self.comment_filter_enabled_languages
        if len(set(enabled)) != len(enabled):
            raise ValueError("comment_filter_enabled_languages has duplicates")
        missing = set(enabled) - set(self.comment_filter_languages)
        if missing:
            raise ValueError(f"enabled languages without a pack: {sorted(missing)}")
        if not self.top_week_days <= self.top_month_days <= self.top_year_days:
            raise ValueError("Top windows must grow: week <= month <= year")
        if self.top_badge_ranks > self.top_size:
            raise ValueError("top_badge_ranks must not exceed top_size")
        return self

    def top_days(self, period: str) -> int:
        return {
            "week": self.top_week_days,
            "month": self.top_month_days,
            "year": self.top_year_days,
        }[period]

    def top_refresh_minutes(self, period: str) -> int:
        return {
            "week": self.top_week_refresh_minutes,
            "month": self.top_month_refresh_minutes,
            "year": self.top_year_refresh_minutes,
        }[period]


async def load_limits(session: AsyncSession) -> tuple[int, FeedLimits]:
    """``(version, limits)`` of the active ``feed`` config. Fails closed if it is missing."""
    return await load(session, FeedLimits)
