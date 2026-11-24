"""The points ledger and ``reconcile_deed`` (docs/M4-DESIGN.md D48 to D58, §4.2, §4.3, §8 Points).

Deeds are created through the deeds services with a controlled clock; the scoring handlers are
run as the worker would (``run_scoring``), so each test also exercises the event wiring.
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import OutboxEvent, emit, handlers_for
from app.modules.deeds import _edits
from app.modules.deeds import service as deeds
from app.modules.deeds._limits import load_limits
from app.modules.deeds.events import DeedRejected, DeedVerified
from app.modules.deeds.models import Deed, DeedStatus
from app.modules.deeds.schemas import DeedEdit, DeedOut
from app.modules.media.events import PhotoModerationDecided, PhotoRemovedByDoer
from app.modules.scoring._ledger import reconcile_deed
from app.modules.scoring.models import (
    EvidenceLevel,
    LedgerKind,
    LedgerReason,
    LedgerRole,
    PointsLedger,
    ScoringEvidence,
    TextCheck,
)
from tests.deeds.helpers import Member, members, scan_and_report
from tests.deeds.invite_helpers import accept, create_invite, withdraw
from tests.scoring.helpers import (
    SCORING_PREFIX,
    active,
    evaluations,
    ledger,
    photo_ready,
    points,
    run_scoring,
    shape,
    user_points,
)

DOER, REPORTER = LedgerRole.DOER, LedgerRole.REPORTER
# 1 November 2026 00:00 IST.
NOV_1 = datetime(2026, 10, 31, 18, 30, tzinfo=UTC)
DAY = timedelta(days=1)
SPAM = "Helped someone today, follow me for more"


def _verified_at(out: DeedOut) -> datetime:
    assert out.verified_at is not None
    return out.verified_at


async def _inputs(session: AsyncSession, deed_id: uuid.UUID) -> deeds.ScoringInputs:
    inputs = await deeds.scoring_inputs(session, deed_id)
    assert inputs is not None
    return inputs


async def _counts(session: AsyncSession, deed_id: uuid.UUID) -> bool:
    return (await _inputs(session, deed_id)).counts_for_points


async def _group(session: AsyncSession, deed_id: uuid.UUID) -> uuid.UUID:
    group = (await _inputs(session, deed_id)).same_act_group_id
    assert group is not None
    return group


async def _edit(
    session: AsyncSession, reporter: Member, deed_id: uuid.UUID, at: datetime, **fields: str
) -> None:
    _, limits = await load_limits(session)
    await _edits.edit_deed(
        session, reporter.user_id, deed_id, DeedEdit.model_validate(fields), limits=limits, now=at
    )


async def _scored(session: AsyncSession, reporter: Member, doer: Member, **kw: object) -> DeedOut:
    out = await scan_and_report(session, reporter, doer, **kw)  # type: ignore[arg-type]
    await run_scoring(session, out.id)
    return out


# --------------------------------------------------- basic award


async def test_qr_deed_gives_doer_10_and_reporter_2(session: AsyncSession) -> None:
    reporter, doer = await members(session, 2)
    out = await scan_and_report(session, reporter, doer)
    assert await run_scoring(session, out.id) >= 1

    d = await active(session, out.id, DOER)
    r = await active(session, out.id, REPORTER)
    assert d is not None and r is not None
    assert (d.kind, d.points, d.deed_units, d.reason) == (
        LedgerKind.AWARD,
        10,
        1,
        LedgerReason.VERIFIED,
    )
    assert d.user_id == doer.user_id and d.counterpart_user_id == reporter.user_id
    assert d.counts_for_prize_rank is True  # a QR deed is prize-rank eligible
    assert (r.kind, r.points, r.deed_units, r.counts_for_prize_rank) == (
        LedgerKind.AWARD,
        2,
        0,
        False,
    )
    assert r.user_id == reporter.user_id and r.counterpart_user_id == doer.user_id
    verified = _verified_at(out)
    ist_day = verified.astimezone(deeds_tz()).date()
    for row in (d, r):
        assert row.verified_at == verified
        assert (row.earned_on, row.period_month) == (ist_day, ist_day.replace(day=1))
    # Region frozen at the first entry: the deed's ward, or the city when it has none (D66).
    region = await deeds.deed_region(session, out.id)
    assert region is not None and d.region_id == r.region_id == region.id
    assert d.breakdown == {
        "base": 10,
        "text_check": "ok",
        "photo_bonus": 0,
        "pair_ordinal": 1,
        "pair_percent": 100,
        "cap": None,
    }
    # One rule-based evaluation, linked from the doer entry (D54).
    (evaluation,) = await evaluations(session, out.id)
    assert d.evaluation_id == evaluation.id
    assert (evaluation.rule_points, evaluation.text_check) == (10, TextCheck.OK)
    assert evaluation.evidence_level == EvidenceLevel.TEXT_ONLY
    assert evaluation.human_review_required is False
    # PointsChanged per role, ids and numbers only.
    changes = await _points_changed(session, out.id)
    assert sorted((c["role"], c["delta"], c["reason"]) for c in changes) == [
        ("doer", 10, "verified"),
        ("reporter", 2, "verified"),
    ]


def deeds_tz() -> ZoneInfo:
    return ZoneInfo("Asia/Kolkata")


async def _points_changed(session: AsyncSession, deed_id: uuid.UUID) -> list[dict[str, object]]:
    rows = await session.scalars(
        select(OutboxEvent.payload).where(
            OutboxEvent.event_type == "scoring.points_changed",
            OutboxEvent.payload["deed_id"].astext == str(deed_id),
        )
    )
    return list(rows)


async def test_day_and_month_come_from_server_verification_time_in_ist(
    session: AsyncSession,
) -> None:
    a, b, doer = await members(session, 3)
    # 23:59 IST on 31 October and 00:01 IST on 1 November.
    late = await scan_and_report(session, a, doer, at=NOV_1 - timedelta(minutes=1))
    # The client's occurred_at (31 October, 23:40 IST) never decides the day.
    early = await scan_and_report(
        session, b, doer, at=NOV_1 + timedelta(minutes=1), occurred_at=NOV_1 - timedelta(minutes=20)
    )
    await run_scoring(session, late.id, early.id)
    late_row = await active(session, late.id, DOER)
    early_row = await active(session, early.id, DOER)
    assert late_row is not None and early_row is not None
    assert (late_row.earned_on, late_row.period_month) == (date(2026, 10, 31), date(2026, 10, 1))
    assert (early_row.earned_on, early_row.period_month) == (date(2026, 11, 1), date(2026, 11, 1))


async def test_reporter_rows_never_count_for_prize_rank(session: AsyncSession) -> None:
    reporter, doer = await members(session, 2)
    out = await _scored(session, reporter, doer)
    rows = await ledger(session, out.id)
    assert rows and all(not r.counts_for_prize_rank for r in rows if r.role == REPORTER)


# --------------------------------------------------- withdrawal, rejection, same-act cap


async def test_withdrawal_in_window_reverses_both_entries(session: AsyncSession) -> None:
    reporter, doer = await members(session, 2)
    out = await _scored(session, reporter, doer)
    await withdraw(session, reporter, out.id)
    await run_scoring(session, out.id)

    assert await points(session, out.id, DOER) == 0
    assert await points(session, out.id, REPORTER) == 0
    reversals = [r for r in await ledger(session, out.id) if r.kind == LedgerKind.REVERSAL]
    assert sorted((r.role.value, r.points, r.deed_units) for r in reversals) == [
        ("doer", -10, -1),
        ("reporter", -2, 0),
    ]
    assert {r.reason for r in reversals} == {LedgerReason.WITHDRAWN}
    assert await active(session, out.id, DOER) is None


async def test_rejection_reverses_entries(session: AsyncSession) -> None:
    reporter, doer = await members(session, 2)
    out = await _scored(session, reporter, doer)
    # As a later M5/M7 rejection would.
    await session.execute(update(Deed).where(Deed.id == out.id).values(status=DeedStatus.REJECTED))
    emit(
        session,
        DeedRejected(
            deed_id=out.id,
            reporter_id=reporter.user_id,
            doer_id=doer.user_id,
            new_status="rejected",
            reason="test",
        ),
    )
    await session.flush()
    await run_scoring(session, out.id)
    reversals = [r for r in await ledger(session, out.id) if r.kind == LedgerKind.REVERSAL]
    assert {(r.role, r.reason) for r in reversals} == {
        (DOER, LedgerReason.REJECTED),
        (REPORTER, LedgerReason.REJECTED),
    }


async def test_same_act_cap_and_promotion(session: AsyncSession) -> None:
    doer, *witnesses = await members(session, 8)
    t0 = datetime.now(UTC) - timedelta(hours=2)
    outs = [
        await scan_and_report(session, w, doer, at=t0 + timedelta(seconds=i), east_m=i)
        for i, w in enumerate(witnesses[:7])
    ]
    await run_scoring(session, *(o.id for o in outs))

    assert [await _counts(session, o.id) for o in outs] == [True] * 5 + [False] * 2
    assert [await points(session, o.id, DOER) for o in outs] == [10] * 5 + [0] * 2
    assert [await points(session, o.id, REPORTER) for o in outs] == [2] * 5 + [1] * 2
    assert [await active(session, o.id, DOER) for o in outs[5:]] == [None, None]
    # Five verified reports of one act by different reporters: corroborated evidence.
    assert (await evaluations(session, outs[0].id))[0].evidence_level == EvidenceLevel.CORROBORATED

    # Withdrawing a counted report within the window promotes the 6th.
    await withdraw(session, witnesses[0], outs[0].id)
    await run_scoring(session, *(o.id for o in outs))
    assert await points(session, outs[0].id, DOER) == 0
    assert await points(session, outs[0].id, REPORTER) == 0
    promoted = await active(session, outs[5].id, DOER)
    assert promoted is not None and (promoted.points, promoted.reason) == (
        10,
        LedgerReason.PROMOTED,
    )
    reporter_row = await active(session, outs[5].id, REPORTER)
    assert reporter_row is not None
    assert (reporter_row.points, reporter_row.reason) == (2, LedgerReason.PROMOTED)
    assert await points(session, outs[5].id, REPORTER) == 2
    assert await points(session, outs[6].id, REPORTER) == 1


# --------------------------------------------------- daily caps (D52, Q48)


def _day_start() -> datetime:
    # 10 November 2026 00:10 IST.
    return datetime(2026, 11, 9, 18, 40, tzinfo=UTC)


async def test_doer_daily_cap_gives_the_11th_a_zero_decision_and_never_promotes(
    session: AsyncSession,
) -> None:
    doer, *reporters = await members(session, 12)
    start = _day_start()
    # Two hours apart, so each report is its own act (same_act_window_minutes = 120).
    outs = [
        await scan_and_report(session, r, doer, at=start + i * timedelta(minutes=125))
        for i, r in enumerate(reporters[:11])
    ]
    for out in outs:
        await run_scoring(session, out.id)
    assert all([await _counts(session, o.id) for o in outs])
    assert {_verified_at(o).astimezone(deeds_tz()).date() for o in outs} == {date(2026, 11, 10)}

    assert [await points(session, o.id, DOER) for o in outs] == [10] * 10 + [0]
    capped = await active(session, outs[10].id, DOER)
    assert capped is not None
    assert (capped.kind, capped.points, capped.deed_units, capped.reason) == (
        LedgerKind.DECISION,
        0,
        0,
        LedgerReason.DOER_DAILY_CAP,
    )
    # The doer cap never touches the reporter.
    assert await points(session, outs[10].id, REPORTER) == 2

    # Withdrawing one of the first ten (within its window) doesn't promote the 11th.
    await withdraw(session, reporters[3], outs[3].id, at=_verified_at(outs[3]) + timedelta(hours=1))
    await run_scoring(session, outs[3].id)
    await reconcile_deed(session, outs[10].id)
    still = await active(session, outs[10].id, DOER)
    assert still is not None and still.id == capped.id
    assert await user_points(session, doer.user_id, DOER) == 90


async def test_reporter_daily_cap(session: AsyncSession) -> None:
    reporter, *doers = await members(session, 12)
    start = _day_start()
    outs = [
        await scan_and_report(session, reporter, d, at=start + i * timedelta(minutes=5))
        for i, d in enumerate(doers[:11])
    ]
    for out in outs:
        await run_scoring(session, out.id)
    assert [await points(session, o.id, REPORTER) for o in outs] == [2] * 10 + [0]
    capped = await active(session, outs[10].id, REPORTER)
    assert capped is not None
    assert (capped.kind, capped.reason) == (LedgerKind.DECISION, LedgerReason.REPORTER_DAILY_CAP)
    # The reporter cap never touches the doer.
    assert await points(session, outs[10].id, DOER) == 10


# --------------------------------------------------- pair multipliers (Q47)


async def test_pair_schedule_per_ist_month_for_both_roles(session: AsyncSession) -> None:
    """§4.2 worked example: Priya reports Ahmed 12 times in November."""
    priya, ahmed = await members(session, 2)
    outs = [
        await _scored(session, priya, ahmed, at=NOV_1 + i * DAY + timedelta(hours=6))
        for i in range(12)
    ]
    assert [await points(session, o.id, DOER) for o in outs] == [10] * 3 + [8] * 4 + [5] * 5
    assert [await points(session, o.id, REPORTER) for o in outs] == [2] * 7 + [1] * 5
    fourth = await active(session, outs[3].id, DOER)
    assert fourth is not None
    assert (fourth.breakdown["pair_ordinal"], fourth.breakdown["pair_percent"]) == (4, 75)


async def test_pair_ordinal_resets_with_the_ist_month(session: AsyncSession) -> None:
    reporter, doer = await members(session, 2)
    october = [
        await _scored(session, reporter, doer, at=NOV_1 - (4 - i) * DAY + timedelta(hours=1))
        for i in range(4)
    ]
    assert await points(session, october[3].id, DOER) == 8
    first_november = await _scored(session, reporter, doer, at=NOV_1 + timedelta(hours=6))
    assert await points(session, first_november.id, DOER) == 10


async def test_withdrawn_earlier_reports_dont_change_later_multipliers(
    session: AsyncSession,
) -> None:
    reporter, doer = await members(session, 2)
    first = await _scored(session, reporter, doer, at=NOV_1 + timedelta(hours=6))
    await withdraw(session, reporter, first.id, at=_verified_at(first) + timedelta(hours=1))
    await run_scoring(session, first.id)
    later = [
        await _scored(session, reporter, doer, at=NOV_1 + i * DAY + timedelta(hours=6))
        for i in range(1, 4)
    ]
    # The withdrawn report still holds ordinal 1, so the 4th report of the month is at 75 %.
    assert [await points(session, o.id, DOER) for o in later] == [10, 10, 8]


# --------------------------------------------------- edits and the window (D56)


async def test_edit_inside_the_window_rescores(session: AsyncSession) -> None:
    reporter, doer = await members(session, 2)
    now = datetime.now(UTC)
    out = await _scored(session, reporter, doer, at=now - timedelta(hours=23))
    await _edit(session, reporter, out.id, now, description=SPAM)
    await run_scoring(session, out.id)

    row = await active(session, out.id, DOER)
    assert row is not None and (row.points, row.reason) == (4, LedgerReason.EDITED)
    assert row.breakdown["text_check"] == "spam_or_empty"
    reversal = next(r for r in await ledger(session, out.id) if r.kind == LedgerKind.REVERSAL)
    assert (reversal.points, reversal.reason) == (-10, LedgerReason.EDITED)
    latest = [e for e in await evaluations(session, out.id) if e.id == row.evaluation_id]
    assert latest[0].human_review_required is True
    # The reporter's points don't depend on the text.
    assert await points(session, out.id, REPORTER) == 2


async def test_category_edit_inside_the_window_reevaluates_without_changing_points(
    session: AsyncSession,
) -> None:
    reporter, doer = await members(session, 2)
    out = await _scored(session, reporter, doer)
    # "Helped a stray dog" filed under community: a mismatch, recorded only (D55).
    await _edit(session, reporter, out.id, datetime.now(UTC), category="community")
    await run_scoring(session, out.id)
    assert await points(session, out.id, DOER) == 10
    assert len(await ledger(session, out.id)) == 2  # same points: nothing re-written


async def test_edit_after_the_window_never_changes_points(session: AsyncSession) -> None:
    reporter, doer = await members(session, 2)
    now = datetime.now(UTC)
    out = await _scored(session, reporter, doer, at=now - timedelta(hours=25))
    before = await ledger(session, out.id)
    await _edit(session, reporter, out.id, now, description=SPAM)
    await run_scoring(session, out.id)
    assert len(await ledger(session, out.id)) == len(before)
    # Even a later re-score for another reason reads the text as it was at the window's end.
    assert await reconcile_deed(session, out.id) == []
    assert await points(session, out.id, DOER) == 10


async def test_people_helped_edits_never_rescore(session: AsyncSession) -> None:
    reporter, doer = await members(session, 2)
    out = await _scored(session, reporter, doer)
    _, limits = await load_limits(session)
    await _edits.edit_deed(
        session,
        reporter.user_id,
        out.id,
        DeedEdit.model_validate({"beneficiaries_count": 3}),
        limits=limits,
    )
    assert await run_scoring(session, out.id) == 1  # the handler ran and did nothing
    assert len(await ledger(session, out.id)) == 2


# --------------------------------------------------- photo evidence (D51, Q29)


async def test_photo_bonus_once_and_never_removed(session: AsyncSession) -> None:
    reporter, doer = await members(session, 2)
    out = await _scored(session, reporter, doer)
    photo = await photo_ready(session, out.id, reporter.user_id, out.reported_at)
    await run_scoring(session, out.id)

    row = await active(session, out.id, DOER)
    assert row is not None and (row.points, row.reason) == (12, LedgerReason.PHOTO_EVIDENCE)
    assert row.breakdown["photo_bonus"] == 2
    assert await points(session, out.id, REPORTER) == 2
    evidence = await session.get(ScoringEvidence, out.id, populate_existing=True)
    assert evidence is not None and evidence.first_photo_id == photo
    latest = [e for e in await evaluations(session, out.id) if e.id == row.evaluation_id]
    assert (latest[0].rule_points, latest[0].evidence_level) == (12, EvidenceLevel.PHOTO)

    # A second photo gives no second bonus.
    await photo_ready(session, out.id, doer.user_id, out.reported_at + timedelta(hours=1))
    await run_scoring(session, out.id)
    assert await points(session, out.id, DOER) == 12
    evidence = await session.get(ScoringEvidence, out.id, populate_existing=True)
    assert evidence is not None and evidence.first_photo_id == photo

    # Removal by the doer or by moderation: no scoring handler exists, nothing changes (Q29).
    for event_type in (PhotoRemovedByDoer.event_type, PhotoModerationDecided.event_type):
        assert not [n for n in handlers_for(event_type) if n.startswith(SCORING_PREFIX)]
    emit(
        session,
        PhotoRemovedByDoer(
            photo_id=photo, deed_id=out.id, uploader_id=reporter.user_id, doer_id=doer.user_id
        ),
    )
    emit(
        session,
        PhotoModerationDecided(
            photo_id=photo,
            case_id=None,
            deed_id=out.id,
            uploader_id=reporter.user_id,
            action="remove",
            removal_reason="fake",
        ),
    )
    await session.flush()
    rows = len(await ledger(session, out.id))
    assert await run_scoring(session, out.id) == 0
    assert len(await ledger(session, out.id)) == rows
    assert await reconcile_deed(session, out.id) == []
    assert await points(session, out.id, DOER) == 12


async def test_photo_attached_after_the_photo_window_is_not_evidence(
    session: AsyncSession,
) -> None:
    reporter, doer = await members(session, 2)
    out = await _scored(session, reporter, doer)
    await photo_ready(session, out.id, reporter.user_id, out.reported_at + timedelta(hours=25))
    await run_scoring(session, out.id)
    assert await session.get(ScoringEvidence, out.id) is None
    assert await points(session, out.id, DOER) == 10


async def test_photo_ready_after_the_withdrawal_window_is_recorded_but_scores_nothing(
    session: AsyncSession,
) -> None:
    reporter, doer = await members(session, 2)
    out = await _scored(session, reporter, doer, at=datetime.now(UTC) - timedelta(hours=25))
    # Attached at +23 h (inside the photo window), processed now (after the 24 h window).
    await photo_ready(session, out.id, reporter.user_id, out.reported_at + timedelta(hours=23))
    await run_scoring(session, out.id)
    evidence = await session.get(ScoringEvidence, out.id)
    assert evidence is not None and evidence.first_photo_ready_at is not None
    assert await points(session, out.id, DOER) == 10
    assert len(await ledger(session, out.id)) == 2


# --------------------------------------------------- invite deeds and prize rank


@pytest.mark.usefixtures("invites_on")
async def test_invite_deed_prize_rank_follows_the_second_report(session: AsyncSession) -> None:
    reporter, doer, witness = await members(session, 3)
    t0 = datetime.now(UTC) - timedelta(hours=6)
    created, token = await create_invite(session, reporter, at=t0, occurred_at=t0, north_m=0)
    await accept(session, token, doer, at=t0 + timedelta(minutes=5))
    await run_scoring(session, created.id)
    row = await active(session, created.id, DOER)
    assert row is not None and (row.points, row.counts_for_prize_rank) == (10, False)

    second = await scan_and_report(session, witness, doer, at=t0 + timedelta(minutes=10))
    await run_scoring(session, created.id, second.id)
    row = await active(session, created.id, DOER)
    assert row is not None
    assert (row.points, row.counts_for_prize_rank, row.reason) == (
        10,
        True,
        LedgerReason.PRIZE_RANK_CHANGED,
    )
    assert row.deed_units == 1

    await withdraw(session, witness, second.id)
    await run_scoring(session, created.id, second.id)
    row = await active(session, created.id, DOER)
    assert row is not None and row.counts_for_prize_rank is False
    assert await points(session, created.id, DOER) == 10
    # Region and dates never move across re-scores (D50, D58, D66).
    rows = await ledger(session, created.id)
    assert len({(r.region_id, r.earned_on, r.verified_at) for r in rows}) == 1


# --------------------------------------------------- idempotency and order (D49)


async def test_handlers_twice_or_out_of_order_give_the_same_ledger(session: AsyncSession) -> None:
    a, b, doer = await members(session, 3)
    now = datetime.now(UTC)
    ledgers: list[list[PointsLedger]] = []
    for reporter, reverse in ((a, False), (b, True)):
        out = await scan_and_report(session, reporter, doer, at=now - timedelta(hours=3), east_m=0)
        await photo_ready(session, out.id, reporter.user_id, out.reported_at)
        await _edit(session, reporter, out.id, now, description=SPAM)
        await run_scoring(session, out.id, reverse=reverse)
        # Again (run_handler skips them) and directly (reconcile writes nothing).
        assert await run_scoring(session, out.id) == 0
        assert await reconcile_deed(session, out.id) == []
        # A duplicated event changes nothing either.
        emit(
            session,
            DeedVerified(
                deed_id=out.id,
                reporter_id=reporter.user_id,
                doer_id=doer.user_id,
                method="qr_scan",
                verified_at=_verified_at(out),
                same_act_group_id=await _group(session, out.id),
                counts_for_points=True,
                prize_rank_eligible=True,
                limits_version=1,
            ),
        )
        await session.flush()
        before = len(await ledger(session, out.id))
        assert await run_scoring(session, out.id) == 1
        assert len(await ledger(session, out.id)) == before
        assert await points(session, out.id, DOER) == 6  # min 4 + photo 2
        assert await points(session, out.id, REPORTER) == 2
        current = [
            r
            for r in (await active(session, out.id, DOER), await active(session, out.id, REPORTER))
            if r is not None
        ]
        ledgers.append(current)
    assert shape(ledgers[0]) == shape(ledgers[1])


async def test_never_verified_deed_has_no_entries(session: AsyncSession) -> None:
    assert await reconcile_deed(session, uuid.uuid4()) == []
