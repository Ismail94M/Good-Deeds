"""Rings: small groups who mostly verify each other (docs/M5-DESIGN.md D84; PRD §5
"A -> B -> C -> A"; PO Q81, SEC5-M5, M6).

Graph: an edge R -> D for each pair with at least ``ring.min_edge_deeds`` verified deeds in the
window ``[t - ring.window_days, t]``. When a deed R -> D is verified at ``t``, find the simple
paths D -> ... -> R of ``min_size - 1`` to ``max_size - 1`` edges, so cycles of
``min_size``..``max_size`` people (2-person cycles are the reciprocal detector's).

**Search (bounded, both ends).** Breadth-first from D over out-edges *and* from R over in-edges,
one read per hop for the whole frontier, until the two sides together span ``max_size - 1`` hops
or one side runs out (then every path is already loaded). Each hop expands the smaller
frontier; if that read would take more than half the remaining edge budget, the other side is
tried instead, so one well-connected member (who reports or is reported by many genuine people)
doesn't stop the check. At most ``max_search_edges`` edges and ``max_queries_per_check`` reads
in all; past either the check is *truncated* and flags nothing (the per-deed check records that
for admins, and the nightly sweep retries it with ``retry_max_search_edges``).

**One finding per group (Q81).** Cycles are enumerated in memory (at most ``max_cycles``). Every
cycle through the trigger edge contains R and D, so together they form one connected group. The
internal-share test ("mostly verify each other": the deeds members received from other members
are at least ``min_internal_share_pct`` % of all the deeds they received in the window) is
computed from one batched read of the members' in-edges, first for the whole group; if the
group fails, for each cycle on its own, keeping the cycles that pass. The result is one
``Finding`` whose subjects are the union of the passing cycles' members (at most
``max_group_size`` people, then the finding is ``capped``) and whose deeds are the union of
their cycle-edge deeds. The finding asks ``raise_flag`` to join a
live ring case that shares people with it (``join_overlapping``), so a group found from
different deeds, or growing over time, stays one case.
"""

import logging
import uuid
from collections import Counter, defaultdict, deque
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from itertools import pairwise

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import DomainEvent
from app.modules.deeds.service import Edge, EdgeMatch, verified_edges
from app.modules.fraud._limits import FraudConfig, RingRules, load_fraud
from app.modules.fraud.detectors._graph import DbEdges, EdgeSource, verified_trigger
from app.modules.fraud.detectors.base import Finding, SweepResult, SweepWindow
from app.modules.fraud.models import (
    DetectorRunStatus,
    DetectorRunTrigger,
    FlagCaseType,
    FraudDetectorRun,
)

log = logging.getLogger(__name__)

NAME = "ring"
Pair = tuple[uuid.UUID, uuid.UUID]


@dataclass
class RingSearch:
    findings: list[Finding] = field(default_factory=list)
    edges_scanned: int = 0
    queries: int = 0
    truncated: bool = False


class _Truncated(Exception):
    pass


class _TooBig(Exception):
    """One hop's read went over the share of the budget it was given."""


