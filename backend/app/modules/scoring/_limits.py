"""Typed values of the ``scoring`` and ``leaderboard`` config namespaces (docs/M4-DESIGN.md §2.6,
D60).

Every key is required and has no default: the ``config_versions`` rows (seeded as ``scoring`` v1
and ``leaderboard`` v1 by migration 0020) are the only source of these values. Change them by
publishing a new version.

Text-rule patterns are validated when the config loads (D55): each must compile, stay short, and
avoid nested quantifiers (the classic catastrophic-backtracking shape). They only ever run on the
normalised description, which is at most ``MAX_TEXT_CHARS`` long.
"""

import re
from typing import Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.runtime_config import RuntimeConfig, load

# Rate specs are "<count>/<seconds>".
RATE_SPEC = r"^[1-9][0-9]*/[1-9][0-9]*$"
# Text rules never look at more than this many characters (descriptions are at most 1000).
MAX_TEXT_CHARS = 1000
MAX_PATTERN_CHARS = 200
MAX_PATTERNS = 50
# A group containing a quantifier, itself quantified: ``(a+)+``, ``(\w*x)*``, ``(a{2,})+``.
_NESTED_QUANTIFIER = re.compile(r"\((?:[^()\\]|\\.)*[+*}](?:[^()\\]|\\.)*\)\s*[+*{]")

PairRole = Literal["doer", "reporter"]


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PairMultiplier(_Frozen):
    """From this pair ordinal on (in the pair window), points are ``percent`` of the full value."""

    from_ordinal: int = Field(ge=1)
    percent: int = Field(ge=1, le=100)


def _check_pattern(pattern: str) -> None:
    if not pattern or len(pattern) > MAX_PATTERN_CHARS:
        raise ValueError(f"spam patterns must be 1 to {MAX_PATTERN_CHARS} characters")
    if _NESTED_QUANTIFIER.search(pattern):
        raise ValueError(f"spam pattern {pattern!r} has a nested quantifier")
    try:
        re.compile(pattern)
    except re.error as exc:
        raise ValueError(f"spam pattern {pattern!r} is not a valid regular expression") from exc


class TextRules(_Frozen):
    """D55: crude, English-only checks on the normalised description."""

    # Fewer letters than this (after normalising) is ``spam_or_empty``.
    min_letters: int = Field(ge=0)
    # A run of the same character longer than this is ``spam_or_empty`` ("!!!!!!!!", "aaaaaaa").
    max_repeated_char_run: int = Field(ge=2)
    spam_patterns: tuple[str, ...] = Field(max_length=MAX_PATTERNS)
    # Category slug -> words that suggest it. A description that contains words of other listed
    # categories but none of its own is ``category_mismatch`` (recorded only, D55).
    category_keywords: dict[str, tuple[str, ...]]

    @model_validator(mode="after")
    def _valid(self) -> Self:
        for pattern in self.spam_patterns:
            _check_pattern(pattern)
        for slug, words in self.category_keywords.items():
            if any(not w.strip() for w in words):
                raise ValueError(f"category_keywords[{slug!r}] must not contain empty words")
        return self


class ScoringConfig(RuntimeConfig):
    """Config namespace ``scoring`` (rule-based points, caps and pair multipliers)."""

    namespace = "scoring"

    # Every day and month rule uses this zone, applied to server times (D52, D58).
    timezone: str
    # Doer points per category (PO Q45 = C: 10 for all). A missing category scores as ``other``.
    category_base_points: dict[str, int]
    # A ``spam_or_empty`` report still gets this (PRD §9), instead of its category base.
    min_points: int = Field(ge=1)
    photo_bonus: int = Field(ge=0)
    # A photo attached within this many hours of the report counts as photo evidence (D51).
    photo_bonus_window_hours: int = Field(ge=0)
    # Reporter points for a counted report, and for one beyond the same-act cap.
    reporter_points: int = Field(ge=1)
    reporter_points_beyond_cap: int = Field(ge=1)
    # Decisions per user per IST day of verification (D52, PO Q48).
    doer_daily_cap: int = Field(ge=0)
    reporter_daily_cap: int = Field(ge=0)
    # PO Q47: pair multipliers per IST month, for the listed roles.
    pair_window: Literal["ist_month"]
    pair_roles: tuple[PairRole, ...]
    pair_multipliers: tuple[PairMultiplier, ...] = Field(min_length=1)
    text_rules: TextRules
    # Rate limits ("<count>/<seconds>") for the scoring read endpoints.
    rate_me_user: str = Field(pattern=RATE_SPEC)
    rate_public_ip: str = Field(pattern=RATE_SPEC)

    @model_validator(mode="after")
    def _valid(self) -> Self:
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown timezone {self.timezone!r}") from exc
        if "other" not in self.category_base_points:
            raise ValueError("category_base_points must have 'other' (the fallback)")
        if any(points < 1 for points in self.category_base_points.values()):
            raise ValueError("category_base_points must be at least 1")
        ordinals = [m.from_ordinal for m in self.pair_multipliers]
        if ordinals[0] != 1 or ordinals != sorted(set(ordinals)):
            raise ValueError("pair_multipliers must start at ordinal 1 and strictly increase")
        if len(set(self.pair_roles)) != len(self.pair_roles):
            raise ValueError("pair_roles must not repeat")
        return self

    @property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def pair_percent(self, role: PairRole, ordinal: int) -> int:
        """The multiplier (percent) for the ``ordinal``-th report of a pair in its window."""
        if role not in self.pair_roles:
            return 100
        percent = 100
        for step in self.pair_multipliers:
            if ordinal >= step.from_ordinal:
                percent = step.percent
        return percent


