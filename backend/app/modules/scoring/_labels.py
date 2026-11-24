"""Display text the server sends so the client never maps slugs (docs/M4-DESIGN.md §3.4 #1, #6).

These are wording, not limits: every number in them comes from config. Reason labels describe
a ledger row's ``reason``; badge descriptions are built from the badge's kind and threshold;
title labels fill ``{region}`` and ``{month}`` in the configured title text.
"""

from datetime import date

from app.modules.scoring._limits import Badge, LeaderboardConfig
from app.modules.scoring.models import LedgerReason

REASON_LABELS: dict[str, str] = {
    LedgerReason.VERIFIED: "Deed verified",
    LedgerReason.PROMOTED: "Report now counts",
    LedgerReason.EDITED: "Deed edited",
    LedgerReason.PRIZE_RANK_CHANGED: "Leaderboard eligibility changed",
    LedgerReason.PHOTO_EVIDENCE: "Photo added",
    LedgerReason.DOER_DAILY_CAP: "Daily limit reached: still counts on your profile",
    LedgerReason.REPORTER_DAILY_CAP: "Daily reporting limit reached",
    LedgerReason.WITHDRAWN: "Report withdrawn",
    LedgerReason.REJECTED: "Report rejected",
    LedgerReason.REMOVED: "Deed removed",
    LedgerReason.COUNTS_FOR_POINTS_LOST: "Report no longer counts",
}


def reason_label(reason: str) -> str:
    return REASON_LABELS.get(reason, reason.replace("_", " ").capitalize())


def badge_description(badge: Badge) -> str:
    n = badge.threshold
    if badge.kind == "doer_deeds":
        return "Recognised for a verified deed" if n == 1 else f"Recognised for {n} verified deeds"
    return "Recognised a deed you witnessed" if n == 1 else f"Recognised {n} deeds you witnessed"


def month_text(month: date) -> str:
    """``November 2026``."""
    return month.strftime("%B %Y")


def month_param(month: date) -> str:
    """``2026-11``."""
    return month.strftime("%Y-%m")


def title_label(
    config: LeaderboardConfig, level_kind: str, title_key: str, region: str, month: date
) -> str:
    """The configured label of a title, filled in; the key itself if the config has no such
    title any more."""
    for level in config.levels:
        if level.kind != level_kind:
            continue
        for title in level.titles:
            if title.key == title_key:
                return title.label.replace("{region}", region).replace("{month}", month_text(month))
    return f"{title_key} · {region} · {month_text(month)}"