class _Search:
    """One bounded search from one trigger edge."""

    def __init__(self, source: EdgeSource, trigger: Edge, rules: RingRules, max_edges: int) -> None:
        self.source = source
        self.rules = rules
        self.max_edges = max_edges
        self.until = trigger.verified_at
        self.since = trigger.verified_at - timedelta(days=rules.window_days)
        self.reporter = trigger.reporter_id
        self.doer = trigger.doer_id
        # (reporter, doer) -> deed id -> edge, for the pairs loaded so far.
        self.deeds: defaultdict[Pair, dict[uuid.UUID, Edge]] = defaultdict(dict)
        self.loaded = 0
        self.queries = 0

    # ------------------------------------------------------------------ reads

    async def _read(
        self, users: Iterable[uuid.UUID], match: EdgeMatch, cap: int | None = None
    ) -> list[Edge]:
        """One edge read, counted against both budgets. ``cap`` (at most the remaining edge
        budget) raises ``_TooBig`` instead of truncating, so the caller can try another way."""
        if self.queries >= self.rules.max_queries_per_check:
            raise _Truncated
        remaining = self.max_edges - self.loaded
        limit = remaining if cap is None else min(cap, remaining)
        self.queries += 1
        batch = await self.source.edges(
            since=self.since, until=self.until, user_ids=set(users), match=match, limit=limit + 1
        )
        self.loaded += len(batch)
        if len(batch) > limit:
            if limit < remaining:  # over this hop's share only: the caller may try the other side
                raise _TooBig
            raise _Truncated
        for e in batch:
            self.deeds[(e.reporter_id, e.doer_id)][e.deed_id] = e
        return batch

    def _is_edge(self, pair: Pair) -> bool:
        return pair[0] != pair[1] and len(self.deeds.get(pair, ())) >= self.rules.min_edge_deeds

    def _graph(self) -> tuple[dict[uuid.UUID, set[uuid.UUID]], dict[uuid.UUID, set[uuid.UUID]]]:
        succ: defaultdict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
        pred: defaultdict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
        for pair in self.deeds:
            if self._is_edge(pair):
                succ[pair[0]].add(pair[1])
                pred[pair[1]].add(pair[0])
        return succ, pred

    # ------------------------------------------------------------------ search

    async def _load(self) -> None:
        """Load every edge of every path D -> ... -> R of at most ``max_size - 1`` edges.

        After ``f`` forward hops every node at distance < f from D has its out-edges loaded, and
        after ``b`` backward hops every node at distance < b to R has its in-edges loaded; with
        ``f + b >= max_size - 1`` each edge of such a path is one of those. A side whose frontier
        is empty has reached everything it can, so every path is loaded already."""
        hops = self.rules.max_size - 1
        seen_f, seen_b = {self.doer}, {self.reporter}
        front_f, front_b = {self.doer}, {self.reporter}
        done_f = done_b = 0
        blocked_f = blocked_b = False
        while done_f + done_b < hops and front_f and front_b:
            sides = ["f", "b"] if len(front_f) <= len(front_b) else ["b", "f"]
            sides = [s for s in sides if not (blocked_f if s == "f" else blocked_b)]
            if not sides:
                raise _Truncated
            for i, side in enumerate(sides):
                # A side tried first may use half the remaining budget, the last one all of it.
                cap = (self.max_edges - self.loaded) // 2 if i < len(sides) - 1 else None
                try:
                    if side == "f":
                        batch = await self._read(front_f, "reporter", cap)
                    else:
                        batch = await self._read(front_b, "doer", cap)
                except _TooBig:
                    if side == "f":
                        blocked_f = True
                    else:
                        blocked_b = True
                    continue
                if side == "f":
                    nxt = {e.doer_id for e in batch if self._is_edge((e.reporter_id, e.doer_id))}
                    front_f = nxt - seen_f - {self.reporter}
                    seen_f |= front_f
                    done_f += 1
                else:
                    nxt = {
                        e.reporter_id for e in batch if self._is_edge((e.reporter_id, e.doer_id))
                    }
                    front_b = nxt - seen_b - {self.doer}
                    seen_b |= front_b
                    done_b += 1
                break
            else:
                raise _Truncated

    def _cycles(self) -> tuple[list[tuple[frozenset[uuid.UUID], set[Pair]]], bool]:
        """Each simple path D -> ... -> R as (members, cycle pairs incl. R -> D), in a fixed
        order, at most ``max_cycles`` of them. The flag says the cap was hit."""
        succ, pred = self._graph()
        max_edges = self.rules.max_size - 1
        min_edges = self.rules.min_size - 1
        # Distance to R over the loaded edges (prunes paths that can't close in time).
        to_r: dict[uuid.UUID, int] = {self.reporter: 0}
        queue = deque([self.reporter])
        while queue:
            node = queue.popleft()
            if to_r[node] >= max_edges:
                continue
            for p in pred.get(node, ()):
                if p not in to_r:
                    to_r[p] = to_r[node] + 1
                    queue.append(p)
        found: list[tuple[frozenset[uuid.UUID], set[Pair]]] = []
        capped = False

        def walk(path: list[uuid.UUID]) -> None:
            nonlocal capped
            for nxt in sorted(succ.get(path[-1], ())):
                if capped:
                    return
                edges = len(path)  # edges in path + [nxt]
                if nxt == self.reporter:
                    if edges >= min_edges:
                        if len(found) >= self.rules.max_cycles:
                            capped = True
                            return
                        full = [*path, nxt]
                        pairs = set(pairwise(full))
                        pairs.add((self.reporter, self.doer))
                        found.append((frozenset(full), pairs))
                elif nxt not in path and edges + to_r.get(nxt, max_edges + 1) <= max_edges:
                    walk([*path, nxt])

        walk([self.doer])
        found.sort(key=lambda c: (len(c[0]), sorted(str(u) for u in c[0])))
        return found, capped

    async def run(self) -> RingSearch:
        result = RingSearch()
        if self.reporter == self.doer:
            return result
        try:
            await self._load()
            cycles, cycles_capped = self._cycles()
            if not cycles:
                return RingSearch(edges_scanned=self.loaded, queries=self.queries)
            # Cycles in order while the group stays within ``max_group_size``.
            group: set[uuid.UUID] = set()
            considered: list[tuple[frozenset[uuid.UUID], set[Pair]]] = []
            group_capped = False
            for members, pairs in cycles:
                if len(group | members) > self.rules.max_group_size:
                    group_capped = True
                    continue
                group |= members
                considered.append((members, pairs))
            # One read of what the members received (also gives the trigger pair R -> D).
            received = await self._read(group, "doer")
        except _Truncated:
            log.warning(
                "fraud ring search truncated at %d edges, %d reads", self.loaded, self.queries
            )
            return RingSearch(edges_scanned=self.loaded, queries=self.queries, truncated=True)
        result.edges_scanned = self.loaded
        result.queries = self.queries
        if not self._is_edge((self.reporter, self.doer)):
            return result

        total: Counter[uuid.UUID] = Counter()
        by_pair: Counter[Pair] = Counter()
        seen: set[uuid.UUID] = set()
        for e in received:
            if e.deed_id in seen:
                continue
            seen.add(e.deed_id)
            total[e.doer_id] += 1
            by_pair[(e.reporter_id, e.doer_id)] += 1

        def internal(members: frozenset[uuid.UUID] | set[uuid.UUID]) -> tuple[int, int]:
            inside = sum(by_pair[(r, d)] for r in members for d in members if r != d)
            return inside, sum(total[m] for m in members)

        def mostly_internal(members: frozenset[uuid.UUID] | set[uuid.UUID]) -> bool:
            inside, all_in = internal(members)
            return all_in > 0 and inside * 100 >= self.rules.min_internal_share_pct * all_in

        # The whole group first: in a large group each person also receives from members
        # outside any one short cycle, so the group is the unit that "mostly verifies each
        # other" (Q81). Otherwise the cycles that pass on their own, as one group.
        whole = {m for members, _ in considered for m in members}
        if mostly_internal(whole):
            passing = considered
        else:
            passing = [(m, p) for m, p in considered if mostly_internal(m)]
        if not passing:
            return result
        passed = len(passing)
        subjects = {m for members, _ in passing for m in members}
        pairs_used = {pair for _, pairs in passing for pair in pairs}
        cycle_deeds = {d for pair in pairs_used for d in self.deeds[pair]}
        inside, all_in = internal(subjects)
        result.findings.append(
            Finding(
                case_type=FlagCaseType.RING,
                subject_ids=frozenset(subjects),
                deed_ids=frozenset(cycle_deeds),
                signal={
                    "size": len(subjects),
                    "internal_share_pct": inside * 100 // all_in,
                    "edges": len(cycle_deeds),
                    "cycles": passed,
                    "capped": cycles_capped or group_capped,
                },
                join_overlapping=True,
            )
        )
        return result


