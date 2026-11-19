"""QR / ID + code scans: locking in verification proof while the code is fresh (§3.2 #1, #2).

A scan row is only written once every check has passed, so ``qr_scans`` holds accepted scans
only. Each scan can then be consumed once by ``POST /api/deeds`` within ``scan_submit_minutes``.
"""

import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import exists, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, Gone, NotFound
from app.core.ratelimit import RateLimiter
from app.core.security import keyed_hash
from app.modules.deeds import _plausibility as plaus
from app.modules.deeds._areas import area_names, display_name, resolve_area_id
from app.modules.deeds._common import (
    constraint_name,
    ist_day,
    ist_month,
    lat_lng,
    point_ewkt,
    to_point,
    utcnow,
)
from app.modules.deeds._limits import DeedLimits
from app.modules.deeds._reporter_limit import (
    pair_limit_reached,
    pair_reports_remaining,
    precheck_monthly_limit,
)
from app.modules.deeds.models import Deed, QrScan, ScanMethod
from app.modules.deeds.schemas import ScanCreate, ScanDetail, ScanOut, UserRef
from app.modules.identity import service as identity

_ALREADY_TODAY = "You've already recognised this person today. Try again tomorrow."


def _already_today(doer: UserRef) -> Conflict:
    """Scan-time only: the body names the doer (D27). Raised only after the code resolved and
    the self and phone checks passed, and the witness has already recorded this person today."""
    return Conflict(
        _ALREADY_TODAY, code="already_recognized_today", extra={"doer": doer.model_dump()}
    )


def _already_scanned() -> Conflict:
    return Conflict(
        "You've already scanned this code. Ask them to show a fresh one.", code="already_scanned"
    )


def _cannot_verify_self() -> Conflict:
    return Conflict("You can't verify your own deed.", code="cannot_verify_self")


# --------------------------------------------------- rate limits


async def enforce_rate_limits(
    limiter: RateLimiter, limits: DeedLimits, data: ScanCreate, user_id: uuid.UUID, ip: str
) -> None:
    """Account and IP limits (D1); typed codes also get a per-target limit that holds across
    witnesses and IPs (D11). Runs before any lookup."""
    if not data.is_manual:
        await limiter.hit(f"scan:u:{user_id}", limits.rate_scan_witness)
        await limiter.hit(f"scan:ip:{ip}", limits.rate_scan_ip)
        return
    await limiter.hit(f"manual:u:{user_id}", limits.rate_manual_witness)
    await limiter.hit(f"manual:ip:{ip}", limits.rate_manual_ip)
    raw = data.short_id or ""
    target = identity.normalize_short_id(raw) or raw.strip().upper()
    await limiter.hit(f"manual:t:{target}", limits.rate_manual_target)
    # A slower daily cap per target (security L4): the 15-minute limit alone still allows
    # 1920 guesses a day at one ID across many witnesses and IPs.
    await limiter.hit(f"manual:td:{target}", limits.rate_manual_target_daily)


# --------------------------------------------------- plausibility inputs


async def _last_fix(
    session: AsyncSession,
    *,
    witness_id: uuid.UUID | None = None,
    doer_id: uuid.UUID | None = None,
    before: datetime | None = None,
    exclude: uuid.UUID | None = None,
) -> plaus.Fix | None:
    """The latest accepted scan by a witness, or of a doer by anyone."""
    lat, lng = lat_lng(QrScan.location)
    stmt = select(lat, lng, QrScan.scanned_at).order_by(QrScan.scanned_at.desc()).limit(1)
    if witness_id is not None:
        stmt = stmt.where(QrScan.witness_user_id == witness_id)
    if doer_id is not None:
        stmt = stmt.where(QrScan.doer_user_id == doer_id)
    if before is not None:
        stmt = stmt.where(QrScan.scanned_at <= before)
    if exclude is not None:
        stmt = stmt.where(QrScan.id != exclude)
    row = (await session.execute(stmt)).first()
    if row is None:
        return None
    point = to_point(row[0], row[1])
    return None if point is None else plaus.Fix(point=point, at=row[2])


def _centre(limits: DeedLimits) -> plaus.Point:
    return plaus.Point(lat=limits.service_area.lat, lng=limits.service_area.lng)


