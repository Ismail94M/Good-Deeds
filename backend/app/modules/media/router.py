"""Media API (docs/M3-DESIGN.md §3.3), mounted under /api: ``/media/...`` and the admin
photo-report routes ``/admin/...``. Organised by section.

This router is the only media file that reads ``feed.service`` (deed visibility for reports and
the admin page, D30).

Routers stay thin: load the ``media`` config, apply rate limits (before any lookup), call the
service, commit. Object deletes run right after the commit, so every URL already issued for a
removed photo stops working at once (§4.7); a failed delete is left for the cleanup jobs.
"""

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.ratelimit import Limiter, client_ip
from app.core.storage import ObjectStorage, get_storage
from app.modules.feed import service as feed
from app.modules.identity import service as identity
from app.modules.identity.service import OnboardedUser, ReviewerUser
from app.modules.media import _admin, _cursor, _moves, _owners, _reports, _uploads
from app.modules.media._common import photo_not_found, utcnow
from app.modules.media._limits import MediaLimits, load_limits
from app.modules.media.models import PhotoReportCase
from app.modules.media.schemas import (
    AttachOut,
    AttachRequest,
    CaseDetail,
    CasePage,
    CompleteOut,
    DecisionCreate,
    DeedPhotosOut,
    OptionsOut,
    ReportCreate,
    ReportReceived,
    RestoreCreate,
    SelfLogPhotosOut,
    UploadCreate,
    UploadOut,
)

router = APIRouter(tags=["media"])
Session = Annotated[AsyncSession, Depends(get_session)]
Storage = Annotated[ObjectStorage, Depends(get_storage)]


# --------------------------------------------------- options (#1)


@router.get("/media/options", response_model=OptionsOut)
async def get_options(user: OnboardedUser, session: Session) -> OptionsOut:
    _, limits = await load_limits(session)
    return _uploads.options(limits)


# --------------------------------------------------- uploads (#2 to #4)


@router.post("/media/uploads", response_model=UploadOut, status_code=status.HTTP_201_CREATED)
async def create_upload(
    data: UploadCreate,
    request: Request,
    user: OnboardedUser,
    session: Session,
    storage: Storage,
    limiter: Limiter,
) -> UploadOut:
    _, limits = await load_limits(session)
    ip = client_ip(request)
    await _uploads.enforce_upload_limits(limiter, limits, user.id, ip)
    out = await _uploads.create_upload(session, storage, user.id, data, limits=limits, ip=ip)
    await session.commit()
    return out


@router.post("/media/uploads/{photo_id}/complete", response_model=CompleteOut)
async def complete_upload(
    photo_id: uuid.UUID, user: OnboardedUser, session: Session, storage: Storage
) -> CompleteOut:
    out = await _uploads.complete_upload(session, storage, user.id, photo_id)
    await session.commit()
    return out


