"""The invite path: create, preview, accept, reissue, expiry and the prize-rank upgrade
(docs/M2-DESIGN.md §3.2 #3, #7, #8, #9; §3.4 step 7; §7 Invites)."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.jobs import job_app
from app.core.security import sha256_hex
from app.modules.deeds import _edits, _invites
from app.modules.deeds._limits import load_limits
from app.modules.deeds.models import (
    Deed,
    DeedStatus,
    InviteStatus,
    SameActGroup,
    VerificationEvent,
    VerificationEventKind,
)
from app.modules.deeds.schemas import DeedEdit
from app.modules.identity import service
from tests.deeds.helpers import (
    Member,
    count,
    location,
    make_member,
    members,
    outbox,
    publish_limits,
    scan_and_report,
)
from tests.deeds.invite_helpers import (
    accept,
    audits,
    create_invite,
    invite_body,
    invite_row,
    share_device,
    token_of,
)

# Invites are switched off by default (D17); these tests exercise the built invite code.
pytestmark = pytest.mark.usefixtures("invites_on")


async def _post(client: AsyncClient, who: Member, body: dict[str, Any]) -> Any:
    return await client.post("/api/deeds", json=body, headers=who.headers)


async def _deed(session: AsyncSession, deed_id: uuid.UUID | str) -> Deed:
    deed = await session.get(Deed, uuid.UUID(str(deed_id)), populate_existing=True)
    assert deed is not None
    return deed


def _t0() -> datetime:
    return datetime.now(UTC) - timedelta(hours=6)


# --------------------------------------------------- create


async def test_create_invite_returns_pending_deed_and_link(
    client: AsyncClient, session: AsyncSession
) -> None:
    reporter = await make_member(session)
    response = await _post(client, reporter, invite_body())
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "pending"
    assert body["verification_method"] == "invite_accept"
    assert body["doer"] is None
    assert body["same_act"] is None
    assert body["prize_rank_eligible"] is False
    assert body["has_location"] is False
    assert body["can_edit"] is True and body["can_withdraw"] is True
    invite = body["invite"]
    assert invite["status"] == "pending" and invite["sent_via"] == "whatsapp"
    assert invite["url"].startswith("http://localhost:5173/invite/")
    raw = invite["url"].rsplit("/", 1)[1]
    assert len(raw) >= 43

    # Stored only as a hash.
    row = await invite_row(session, uuid.UUID(body["id"]))
    assert row.token_hash == sha256_hex(raw)
    stored = await session.scalar(
        text("SELECT count(*) FROM invites WHERE row_to_json(invites)::text LIKE :raw"),
        {"raw": f"%{raw}%"},
    )
    assert stored == 0
    created = datetime.fromisoformat(invite["created_at"])
    assert datetime.fromisoformat(invite["expires_at"]) - created == timedelta(hours=168)


async def test_create_invite_emits_only_deed_submitted(
    client: AsyncClient, session: AsyncSession
) -> None:
    reporter = await make_member(session)
    deed_id = uuid.UUID((await _post(client, reporter, invite_body())).json()["id"])
    submitted = await outbox(session, "deeds.deed_submitted", deed_id)
    assert len(submitted) == 1
    payload = submitted[0].payload
    assert payload["status"] == "pending" and payload["doer_id"] is None
    assert payload["method"] == "invite_accept"
    assert "description" not in payload
    assert await outbox(session, "deeds.deed_verified", deed_id) == []


async def test_create_invite_with_location(client: AsyncClient, session: AsyncSession) -> None:
    reporter = await make_member(session)
    response = await _post(client, reporter, invite_body(location=location(north_m=300)))
    assert response.status_code == 201, response.text
    assert response.json()["has_location"] is True
    assert "12.97" not in response.text
    deed = await _deed(session, response.json()["id"])
    assert deed.location is not None and deed.location_accuracy_m == 20


async def test_invite_location_far_from_last_scan_is_accepted(
    client: AsyncClient, session: AsyncSession
) -> None:
    """No travel check on the invite path (D5): the location is where the deed happened."""
    reporter, doer = await members(session, 2)
    await scan_and_report(session, reporter, doer)
    await session.commit()
    response = await _post(client, reporter, invite_body(location=location(north_m=30_000)))
    assert response.status_code == 201, response.text


@pytest.mark.parametrize(
    ("loc", "code"),
    [
        (location(accuracy_m=501), "location_too_imprecise"),
        (location(north_m=41_000), "outside_service_area"),
    ],
)
async def test_invite_location_checks(
    client: AsyncClient, session: AsyncSession, loc: dict[str, Any], code: str
) -> None:
    reporter = await make_member(session)
    response = await _post(client, reporter, invite_body(location=loc))
    assert response.status_code == 400
    assert response.json()["code"] == code


async def test_invite_occurred_at_bounds(session: AsyncSession) -> None:
    reporter = await make_member(session)
    now = datetime(2026, 11, 10, 6, 0, tzinfo=UTC)
    week = timedelta(days=7)
    await create_invite(session, reporter, at=now, occurred_at=now - week + timedelta(seconds=1))
    await create_invite(session, reporter, at=now, occurred_at=now + timedelta(seconds=60))
    for bad in (now - week - timedelta(seconds=1), now + timedelta(seconds=61)):
        with pytest.raises(AppError) as exc:
            await create_invite(session, reporter, at=now, occurred_at=bad)
        assert exc.value.code == "implausible_time"


async def test_invite_occurred_at_is_required(client: AsyncClient, session: AsyncSession) -> None:
    reporter = await make_member(session)
    body = invite_body()
    del body["occurred_at"]
    assert (await _post(client, reporter, body)).status_code == 422


async def test_invite_content_is_validated(client: AsyncClient, session: AsyncSession) -> None:
    reporter = await make_member(session)
    response = await _post(client, reporter, invite_body(category="nope"))
    assert response.status_code == 400 and response.json()["code"] == "invalid_category"
    response = await _post(client, reporter, invite_body(sent_via="carrier_pigeon"))
    assert response.status_code == 422


# --------------------------------------------------- daily limit


async def test_five_invites_a_day_then_429(client: AsyncClient, session: AsyncSession) -> None:
    reporter = await make_member(session)
    for _ in range(5):
        assert (await _post(client, reporter, invite_body())).status_code == 201
    response = await _post(client, reporter, invite_body())
    assert response.status_code == 429
    assert response.json()["code"] == "invite_daily_limit"


async def test_expired_and_accepted_invites_still_count(session: AsyncSession) -> None:
    reporter, doer = await members(session, 2)
    day = datetime(2026, 11, 10, 4, 0, tzinfo=UTC)  # 09:30 IST
    _, token = await create_invite(session, reporter, at=day)
    await accept(session, token, doer, at=day + timedelta(minutes=5))
    second, _ = await create_invite(session, reporter, at=day + timedelta(minutes=10))
    row = await invite_row(session, second.id)
    row.status = InviteStatus.EXPIRED
    await session.flush()
    for i in range(3):
        await create_invite(session, reporter, at=day + timedelta(minutes=20 + i))
    with pytest.raises(AppError) as exc:
        await create_invite(session, reporter, at=day + timedelta(hours=1))
    assert exc.value.code == "invite_daily_limit"
    assert exc.value.status_code == 429


async def test_daily_limit_resets_on_the_ist_day(session: AsyncSession) -> None:
    reporter = await make_member(session)
    # 18:29 UTC is 23:59 IST on the 10th; 18:31 UTC is 00:01 IST on the 11th.
    before = datetime(2026, 11, 10, 18, 29, tzinfo=UTC)
    for _ in range(5):
        await create_invite(session, reporter, at=before)
    with pytest.raises(AppError):
        await create_invite(session, reporter, at=before)
    await create_invite(session, reporter, at=datetime(2026, 11, 10, 18, 31, tzinfo=UTC))


async def test_daily_limit_comes_from_config(session: AsyncSession) -> None:
    await publish_limits(session, invites_per_day=2)
    reporter = await make_member(session)
    now = datetime(2026, 11, 10, 6, 0, tzinfo=UTC)
    await create_invite(session, reporter, at=now)
    await create_invite(session, reporter, at=now)
    with pytest.raises(AppError) as exc:
        await create_invite(session, reporter, at=now)
    assert exc.value.code == "invite_daily_limit"


# --------------------------------------------------- preview


async def test_preview_of_pending_invite(client: AsyncClient, session: AsyncSession) -> None:
    reporter = await make_member(session)
    created = (await _post(client, reporter, invite_body(location=location()))).json()
    raw = created["invite"]["url"].rsplit("/", 1)[1]
    response = await client.get(f"/api/invites/by-token/{raw}")
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    body = response.json()
    assert body == {
        "status": "pending",
        "reporter": {"handle": reporter.handle, "display_name": reporter.display_name},
        "category": "animals",
        "description": "Helped a stray dog across the road",
        "occurred_at": body["occurred_at"],
        "expires_at": created["invite"]["expires_at"],
    }
    assert "12.97" not in response.text


async def _gone(client: AsyncClient, raw: str, code: str) -> None:
    response = await client.get(f"/api/invites/by-token/{raw}")
    assert response.status_code == 410, response.text
    assert response.json() == {"detail": response.json()["detail"], "code": code}
    assert "stray dog" not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"


async def test_preview_unknown_token_is_404(client: AsyncClient) -> None:
    response = await client.get("/api/invites/by-token/not-a-real-token")
    assert response.status_code == 404
    assert response.json()["code"] == "invite_invalid"
    assert response.headers["cache-control"] == "no-store"
    long = await client.get("/api/invites/by-token/" + "x" * 500)
    assert long.status_code == 404


async def test_preview_of_expired_invite_is_410(client: AsyncClient, session: AsyncSession) -> None:
    reporter = await make_member(session)
    # Past expiry but before the job ran ...
    _, token = await create_invite(session, reporter, at=datetime.now(UTC) - timedelta(days=8))
    await session.commit()
    await _gone(client, token, "invite_expired")
    # ... and after it ran.
    await _invites.expire_invites(session)
    await session.commit()
    await _gone(client, token, "invite_expired")


async def test_preview_of_used_invite_is_410(client: AsyncClient, session: AsyncSession) -> None:
    reporter, doer = await members(session, 2)
    _, token = await create_invite(session, reporter)
    await accept(session, token, doer)
    await session.commit()
    await _gone(client, token, "invite_used")


async def test_preview_is_rate_limited_per_ip(
    client: AsyncClient, other_ip: AsyncClient, session: AsyncSession
) -> None:
    await publish_limits(session, rate_invite_preview_ip="2/900")
    for _ in range(2):
        assert (await client.get("/api/invites/by-token/abc")).status_code == 404
    limited = await client.get("/api/invites/by-token/abc")
    assert limited.status_code == 429 and limited.json()["code"] == "rate_limited"
    assert limited.headers["cache-control"] == "no-store"
    assert (await other_ip.get("/api/invites/by-token/abc")).status_code == 404


# --------------------------------------------------- accept


async def test_accept_verifies_and_groups_the_deed(
    client: AsyncClient, session: AsyncSession
) -> None:
    reporter, doer = await members(session, 2)
    created, token = await create_invite(session, reporter, north_m=0)
    await session.commit()
    response = await client.post(f"/api/invites/by-token/{token}/accept", headers=doer.headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "verified"
    assert body["doer"] == {"handle": doer.handle, "display_name": doer.display_name}
    assert body["prize_rank_eligible"] is False
    assert body["same_act"] == {"report_count": 1, "counts_for_points": True}
    # The doer isn't the reporter: no edit, no invite details.
    assert body["can_edit"] is False and body["invite"] is None
    assert response.headers["cache-control"] == "no-store"

    deed = await _deed(session, created.id)
    assert deed.doer_user_id == doer.user_id and deed.verified_at is not None
    assert deed.same_act_group_id is not None and deed.counts_for_points is True
    invite = await invite_row(session, created.id)
    assert invite.status == InviteStatus.ACCEPTED
    assert invite.accepted_by == doer.user_id and invite.accepted_at is not None
    assert invite.accepted_phone_hash == doer.phone_hash
    assert invite.shared_device is False

    events = (
        await session.scalars(select(VerificationEvent).where(VerificationEvent.deed_id == deed.id))
    ).all()
    assert [e.kind for e in events] == [VerificationEventKind.INVITE_ACCEPTED]
    assert events[0].actor_user_id == doer.user_id
    assert set(events[0].evidence) == {"shared_device", "has_location", "hours_to_accept"}

    verified = await outbox(session, "deeds.deed_verified", deed.id)
    assert len(verified) == 1
    assert verified[0].payload["prize_rank_eligible"] is False
    assert verified[0].payload["method"] == "invite_accept"
    assert verified[0].payload["doer_id"] == str(doer.user_id)


async def test_existing_member_with_history_can_accept(session: AsyncSession) -> None:
    """Q5/D9: an established member (who already has verified deeds) can accept."""
    reporter, doer, witness = await members(session, 3)
    await scan_and_report(session, witness, doer, at=_t0())
    _, token = await create_invite(session, reporter)
    out = await accept(session, token, doer)
    assert out.status == "verified"


async def test_invite_without_location_gets_own_group(session: AsyncSession) -> None:
    reporter, doer = await members(session, 2)
    created, token = await create_invite(session, reporter)
    await accept(session, token, doer)
    deed = await _deed(session, created.id)
    group = await session.get(SameActGroup, deed.same_act_group_id)
    assert group is not None and group.centre is None


async def test_reporter_cannot_accept_own_invite(
    client: AsyncClient, session: AsyncSession
) -> None:
    reporter = await make_member(session)
    _, token = await create_invite(session, reporter)
    await session.commit()
    response = await client.post(f"/api/invites/by-token/{token}/accept", headers=reporter.headers)
    assert response.status_code == 403
    assert response.json()["code"] == "cannot_accept_own_invite"


async def test_accounts_sharing_a_number_cannot_accept(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    reporter, doer = await members(session, 2)
    _, token = await create_invite(session, reporter)

    async def same_number(_: AsyncSession, user_id: uuid.UUID) -> set[str]:
        return {"shared-number-hash"}

    monkeypatch.setattr(service, "phone_hashes", same_number)
    with pytest.raises(AppError) as exc:
        await accept(session, token, doer)
    assert exc.value.code == "same_phone" and exc.value.status_code == 403


async def test_non_onboarded_accepter_gets_403(client: AsyncClient, session: AsyncSession) -> None:
    reporter = await make_member(session)
    newcomer = await make_member(session, onboarded=False)
    created, token = await create_invite(session, reporter)
    await session.commit()
    response = await client.post(f"/api/invites/by-token/{token}/accept", headers=newcomer.headers)
    assert response.status_code == 403
    assert (await invite_row(session, created.id)).status == InviteStatus.PENDING


async def test_accepting_twice_is_410(client: AsyncClient, session: AsyncSession) -> None:
    reporter, doer = await members(session, 2)
    _, token = await create_invite(session, reporter)
    await session.commit()
    url = f"/api/invites/by-token/{token}/accept"
    assert (await client.post(url, headers=doer.headers)).status_code == 200
    again = await client.post(url, headers=doer.headers)
    assert again.status_code == 410 and again.json()["code"] == "invite_used"


async def test_accept_after_expiry_before_job_is_410(session: AsyncSession) -> None:
    reporter, doer = await members(session, 2)
    at = datetime(2026, 11, 1, 6, 0, tzinfo=UTC)
    created, token = await create_invite(session, reporter, at=at)
    expires = at + timedelta(hours=168)
    with pytest.raises(AppError) as exc:
        await accept(session, token, doer, at=expires)
    assert exc.value.code == "invite_expired" and exc.value.status_code == 410
    # One second before expiry still works.
    out = await accept(session, token, doer, at=expires - timedelta(seconds=1))
    assert out.id == created.id


async def test_unknown_token_accept_is_404(client: AsyncClient, session: AsyncSession) -> None:
    doer = await make_member(session)
    response = await client.post("/api/invites/by-token/nope/accept", headers=doer.headers)
    assert response.status_code == 404 and response.json()["code"] == "invite_invalid"


async def test_pair_with_deed_on_same_report_day_gets_409(session: AsyncSession) -> None:
    reporter, doer = await members(session, 2)
    at = datetime(2026, 11, 10, 6, 0, tzinfo=UTC)
    _, token = await create_invite(session, reporter, at=at)
    await scan_and_report(session, reporter, doer, at=at + timedelta(minutes=5))
    with pytest.raises(AppError) as exc:
        await accept(session, token, doer, at=at + timedelta(minutes=10))
    assert exc.value.code == "already_recognized_today" and exc.value.status_code == 409


# --------------------------------------------------- shared device (D6)


async def _accept_on_day(
    session: AsyncSession, reporter: Member, doer: Member, day: datetime
) -> None:
    _, token = await create_invite(session, reporter, at=day)
    await accept(session, token, doer, at=day + timedelta(minutes=1))


async def test_shared_device_allowed_fifteen_times_a_month(session: AsyncSession) -> None:
    # Isolate D6 from the reporter's own monthly limit (D16, 15), which would refuse the 16th
    # invite before it could be accepted.
    await publish_limits(session, reporter_monthly_limit=100)
    reporter, doer, other_reporter = await members(session, 3)
    await share_device(session, reporter, doer, other_reporter)
    oct_1 = datetime(2026, 10, 1, 6, 0, tzinfo=UTC)
    for i in range(15):
        await _accept_on_day(session, reporter, doer, oct_1 + timedelta(days=i))
    shared = await session.scalar(
        text("SELECT count(*) FROM invites WHERE reporter_user_id = :r AND shared_device"),
        {"r": reporter.user_id},
    )
    assert shared == 15

    # The 16th in October (on the 28th) is refused ...
    oct_28 = oct_1 + timedelta(days=27)
    _, token = await create_invite(session, reporter, at=oct_28)
    with pytest.raises(AppError) as exc:
        await accept(session, token, doer, at=oct_28 + timedelta(minutes=1))
    assert exc.value.code == "same_device_limit" and exc.value.status_code == 403
    # ... a different pair on the same device is counted separately ...
    await _accept_on_day(session, other_reporter, doer, oct_28)
    # ... and the next IST month is allowed again (00:01 IST on 1 November).
    nov_1 = datetime(2026, 10, 31, 18, 31, tzinfo=UTC)
    out = await accept(session, token, doer, at=nov_1)
    assert out.status == "verified"


async def test_shared_device_from_request_header_counts(session: AsyncSession) -> None:
    """The accepting device (X-Device-Id) counts as one of the accepter's devices."""
    await publish_limits(session, shared_device_pair_monthly_limit=0)
    reporter, doer = await members(session, 2)
    await share_device(session, reporter, device="reporter-phone")
    _, token = await create_invite(session, reporter)
    with pytest.raises(AppError) as exc:
        await accept(session, token, doer, device_id="reporter-phone")
    assert exc.value.code == "same_device_limit"
    out = await accept(session, token, doer, device_id="doer-phone")
    assert out.status == "verified"