def _evidence(
    accuracy_m: int,
    here: plaus.Fix,
    witness_prev: plaus.Fix | None,
    doer_prev: plaus.Fix | None,
    limits: DeedLimits,
) -> dict[str, Any]:
    """Plausibility figures for ``verification_events.evidence``. Never raw coordinates."""

    def speed(prev: plaus.Fix | None) -> float | None:
        return None if prev is None else round(plaus.speed_kmh(prev, here), 1)

    return {
        "accuracy_m": accuracy_m,
        "service_area_km": round(plaus.service_area_km(here.point, _centre(limits)), 2),
        "witness_speed_kmh": speed(witness_prev),
        "doer_speed_kmh": speed(doer_prev),
    }


async def scan_evidence(session: AsyncSession, scan: QrScan, limits: DeedLimits) -> dict[str, Any]:
    """Recompute the scan's plausibility figures against the scans that preceded it."""
    lat, lng = lat_lng(QrScan.location)
    row = (await session.execute(select(lat, lng).where(QrScan.id == scan.id))).one()
    point = to_point(row[0], row[1])
    assert point is not None
    here = plaus.Fix(point=point, at=scan.scanned_at)
    witness_prev = await _last_fix(
        session, witness_id=scan.witness_user_id, before=scan.scanned_at, exclude=scan.id
    )
    doer_prev = await _last_fix(
        session, doer_id=scan.doer_user_id, before=scan.scanned_at, exclude=scan.id
    )
    return _evidence(scan.accuracy_m, here, witness_prev, doer_prev, limits)


# --------------------------------------------------- create


async def _resolve(session: AsyncSession, data: ScanCreate, now: datetime) -> identity.ResolvedDoer:
    if data.token is not None:
        return await identity.resolve_qr_token(session, data.token, now=now.timestamp())
    assert data.short_id is not None and data.code is not None
    return await identity.resolve_manual_code(
        session, data.short_id, data.code, now=now.timestamp()
    )


async def _window_used(
    session: AsyncSession, witness_id: uuid.UUID, doer_id: uuid.UUID, window: int
) -> bool:
    used = exists().where(
        QrScan.doer_user_id == doer_id,
        QrScan.witness_user_id == witness_id,
        QrScan.token_window == window,
    )
    return bool(await session.scalar(select(used)))


async def _pair_used_today(
    session: AsyncSession, witness_id: uuid.UUID, doer_id: uuid.UUID, day: Any
) -> bool:
    scanned = exists().where(
        QrScan.doer_user_id == doer_id,
        QrScan.witness_user_id == witness_id,
        QrScan.scan_day == day,
    )
    reported = exists().where(
        Deed.doer_user_id == doer_id,
        Deed.reporter_user_id == witness_id,
        Deed.report_day == day,
    )
    return bool(await session.scalar(select(scanned | reported)))


