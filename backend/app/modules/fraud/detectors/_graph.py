"""The deed graph for the graph detectors (reciprocal, ring): one directed edge per verified deed,
reporter -> doer, read through ``deeds.service.verified_edges`` (docs/M5-DESIGN.md §4.3).

Detectors read edges through an ``EdgeSource`` so the per-deed check (database reads) and the
nightly sweep (one preloaded, indexed list) run exactly the same rule code.
"""

import uuid
from collections import defaultdict
from collections.abc import Collection
from datetime import datetime
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import DomainEvent
from app.modules.deeds.service import DeedVerified, Edge, EdgeMatch, get_deed, verified_edges


class EdgeSource(Protocol):
    async def edges(
        self,
        *,
        since: datetime,
        until: datetime,
        user_ids: Collection[uuid.UUID],
        match: EdgeMatch,
        limit: int | None = None,
    ) -> list[Edge]:
        """Same contract as ``deeds.service.verified_edges`` with a user filter."""
        ...


class DbEdges:
    """Reads the graph from the database (the per-deed check)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def edges(
        self,
        *,
        since: datetime,
        until: datetime,
        user_ids: Collection[uuid.UUID],
        match: EdgeMatch,
        limit: int | None = None,
    ) -> list[Edge]:
        return await verified_edges(
            self._session, since=since, until=until, user_ids=user_ids, match=match, limit=limit
        )


def _order(edge: Edge) -> tuple[datetime, uuid.UUID]:
    return edge.verified_at, edge.deed_id


class MemoryEdges:
    """A preloaded set of edges, indexed by reporter and by doer (the sweep)."""

    def __init__(self, edges: Collection[Edge]) -> None:
        self._out: defaultdict[uuid.UUID, list[Edge]] = defaultdict(list)
        self._in: defaultdict[uuid.UUID, list[Edge]] = defaultdict(list)
        for e in sorted(edges, key=_order):
            self._out[e.reporter_id].append(e)
            self._in[e.doer_id].append(e)

    async def edges(
        self,
        *,
        since: datetime,
        until: datetime,
        user_ids: Collection[uuid.UUID],
        match: EdgeMatch,
        limit: int | None = None,
    ) -> list[Edge]:
        found: dict[uuid.UUID, Edge] = {}
        for user in set(user_ids):
            lists = []
            if match in ("reporter", "any"):
                lists.append(self._out.get(user, []))
            if match in ("doer", "any"):
                lists.append(self._in.get(user, []))
            for edges in lists:
                for e in edges:
                    if since <= e.verified_at <= until:
                        found[e.deed_id] = e
            if limit is not None and len(found) > limit:
                break  # over the caller's limit already: stop collecting (SEC5-L7)
        result = sorted(found.values(), key=_order)
        return result if limit is None else result[:limit]


async def verified_trigger(session: AsyncSession, event: DomainEvent) -> Edge | None:
    """The edge a ``DeedVerified`` event is about, as the deed is now: ``None`` for other events,
    and for a deed that is no longer verified (withdrawn or rejected since)."""
    if not isinstance(event, DeedVerified):
        return None
    deed = await get_deed(session, event.deed_id)
    if deed is None or deed.status != "verified" or deed.doer_id is None:
        return None
    if deed.verified_at is None or deed.doer_id == deed.reporter_id:
        return None
    return Edge(deed.id, deed.reporter_id, deed.doer_id, deed.verified_at, deed.counts_for_points)
