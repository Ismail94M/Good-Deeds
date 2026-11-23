"""Scoring API (docs/M4-DESIGN.md §3.3 #1 to #10, §3.4), mounted under /api.

Reads only; there are no member writes. #4 is public (§3.4 #4) with a per-IP limit, plus a
per-account limit when signed in; the other member reads need ``CurrentUser`` and are limited
per account and per IP (#2 to #8, ``scoring.rate_me_user`` / ``scoring.rate_public_ip``).
#9 and #10 are admin only (moderators get 403). Routers stay thin: limits first, then the
read in ``_reads``.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.ratelimit import Limiter, client_ip
from app.modules.deeds import service as deeds
from app.modules.identity import service as identity
from app.modules.identity.service import AdminUser, CurrentUser
from app.modules.scoring import _reads
from app.modules.scoring._limits import load_scoring
from app.modules.scoring.models import LedgerRole
from app.modules.scoring.schemas import (
    AdminDeedScoring,
    AdminMonth,
    DeedPoints,
    FeaturedCityBoard,
    Heroes,
    Leaderboard,
    LedgerPage,
    MyScoring,
    PublicScoring,
    RegionOption,
    ScoringOptions,
)

router = APIRouter(tags=["scoring"])
Session = Annotated[AsyncSession, Depends(get_session)]

_bearer = HTTPBearer(auto_error=False)


async def _limit(
    session: AsyncSession, limiter: Limiter, request: Request, user_id: uuid.UUID | None
) -> None:
    _, config = await load_scoring(session)
    if user_id is not None:
        await limiter.hit(f"scoring:u:{user_id}", config.rate_me_user)
    await limiter.hit(f"scoring:ip:{client_ip(request)}", config.rate_public_ip)


async def _optional_user_id(
    session: Session,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> uuid.UUID | None:
    """The signed-in user, or None for a visitor. A bad token is still 401 (as everywhere)."""
    if credentials is None:
        return None
    return (await identity.current_user(session, credentials)).id


# --------------------------------------------------- #1 to #5


@router.get("/scoring/options", response_model=ScoringOptions)
async def scoring_options(user: CurrentUser, session: Session) -> ScoringOptions:
    return await _reads.options(session)


@router.get("/scoring/me", response_model=MyScoring)
async def my_scoring(
    request: Request, user: CurrentUser, session: Session, limiter: Limiter
) -> MyScoring:
    await _limit(session, limiter, request, user.id)
    return await _reads.my_scoring(session, user.id)


@router.get("/scoring/me/ledger", response_model=LedgerPage)
async def my_ledger(
    request: Request,
    user: CurrentUser,
    session: Session,
    limiter: Limiter,
    role: LedgerRole | None = None,
    cursor: str | None = None,
) -> LedgerPage:
    await _limit(session, limiter, request, user.id)
    return await _reads.ledger_page(session, user.id, role=role, cursor=cursor)


@router.get("/scoring/users/{handle}", response_model=PublicScoring)
async def public_scoring(
    handle: str,
    request: Request,
    session: Session,
    limiter: Limiter,
    viewer_id: Annotated[uuid.UUID | None, Depends(_optional_user_id)],
) -> PublicScoring:
    """Public (§3.4 #4): tier, badges and titles; never points or ranks."""
    await _limit(session, limiter, request, viewer_id)
    return await _reads.public_scoring(session, handle)


@router.get("/scoring/deeds/{deed_id}", response_model=DeedPoints)
async def deed_points(
    deed_id: uuid.UUID, request: Request, user: CurrentUser, session: Session, limiter: Limiter
) -> DeedPoints:
    """The caller's own row only; 404 for anyone but the doer and reporter, and for pending
    deeds."""
    await _limit(session, limiter, request, user.id)
    return await _reads.deed_points(session, user.id, deed_id)


# --------------------------------------------------- #6 to #8 leaderboards


@router.get("/leaderboards", response_model=Leaderboard, tags=["leaderboards"])
async def leaderboard(
    request: Request,
    user: CurrentUser,
    session: Session,
    limiter: Limiter,
    region: str | None = None,
    month: str | None = None,
    cursor: str | None = None,
) -> Leaderboard:
    await _limit(session, limiter, request, user.id)
    return await _reads.leaderboard(
        session, user.id, region_code=region, month=month, cursor=cursor
    )


@router.get("/leaderboards/regions", response_model=list[RegionOption], tags=["leaderboards"])
async def leaderboard_regions(
    request: Request,
    user: CurrentUser,
    session: Session,
    limiter: Limiter,
    kind: deeds.RegionKind = deeds.RegionKind.WARD,
    q: Annotated[str | None, Query(max_length=80)] = None,
) -> list[RegionOption]:
    await _limit(session, limiter, request, user.id)
    return await _reads.region_options(session, kind, q)


@router.get("/leaderboards/featured", response_model=FeaturedCityBoard, tags=["leaderboards"])
async def leaderboard_featured(
    request: Request,
    user: CurrentUser,
    session: Session,
    limiter: Limiter,
    city: Annotated[str | None, Query(max_length=40)] = None,
    month: str | None = None,
) -> FeaturedCityBoard:
    await _limit(session, limiter, request, user.id)
    return await _reads.featured_city(session, city_key=city, month=month)


@router.get("/leaderboards/heroes", response_model=Heroes, tags=["leaderboards"])
async def leaderboard_heroes(
    request: Request,
    user: CurrentUser,
    session: Session,
    limiter: Limiter,
    region: str | None = None,
    year: Annotated[int | None, Query(ge=_reads.MIN_YEAR, le=_reads.MAX_YEAR)] = None,
) -> Heroes:
    await _limit(session, limiter, request, user.id)
    return await _reads.heroes(session, region_code=region, year=year)


# --------------------------------------------------- #9, #10 admin


@router.get("/admin/scoring/deeds/{deed_id}", response_model=AdminDeedScoring, tags=["admin"])
async def admin_deed(deed_id: uuid.UUID, admin: AdminUser, session: Session) -> AdminDeedScoring:
    """404 for an unknown deed and for an admin who did or reported it."""
    return await _reads.admin_deed(session, admin.id, deed_id)


@router.get("/admin/scoring/months/{month}", response_model=AdminMonth, tags=["admin"])
async def admin_month(month: str, admin: AdminUser, session: Session) -> AdminMonth:
    return await _reads.admin_month(session, month)
