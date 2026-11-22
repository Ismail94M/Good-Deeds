"""Rule-based evaluation of one counted report (docs/M4-DESIGN.md D54, D55, §4.4; PRD §9).

``evaluate(category, description, config)`` gives the report's base points and its text check:

- ``spam_or_empty``: too few letters, a long run of one character, or a configured spam pattern.
  The base drops to ``min_points`` (every verified deed still gets a minimum score) and the report
  is flagged for review (read by the M5 queue).
- ``category_mismatch``: the description has keywords of another category and none of its own.
  Recorded only; it never costs the doer points (English keyword lists are crude).
- ``ok`` otherwise.

The photo bonus is not decided here: it comes from the evidence record (D51, ``_ledger.py``).
Nothing here reads AI output (D62).
"""

import logging
import re
from dataclasses import dataclass
from functools import lru_cache

from app.modules.scoring._limits import ScoringConfig, TextRules
from app.modules.scoring._text import letter_count, longest_run, normalise, tokens
from app.modules.scoring.models import TextCheck

logger = logging.getLogger(__name__)

FALLBACK_CATEGORY = "other"


@dataclass(frozen=True)
class RuleResult:
    # Points before the photo bonus, caps and the pair multiplier.
    base: int
    text_check: TextCheck
    needs_review: bool


@lru_cache(maxsize=256)
def _compiled(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern)


def _word_hits(words: tuple[str, ...], token_set: frozenset[str], text: str) -> bool:
    for word in words:
        folded = normalise(word).strip()
        if " " in folded:
            if re.search(rf"(?<!\w){re.escape(folded)}(?!\w)", text):
                return True
        elif folded in token_set or f"{folded}s" in token_set or f"{folded}es" in token_set:
            return True
    return False


def text_check(category: str, description: str, rules: TextRules) -> TextCheck:
    text = normalise(description)
    if letter_count(text) < rules.min_letters or longest_run(text) > rules.max_repeated_char_run:
        return TextCheck.SPAM_OR_EMPTY
    if any(_compiled(p).search(text) for p in rules.spam_patterns):
        return TextCheck.SPAM_OR_EMPTY
    token_set = frozenset(tokens(text))
    own = rules.category_keywords.get(category, ())
    if own and _word_hits(own, token_set, text):
        return TextCheck.OK
    others = any(
        _word_hits(words, token_set, text)
        for slug, words in rules.category_keywords.items()
        if slug != category
    )
    return TextCheck.CATEGORY_MISMATCH if others else TextCheck.OK


def category_base(category: str, config: ScoringConfig) -> int:
    points = config.category_base_points.get(category)
    if points is None:
        logger.warning("scoring: category %r has no base points; scoring it as 'other'", category)
        return config.category_base_points[FALLBACK_CATEGORY]
    return points


def evaluate(category: str, description: str, config: ScoringConfig) -> RuleResult:
    check = text_check(category, description, config.text_rules)
    if check == TextCheck.SPAM_OR_EMPTY:
        return RuleResult(base=config.min_points, text_check=check, needs_review=True)
    return RuleResult(base=category_base(category, config), text_check=check, needs_review=False)