async def test_no_shared_device_means_no_monthly_limit(session: AsyncSession) -> None:
    await publish_limits(session, shared_device_pair_monthly_limit=0)
    reporter, doer = await members(session, 2)
    await share_device(session, reporter, device="a")
    await share_device(session, doer, device="b")
    _, token = await create_invite(session, reporter)
    await accept(session, token, doer)
    assert (
        await invite_row(session, (await _only_deed(session, reporter)).id)
    ).shared_device is False


async def _only_deed(session: AsyncSession, reporter: Member) -> Deed:
    return (
        await session.scalars(select(Deed).where(Deed.reporter_user_id == reporter.user_id))
    ).one()


# --------------------------------------------------- prize rank (§3.4 step 7)


async def test_second_independent_report_grants_prize_rank(session: AsyncSession) -> None:
    reporter, doer, witness = await members(session, 3)
    t0 = _t0()
    created, token = await create_invite(session, reporter, at=t0, occurred_at=t0, north_m=0)
    accepted = await accept(session, token, doer, at=t0 + timedelta(minutes=5))
    assert accepted.prize_rank_eligible is False

    await scan_and_report(session, witness, doer, at=t0 + timedelta(minutes=30), north_m=50)
    deed = await _deed(session, created.id)
    assert deed.prize_rank_eligible is True
    granted = await audits(session, deed.id, "prize_rank_eligible.granted")
    assert len(granted) == 1
    assert granted[0].reason == "second_independent_report" and granted[0].actor_id is None
    assert granted[0].old_value == {"prize_rank_eligible": False}
    changed = await outbox(session, "deeds.prize_rank_changed", deed.id)
    assert len(changed) == 1
    assert changed[0].payload["prize_rank_eligible"] is True
    assert changed[0].payload["reason"] == "second_independent_report"
    # Further reports don't flip it again.
    third = await make_member(session)
    await scan_and_report(session, third, doer, at=t0 + timedelta(minutes=40), north_m=60)
    assert len(await outbox(session, "deeds.prize_rank_changed", deed.id)) == 1


