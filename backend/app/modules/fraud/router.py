"""Fraud HTTP routes (docs/M5-DESIGN.md §3.3), mounted under /api.

Admin only (``identity.service.AdminUser``: signed out 401, members and moderators 403).
Conflicted admins get 404 on a case (D90). Reads are limited per account by
``fraud.rate_admin_read``, writes by ``fraud.rate_admin_write``. Routers stay thin: limit, call
the service, commit, then read the case back. There is no hold endpoint (PO Q73). Appeals and
the member ``/api/reviews`` endpoints are task B45.
"""

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.ratelimit import Limiter
from app.modules.fraud import _audit_view, _explain, _review
from app.modules.fraud._common import utcnow
from app.modules.fraud._limits import FraudConfig, load_fraud
from app.modules.fraud.schemas import (
    AuditPage,
    CaseDetail,
    CasePage,
    DecisionIn,
    ExplanationsIn,
    FraudOptions,
    FraudStatus,
    NoteIn,
)
from app.modules.identity.service import AdminUser

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
