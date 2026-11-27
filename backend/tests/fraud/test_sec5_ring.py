"""SEC5 ring fixes (docs/M5-SEC5.md M5, M6, L7; PO Q81): one case per connected group of
colluders, bounded reads per check, a search that a busy member can't stop, visible per-deed
truncation and a chunked sweep."""

import uuid
from collections.abc import Collection
from datetime import UTC, datetime, timedelta
from itertools import pairwise, permutations

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.deeds.service import Edge, EdgeMatch
from app.modules.fraud._detect import chunk_windows, run_sweep
from app.modules.fraud._limits import RingRules, load_fraud
from app.modules.fraud.detectors import detector_by_name, ring
from app.modules.fraud.detectors._graph import DbEdges, MemoryEdges
from app.modules.fraud.detectors.base import SweepWindow
from app.modules.fraud.models import (
    DetectorRunStatus,
    DetectorRunTrigger,
    FlagCase,
    FlagCaseType,
    FlagCaseUser,
    FraudDetectorRun,
)
from tests.deeds.helpers import members
from tests.fraud.helpers import (
    DAY,
    case_deeds,
    cases_for,
    cases_touching,
    days_ago,
    deed,
    publish_fraud,
    run_fraud,
)
from tests.fraud.review_helpers import admin

RING = FlagCaseType.RING
HOUR = timedelta(hours=1)


def rules(**changes: int) -> RingRules:
    values: dict[str, int] = {
        "window_days": 30,
        "min_size": 3,
        "max_size": 5,
        "min_edge_deeds": 1,
        "min_internal_share_pct": 50,
        "max_search_edges": 5000,
        "max_queries_per_check": 12,
        "max_cycles": 5000,
        "max_group_size": 50,
        "retry_max_search_edges": 50000,
    }
    values.update(changes)
    return RingRules.model_validate(values)


class Counting:
    """An ``EdgeSource`` that counts reads."""

    def __init__(self, inner: MemoryEdges | DbEdges) -> None:
        self.inner = inner
        self.reads = 0

    async def edges(
        self,
        *,
        since: datetime,
        until: datetime,
        user_ids: Collection[uuid.UUID],
        match: EdgeMatch,
        limit: int | None = None,
    ) -> list[Edge]:
        self.reads += 1
        return await self.inner.edges(
            since=since, until=until, user_ids=user_ids, match=match, limit=limit
        )


def edge(r: uuid.UUID, d: uuid.UUID, at: datetime) -> Edge:
    return Edge(uuid.uuid4(), r, d, at, True)


def clique(n: int, start: datetime) -> tuple[list[uuid.UUID], list[Edge]]:
    people = [uuid.uuid4() for _ in range(n)]
    made = [
        edge(r, d, start + k * timedelta(minutes=1))
        for k, (r, d) in enumerate(permutations(people, 2))
    ]
    return people, made


async def _ring_cases(session: AsyncSession, deed_ids: Collection[uuid.UUID]) -> list[FlagCase]:
    return [c for c in await cases_touching(session, deed_ids) if c.type == RING]


async def _subjects(session: AsyncSession, case_id: uuid.UUID) -> set[uuid.UUID]:
    return set(
        await session.scalars(select(FlagCaseUser.user_id).where(FlagCaseUser.case_id == case_id))
    )


# --------------------------------------------------- M5: one case per group, bounded reads