async def load_scoring(session: AsyncSession) -> tuple[int, ScoringConfig]:
    """``(version, config)`` of the active ``scoring`` config. Fails closed if it is missing."""
    return await load(session, ScoringConfig)


# --------------------------------------------------- leaderboard namespace


LevelKind = Literal["ward", "city", "state", "country"]


class TitleRule(_Frozen):
    """A title for the given places on a final board, e.g. ``hero`` for place 1."""

    key: str = Field(min_length=1, max_length=40)
    places: tuple[int, ...] = Field(min_length=1)
    # ``{region}`` and ``{month}`` are filled in when shown.
    label: str = Field(min_length=1)

    @model_validator(mode="after")
    def _valid(self) -> Self:
        if any(p < 1 for p in self.places):
            raise ValueError("title places start at 1")
        return self


class LevelRule(_Frozen):
    kind: LevelKind
    enabled: bool
    # Title minimums (D67, PO Q54): counted deeds in that region that month, from at least this
    # many distinct reporters. Boards are always shown; minimums only gate titles.
    min_counted_deeds: int = Field(ge=0)
    min_distinct_reporters: int = Field(ge=0)
    titles: tuple[TitleRule, ...]


class Tier(_Frozen):
    slug: str = Field(min_length=1, max_length=40)
    label: str = Field(min_length=1)
    min_points: int = Field(ge=0)


class Badge(_Frozen):
    slug: str = Field(min_length=1, max_length=40)
    label: str = Field(min_length=1)
    kind: Literal["doer_deeds", "reports"]
    threshold: int = Field(ge=1)


class FeaturedCity(_Frozen):
    """A city whose top performers are featured on the leaderboard screen (PO 2026-11-23, D72).
    Members count for it by their registered city (``users.city``), matched case-insensitively
    against ``registered_city_names``; they are ranked with the same rules as every board (D59,
    D67) over the month's counted deeds. Several cities can be listed; the first enabled one is
    the default."""

    key: str = Field(min_length=1, max_length=40, pattern=r"^[a-z0-9-]+$")
    name: str = Field(min_length=1, max_length=60)
    registered_city_names: tuple[str, ...] = Field(min_length=1)
    top_n: int = Field(ge=1, le=20)
    # The PO asked for points here (2026-11-23), an exception to D69 for this section only.
    show_points: bool
    enabled: bool


class RankWeights(_Frozen):
    deeds: int = Field(ge=0)
    points: int = Field(ge=0)


class LeaderboardConfig(RuntimeConfig):
    """Config namespace ``leaderboard`` (boards, levels, titles, tiers and badges)."""

    namespace = "leaderboard"

    # Rank score = deeds * deed_count + points * doer_points (D59).
    weights: RankWeights
    refresh_seconds: int = Field(gt=0)
    page_size: int = Field(gt=0, le=200)
    # Finalization waits this long after month end + the withdrawal window (D64).
    finalize_margin_minutes: int = Field(ge=0)
    # PO Q58 = B: dense (1, 1, 2); "competition" is 1, 1, 3.
    shared_rank_style: Literal["dense", "competition"]
    levels: tuple[LevelRule, ...]
    tiers: tuple[Tier, ...] = Field(min_length=1)
    badges: tuple[Badge, ...]
    featured_cities: tuple[FeaturedCity, ...]

    @model_validator(mode="after")
    def _valid(self) -> Self:
        keys = [c.key for c in self.featured_cities]
        if len(set(keys)) != len(keys):
            raise ValueError("featured city keys must be unique")
        kinds = [level.kind for level in self.levels]
        if len(set(kinds)) != len(kinds):
            raise ValueError("each level kind may appear once")
        if self.tiers[0].min_points != 0:
            raise ValueError("the first tier must start at 0 points")
        mins = [t.min_points for t in self.tiers]
        if mins != sorted(set(mins)):
            raise ValueError("tier min_points must strictly increase")
        slugs = [b.slug for b in self.badges]
        if len(set(slugs)) != len(slugs):
            raise ValueError("badge slugs must be unique")
        return self


async def load_leaderboard(session: AsyncSession) -> tuple[int, LeaderboardConfig]:
    """``(version, config)`` of the active ``leaderboard`` config. Fails closed if missing."""
    return await load(session, LeaderboardConfig)
