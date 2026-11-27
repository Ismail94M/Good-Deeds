"""QA10: M5 effects on final months and titles, through the real review flow (docs/M5-DESIGN.md
D92, D93, D100, §8 "Holds, scoring and finalization", "Overrides and revocation"):

- the finalization barrier waits for the hold events a real confirm emits, and the final board
  then leaves the held deeds out;
- after a month is final, an override and a fraud rejection write ledger rows dated in that
  month while its final boards and titles never move;
- a title can be revoked only once the decision is final for the person: not while the appeal
  window is open or an appeal waits, yes after an upheld appeal or a closed window, never after
  an overturn.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.deeds.models import Deed, DeedStatus
from app.modules.fraud.models import FlagCaseType
from app.modules.scoring import _leaderboard, _reads
from app.modules.scoring._finalize import Outcome, finalize_month
from app.modules.scoring.models import HeroTitle, LedgerReason, LedgerRole
from app.modules.scoring.service import final_snapshot, titles_for
from tests.deeds.helpers import Member, members, scan_and_report
from tests.fraud.appeal_helpers import (
    appeal,
    appeal_of,
    confirm,
    decide_appeal,
    drain,
    finalize,
    notice_of,
)
from tests.fraud.helpers import deed
from tests.fraud.review_helpers import admin, open_case
from tests.scoring.board_helpers import city, counted, ist, month, ward
from tests.scoring.helpers import ledger, points, run_scoring
from tests.scoring.override_helpers import override

pytestmark = pytest.mark.usefixtures("city_board_on")

SEP = month(2026, 9)
SEP_CUTOFF = ist(2026, 10, 2, 1, 0)
AFTER = SEP_CUTOFF + timedelta(minutes=1)
LATER = timedelta(days=31)
NOTE = "Confirmed reciprocal recognitions."


async def _scored(session: AsyncSession, reporter: Member, doer: Member, day: int) -> uuid.UUID:
    out = await scan_and_report(session, reporter, doer, at=ist(2026, 9, day))
    assert out.status == "verified"
    await run_scoring(session, out.id)
    return out.id


async def _status(session: AsyncSession, deed_id: uuid.UUID) -> DeedStatus:
    status = await session.scalar(
        select(Deed.status).where(Deed.id == deed_id).execution_options(populate_existing=True)
    )
    assert status is not None
    return status


# --------------------------------------------------- barrier with a real confirm (D93 b)


async def test_finalization_waits_for_hold_events_from_a_real_confirm(
    session: AsyncSession,
) -> None:
    a, b, r, control = await members(session, 4)
    decider = await admin(session)
    ab = await _scored(session, a, b, 10)
    ba = await _scored(session, b, a, 11)
    await _scored(session, r, control, 12)
    case_id = await open_case(session, FlagCaseType.RECIPROCAL, [a, b], [ab, ba])
    await confirm(session, decider, case_id, {ab: "exclude", ba: "reject"})

    # Two DeedHoldChanged events nobody has scored yet: the month must not finalize.
    waiting = await finalize_month(session, SEP, now=AFTER)
    assert (waiting.outcome, waiting.pending_events) == (Outcome.EVENTS_PENDING, 2)
    status = await _reads.admin_month(session, "2026-09")
    assert status.pending_hold_events == 2

    await run_scoring(session, ab, ba)
    done = await finalize_month(session, SEP, now=AFTER)
    assert done.outcome == Outcome.FINALIZED
    final = await final_snapshot(session, await city(session), SEP)
    assert final is not None
    assert {e.user_id for e in final.entries} == {control.user_id}


# --------------------------------------------------- after final (D93 d)


async def test_after_final_an_override_and_a_rejection_change_the_ledger_not_the_board(
    client: AsyncClient, session: AsyncSession
) -> None:
    a, b, r, control = await members(session, 4)
    decider, overrider = await admin(session), await admin(session)
    ab = await _scored(session, a, b, 10)  # doer b
    ba = await _scored(session, b, a, 11)  # doer a
    await _scored(session, r, control, 12)
    await session.commit()
    done = await finalize_month(session, SEP, now=datetime.now(UTC))
    assert done.outcome == Outcome.FINALIZED
    await session.commit()
    city_id = await city(session)
    final = await final_snapshot(session, city_id, SEP)
    assert final is not None
    assert {a.user_id, b.user_id, control.user_id} <= {e.user_id for e in final.entries}
    titles = await titles_for(session, [a.user_id, b.user_id, control.user_id])
    rows_before = {d: {row.id for row in await ledger(session, d)} for d in (ab, ba)}

    # An override on a's deed (10 -> 5): new ledger rows, dated in September.
    res = await override(client, overrider, ba, points=5)
    assert res.status_code == 200, res.text
    assert await points(session, ba, LedgerRole.DOER) == 5
    new_rows = [row for row in await ledger(session, ba) if row.id not in rows_before[ba]]
    assert new_rows and all(row.period_month == SEP for row in new_rows)
    assert {row.reason for row in new_rows} == {LedgerReason.ADMIN_OVERRIDE}

    # A rejection of a -> b, final after the appeal window: reversal rows dated in September.
    case_id = await open_case(session, FlagCaseType.RECIPROCAL, [a, b], [ab, ba])
    await confirm(session, decider, case_id, {ab: "reject", ba: "release"})
    result = await finalize(session, datetime.now(UTC) + LATER)
    assert result.rejected == {case_id: [ab]}
    await drain(session, [ab, ba], [case_id])
    assert await _status(session, ab) == DeedStatus.REJECTED
    assert sum(row.points for row in await ledger(session, ab)) == 0
    late = [row for row in await ledger(session, ab) if row.id not in rows_before[ab]]
    assert late and all(row.period_month == SEP for row in late)

    # The final board, its snapshot and the titles never move; refreshes skip the month.
    again = await final_snapshot(session, city_id, SEP)
    assert again is not None and again.entries == final.entries
    latest = await _leaderboard.latest_snapshot(session, city_id, SEP)
    assert latest is not None and latest.id == final.id
    assert SEP not in await _leaderboard.refresh_due(session, now=datetime.now(UTC))
    assert await titles_for(session, [a.user_id, b.user_id, control.user_id]) == titles


# --------------------------------------------------- revocation only when final (D92, D100)


async def _hero(session: AsyncSession) -> tuple[Member, HeroTitle]:
    """``a`` is September's ward Hero of a test ward (3 deeds, 2 reporters)."""
    a, b, r1, r2 = await members(session, 4)
    region = await ward(session, "RV1")
    for day, reporter in ((3, r1), (4, r2), (5, r1)):
        await counted(session, a, reporter, region_id=region, verified_at=ist(2026, 9, day))
    for day, reporter in ((6, r1), (7, r2), (8, r1)):
        await counted(
            session, b, reporter, region_id=region, verified_at=ist(2026, 9, day), points=5
        )
    await finalize_month(session, SEP, now=ist(2026, 10, 3))
    await session.commit()
    title = await session.scalar(
        select(HeroTitle).where(
            HeroTitle.user_id == a.user_id, HeroTitle.region_id == region, HeroTitle.place == 1
        )
    )
    assert title is not None
    return a, title