async def test_a_ten_person_clique_is_one_case_with_bounded_reads(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every ordered pair of 10 people recognise each other (90 deeds). Old code: 582 ring
    cases and one read per cycle. Now: one case for all 10, at most ``max_queries_per_check``
    reads per check."""
    checks: list[int] = []

    class CountingDb(DbEdges):
        def __init__(self, session: AsyncSession) -> None:
            super().__init__(session)
            checks.append(0)

        async def edges(
            self,
            *,
            since: datetime,
            until: datetime,
            user_ids: Collection[uuid.UUID],
            match: EdgeMatch,
            limit: int | None = None,
        ) -> list[Edge]:
            checks[-1] += 1
            return await super().edges(
                since=since, until=until, user_ids=user_ids, match=match, limit=limit
            )

    monkeypatch.setattr(ring, "DbEdges", CountingDb)
    people = await members(session, 10)
    # Person n recognises, and is recognised by, everyone before them; 3 h apart (same-act
    # window 2 h), so each new triangle includes the newest person.
    pairs = [p for n in range(1, 10) for i in range(n) for p in ((i, n), (n, i))]
    made: list[uuid.UUID] = []
    start = days_ago(14)
    for k, (r, d) in enumerate(pairs):
        made.append(await deed(session, people[r], people[d], start + 3 * k * HOUR))
    await run_fraud(session, *made)

    [case] = await _ring_cases(session, made)
    assert await _subjects(session, case.id) == {p.user_id for p in people}
    # The case holds the cycle-edge deeds: every deed is on some 3-cycle of the clique.
    assert await case_deeds(session, case.id) == set(made)
    _, config = await load_fraud(session)
    assert checks and max(checks) <= config.ring.max_queries_per_check


async def test_a_thirty_person_clique_check_is_bounded_and_capped() -> None:
    """In memory: 870 edges, about 20,000 cycles through the trigger edge. One finding, at most
    ``max_queries_per_check`` reads, cycles capped at ``max_cycles``."""
    people, made = clique(30, datetime.now(UTC) - 10 * DAY)
    source = Counting(MemoryEdges(made))
    trigger = made[-1]
    search = await ring.find_rings(source, trigger, rules(max_cycles=500))
    assert not search.truncated
    assert source.reads <= 12
    [finding] = search.findings
    assert finding.subject_ids == frozenset(people)
    assert finding.join_overlapping
    assert finding.signal["capped"] is True
    assert finding.signal["cycles"] == 500


async def test_group_size_is_capped() -> None:
    _people, made = clique(12, datetime.now(UTC) - 10 * DAY)
    search = await ring.find_rings(
        MemoryEdges(made), made[-1], rules(max_group_size=6, min_internal_share_pct=20)
    )
    [finding] = search.findings
    assert len(finding.subject_ids) <= 6
    assert finding.signal["capped"] is True


async def test_cycles_sharing_a_person_found_from_different_deeds_join_one_case(
    session: AsyncSession,
) -> None:
    a, b, c, d, e = await members(session, 5)
    t0 = days_ago(10)
    first = [
        await deed(session, a, b, t0),
        await deed(session, b, c, t0 + DAY),
        await deed(session, c, a, t0 + 2 * DAY),
    ]
    await run_fraud(session, *first)
    [case] = await cases_for(session, RING, a, b, c)
    # A second ring through c only.
    second = [
        await deed(session, c, d, t0 + 3 * DAY),
        await deed(session, d, e, t0 + 4 * DAY),
        await deed(session, e, c, t0 + 5 * DAY),
    ]
    await run_fraud(session, *second)

    [joined] = await _ring_cases(session, [*first, *second])
    assert joined.id == case.id
    assert await _subjects(session, case.id) == {m.user_id for m in (a, b, c, d, e)}
    assert await case_deeds(session, case.id) == {*first, *second}
    row = await session.get(FlagCase, case.id, populate_existing=True)
    assert row is not None and row.deed_count == 6


async def test_disjoint_rings_stay_separate_cases(session: AsyncSession) -> None:
    people = await members(session, 6)
    t0 = days_ago(10)
    made: list[uuid.UUID] = []
    for group in (people[:3], people[3:]):
        for i in range(3):
            made.append(await deed(session, group[i], group[(i + 1) % 3], t0 + len(made) * DAY))
    await run_fraud(session, *made)
    assert len(await cases_for(session, RING, *people[:3])) == 1
    assert len(await cases_for(session, RING, *people[3:])) == 1


# --------------------------------------------------- M6: busy members, visible truncation


async def test_a_member_who_reports_many_others_does_not_stop_the_check() -> None:
    """Ravi -> Kavya -> Sneha -> Ravi; Kavya also recognises 100 other people. With a budget of
    60 edges a forward-only search (Kavya's 100 out-edges) would be truncated."""
    now = datetime.now(UTC)
    ravi, kavya, sneha = (uuid.uuid4() for _ in range(3))
    cycle = [(ravi, kavya), (kavya, sneha), (sneha, ravi)]
    others = [edge(kavya, uuid.uuid4(), now - 3 * DAY) for _ in range(100)]
    # Each edge of the ring in turn is the one verified last (the one that closes it).
    for last in range(3):
        ring_edges = [
            edge(r, d, now - (1 if k == last else 5 + k) * DAY) for k, (r, d) in enumerate(cycle)
        ]
        made = [*ring_edges, *others]
        search = await ring.find_rings(
            MemoryEdges(made), ring_edges[last], rules(max_search_edges=60)
        )
        assert not search.truncated, last
        [finding] = search.findings
        assert finding.subject_ids == {ravi, kavya, sneha}


async def test_a_truncated_check_is_recorded_shown_and_retried_by_the_sweep(
    client: AsyncClient, session: AsyncSession
) -> None:
    await publish_fraud(session, ring={"max_search_edges": 2})
    people = await members(session, 3)
    t0 = days_ago(10)
    made = [await deed(session, people[i], people[(i + 1) % 3], t0 + i * DAY) for i in range(3)]
    await run_fraud(session, *made)
    assert await cases_for(session, RING, *people) == []
    runs = list(
        await session.scalars(
            select(FraudDetectorRun).where(FraudDetectorRun.trigger == DetectorRunTrigger.EVENT)
        )
    )
    assert runs and all(r.status == DetectorRunStatus.TRUNCATED for r in runs)
    assert all(r.detector == "ring" and r.finished_at is not None for r in runs)

    who = await admin(session)
    status = (await client.get("/api/admin/fraud/status", headers=who.headers)).json()
    [ring_status] = [d for d in status["detectors"] if d["detector"] == "ring"]
    assert ring_status["truncated_checks_7d"] == len(runs)
    # Event rows aren't "the last run".
    assert ring_status["last_run"] is None

    detector = detector_by_name("ring")
    assert detector is not None
    window = SweepWindow(since=days_ago(11), until=datetime.now(UTC))
    # The retry budget is as small: still truncated.
    await publish_fraud(session, ring={"max_search_edges": 2, "retry_max_search_edges": 2})
    run = await run_sweep(session, detector, window, trigger=DetectorRunTrigger.CLI)
    assert run is not None and run.status == DetectorRunStatus.TRUNCATED
    assert await cases_for(session, RING, *people) == []
    # A bigger retry budget finds it.
    await publish_fraud(session, ring={"max_search_edges": 2, "retry_max_search_edges": 5000})
    run = await run_sweep(session, detector, window, trigger=DetectorRunTrigger.CLI)
    assert run is not None and run.status == DetectorRunStatus.DONE
    [case] = await cases_for(session, RING, *people)
    assert await case_deeds(session, case.id) == set(made)


async def test_the_read_cap_truncates() -> None:
    """a -> b -> c -> d -> e -> a, closed by e -> a; b also recognises 40 others. The forward
    read of b's out-edges is over its half of the budget, so the search goes backward instead:
    6 reads in all (one wasted). With a cap of 5 reads the check is truncated."""
    now = datetime.now(UTC)
    a, b, c, d, e = (uuid.uuid4() for _ in range(5))
    made = [
        edge(a, b, now - 5 * DAY),
        edge(b, c, now - 4 * DAY),
        *(edge(b, uuid.uuid4(), now - 4 * DAY) for _ in range(40)),
        edge(c, d, now - 3 * DAY),
        edge(d, e, now - 2 * DAY),
        edge(e, a, now - DAY),
    ]
    source = Counting(MemoryEdges(made))
    search = await ring.find_rings(
        source, made[-1], rules(max_search_edges=60, max_queries_per_check=6)
    )
    assert not search.truncated
    [finding] = search.findings
    assert finding.subject_ids == {a, b, c, d, e}
    assert source.reads == 6

    source = Counting(MemoryEdges(made))
    search = await ring.find_rings(
        source, made[-1], rules(max_search_edges=60, max_queries_per_check=5)
    )
    assert search.truncated and not search.findings
    assert source.reads == 5


# --------------------------------------------------- L7: chunked sweep


async def test_the_sweep_works_in_chunks_and_still_finds_the_ring(session: AsyncSession) -> None:
    await publish_fraud(session, sweep_chunk_edges=1)
    _, config = await load_fraud(session)
    people = await members(session, 3)
    t0 = days_ago(10)
    made = [await deed(session, people[i], people[(i + 1) % 3], t0 + i * DAY) for i in range(3)]
    window = SweepWindow(since=days_ago(11), until=datetime.now(UTC))
    chunks = [c async for c in chunk_windows(session, window, config.sweep_chunk_edges)]
    assert len(chunks) >= 3
    assert chunks[0].since == window.since and chunks[-1].until == window.until
    for prev, nxt in pairwise(chunks):
        assert prev.until < nxt.since

    detector = detector_by_name("ring")
    assert detector is not None
    run = await run_sweep(session, detector, window, trigger=DetectorRunTrigger.CLI)
    assert run is not None and run.status == DetectorRunStatus.DONE
    assert run.cases_opened == 1
    [case] = await cases_for(session, RING, *people)
    assert await case_deeds(session, case.id) == set(made)
    # Idempotent: a second sweep opens nothing.
    again = await run_sweep(session, detector, window, trigger=DetectorRunTrigger.CLI)
    assert again is not None and (again.cases_opened, again.deeds_added) == (0, 0)
    total = await session.scalar(
        select(func.count()).select_from(FlagCase).where(FlagCase.type == RING)
    )
    assert total is not None and total >= 1


async def test_chunks_of_an_empty_window(session: AsyncSession) -> None:
    far = datetime(2001, 1, 1, tzinfo=UTC)
    window = SweepWindow(since=far, until=far + DAY)
    assert [c async for c in chunk_windows(session, window, 10)] == [window]


async def test_reciprocal_sweep_by_chunk(session: AsyncSession) -> None:
    await publish_fraud(session, sweep_chunk_edges=1)
    a, b = await members(session, 2)
    made = [await deed(session, a, b, days_ago(5)), await deed(session, b, a, days_ago(4))]
    detector = detector_by_name("reciprocal")
    assert detector is not None
    run = await run_sweep(
        session,
        detector,
        SweepWindow(since=days_ago(6), until=datetime.now(UTC)),
        trigger=DetectorRunTrigger.SWEEP,
    )
    assert run is not None and run.status == DetectorRunStatus.DONE
    [case] = await cases_for(session, FlagCaseType.RECIPROCAL, a, b)
    assert await case_deeds(session, case.id) == set(made)
