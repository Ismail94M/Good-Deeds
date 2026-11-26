"""Rejecting a verified deed for a confirmed fraud case (docs/M5-DESIGN.md D89, §4.7)."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.deeds import service as deeds
from app.modules.deeds.models import (
    Deed,
    DeedStatus,
    SameActGroup,
    VerificationEvent,
    VerificationEventKind,
)
from app.modules.deeds.schemas import DeedOut
from app.modules.feed.models import PostState
from app.modules.scoring.models import LedgerKind, LedgerReason, LedgerRole
from tests.deeds.helpers import (
    Member,
    make_member,
    members,
    outbox,
    publish_limits,
    scan_and_report,
)
from tests.deeds.invite_helpers import accept, audits, create_invite, withdraw
from tests.feed.helpers import feed_events, post_of, run_feed_handlers
from tests.scoring.helpers import ledger, points, run_scoring

_REJECTED_FIELDS = ("deed_id", "reporter_id", "doer_id", "new_status", "reason")


def _t0() -> datetime:
    return datetime.now(UTC) - timedelta(hours=6)


async def _deed(session: AsyncSession, deed_id: uuid.UUID) -> Deed:
    deed = await session.get(Deed, deed_id, populate_existing=True)
    assert deed is not None
    return deed


async def _counters(session: AsyncSession, deed_id: uuid.UUID) -> tuple[int, int, int]:
    deed = await _deed(session, deed_id)
    g = await session.get(SameActGroup, deed.same_act_group_id, populate_existing=True)
    assert g is not None
    return g.verified_count, g.counted_count, g.distinct_reporters


async def _reports(
    session: AsyncSession, doer: Member, witnesses: list[Member], t0: datetime
) -> list[DeedOut]:
    return [
        await scan_and_report(session, w, doer, at=t0 + timedelta(minutes=i), east_m=i * 5)
        for i, w in enumerate(witnesses)
    ]


async def _events(session: AsyncSession, deed_id: uuid.UUID) -> list[VerificationEvent]:
    rows = await session.scalars(
        select(VerificationEvent)
        .where(
            VerificationEvent.deed_id == deed_id,
            VerificationEvent.kind == VerificationEventKind.REJECTED,
        )
        .order_by(VerificationEvent.created_at)
    )
    return list(rows)


async def test_rejects_a_verified_deed_with_event_audit_and_history(
    session: AsyncSession,
) -> None:
    doer, admin = await make_member(session), await make_member(session)
    witnesses = await members(session, 2)
    first, second = await _reports(session, doer, witnesses, _t0())
    assert await _counters(session, first.id) == (2, 2, 2)
    case_id = uuid.uuid4()

    assert await deeds.reject_for_fraud(session, first.id, case_id, admin.user_id) is True

    deed = await _deed(session, first.id)
    assert deed.status == DeedStatus.REJECTED
    assert deed.counts_for_points is True  # kept for history, like a withdrawal
    assert await _counters(session, second.id) == (1, 1, 1)

    [event] = await outbox(session, "deeds.deed_rejected", first.id)
    assert {k: event.payload[k] for k in _REJECTED_FIELDS} == {
        "deed_id": str(first.id),
        "reporter_id": str(witnesses[0].user_id),
        "doer_id": str(doer.user_id),
        "new_status": "rejected",
        "reason": "fraud_confirmed",
    }

    [verification] = await _events(session, first.id)
    assert verification.actor_user_id == admin.user_id
    assert verification.evidence["case_id"] == str(case_id)

    [audit] = await audits(session, first.id, "deed.rejected")
    assert audit.actor_id == admin.user_id
    assert audit.reason == "fraud_confirmed"
    assert audit.new_value == {"case_id": str(case_id)}
    assert audit.old_value == {"status": "verified"}

    edges = await deeds.verified_edges(
        session, since=_t0() - timedelta(days=1), until=datetime.now(UTC), user_ids={doer.user_id}
    )
    assert [e.deed_id for e in edges] == [second.id]


async def test_rejecting_an_uncounted_report_keeps_the_counted_count(
    session: AsyncSession,
) -> None:
    await publish_limits(session, same_act_cap=1)
    doer = await make_member(session)
    witnesses = await members(session, 2)
    first, second = await _reports(session, doer, witnesses, _t0())
    assert await _counters(session, first.id) == (2, 1, 2)
    assert await deeds.reject_for_fraud(session, second.id, uuid.uuid4(), None) is True
    assert await _counters(session, first.id) == (1, 1, 1)


async def test_no_promotion_of_the_next_report_in_the_act(session: AsyncSession) -> None:
    await publish_limits(session, same_act_cap=1)
    doer = await make_member(session)
    witnesses = await members(session, 2)
    first, second = await _reports(session, doer, witnesses, _t0())
    assert (await _deed(session, second.id)).counts_for_points is False

    assert await deeds.reject_for_fraud(session, first.id, uuid.uuid4(), None) is True

    # A withdrawal would promote ``second`` into the freed slot (D13); a fraud rejection doesn't.
    assert (await _deed(session, second.id)).counts_for_points is False
    assert await _counters(session, second.id) == (1, 0, 1)
    assert await outbox(session, "deeds.counts_for_points_changed", second.id) == []
    assert await audits(session, second.id, "counts_for_points.granted") == []


async def test_second_call_is_a_no_op(session: AsyncSession) -> None:
    doer, admin = await make_member(session), await make_member(session)
    witnesses = await members(session, 2)
    first, second = await _reports(session, doer, witnesses, _t0())
    case_id = uuid.uuid4()
    assert await deeds.reject_for_fraud(session, first.id, case_id, admin.user_id) is True
    assert await deeds.reject_for_fraud(session, first.id, case_id, admin.user_id) is False

    assert await _counters(session, second.id) == (1, 1, 1)
    assert len(await outbox(session, "deeds.deed_rejected", first.id)) == 1
    assert len(await _events(session, first.id)) == 1
    assert len(await audits(session, first.id, "deed.rejected")) == 1


async def test_skips_a_withdrawn_deed_and_an_unknown_one(session: AsyncSession) -> None:
    doer = await make_member(session)
    witnesses = await members(session, 2)
    first, second = await _reports(session, doer, witnesses, _t0())
    await withdraw(session, witnesses[0], first.id)
    assert await _counters(session, second.id) == (1, 1, 1)

    assert await deeds.reject_for_fraud(session, first.id, uuid.uuid4(), None) is False
    assert (await _deed(session, first.id)).status == DeedStatus.WITHDRAWN
    assert await _counters(session, second.id) == (1, 1, 1)
    assert await outbox(session, "deeds.deed_rejected", first.id) == []
    assert await _events(session, first.id) == []
    assert await deeds.reject_for_fraud(session, uuid.uuid4(), uuid.uuid4(), None) is False


async def test_rejecting_the_second_reporter_revokes_invite_prize_rank(
    session: AsyncSession,
) -> None:
    reporter, doer, witness = await members(session, 3)
    t0 = _t0()
    created, token = await create_invite(session, reporter, at=t0, occurred_at=t0, north_m=0)
    await accept(session, token, doer, at=t0 + timedelta(minutes=5))
    second = await scan_and_report(session, witness, doer, at=t0 + timedelta(minutes=10))
    assert (await _deed(session, created.id)).prize_rank_eligible is True

    assert await deeds.reject_for_fraud(session, second.id, uuid.uuid4(), None) is True

    assert (await _deed(session, created.id)).prize_rank_eligible is False
    [revoked] = await audits(session, created.id, "prize_rank_eligible.revoked")
    assert revoked.reason == "second_report_rejected" and revoked.actor_id is None
    changes = await outbox(session, "deeds.prize_rank_changed", created.id)
    assert [c.payload["prize_rank_eligible"] for c in changes] == [True, False]
    assert await _counters(session, created.id) == (1, 1, 1)


async def test_existing_handlers_remove_the_post_and_reverse_points(
    session: AsyncSession,
) -> None:
    reporter, doer = await members(session, 2)
    out = await scan_and_report(session, reporter, doer, at=_t0())
    await run_feed_handlers(session, await feed_events(session, out.id))
    await run_scoring(session, out.id)
    post = await post_of(session, out.id)
    assert post is not None and post.state == PostState.PUBLISHED
    assert await points(session, out.id, LedgerRole.DOER) > 0

    assert await deeds.reject_for_fraud(session, out.id, uuid.uuid4(), None) is True
    await run_feed_handlers(session, await feed_events(session, out.id))
    await run_scoring(session, out.id)

    post = await post_of(session, out.id)
    assert post is not None and post.state == PostState.REJECTED
    assert await points(session, out.id, LedgerRole.DOER) == 0
    assert await points(session, out.id, LedgerRole.REPORTER) == 0
    reversals = [r for r in await ledger(session, out.id) if r.kind == LedgerKind.REVERSAL]
    assert reversals and {r.reason for r in reversals} == {LedgerReason.REJECTED}