async def _confirmed_about(session: AsyncSession, hero: Member, decider: Member) -> uuid.UUID:
    """A real reciprocal case hero <-> x, confirmed with hero's deed excluded. Its deeds are
    from September, the title's month (SEC5-L4, ``case_not_for_this_month``)."""
    (x,) = await members(session, 1)
    hx = await deed(session, hero, x, ist(2026, 9, 10))
    xh = await deed(session, x, hero, ist(2026, 9, 11))
    case_id = await open_case(session, FlagCaseType.RECIPROCAL, [hero, x], [hx, xh])
    await confirm(session, decider, case_id, {hx: "exclude", xh: "release"})
    return case_id


async def _revoke(
    client: AsyncClient, who: Member, title: HeroTitle, case_id: uuid.UUID
) -> tuple[int, str | None]:
    res = await client.post(
        f"/api/admin/scoring/titles/{title.id}/revoke",
        json={"case_id": str(case_id), "note": NOTE},
        headers=who.headers,
    )
    return res.status_code, res.json().get("code")


async def test_revocation_waits_for_the_appeal_and_follows_an_uphold(
    client: AsyncClient, session: AsyncSession
) -> None:
    hero, title = await _hero(session)
    decider, reviewer, revoker = await admin(session), await admin(session), await admin(session)
    case_id = await _confirmed_about(session, hero, decider)

    # Window open, no appeal yet.
    assert await _revoke(client, revoker, title, case_id) == (409, "decision_not_final")
    notice = await notice_of(session, case_id, hero)
    assert (await appeal(client, hero, notice.id)).status_code == 200
    # Appeal waiting, even after the window closed.
    await finalize(session, datetime.now(UTC) + LATER)
    assert await _revoke(client, revoker, title, case_id) == (409, "decision_not_final")

    appeal_id = (await appeal_of(session, notice.id)).id
    assert (await decide_appeal(client, reviewer, appeal_id, "upheld")).status_code == 200
    assert await _revoke(client, revoker, title, case_id) == (200, None)
    await session.refresh(title)
    assert title.revoked_at is not None


async def test_revocation_after_the_window_closes_with_no_appeal(
    client: AsyncClient, session: AsyncSession
) -> None:
    hero, title = await _hero(session)
    decider, revoker = await admin(session), await admin(session)
    case_id = await _confirmed_about(session, hero, decider)
    assert await _revoke(client, revoker, title, case_id) == (409, "decision_not_final")
    await finalize(session, datetime.now(UTC) + LATER)
    assert await _revoke(client, revoker, title, case_id) == (200, None)


async def test_an_overturned_decision_never_allows_revocation(
    client: AsyncClient, session: AsyncSession
) -> None:
    hero, title = await _hero(session)
    decider, reviewer, revoker = await admin(session), await admin(session), await admin(session)
    case_id = await _confirmed_about(session, hero, decider)
    notice = await notice_of(session, case_id, hero)
    assert (await appeal(client, hero, notice.id)).status_code == 200
    appeal_id = (await appeal_of(session, notice.id)).id
    assert (await decide_appeal(client, reviewer, appeal_id, "overturned")).status_code == 200
    assert await _revoke(client, revoker, title, case_id) == (409, "case_not_confirmed")
    await finalize(session, datetime.now(UTC) + 3 * LATER)
    assert await _revoke(client, revoker, title, case_id) == (409, "case_not_confirmed")
    await session.refresh(title)
    assert title.revoked_at is None


async def test_the_deciding_admin_may_revoke_but_a_subject_of_another_case_is_not_enough(
    client: AsyncClient, session: AsyncSession
) -> None:
    """D92: the case must be about the title holder. A final confirmed case about someone else
    is ``case_not_confirmed``."""
    hero, title = await _hero(session)
    decider = await admin(session)
    (someone,) = await members(session, 1)
    other_case = await _confirmed_about(session, someone, decider)
    await finalize(session, datetime.now(UTC) + LATER)
    assert await _revoke(client, decider, title, other_case) == (409, "case_not_confirmed")
    own_case = await _confirmed_about(session, hero, decider)
    await finalize(session, datetime.now(UTC) + LATER)
    assert await _revoke(client, decider, title, own_case) == (200, None)
