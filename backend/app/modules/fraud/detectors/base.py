"""The detector plug-in interface (docs/M5-DESIGN.md D102).

A detector finds an unusual pattern and returns ``Finding``s. It never writes a case: the
caller (``fraud._detect``) passes each finding to ``fraud._cases.raise_flag``, which opens or
joins a case. (A detector may record its own truncated per-deed check in
``fraud_detector_runs``, as the ring detector does, SEC5-M6.) Opening a case changes nothing
for anyone (PO principle 1, D98).

To add a detector: write a class satisfying ``Detector`` in a new file of this package, list an
instance in ``DETECTORS`` (``detectors/__init__.py``) and publish a ``fraud`` config version with
its ``detectors_enabled`` key (plus a typed field in ``FraudConfig`` if it has thresholds). A new
case type also needs a value of the ``flag_case_type`` enum (a small migration). Nothing in
cases, holds, review, notices, appeals or scoring changes.
"""

import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import DomainEvent
from app.modules.fraud._limits import FraudConfig
from app.modules.fraud.models import FlagCaseType

# A signal value: a number or a short code. Never names, text, coordinates or hashes (D95).
SignalValue = int | float | str | bool


@dataclass(frozen=True)
class Finding:
    """One pattern worth an admin's look: who (``subject_ids``), which deeds, and the numbers
    behind it (``signal``). Findings for the same type and subjects land in the same case."""

    case_type: FlagCaseType
    subject_ids: frozenset[uuid.UUID]
    deed_ids: frozenset[uuid.UUID]
    signal: Mapping[str, SignalValue] = field(default_factory=dict)
    # Join a live case of the same type that shares any subject (adding the new subjects to it)
    # instead of opening a case per exact subject set: for detectors whose findings are parts of
    # one group, such as rings (PO Q81). ``raise_flag`` implements it generically.
    join_overlapping: bool = False

    def __post_init__(self) -> None:
        if not self.subject_ids:
            raise ValueError("a finding needs at least one subject")
        if not self.deed_ids:
            raise ValueError("a finding needs at least one deed")
        for key, value in self.signal.items():
            if not isinstance(value, int | float | str | bool):
                raise ValueError(f"signal {key!r} must be a number or a code")
            if isinstance(value, str) and len(value) > 40:
                raise ValueError(f"signal {key!r} must be a short code")


@dataclass(frozen=True)
class SweepWindow:
    """Deeds verified in ``[since, until]`` are re-checked as if each had just been verified
    (a detector may look further back from each one, by its own window)."""

    since: datetime
    until: datetime


@dataclass
class SweepResult:
    findings: list[Finding] = field(default_factory=list)
    # Deed edges the sweep read (for the admin status page).
    edges_scanned: int = 0
    # A bounded search hit its limit somewhere; nothing was flagged from that partial search.
    truncated: bool = False


class Detector(Protocol):
    """A fraud detector plug-in (D102)."""

    # Unique, matches ``fraud_detector_runs.detector`` (``^[a-z][a-z0-9_]{0,39}$``) and the
    # ``detectors_enabled`` config key that switches it.
    name: str
    case_type: FlagCaseType

    async def on_event(
        self, session: AsyncSession, event: DomainEvent, config: FraudConfig
    ) -> list[Finding]:
        """Check one event (e.g. ``DeedVerified``). Returns ``[]`` for events it ignores."""
        ...

    async def sweep(
        self, session: AsyncSession, window: SweepWindow, config: FraudConfig
    ) -> SweepResult:
        """Re-check everything in ``window`` (the nightly safety net and the CLI)."""
        ...

    def sweep_days(self, config: FraudConfig) -> int:
        """How many days back the nightly sweep re-checks (usually the detector's window)."""
        ...


def merge_findings(findings: Iterable[Finding]) -> list[Finding]:
    """One finding per (type, subjects), with the union of their deeds and the last signal, in
    first-seen order. Raising the merged list gives the same cases and deeds as raising each."""
    merged: dict[tuple[FlagCaseType, frozenset[uuid.UUID]], Finding] = {}
    for f in findings:
        key = (f.case_type, f.subject_ids)
        old = merged.get(key)
        merged[key] = (
            f
            if old is None
            else Finding(
                f.case_type,
                f.subject_ids,
                old.deed_ids | f.deed_ids,
                f.signal,
                join_overlapping=f.join_overlapping or old.join_overlapping,
            )
        )
    return list(merged.values())