async def create_scan(
    session: AsyncSession,
    witness_id: uuid.UUID,
    data: ScanCreate,
    *,
    limits: DeedLimits,
    limits_version: int,
    device_id: str | None,
    ip: str,
    now: datetime | None = None,
) -> ScanOut:
    """Check and record a scan. Rate limits are the caller's job (they come first)."""
    now = now or utcnow()
    today = ist_day(now)
    # D16 early warning: a scan at the monthly limit could never become a deed, and it would
    # still use up the pair's IST day. The locked check runs again on deed create.
    await precheck_monthly_limit(session, witness_id, limits, now)
    doer = await _resolve(session, data, now)

    # Independence: never the same account, and never two accounts that ever shared a number.
    if doer.user_id == witness_id:
        raise _cannot_verify_self()
    if await identity.phone_hashes(session, witness_id) & await identity.phone_hashes(
        session, doer.user_id
    ):
        raise _cannot_verify_self()
    # Pre-checks for friendly errors; the unique constraints are the real guard.
    if await _window_used(session, witness_id, doer.user_id, doer.window):
        raise _already_scanned()
    doer_ref = UserRef(handle=doer.handle, display_name=doer.display_name)
    if await _pair_used_today(session, witness_id, doer.user_id, today):
        raise _already_today(doer_ref)
    # D19 pair pre-check (unlocked): refuse before a scan is recorded, so the pair's IST day
    # isn't used up by a scan that could never become a deed. Only here, after the code resolved
    # and the self and phone checks passed, so it reveals nothing about a code the witness
    # couldn't already use. The locked check on deed create is the authoritative one.
    pair_remaining = await pair_reports_remaining(session, witness_id, doer.user_id, limits, now)
    if not pair_remaining:
        raise pair_limit_reached(
            limits.reporter_doer_monthly_limit, extra={"doer": doer_ref.model_dump()}
        )

    loc = data.location
    here = plaus.Fix(point=plaus.Point(lat=loc.lat, lng=loc.lng), at=now)
    plaus.ensure_accuracy(loc.accuracy_m, limits.max_location_accuracy_m)
    plaus.ensure_in_service_area(here.point, _centre(limits), limits.service_area.radius_km)
    speed, slack = limits.max_travel_speed_kmh, limits.travel_slack_m
    plaus.ensure_travel(await _last_fix(session, witness_id=witness_id), here, speed, slack)
    # Catches a screenshot forwarded to a remote witness within the grace window.
    plaus.ensure_travel(await _last_fix(session, doer_id=doer.user_id), here, speed, slack)

    scan = QrScan(
        witness_user_id=witness_id,
        doer_user_id=doer.user_id,
        scan_method=ScanMethod.MANUAL_CODE if data.is_manual else ScanMethod.QR,
        token_window=doer.window,
        scanned_at=now,
        scan_day=today,
        location=point_ewkt(loc.lat, loc.lng),
        accuracy_m=loc.accuracy_m,
        device_hash=identity.device_hash(device_id) if device_id else None,
        ip_hash=keyed_hash("ip", ip),
        expires_at=now + timedelta(minutes=limits.scan_submit_minutes),
        limits_version=limits_version,
    )
    session.add(scan)
    try:
        async with session.begin_nested():
            await session.flush()
    except IntegrityError as exc:
        name = constraint_name(exc)
        if name == "uq_qr_scans_doer_window_witness":
            raise _already_scanned() from exc
        if name == "uq_qr_scans_doer_witness_day":
            raise _already_today(doer_ref) from exc
        raise
    return ScanOut(
        scan_id=scan.id,
        doer=doer_ref,
        expires_at=scan.expires_at,
        scanned_at=scan.scanned_at,
        pair_reports_remaining=pair_remaining,
        month_resets_at=ist_month(now)[1],
        area_name=await _area_name(session, point_ewkt(loc.lat, loc.lng), limits),
    )


async def _area_name(session: AsyncSession, location: str | None, limits: DeedLimits) -> str:
    """The area of a scan point (the deed will get the same one). Never coordinates."""
    area_id = await resolve_area_id(session, location, limits.area_fallback_m)
    return display_name(area_id, await area_names(session, [area_id]))


# --------------------------------------------------- read


async def _scan_point(session: AsyncSession, scan_id: uuid.UUID) -> str:
    lat, lng = lat_lng(QrScan.location)
    row = (await session.execute(select(lat, lng).where(QrScan.id == scan_id))).one()
    return point_ewkt(row[0], row[1])


def scan_not_found() -> NotFound:
    return NotFound("We couldn't find that scan. Please scan again.", code="scan_not_found")


def scan_expired() -> Gone:
    return Gone("This scan has expired. Please scan their code again.", code="scan_expired")


async def get_scan(
    session: AsyncSession,
    witness_id: uuid.UUID,
    scan_id: uuid.UUID,
    *,
    limits: DeedLimits,
    now: datetime | None = None,
) -> ScanDetail:
    """The caller's own scan. Another witness's scan is indistinguishable from a missing one.
    A consumed scan is still returned (``consumed=true``) so the form can show it was used.
    ``pair_reports_remaining`` and ``month_resets_at`` are recomputed at read time (D20)."""
    now = now or utcnow()
    scan = await session.get(QrScan, scan_id)
    if scan is None or scan.witness_user_id != witness_id:
        raise scan_not_found()
    if scan.consumed_at is None and scan.expires_at <= now:
        raise scan_expired()
    summaries = await identity.public_summaries(session, [scan.doer_user_id])
    doer = summaries.get(scan.doer_user_id)
    if doer is None:
        raise scan_not_found()
    return ScanDetail(
        scan_id=scan.id,
        doer=UserRef(handle=doer.handle, display_name=doer.display_name),
        expires_at=scan.expires_at,
        scanned_at=scan.scanned_at,
        pair_reports_remaining=await pair_reports_remaining(
            session, witness_id, scan.doer_user_id, limits, now
        ),
        month_resets_at=ist_month(now)[1],
        area_name=await _area_name(session, await _scan_point(session, scan.id), limits),
        consumed=scan.consumed_at is not None,
    )