async def test_accepting_into_a_group_with_another_reporter_grants_at_once(
    session: AsyncSession,
) -> None:
    reporter, doer, witness = await members(session, 3)
    t0 = _t0()
    await scan_and_report(session, witness, doer, at=t0)
    created, token = await create_invite(
        session, reporter, at=t0 + timedelta(minutes=5), occurred_at=t0, north_m=20
    )
    out = await accept(session, token, doer, at=t0 + timedelta(minutes=10))
    assert out.prize_rank_eligible is True
    verified = await outbox(session, "deeds.deed_verified", created.id)
    assert verified[0].payload["prize_rank_eligible"] is False
    assert len(await audits(session, created.id, "prize_rank_eligible.granted")) == 1


async def test_second_report_by_the_same_reporter_does_not_grant(session: AsyncSession) -> None:
    reporter, doer = await members(session, 2)
    # 17:30 UTC = 23:00 IST on the 10th; the QR report is at 00:30 IST on the 11th.
    t0 = datetime(2026, 11, 10, 17, 30, tzinfo=UTC)
    created, token = await create_invite(session, reporter, at=t0, occurred_at=t0, north_m=0)
    await accept(session, token, doer, at=t0 + timedelta(minutes=5))
    second = await scan_and_report(session, reporter, doer, at=t0 + timedelta(minutes=90))
    deed = await _deed(session, created.id)
    assert second.same_act is not None and deed.same_act_group_id is not None
    group = await session.get(SameActGroup, deed.same_act_group_id, populate_existing=True)
    assert group is not None and group.verified_count == 2 and group.distinct_reporters == 1
    assert deed.prize_rank_eligible is False
    assert await audits(session, deed.id, "prize_rank_eligible.granted") == []


