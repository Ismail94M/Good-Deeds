"""The QR path of ``POST /api/deeds`` (§3.2 #3): turn a fresh scan into a verified deed.

In one transaction: check the monthly reporting limits (total and pair, D19), consume the scan
(conditional UPDATE, so two submits of one scan create one deed), check the time, group the deed
(§3.4), insert it ``verified``, write the ``verification_events`` row, emit ``DeedSubmitted`` +
``DeedVerified``, and run the prize-rank upgrade for invite deeds in the group.
"""

import uuid
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, Conflict
from app.core.events import emit
from app.modules.deeds import _plausibility as plaus
from app.modules.deeds._common import constraint_name, ist_day, lat_lng, point_ewkt, utcnow
from app.modules.deeds._grouping import assign_group, upgrade_prize_rank
from app.modules.deeds._limits import DeedLimits
from app.modules.deeds._reporter_limit import ensure_under_monthly_limits
from app.modules.deeds._scans import scan_evidence, scan_expired, scan_not_found
from app.modules.deeds._views import deed_out
from app.modules.deeds.events import DeedSubmitted, DeedVerified
from app.modules.deeds.models import (
    Deed,
    DeedStatus,
    QrScan,
    VerificationEvent,
    VerificationEventKind,
    VerificationMethod,
)
from app.modules.deeds.schemas import DeedOut, QrDeedCreate


def validate_content(
    limits: DeedLimits, category: str, description: str, beneficiaries_count: int | None
) -> str:
    """Check deed content against the config. Returns the trimmed description."""
    if not limits.has_category(category):
        raise AppError("Please choose a category from the list.", code="invalid_category")
    text = description.strip()
    if not limits.description_min <= len(text) <= limits.description_max:
        raise AppError(
            f"Please describe the deed in {limits.description_min} to "
            f"{limits.description_max} characters.",
            code="invalid_description",
        )
    if beneficiaries_count is not None and beneficiaries_count > limits.beneficiaries_max:
        raise AppError(
            f"People helped can be at most {limits.beneficiaries_max}.",
            code="invalid_beneficiaries",
        )
    return text


async def _scan_doer(session: AsyncSession, witness_id: uuid.UUID, scan_id: uuid.UUID) -> uuid.UUID:
    """The scan's doer, read without a lock (a scan's doer never changes) so the pair limit can
    be checked under ``reporter_month`` before the scan row is locked. A missing or another
    witness's scan is ``scan_not_found``, as on consume."""
    doer_id = await session.scalar(
        select(QrScan.doer_user_id).where(
            QrScan.id == scan_id, QrScan.witness_user_id == witness_id
        )
    )
    if doer_id is None:
        raise scan_not_found()
    return doer_id


async def _consume_scan(
    session: AsyncSession, witness_id: uuid.UUID, scan_id: uuid.UUID, now: datetime
) -> QrScan:
    """Atomically mark the scan used. A concurrent submit blocks on the row lock and then finds
    ``consumed_at`` set, so exactly one deed is created per scan."""
    stmt = (
        update(QrScan)
        .where(
            QrScan.id == scan_id,
            QrScan.witness_user_id == witness_id,
            QrScan.consumed_at.is_(None),
            QrScan.expires_at > now,
        )
        .values(consumed_at=now)
        .returning(QrScan)
        .execution_options(populate_existing=True, synchronize_session=False)
    )
    scan = (await session.scalars(stmt)).first()
    if scan is not None:
        return scan
    existing = (
        await session.scalars(
            select(QrScan).where(QrScan.id == scan_id).execution_options(populate_existing=True)
        )
    ).first()
    if existing is None or existing.witness_user_id != witness_id:
        raise scan_not_found()
    if existing.consumed_at is not None:
        raise Conflict("A deed has already been reported for this scan.", code="scan_used")
    raise scan_expired()


async def create_qr_deed(
    session: AsyncSession,
    reporter_id: uuid.UUID,
    data: QrDeedCreate,
    *,
    limits: DeedLimits,
    limits_version: int,
    now: datetime | None = None,
) -> DeedOut:
    now = now or utcnow()
    description = validate_content(
        limits, data.category, data.description, data.beneficiaries_count
    )
    # D19: both monthly limits before the scan is consumed, so the scan row lock is always taken
    # after the ``reporter_month`` lock and a refused report leaves the scan unused.
    doer_id = await _scan_doer(session, reporter_id, data.scan_id)
    await ensure_under_monthly_limits(session, reporter_id, doer_id, limits, now)
    scan = await _consume_scan(session, reporter_id, data.scan_id, now)

    occurred_at = data.occurred_at or scan.scanned_at
    plaus.ensure_occurred_at(
        occurred_at, *plaus.qr_occurred_range(scan.scanned_at, limits.qr_occurred_max_age_minutes)
    )

    # The deed happened where the witness scanned.
    lat, lng = lat_lng(QrScan.location)
    row = (await session.execute(select(lat, lng).where(QrScan.id == scan.id))).one()
    location = point_ewkt(row[0], row[1])

    assignment = await assign_group(
        session,
        doer_id=scan.doer_user_id,
        reporter_id=reporter_id,
        location=location,
        # L7-A: group by the server's scan time, not the reporter's (backdatable) "When?".
        grouped_at=scan.scanned_at,
        limits=limits,
        limits_version=limits_version,
    )
    deed = Deed(
        reporter_user_id=reporter_id,
        doer_user_id=scan.doer_user_id,
        category=data.category,
        description=description,
        beneficiaries_count=data.beneficiaries_count,
        location=location,
        location_accuracy_m=scan.accuracy_m,
        occurred_at=occurred_at,
        reported_at=now,
        report_day=ist_day(now),
        status=DeedStatus.VERIFIED,
        verification_method=VerificationMethod.QR_SCAN,
        verified_at=now,
        scan_id=scan.id,
        same_act_group_id=assignment.group.id,
        counts_for_points=assignment.counts_for_points,
        # QR-verified deeds count for prizes immediately (D9 / Q13).
        prize_rank_eligible=True,
        limits_version=limits_version,
    )
    session.add(deed)
    try:
        async with session.begin_nested():
            await session.flush()
    except IntegrityError as exc:
        # A scan just before IST midnight, submitted just after, can meet a deed the same
        # pair already has on the new day.
        if constraint_name(exc) == "uq_deeds_pair_day":
            raise Conflict(
                "You've already recognised this person today. Try again tomorrow.",
                code="already_recognized_today",
            ) from exc
        raise

    session.add(
        VerificationEvent(
            deed_id=deed.id,
            kind=VerificationEventKind.QR_VERIFIED,
            actor_user_id=reporter_id,
            scan_id=scan.id,
            evidence=await scan_evidence(session, scan, limits),
        )
    )
    emit(
        session,
        DeedSubmitted(
            deed_id=deed.id,
            reporter_id=reporter_id,
            doer_id=scan.doer_user_id,
            method="qr_scan",
            status="verified",
            category=deed.category,
            occurred_at=occurred_at,
        ),
    )
    emit(
        session,
        DeedVerified(
            deed_id=deed.id,
            reporter_id=reporter_id,
            doer_id=scan.doer_user_id,
            method="qr_scan",
            verified_at=now,
            same_act_group_id=assignment.group.id,
            counts_for_points=assignment.counts_for_points,
            prize_rank_eligible=True,
            limits_version=limits_version,
        ),
    )
    await session.flush()
    # A second independent report makes the group's invite deeds prize-rank eligible (§3.4).
    await upgrade_prize_rank(session, assignment.group)
    return await deed_out(session, deed, reporter_id, group=assignment.group)
