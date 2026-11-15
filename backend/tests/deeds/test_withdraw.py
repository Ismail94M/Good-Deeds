"""Reporter withdrawal (docs/M2-DESIGN.md §3.2 #15, §3.4b, D13, §7 Edit and withdraw)."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.modules.deeds import _invites
from app.modules.deeds.models import (
    Deed,
    DeedStatus,
    InviteStatus,
    SameActGroup,
    VerificationEvent,
    VerificationEventKind,
)
from app.modules.deeds.schemas import DeedOut
from tests.deeds.helpers import (
    Member,
    make_member,
    members,
    outbox,
    publish_limits,
    scan,
    scan_and_report,
)
from tests.deeds.invite_helpers import accept, audits, create_invite, invite_row, withdraw


def _t0() -> datetime:
    return datetime.now(UTC) - timedelta(hours=6)


async def _deed(session: AsyncSession, deed_id: uuid.UUID) -> Deed:
    deed = await session.get(Deed, deed_id, populate_existing=True)
    assert deed is not None
    return deed


async def _group(session: AsyncSession, deed_id: uuid.UUID) -> SameActGroup:
    deed = await _deed(session, deed_id)
    group = await session.get(SameActGroup, deed.same_act_group_id, populate_existing=True)
    assert group is not None
    return group


async def _counters(session: AsyncSession, deed_id: uuid.UUID) -> tuple[int, int, int]:
    g = await _group(session, deed_id)
    return g.verified_count, g.counted_count, g.distinct_reporters


async def _counts(session: AsyncSession, deed: DeedOut) -> bool:
    return (await _deed(session, deed.id)).counts_for_points


async def _reports(
    session: AsyncSession, doer: Member, witnesses: list[Member], t0: datetime, start: int = 0
) -> list[DeedOut]:
    return [
        await scan_and_report(
            session, w, doer, at=t0 + timedelta(minutes=start + i), east_m=(start + i) * 5
        )
        for i, w in enumerate(witnesses)
    ]


# --------------------------------------------------- pending deeds


async def test_withdrawing_pending_deed_cancels_invite(
    client: AsyncClient, session: AsyncSession
) -> None:
    reporter, doer = await members(session, 2)
    created, token = await create_invite(session, reporter)
    await session.commit()
    response = await client.post(f"/api/deeds/{created.id}/withdraw", headers=reporter.headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "withdrawn" and body["withdrawn_at"] is not None
    assert body["can_edit"] is False and body["can_withdraw"] is False
    assert body["invite"]["status"] == "cancelled"
    assert (await invite_row(session, created.id)).status == InviteStatus.CANCELLED

    preview = await client.get(f"/api/invites/by-token/{token}")
    assert preview.status_code == 410 and preview.json()["code"] == "invite_cancelled"
    assert "stray dog" not in preview.text
    accepted = await client.post(f"/api/invites/by-token/{token}/accept", headers=doer.headers)
    assert accepted.status_code == 410 and accepted.json()["code"] == "invite_cancelled"

    events = (
        await session.scalars(
            select(VerificationEvent).where(VerificationEvent.deed_id == created.id)
        )
    ).all()
    assert [(e.kind, e.actor_user_id) for e in events] == [
        (VerificationEventKind.WITHDRAWN, reporter.user_id)
    ]
    withdrawn = await outbox(session, "deeds.deed_withdrawn", created.id)
    assert len(withdrawn) == 1
    assert withdrawn[0].payload["previous_status"] == "pending"
    assert withdrawn[0].payload["doer_id"] is None
    assert withdrawn[0].payload["same_act_group_id"] is None


async def test_withdrawn_invite_is_not_expired_later(session: AsyncSession) -> None:
    reporter = await make_member(session)
    at = datetime(2026, 11, 1, 6, 0, tzinfo=UTC)
    created, _ = await create_invite(session, reporter, at=at)
    await withdraw(session, reporter, created.id, at=at + timedelta(hours=1))
    await _invites.expire_invites(session, now=at + timedelta(days=30))
    assert (await _deed(session, created.id)).status == DeedStatus.WITHDRAWN
    assert await outbox(session, "deeds.deed_rejected", created.id) == []


# --------------------------------------------------- verified deeds


async def test_withdrawing_verified_deed_leaves_group(
    client: AsyncClient, session: AsyncSession
) -> None:
    doer = await make_member(session)
    a, b = await members(session, 2)
    deeds = await _reports(session, doer, [a, b], _t0())
    await session.commit()
    response = await client.post(f"/api/deeds/{deeds[0].id}/withdraw", headers=a.headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "withdrawn"
    assert body["same_act"] == {"report_count": 1, "counts_for_points": True}
    assert await _counters(session, deeds[1].id) == (1, 1, 1)
    # The withdrawn deed keeps counts_for_points for history.
    assert (await _deed(session, deeds[0].id)).counts_for_points is True

    withdrawn = await outbox(session, "deeds.deed_withdrawn", deeds[0].id)
    assert len(withdrawn) == 1
    payload = withdrawn[0].payload
    assert payload["previous_status"] == "verified"
    assert payload["doer_id"] == str(doer.user_id)
    assert payload["same_act_group_id"] == str((await _group(session, deeds[1].id)).id)


async def test_only_the_reporter_can_withdraw(client: AsyncClient, session: AsyncSession) -> None:
    reporter, doer = await members(session, 2)
    stranger = await make_member(session)
    deed = await scan_and_report(session, reporter, doer)
    await session.commit()
    for who in (doer, stranger):
        response = await client.post(f"/api/deeds/{deed.id}/withdraw", headers=who.headers)
        assert response.status_code == 404 and response.json()["code"] == "deed_not_found"
    assert (await _deed(session, deed.id)).status == DeedStatus.VERIFIED


@pytest.mark.parametrize("status", [DeedStatus.UNVERIFIED, DeedStatus.REJECTED])
async def test_closed_deeds_cannot_be_withdrawn(
    client: AsyncClient, session: AsyncSession, status: DeedStatus
) -> None:
    reporter = await make_member(session)
    created, _ = await create_invite(session, reporter)
    await session.execute(update(Deed).where(Deed.id == created.id).values(status=status))
    await session.commit()
    response = await client.post(f"/api/deeds/{created.id}/withdraw", headers=reporter.headers)
    assert response.status_code == 409 and response.json()["code"] == "deed_not_withdrawable"


async def test_withdrawing_twice_is_409(client: AsyncClient, session: AsyncSession) -> None:
    reporter, doer = await members(session, 2)
    deed = await scan_and_report(session, reporter, doer)
    await session.commit()
    url = f"/api/deeds/{deed.id}/withdraw"
    assert (await client.post(url, headers=reporter.headers)).status_code == 200
    again = await client.post(url, headers=reporter.headers)
    assert again.status_code == 409 and again.json()["code"] == "deed_not_withdrawable"
    assert len(await outbox(session, "deeds.deed_withdrawn", deed.id)) == 1


async def test_pair_cannot_report_again_the_same_day(session: AsyncSession) -> None:
    reporter, doer = await members(session, 2)
    at = _t0()
    deed = await scan_and_report(session, reporter, doer, at=at)
    await withdraw(session, reporter, deed.id, at=at + timedelta(minutes=1))
    with pytest.raises(AppError) as exc:
        await scan(session, reporter, doer, at=at + timedelta(minutes=2))
    assert exc.value.code == "already_recognized_today"


# --------------------------------------------------- promotion (D13)


async def test_withdrawing_counted_report_promotes_oldest_uncounted(
    session: AsyncSession,
) -> None:
    doer = await make_member(session)
    witnesses = await members(session, 7)
    t0 = _t0()
    deeds = await _reports(session, doer, witnesses[:5], t0)
    # Two extra witnesses, verified out of insertion order: the 7th is verified before the 6th.
    sixth = await scan_and_report(session, witnesses[5], doer, at=t0 + timedelta(minutes=20))
    seventh = await scan_and_report(session, witnesses[6], doer, at=t0 + timedelta(minutes=10))
    assert not await _counts(session, sixth) and not await _counts(session, seventh)
    assert await _counters(session, deeds[0].id) == (7, 5, 7)

    await withdraw(session, witnesses[1], deeds[1].id)
    assert await _counters(session, deeds[0].id) == (6, 5, 6)
    # Oldest by verified_at wins, not oldest inserted; at most one promotion.
    assert await _counts(session, seventh) is True
    assert await _counts(session, sixth) is False

    granted = await audits(session, seventh.id, "counts_for_points.granted")
    assert len(granted) == 1
    assert granted[0].reason == "promoted_after_withdrawal" and granted[0].actor_id is None
    changed = await outbox(session, "deeds.counts_for_points_changed", seventh.id)
    assert len(changed) == 1
    assert changed[0].payload["counts_for_points"] is True
    assert changed[0].payload["reason"] == "promoted_after_withdrawal"
    assert changed[0].payload["reporter_id"] == str(witnesses[6].user_id)
    assert await outbox(session, "deeds.counts_for_points_changed", sixth.id) == []


async def test_promotion_ties_on_verified_at_go_to_the_lower_id(session: AsyncSession) -> None:
    await publish_limits(session, same_act_cap=1)
    doer = await make_member(session)
    a, b, c = await members(session, 3)
    t0 = _t0()
    first = await scan_and_report(session, a, doer, at=t0)
    tied = [
        await scan_and_report(session, w, doer, at=t0 + timedelta(minutes=5), east_m=10)
        for w in (b, c)
    ]
    await withdraw(session, a, first.id)
    expected = min(tied, key=lambda d: d.id)
    assert [await _counts(session, d) for d in tied] == [d.id == expected.id for d in tied]


async def test_promotion_skips_reporter_already_counted(session: AsyncSession) -> None:
    await publish_limits(session, same_act_cap=2)
    doer = await make_member(session)
    r, a, b = await members(session, 3)
    # 18:00 UTC = 23:30 IST; reports after 18:30 UTC fall on the next IST day.
    t0 = datetime(2026, 11, 10, 18, 0, tzinfo=UTC)
    r1 = await scan_and_report(session, r, doer, at=t0)
    a1 = await scan_and_report(session, a, doer, at=t0 + timedelta(minutes=5), east_m=5)
    r2 = await scan_and_report(session, r, doer, at=t0 + timedelta(minutes=40), east_m=10)
    b1 = await scan_and_report(session, b, doer, at=t0 + timedelta(minutes=45), east_m=15)
    assert [await _counts(session, d) for d in (r1, a1, r2, b1)] == [True, True, False, False]

    # r2 is older, but r already has a counted report: b1 is promoted instead.
    await withdraw(session, a, a1.id)
    assert await _counts(session, r2) is False
    assert await _counts(session, b1) is True
    assert await _counters(session, r1.id) == (3, 2, 2)


async def test_reporter_whose_counted_report_is_withdrawn_can_be_promoted(
    session: AsyncSession,
) -> None:
    await publish_limits(session, same_act_cap=2)
    doer = await make_member(session)
    r, a, b = await members(session, 3)
    t0 = datetime(2026, 11, 10, 18, 0, tzinfo=UTC)
    r1 = await scan_and_report(session, r, doer, at=t0)
    await scan_and_report(session, a, doer, at=t0 + timedelta(minutes=5), east_m=5)
    r2 = await scan_and_report(session, r, doer, at=t0 + timedelta(minutes=40), east_m=10)
    b1 = await scan_and_report(session, b, doer, at=t0 + timedelta(minutes=45), east_m=15)

    await withdraw(session, r, r1.id)
    # r no longer has a counted verified report, and r2 is the oldest uncounted one.
    assert await _counts(session, r2) is True
    assert await _counts(session, b1) is False
    assert await _counters(session, r2.id) == (3, 2, 3)


async def test_withdrawn_uncounted_report_is_skipped_and_promotes_nothing(
    session: AsyncSession,
) -> None:
    await publish_limits(session, same_act_cap=2)
    doer = await make_member(session)
    a, b, c, d = await members(session, 4)
    deeds = await _reports(session, doer, [a, b, c, d], _t0())
    assert [await _counts(session, x) for x in deeds] == [True, True, False, False]

    # Withdrawing an uncounted report promotes nothing.
    await withdraw(session, c, deeds[2].id)
    assert await _counters(session, deeds[0].id) == (3, 2, 3)
    assert await _counts(session, deeds[3]) is False
    for x in deeds:
        assert await outbox(session, "deeds.counts_for_points_changed", x.id) == []

    # Withdrawing a counted one skips the withdrawn c and promotes d.
    await withdraw(session, a, deeds[0].id)
    assert await _counts(session, deeds[2]) is False
    assert await _counts(session, deeds[3]) is True
    assert await _counters(session, deeds[1].id) == (2, 2, 2)


async def test_without_uncounted_report_the_next_new_report_takes_the_slot(
    session: AsyncSession,
) -> None:
    doer = await make_member(session)
    witnesses = await members(session, 6)
    t0 = _t0()
    deeds = await _reports(session, doer, witnesses[:5], t0)
    await withdraw(session, witnesses[0], deeds[0].id)
    assert await _counters(session, deeds[1].id) == (4, 4, 4)
    for x in deeds:
        assert await outbox(session, "deeds.counts_for_points_changed", x.id) == []
    later = await scan_and_report(
        session, witnesses[5], doer, at=t0 + timedelta(minutes=30), east_m=40
    )
    assert later.same_act is not None and later.same_act.counts_for_points is True
    assert await _counters(session, deeds[1].id) == (5, 5, 5)


# --------------------------------------------------- prize rank revoke (§3.4b step 4)


async def test_withdrawing_second_report_revokes_prize_rank(session: AsyncSession) -> None:
    reporter, doer, witness = await members(session, 3)
    t0 = _t0()
    created, token = await create_invite(session, reporter, at=t0, occurred_at=t0, north_m=0)
    await accept(session, token, doer, at=t0 + timedelta(minutes=5))
    second = await scan_and_report(session, witness, doer, at=t0 + timedelta(minutes=10))
    assert (await _deed(session, created.id)).prize_rank_eligible is True

    await withdraw(session, witness, second.id)
    deed = await _deed(session, created.id)
    assert deed.prize_rank_eligible is False
    revoked = await audits(session, created.id, "prize_rank_eligible.revoked")
    assert len(revoked) == 1
    assert revoked[0].reason == "second_report_withdrawn" and revoked[0].actor_id is None
    changes = await outbox(session, "deeds.prize_rank_changed", created.id)
    assert [c.payload["prize_rank_eligible"] for c in changes] == [True, False]
    assert changes[1].payload["reason"] == "second_report_withdrawn"
    assert await _counters(session, created.id) == (1, 1, 1)


async def test_prize_rank_kept_while_two_reporters_remain(session: AsyncSession) -> None:
    reporter, doer, w1, w2 = await members(session, 4)
    t0 = _t0()
    created, token = await create_invite(session, reporter, at=t0, occurred_at=t0, north_m=0)
    await accept(session, token, doer, at=t0 + timedelta(minutes=5))
    second = await scan_and_report(session, w1, doer, at=t0 + timedelta(minutes=10))
    await scan_and_report(session, w2, doer, at=t0 + timedelta(minutes=12), east_m=10)
    await withdraw(session, w1, second.id)
    assert (await _deed(session, created.id)).prize_rank_eligible is True
    assert await audits(session, created.id, "prize_rank_eligible.revoked") == []


async def test_qr_deeds_keep_prize_rank_when_alone(session: AsyncSession) -> None:
    doer, a, b = await members(session, 3)
    t0 = _t0()
    first = await scan_and_report(session, a, doer, at=t0)
    second = await scan_and_report(session, b, doer, at=t0 + timedelta(minutes=1), east_m=5)
    await withdraw(session, b, second.id)
    # Only invite deeds depend on the second-report rule.
    assert (await _deed(session, first.id)).prize_rank_eligible is True