async def test_invite_without_location_never_gets_prize_rank(session: AsyncSession) -> None:
    reporter, doer, witness = await members(session, 3)
    t0 = _t0()
    created, token = await create_invite(session, reporter, at=t0, occurred_at=t0)
    await accept(session, token, doer, at=t0 + timedelta(minutes=5))
    await scan_and_report(session, witness, doer, at=t0 + timedelta(minutes=10))
    assert (await _deed(session, created.id)).prize_rank_eligible is False


# --------------------------------------------------- reissue


async def test_reissue_rotates_the_token(client: AsyncClient, session: AsyncSession) -> None:
    reporter = await make_member(session)
    created = (await _post(client, reporter, invite_body())).json()
    old = created["invite"]["url"].rsplit("/", 1)[1]
    response = await client.post(
        f"/api/invites/{created['invite']['id']}/reissue", headers=reporter.headers
    )
    assert response.status_code == 200, response.text
    body = response.json()
    new = body["url"].rsplit("/", 1)[1]
    assert new != old
    assert body["expires_at"] == created["invite"]["expires_at"]
    assert (await client.get(f"/api/invites/by-token/{old}")).status_code == 404
    assert (await client.get(f"/api/invites/by-token/{new}")).status_code == 200
    row = await invite_row(session, uuid.UUID(created["id"]))
    assert row.reissued_at is not None and row.token_hash == sha256_hex(new)
    # The daily count is unchanged: one invite today.
    assert await count(session, type(row), type(row).reporter_user_id == reporter.user_id) == 1


