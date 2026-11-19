"""The kind-words filter (docs/M3-DESIGN.md §3.6, Q35, Q37; test plan §8 "Social" comments).

Pure-function tests against the seeded ``feed`` config, plus test-only language packs.
"""

import ast
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.feed._kindness import check_comment, has_link, has_phone
from app.modules.feed._limits import FeedLimits, load_limits

FEED_DIR = Path(__file__).resolve().parents[2] / "app" / "modules" / "feed"

HI_PACK: dict[str, Any] = {
    # A Devanagari and a Latin-transliterated test term (not real Hindi abuse).
    "blocked_terms": ["परीक्षण", "testshabd"],
    "substitutions": {"9": "g"},
    "keep_marks": ["ि", "ी", "्"],
    "squashed_min_letters": 5,
}


@pytest.fixture
async def cfg(session: AsyncSession) -> FeedLimits:
    return (await load_limits(session))[1]


def _with(cfg: FeedLimits, **changes: Any) -> FeedLimits:
    return FeedLimits.model_validate({**cfg.model_dump(), **changes})


@pytest.mark.parametrize(
    "text",
    [
        "You are stupid",
        "STUPID",
        "5tup1d!",  # leetspeak
        "stuuuupid",  # repeated letters
        "s t u p i d",  # spaced out
        "stu.pid",
        "s-t-u-p-i-d person",
        "st​upid",  # zero-width space
        "stúpid",  # combining accent
        "\uff53\uff54\uff55\uff50\uff49\uff44",  # full-width letters
        "please shut up",  # a phrase
        "shutup",
        "what an idiot",
    ],
)
async def test_blocks_listed_terms_and_their_disguises(cfg: FeedLimits, text: str) -> None:
    assert check_comment(text, cfg) == "not_kind"


@pytest.mark.parametrize(
    "text",
    [
        "Thank you so much for helping!",
        "That was really kind, well done",
        "She came closer to help",  # contains "loser" inside a word
        "Dumbbells were carried up the stairs",  # "dumb" only as part of a word
        "Scunthorpe",
        "Mumbai to Bengaluru",
        "",
    ],
)
async def test_allows_kind_comments_and_substrings(cfg: FeedLimits, text: str) -> None:
    assert check_comment(text, cfg) is None


@pytest.mark.parametrize(
    "text",
    [
        "see https://example.org",
        "visit www.example",
        "go to example.com now",
        "EXAMPLE.IN",
    ],
)
async def test_links(cfg: FeedLimits, text: str) -> None:
    assert has_link(text)
    assert check_comment(text, cfg) == "link"
    assert check_comment(text, _with(cfg, comment_block_links=False)) is None


@pytest.mark.parametrize(
    "text",
    ["call 9876543210", "+91 98765 43210", "98765-43210", "(987) 654-3210", "+919876543210"],
)
async def test_phone_numbers(cfg: FeedLimits, text: str) -> None:
    assert has_phone(text)
    assert check_comment(text, cfg) == "phone"
    assert check_comment(text, _with(cfg, comment_block_phone_numbers=False)) is None


async def test_short_numbers_are_fine(cfg: FeedLimits) -> None:
    assert check_comment("Helped 25 people at 9:30 on 12-11-2026", cfg) is None


async def test_unkind_wins_over_link_and_phone(cfg: FeedLimits) -> None:
    assert check_comment("stupid example.com 9876543210", cfg) == "not_kind"
    assert check_comment("example.com 9876543210", cfg) == "link"


async def test_only_enabled_packs_apply(cfg: FeedLimits) -> None:
    packs = {**cfg.model_dump()["comment_filter_languages"], "hi": HI_PACK}
    hi_off = _with(cfg, comment_filter_languages=packs)
    assert check_comment("यह परीक्षण है", hi_off) is None
    assert check_comment("bas testshabd", hi_off) is None
    hi_on = _with(
        cfg, comment_filter_languages=packs, comment_filter_enabled_languages=["en", "hi"]
    )
    assert check_comment("यह परीक्षण है", hi_on) == "not_kind"
    assert check_comment("bas testshabd", hi_on) == "not_kind"
    assert check_comment("t e s t s h a b d", hi_on) == "not_kind"
    # English still applies alongside.
    assert check_comment("stupid", hi_on) == "not_kind"


async def test_packs_never_share_substitutions(cfg: FeedLimits) -> None:
    """ "9" -> "g" exists only in the test pack, so "9" never turns into a letter for English, and
    English's "1" -> "i" doesn't apply to the test pack's terms."""
    packs = {
        **cfg.model_dump()["comment_filter_languages"],
        "hi": {**HI_PACK, "blocked_terms": ["gaali", "chiiz"]},
    }
    hi_on = _with(
        cfg, comment_filter_languages=packs, comment_filter_enabled_languages=["en", "hi"]
    )
    assert check_comment("9aali", hi_on) == "not_kind"  # its own substitution
    assert check_comment("ch11z", hi_on) is None  # English's "1" -> "i" isn't the test pack's


async def test_config_validation_of_packs(cfg: FeedLimits) -> None:
    with pytest.raises(ValidationError):
        _with(cfg, comment_filter_enabled_languages=["en", "hi"])  # no pack for hi
    packs = cfg.model_dump()["comment_filter_languages"]
    with pytest.raises(ValidationError):
        _with(cfg, comment_filter_languages={**packs, "hi": {**HI_PACK, "unknown_key": 1}})
    with pytest.raises(ValidationError):
        _with(cfg, comment_filter_languages={**packs, "hindi": HI_PACK})


def test_no_feed_code_names_a_language() -> None:
    """Q35: only ``_kindness.py`` and the config know which languages exist, and even
    ``_kindness.py`` names none."""
    codes = {"en", "hi", "kn", "ta", "te", "ml", "mr", "ur", "bn"}
    problems = []
    for path in FEED_DIR.rglob("*.py"):
        if path.name == "_limits.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and node.value in codes:
                problems.append(f"{path.name}: {node.value!r}")
    assert problems == []
