"""Scoring config and rule-based evaluation (docs/M4-DESIGN.md §2.6, D53, D55, §4.4; PO Q45 = C)."""

import logging
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.deeds._limits import load_limits
from app.modules.scoring import _rules
from app.modules.scoring._ledger import round_half_up
from app.modules.scoring._limits import (
    LeaderboardConfig,
    ScoringConfig,
    load_leaderboard,
    load_scoring,
)
from app.modules.scoring._text import longest_run, normalise
from app.modules.scoring.models import TextCheck


async def _config(session: AsyncSession) -> ScoringConfig:
    return (await load_scoring(session))[1]


def _with(config: ScoringConfig, **text_rules: Any) -> dict[str, Any]:
    values = config.model_dump(mode="json")
    values["text_rules"] |= text_rules
    return values


# --------------------------------------------------- config


async def test_seeded_configs_load_with_the_po_values(session: AsyncSession) -> None:
    version, scoring = await load_scoring(session)
    assert version >= 1
    assert set(scoring.category_base_points.values()) == {10}  # Q45 = C
    assert (scoring.min_points, scoring.photo_bonus) == (4, 2)
    assert (scoring.reporter_points, scoring.reporter_points_beyond_cap) == (2, 1)
    assert (scoring.doer_daily_cap, scoring.reporter_daily_cap) == (10, 10)
    assert scoring.timezone == "Asia/Kolkata"
    # Q47: ordinals 1-3 100 %, 4-7 75 %, 8-15 50 %, both roles.
    for role in ("doer", "reporter"):
        assert [scoring.pair_percent(role, n) for n in range(1, 16)] == [100] * 3 + [75] * 4 + [
            50
        ] * 8
    _, board = await load_leaderboard(session)
    assert board.shared_rank_style == "dense"
    assert [lvl.kind for lvl in board.levels if lvl.enabled] == ["ward", "city"]


async def test_every_deeds_category_has_base_points(session: AsyncSession) -> None:
    """The ``scoring`` and ``deeds`` seeds agree (§2.6), so no category falls back to ``other``."""
    _, limits = await load_limits(session)
    scoring = await _config(session)
    assert {c.slug for c in limits.categories} <= set(scoring.category_base_points)


async def test_pair_roles_limit_the_multiplier(session: AsyncSession) -> None:
    values = (await _config(session)).model_dump(mode="json") | {"pair_roles": ["doer"]}
    config = ScoringConfig.model_validate(values)
    assert config.pair_percent("doer", 8) == 50
    assert config.pair_percent("reporter", 8) == 100


@pytest.mark.parametrize(
    "pattern",
    [
        r"(a+)+$",  # nested quantifier: catastrophic backtracking
        r"(\w*x)*",
        r"(ab{2,})+",
        r"([unclosed",
        "",
        "x" * 201,
    ],
)
async def test_unsafe_or_invalid_spam_patterns_are_refused_at_load(
    session: AsyncSession, pattern: str
) -> None:
    config = await _config(session)
    with pytest.raises(ValidationError):
        ScoringConfig.model_validate(_with(config, spam_patterns=[pattern]))


async def test_config_validation(session: AsyncSession) -> None:
    good = (await _config(session)).model_dump(mode="json")
    bad_values: list[dict[str, Any]] = [
        good | {"timezone": "Mars/Olympus"},
        good | {"category_base_points": {"animals": 10}},  # no "other" fallback
        good | {"pair_multipliers": [{"from_ordinal": 2, "percent": 100}]},
        good
        | {
            "pair_multipliers": [
                {"from_ordinal": 1, "percent": 100},
                {"from_ordinal": 1, "percent": 50},
            ]
        },
        good | {"pair_window": "rolling_30_days"},
        good | {"unknown_key": 1},
    ]
    for values in bad_values:
        with pytest.raises(ValidationError):
            ScoringConfig.model_validate(values)
    missing = dict(good)
    del missing["min_points"]
    with pytest.raises(ValidationError):
        ScoringConfig.model_validate(missing)


