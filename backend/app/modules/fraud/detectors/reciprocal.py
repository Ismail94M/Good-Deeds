"""Reciprocal verification (docs/M5-DESIGN.md D83; PRD §5 "A verifies B and B verifies A within
a short period").

When a deed A -> B (reporter A, doer B) is verified at ``t``: if, among the deeds verified in
``[t - reciprocal.window_days, t]``, there are at least ``reciprocal.min_deeds_each_way`` in each
direction between A and B, raise a ``reciprocal`` finding for {A, B} with every one of those
deeds. Only deeds that are still verified count (withdrawn and rejected ones drop out).
"""

import uuid
from collections import defaultdict
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import DomainEvent
from app.modules.deeds.service import Edge, verified_edges
from app.modules.fraud._limits import FraudConfig, ReciprocalRules
from app.modules.fraud.detectors._graph import (
    DbEdges,
    EdgeSource,
    MemoryEdges,
    verified_trigger,
)
from app.modules.fraud.detectors.base import Finding, SweepResult, SweepWindow, merge_findings
from app.modules.fraud.models import FlagCaseType

HOUR = timedelta(hours=1)


def _signal(a: uuid.UUID, b: uuid.UUID, pair: list[Edge]) -> dict[str, int]:
    """``a`` is the lower user id, so the same pair always reads the same way round.
    ``first_gap_hours``: from the first deed in the window to the first deed after it in the
    other direction."""
    a_to_b = sum(1 for e in pair if e.reporter_id == a)
    first = pair[0]
    back = next(e for e in pair if e.reporter_id != first.reporter_id)
    gap = int((back.verified_at - first.verified_at) / HOUR)
    return {"a_to_b": a_to_b, "b_to_a": len(pair) - a_to_b, "first_gap_hours": gap}


async def check_pair(source: EdgeSource, trigger: Edge, rules: ReciprocalRules) -> Finding | None:
    """D83 for one newly verified edge."""
    r, d = trigger.reporter_id, trigger.doer_id
    if r == d:
        return None
    edges = await source.edges(
        since=trigger.verified_at - timedelta(days=rules.window_days),
        until=trigger.verified_at,
        user_ids={r, d},
        match="reporter",
    )
    pair = [e for e in edges if {e.reporter_id, e.doer_id} == {r, d}]
    forward = sum(1 for e in pair if e.reporter_id == r)
    backward = len(pair) - forward
    if min(forward, backward) < rules.min_deeds_each_way:
        return None
    a, b = sorted((r, d))
    return Finding(
        case_type=FlagCaseType.RECIPROCAL,
        subject_ids=frozenset({r, d}),
        deed_ids=frozenset(e.deed_id for e in pair),
        signal=_signal(a, b, pair),
    )


class ReciprocalDetector:
    name = "reciprocal"
    case_type = FlagCaseType.RECIPROCAL

    async def on_event(
        self, session: AsyncSession, event: DomainEvent, config: FraudConfig
    ) -> list[Finding]:
        trigger = await verified_trigger(session, event)
        if trigger is None:
            return []
        finding = await check_pair(DbEdges(session), trigger, config.reciprocal)
        return [] if finding is None else [finding]

    async def sweep(
        self, session: AsyncSession, window: SweepWindow, config: FraudConfig
    ) -> SweepResult:
        """Every deed verified in the window, checked as if just verified."""
        rules = config.reciprocal
        edges = await verified_edges(
            session, since=window.since - timedelta(days=rules.window_days), until=window.until
        )
        directions: defaultdict[frozenset[uuid.UUID], set[uuid.UUID]] = defaultdict(set)
        for e in edges:
            directions[frozenset({e.reporter_id, e.doer_id})].add(e.reporter_id)
        source = MemoryEdges(edges)
        findings: list[Finding] = []
        for e in edges:
            # Only pairs with deeds both ways can match; skip the rest without a lookup.
            if (
                e.verified_at < window.since
                or len(directions[frozenset({e.reporter_id, e.doer_id})]) < 2
            ):
                continue
            finding = await check_pair(source, e, rules)
            if finding is not None:
                findings.append(finding)
        return SweepResult(findings=merge_findings(findings), edges_scanned=len(edges))

    def sweep_days(self, config: FraudConfig) -> int:
        return config.reciprocal.window_days
