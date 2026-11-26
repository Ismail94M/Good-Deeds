"""Fraud HTTP routes (docs/M5-DESIGN.md §3.3), mounted under /api.

``/api/admin/*`` is admin only (``identity.service.AdminUser``: signed out 401, members and
moderators 403). Conflicted admins get 404 on a case or an appeal (D90), and so does the admin
who made the decision under appeal (D100). Reads are limited per account by
``fraud.rate_admin_read``, writes by ``fraud.rate_admin_write``. Routers stay thin: limit, call
the service, commit, then read the result back. There is no hold endpoint (PO Q73).

``/api/reviews`` (M1 to M4) is for any signed-in person, about their own items only (404 for
anyone else's). The member writes (reply, appeal) share ``fraud.rate_admin_write`` per account:
the ``fraud`` config has no separate member rate yet.
"""

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.ratelimit import Limiter
from app.modules.fraud import _appeals, _audit_view, _explain, _member, _review
from app.modules.fraud._common import utcnow
from app.modules.fraud._limits import FraudConfig, load_fraud
from app.modules.fraud.schemas import (
    AppealDecisionIn,
    AppealDetail,
    AppealIn,
    AppealPage,
    AuditPage,
    CaseDetail,
    CasePage,
    DecisionIn,
    ExplanationsIn,
    FraudOptions,
    FraudStatus,
    MyExplanation,
    MyNotice,
    MyReviews,
    NoteIn,
    ReplyIn,
)
from app.modules.identity.service import AdminUser, CurrentUser

router = APIRouter(tags=["admin"])
Session = Annotated[AsyncSession, Depends(get_session)]
CaseTypeParam = Literal["reciprocal", "ring", "concentration", "invite_anomaly", "content"]


async def _read(session: AsyncSession, limiter: Limiter, admin_id: uuid.UUID) -> FraudConfig:
    _, config = await load_fraud(session)
    await limiter.hit(f"fraud:read:u:{admin_id}", config.rate_admin_read)
    return config


async def _write(session: AsyncSession, limiter: Limiter, admin_id: uuid.UUID) -> FraudConfig:
    _, config = await load_fraud(session)
    await limiter.hit(f"fraud:write:u:{admin_id}", config.rate_admin_write)
    return config


async def _detail(
    session: AsyncSession, admin_id: uuid.UUID, case_id: uuid.UUID, config: FraudConfig
) -> CaseDetail:
    return await _review.case_detail(session, admin_id, case_id, config=config, now=utcnow())


# --------------------------------------------------- #1, #7


@router.get("/admin/fraud/options", response_model=FraudOptions)
async def fraud_options(admin: AdminUser, session: Session, limiter: Limiter) -> FraudOptions:
    return _review.options(await _read(session, limiter, admin.id))


@router.get("/admin/fraud/status", response_model=FraudStatus)
async def fraud_status(admin: AdminUser, session: Session, limiter: Limiter) -> FraudStatus:
    await _read(session, limiter, admin.id)
    version, config = await load_fraud(session)
    return await _review.status(
        session, admin.id, config=config, config_version=version, now=utcnow()
    )


# --------------------------------------------------- #2, #3


@router.get("/admin/fraud/cases", response_model=CasePage)
async def list_cases(
    admin: AdminUser,
    session: Session,
    limiter: Limiter,
    status: Literal["open", "parked", "resolved"] = "open",
    type: CaseTypeParam | None = None,
    user: Annotated[str | None, Query(max_length=60)] = None,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
) -> CasePage:
    config = await _read(session, limiter, admin.id)
    return await _review.list_cases(
        session,
        admin.id,
        queue=status,
        case_type=type,
        user_handle=user,
        cursor=cursor,
        config=config,
        now=utcnow(),
    )


@router.get("/admin/fraud/cases/{case_id}", response_model=CaseDetail)
async def case_detail(
    case_id: uuid.UUID, admin: AdminUser, session: Session, limiter: Limiter
) -> CaseDetail:
    config = await _read(session, limiter, admin.id)
    return await _detail(session, admin.id, case_id, config)


# --------------------------------------------------- #4, #5, #6, #10


@router.post("/admin/fraud/cases/{case_id}/park", response_model=CaseDetail)
async def park_case(
    case_id: uuid.UUID, data: NoteIn, admin: AdminUser, session: Session, limiter: Limiter
) -> CaseDetail:
    config = await _write(session, limiter, admin.id)
    await _review.park(session, admin.id, case_id, data.note, config=config, now=utcnow())
    await session.commit()
    return await _detail(session, admin.id, case_id, config)


@router.post("/admin/fraud/cases/{case_id}/unpark", response_model=CaseDetail)
async def unpark_case(
    case_id: uuid.UUID, data: NoteIn, admin: AdminUser, session: Session, limiter: Limiter
) -> CaseDetail:
    config = await _write(session, limiter, admin.id)
    await _review.unpark(session, admin.id, case_id, data.note, config=config, now=utcnow())
    await session.commit()
    return await _detail(session, admin.id, case_id, config)


