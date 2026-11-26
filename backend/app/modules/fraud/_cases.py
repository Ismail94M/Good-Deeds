"""Opening or joining a flag case from a detector finding (docs/M5-DESIGN.md §4.2, D81).

Any finding, whichever detector made it (reciprocal, ring, and later concentration or content
from scoring's events), goes through ``raise_flag``. Opening a case changes nothing for anyone
(PO principle 1, D98): no hold (``hold_on_open`` is false for every type), no feed change, no
notice. Every write is insert-or-join with ``ON CONFLICT DO NOTHING``, so raising the same
finding twice changes nothing.

Lock order (§4.1): advisory ``fraud_case:(type, subject_key)`` -> ``flag_cases`` row ->
``prize_holds`` inserts. A case never takes deeds or scoring locks.
"""

import hashlib
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import advisory_xact_lock
from app.core.events import emit
from app.modules.deeds.service import get_deeds
from app.modules.fraud._limits import FraudConfig
from app.modules.fraud.detectors.base import Finding
from app.modules.fraud.events import DeedHoldChanged, FlagRaised
from app.modules.fraud.models import (
    FlagCase,
    FlagCaseDeed,
    FlagCaseStatus,
    FlagCaseUser,
    PrizeHold,
)

LIVE = (FlagCaseStatus.OPEN, FlagCaseStatus.PARKED)
# A month's boards become final about a day after it ends (M4 D64); the queue asks admins to
# decide before that (D93).
REVIEW_MARGIN = timedelta(hours=24)


@dataclass(frozen=True)
class RaiseResult:
    case_id: uuid.UUID
    created: bool
    deeds_added: int


def subject_key(case_type: str, user_ids: Iterable[uuid.UUID]) -> str:
    """SHA-256 hex of the type and the sorted subject ids (D81): the same people give the same
    key whichever deed or detector run found them."""
    raw = case_type + ":" + ",".join(sorted(str(u) for u in set(user_ids)))
    return hashlib.sha256(raw.encode("ascii")).hexdigest()


def month_review_deadline(at: datetime, zone: ZoneInfo) -> datetime:
    """The end of ``at``'s calendar month in ``zone``, plus 24 h."""
    local = at.astimezone(zone)
    start = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    next_month = (start + timedelta(days=32)).replace(day=1)
    return next_month.astimezone(UTC) + REVIEW_MARGIN


async def _review_by(
    session: AsyncSession, deed_ids: Iterable[uuid.UUID], zone: ZoneInfo, now: datetime
) -> datetime | None:
    """The earliest review deadline of the case's deeds' months that is still ahead.

    ``fraud`` can't read scoring's ``month_finalizations`` (D78), so "not yet final" is taken as
    "deadline not passed": a month whose end + 24 h is past counts as final (finalization runs
    shortly after it). Null when every month is past.
    """
    deadlines = {
        month_review_deadline(view.verified_at, zone)
        for view in (await get_deeds(session, deed_ids)).values()
        if view.verified_at is not None
    }
    ahead = [d for d in deadlines if d > now]
    return min(ahead) if ahead else None


async def _live_case(session: AsyncSession, case_type: str, key: str) -> FlagCase | None:
    return await session.scalar(
        select(FlagCase)
        .where(FlagCase.type == case_type, FlagCase.subject_key == key, FlagCase.status.in_(LIVE))
        .with_for_update()
        .execution_options(populate_existing=True)
    )


async def _already_decided(
    session: AsyncSession, case_type: str, key: str, deed_ids: Iterable[uuid.UUID]
) -> set[uuid.UUID]:
    """Deeds already in a resolved case of the same type and subjects: an admin has looked at
    them, so they are never flagged again for these people (D81)."""
    rows = await session.scalars(
        select(FlagCaseDeed.deed_id)
        .join(FlagCase, FlagCase.id == FlagCaseDeed.case_id)
        .where(
            FlagCase.type == case_type,
            FlagCase.subject_key == key,
            FlagCase.status == FlagCaseStatus.RESOLVED,
            FlagCaseDeed.deed_id.in_(set(deed_ids)),
        )
    )
    return set(rows)


