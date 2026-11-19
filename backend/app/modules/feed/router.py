"""Feed API (docs/M3-DESIGN.md §3.4), mounted under /api. Organised by section.

Reads need ``CurrentUser``; writes need ``OnboardedUser`` (D44). The public deed page needs no
auth, and ``/users/{handle}/social`` works signed out. Routers stay thin: load the ``feed``
config, apply rate limits (before any lookup), call the service.
"""

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.errors import AppError
from app.core.ratelimit import Limiter, client_ip
from app.core.storage import ObjectStorage, get_storage
from app.modules.feed import (
    _comments,
    _cursor,
    _follows,
    _moderation,
    _notices,
    _reads,
    _reports,
    _writes,
)
from app.modules.feed._limits import FeedLimits, load_limits
from app.modules.feed._shared_limits import load_shared
from app.modules.feed.models import TopPeriod
from app.modules.feed.schemas import (
    CommentCaseDetail,
    CommentCasePage,
    CommentDecisionIn,
    CommentIn,
    CommentNotice,
    CommentOut,
    CommentPage,
    CommentReportIn,
    CommentRestoreIn,
    ConnectionPage,
    FeedOptions,
    LikeOut,
    PostDetail,
    PostPage,
    PublicDeed,
    ReportReceived,
    SaveOut,
    ShareIn,
    SocialOut,
    VisibilityOut,
)
from app.modules.identity import service as identity
from app.modules.identity.service import CurrentUser, OnboardedUser, ReviewerUser

router = APIRouter(tags=["feed"])
Session = Annotated[AsyncSession, Depends(get_session)]
Storage = Annotated[ObjectStorage, Depends(get_storage)]
Limit = Annotated[int, Query(ge=1, le=_cursor.MAX_PAGE_SIZE)]

_bearer = HTTPBearer(auto_error=False)

# Q40: the public page is never cached, never indexed, and sends only the origin as referrer.
PUBLIC_HEADERS = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "X-Robots-Tag": "noindex, nofollow",
}


async def _feed_read_limit(session: AsyncSession, limiter: Limiter, user_id: uuid.UUID) -> None:
    _, limits = await load_limits(session)
    await limiter.hit(f"feedread:u:{user_id}", limits.rate_feed_read_user)


# --------------------------------------------------- #1 feed, options


@router.get("/feed", response_model=PostPage)
async def get_feed(
    user: CurrentUser,
    session: Session,
    limiter: Limiter,
    storage: Storage,
    tab: _reads.Tab,
    period: TopPeriod | None = None,
    cursor: str | None = None,
    limit: Limit = _cursor.DEFAULT_PAGE_SIZE,
) -> PostPage:
    await _feed_read_limit(session, limiter, user.id)
    return await _reads.feed(
        session, user.id, storage, tab=tab, period=period, cursor=cursor, limit=limit
    )


@router.get("/feed/options", response_model=FeedOptions)
async def feed_options(user: CurrentUser, session: Session) -> FeedOptions:
    _, limits = await load_limits(session)
    return _reads.options(limits, await load_shared(session))


# --------------------------------------------------- #2 deed page


@router.get("/feed/deeds/{deed_id}", response_model=PostDetail)
async def get_post(
    deed_id: uuid.UUID, user: CurrentUser, session: Session, limiter: Limiter, storage: Storage
) -> PostDetail:
    await _feed_read_limit(session, limiter, user.id)
    return await _reads.deed_detail(session, deed_id, user.id, storage)


# --------------------------------------------------- #10 public page (no auth)


@router.get("/public/deeds/{deed_id}", response_model=PublicDeed)
async def public_deed(
    deed_id: uuid.UUID,
    request: Request,
    response: Response,
    session: Session,
    limiter: Limiter,
    storage: Storage,
) -> PublicDeed | JSONResponse:
    response.headers.update(PUBLIC_HEADERS)
    try:
        _, limits = await load_limits(session)
        await limiter.hit(f"public:ip:{client_ip(request)}", limits.rate_public_deed_ip)
        return await _reads.public_deed(session, deed_id, storage)
    except AppError as exc:
        # The same headers on the error answers (404 deed_unavailable, 429).
        body = {"detail": exc.detail} | ({"code": exc.code} if exc.code else {})
        return JSONResponse(status_code=exc.status_code, content=body, headers=PUBLIC_HEADERS)


# --------------------------------------------------- #11 profiles