async def test_reissue_by_someone_else_is_404(client: AsyncClient, session: AsyncSession) -> None:
    reporter, stranger = await members(session, 2)
    created = (await _post(client, reporter, invite_body())).json()
    url = f"/api/invites/{created['invite']['id']}/reissue"
    response = await client.post(url, headers=stranger.headers)
    assert response.status_code == 404 and response.json()["code"] == "invite_not_found"
    missing = await client.post(f"/api/invites/{uuid.uuid4()}/reissue", headers=reporter.headers)
    assert missing.status_code == 404


async def test_reissue_of_non_pending_invite_is_409(
    client: AsyncClient, session: AsyncSession
) -> None:
    reporter, doer = await members(session, 2)
    created, token = await create_invite(session, reporter)
    await accept(session, token, doer)
    await session.commit()
    assert created.invite is not None
    response = await client.post(
        f"/api/invites/{created.invite.id}/reissue", headers=reporter.headers
    )
    assert response.status_code == 409 and response.json()["code"] == "invite_not_pending"


# --------------------------------------------------- expiry job


async def test_expiry_job_marks_deed_unverified(session: AsyncSession) -> None:
    reporter = await make_member(session)
    at = datetime(2026, 11, 1, 6, 0, tzinfo=UTC)
    created, _ = await create_invite(session, reporter, at=at)
    expires = at + timedelta(hours=168)
    # Not yet due.
    await _invites.expire_invites(session, now=expires - timedelta(seconds=1))
    assert (await _deed(session, created.id)).status == DeedStatus.PENDING

    assert await _invites.expire_invites(session, now=expires) >= 1
    deed = await _deed(session, created.id)
    assert deed.status == DeedStatus.UNVERIFIED
    assert (await invite_row(session, created.id)).status == InviteStatus.EXPIRED
    events = (
        await session.scalars(select(VerificationEvent).where(VerificationEvent.deed_id == deed.id))
    ).all()
    assert [e.kind for e in events] == [VerificationEventKind.INVITE_EXPIRED]
    assert events[0].actor_user_id is None
    rejected = await outbox(session, "deeds.deed_rejected", deed.id)
    assert len(rejected) == 1
    assert rejected[0].payload["new_status"] == "unverified"
    assert rejected[0].payload["reason"] == "invite_expired"

    # Idempotent on re-run.
    await _invites.expire_invites(session, now=expires + timedelta(hours=1))
    assert len(await outbox(session, "deeds.deed_rejected", deed.id)) == 1
    assert await count(session, VerificationEvent, VerificationEvent.deed_id == deed.id) == 1