async def _held(session: AsyncSession, deed_ids: Iterable[uuid.UUID]) -> set[uuid.UUID]:
    rows = await session.scalars(
        select(PrizeHold.deed_id)
        .where(PrizeHold.deed_id.in_(set(deed_ids)), PrizeHold.released_at.is_(None))
        .distinct()
    )
    return set(rows)


async def _open_case(
    session: AsyncSession, finding: Finding, key: str, config_version: int
) -> uuid.UUID:
    case_id = await session.scalar(
        insert(FlagCase)
        .values(
            id=uuid.uuid4(),
            type=finding.case_type,
            subject_key=key,
            signal=dict(finding.signal),
            detector_config_version=config_version,
        )
        .on_conflict_do_nothing(
            index_elements=[FlagCase.type, FlagCase.subject_key],
            index_where=FlagCase.status.in_(LIVE),
        )
        .returning(FlagCase.id)
    )
    if case_id is None:  # Can't happen under the advisory lock; join the winner if it does.
        existing = await _live_case(session, finding.case_type, key)
        assert existing is not None
        return existing.id
    await session.execute(
        insert(FlagCaseUser)
        .values([{"case_id": case_id, "user_id": u} for u in sorted(finding.subject_ids)])
        .on_conflict_do_nothing()
    )
    assert isinstance(case_id, uuid.UUID)
    return case_id


async def raise_flag(
    session: AsyncSession,
    finding: Finding,
    config: FraudConfig,
    *,
    config_version: int,
    now: datetime | None = None,
) -> RaiseResult | None:
    """Open a case for ``finding``, or add its deeds to the live (open or parked) case for the
    same type and subjects. Returns ``None`` when nothing was raised: the case type is switched
    off, or every deed was already decided in a resolved case for these people. The caller
    commits."""
    case_type = finding.case_type.value
    if not config.enabled(case_type):
        return None
    now = now or datetime.now(UTC)
    key = subject_key(case_type, finding.subject_ids)
    await advisory_xact_lock(session, "fraud_case", case_type, key)

    deed_ids = set(finding.deed_ids) - await _already_decided(
        session, case_type, key, finding.deed_ids
    )
    if not deed_ids:
        return None

    case = await _live_case(session, case_type, key)
    created = case is None
    case_id = await _open_case(session, finding, key, config_version) if case is None else case.id

    added = set(
        await session.scalars(
            insert(FlagCaseDeed)
            .values([{"case_id": case_id, "deed_id": d} for d in sorted(deed_ids)])
            .on_conflict_do_nothing()
            .returning(FlagCaseDeed.deed_id)
        )
    )

    if added and config.holds_on_open(case_type):
        # All false (PO principle 1, D80, Q73); kept so a PO-approved type can hold on open.
        held_before = await _held(session, added)
        newly = set(
            await session.scalars(
                insert(PrizeHold)
                .values([{"deed_id": d, "case_id": case_id} for d in sorted(added)])
                .on_conflict_do_nothing()
                .returning(PrizeHold.deed_id)
            )
        )
        for deed_id in sorted(newly - held_before):
            emit(session, DeedHoldChanged(deed_id=deed_id, held=True, case_id=case_id))

    if created:
        emit(
            session,
            FlagRaised(
                case_id=case_id,
                type=case_type,
                user_ids=sorted(finding.subject_ids),
                deed_ids=sorted(added),
            ),
        )

    if created or added:
        row = await _live_case(session, case_type, key)
        assert row is not None and row.id == case_id
        all_deeds = set(
            await session.scalars(
                select(FlagCaseDeed.deed_id).where(FlagCaseDeed.case_id == case_id)
            )
        )
        row.deed_count = len(all_deeds)
        row.held_count = int(
            await session.scalar(
                select(func.count())
                .select_from(PrizeHold)
                .where(PrizeHold.case_id == case_id, PrizeHold.released_at.is_(None))
            )
            or 0
        )
        row.review_by = await _review_by(session, all_deeds, config.zone, now)
        row.updated_at = now
        if not created:
            row.signal = dict(finding.signal)
        await session.flush()

    return RaiseResult(case_id=case_id, created=created, deeds_added=len(added))
