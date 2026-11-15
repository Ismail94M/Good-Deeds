"""Deeds API (docs/M2-DESIGN.md §3.2), mounted under /api. Organised by section.

Routers stay thin: load the ``deeds`` config, apply rate limits (before any lookup), call the
service, commit.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.ratelimit import Limiter, client_ip
from app.modules.deeds import _qr_deeds, _scans
from app.modules.deeds._limits import load_limits
from app.modules.deeds.schemas import DeedCreate, DeedOut, ScanCreate, ScanDetail, ScanOut
from app.modules.identity.service import DeviceId, OnboardedUser

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
    return await _scans.get_scan(session, user.id, scan_id)


# --------------------------------------------------- deed create (§3.2 #3)


@router.post("/deeds", response_model=DeedOut, status_code=status.HTTP_201_CREATED)
async def create_deed(data: DeedCreate, user: OnboardedUser, session: Session) -> DeedOut:
    version, limits = await load_limits(session)
    # B6 adds the invite branch (``data.method == "invite"``).
    out = await _qr_deeds.create_qr_deed(
        session, user.id, data, limits=limits, limits_version=version
    )
    await session.commit()
    return out


# --------------------------------------------------- invites (B6)

# --------------------------------------------------- self-logs and profile counts (B7)

# --------------------------------------------------- read endpoints and options (B8)

# --------------------------------------------------- edit and withdraw (B11)
