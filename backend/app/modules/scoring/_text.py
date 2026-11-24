"""Text normalisation for the rule-based checks (docs/M4-DESIGN.md D55, §4.4).

The look-alike folding follows ``feed/_kindness.py`` (copied, because scoring may not import
``feed``, D47): NFKC, casefold, zero-width characters dropped, combining marks removed. Input is
cut to ``MAX_TEXT_CHARS`` first, so every check, including the config's regular expressions, runs
on bounded text.
"""

import re
import unicodedata

from app.modules.scoring._limits import MAX_TEXT_CHARS

_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍‎‏⁠﻿­͏"))
_RUNS = re.compile(r"(\S)\1*")


def normalise(text: str) -> str:
    """Folded, bounded text: NFKC, casefold, no zero-width characters, no combining marks."""
    text = unicodedata.normalize("NFKC", text[:MAX_TEXT_CHARS]).casefold().translate(_ZERO_WIDTH)
    text = "".join(
        ch for ch in unicodedata.normalize("NFD", text) if unicodedata.category(ch)[0] != "M"
    )
    return unicodedata.normalize("NFC", text)[:MAX_TEXT_CHARS]


def tokens(text: str) -> list[str]:
    """Runs of letters (any script), in order."""
    out: list[str] = []
    current: list[str] = []
    for ch in text:
        if unicodedata.category(ch)[0] == "L":
            current.append(ch)
        elif current:
            out.append("".join(current))
            current = []
    if current:
        out.append("".join(current))
    return out


def letter_count(text: str) -> int:
    return sum(1 for ch in text if unicodedata.category(ch)[0] == "L")


def longest_run(text: str) -> int:
    """The longest run of one repeated non-space character ("!!!!" is 4)."""
    return max((len(m.group(0)) for m in _RUNS.finditer(text)), default=0)
