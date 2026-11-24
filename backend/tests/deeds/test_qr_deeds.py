"""The QR path of POST /api/deeds (docs/M2-DESIGN.md §3.2 #3, §4, §7)."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict
from app.core.events import OutboxEvent
from app.modules.deeds.models import (
    Deed,
    DeedStatus,
    QrScan,
    VerificationEvent,
    VerificationEventKind,
    VerificationMethod,
)
from app.modules.identity import service
from tests.deeds.helpers import (
    Member,
    count,
    deed_body,
    location,
    make_member,
    members,
    outbox,
    publish_limits,
    report,
    scan,
    token_for,
)


async def _api_scan(
    client: AsyncClient, session: AsyncSession, witness: Member, doer: Member
) -> str:
    response = await client.post(
        "/api/scans",
        json={"token": await token_for(session, doer), "location": location()},
        headers=witness.headers,
    )
    assert response.status_code == 201, response.text
    return str(response.json()["scan_id"])


async def _post_deed(client: AsyncClient, witness: Member, body: dict[str, Any]) -> Any:
    return await client.post("/api/deeds", json=body, headers=witness.headers)


# --------------------------------------------------- happy path


async def test_qr_deed_is_verified_instantly(client: AsyncClient, session: AsyncSession) -> None:
    witness, doer = await members(session, 2)
    scan_id = await _api_scan(client, session, witness, doer)
    response = await _post_deed(client, witness, deed_body(scan_id))
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "verified"
    assert body["verification_method"] == "qr_scan"
    assert body["category"] == "animals"
    assert body["beneficiaries_count"] == 1
    assert body["reporter"] == {"handle": witness.handle, "display_name": witness.display_name}
    assert body["doer"] == {"handle": doer.handle, "display_name": doer.display_name}
    assert body["same_act"] == {"report_count": 1, "counts_for_points": True}
    assert body["prize_rank_eligible"] is True
    assert body["has_location"] is True
    assert body["can_edit"] is True and body["can_withdraw"] is True
    assert body["invite"] is None
    assert body["withdrawn_at"] is None and body["last_edited_at"] is None
    assert body["verified_at"] is not None
    # No coordinates anywhere.
    assert "12.97" not in response.text and "77.59" not in response.text

    scan_row = await session.get(QrScan, uuid.UUID(scan_id))
    deed = await session.get(Deed, uuid.UUID(body["id"]))
    assert scan_row is not None and deed is not None
    assert scan_row.consumed_at is not None
    assert deed.scan_id == scan_row.id
    assert deed.doer_user_id == doer.user_id
    # occurred_at defaults to the scan time; the location is the scan location.
    assert deed.occurred_at == scan_row.scanned_at
    assert deed.location_accuracy_m == scan_row.accuracy_m
    same_place = await session.scalar(
        select(func.ST_Distance(Deed.location, QrScan.location))
        .join(QrScan, QrScan.id == Deed.scan_id)
        .where(Deed.id == deed.id)
    )
    assert same_place == 0
    assert deed.report_day == deed.reported_at.astimezone(service.LAUNCH_TZ).date()


async def test_verification_event_has_evidence_without_coordinates(
    client: AsyncClient, session: AsyncSession
) -> None:
    witness, doer = await members(session, 2)
    scan_id = await _api_scan(client, session, witness, doer)
    deed_id = (await _post_deed(client, witness, deed_body(scan_id))).json()["id"]
    events = (
        await session.scalars(
            select(VerificationEvent).where(VerificationEvent.deed_id == uuid.UUID(deed_id))
        )
    ).all()
    assert len(events) == 1
    event = events[0]
    assert event.kind == VerificationEventKind.QR_VERIFIED
    assert event.actor_user_id == witness.user_id
    assert event.scan_id == uuid.UUID(scan_id)
    assert set(event.evidence) == {
        "accuracy_m",
        "service_area_km",
        "witness_speed_kmh",
        "doer_speed_kmh",
    }
    assert event.evidence["accuracy_m"] == 20
    assert event.evidence["service_area_km"] == pytest.approx(0, abs=0.01)
    assert event.evidence["witness_speed_kmh"] is None  # first scan for both
    assert event.evidence["doer_speed_kmh"] is None


async def test_evidence_records_speed_against_previous_scans(session: AsyncSession) -> None:
    w, d1, d2 = await members(session, 3)
    t0 = datetime.now(UTC) - timedelta(minutes=20)
    await scan(session, w, d1, at=t0)
    s2 = await scan(session, w, d2, at=t0 + timedelta(minutes=10), north_m=1000)
    deed = await report(session, w, s2.scan_id, at=t0 + timedelta(minutes=11))
    event = await session.scalar(
        select(VerificationEvent).where(VerificationEvent.deed_id == deed.id)
    )
    assert event is not None
    assert event.evidence["witness_speed_kmh"] == pytest.approx(6.0, abs=0.1)
    assert event.evidence["doer_speed_kmh"] is None


async def test_qr_submit_emits_one_submitted_and_one_verified(
    client: AsyncClient, session: AsyncSession
) -> None:
    witness, doer = await members(session, 2)
    scan_id = await _api_scan(client, session, witness, doer)
    deed_id = uuid.UUID((await _post_deed(client, witness, deed_body(scan_id))).json()["id"])
    deed = await session.get(Deed, deed_id)
    assert deed is not None

    submitted = await outbox(session, "deeds.deed_submitted", deed_id)
    verified = await outbox(session, "deeds.deed_verified", deed_id)
    assert len(submitted) == 1 and len(verified) == 1
    assert submitted[0].payload["status"] == "verified"
    assert submitted[0].payload["method"] == "qr_scan"
    assert submitted[0].payload["doer_id"] == str(doer.user_id)
    assert submitted[0].payload["reporter_id"] == str(witness.user_id)
    assert verified[0].payload["counts_for_points"] is True
    assert verified[0].payload["prize_rank_eligible"] is True
    assert verified[0].payload["same_act_group_id"] == str(deed.same_act_group_id)
    assert verified[0].payload["limits_version"] == deed.limits_version
    for row in (submitted[0], verified[0]):
        serialized = str(row.payload)
        assert "Helped a stray dog" not in serialized  # no descriptions
        assert "12.97" not in serialized and "77.59" not in serialized  # no coordinates
        assert "lat" not in row.payload and "location" not in row.payload


# --------------------------------------------------- scan consumption


async def test_scan_is_single_use(client: AsyncClient, session: AsyncSession) -> None:
    witness, doer = await members(session, 2)
    scan_id = await _api_scan(client, session, witness, doer)
    assert (await _post_deed(client, witness, deed_body(scan_id))).status_code == 201
    again = await _post_deed(client, witness, deed_body(scan_id))
    assert again.status_code == 409
    assert again.json()["code"] == "scan_used"
    assert await count(session, Deed, Deed.scan_id == uuid.UUID(scan_id)) == 1


async def test_other_witness_scan_and_unknown_scan_are_404(
    client: AsyncClient, session: AsyncSession
) -> None:
    witness, doer, other = await members(session, 3)
    scan_id = await _api_scan(client, session, witness, doer)
    for who, sid in ((other, scan_id), (doer, scan_id), (witness, str(uuid.uuid4()))):
        response = await _post_deed(client, who, deed_body(sid))
        assert response.status_code == 404
        assert response.json()["code"] == "scan_not_found"
    # The real owner can still use it.
    assert (await _post_deed(client, witness, deed_body(scan_id))).status_code == 201


async def test_expired_scan_is_410(client: AsyncClient, session: AsyncSession) -> None:
    witness, doer = await members(session, 2)
    scan_id = await _api_scan(client, session, witness, doer)
    await session.execute(
        update(QrScan)
        .where(QrScan.id == uuid.UUID(scan_id))
        .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    await session.commit()
    response = await _post_deed(client, witness, deed_body(scan_id))
    assert response.status_code == 410
    assert response.json()["code"] == "scan_expired"


async def test_scan_submit_window_comes_from_config(session: AsyncSession) -> None:
    witness, doer = await members(session, 2)
    await publish_limits(session, scan_submit_minutes=1)
    t0 = datetime.now(UTC) - timedelta(minutes=5)
    s = await scan(session, witness, doer, at=t0)
    assert s.expires_at == t0 + timedelta(minutes=1)


async def test_failed_submit_leaves_scan_usable(client: AsyncClient, session: AsyncSession) -> None:
    witness, doer = await members(session, 2)
    scan_id = await _api_scan(client, session, witness, doer)
    future = (datetime.now(UTC) + timedelta(minutes=5)).isoformat()
    bad = await _post_deed(client, witness, deed_body(scan_id, occurred_at=future))
    assert bad.status_code == 400
    assert bad.json()["code"] == "implausible_time"
    scan_row = await session.get(QrScan, uuid.UUID(scan_id))
    assert scan_row is not None
    await session.refresh(scan_row)
    assert scan_row.consumed_at is None
    assert await count(session, Deed, Deed.scan_id == uuid.UUID(scan_id)) == 0
    assert (await _post_deed(client, witness, deed_body(scan_id))).status_code == 201


# --------------------------------------------------- occurred_at


async def test_occurred_at_bounds(session: AsyncSession) -> None:
    from app.core.errors import AppError

    cases = [
        (timedelta(minutes=-120), True),
        (timedelta(minutes=-120, seconds=-1), False),
        (timedelta(seconds=60), True),
        (timedelta(seconds=61), False),
    ]
    doer = await make_member(session)
    t0 = datetime.now(UTC) - timedelta(minutes=5)
    for i, (delta, ok) in enumerate(cases):
        witness = await make_member(session)
        s = await scan(session, witness, doer, at=t0 + timedelta(seconds=i), east_m=i)
        scan_row = await session.get(QrScan, s.scan_id)
        assert scan_row is not None
        occurred = scan_row.scanned_at + delta
        if ok:
            deed = await report(
                session, witness, s.scan_id, at=t0 + timedelta(minutes=1), occurred_at=occurred
            )
            assert deed.occurred_at == occurred
        else:
            with pytest.raises(AppError) as exc:
                async with session.begin_nested():
                    await report(
                        session,
                        witness,
                        s.scan_id,
                        at=t0 + timedelta(minutes=1),
                        occurred_at=occurred,
                    )
            assert exc.value.code == "implausible_time"


# --------------------------------------------------- validation


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"category": "crypto"}, "invalid_category"),
        ({"description": "too short"}, "invalid_description"),
        ({"description": "          padded            "}, "invalid_description"),
    ],
)
async def test_content_checked_against_config(
    client: AsyncClient, session: AsyncSession, overrides: dict[str, Any], code: str
) -> None:
    witness, doer = await members(session, 2)
    scan_id = await _api_scan(client, session, witness, doer)
    response = await _post_deed(client, witness, deed_body(scan_id, **overrides))
    assert response.status_code == 400
    assert response.json()["code"] == code


async def test_beneficiaries_max_comes_from_config(
    client: AsyncClient, session: AsyncSession
) -> None:
    witness, doer = await members(session, 2)
    await publish_limits(session, beneficiaries_max=3)
    scan_id = await _api_scan(client, session, witness, doer)
    response = await _post_deed(client, witness, deed_body(scan_id, beneficiaries_count=4))
    assert response.status_code == 400
    assert response.json()["code"] == "invalid_beneficiaries"


@pytest.mark.parametrize(
    "overrides",
    [
        {"counts_for_points": True},
        {"status": "verified"},
        {"doer_user_id": str(uuid.uuid4())},
        {"prize_rank_eligible": True},
        {"location": location()},
        {"method": "something_else"},
        {"beneficiaries_count": 0},
        {"occurred_at": "2026-11-15T10:00:00"},  # no timezone
    ],
)
async def test_server_controlled_fields_are_rejected(
    client: AsyncClient, session: AsyncSession, overrides: dict[str, Any]
) -> None:
    witness, doer = await members(session, 2)
    scan_id = await _api_scan(client, session, witness, doer)
    response = await _post_deed(client, witness, deed_body(scan_id, **overrides))
    assert response.status_code == 422, response.text


async def test_deed_create_requires_onboarding(client: AsyncClient, session: AsyncSession) -> None:
    user = await make_member(session, onboarded=False)
    response = await _post_deed(client, user, deed_body(uuid.uuid4()))
    assert response.status_code == 403
    assert (await client.post("/api/deeds", json=deed_body(uuid.uuid4()))).status_code == 401


# --------------------------------------------------- pair-day across IST midnight


async def test_pair_day_on_deeds_maps_to_already_recognized_today(session: AsyncSession) -> None:
    witness, doer = await members(session, 2)
    # 18:25 UTC = 23:55 IST on the 15th; 18:31 UTC = 00:01 IST on the 16th.
    s1 = await scan(session, witness, doer, at=datetime(2026, 11, 15, 18, 25, tzinfo=UTC))
    s2 = await scan(session, witness, doer, at=datetime(2026, 11, 15, 18, 31, tzinfo=UTC))
    # The first deed is reported after midnight, so both deeds would have report_day = 16th.
    await report(session, witness, s1.scan_id, at=datetime(2026, 11, 15, 18, 35, tzinfo=UTC))
    with pytest.raises(Conflict) as exc:
        await report(session, witness, s2.scan_id, at=datetime(2026, 11, 15, 18, 36, tzinfo=UTC))
    assert exc.value.code == "already_recognized_today"


async def test_scan_refused_when_pair_already_has_a_deed_today(session: AsyncSession) -> None:
    witness, doer = await members(session, 2)
    s1 = await scan(session, witness, doer, at=datetime(2026, 11, 15, 18, 25, tzinfo=UTC))
    await report(session, witness, s1.scan_id, at=datetime(2026, 11, 15, 18, 35, tzinfo=UTC))
    with pytest.raises(Conflict) as exc:
        await scan(session, witness, doer, at=datetime(2026, 11, 15, 18, 40, tzinfo=UTC))
    assert exc.value.code == "already_recognized_today"


# --------------------------------------------------- ledger integrity


@pytest.mark.parametrize(
    "statement",
    ["UPDATE verification_events SET evidence = '{}'", "DELETE FROM verification_events"],
)
async def test_verification_events_are_append_only(session: AsyncSession, statement: str) -> None:
    witness, doer = await members(session, 2)
    s = await scan(session, witness, doer)
    deed = await report(session, witness, s.scan_id)
    with pytest.raises(DBAPIError, match="append-only"):
        async with session.begin_nested():
            await session.execute(text(f"{statement} WHERE deed_id = :d"), {"d": deed.id})


async def test_deed_row_is_consistent(session: AsyncSession) -> None:
    witness, doer = await members(session, 2)
    s = await scan(session, witness, doer)
    out = await report(session, witness, s.scan_id)
    deed = await session.get(Deed, out.id)
    assert deed is not None
    assert deed.status == DeedStatus.VERIFIED
    assert deed.verification_method == VerificationMethod.QR_SCAN
    assert deed.prize_rank_eligible is True
    assert deed.same_act_group_id is not None
    assert (
        await count(session, OutboxEvent, OutboxEvent.payload["deed_id"].astext == str(deed.id))
        == 2
    )
