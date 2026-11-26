"""Rings: small groups who mostly verify each other (docs/M5-DESIGN.md D84; PRD §5
"A -> B -> C -> A").

Graph: an edge R -> D for each pair with at least ``ring.min_edge_deeds`` verified deeds in the
window ``[t - ring.window_days, t]``. When a deed R -> D is verified at ``t``, search the simple
paths D -> ... -> R of 2 to ``max_size - 1`` edges, so cycles of ``min_size``..``max_size``
people (2-person cycles are the reciprocal detector's). The search is breadth-first from D over
out-edges, loading each frontier's edges in one read, with at most ``ring.max_search_edges``
edges loaded in all; past that the search is *truncated* and flags nothing.

A cycle is a finding only if its members "mostly verify each other": the deeds members received
from other members are at least ``ring.min_internal_share_pct`` % of all the deeds the members
received in the window. Members are a set, so the same ring found from any of its edges gives the
same subjects (and the same case). Finding deeds: every deed on the cycle's edges in the window.
"""

import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import DomainEvent
from app.modules.deeds.service import Edge, verified_edges
from app.modules.fraud._limits import FraudConfig, RingRules
from app.modules.fraud.detectors._graph import (
    DbEdges,
    EdgeSource,
    MemoryEdges,
    verified_trigger,
)
from app.modules.fraud.detectors.base import Finding, SweepResult, SweepWindow, merge_findings
from app.modules.fraud.models import FlagCaseType

log = logging.getLogger(__name__)

Pair = tuple[uuid.UUID, uuid.UUID]


@dataclass
class RingSearch:
    findings: list[Finding] = field(default_factory=list)
    edges_scanned: int = 0
    truncated: bool = False


class _Truncated(Exception):
    pass


class _Search:
    """One bounded search from one trigger edge."""

    def __init__(self, source: EdgeSource, trigger: Edge, rules: RingRules) -> None:
        self.source = source
        self.rules = rules
        self.until = trigger.verified_at
        self.since = trigger.verified_at - timedelta(days=rules.window_days)
        self.reporter = trigger.reporter_id
        self.doer = trigger.doer_id
        # (reporter, doer) -> deeds in the window, for the pairs loaded so far.
        self.deeds: defaultdict[Pair, list[Edge]] = defaultdict(list)
        self.loaded = 0

    async def _load(self, users: set[uuid.UUID]) -> None:
        """Out-edges of ``users``, counted against ``max_search_edges``."""
        budget = self.rules.max_search_edges - self.loaded
        batch = await self.source.edges(
            since=self.since, until=self.until, user_ids=users, match="reporter", limit=budget + 1
        )
        self.loaded += len(batch)
        if self.loaded > self.rules.max_search_edges:
            raise _Truncated
        for e in batch:
            self.deeds[(e.reporter_id, e.doer_id)].append(e)

    def _is_edge(self, pair: Pair) -> bool:
        return len(self.deeds.get(pair, ())) >= self.rules.min_edge_deeds

    def _successors(self) -> defaultdict[uuid.UUID, set[uuid.UUID]]:
        succ: defaultdict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
        for (r, d), _ in self.deeds.items():
            if r != d and self._is_edge((r, d)):
                succ[r].add(d)
        return succ

    async def _expand(self) -> None:
        """BFS from the doer: nodes up to ``max_size - 2`` edges away get their out-edges
        loaded (a path of ``max_size - 1`` edges ends at the reporter, who is never expanded)."""
        expanded: set[uuid.UUID] = set()
        frontier = {self.doer}
        for _ in range(self.rules.max_size - 1):
            to_load = frontier - expanded - {self.reporter}
            if not to_load:
                return
            await self._load(to_load)
            expanded |= to_load
            succ = self._successors()
            frontier = {d for u in to_load for d in succ[u]}

    def _cycles(self) -> dict[frozenset[uuid.UUID], set[Pair]]:
        """Member set -> the cycle edges of every simple path doer -> ... -> reporter found."""
        succ = self._successors()
        found: dict[frozenset[uuid.UUID], set[Pair]] = {}
        max_edges = self.rules.max_size - 1
        min_edges = self.rules.min_size - 1

        def walk(path: list[uuid.UUID]) -> None:
            for nxt in sorted(succ[path[-1]]):
                edges = len(path)  # edges in path + [nxt]
                if nxt == self.reporter:
                    if edges >= min_edges:
                        members = frozenset(path) | {nxt}
                        pairs = set(zip(path, [*path[1:], nxt], strict=True))
                        pairs.add((self.reporter, self.doer))
                        found.setdefault(members, set()).update(pairs)
                elif nxt not in path and edges < max_edges:
                    walk([*path, nxt])

        walk([self.doer])
        return found

    async def run(self) -> RingSearch:
        result = RingSearch()
        if self.reporter == self.doer:
            return result
        try:
            await self._expand()
            cycles = self._cycles()
            if cycles:
                # The trigger's own pair must be a graph edge too.
                await self._load({self.reporter})
        except _Truncated:
            log.warning("fraud ring search truncated at %d edges", self.loaded)
            return RingSearch(edges_scanned=self.loaded, truncated=True)
        result.edges_scanned = self.loaded
        if not cycles or not self._is_edge((self.reporter, self.doer)):
            return result
        for members, pairs in cycles.items():
            received = await self.source.edges(
                since=self.since, until=self.until, user_ids=members, match="doer"
            )
            result.edges_scanned += len(received)
            internal = sum(1 for e in received if e.reporter_id in members)
            total = len(received)
            if total == 0 or internal * 100 < self.rules.min_internal_share_pct * total:
                continue
            cycle_deeds = [e for pair in pairs for e in self.deeds[pair]]
            result.findings.append(
                Finding(
                    case_type=FlagCaseType.RING,
                    subject_ids=members,
                    deed_ids=frozenset(e.deed_id for e in cycle_deeds),
                    signal={
                        "size": len(members),
                        "internal_share_pct": internal * 100 // total,
                        "edges": len(cycle_deeds),
                    },
                )
            )
        return result


async def find_rings(source: EdgeSource, trigger: Edge, rules: RingRules) -> RingSearch:
    """D84 for one newly verified edge."""
    return await _Search(source, trigger, rules).run()


class RingDetector:
    name = "ring"
    case_type = FlagCaseType.RING

    async def on_event(
        self, session: AsyncSession, event: DomainEvent, config: FraudConfig
    ) -> list[Finding]:
        trigger = await verified_trigger(session, event)
        if trigger is None:
            return []
        return (await find_rings(DbEdges(session), trigger, config.ring)).findings

    async def sweep(
        self, session: AsyncSession, window: SweepWindow, config: FraudConfig
    ) -> SweepResult:
        """Every deed verified in the window, checked as if just verified (each search is
        bounded by ``max_search_edges`` as in the per-deed check)."""
        rules = config.ring
        edges = await verified_edges(
            session, since=window.since - timedelta(days=rules.window_days), until=window.until
        )
        source = MemoryEdges(edges)
        result = SweepResult(edges_scanned=len(edges))
        findings: list[Finding] = []
        for e in edges:
            if e.verified_at < window.since:
                continue
            search = await find_rings(source, e, rules)
            result.truncated = result.truncated or search.truncated
            findings.extend(search.findings)
        result.findings = merge_findings(findings)
        return result

    def sweep_days(self, config: FraudConfig) -> int:
        return config.ring.window_days
