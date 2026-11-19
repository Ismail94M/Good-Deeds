"""Kind-words filter for comments (docs/M3-DESIGN.md §3.6, Q35, Q37).

``check_comment(text, cfg)`` returns None when the comment may be posted, else the first reason it
can't: ``"not_kind"``, then ``"link"``, then ``"phone"``. The caller turns these into friendly
errors that never say which word matched.

Languages are data, not code (Q35): each enabled pack in ``comment_filter_languages`` is applied on
its own (its terms, its look-alike substitutions, the combining marks its script keeps), and a
comment is checked against every enabled pack. Nothing else in the app knows which languages
exist. Tokenising is script-agnostic: a token is a run of Unicode letters and kept marks.

Matching, per pack:
1. Normalise (NFKC, casefold, drop zero-width characters and combining marks except the pack's
   ``keep_marks``, apply the pack's substitutions, collapse a character repeated 3+ times to 2).
   Terms are normalised the same way.
2. A term matches when its tokens appear as consecutive whole tokens ("shut up").
3. "Squashed" matching for terms of at least ``squashed_min_letters`` letters: the term's letters
   equal the concatenation of consecutive tokens ("s t u p i d", "stu.pid", "shutup"). The match
   must start and end on token boundaries, so a long word that merely *contains* a term
   ("closer" for "loser") never matches, and short terms only ever match whole tokens.

Links and phone numbers are checked on the original text and are language-independent.
"""

import re
import unicodedata
from typing import Literal

from app.modules.feed._limits import FeedLimits, LanguagePack

Problem = Literal["not_kind", "link", "phone"]

_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍‎‏⁠﻿­͏"))
_REPEATS = re.compile(r"(.)\1{2,}", re.DOTALL)
_DOUBLES = re.compile(r"(.)\1", re.DOTALL)
_LINK = re.compile(
    r"https?://|www\.|\b[a-z0-9][a-z0-9-]*\.(?:com|in|net|org|co|io|me|info|biz|xyz|ly|gl|app"
    r"|link|site|online|shop|store|to|be|tv|us|uk|ai|dev)\b",
    re.IGNORECASE,
)
_PHONE_SEPARATORS = re.compile(r"[\s\-().]")
_PHONE = re.compile(r"\d{10,}")


def _normalise(text: str, pack: LanguagePack) -> str:
    text = unicodedata.normalize("NFKC", text).casefold().translate(_ZERO_WIDTH)
    keep = set(pack.keep_marks)
    text = "".join(
        ch
        for ch in unicodedata.normalize("NFD", text)
        if not unicodedata.category(ch).startswith("M") or ch in keep
    )
    text = unicodedata.normalize("NFC", text)
    text = "".join(pack.substitutions.get(ch, ch) for ch in text)
    return _REPEATS.sub(r"\1\1", text)


def _tokens(text: str, pack: LanguagePack) -> list[str]:
    """Runs of letters (any script) and the pack's kept marks."""
    keep = set(pack.keep_marks)
    tokens: list[str] = []
    current: list[str] = []
    for ch in text:
        if unicodedata.category(ch).startswith("L") or ch in keep:
            current.append(ch)
        elif current:
            tokens.append("".join(current))
            current = []
    if current:
        tokens.append("".join(current))
    return tokens


def _spans_tokens(tokens: list[str], target: str) -> bool:
    """Whether ``target`` equals the concatenation of some run of consecutive tokens."""
    for start in range(len(tokens)):
        joined = ""
        for token in tokens[start:]:
            joined += token
            if len(joined) >= len(target):
                break
        if joined == target:
            return True
    return False


def _single(tokens: list[str]) -> list[str]:
    return [_DOUBLES.sub(r"\1", t) for t in tokens]


def _matches(tokens: list[str], term_tokens: list[str], squashed_min: int) -> bool:
    k = len(term_tokens)
    if any(tokens[i : i + k] == term_tokens for i in range(len(tokens) - k + 1)):
        return True
    letters = "".join(term_tokens)
    return len(letters) >= squashed_min and _spans_tokens(tokens, letters)


def _unkind(text: str, pack: LanguagePack) -> bool:
    tokens = _tokens(_normalise(text, pack), pack)
    if not tokens:
        return False
    # Also compared with every doubled letter made single, on both sides, so "stuuuupid" (two
    # after normalising) still meets "stupid".
    single = _single(tokens)
    for term in pack.blocked_terms:
        term_tokens = _tokens(_normalise(term, pack), pack)
        if not term_tokens:
            continue
        if _matches(tokens, term_tokens, pack.squashed_min_letters) or _matches(
            single, _single(term_tokens), pack.squashed_min_letters
        ):
            return True
    return False


def has_link(text: str) -> bool:
    return _LINK.search(unicodedata.normalize("NFKC", text)) is not None


def has_phone(text: str) -> bool:
    """10 or more digits once spaces, dashes, brackets, dots and ``+91`` are removed."""
    digits = _PHONE_SEPARATORS.sub("", unicodedata.normalize("NFKC", text)).replace("+91", "")
    return _PHONE.search(digits) is not None


def check_comment(text: str, cfg: FeedLimits) -> Problem | None:
    for code in cfg.comment_filter_enabled_languages:
        if _unkind(text, cfg.comment_filter_languages[code]):
            return "not_kind"
    if cfg.comment_block_links and has_link(text):
        return "link"
    if cfg.comment_block_phone_numbers and has_phone(text):
        return "phone"
    return None
