"""Scoring API (docs/M4-DESIGN.md §3.3 #1 to #10, §3.4), mounted under /api.

Reads only; there are no member writes. #4 is public (§3.4 #4) with a per-IP limit, plus a
per-account limit when signed in; the other member reads need ``CurrentUser`` and are limited
per account and per IP (#2 to #8, ``scoring.rate_me_user`` / ``scoring.rate_public_ip``).
#9 and #10 are admin only (moderators get 403). Routers stay thin: limits first, then the
read in ``_reads``.

M5 (docs/M5-DESIGN.md §3.4 #13 to #18): admin overrides, big-cut requests and title/badge
revocation, admin only, limited per account by ``fraud.rate_admin_read`` /
``fraud.rate_admin_write``. Conflicted admins get 404 (D90).
"""

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.ratelimit import Limiter, client_ip
from app.modules.deeds import service as deeds
from app.modules.fraud.service import override_rules
from app.modules.identity import service as identity
from app.modules.identity.service import AdminUser, CurrentUser
from app.modules.scoring import _overrides, _reads, _revoke
from app.modules.scoring._limits import load_scoring
from app.modules.scoring.models import LedgerRole, OverrideRequest
from app.modules.scoring.schemas import (
    AdminDeedScoring,
    AdminMonth,
    AdminUserScoring,
    DeedPoints,
    FeaturedCityBoard,
    Heroes,
    Leaderboard,
    LedgerPage,
    MyScoring,
    OverrideDecisionIn,
    OverrideDecisionOut,
    OverrideIn,
    OverrideRequestOut,
    OverrideRequestPage,
    PublicScoring,
    RegionOption,
    RevokeIn,
    ScoringOptions,
    UserRanks,
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


@router.get("/scoring/users/{handle}/ranks", response_model=UserRanks)
async def user_ranks(
    handle: str, request: Request, user: CurrentUser, session: Session, limiter: Limiter
) -> UserRanks:
    """#12 (D76): a person's rank this month at each level. Signed-in only, like the boards it
    is read from; nobody can hide from the boards, so there is no opt-out."""
    await _limit(session, limiter, request, user.id)
    return await _reads.user_ranks(session, handle)


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
    """404 for an unknown deed and for a conflicted admin (D90)."""
    return await _reads.admin_deed(session, admin.id, deed_id)


@router.get("/admin/scoring/months/{month}", response_model=AdminMonth, tags=["admin"])
async def admin_month(month: str, admin: AdminUser, session: Session) -> AdminMonth:
    return await _reads.admin_month(session, month)


# --------------------------------------------------- M5 #13 to #18 admin


async def _admin_limit(
    session: AsyncSession, limiter: Limiter, admin_id: uuid.UUID, kind: Literal["read", "write"]
) -> None:
    rules = await override_rules(session)
    spec = rules.rate_admin_write if kind == "write" else rules.rate_admin_read
    await limiter.hit(f"scoring:admin:{kind}:u:{admin_id}", spec)


@router.post(
    "/admin/scoring/deeds/{deed_id}/overrides",
    response_model=AdminDeedScoring | OverrideRequestOut,
    tags=["admin"],
)
async def override_points(
    deed_id: uuid.UUID,
    body: OverrideIn,
    response: Response,
    admin: AdminUser,
    session: Session,
    limiter: Limiter,
) -> AdminDeedScoring | OverrideRequestOut:
    """#13: 200 with the #9 body when applied; 202 with the request when the cut needs a second
    admin (Q77)."""
    await _admin_limit(session, limiter, admin.id, "write")
    result = await _overrides.create_override(session, admin.id, deed_id, body)
    await session.commit()
    if isinstance(result, OverrideRequest):
        response.status_code = 202
        (out,) = await _overrides.requests_out(session, [result])
        return out
    return await _reads.admin_deed(session, admin.id, deed_id)


@router.get("/admin/scoring/override-requests", response_model=OverrideRequestPage, tags=["admin"])
async def override_requests(
    admin: AdminUser,
    session: Session,
    limiter: Limiter,
    status: Literal["pending", "decided"] = "pending",
    cursor: str | None = None,
) -> OverrideRequestPage:
    """#17: leaves out the admin's own requests and conflicted ones."""
    await _admin_limit(session, limiter, admin.id, "read")
    return await _overrides.list_requests(session, admin.id, status=status, cursor=cursor)


@router.post(
    "/admin/scoring/override-requests/{request_id}/decision",
    response_model=OverrideDecisionOut,
    tags=["admin"],
)
async def decide_override_request(
    request_id: uuid.UUID,
    body: OverrideDecisionIn,
    admin: AdminUser,
    session: Session,
    limiter: Limiter,
) -> OverrideDecisionOut:
    """#18: approve or decline; 404 for the requester and conflicted admins."""
    await _admin_limit(session, limiter, admin.id, "write")
    request = await _overrides.decide_request(session, admin.id, request_id, body)
    await session.commit()
    (out,) = await _overrides.requests_out(session, [request])
    deed = None
    if body.decision == "approve":
        deed = await _reads.admin_deed(session, admin.id, request.deed_id)
    return OverrideDecisionOut(request=out, deed=deed)


@router.get("/admin/scoring/users/{handle}", response_model=AdminUserScoring, tags=["admin"])
async def admin_user(
    handle: str, admin: AdminUser, session: Session, limiter: Limiter
) -> AdminUserScoring:
    """#14: 404 for an unknown handle, the admin themself and a related person."""
    await _admin_limit(session, limiter, admin.id, "read")
    return await _revoke.admin_user(session, admin.id, handle)


@router.post(
    "/admin/scoring/titles/{title_id}/revoke", response_model=AdminUserScoring, tags=["admin"]
)
async def revoke_title(
    title_id: uuid.UUID, body: RevokeIn, admin: AdminUser, session: Session, limiter: Limiter
) -> AdminUserScoring:
    """#15 (D92)."""
    await _admin_limit(session, limiter, admin.id, "write")
    user_id = await _revoke.revoke_title(session, admin.id, title_id, body)
    await session.commit()
    return await _revoke.user_view(session, user_id)


@router.post(
    "/admin/scoring/badges/{badge_id}/revoke", response_model=AdminUserScoring, tags=["admin"]
)
async def revoke_badge(
    badge_id: uuid.UUID, body: RevokeIn, admin: AdminUser, session: Session, limiter: Limiter
) -> AdminUserScoring:
    """#16 (D92)."""
    await _admin_limit(session, limiter, admin.id, "write")
    user_id = await _revoke.revoke_badge(session, admin.id, badge_id, body)
    await session.commit()
    return await _revoke.user_view(session, user_id)
