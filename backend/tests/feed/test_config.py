"""The ``feed`` config namespace (docs/M3-DESIGN.md §2.5, §3.6, §8 Config, migration 0015)."""

from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.runtime_config import ConfigVersion
from app.modules.feed._limits import FeedLimits, load_limits

# §2.5, apart from the English blocked-terms starter list (PO review before launch).
DESIGN_V1: dict[str, Any] = {
    "comment_max_chars": 300,
    "comment_block_links": True,
    "comment_block_phone_numbers": True,
    "comment_filter_enabled_languages": ["en"],
    "deleted_comment_retention_days": 30,
    "top_week_days": 7,
    "top_month_days": 30,
    "top_year_days": 365,
    "top_like_weight": 1,
    "top_comment_weight": 3,
    "top_size": 50,
    "top_badge_ranks": 3,
    "top_week_refresh_minutes": 10,
    "top_month_refresh_minutes": 30,
    "top_year_refresh_minutes": 60,
    "max_following": 2000,
    "rate_comment_user": "5/60",
    "rate_comment_user_daily": "100/86400",
    "rate_comment_ip": "60/3600",
    "rate_comment_report_user": "10/3600",
    "rate_like_user": "120/3600",
    "rate_like_ip": "600/3600",
    "rate_save_user": "120/3600",
    "rate_follow_user": "60/3600",
    "rate_follow_ip": "300/3600",
    "rate_hide_user": "30/86400",
    "rate_share_user": "60/3600",
    "rate_public_deed_ip": "120/900",
    "rate_feed_read_user": "600/900",
}
# Migration 0024 (Stories, D73) adds these to the latest version.
STORY_KEYS: dict[str, Any] = {
    "story_window_hours": 24,
    "story_max_people": 50,
    "story_max_per_person": 20,
    "story_seconds": 5,
    "story_seen_retention_hours": 48,
    "rate_story_seen_user": "600/900",
}
EN_SUBSTITUTIONS = {"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s"}


async def _v1(session: AsyncSession) -> dict[str, Any]:
    row = await session.get(ConfigVersion, ("feed", 1))
    assert row is not None
    assert row.created_by is None
    return dict(row.values)


async def _current(session: AsyncSession) -> dict[str, Any]:
    """v1 plus the keys later versions added (what FeedLimits validates today)."""
    return {**await _v1(session), **STORY_KEYS}


async def test_feed_v1_is_the_design_seed(session: AsyncSession) -> None:
    values = await _v1(session)
    packs = values.pop("comment_filter_languages")
    assert values == DESIGN_V1
    assert set(packs) == {"en"}
    en = packs["en"]
    assert en["substitutions"] == EN_SUBSTITUTIONS
    assert en["keep_marks"] == [] and en["squashed_min_letters"] == 5
    assert en["blocked_terms"] and all(t == t.strip().lower() for t in en["blocked_terms"])
    # Words common in descriptions of good deeds are not blocked.
    assert not {"trash", "useless", "dirty", "garbage"} & set(en["blocked_terms"])

    limits = FeedLimits.model_validate(await _current(session))
    assert set(FeedLimits.model_fields) == {*DESIGN_V1, *STORY_KEYS, "comment_filter_languages"}
    assert limits.comment_filter_enabled_languages == ("en",)
    assert [limits.top_days(p) for p in ("week", "month", "year")] == [7, 30, 365]
    assert [limits.top_refresh_minutes(p) for p in ("week", "month", "year")] == [10, 30, 60]


async def test_load_limits_returns_the_active_version(session: AsyncSession) -> None:
    version, limits = await load_limits(session)
    assert version >= 2 and isinstance(limits, FeedLimits)
    assert {k: getattr(limits, k) for k in STORY_KEYS} == STORY_KEYS


async def test_feed_limits_have_no_defaults_and_no_extras(session: AsyncSession) -> None:
    values = await _current(session)
    for key in values:
        partial = {k: v for k, v in values.items() if k != key}
        with pytest.raises(ValidationError):
            FeedLimits.model_validate(partial)
    with pytest.raises(ValidationError):
        FeedLimits.model_validate({**values, "unexpected": 1})
    pack = values["comment_filter_languages"]["en"]
    for key in pack:
        partial_pack = {k: v for k, v in pack.items() if k != key}
        with pytest.raises(ValidationError):
            FeedLimits.model_validate({**values, "comment_filter_languages": {"en": partial_pack}})


async def test_a_language_is_enabled_by_config_only(session: AsyncSession) -> None:
    """Q35: adding a language is a new pack plus its code in the enabled list."""
    values = await _current(session)
    hi = {
        "blocked_terms": ["test"],
        "substitutions": {},
        "keep_marks": ["ा"],
        "squashed_min_letters": 4,
    }
    packs = {**values["comment_filter_languages"], "hi": hi}
    # A pack may be written before it is switched on.
    FeedLimits.model_validate({**values, "comment_filter_languages": packs})
    limits = FeedLimits.model_validate(
        {
            **values,
            "comment_filter_languages": packs,
            "comment_filter_enabled_languages": ["en", "hi"],
        }
    )
    assert limits.comment_filter_enabled_languages == ("en", "hi")


@pytest.mark.parametrize(
    "change",
    [
        {"comment_filter_enabled_languages": ["en", "kn"]},  # no pack for kn
        {"comment_filter_enabled_languages": ["en", "en"]},
        {"comment_filter_enabled_languages": ["EN"]},
        {"comment_filter_enabled_languages": ["eng"]},
        {"comment_max_chars": 1001},  # above the column's ceiling
        {"comment_max_chars": 0},
        {"top_week_days": 0},
        {"top_month_days": 400},  # windows must grow week <= month <= year
        {"top_badge_ranks": 51},
        {"top_size": 40000},
        {"top_like_weight": -1},
        {"rate_comment_user": "5 per minute"},
        {"rate_like_user": "120/0"},
        {"story_window_hours": 0},
        {"story_window_hours": 200},
        {"story_seen_retention_hours": 12},  # must cover the window
        {"story_seconds": 0},
        {"rate_story_seen_user": "lots"},
    ],
)
async def test_feed_limits_reject_inconsistent_values(
    session: AsyncSession, change: dict[str, Any]
) -> None:
    values = await _current(session)
    with pytest.raises(ValidationError):
        FeedLimits.model_validate({**values, **change})


@pytest.mark.parametrize(
    "pack_change",
    [
        {"substitutions": {"00": "o"}},
        {"substitutions": {"0": "oo"}},
        {"keep_marks": ["ab"]},
        {"blocked_terms": ["ok", "  "]},
        {"squashed_min_letters": 0},
    ],
)
async def test_language_packs_are_validated(
    session: AsyncSession, pack_change: dict[str, Any]
) -> None:
    values = await _current(session)
    pack = {**values["comment_filter_languages"]["en"], **pack_change}
    with pytest.raises(ValidationError):
        FeedLimits.model_validate({**values, "comment_filter_languages": {"en": pack}})