@router.post("/admin/fraud/cases/{case_id}/decision", response_model=CaseDetail)
async def decide_case(
    case_id: uuid.UUID, data: DecisionIn, admin: AdminUser, session: Session, limiter: Limiter
) -> CaseDetail:
    config = await _write(session, limiter, admin.id)
    await _review.decide(session, admin.id, case_id, data, config=config, now=utcnow())
    await session.commit()
    return await _detail(session, admin.id, case_id, config)


@router.post("/admin/fraud/cases/{case_id}/explanations", response_model=CaseDetail)
async def request_explanations(
    case_id: uuid.UUID,
    data: ExplanationsIn,
    admin: AdminUser,
    session: Session,
    limiter: Limiter,
) -> CaseDetail:
    config = await _write(session, limiter, admin.id)
    await _explain.request_explanations(
        session, admin.id, case_id, data, config=config, now=utcnow()
    )
    await session.commit()
    return await _detail(session, admin.id, case_id, config)


# --------------------------------------------------- #8 audit view (D96)


@router.get("/admin/audit", response_model=AuditPage)
async def audit_view(
    admin: AdminUser,
    session: Session,
    limiter: Limiter,
    entity_type: Annotated[str | None, Query(max_length=100)] = None,
    entity_id: Annotated[str | None, Query(max_length=100)] = None,
    actor: Annotated[str | None, Query(max_length=60)] = None,
    action: Annotated[str | None, Query(max_length=100)] = None,
    since: Annotated[datetime | None, Query(alias="from")] = None,
    until: Annotated[datetime | None, Query(alias="to")] = None,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
) -> AuditPage:
    config = await _read(session, limiter, admin.id)
    return await _audit_view.audit_page(
        session,
        entity_type=entity_type,
        entity_id=entity_id,
        actor_handle=actor,
        action=action,
        since=since,
        until=until,
        cursor=cursor,
        config=config,
        now=utcnow(),
    )


# --------------------------------------------------- #11, #12 appeals (D100)


@router.get("/admin/fraud/appeals", response_model=AppealPage)
async def list_appeals(
    admin: AdminUser,
    session: Session,
    limiter: Limiter,
    status: Literal["open", "decided"] = "open",
    cursor: Annotated[str | None, Query(max_length=500)] = None,
) -> AppealPage:
    config = await _read(session, limiter, admin.id)
    return await _appeals.list_appeals(
        session, admin.id, queue=status, cursor=cursor, config=config, now=utcnow()
    )


@router.get("/admin/fraud/appeals/{appeal_id}", response_model=AppealDetail)
async def appeal_detail(
    appeal_id: uuid.UUID, admin: AdminUser, session: Session, limiter: Limiter
) -> AppealDetail:
    config = await _read(session, limiter, admin.id)
    return await _appeals.appeal_detail(session, admin.id, appeal_id, config=config, now=utcnow())


@router.post("/admin/fraud/appeals/{appeal_id}/decision", response_model=AppealDetail)
async def decide_appeal(
    appeal_id: uuid.UUID,
    data: AppealDecisionIn,
    admin: AdminUser,
    session: Session,
    limiter: Limiter,
) -> AppealDetail:
    config = await _write(session, limiter, admin.id)
    await _appeals.decide_appeal(session, admin.id, appeal_id, data, config=config, now=utcnow())
    await session.commit()
    return await _appeals.appeal_detail(session, admin.id, appeal_id, config=config, now=utcnow())


# --------------------------------------------------- M1 to M4 member reviews (D99 to D101)


@router.get("/reviews", response_model=MyReviews, tags=["reviews"])
async def my_reviews(user: CurrentUser, session: Session) -> MyReviews:
    _, config = await load_fraud(session)
    return await _member.my_reviews(session, user.id, config=config, now=utcnow())


@router.post(
    "/reviews/explanations/{request_id}/reply", response_model=MyExplanation, tags=["reviews"]
)
async def reply_to_explanation(
    request_id: uuid.UUID, data: ReplyIn, user: CurrentUser, session: Session, limiter: Limiter
) -> MyExplanation:
    config = await _write(session, limiter, user.id)
    await _member.reply(session, user.id, request_id, data.reply, config=config, now=utcnow())
    await session.commit()
    return await _member.my_explanation(session, user.id, request_id, config=config, now=utcnow())


@router.post("/reviews/notices/{notice_id}/appeal", response_model=MyNotice, tags=["reviews"])
async def appeal_notice(
    notice_id: uuid.UUID, data: AppealIn, user: CurrentUser, session: Session, limiter: Limiter
) -> MyNotice:
    config = await _write(session, limiter, user.id)
    await _appeals.create_appeal(
        session, user.id, notice_id, data.reason, config=config, now=utcnow()
    )
    await session.commit()
    return await _member.my_notice(session, user.id, notice_id, config=config, now=utcnow())


@router.post("/reviews/seen", status_code=204, tags=["reviews"])
async def reviews_seen(user: CurrentUser, session: Session) -> Response:
    await _member.mark_seen(session, user.id, utcnow())
    await session.commit()
    return Response(status_code=204)
