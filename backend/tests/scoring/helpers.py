"""Helpers for scoring tests: run the scoring handlers as the worker would, and read the ledger."""

import uuid
from collections.abc import Iterable
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import OutboxEvent, emit, handlers_for, run_handler
from app.modules.media.service import DeedPhotoReady
from app.modules.scoring.models import (
    ImpactEvaluation,
    LedgerKind,
    LedgerRole,
    PointsLedger,
)
from tests.deeds.helpers import OUTBOX_WRITE_ORDER

SCORING_PREFIX = "app.modules.scoring."


async def run_scoring(session: AsyncSession, *deed_ids: uuid.UUID, reverse: bool = False) -> int:
    """Run every scoring handler not yet run for the outbox events about these deeds (oldest
    first, or newest first with ``reverse``). Returns how many ran."""
    rows = list(
        await session.scalars(
            select(OutboxEvent)
            .where(OutboxEvent.payload["deed_id"].astext.in_([str(d) for d in deed_ids]))
            .order_by(*OUTBOX_WRITE_ORDER)
        )
    )
    if reverse:
        rows.reverse()
    ran = 0
    for row in rows:
        for name in handlers_for(row.event_type):
            if name.startswith(SCORING_PREFIX) and await run_handler(session, row.id, name):
                ran += 1
    return ran


async def photo_ready(
    session: AsyncSession,
    deed_id: uuid.UUID,
    uploader_id: uuid.UUID,
    attached_at: datetime,
    photo_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """Emit ``DeedPhotoReady`` as the media worker would; returns the photo id."""
    photo_id = photo_id or uuid.uuid4()
    emit(
        session,
        DeedPhotoReady(
            photo_id=photo_id, deed_id=deed_id, uploader_id=uploader_id, attached_at=attached_at
        ),
    )
    await session.flush()
    return photo_id


async def ledger(session: AsyncSession, deed_id: uuid.UUID) -> list[PointsLedger]:
    rows = await session.scalars(
        select(PointsLedger)
        .where(PointsLedger.deed_id == deed_id)
        .order_by(PointsLedger.created_at, PointsLedger.kind.desc())
        .execution_options(populate_existing=True)
    )
    return list(rows)


async def active(
    session: AsyncSession, deed_id: uuid.UUID, role: LedgerRole
) -> PointsLedger | None:
    """The (deed, role)'s active entry: an award or decision not reversed."""
    rows = await ledger(session, deed_id)
    reversed_ids = {r.reverses_entry_id for r in rows if r.kind == LedgerKind.REVERSAL}
    found = [
        r
        for r in rows
        if r.role == role and r.kind != LedgerKind.REVERSAL and r.id not in reversed_ids
    ]
    assert len(found) <= 1
    return found[0] if found else None


async def points(session: AsyncSession, deed_id: uuid.UUID, role: LedgerRole) -> int:
    """Net points of a deed and role (the ledger sum)."""
    total = await session.scalar(
        select(func.coalesce(func.sum(PointsLedger.points), 0)).where(
            PointsLedger.deed_id == deed_id, PointsLedger.role == role
        )
    )
    return int(total or 0)


async def user_points(session: AsyncSession, user_id: uuid.UUID, role: LedgerRole) -> int:
    total = await session.scalar(
        select(func.coalesce(func.sum(PointsLedger.points), 0)).where(
            PointsLedger.user_id == user_id, PointsLedger.role == role
        )
    )
    return int(total or 0)


async def evaluations(session: AsyncSession, deed_id: uuid.UUID) -> list[ImpactEvaluation]:
    rows = await session.scalars(
        select(ImpactEvaluation).where(ImpactEvaluation.deed_id == deed_id)
    )
    return list(rows)


def shape(rows: Iterable[PointsLedger]) -> list[tuple[str, str, int, int, bool]]:
    """Order-free summary of ledger rows, for comparing two ledgers."""
    return sorted(
        (r.role.value, r.kind.value, r.points, r.deed_units, r.counts_for_prize_rank) for r in rows
    )