async def _optional_user_id(
    session: Session,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> uuid.UUID | None:
    """The signed-in user, or None for a visitor. A bad token is still 401 (as everywhere)."""
    if credentials is None:
        return None
    return (await identity.current_user(session, credentials)).id


@router.get("/users/{handle}/social", response_model=SocialOut, tags=["users"])
async def social(
    handle: str,
    request: Request,
    session: Session,
    limiter: Limiter,
    viewer_id: Annotated[uuid.UUID | None, Depends(_optional_user_id)],
) -> SocialOut:
    _, limits = await load_limits(session)
    if viewer_id is None:
        await limiter.hit(f"public:ip:{client_ip(request)}", limits.rate_public_deed_ip)
    return await _reads.social(session, handle, viewer_id)


@router.get("/feed/users/{handle}/deeds", response_model=PostPage)
async def user_deeds(
    handle: str,
    user: CurrentUser,
    session: Session,
    limiter: Limiter,
    storage: Storage,
    cursor: str | None = None,
    limit: Limit = _cursor.DEFAULT_PAGE_SIZE,
) -> PostPage:
    await _feed_read_limit(session, limiter, user.id)
    return await _reads.user_deeds(session, handle, user.id, storage, cursor=cursor, limit=limit)


# --------------------------------------------------- #12 saved


@router.get("/me/saved", response_model=PostPage)
async def my_saved(
    user: CurrentUser,
    session: Session,
    limiter: Limiter,
    storage: Storage,
    cursor: str | None = None,
    limit: Limit = _cursor.DEFAULT_PAGE_SIZE,
) -> PostPage:
    await _feed_read_limit(session, limiter, user.id)
    return await _reads.saved(session, user.id, storage, cursor=cursor, limit=limit)


# --------------------------------------------------- #3 likes, #4 saves (OnboardedUser)


@router.put("/feed/deeds/{deed_id}/like", response_model=LikeOut)
async def like(
    deed_id: uuid.UUID, request: Request, user: OnboardedUser, session: Session, limiter: Limiter
) -> LikeOut:
    _, limits = await load_limits(session)
    await limiter.hit(f"like:u:{user.id}", limits.rate_like_user)
    await limiter.hit(f"like:ip:{client_ip(request)}", limits.rate_like_ip)
    out = await _writes.like(session, deed_id, user.id)
    await session.commit()
    return out


@router.delete("/feed/deeds/{deed_id}/like", response_model=LikeOut)
async def unlike(
    deed_id: uuid.UUID, request: Request, user: OnboardedUser, session: Session, limiter: Limiter
) -> LikeOut:
    _, limits = await load_limits(session)
    await limiter.hit(f"like:u:{user.id}", limits.rate_like_user)
    await limiter.hit(f"like:ip:{client_ip(request)}", limits.rate_like_ip)
    out = await _writes.unlike(session, deed_id, user.id)
    await session.commit()
    return out


@router.put("/feed/deeds/{deed_id}/save", response_model=SaveOut)
async def save(
    deed_id: uuid.UUID, user: OnboardedUser, session: Session, limiter: Limiter
) -> SaveOut:
    _, limits = await load_limits(session)
    await limiter.hit(f"save:u:{user.id}", limits.rate_save_user)
    out = await _writes.save(session, deed_id, user.id)
    await session.commit()
    return out


@router.delete("/feed/deeds/{deed_id}/save", response_model=SaveOut)
async def unsave(
    deed_id: uuid.UUID, user: OnboardedUser, session: Session, limiter: Limiter
) -> SaveOut:
    _, limits = await load_limits(session)
    await limiter.hit(f"save:u:{user.id}", limits.rate_save_user)
    out = await _writes.unsave(session, deed_id, user.id)
    await session.commit()
    return out


# --------------------------------------------------- #5 to #7 comments


@router.get("/feed/deeds/{deed_id}/comments", response_model=CommentPage)
async def list_comments(
    deed_id: uuid.UUID,
    user: CurrentUser,
    session: Session,
    limiter: Limiter,
    cursor: str | None = None,
    limit: Limit = _cursor.DEFAULT_PAGE_SIZE,
) -> CommentPage:
    await _feed_read_limit(session, limiter, user.id)
    return await _comments.list_comments(session, deed_id, user.id, cursor=cursor, limit=limit)


@router.post("/feed/deeds/{deed_id}/comments", response_model=CommentOut, status_code=201)
async def post_comment(
    deed_id: uuid.UUID,
    data: CommentIn,
    request: Request,
    user: OnboardedUser,
    session: Session,
    limiter: Limiter,
) -> CommentOut:
    _, limits = await load_limits(session)
    await limiter.hit(f"comment:u:{user.id}", limits.rate_comment_user)
    await limiter.hit(f"comment:ud:{user.id}", limits.rate_comment_user_daily)
    await limiter.hit(f"comment:ip:{client_ip(request)}", limits.rate_comment_ip)
    out = await _comments.post_comment(session, deed_id, user.id, data.body, limits=limits)
    await session.commit()
    return out


@router.delete("/feed/comments/{comment_id}", status_code=204)
async def delete_comment(comment_id: uuid.UUID, user: OnboardedUser, session: Session) -> None:
    await _comments.delete_comment(session, comment_id, user.id)
    await session.commit()


# --------------------------------------------------- #8 hide / show, #9 shares


@router.post("/feed/deeds/{deed_id}/hide", response_model=VisibilityOut)
async def hide(
    deed_id: uuid.UUID, user: OnboardedUser, session: Session, limiter: Limiter
) -> VisibilityOut:
    _, limits = await load_limits(session)
    await limiter.hit(f"hide:u:{user.id}", limits.rate_hide_user)
    out = await _writes.set_hidden(session, deed_id, user.id, hidden=True)
    await session.commit()
    return out


@router.post("/feed/deeds/{deed_id}/show", response_model=VisibilityOut)
async def show(
    deed_id: uuid.UUID, user: OnboardedUser, session: Session, limiter: Limiter
) -> VisibilityOut:
    _, limits = await load_limits(session)
    await limiter.hit(f"hide:u:{user.id}", limits.rate_hide_user)
    out = await _writes.set_hidden(session, deed_id, user.id, hidden=False)
    await session.commit()
    return out


@router.post("/feed/deeds/{deed_id}/shares", status_code=204)
async def record_share(
    deed_id: uuid.UUID, data: ShareIn, user: OnboardedUser, session: Session, limiter: Limiter
) -> None:
    _, limits = await load_limits(session)
    await limiter.hit(f"share:u:{user.id}", limits.rate_share_user)
    await _writes.record_share(session, deed_id, user.id, data.target)
    await session.commit()


# --------------------------------------------------- #11 follows


async def _follow_limits(
    session: AsyncSession, limiter: Limiter, request: Request, user_id: uuid.UUID
) -> FeedLimits:
    _, limits = await load_limits(session)
    await limiter.hit(f"follow:u:{user_id}", limits.rate_follow_user)
    await limiter.hit(f"follow:ip:{client_ip(request)}", limits.rate_follow_ip)
    return limits


@router.put("/users/{handle}/follow", response_model=SocialOut, tags=["users"])
async def follow(
    handle: str, request: Request, user: OnboardedUser, session: Session, limiter: Limiter
) -> SocialOut:
    limits = await _follow_limits(session, limiter, request, user.id)
    out = await _follows.follow(session, user.id, handle, limits=limits)
    await session.commit()
    return out


@router.delete("/users/{handle}/follow", response_model=SocialOut, tags=["users"])
async def unfollow(
    handle: str, request: Request, user: OnboardedUser, session: Session, limiter: Limiter
) -> SocialOut:
    await _follow_limits(session, limiter, request, user.id)
    out = await _follows.unfollow(session, user.id, handle)
    await session.commit()
    return out


@router.delete("/me/followers/{handle}", status_code=204, tags=["users"])
async def remove_follower(
    handle: str, request: Request, user: OnboardedUser, session: Session, limiter: Limiter
) -> None:
    await _follow_limits(session, limiter, request, user.id)
    await _follows.remove_follower(session, user.id, handle)
    await session.commit()


@router.get("/me/followers", response_model=ConnectionPage, tags=["users"])
async def my_followers(
    user: CurrentUser,
    session: Session,
    limiter: Limiter,
    cursor: str | None = None,
    limit: Limit = _cursor.DEFAULT_PAGE_SIZE,
) -> ConnectionPage:
    await _feed_read_limit(session, limiter, user.id)
    return await _follows.connections(
        session, user.id, kind="followers", cursor=cursor, limit=limit
    )


@router.get("/me/following", response_model=ConnectionPage, tags=["users"])
async def my_following(
    user: CurrentUser,
    session: Session,
    limiter: Limiter,
    cursor: str | None = None,
    limit: Limit = _cursor.DEFAULT_PAGE_SIZE,
) -> ConnectionPage:
    await _feed_read_limit(session, limiter, user.id)
    return await _follows.connections(
        session, user.id, kind="following", cursor=cursor, limit=limit
    )


# --------------------------------------------------- one-time notices (Q41)


@router.get("/feed/me/notices", response_model=list[str])
async def my_notices(user: CurrentUser, session: Session) -> list[str]:
    return await _notices.unseen(session, user.id)


@router.post("/feed/me/notices/{key}/seen", status_code=204)
async def notice_seen(key: str, user: CurrentUser, session: Session) -> None:
    await _notices.mark_seen(session, user.id, key)
    await session.commit()


# --------------------------------------------------- #13 comment reports, removal notices


@router.post("/feed/comments/{comment_id}/reports", response_model=ReportReceived, status_code=202)
async def report_comment(
    comment_id: uuid.UUID,
    data: CommentReportIn,
    user: OnboardedUser,
    session: Session,
    limiter: Limiter,
) -> ReportReceived:
    _, limits = await load_limits(session)
    await limiter.hit(f"commentreport:u:{user.id}", limits.rate_comment_report_user)
    shared = await load_shared(session)
    await _reports.report_comment(
        session, comment_id, user.id, data.reason, data.details, shared=shared
    )
    await session.commit()
    return ReportReceived()


@router.get("/feed/me/comment-notices", response_model=list[CommentNotice])
async def my_comment_notices(user: CurrentUser, session: Session) -> list[CommentNotice]:
    return await _reports.comment_notices(session, user.id, shared=await load_shared(session))


@router.post("/feed/me/comment-notices/{comment_id}/seen", status_code=204)
async def comment_notice_seen(comment_id: uuid.UUID, user: CurrentUser, session: Session) -> None:
    await _reports.mark_notice_seen(session, user.id, comment_id)
    await session.commit()


# --------------------------------------------------- #14 to #17 admin (moderators and admins, Q34)


async def _decision_limit(session: AsyncSession, limiter: Limiter, user_id: uuid.UUID) -> None:
    shared = await load_shared(session)
    # The same key and spec as photo decisions: one budget per reviewer.
    await limiter.hit(f"admindecision:u:{user_id}", shared.rate_admin_decision_user)


@router.get("/admin/comment-reports", response_model=CommentCasePage, tags=["admin"])
async def comment_report_queue(
    user: ReviewerUser,
    session: Session,
    status: Literal["open", "resolved"] = "open",
    cursor: str | None = None,
    limit: Limit = _cursor.DEFAULT_PAGE_SIZE,
) -> CommentCasePage:
    return await _moderation.list_cases(session, queue=status, cursor=cursor, limit=limit)


@router.get("/admin/comment-reports/{case_id}", response_model=CommentCaseDetail, tags=["admin"])
async def comment_report_detail(
    case_id: uuid.UUID, user: ReviewerUser, session: Session
) -> CommentCaseDetail:
    _, limits = await load_limits(session)
    return await _moderation.case_detail(session, case_id, limits=limits)


@router.post(
    "/admin/comment-reports/{case_id}/decision", response_model=CommentCaseDetail, tags=["admin"]
)
async def decide_comment_report(
    case_id: uuid.UUID,
    data: CommentDecisionIn,
    user: ReviewerUser,
    session: Session,
    limiter: Limiter,
) -> CommentCaseDetail:
    await _decision_limit(session, limiter, user.id)
    await _moderation.decide(
        session, user.id, case_id, data.action, data.note, shared=await load_shared(session)
    )
    await session.commit()
    _, limits = await load_limits(session)
    return await _moderation.case_detail(session, case_id, limits=limits)


@router.post(
    "/admin/comments/{comment_id}/restore", response_model=CommentCaseDetail, tags=["admin"]
)
async def restore_comment(
    comment_id: uuid.UUID,
    data: CommentRestoreIn,
    user: ReviewerUser,
    session: Session,
    limiter: Limiter,
) -> CommentCaseDetail:
    await _decision_limit(session, limiter, user.id)
    _, limits = await load_limits(session)
    case_id = await _moderation.restore(
        session, user.id, comment_id, data.note, limits=limits, shared=await load_shared(session)
    )
    await session.commit()
    return await _moderation.case_detail(session, case_id, limits=limits)