async def test_leaderboard_config_validation(session: AsyncSession) -> None:
    good = (await load_leaderboard(session))[1].model_dump(mode="json")
    LeaderboardConfig.model_validate(good)
    for values in (
        good | {"shared_rank_style": "random"},
        good | {"tiers": [{"slug": "a", "label": "A", "min_points": 5}]},
        good | {"levels": [*good["levels"], good["levels"][0]]},
        good | {"badges": [*good["badges"], good["badges"][0]]},
    ):
        with pytest.raises(ValidationError):
            LeaderboardConfig.model_validate(values)


# --------------------------------------------------- text and rules


def _fullwidth(text: str) -> str:
    """Full-width look-alikes of ASCII letters (built here so the source stays ASCII)."""
    return "".join(chr(ord(c) + 0xFEE0) if c.isascii() and c.isalpha() else c for c in text)


def test_normalise_folds_look_alikes_and_bounds_the_input() -> None:
    assert normalise(_fullwidth("FOLLOW") + chr(0x200B) + " M" + chr(0xE9)) == "follow me"
    assert len(normalise("a" * 5000)) == 1000
    assert longest_run("hi!!!!!! there") == 6


async def test_every_category_scores_ten(session: AsyncSession) -> None:
    config = await _config(session)
    for slug in config.category_base_points:
        result = _rules.evaluate(slug, "Helped an elderly neighbour carry heavy shopping", config)
        assert result.base == 10, slug
        assert not result.needs_review


@pytest.mark.parametrize(
    "description",
    [
        "",
        "ok ok 123",  # fewer than 10 letters
        "Great!!!!!!!!!! thing done today",  # a run longer than 6
        "Helped a lady. Follow me for more good deeds",
        "Nice work, please SUBSCRIBE to my channel",
        _fullwidth("Whatsapp me") + " for the deed",
    ],
)
async def test_spam_or_empty_gets_min_points_and_a_review_flag(
    session: AsyncSession, description: str
) -> None:
    result = _rules.evaluate("animals", description, await _config(session))
    assert result.text_check == TextCheck.SPAM_OR_EMPTY
    assert (result.base, result.needs_review) == (4, True)


async def test_category_mismatch_is_recorded_only(session: AsyncSession) -> None:
    config = await _config(session)
    mismatch = _rules.evaluate("community", "Fed three stray dogs near the bus stop", config)
    assert mismatch.text_check == TextCheck.CATEGORY_MISMATCH
    assert (mismatch.base, mismatch.needs_review) == (10, False)
    # Its own keywords win, and a category without keywords and no other hits is fine.
    assert _rules.evaluate("animals", "Fed a puppy and a cat", config).text_check == TextCheck.OK
    assert (
        _rules.evaluate("community", "Cleaned the park with neighbours", config).text_check
        == TextCheck.OK
    )
    # Whole words only: "category" contains "cat" but is not a keyword hit.
    assert (
        _rules.evaluate("community", "Sorted every category of donations", config).text_check
        == TextCheck.OK
    )


async def test_unknown_category_scores_as_other(
    session: AsyncSession, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = await _config(session)
    # Alembic's logging config (migrated_db fixture) disables loggers created before it.
    monkeypatch.setattr(_rules.logger, "disabled", False)
    with caplog.at_level(logging.WARNING, logger=_rules.__name__):
        result = _rules.evaluate("retired_category", "Helped carry the shopping home", config)
    assert result.base == config.category_base_points["other"]
    assert "retired_category" in " ".join(r.getMessage() for r in caplog.records)


def test_round_half_up_and_minimum_one() -> None:
    # §4.2 worked example: 10 at 75 % is 8 (7.5 rounded up), 2 at 75 % is 2, 1 at 50 % stays 1.
    assert round_half_up(10, 75) == 8
    assert round_half_up(10, 50) == 5
    assert round_half_up(2, 75) == 2
    assert round_half_up(2, 50) == 1
    assert round_half_up(1, 50) == 1
    assert round_half_up(12, 75) == 9
