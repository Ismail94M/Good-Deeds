"""Deeds API (docs/M2-DESIGN.md §3.2), mounted under /api. Organised by section.

Routers stay thin: load the ``deeds`` config, apply rate limits (before any lookup), call the
service, commit.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.errors import AppError
from app.core.ratelimit import Limiter, client_ip
from app.modules.deeds import (
    _cursor,
    _edits,
    _invites,
    _qr_deeds,
    _reads,
    _scans,
    _self_logs,
    schemas,
)
from app.modules.deeds._limits import load_limits
from app.modules.deeds.schemas import (
    DeedCreate,
    DeedEdit,
    DeedOut,
    InviteDeedCreate,
    InviteOut,
    InvitePreview,
    ScanCreate,
    ScanDetail,
    ScanOut,
)
from app.modules.identity.service import CurrentUser, DeviceId, OnboardedUser

router = APIRouter(tags=["deeds"])
Session = Annotated[AsyncSession, Depends(get_session)]


# --------------------------------------------------- scans (§3.2 #1, #2)


@router.post("/scans", response_model=ScanOut, status_code=status.HTTP_201_CREATED)
async def create_scan(
    data: ScanCreate,
    request: Request,
    user: OnboardedUser,
    session: Session,
    limiter: Limiter,
    device_id: DeviceId,
) -> ScanOut:
    version, limits = await load_limits(session)
    ip = client_ip(request)
    await _scans.enforce_rate_limits(limiter, limits, data, user.id, ip)
    out = await _scans.create_scan(
        session,
        user.id,
        data,
        limits=limits,
        limits_version=version,
        device_id=device_id,
        ip=ip,
    )
    await session.commit()
    return out


@router.get("/scans/{scan_id}", response_model=ScanDetail)
async def get_scan(scan_id: uuid.UUID, user: OnboardedUser, session: Session) -> ScanDetail:
    _, limits = await load_limits(session)
    return await _scans.get_scan(session, user.id, scan_id, limits=limits)


# --------------------------------------------------- deed create (§3.2 #3)


@router.post("/deeds", response_model=DeedOut, status_code=status.HTTP_201_CREATED)
async def create_deed(
    data: DeedCreate, request: Request, user: OnboardedUser, session: Session, limiter: Limiter
) -> DeedOut:
    version, limits = await load_limits(session)
    if isinstance(data, InviteDeedCreate):
        await _invites.limit_create(limiter, limits, client_ip(request))
        _invites.ensure_enabled(limits)
        out = await _invites.create_invite_deed(
            session, user.id, data, limits=limits, limits_version=version
        )
    else:
        out = await _qr_deeds.create_qr_deed(
            session, user.id, data, limits=limits, limits_version=version
        )
    await session.commit()
    return out


# --------------------------------------------------- invites (B6)
# Switched off by config while ``invites_enabled`` is false (D17): create, preview, accept and
# reissue return 403 ``invites_disabled`` after their rate limits. ``/me/invites`` still lists.

# The raw token is in the URL: keep it out of caches and Referer headers.
_TOKEN_HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}


@router.get("/invites/by-token/{token}", response_model=InvitePreview)
async def preview_invite(
    token: str, request: Request, response: Response, session: Session, limiter: Limiter
) -> InvitePreview | JSONResponse:
    try:
        _, limits = await load_limits(session)
        await _invites.limit_preview(limiter, limits, client_ip(request))
        _invites.ensure_enabled(limits)
        out = await _invites.preview(session, token)
    except AppError as exc:
        body = {"detail": exc.detail} | ({"code": exc.code} if exc.code else {})
        return JSONResponse(status_code=exc.status_code, content=body, headers=_TOKEN_HEADERS)
    response.headers.update(_TOKEN_HEADERS)
    return out


@router.post("/invites/by-token/{token}/accept", response_model=DeedOut)
async def accept_invite(
    token: str,
    request: Request,
    response: Response,
    user: OnboardedUser,
    session: Session,
    limiter: Limiter,
    device_id: DeviceId,
) -> DeedOut:
    version, limits = await load_limits(session)
    await _invites.limit_accept(limiter, limits, user.id, client_ip(request))
    _invites.ensure_enabled(limits)
    out = await _invites.accept(
        session,
        token,
        accepter_id=user.id,
        accepter_phone_e164=user.phone_e164,
        device_id=device_id,
        limits=limits,
        limits_version=version,
    )
    await session.commit()
    response.headers.update(_TOKEN_HEADERS)
    return out


@router.post("/invites/{invite_id}/reissue", response_model=InviteOut)
async def reissue_invite(
    invite_id: uuid.UUID,
    request: Request,
    user: OnboardedUser,
    session: Session,
    limiter: Limiter,
) -> InviteOut:
    _, limits = await load_limits(session)
    await _invites.limit_reissue(limiter, limits, user.id, client_ip(request))
    _invites.ensure_enabled(limits)
    out = await _invites.reissue(session, user.id, invite_id)
    await session.commit()
    return out


# --------------------------------------------------- self-logs and profile counts (B7)

PageSize = Annotated[int, Query(ge=1, le=_cursor.MAX_PAGE_SIZE)]


@router.post(
    "/me/self-logs", response_model=schemas.SelfLogOut, status_code=status.HTTP_201_CREATED
)
async def create_self_log(
    data: schemas.SelfLogCreate,
    request: Request,
    user: OnboardedUser,
    session: Session,
    limiter: Limiter,
) -> schemas.SelfLogOut:
    _, limits = await load_limits(session)
    await _self_logs.enforce_write_limits(limiter, limits, user.id, client_ip(request))
    out = await _self_logs.create(session, user.id, data, limits=limits)
    await session.commit()
    return out


@router.get("/me/self-logs", response_model=schemas.SelfLogPage)
async def list_self_logs(
    user: OnboardedUser,
    session: Session,
    cursor: str | None = None,
    limit: PageSize = _cursor.DEFAULT_PAGE_SIZE,
) -> schemas.SelfLogPage:
    return await _self_logs.list_for_owner(session, user.id, cursor=cursor, limit=limit)


@router.patch("/me/self-logs/{log_id}", response_model=schemas.SelfLogOut)
async def update_self_log(
    log_id: uuid.UUID,
    data: schemas.SelfLogUpdate,
    request: Request,
    user: OnboardedUser,
    session: Session,
    limiter: Limiter,
) -> schemas.SelfLogOut:
    _, limits = await load_limits(session)
    await _self_logs.enforce_write_limits(limiter, limits, user.id, client_ip(request))
    out = await _self_logs.update(session, user.id, log_id, data, limits=limits)
    await session.commit()
    return out


@router.delete("/me/self-logs/{log_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_self_log(
    log_id: uuid.UUID,
    request: Request,
    user: OnboardedUser,
    session: Session,
    limiter: Limiter,
) -> None:
    _, limits = await load_limits(session)
    await _self_logs.enforce_write_limits(limiter, limits, user.id, client_ip(request))
    await _self_logs.delete_log(session, user.id, log_id)
    await session.commit()


@router.get("/users/{handle}/deed-counts", response_model=schemas.DeedCountsOut, tags=["users"])
async def deed_counts(
    handle: str, request: Request, session: Session, limiter: Limiter
) -> schemas.DeedCountsOut:
    """Public (no auth): both profile counts, never any self-log content (D4)."""
    _, limits = await load_limits(session)
    await limiter.hit(f"counts:ip:{client_ip(request)}", limits.rate_profile_counts_ip)
    return await _self_logs.deed_counts(session, handle)


# --------------------------------------------------- read endpoints and options (B8)


# Declared before ``/deeds/{deed_id}`` so "options" is not parsed as an ID.
@router.get("/deeds/options", response_model=schemas.DeedOptions)
async def deed_options(user: OnboardedUser, session: Session) -> schemas.DeedOptions:
    _, limits = await load_limits(session)
    return await _reads.options(session, user.id, limits=limits)


@router.get("/deeds/{deed_id}", response_model=DeedOut)
async def get_deed(deed_id: uuid.UUID, user: CurrentUser, session: Session) -> DeedOut:
    """Reporter or doer only; anyone else gets 404 ``deed_not_found``."""
    return await _reads.get_deed(session, user.id, deed_id)


@router.get("/me/deeds", response_model=schemas.DeedPage)
async def my_deeds(
    user: CurrentUser,
    session: Session,
    role: _reads.Role = "reporter",
    cursor: str | None = None,
    limit: PageSize = _cursor.DEFAULT_PAGE_SIZE,
) -> schemas.DeedPage:
    return await _reads.my_deeds(session, user.id, role=role, cursor=cursor, limit=limit)


@router.get("/me/invites", response_model=schemas.InvitePage)
async def my_invites(
    user: CurrentUser,
    session: Session,
    status: _reads.InviteFilter | None = None,
    cursor: str | None = None,
    limit: PageSize = _cursor.DEFAULT_PAGE_SIZE,
) -> schemas.InvitePage:
    return await _reads.my_invites(session, user.id, status=status, cursor=cursor, limit=limit)


# --------------------------------------------------- edit and withdraw (B11)


@router.patch("/deeds/{deed_id}", response_model=DeedOut)
async def edit_deed(
    deed_id: uuid.UUID,
    data: DeedEdit,
    request: Request,
    user: OnboardedUser,
    session: Session,
    limiter: Limiter,
) -> DeedOut:
    _, limits = await load_limits(session)
    await _edits.limit_edit(limiter, limits, user.id, client_ip(request))
    out = await _edits.edit_deed(session, user.id, deed_id, data, limits=limits)
    await session.commit()
    return out


@router.post("/deeds/{deed_id}/withdraw", response_model=DeedOut)
async def withdraw_deed(
    deed_id: uuid.UUID,
    request: Request,
    user: OnboardedUser,
    session: Session,
    limiter: Limiter,
) -> DeedOut:
    _, limits = await load_limits(session)
    await _edits.limit_edit(limiter, limits, user.id, client_ip(request))
    out = await _edits.withdraw_deed(session, user.id, deed_id, limits=limits)
    await session.commit()
    return out