async def test_expiry_job_skips_accepted_invites(session: AsyncSession) -> None:
    reporter, doer = await members(session, 2)
    at = datetime(2026, 11, 1, 6, 0, tzinfo=UTC)
    created, token = await create_invite(session, reporter, at=at)
    await accept(session, token, doer, at=at + timedelta(hours=1))
    await _invites.expire_invites(session, now=at + timedelta(days=30))
    assert (await _deed(session, created.id)).status == DeedStatus.VERIFIED
    assert (await invite_row(session, created.id)).status == InviteStatus.ACCEPTED
    assert await outbox(session, "deeds.deed_rejected", created.id) == []


async def test_expiry_job_works_in_batches(session: AsyncSession) -> None:
    reporter = await make_member(session)
    at = datetime(2001, 1, 1, 6, 0, tzinfo=UTC)  # older than anything else in the database
    first, _ = await create_invite(session, reporter, at=at)
    second, _ = await create_invite(session, reporter, at=at + timedelta(minutes=1))
    due = at + timedelta(days=7, minutes=2)
    assert await _invites.expire_invites(session, now=due, batch_size=1) == 1
    assert (await _deed(session, first.id)).status == DeedStatus.UNVERIFIED
    assert (await _deed(session, second.id)).status == DeedStatus.PENDING
    assert await _invites.expire_invites(session, now=due, batch_size=1) == 1
    assert (await _deed(session, second.id)).status == DeedStatus.UNVERIFIED


