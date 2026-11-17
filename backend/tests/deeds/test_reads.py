"""Read endpoints and the public views (docs/M2-DESIGN.md §3.2 #4, #5, #6, #10, §3.3, §4).

Invites are inserted directly here: creating them is B6's job, reading them is B8's.
"""

import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import sha256_hex
from app.modules.deeds import service
from app.modules.deeds._common import ist_day, ist_month
from app.modules.deeds.models import (
    Deed,
    DeedStatus,
    Invite,
    InviteSentVia,
    InviteStatus,
    VerificationMethod,
)
from tests.deeds.helpers import Member, make_member, members, publish_limits, scan_and_report


async def _pending_deed(
    session: AsyncSession,
    reporter: Member,
    *,
    invite_status: InviteStatus = InviteStatus.PENDING,
    at: datetime | None = None,
    accepted_by: Member | None = None,
) -> tuple[Deed, Invite]:
    """A pending invite deed (no doer) and its invite, inserted directly."""
    at = at or datetime.now(UTC)
    deed = Deed(
        reporter_user_id=reporter.user_id,
        category="animals",
        description="Rescued a kitten from a drain",
        occurred_at=at - timedelta(hours=1),
        reported_at=at,
        report_day=ist_day(at),
        status=DeedStatus.PENDING
        if invite_status == InviteStatus.PENDING
        else DeedStatus.UNVERIFIED,
        verification_method=VerificationMethod.INVITE_ACCEPT,
        limits_version=1,
    )
    session.add(deed)
    await session.flush()
    accepted = invite_status == InviteStatus.ACCEPTED and accepted_by is not None
    invite = Invite(
        deed_id=deed.id,
        reporter_user_id=reporter.user_id,
        token_hash=sha256_hex(secrets.token_urlsafe(16)),
        sent_via=InviteSentVia.WHATSAPP,
        status=invite_status,
        created_at=at,
        created_day=ist_day(at),
        expires_at=at + timedelta(days=7),
        accepted_by=accepted_by.user_id if accepted and accepted_by else None,
        accepted_at=at if accepted else None,
    )
    session.add(invite)
    await session.flush()
    return deed, invite


def _no_coordinates(text: str) -> None:
    assert "12.97" not in text and "77.59" not in text
    assert '"location"' not in text and '"lat"' not in text


# --------------------------------------------------- GET /api/deeds/{id}


async def test_reporter_and_doer_can_read_a_deed(
    client: AsyncClient, session: AsyncSession
) -> None:
    witness, doer, stranger = await members(session, 3)
    deed = await scan_and_report(session, witness, doer)
    await session.commit()

    as_reporter = await client.get(f"/api/deeds/{deed.id}", headers=witness.headers)
    assert as_reporter.status_code == 200, as_reporter.text
    body = as_reporter.json()
    assert body["doer"] == {"handle": doer.handle, "display_name": doer.display_name}
    assert body["can_edit"] is True and body["can_withdraw"] is True
    assert body["same_act"] == {"report_count": 1, "counts_for_points": True}
    _no_coordinates(as_reporter.text)

    as_doer = await client.get(f"/api/deeds/{deed.id}", headers=doer.headers)
    assert as_doer.status_code == 200
    assert as_doer.json()["can_edit"] is False and as_doer.json()["can_withdraw"] is False
    _no_coordinates(as_doer.text)

    # Anyone else: 404, exactly like an unknown ID.
    for headers, deed_id in ((stranger.headers, deed.id), (witness.headers, uuid.uuid4())):
        response = await client.get(f"/api/deeds/{deed_id}", headers=headers)
        assert response.status_code == 404
        assert response.json() == {
            "detail": "We couldn't find that deed.",
            "code": "deed_not_found",
        }
    assert (await client.get(f"/api/deeds/{deed.id}")).status_code == 401


async def test_pending_deed_never_shows_a_doer(client: AsyncClient, session: AsyncSession) -> None:
    reporter, stranger = await members(session, 2)
    deed, invite = await _pending_deed(session, reporter)
    await session.commit()

    response = await client.get(f"/api/deeds/{deed.id}", headers=reporter.headers)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "pending" and body["doer"] is None
    assert body["same_act"] is None and body["has_location"] is False
    # The reporter sees the invite, but never its link again.
    assert body["invite"]["id"] == str(invite.id)
    assert body["invite"]["url"] is None
    assert (await client.get(f"/api/deeds/{deed.id}", headers=stranger.headers)).status_code == 404


async def test_doer_does_not_see_the_invite(client: AsyncClient, session: AsyncSession) -> None:
    reporter, doer = await members(session, 2)
    deed = await scan_and_report(session, reporter, doer)
    session.add(
        Invite(
            deed_id=deed.id,
            reporter_user_id=reporter.user_id,
            token_hash=sha256_hex(secrets.token_urlsafe(16)),
            sent_via=InviteSentVia.OTHER,
            status=InviteStatus.ACCEPTED,
            created_day=ist_day(datetime.now(UTC)),
            expires_at=datetime.now(UTC) + timedelta(days=7),
            accepted_by=doer.user_id,
            accepted_at=datetime.now(UTC),
        )
    )
    await session.commit()
    as_doer = (await client.get(f"/api/deeds/{deed.id}", headers=doer.headers)).json()
    assert as_doer["invite"] is None
    as_reporter = (await client.get(f"/api/deeds/{deed.id}", headers=reporter.headers)).json()
    assert as_reporter["invite"]["status"] == "accepted"


