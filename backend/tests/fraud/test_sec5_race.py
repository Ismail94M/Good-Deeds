"""SEC5-L1: an appeal sent at the edge of its window races ``finalize_decisions``.

Two real sessions on committed data (like ``tests/deeds/test_concurrency.py``): the appeal holds
the notice row lock; the finalize run waits for it and must then see the appeal, so the notice
is not made final. The fixture deletes everything it committed afterwards.
"""

import asyncio
import hashlib
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text

from app.core.db import SessionLocal, engine
from app.core.errors import Conflict
from app.modules.fraud._appeals import create_appeal
from app.modules.fraud._finalize import finalize_decisions
from app.modules.fraud._limits import load_fraud
from app.modules.fraud.models import (
    Appeal,
    DecisionNotice,
    FlagCase,
    FlagCaseType,
    FlagCaseUser,
    NoticeKind,
)
from tests.deeds.helpers import make_member

REASON = "We are neighbours and really did help each other that week."
_TABLES_WITH_GUARDS = (
    ("appeals", "appeals_decide_once"),
    ("decision_notices", "decision_notices_final_once"),
    ("flag_case_users", "flag_case_users_append_only"),
    ("flag_cases", "flag_cases_guard"),
)


@dataclass
class Committed:
    user_ids: list[uuid.UUID] = field(default_factory=list)
    case_ids: list[uuid.UUID] = field(default_factory=list)


@pytest.fixture
async def committed(migrated_db: None) -> AsyncIterator[Committed]:
    tracker = Committed()
    yield tracker
    async with engine.begin() as conn:
        for table, trigger in _TABLES_WITH_GUARDS:
            await conn.execute(text(f"ALTER TABLE {table} DISABLE TRIGGER {trigger}"))
        c = {"c": tracker.case_ids}
        await conn.execute(
            text(
                "DELETE FROM appeals WHERE notice_id IN "
                "(SELECT id FROM decision_notices WHERE case_id = ANY(:c))"
            ),
            c,
        )
        await conn.execute(text("DELETE FROM decision_notices WHERE case_id = ANY(:c)"), c)
        await conn.execute(text("DELETE FROM flag_case_users WHERE case_id = ANY(:c)"), c)
        await conn.execute(text("DELETE FROM flag_cases WHERE id = ANY(:c)"), c)
        for table, trigger in _TABLES_WITH_GUARDS:
            await conn.execute(text(f"ALTER TABLE {table} ENABLE TRIGGER {trigger}"))
        await conn.execute(
            text("DELETE FROM users WHERE id = ANY(:ids)"), {"ids": tracker.user_ids}
        )


async def _notice(tracker: Committed, appeal_until: datetime) -> tuple[uuid.UUID, uuid.UUID]:
    """A committed member with a case notice whose window ends at ``appeal_until``."""
    async with SessionLocal() as s:
        member = await make_member(s, phone_history=False)
    tracker.user_ids.append(member.user_id)
    async with SessionLocal() as s, s.begin():
        case_id = uuid.uuid4()
        s.add(
            FlagCase(
                id=case_id,
                type=FlagCaseType.CONTENT,
                subject_key=hashlib.sha256(case_id.bytes).hexdigest(),
                detector_config_version=1,
            )
        )
        await s.flush()
        s.add(FlagCaseUser(case_id=case_id, user_id=member.user_id))
        await s.flush()
        notice = DecisionNotice(
            user_id=member.user_id,
            kind=NoticeKind.CASE,
            case_id=case_id,
            items=[],
            reason_key="case",
            created_at=appeal_until - timedelta(days=30),
            appeal_until=appeal_until,
        )
        s.add(notice)
        await s.flush()
        tracker.case_ids.append(case_id)
        return member.user_id, notice.id


async def test_finalize_waits_for_an_appeal_at_the_window_edge_and_sees_it(
    committed: Committed,
) -> None:
    edge = datetime.now(UTC) - timedelta(minutes=1)
    user_id, notice_id = await _notice(committed, edge)

    async with SessionLocal() as appealing, SessionLocal() as finalizing:
        _, config = await load_fraud(appealing)
        # The member appeals a moment before the window ends; not committed yet.
        await create_appeal(
            appealing,
            user_id,
            notice_id,
            REASON,
            config=config,
            now=edge - timedelta(seconds=1),
        )

        async def run_finalize() -> int:
            result = await finalize_decisions(finalizing, edge + timedelta(seconds=1))
            await finalizing.commit()
            return result.notices_final

        task = asyncio.create_task(run_finalize())
        await asyncio.sleep(0.5)  # finalize is now waiting on the notice row lock
        assert not task.done()
        await appealing.commit()
        await asyncio.wait_for(task, timeout=10)

    async with SessionLocal() as s:
        notice = await s.get(DecisionNotice, notice_id)
        assert notice is not None and notice.final_at is None
        assert await s.scalar(select(Appeal.id).where(Appeal.notice_id == notice_id))


async def test_an_appeal_after_finalize_took_the_notice_is_refused(
    committed: Committed,
) -> None:
    edge = datetime.now(UTC) - timedelta(minutes=1)
    user_id, notice_id = await _notice(committed, edge)

    async with SessionLocal() as appealing, SessionLocal() as finalizing:
        _, config = await load_fraud(appealing)
        await finalize_decisions(finalizing, edge + timedelta(seconds=1))  # locks, not committed

        async def run_appeal() -> None:
            await create_appeal(
                appealing,
                user_id,
                notice_id,
                REASON,
                config=config,
                now=edge - timedelta(seconds=1),
            )

        task = asyncio.create_task(run_appeal())
        await asyncio.sleep(0.5)
        assert not task.done()
        await finalizing.commit()
        with pytest.raises(Conflict) as err:
            await asyncio.wait_for(task, timeout=10)
        assert err.value.code == "appeal_closed"
        await appealing.rollback()

    async with SessionLocal() as s:
        notice = await s.get(DecisionNotice, notice_id)
        assert notice is not None and notice.final_at is not None
