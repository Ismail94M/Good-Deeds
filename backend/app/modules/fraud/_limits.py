"""Typed values of the ``fraud`` config namespace (docs/M5-DESIGN.md §2.4, D97).

Every key is required and has no default: the ``config_versions`` rows (seeded as ``fraud`` v1 by
migration 0026) are the only source of these values. Change them by publishing a new version.

``detectors_enabled`` and ``hold_on_open`` are maps keyed by detector / case type name, so a new
detector (D102) adds a key in a new config version instead of a new field here. Every built-in
case type must be present in both maps. A detector's own thresholds are a typed field below
(``reciprocal``, ``ring``, ...): adding a detector with thresholds adds one field.
"""

import re
from typing import Annotated, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.runtime_config import RuntimeConfig, load
from app.modules.fraud.models import FlagCaseType

# Rate specs are "<count>/<seconds>".
RATE_SPEC = r"^[1-9][0-9]*/[1-9][0-9]*$"
# Detector and case type names (also the ``fraud_detector_runs.detector`` CHECK).
NAME_PATTERN = r"^[a-z][a-z0-9_]{0,39}$"
CASE_TYPES = frozenset(t.value for t in FlagCaseType)

Percent = Annotated[int, Field(ge=1, le=100)]
WindowDays = Annotated[int, Field(ge=1, le=365)]


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ExplanationRules(_Frozen):
    """D99: how long a person has to reply, and how long a reply may be."""

    reply_days: int = Field(ge=3, le=60)
    reply_max_chars: int = Field(ge=1, le=2000)


class AppealRules(_Frozen):
    """D100: one appeal per notice within ``window_days``."""

    window_days: int = Field(ge=7, le=90)
    reason_min_chars: int = Field(ge=1)
    reason_max_chars: int = Field(ge=1, le=2000)

    @model_validator(mode="after")
    def _valid(self) -> Self:
        if self.reason_min_chars > self.reason_max_chars:
            raise ValueError("appeal.reason_min_chars must not exceed reason_max_chars")
        return self


class ReciprocalRules(_Frozen):
    """D83: A reported B and B reported A within ``window_days``, at least
    ``min_deeds_each_way`` verified deeds in each direction."""

    window_days: WindowDays
    min_deeds_each_way: int = Field(ge=1)


class RingRules(_Frozen):
    """D84: cycles of ``min_size``..``max_size`` people in ``window_days``, each edge with at
    least ``min_edge_deeds`` deeds, whose members received at least ``min_internal_share_pct`` of
    their deeds from each other. The search loads at most ``max_search_edges`` deed edges."""

    window_days: WindowDays
    min_size: int = Field(ge=3)
    max_size: int = Field(le=6)
    min_edge_deeds: int = Field(ge=1)
    min_internal_share_pct: Percent
    max_search_edges: int = Field(ge=1, le=1_000_000)

    @model_validator(mode="after")
    def _valid(self) -> Self:
        if self.min_size > self.max_size:
            raise ValueError("ring.min_size must not exceed ring.max_size")
        return self


class ConcentrationRules(_Frozen):
    """D85: a case once a doer has ``min_doer_points`` prize-rank points in a month and one
    reporter's share is above ``max_reporter_share_pct``."""

    max_reporter_share_pct: Percent
    min_doer_points: int = Field(ge=0)


class InviteAnomalyRules(_Frozen):
    """Typed now for the deferred invite-anomaly detector (D87, §4.5); nothing reads them in M5."""

    shared_device_accepts_per_pair_month: int = Field(ge=1)
    new_account_hours: int = Field(ge=1)
    new_account_accepts_per_reporter_30d: int = Field(ge=1)
    accept_within_minutes: int = Field(ge=1)


class OverrideSecondAdmin(_Frozen):
    """D91, Q77: overrides that need a second admin's approval."""

    max_cut_points: int = Field(ge=0)
    cut_to_zero: bool
    expire_days: int = Field(ge=1, le=90)


class FraudConfig(RuntimeConfig):
    """Config namespace ``fraud`` (detectors, review, explanations, appeals, overrides)."""

    namespace = "fraud"

    # Month rules (review deadlines) use this zone, applied to server times.
    timezone: str
    # Detector / case type name -> on. A finding is raised only when both its detector and its
    # case type are on; the sweep skips a detector that is off.
    detectors_enabled: dict[str, bool]
    # Case type -> hold the case's deeds when it opens. All false (PO principle 1, D80, Q73);
    # turning one on needs PO sign-off.
    hold_on_open: dict[str, bool]
    explanation: ExplanationRules
    appeal: AppealRules
    # Reason key -> neutral member text (D101).
    member_reason_labels: dict[str, str]
    reciprocal: ReciprocalRules
    ring: RingRules
    concentration: ConcentrationRules
    invite_anomaly: InviteAnomalyRules
    # D90: an admin with a verified deed with a subject in this many days is conflicted.
    coi_lookback_days: WindowDays
    note_min_chars: int = Field(ge=5)
    note_max_chars: int = Field(ge=1, le=2000)
    override_max_points: int = Field(ge=0)
    override_second_admin: OverrideSecondAdmin
    override_reason_codes: tuple[str, ...] = Field(min_length=1)
    page_size: int = Field(gt=0, le=200)
    rate_admin_read: str = Field(pattern=RATE_SPEC)
    rate_admin_write: str = Field(pattern=RATE_SPEC)

    @model_validator(mode="after")
    def _valid(self) -> Self:
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown timezone {self.timezone!r}") from exc
        for field in ("detectors_enabled", "hold_on_open"):
            values: dict[str, bool] = getattr(self, field)
            missing = CASE_TYPES - values.keys()
            if missing:
                raise ValueError(f"{field} is missing {sorted(missing)}")
            bad = [k for k in values if not re.fullmatch(NAME_PATTERN, k)]
            if bad:
                raise ValueError(f"{field} has invalid names {sorted(bad)}")
        unknown_holds = self.hold_on_open.keys() - CASE_TYPES
        if unknown_holds:
            raise ValueError(f"hold_on_open has unknown case types {sorted(unknown_holds)}")
        if self.note_min_chars > self.note_max_chars:
            raise ValueError("note_min_chars must not exceed note_max_chars")
        codes = self.override_reason_codes
        if len(set(codes)) != len(codes):
            raise ValueError("override_reason_codes must not repeat")
        unlabelled = [c for c in codes if not self.member_reason_labels.get(c, "").strip()]
        if unlabelled:
            raise ValueError(f"member_reason_labels needs a label for {unlabelled}")
        if not self.member_reason_labels.get("case", "").strip():
            raise ValueError("member_reason_labels needs a label for 'case'")
        return self

    @property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def enabled(self, name: str) -> bool:
        """Whether the detector or case type ``name`` is on (unknown names are off)."""
        return self.detectors_enabled.get(name, False)

    def holds_on_open(self, case_type: str) -> bool:
        return self.hold_on_open.get(case_type, False)


async def load_fraud(session: AsyncSession) -> tuple[int, FraudConfig]:
    """``(version, config)`` of the active ``fraud`` config. Fails closed if it is missing."""
    return await load(session, FraudConfig)