# --------------------------------------------------- GET /api/me/deeds


async def _all_pages(client: AsyncClient, member: Member, **params: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        query = params | ({"cursor": cursor} if cursor else {})
        response = await client.get("/api/me/deeds", params=query, headers=member.headers)
        assert response.status_code == 200, response.text
        page = response.json()
        items.extend(page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            return items


async def test_my_deeds_by_role_with_keyset_pages(
    client: AsyncClient, session: AsyncSession
) -> None:
    me = await make_member(session)
    others = await members(session, 3)
    base = datetime.now(UTC) - timedelta(hours=1)
    reported = []
    for i, other in enumerate(others):
        reported.append(await scan_and_report(session, me, other, at=base + timedelta(minutes=i)))
    pending, _ = await _pending_deed(session, me, at=base + timedelta(minutes=10))
    about_me = [
        await scan_and_report(session, other, me, at=base + timedelta(minutes=20 + i))
        for i, other in enumerate(others[:2])
    ]
    await session.commit()

    as_reporter = await _all_pages(client, me, role="reporter", limit=2)
    expected = [str(pending.id)] + [str(d.id) for d in reversed(reported)]
    assert [d["id"] for d in as_reporter] == expected
    assert as_reporter[0]["doer"] is None and as_reporter[0]["invite"] is not None
    _no_coordinates(str(as_reporter))

    as_doer = await _all_pages(client, me, role="doer", limit=1)
    assert [d["id"] for d in as_doer] == [str(d.id) for d in reversed(about_me)]
    assert all(d["invite"] is None and not d["can_edit"] for d in as_doer)

    # The default role is reporter.
    first = (await client.get("/api/me/deeds", headers=me.headers)).json()
    assert [d["id"] for d in first["items"]] == expected
    assert first["next_cursor"] is None


async def test_my_deeds_rejects_bad_params(client: AsyncClient, session: AsyncSession) -> None:
    me = await make_member(session)
    for params, status in (
        ({"role": "admin"}, 422),
        ({"limit": 0}, 422),
        ({"limit": 51}, 422),
        ({"cursor": "bm90LWEtY3Vyc29y"}, 400),
    ):
        response = await client.get("/api/me/deeds", params=params, headers=me.headers)
        assert response.status_code == status, params
    assert (await client.get("/api/me/deeds")).status_code == 401


# --------------------------------------------------- GET /api/me/invites


async def test_my_invites_filter_and_pages(client: AsyncClient, session: AsyncSession) -> None:
    me, other, accepter = await members(session, 3)
    base = datetime.now(UTC) - timedelta(days=2)
    pending = [
        (await _pending_deed(session, me, at=base + timedelta(minutes=i)))[1] for i in range(3)
    ]
    _, expired = await _pending_deed(
        session, me, invite_status=InviteStatus.EXPIRED, at=base + timedelta(minutes=10)
    )
    await _pending_deed(session, other)
    await session.commit()

    response = await client.get(
        "/api/me/invites", params={"status": "pending", "limit": 2}, headers=me.headers
    )
    page = response.json()
    assert [i["id"] for i in page["items"]] == [str(pending[2].id), str(pending[1].id)]
    assert all(i["url"] is None for i in page["items"])
    page2 = (
        await client.get(
            "/api/me/invites",
            params={"status": "pending", "limit": 2, "cursor": page["next_cursor"]},
            headers=me.headers,
        )
    ).json()
    assert [i["id"] for i in page2["items"]] == [str(pending[0].id)]
    assert page2["next_cursor"] is None

    expired_page = (
        await client.get("/api/me/invites", params={"status": "expired"}, headers=me.headers)
    ).json()
    assert [i["id"] for i in expired_page["items"]] == [str(expired.id)]
    everything = (await client.get("/api/me/invites", headers=me.headers)).json()
    assert len(everything["items"]) == 4  # never another reporter's invites
    assert (
        await client.get("/api/me/invites", params={"status": "cancelled"}, headers=me.headers)
    ).status_code == 422
    assert (await client.get("/api/me/invites", headers=accepter.headers)).json()["items"] == []


# --------------------------------------------------- GET /api/deeds/options


async def test_options_reflect_config_and_todays_invites(
    client: AsyncClient, session: AsyncSession
) -> None:
    me = await make_member(session)
    await publish_limits(session, invites_per_day=3, reporter_monthly_limit=10)
    now = datetime.now(UTC)
    yesterday = now - timedelta(days=1, hours=1)
    # Every invite created today counts, whatever its status; yesterday's don't.
    await _pending_deed(session, me, at=now)
    await _pending_deed(session, me, invite_status=InviteStatus.EXPIRED, at=now)
    await _pending_deed(session, me, at=yesterday)
    await session.commit()
    # The monthly limit (D16) counts every deed this IST month, including yesterday's if it
    # falls in the same month.
    month_start, _ = ist_month(now)
    reported = 2 + (1 if yesterday >= month_start else 0)

    response = await client.get("/api/deeds/options", headers=me.headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["invites_remaining_today"] == 1
    assert body["reports_remaining_this_month"] == 10 - reported
    assert body["invites_enabled"] is False  # D17 default
    assert {"slug": "animals", "label": "Animals"} in body["categories"]
    assert set(body) == {
        "categories",
        "invites_enabled",
        "invites_remaining_today",
        "reports_remaining_this_month",
        "invite_expiry_hours",
        # Revision 5 (D20, D23)
        "reporter_monthly_limit",
        "reporter_doer_monthly_limit",
        "month_resets_at",
        "description_min",
        "description_max",
        "beneficiaries_max",
        "scan_submit_minutes",
        "qr_occurred_max_age_minutes",
        "max_location_accuracy_m",
        "service_area_radius_km",
        "same_act_cap",
        "photos_enabled",
    }
    # The limits and copy values follow config (seeded values, apart from the two published).
    assert body["reporter_monthly_limit"] == 10
    assert body["reporter_doer_monthly_limit"] == 15
    assert body["photos_enabled"] is False
    assert body["beneficiaries_max"] == 10000
    assert body["qr_occurred_max_age_minutes"] == 120
    assert body["max_location_accuracy_m"] == 500
    assert body["service_area_radius_km"] == 40
    assert body["same_act_cap"] == 5
    assert datetime.fromisoformat(body["month_resets_at"]) == ist_month(now)[1]
    assert body["month_resets_at"].endswith("+05:30")

    await _pending_deed(session, me, at=now)
    await _pending_deed(session, me, at=now)
    await session.commit()
    body = (await client.get("/api/deeds/options", headers=me.headers)).json()
    assert body["invites_remaining_today"] == 0  # never negative


async def test_options_need_an_onboarded_member(client: AsyncClient, session: AsyncSession) -> None:
    newcomer = await make_member(session, onboarded=False)
    response = await client.get("/api/deeds/options", headers=newcomer.headers)
    assert response.status_code == 403


async def test_options_limits_and_copy_values_follow_config(
    client: AsyncClient, session: AsyncSession
) -> None:
    """Revision 5 (D20, D23): nothing the F3 copy shows is hard-coded in the response."""
    me = await make_member(session)
    await publish_limits(
        session,
        reporter_monthly_limit=40,
        reporter_doer_monthly_limit=4,
        photos_enabled=True,
        max_location_accuracy_m=250,
        qr_occurred_max_age_minutes=90,
        beneficiaries_max=500,
        same_act_cap=3,
    )
    body = (await client.get("/api/deeds/options", headers=me.headers)).json()
    assert body["reporter_monthly_limit"] == 40
    assert body["reports_remaining_this_month"] == 40
    assert body["reporter_doer_monthly_limit"] == 4
    assert body["photos_enabled"] is True
    assert body["max_location_accuracy_m"] == 250
    assert body["qr_occurred_max_age_minutes"] == 90
    assert body["beneficiaries_max"] == 500
    assert body["same_act_cap"] == 3


# --------------------------------------------------- deeds.service views (§4)


async def test_service_views(session: AsyncSession) -> None:
    doer = await make_member(session)
    w1, w2 = await members(session, 2)
    first = await scan_and_report(session, w1, doer)
    second = await scan_and_report(session, w2, doer, east_m=30)
    pending, _ = await _pending_deed(session, w1)

    view = await service.get_deed(session, first.id)
    assert view is not None
    assert view.doer_id == doer.user_id and view.reporter_id == w1.user_id
    assert view.status == service.DeedStatus.VERIFIED and view.has_location
    assert not hasattr(view, "location")
    pending_view = await service.get_deed(session, pending.id)
    assert pending_view is not None and pending_view.doer_id is None
    assert await service.get_deed(session, uuid.uuid4()) is None

    await session.execute(
        update(Deed)
        .where(Deed.id == second.id)
        .values(status=DeedStatus.WITHDRAWN, withdrawn_at=datetime.now(UTC))
    )
    verified = await service.deeds_for_doer(session, doer.user_id)
    assert [v.id for v in verified] == [first.id]
    both = await service.deeds_for_doer(
        session, doer.user_id, statuses=(DeedStatus.VERIFIED, DeedStatus.WITHDRAWN)
    )
    assert {v.id for v in both} == {first.id, second.id}

    assert view.same_act_group_id is not None
    group = await service.same_act_group_view(session, view.same_act_group_id)
    assert group is not None
    assert (group.verified_count, group.counted_count, group.distinct_reporters) == (2, 2, 2)
    assert group.has_centre and group.cap == 5
    # Only still-verified members are listed.
    assert group.verified_deed_ids == (first.id,)
    assert await service.same_act_group_view(session, uuid.uuid4()) is None
