"""Photo reports (§3.3 #10, D42, Q44). A report never changes the photo, the deed, the feed,
counts or anything scored: it only opens or joins the photo's one open case.

Visibility of the deed is checked by the router through ``feed.service`` (this file never
imports ``feed``, D30) and passed in.
"""

import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import advisory_xact_lock
from app.core.errors import AppError
from app.core.ratelimit import RateLimiter
from app.core.security import keyed_hash
from app.modules.media._common import Invalid, photo_not_found, utcnow
from app.modules.media._limits import MediaLimits
from app.modules.media.models import (
    PHOTO_REPORT_PRIORITY,
    Photo,
    PhotoPurpose,
    PhotoReport,
    PhotoReportCase,
    PhotoReportReason,
    PhotoStatus,
    PhotoTakedown,
    ReportCaseStatus,
)
from app.modules.media.schemas import ReportCreate


class OwnPhoto(AppError):
    status_code = 400


async def enforce_member_limits(
    limiter: RateLimiter, limits: MediaLimits, user_id: uuid.UUID, ip: str
) -> None:
    await limiter.hit(f"report:u:{user_id}", limits.rate_report_user)
    await limiter.hit(f"report:ip:{ip}", limits.rate_report_ip)


async def enforce_visitor_limits(limiter: RateLimiter, limits: MediaLimits, ip: str) -> None:
    await limiter.hit(f"report:ip:{ip}", limits.rate_report_ip)
    await limiter.hit(f"report:ipd:{ip}", limits.rate_report_ip_daily)


async def reportable_photo(
    session: AsyncSession, photo_id: uuid.UUID
) -> tuple[uuid.UUID, uuid.UUID]:
    """``(deed_id, uploader_id)`` of a photo that may be reported: ready, not taken down, on a
    deed. Anything else (including every self-log photo) is 404."""
    row = (
        await session.execute(
            select(Photo.deed_id, Photo.uploader_user_id).where(
                Photo.id == photo_id,
                Photo.status == PhotoStatus.READY,
                Photo.takedown == PhotoTakedown.NONE,
                Photo.purpose == PhotoPurpose.DEED,
                Photo.object_move.is_(None),
                Photo.deed_id.is_not(None),
            )
        )
    ).first()
    if row is None or row.deed_id is None:
        raise photo_not_found()
    return row.deed_id, row.uploader_user_id


async def file_report(
    session: AsyncSession,
    photo_id: uuid.UUID,
    data: ReportCreate,
    *,
    reporter_id: uuid.UUID | None,
    uploader_id: uuid.UUID,
    ip: str,
    limits: MediaLimits,
    now: datetime | None = None,
) -> None:
    """Open or join the photo's open case. A duplicate (same member, or same visitor IP in this
    case) changes nothing and gets the same answer."""
    now = now or utcnow()
    if reporter_id is not None and reporter_id == uploader_id:
        raise OwnPhoto("You can't report your own photo.", code="own_photo")
    details = (data.details or "").strip() or None
    if details is not None and len(details) > limits.report_details_max:
        raise Invalid("The details are too long.", code="details_too_long")
    reason = PhotoReportReason(data.reason)
    priority = PHOTO_REPORT_PRIORITY[reason]
    ip_hash = keyed_hash("ip", ip)

    await advisory_xact_lock(session, "photo_case", str(photo_id))
    case = await session.scalar(
        select(PhotoReportCase)
        .where(
            PhotoReportCase.photo_id == photo_id, PhotoReportCase.status == ReportCaseStatus.OPEN
        )
        .with_for_update()
    )
    if case is None:
        case = PhotoReportCase(
            photo_id=photo_id,
            status=ReportCaseStatus.OPEN,
            priority=priority,
            report_count=0,
            opened_at=now,
        )
        session.add(case)
        await session.flush()
    else:
        duplicate = (
            PhotoReport.reporter_user_id == reporter_id
            if reporter_id is not None
            else (
                PhotoReport.reporter_user_id.is_(None) & (PhotoReport.reporter_ip_hash == ip_hash)
            )
        )
        exists = await session.scalar(
            select(func.count())
            .select_from(PhotoReport)
            .where(PhotoReport.case_id == case.id, duplicate)
        )
        if exists:
            return
    session.add(
        PhotoReport(
            case_id=case.id,
            photo_id=photo_id,
            reporter_user_id=reporter_id,
            reporter_ip_hash=ip_hash,
            reason=reason,
            details=details,
            created_at=now,
        )
    )
    case.report_count += 1
    case.priority = min(case.priority, priority)  # never lowered by a less serious report
    await session.flush()
