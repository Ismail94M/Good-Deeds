"""Reporter concentration (docs/M5-DESIGN.md D85, §4.4; PRD §5, §12).

``check_concentration`` (job ``scoring.check_concentration``, every 15 min) looks at every open
(not finalized) month. Per doer and month, over the doer's ledger rows that count for prize
rank, it sums the points from each reporter (``counterpart_user_id``). When the doer has at
least ``min_doer_points`` prize-rank points that month and one reporter's share is over
``max_reporter_share_pct``, it records the (doer, reporter, month) once in
``concentration_signals`` and emits ``ReporterConcentrationExceeded`` with that pair's
prize-rank deeds. ``fraud`` opens a ``concentration`` case. Nothing else happens: no cap, no
hold, no rank change (PO principle 1, D98; the 30 % cap is applied in M8's prize calculation,
Q61).

The thresholds live in the ``fraud`` config (D97) and are read through
``fraud.service.concentration_rule``; the check is skipped while
``detectors_enabled.concentration`` is false. Held deeds count for prize rank no more, so they
drop out of the measure. Insert-once: re-running finds the signal already there and emits
nothing.

Never imports or reads AI evaluations (D62).
"""

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import emit
from app.modules.fraud.service import concentration_rule
from app.modules.scoring.events import ReporterConcentrationExceeded
from app.modules.scoring.models import (
    ConcentrationSignal,
    LedgerRole,
    MonthFinalization,
    PointsLedger,
)


@dataclass(frozen=True)
class Signal:
    doer_id: uuid.UUID
    reporter_id: uuid.UUID
    period_month: date
    share_bp: int
    doer_points: int
    deed_ids: tuple[uuid.UUID, ...]


async def _pair_points(session: AsyncSession) -> list[tuple[uuid.UUID, uuid.UUID, date, int]]:
    """Net prize-rank doer points per (doer, reporter, open month)."""
    finalized = (
        select(MonthFinalization.period_month)
        .where(MonthFinalization.period_month == PointsLedger.period_month)
        .exists()
    )
    rows = await session.execute(
        select(
            PointsLedger.user_id,
            PointsLedger.counterpart_user_id,
            PointsLedger.period_month,
            func.sum(PointsLedger.points),
        )
        .where(
            PointsLedger.role == LedgerRole.DOER,
            PointsLedger.counts_for_prize_rank.is_(True),
            PointsLedger.counterpart_user_id.is_not(None),
            ~finalized,
        )
        .group_by(PointsLedger.user_id, PointsLedger.counterpart_user_id, PointsLedger.period_month)
    )
    return [
        (doer, reporter, month, int(points))
        for doer, reporter, month, points in rows
        if reporter is not None
    ]


async def _pair_deeds(
    session: AsyncSession, doer_id: uuid.UUID, reporter_id: uuid.UUID, month: date
) -> list[uuid.UUID]:
    """The pair's deeds counted for prize rank in ``month`` (net ``deed_units`` positive)."""
    rows = await session.scalars(
        select(PointsLedger.deed_id)
        .where(
            PointsLedger.role == LedgerRole.DOER,
            PointsLedger.counts_for_prize_rank.is_(True),
            PointsLedger.user_id == doer_id,
            PointsLedger.counterpart_user_id == reporter_id,
            PointsLedger.period_month == month,
        )
        .group_by(PointsLedger.deed_id)
        .having(func.sum(PointsLedger.deed_units) > 0)
        .order_by(PointsLedger.deed_id)
    )
    return list(rows)


async def check_concentration(session: AsyncSession) -> list[Signal]:
    """Record and emit every new over-the-limit (doer, reporter, open month). Returns the new
    signals (none while the check is switched off). The caller commits."""
    rule = await concentration_rule(session)
    if not rule.enabled:
        return []
    per_doer: dict[tuple[uuid.UUID, date], list[tuple[uuid.UUID, int]]] = defaultdict(list)
    for doer, reporter, month, points in await _pair_points(session):
        per_doer[(doer, month)].append((reporter, points))

    signals: list[Signal] = []
    for (doer, month), reporters in sorted(per_doer.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        total = sum(points for _, points in reporters)
        if total <= 0 or total < rule.min_doer_points:
            continue
        for reporter, points in sorted(reporters, key=lambda r: str(r[0])):
            if points * 100 <= rule.max_reporter_share_pct * total:
                continue
            share_bp = min(10_000, points * 10_000 // total)
            inserted = await session.scalar(
                insert(ConcentrationSignal)
                .values(
                    doer_id=doer,
                    reporter_id=reporter,
                    period_month=month,
                    share_bp=share_bp,
                    doer_points=total,
                )
                .on_conflict_do_nothing()
                .returning(ConcentrationSignal.doer_id)
            )
            if inserted is None:
                continue  # already signalled for this month
            deed_ids = await _pair_deeds(session, doer, reporter, month)
            if not deed_ids:
                continue  # can't happen with positive points; nothing to flag
            emit(
                session,
                ReporterConcentrationExceeded(
                    doer_id=doer,
                    reporter_id=reporter,
                    period_month=month,
                    share_bp=share_bp,
                    doer_points=total,
                    deed_ids=deed_ids,
                ),
            )
            signals.append(Signal(doer, reporter, month, share_bp, total, tuple(deed_ids)))
    await session.flush()
    return signals
