"""Reads for the fraud module (docs/M5-DESIGN.md §3.1, D83, D84, D90).

The deed graph: one directed edge per verified deed, reporter -> doer. Device and network
hashes are compared inside SQL and never returned, so they never leave this module (CLAUDE.md
privacy rule); callers only see booleans.
"""

import uuid
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import ColumnElement, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.modules.deeds.models import Deed, DeedStatus, Invite, InviteStatus, QrScan

EdgeMatch = Literal["reporter", "doer", "any"]
Pair = tuple[uuid.UUID, uuid.UUID]


@dataclass(frozen=True)
class Edge:
    """A verified deed as a graph edge: ``reporter_id`` vouched for ``doer_id``."""

    deed_id: uuid.UUID
    reporter_id: uuid.UUID
    doer_id: uuid.UUID
    verified_at: datetime
    counts_for_points: bool


@dataclass(frozen=True)
class PairSignals:
    """Weak shared-device / shared-network signals for a pair (booleans only, never hashes)."""

    shared_device: bool
    shared_network: bool


async def verified_edges(
    session: AsyncSession,
    *,
    since: datetime,
    until: datetime,
    user_ids: Collection[uuid.UUID] | None = None,
    match: EdgeMatch = "any",
    limit: int | None = None,
) -> list[Edge]:
    """Deeds whose status is ``verified`` now, with ``since <= verified_at <= until`` (both ends
    inclusive, so the deed that triggered a check at ``t`` is inside ``until=t``), ordered by
    ``verified_at`` then deed id. Always the server's ``verified_at``, never ``occurred_at``.

    ``user_ids`` filters by person: with ``match="reporter"`` only out-edges of those users (a
    BFS frontier), ``"doer"`` only in-edges, ``"any"`` (default) every edge touching one of them.
    ``user_ids=None`` means no filter; an empty collection returns nothing. ``limit`` caps the
    rows (pass ``max_search_edges + 1`` to detect truncation).
    """
    stmt = select(
        Deed.id, Deed.reporter_user_id, Deed.doer_user_id, Deed.verified_at, Deed.counts_for_points
    ).where(
        Deed.status == DeedStatus.VERIFIED,
        Deed.verified_at >= since,
        Deed.verified_at <= until,
    )
    if user_ids is not None:
        ids = set(user_ids)
        if not ids:
            return []
        conditions: list[ColumnElement[bool]] = []
        if match in ("reporter", "any"):
            conditions.append(Deed.reporter_user_id.in_(ids))
        if match in ("doer", "any"):
            conditions.append(Deed.doer_user_id.in_(ids))
        stmt = stmt.where(or_(*conditions))
    stmt = stmt.order_by(Deed.verified_at, Deed.id)
    if limit is not None:
        stmt = stmt.limit(limit)
    edges: list[Edge] = []
    for deed_id, reporter_id, doer_id, verified_at, counts in await session.execute(stmt):
        # Guaranteed by the ``verified_has_doer`` CHECK constraint.
        assert doer_id is not None and verified_at is not None
        edges.append(Edge(deed_id, reporter_id, doer_id, verified_at, counts))
    return edges


def _unordered(a: uuid.UUID, b: uuid.UUID) -> Pair:
    return (a, b) if a <= b else (b, a)


async def _shared_scan_column(
    session: AsyncSession, column: str, users: set[uuid.UUID]
) -> set[Pair]:
    """Unordered pairs of distinct ``users`` who scanned (as witnesses) with the same non-null
    hash in ``column``. Only user ids come back; the hashes stay in the database."""
    a, b = aliased(QrScan), aliased(QrScan)
    col_a, col_b = getattr(a, column), getattr(b, column)
    rows = await session.execute(
        select(a.witness_user_id, b.witness_user_id)
        .distinct()
        .join(b, col_b == col_a)
        .where(
            col_a.is_not(None),
            a.witness_user_id.in_(users),
            b.witness_user_id.in_(users),
            a.witness_user_id < b.witness_user_id,
        )
    )
    return {(x, y) for x, y in rows}


async def _shared_device_invites(session: AsyncSession, users: set[uuid.UUID]) -> set[Pair]:
    """Unordered pairs with an accepted invite flagged ``shared_device`` between them (D6)."""
    rows = await session.execute(
        select(Invite.reporter_user_id, Invite.accepted_by)
        .distinct()
        .where(
            Invite.status == InviteStatus.ACCEPTED,
            Invite.shared_device.is_(True),
            Invite.reporter_user_id.in_(users),
            Invite.accepted_by.in_(users),
        )
    )
    return {_unordered(r, d) for r, d in rows if d is not None}


async def pair_signals(session: AsyncSession, pairs: Iterable[Pair]) -> dict[Pair, PairSignals]:
    """Signals for each ``(reporter_id, doer_id)`` pair (every pair asked for gets an entry).

    Symmetric and not time-bounded: ``shared_device`` when both people have scanned from the
    same device (``qr_scans.device_hash``) or an accepted invite between them, either way round,
    was flagged ``shared_device``; ``shared_network`` when both have scanned from the same
    network (``qr_scans.ip_hash``). Weak signals (clients choose the device ID, many people share
    a mobile carrier's address); the hashes are compared in SQL and never returned.
    """
    wanted = {(r, d) for r, d in pairs}
    users = {u for pair in wanted for u in pair}
    if not users:
        return {}
    devices = await _shared_scan_column(session, "device_hash", users)
    devices |= await _shared_device_invites(session, users)
    networks = await _shared_scan_column(session, "ip_hash", users)
    return {
        (r, d): PairSignals(
            shared_device=r != d and _unordered(r, d) in devices,
            shared_network=r != d and _unordered(r, d) in networks,
        )
        for r, d in wanted
    }


async def related_users(
    session: AsyncSession, user_id: uuid.UUID, since: datetime
) -> set[uuid.UUID]:
    """People with a deed verified at or after ``since`` with ``user_id`` as reporter or doer
    (the conflict-of-interest rule, D90). Any deed that was ever verified counts, whatever its
    status now: a withdrawn or rejected report still shows the two people know each other. The
    user is never in the result."""
    rows = await session.execute(
        select(Deed.reporter_user_id, Deed.doer_user_id).where(
            Deed.verified_at.is_not(None),
            Deed.verified_at >= since,
            or_(Deed.reporter_user_id == user_id, Deed.doer_user_id == user_id),
        )
    )
    related: set[uuid.UUID] = set()
    for reporter_id, doer_id in rows:
        related.add(reporter_id)
        if doer_id is not None:
            related.add(doer_id)
    related.discard(user_id)
    return related
