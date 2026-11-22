"""Two workers finalize one month at once (docs/M4-DESIGN.md D64, §4.1): the per-month advisory
lock lets exactly one write it. This commits for real, in a month nobody else uses (March 2001,
no ledger rows, no outbox events that old), and deletes what it wrote afterwards, including the
append-only ``month_finalizations`` row (the sanctioned test exception)."""

import asyncio
from collections.abc import AsyncIterator
from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import engine
from app.modules.scoring import _finalize
from app.modules.scoring._finalize import FinalizeResult, Outcome, finalize_month
from tests.deeds.test_concurrency import _in_own_session, _meet_before
from tests.scoring.board_helpers import ist

MONTH = date(2001, 3, 1)


@pytest.fixture
async def clean_month(migrated_db: None) -> AsyncIterator[None]:
    yield
    params = {"m": MONTH, "s": MONTH.isoformat()}
    async with engine.begin() as conn:
        await conn.execute(
            text("ALTER TABLE month_finalizations DISABLE TRIGGER month_finalizations_append_only")
        )
        await conn.execute(text("DELETE FROM month_finalizations WHERE period_month = :m"), params)
        await conn.execute(
            text("ALTER TABLE month_finalizations ENABLE TRIGGER month_finalizations_append_only")
        )
        await conn.execute(
            text(
                "DELETE FROM outbox_events WHERE event_type = 'scoring.month_finalized' "
                "AND payload->>'period_month' = :s"
            ),
            params,
        )


async def test_two_concurrent_finalizations_write_once(
    clean_month: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    _meet_before(monkeypatch, _finalize, "advisory_xact_lock")
    now = ist(2001, 4, 5)

    async def run(s: AsyncSession) -> FinalizeResult:
        return await finalize_month(s, MONTH, now=now)

    results = await asyncio.gather(_in_own_session(run), _in_own_session(run))
    outcomes = sorted(r.outcome for r in results if isinstance(r, FinalizeResult))
    assert outcomes == sorted([Outcome.FINALIZED, Outcome.ALREADY_FINALIZED])
    async with engine.connect() as conn:
        count = await conn.scalar(
            text(
                "SELECT count(*) FROM outbox_events WHERE event_type = 'scoring.month_finalized' "
                "AND payload->>'period_month' = :s"
            ),
            {"s": MONTH.isoformat()},
        )
    assert count == 1