async def test_expiry_job_is_registered_every_fifteen_minutes() -> None:
    assert "deeds.expire_invites" in job_app.tasks
    periodic = {key[0]: task.cron for key, task in job_app.periodic_registry.periodic_tasks.items()}
    assert periodic["deeds.expire_invites"] == "*/15 * * * *"


# --------------------------------------------------- pending deeds never show the doer


async def test_pending_deed_never_exposes_a_doer(session: AsyncSession) -> None:
    reporter = await make_member(session)
    created, _ = await create_invite(session, reporter)
    assert created.doer is None
    assert token_of(created)
    # Still null after an edit (the reporter's own view of the pending deed).
    _, limits = await load_limits(session)
    edited = await _edits.edit_deed(
        session, reporter.user_id, created.id, DeedEdit(category="community"), limits=limits
    )
    assert edited.doer is None and edited.status == "pending"


# --------------------------------------------------- rate limits (§3.1, D1)


async def test_invite_create_ip_limit(
    client: AsyncClient, other_ip: AsyncClient, session: AsyncSession
) -> None:
    await publish_limits(session, rate_invite_create_ip="1/86400")
    a, b = await members(session, 2)
    assert (await _post(client, a, invite_body())).status_code == 201
    limited = await _post(client, b, invite_body())
    assert limited.status_code == 429 and limited.json()["code"] == "rate_limited"
    assert (
        await other_ip.post("/api/deeds", json=invite_body(), headers=b.headers)
    ).status_code == 201


