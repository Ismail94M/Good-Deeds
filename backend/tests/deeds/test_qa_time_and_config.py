"""QA1: clock boundaries (IST midnight and month edges, expiry instants) and config changes in
the middle of a flow (docs/M2-DESIGN.md §7 Config, Single-use, Plausibility, Invites)."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.modules.deeds import _invites, _self_logs
from app.modules.deeds._limits import Category, ServiceArea, load_limits
from app.modules.deeds.models import Deed, DeedStatus, QrScan
from app.modules.deeds.schemas import SelfLogCreate
from tests.deeds.helpers import (
    location,
    make_member,
    members,
    publish_limits,
    report,
    scan,
    token_for,
)
from tests.deeds.invite_helpers import accept, create_invite, share_device

# --------------------------------------------------- IST days


async def test_same_utc_day_but_new_ist_day_is_allowed(session: AsyncSession) -> None:
    """The other side of the developer's UTC-midnight test: 23:50 IST and 00:10 IST are the
    same UTC day (the 15th) but different IST days, so the pair may scan again."""
    witness, doer = await members(session, 2)
    await scan(session, witness, doer, at=datetime(2026, 11, 15, 18, 20, tzinfo=UTC))
    second = await scan(session, witness, doer, at=datetime(2026, 11, 15, 18, 40, tzinfo=UTC))
    row = await session.get(QrScan, second.scan_id)
    assert row is not None and str(row.scan_day) == "2026-11-16"


async def test_invite_daily_limit_counts_the_ist_day_not_the_utc_day(
    session: AsyncSession,
) -> None:
    await publish_limits(session, invites_per_day=2)
    reporter = await make_member(session)
    # 00:01 IST on the 11th (still the 10th in UTC), then 10:30 IST on the 11th.
    await create_invite(session, reporter, at=datetime(2026, 11, 10, 18, 31, tzinfo=UTC))
    await create_invite(session, reporter, at=datetime(2026, 11, 11, 5, 0, tzinfo=UTC))
    with pytest.raises(AppError) as exc:
        await create_invite(session, reporter, at=datetime(2026, 11, 11, 18, 29, tzinfo=UTC))
    assert exc.value.code == "invite_daily_limit"


async def test_shared_device_month_follows_ist_edges(session: AsyncSession) -> None:
    await publish_limits(session, shared_device_pair_monthly_limit=1)
    reporter, doer = await members(session, 2)
    await share_device(session, reporter, doer)

    async def accept_at(at: datetime) -> None:
        _, token = await create_invite(session, reporter, at=at - timedelta(minutes=1))
        await accept(session, token, doer, at=at)

    # 23:59 IST on 30 September: September's one.
    await accept_at(datetime(2026, 9, 30, 18, 29, tzinfo=UTC))
    # 00:01 IST on 1 October: a new month, although it is still 30 September in UTC.
    await accept_at(datetime(2026, 9, 30, 18, 31, tzinfo=UTC))
    # 23:59 IST on 31 October: October's limit is used.
    with pytest.raises(AppError) as exc:
        await accept_at(datetime(2026, 10, 31, 18, 29, tzinfo=UTC))
    assert exc.value.code == "same_device_limit"
    # 00:01 IST on 1 November: allowed again.
    await accept_at(datetime(2026, 10, 31, 18, 31, tzinfo=UTC))


async def test_self_log_future_limit_is_exactly_five_minutes(session: AsyncSession) -> None:
    owner = await make_member(session)
    _, limits = await load_limits(session)
    now = datetime(2026, 11, 15, 6, 0, tzinfo=UTC)

    def body(at: datetime) -> SelfLogCreate:
        return SelfLogCreate(
            category="animals", description="Fed the street dogs near the park", occurred_at=at
        )

    await _self_logs.create(
        session, owner.user_id, body(now + timedelta(minutes=5)), limits=limits, now=now
    )
    with pytest.raises(AppError) as exc:
        await _self_logs.create(
            session,
            owner.user_id,
            body(now + timedelta(minutes=5, seconds=1)),
            limits=limits,
            now=now,
        )
    assert exc.value.code == "implausible_time"


# --------------------------------------------------- expiry instants, and config changes mid-flow


async def test_scan_submit_window_is_fixed_at_scan_time(session: AsyncSession) -> None:
    w1, w2, doer = await members(session, 3)
    t0 = datetime.now(UTC) - timedelta(hours=1)
    s1 = await scan(session, w1, doer, at=t0)
    s2 = await scan(session, w2, doer, at=t0, east_m=10)
    # Shortening the window afterwards doesn't cut existing scans short ...
    await publish_limits(session, scan_submit_minutes=1)
    out = await report(session, w1, s1.scan_id, at=t0 + timedelta(minutes=15, seconds=-1))
    assert out.status == "verified"
    # ... and lengthening it doesn't extend them: at expires_at exactly it is gone.
    await publish_limits(session, scan_submit_minutes=60)
    with pytest.raises(AppError) as exc:
        await report(session, w2, s2.scan_id, at=t0 + timedelta(minutes=15))
    assert exc.value.code == "scan_expired" and exc.value.status_code == 410


async def test_invite_expiry_is_fixed_at_creation(session: AsyncSession) -> None:
    reporter, doer = await members(session, 2)
    at = datetime(2026, 11, 1, 6, 0, tzinfo=UTC)
    created, token = await create_invite(session, reporter, at=at)
    late, late_token = await create_invite(session, reporter, at=at + timedelta(minutes=1))
    await publish_limits(session, invite_expiry_hours=1)
    # The job two hours later doesn't expire invites made under the 168 h rule ...
    await _invites.expire_invites(session, now=at + timedelta(hours=2))
    deed = await session.get(Deed, created.id, populate_existing=True)
    assert deed is not None and deed.status == DeedStatus.PENDING
    out = await accept(session, token, doer, at=at + timedelta(hours=3))
    assert out.status == "verified"
    # ... and a longer setting doesn't keep them alive past their own expiry.
    await publish_limits(session, invite_expiry_hours=1000)
    with pytest.raises(AppError) as exc:
        await accept(session, late_token, doer, at=at + timedelta(hours=168, minutes=1))
    assert exc.value.code == "invite_expired"
    assert late.invite is not None


async def _post_scan(
    client: AsyncClient, who: Any, doer: Any, session: AsyncSession, **loc: Any
) -> Any:
    return await client.post(
        "/api/scans",
        json={"token": await token_for(session, doer), "location": location(**loc)},
        headers=who.headers,
    )


async def test_accuracy_and_service_area_come_from_config(
    client: AsyncClient, session: AsyncSession
) -> None:
    await publish_limits(
        session,
        max_location_accuracy_m=50,
        service_area=ServiceArea(lat=12.9716, lng=77.5946, radius_km=5),
    )
    witnesses = await members(session, 4)
    doers = await members(session, 4)
    cases = [
        ({"accuracy_m": 50}, 201, None),
        ({"accuracy_m": 51}, 400, "location_too_imprecise"),
        ({"north_m": 4900}, 201, None),
        ({"north_m": 5100}, 400, "outside_service_area"),
    ]
    for (loc, status, code), w, d in zip(cases, witnesses, doers, strict=True):
        r = await _post_scan(client, w, d, session, **loc)
        assert r.status_code == status, (loc, r.text)
        if code:
            assert r.json()["code"] == code


async def test_travel_speed_and_slack_come_from_config(session: AsyncSession) -> None:
    # 36 km/h is 10 m/s: in 60 s that is 600 m, plus 100 m of slack.
    await publish_limits(session, max_travel_speed_kmh=36, travel_slack_m=100)
    w_ok, w_bad, d1, d2, d3, d4 = await members(session, 6)
    t0 = datetime.now(UTC) - timedelta(hours=1)
    await scan(session, w_ok, d1, at=t0)
    await scan(session, w_bad, d2, at=t0, east_m=3000)
    later = t0 + timedelta(seconds=60)
    await scan(session, w_ok, d3, at=later, north_m=680)
    with pytest.raises(AppError) as exc:
        await scan(session, w_bad, d4, at=later, north_m=720, east_m=3000)
    assert exc.value.code == "implausible_travel"


async def test_retiring_a_category_does_not_block_editing_an_old_self_log(
    client: AsyncClient, session: AsyncSession
) -> None:
    owner = await make_member(session)
    created = await client.post(
        "/api/me/self-logs",
        json={
            "category": "animals",
            "description": "Fed the street dogs near the park",
            "occurred_at": datetime.now(UTC).isoformat(),
        },
        headers=owner.headers,
    )
    assert created.status_code == 201
    _, limits = await load_limits(session)
    await publish_limits(
        session, categories=tuple(c for c in limits.categories if c.slug != "animals")
    )
    response = await client.patch(
        f"/api/me/self-logs/{created.json()['id']}",
        json={"description": "Fed and watered the street dogs"},
        headers=owner.headers,
    )
    assert response.status_code == 200, response.text


async def test_retiring_a_category_does_not_block_editing_an_old_deed(
    client: AsyncClient, session: AsyncSession
) -> None:
    """Control for the self-log bug above: the deed path gets this right."""
    witness, doer = await members(session, 2)
    s = await scan(session, witness, doer)
    deed = await report(session, witness, s.scan_id)
    _, limits = await load_limits(session)
    await publish_limits(
        session,
        categories=(
            *[c for c in limits.categories if c.slug != "animals"],
            Category(slug="x", label="X"),
        ),
    )
    response = await client.patch(
        f"/api/deeds/{deed.id}",
        json={"description": "Helped two stray dogs across the road"},
        headers=witness.headers,
    )
    assert response.status_code == 200, response.text
    assert uuid.UUID(response.json()["id"]) == deed.id


# --------------------------------------------------- self-log write limits cover every write


async def test_self_log_patch_and_delete_share_the_write_counter_before_lookup(
    client: AsyncClient, other_ip: AsyncClient, session: AsyncSession
) -> None:
    await publish_limits(session, rate_self_log_user="3/3600", rate_self_log_ip="100/3600")
    owner, other = await members(session, 2)
    created = await client.post(
        "/api/me/self-logs",
        json={
            "category": "animals",
            "description": "Fed the street dogs near the park",
            "occurred_at": datetime.now(UTC).isoformat(),
        },
        headers=owner.headers,
    )
    assert created.status_code == 201
    log_id = created.json()["id"]
    # Probing someone else's / unknown IDs still uses up the counter (limits come first).
    assert (
        await client.patch(
            f"/api/me/self-logs/{uuid.uuid4()}", json={"category": "other"}, headers=owner.headers
        )
    ).status_code == 404
    assert (
        await client.delete(f"/api/me/self-logs/{uuid.uuid4()}", headers=owner.headers)
    ).status_code == 404
    limited = await other_ip.delete(f"/api/me/self-logs/{log_id}", headers=owner.headers)
    assert limited.status_code == 429 and limited.json()["code"] == "rate_limited"
    # Another account is unaffected.
    assert (
        await client.delete(f"/api/me/self-logs/{log_id}", headers=other.headers)
    ).status_code == 404