@router.delete("/media/uploads/{photo_id}", status_code=status.HTTP_204_NO_CONTENT)
async def discard_upload(
    photo_id: uuid.UUID, user: OnboardedUser, session: Session, storage: Storage
) -> Response:
    key = await _uploads.discard_upload(session, user.id, photo_id)
    await session.commit()
    if key is not None:
        await _uploads.mark_staging_deleted(session, storage, photo_id, key, utcnow())
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------- attach (#5, #6)


@router.post("/media/deeds/{deed_id}/photos", response_model=AttachOut)
async def attach_to_deed(
    deed_id: uuid.UUID,
    data: AttachRequest,
    user: OnboardedUser,
    session: Session,
    storage: Storage,
    limiter: Limiter,
) -> AttachOut:
    _, limits = await load_limits(session)
    await _uploads.enforce_write_limit(limiter, limits, user.id)
    photos = await _owners.attach_to_deed(
        session, storage, user.id, deed_id, data.photos, limits=limits
    )
    await session.commit()
    return AttachOut(photos=photos)


@router.post("/media/self-logs/{self_log_id}/photos", response_model=AttachOut)
async def attach_to_self_log(
    self_log_id: uuid.UUID,
    data: AttachRequest,
    user: OnboardedUser,
    session: Session,
    storage: Storage,
    limiter: Limiter,
) -> AttachOut:
    _, limits = await load_limits(session)
    await _uploads.enforce_write_limit(limiter, limits, user.id)
    photos = await _owners.attach_to_self_log(
        session, storage, user.id, self_log_id, data.photos, limits=limits
    )
    await session.commit()
    return AttachOut(photos=photos)


# --------------------------------------------------- owner reads (#7, #8)


@router.get("/media/deeds/{deed_id}/photos", response_model=DeedPhotosOut)
async def deed_photos(
    deed_id: uuid.UUID, user: OnboardedUser, session: Session, storage: Storage
) -> DeedPhotosOut:
    _, limits = await load_limits(session)
    return await _owners.deed_photos(session, storage, user.id, deed_id, limits=limits)


@router.get("/media/self-logs/{self_log_id}/photos", response_model=SelfLogPhotosOut)
async def self_log_photos(
    self_log_id: uuid.UUID, user: OnboardedUser, session: Session, storage: Storage
) -> SelfLogPhotosOut:
    _, limits = await load_limits(session)
    return await _owners.self_log_photos(session, storage, user.id, self_log_id, limits=limits)


# --------------------------------------------------- remove (#9)


@router.delete("/media/photos/{photo_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_photo(
    photo_id: uuid.UUID,
    user: OnboardedUser,
    session: Session,
    storage: Storage,
    limiter: Limiter,
) -> Response:
    _, limits = await load_limits(session)
    await _uploads.enforce_write_limit(limiter, limits, user.id)
    keys = await _owners.remove_photo(session, user.id, photo_id)
    await session.commit()
    await _owners.purge_objects(session, storage, photo_id, keys)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------- photo reports (#10)

_bearer = HTTPBearer(auto_error=False)


async def _reporting_member(
    session: Session,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> uuid.UUID | None:
    """The reporting member, or None for a visitor. A signed-in user who hasn't finished
    onboarding reports as a visitor (D44: a safety report never waits for setup). A bad token is
    still 401, as everywhere."""
    if credentials is None:
        return None
    user = await identity.current_user(session, credentials)
    if not (await identity.onboarding_status(session, user)).complete:
        return None
    return user.id


@router.post(
    "/media/photos/{photo_id}/reports",
    response_model=ReportReceived,
    status_code=status.HTTP_202_ACCEPTED,
)
async def report_photo(
    photo_id: uuid.UUID,
    data: ReportCreate,
    request: Request,
    session: Session,
    limiter: Limiter,
    member_id: Annotated[uuid.UUID | None, Depends(_reporting_member)],
) -> ReportReceived:
    _, limits = await load_limits(session)
    ip = client_ip(request)
    if member_id is not None:
        await _reports.enforce_member_limits(limiter, limits, member_id, ip)
    else:
        await _reports.enforce_visitor_limits(limiter, limits, ip)
    deed_id, uploader_id = await _reports.reportable_photo(session, photo_id)
    viewable = (
        await feed.can_view(session, deed_id, member_id)
        if member_id is not None
        else await feed.is_public(session, deed_id)
    )
    if not viewable:
        raise photo_not_found()
    await _reports.file_report(
        session,
        photo_id,
        data,
        reporter_id=member_id,
        uploader_id=uploader_id,
        ip=ip,
        limits=limits,
    )
    await session.commit()
    return ReportReceived()


# --------------------------------------------------- admin (#11 to #14; moderators and admins, Q34)

Reviewer = ReviewerUser
AdminPageSize = Annotated[int, Query(ge=1, le=_cursor.MAX_PAGE_SIZE)]


async def _detail(
    session: AsyncSession, storage: ObjectStorage, case_id: uuid.UUID, limits: MediaLimits
) -> CaseDetail:
    deed_id = await _admin.case_deed_id(session, case_id)
    vis = await feed.visibility(session, deed_id)  # "Shown in feed" (C6)
    return await _admin.case_detail(
        session, storage, case_id, listed=vis.listed if vis else False, limits=limits
    )


@router.get("/admin/photo-reports", response_model=CasePage, tags=["admin"])
async def list_photo_reports(
    user: Reviewer,
    session: Session,
    storage: Storage,
    status: Literal["open", "resolved"] = "open",
    cursor: str | None = None,
    limit: AdminPageSize = _cursor.DEFAULT_PAGE_SIZE,
) -> CasePage:
    _, limits = await load_limits(session)
    return await _admin.list_cases(
        session, storage, queue=status, limits=limits, cursor=cursor, limit=limit
    )


@router.get("/admin/photo-reports/{case_id}", response_model=CaseDetail, tags=["admin"])
async def photo_report_detail(
    case_id: uuid.UUID, user: Reviewer, session: Session, storage: Storage
) -> CaseDetail:
    _, limits = await load_limits(session)
    return await _detail(session, storage, case_id, limits)


@router.post("/admin/photo-reports/{case_id}/decision", response_model=CaseDetail, tags=["admin"])
async def decide_photo_report(
    case_id: uuid.UUID,
    data: DecisionCreate,
    user: Reviewer,
    session: Session,
    storage: Storage,
    limiter: Limiter,
) -> CaseDetail:
    _, limits = await load_limits(session)
    await _admin.enforce_decision_limit(limiter, limits, user.id)
    photo_id = await session.scalar(
        select(PhotoReportCase.photo_id).where(PhotoReportCase.id == case_id)
    )
    move = await _admin.decide(session, user.id, case_id, data, limits=limits)
    await session.commit()
    if move and photo_id is not None:
        # Every URL already issued stops working now; on failure media.move_objects retries.
        await _moves.run_move(session, storage, photo_id)
    return await _detail(session, storage, case_id, limits)


@router.post("/admin/photos/{photo_id}/restore", response_model=CaseDetail, tags=["admin"])
async def restore_photo(
    photo_id: uuid.UUID,
    data: RestoreCreate,
    user: Reviewer,
    session: Session,
    storage: Storage,
    limiter: Limiter,
) -> CaseDetail:
    _, limits = await load_limits(session)
    await _admin.enforce_decision_limit(limiter, limits, user.id)
    case_id = await _admin.restore(session, user.id, photo_id, data, limits=limits)
    await session.commit()
    await _moves.run_move(session, storage, photo_id)
    return await _detail(session, storage, case_id, limits)