async def test_accept_account_and_ip_limits(
    client: AsyncClient, other_ip: AsyncClient, session: AsyncSession
) -> None:
    await publish_limits(session, rate_invite_accept_user="1/3600", rate_invite_accept_ip="2/3600")
    a, b, c = await members(session, 3)
    url = "/api/invites/by-token/nope/accept"
    assert (await client.post(url, headers=a.headers)).status_code == 404
    # Account key: a is stopped on another IP too.
    assert (await other_ip.post(url, headers=a.headers)).status_code == 429
    # IP key: many accounts on one IP.
    assert (await client.post(url, headers=b.headers)).status_code == 404
    assert (await client.post(url, headers=c.headers)).status_code == 429


async def test_reissue_account_and_ip_limits(
    client: AsyncClient, other_ip: AsyncClient, session: AsyncSession
) -> None:
    await publish_limits(
        session, rate_invite_reissue_user="1/86400", rate_invite_reissue_ip="2/86400"
    )
    a, b, c = await members(session, 3)
    url = f"/api/invites/{uuid.uuid4()}/reissue"
    assert (await client.post(url, headers=a.headers)).status_code == 404
    assert (await other_ip.post(url, headers=a.headers)).status_code == 429
    assert (await client.post(url, headers=b.headers)).status_code == 404
    assert (await client.post(url, headers=c.headers)).status_code == 429