async def find_rings(
    source: EdgeSource, trigger: Edge, rules: RingRules, *, max_edges: int | None = None
) -> RingSearch:
    """D84 for one newly verified edge, with ``max_edges`` (default ``max_search_edges``)."""
    budget = rules.max_search_edges if max_edges is None else max_edges
    return await _Search(source, trigger, rules, budget).run()


async def record_truncated_check(
    session: AsyncSession, trigger: Edge, rules: RingRules, search: RingSearch
) -> None:
    """A ``fraud_detector_runs`` row (trigger ``event``, status ``truncated``) for a per-deed
    check that hit a limit, so admins see the gap on the status page (SEC5-M6)."""
    version, _ = await load_fraud(session)
    now = datetime.now(UTC)
    session.add(
        FraudDetectorRun(
            detector=NAME,
            trigger=DetectorRunTrigger.EVENT,
            window_start=trigger.verified_at - timedelta(days=rules.window_days),
            window_end=trigger.verified_at,
            config_version=version,
            started_at=now,
            finished_at=now,
            status=DetectorRunStatus.TRUNCATED,
            edges_scanned=search.edges_scanned,
        )
    )
    await session.flush()


class RingDetector:
    name = NAME
    case_type = FlagCaseType.RING

    async def on_event(
        self, session: AsyncSession, event: DomainEvent, config: FraudConfig
    ) -> list[Finding]:
        trigger = await verified_trigger(session, event)
        if trigger is None:
            return []
        search = await find_rings(DbEdges(session), trigger, config.ring)
        if search.truncated:
            await record_truncated_check(session, trigger, config.ring, search)
        return search.findings

    async def sweep(
        self, session: AsyncSession, window: SweepWindow, config: FraudConfig
    ) -> SweepResult:
        """Every deed verified in the window, checked as if just verified, with the per-deed
        reads and budget; a truncated check is retried once with ``retry_max_search_edges``
        (SEC5-M6). The caller keeps windows small (``sweep_chunk_edges``, SEC5-L7)."""
        rules = config.ring
        triggers = await verified_edges(session, since=window.since, until=window.until)
        source = DbEdges(session)
        result = SweepResult(edges_scanned=len(triggers))
        for e in triggers:
            search = await find_rings(source, e, rules)
            if search.truncated and rules.retry_max_search_edges > rules.max_search_edges:
                search = await find_rings(source, e, rules, max_edges=rules.retry_max_search_edges)
            result.edges_scanned += search.edges_scanned
            result.truncated = result.truncated or search.truncated
            result.findings.extend(search.findings)
        return result

    def sweep_days(self, config: FraudConfig) -> int:
        return config.ring.window_days
