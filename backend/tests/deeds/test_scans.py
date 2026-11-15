"""POST /api/scans and GET /api/scans/{id} (docs/M2-DESIGN.md §3.2 #1-#2, §7)."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, NotFound
from app.core.runtime_config import publish
from app.core.security import keyed_hash
from app.modules.deeds import _scans
from app.modules.deeds.models import QrScan, ScanMethod
from app.modules.identity import _qr, service
from app.modules.identity.models import DoerCode, User, UserStatus
from tests.deeds.helpers import (
    Member,
    code_for,
    count,
    location,
    make_member,
    members,
    report,
    scan,
    token_for,
)


async def _post_scan(
    client: AsyncClient, witness: Member, body: dict[str, Any], **headers: str
) -> Any:
    return await client.post("/api/scans", json=body, headers={**witness.headers, **headers})


async def _qr_body(session: AsyncSession, doer: Member, **loc: Any) -> dict[str, Any]:
    return {"token": await token_for(session, doer), "location": location(**loc)}


# --------------------------------------------------- happy paths


async def test_qr_scan_is_recorded(client: AsyncClient, session: AsyncSession) -> None:
    witness, doer = await members(session, 2)
    before = datetime.now(UTC)
    response = await _post_scan(
        client, witness, await _qr_body(session, doer), **{"X-Device-Id": "device-abc"}
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert set(body) == {"scan_id", "doer", "expires_at"}
    assert body["doer"] == {"handle": doer.handle, "display_name": doer.display_name}
    expires = datetime.fromisoformat(body["expires_at"])
    assert before + timedelta(minutes=15) <= expires <= datetime.now(UTC) + timedelta(minutes=15)

    row = await session.get(QrScan, uuid.UUID(body["scan_id"]))
    assert row is not None
    assert row.witness_user_id == witness.user_id
    assert row.doer_user_id == doer.user_id
    assert row.scan_method == ScanMethod.QR
    assert row.consumed_at is None
    assert row.accuracy_m == 20
    assert row.device_hash == service.device_hash("device-abc")
    assert row.ip_hash == keyed_hash("ip", "127.0.0.1")
    assert row.scan_day == row.scanned_at.astimezone(service.LAUNCH_TZ).date()
    window_seconds = 30
    assert row.token_window == _qr.current_window(window_seconds, row.scanned_at.timestamp())


async def test_manual_code_scan_is_recorded(client: AsyncClient, session: AsyncSession) -> None:
    witness, doer = await members(session, 2)
    body = {
        # Typed loosely: lower case, with the prefix and a space.
        "short_id": f"gd-{doer.short_id[:3].lower()} {doer.short_id[3:].lower()}",
        "code": await code_for(session, doer),
        "location": location(),
    }
    response = await _post_scan(client, witness, body)
    assert response.status_code == 201, response.text
    row = await session.get(QrScan, uuid.UUID(response.json()["scan_id"]))
    assert row is not None
    assert row.scan_method == ScanMethod.MANUAL_CODE


async def test_client_timestamps_are_ignored(client: AsyncClient, session: AsyncSession) -> None:
    witness, doer = await members(session, 2)
    body = {**await _qr_body(session, doer), "scanned_at": "2020-01-01T00:00:00Z"}
    response = await _post_scan(client, witness, body)
    assert response.status_code == 201, response.text
    row = await session.get(QrScan, uuid.UUID(response.json()["scan_id"]))
    assert row is not None
    assert abs((row.scanned_at - datetime.now(UTC)).total_seconds()) < 60


# --------------------------------------------------- auth and validation


async def test_scan_requires_login_and_onboarding(
    client: AsyncClient, session: AsyncSession
) -> None:
    doer = await make_member(session)
    not_onboarded = await make_member(session, onboarded=False)
    body = await _qr_body(session, doer)
    assert (await client.post("/api/scans", json=body)).status_code == 401
    response = await _post_scan(client, not_onboarded, body)
    assert response.status_code == 403
    assert response.json()["code"] == "onboarding_required"


@pytest.mark.parametrize(
    "body",
    [
        {"token": "x"},  # no location
        {"token": "x", "short_id": "GD-1234567", "code": "123456", "location": location()},
        {"location": location()},
        {"short_id": "GD-1234567", "location": location()},
        {"token": "x", "location": {"lat": 91, "lng": 77, "accuracy_m": 10}},
        {"token": "x", "location": {"lat": 12.9, "lng": 77.5, "accuracy_m": 0}},
    ],
)
async def test_invalid_bodies_are_422(
    client: AsyncClient, session: AsyncSession, body: dict[str, Any]
) -> None:
    witness = await make_member(session)
    assert (await _post_scan(client, witness, body)).status_code == 422


# --------------------------------------------------- resolving the doer


async def test_bad_token_is_qr_invalid(client: AsyncClient, session: AsyncSession) -> None:
    witness, doer = await members(session, 2)
    for token in ("garbage", (await token_for(session, doer))[:-1] + "0"):
        response = await _post_scan(client, witness, {"token": token, "location": location()})
        assert response.status_code == 404
        assert response.json()["code"] == "qr_invalid"
    assert await count(session, QrScan, QrScan.witness_user_id == witness.user_id) == 0


async def test_wrong_code_and_unknown_id_look_identical(
    client: AsyncClient, session: AsyncSession
) -> None:
    witness, doer = await members(session, 2)
    right = await code_for(session, doer)
    wrong = f"{(int(right) + 1) % 1_000_000:06d}"
    wrong_code = await _post_scan(
        client, witness, {"short_id": doer.short_id, "code": wrong, "location": location()}
    )
    unknown_id = await _post_scan(
        client, witness, {"short_id": "GD-ZZZZZZZ", "code": right, "location": location()}
    )
    assert wrong_code.status_code == unknown_id.status_code == 404
    assert wrong_code.json() == unknown_id.json()
    assert wrong_code.json()["code"] == "doer_code_invalid"


async def test_short_id_alone_never_resolves(client: AsyncClient, session: AsyncSession) -> None:
    witness, doer = await members(session, 2)
    response = await _post_scan(
        client, witness, {"short_id": doer.short_id, "code": "", "location": location()}
    )
    assert response.status_code == 422
    response = await _post_scan(client, witness, {"token": doer.short_id, "location": location()})
    assert response.status_code == 404


async def test_token_and_code_grace_window(session: AsyncSession) -> None:
    w1, w2, w3, w4, doer = await members(session, 5)
    now = datetime.now(UTC)
    # Minted in the previous window (accepted for 30-60 s) is still fine...
    await scan(session, w1, doer, at=now, minted_at=now - timedelta(seconds=30))
    await scan(session, w2, doer, at=now, minted_at=now - timedelta(seconds=30), manual=True)
    # ...two windows old is not.
    with pytest.raises(NotFound) as exc:
        await scan(session, w3, doer, at=now, minted_at=now - timedelta(seconds=60))
    assert exc.value.code == "qr_invalid"
    with pytest.raises(NotFound) as exc:
        await scan(session, w4, doer, at=now, minted_at=now - timedelta(seconds=60), manual=True)
    assert exc.value.code == "doer_code_invalid"


async def test_grace_comes_from_config(client: AsyncClient, session: AsyncSession) -> None:
    witness, doer = await members(session, 2)
    await publish(
        session, _qr.QrConfig(window_seconds=30, grace_windows=0), actor_id=None, reason="test"
    )
    await session.commit()
    old = await token_for(session, doer, datetime.now(UTC) - timedelta(seconds=30))
    response = await _post_scan(client, witness, {"token": old, "location": location()})
    assert response.status_code == 404
    assert response.json()["code"] == "qr_invalid"


async def test_rotated_revoked_suspended_or_unonboarded_doer_is_404(
    client: AsyncClient, session: AsyncSession
) -> None:
    witness = await make_member(session)
    rotated, revoked, suspended = await members(session, 3)
    unonboarded = await make_member(session, onboarded=False)
    tokens = {m.user_id: await token_for(session, m) for m in (rotated, revoked, suspended)}
    tokens[unonboarded.user_id] = await token_for(session, unonboarded)
    await session.execute(
        update(DoerCode).where(DoerCode.user_id == rotated.user_id).values(key_version=2)
    )
    await session.execute(
        update(DoerCode)
        .where(DoerCode.user_id == revoked.user_id)
        .values(revoked_at=datetime.now(UTC))
    )
    await session.execute(
        update(User).where(User.id == suspended.user_id).values(status=UserStatus.SUSPENDED)
    )
    await session.commit()
    for token in tokens.values():
        response = await _post_scan(client, witness, {"token": token, "location": location()})
        assert response.status_code == 404, response.text
        assert response.json()["code"] == "qr_invalid"


# --------------------------------------------------- independence


async def test_self_scan_is_refused(client: AsyncClient, session: AsyncSession) -> None:
    me = await make_member(session)
    response = await _post_scan(client, me, await _qr_body(session, me))
    assert response.status_code == 409
    assert response.json()["code"] == "cannot_verify_self"
    manual = await _post_scan(
        client,
        me,
        {"short_id": me.short_id, "code": await code_for(session, me), "location": location()},
    )
    assert manual.status_code == 409
    assert manual.json()["code"] == "cannot_verify_self"


async def test_shared_phone_history_is_refused(
    client: AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    # phone_history makes a number belong to one account ever, so simulate a shared row.
    witness, doer = await members(session, 2)
    real = service.phone_hashes

    async def shared(s: AsyncSession, user_id: uuid.UUID) -> set[str]:
        return await real(s, user_id) | {"shared-hash"}

    monkeypatch.setattr(service, "phone_hashes", shared)
    response = await _post_scan(client, witness, await _qr_body(session, doer))
    assert response.status_code == 409
    assert response.json()["code"] == "cannot_verify_self"


# --------------------------------------------------- single use


async def test_same_window_twice_is_already_scanned(session: AsyncSession) -> None:
    witness, doer = await members(session, 2)
    # IST midnight is a window edge. A token minted at 23:59:30 IST is still accepted at
    # 00:00:10 IST (grace), on a new IST day, so only the window rule stops a second scan.
    minted = datetime(2026, 11, 15, 18, 29, 30, tzinfo=UTC)
    await scan(session, witness, doer, at=minted + timedelta(seconds=20), minted_at=minted)
    with pytest.raises(Conflict) as exc:
        await scan(session, witness, doer, at=minted + timedelta(seconds=40), minted_at=minted)
    assert exc.value.code == "already_scanned"


async def test_window_unique_constraint_maps_to_already_scanned(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The race: the pre-checks ran before the other request committed.
    witness, doer = await members(session, 2)
    minted = datetime(2026, 11, 15, 18, 29, 30, tzinfo=UTC)
    await scan(session, witness, doer, at=minted + timedelta(seconds=20), minted_at=minted)

    async def never(*_args: object) -> bool:
        return False

    monkeypatch.setattr(_scans, "_window_used", never)
    with pytest.raises(Conflict) as exc:
        await scan(session, witness, doer, at=minted + timedelta(seconds=40), minted_at=minted)
    assert exc.value.code == "already_scanned"


async def test_pair_day_unique_constraint_maps_to_already_recognized_today(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    witness, doer = await members(session, 2)
    now = datetime.now(UTC)
    await scan(session, witness, doer, at=now - timedelta(minutes=5))

    async def never(*_args: object) -> bool:
        return False

    monkeypatch.setattr(_scans, "_pair_used_today", never)
    with pytest.raises(Conflict) as exc:
        await scan(session, witness, doer, at=now)
    assert exc.value.code == "already_recognized_today"


async def test_same_pair_same_ist_day_is_refused(
    client: AsyncClient, session: AsyncSession
) -> None:
    witness, doer = await members(session, 2)
    first = await _post_scan(client, witness, await _qr_body(session, doer))
    assert first.status_code == 201
    await session.execute(
        update(QrScan)
        .where(QrScan.id == uuid.UUID(first.json()["scan_id"]))
        .values(token_window=QrScan.token_window - 10)
    )
    await session.commit()
    second = await _post_scan(client, witness, await _qr_body(session, doer))
    assert second.status_code == 409
    assert second.json()["code"] == "already_recognized_today"


async def test_pair_day_follows_ist_not_utc(session: AsyncSession) -> None:
    witness, doer = await members(session, 2)
    # 18:40 UTC on the 15th is 00:10 IST on the 16th; 00:10 UTC on the 16th is 05:40 IST.
    await scan(session, witness, doer, at=datetime(2026, 11, 15, 18, 40, tzinfo=UTC))
    with pytest.raises(Conflict) as exc:
        await scan(session, witness, doer, at=datetime(2026, 11, 16, 0, 10, tzinfo=UTC))
    assert exc.value.code == "already_recognized_today"
    # The next IST day is allowed.
    await scan(session, witness, doer, at=datetime(2026, 11, 16, 18, 40, tzinfo=UTC))


async def test_other_witnesses_and_doers_are_independent(session: AsyncSession) -> None:
    w1, w2, d1, d2 = await members(session, 4)
    now = datetime.now(UTC)
    await scan(session, w1, d1, at=now)
    await scan(session, w2, d1, at=now)
    await scan(session, w1, d2, at=now)


# --------------------------------------------------- plausibility


async def test_location_accuracy_boundary(client: AsyncClient, session: AsyncSession) -> None:
    witness, doer, doer2 = await members(session, 3)
    too_vague = await _post_scan(client, witness, await _qr_body(session, doer, accuracy_m=501))
    assert too_vague.status_code == 400
    assert too_vague.json()["code"] == "location_too_imprecise"
    at_limit = await _post_scan(client, witness, await _qr_body(session, doer2, accuracy_m=500))
    assert at_limit.status_code == 201, at_limit.text


async def test_outside_service_area(client: AsyncClient, session: AsyncSession) -> None:
    witness, doer = await members(session, 2)
    response = await _post_scan(client, witness, await _qr_body(session, doer, north_m=41_000))
    assert response.status_code == 400
    assert response.json()["code"] == "outside_service_area"
    assert await count(session, QrScan, QrScan.witness_user_id == witness.user_id) == 0


async def test_witness_travel(client: AsyncClient, session: AsyncSession) -> None:
    witness, doer_a, doer_b, doer_c = await members(session, 4)
    assert (await _post_scan(client, witness, await _qr_body(session, doer_a))).status_code == 201
    far = await _post_scan(client, witness, await _qr_body(session, doer_b, north_m=10_000))
    assert far.status_code == 400
    assert far.json()["code"] == "implausible_travel"
    near = await _post_scan(client, witness, await _qr_body(session, doer_c, north_m=500))
    assert near.status_code == 201


async def test_doer_travel_catches_forwarded_screenshot(
    client: AsyncClient, session: AsyncSession
) -> None:
    w1, w2, w3, doer = await members(session, 4)
    token = await token_for(session, doer)
    first = await _post_scan(client, w1, {"token": token, "location": location()})
    assert first.status_code == 201
    remote = await _post_scan(client, w2, {"token": token, "location": location(north_m=15_000)})
    assert remote.status_code == 400
    assert remote.json()["code"] == "implausible_travel"
    nearby = await _post_scan(client, w3, {"token": token, "location": location(north_m=100)})
    assert nearby.status_code == 201


async def test_travel_uses_server_time_of_previous_scan(session: AsyncSession) -> None:
    witness, doer_a, doer_b = await members(session, 3)
    t0 = datetime.now(UTC) - timedelta(hours=1)
    await scan(session, witness, doer_a, at=t0)
    # 10 km in 10 minutes is 60 km/h: plausible.
    await scan(session, witness, doer_b, at=t0 + timedelta(minutes=10), north_m=10_000)


# --------------------------------------------------- GET /api/scans/{id}


async def test_get_own_scan(client: AsyncClient, session: AsyncSession) -> None:
    witness, doer = await members(session, 2)
    created = (await _post_scan(client, witness, await _qr_body(session, doer))).json()
    response = await client.get(f"/api/scans/{created['scan_id']}", headers=witness.headers)
    assert response.status_code == 200
    assert response.json() == {**created, "consumed": False}


async def test_get_other_witness_scan_is_404(client: AsyncClient, session: AsyncSession) -> None:
    witness, doer, other = await members(session, 3)
    created = (await _post_scan(client, witness, await _qr_body(session, doer))).json()
    for who in (other, doer):
        response = await client.get(f"/api/scans/{created['scan_id']}", headers=who.headers)
        assert response.status_code == 404
        assert response.json()["code"] == "scan_not_found"
    missing = await client.get(f"/api/scans/{uuid.uuid4()}", headers=witness.headers)
    assert missing.status_code == 404
    assert missing.json() == response.json()


async def test_get_expired_scan_is_410(client: AsyncClient, session: AsyncSession) -> None:
    witness, doer = await members(session, 2)
    created = (await _post_scan(client, witness, await _qr_body(session, doer))).json()
    await session.execute(
        update(QrScan)
        .where(QrScan.id == uuid.UUID(created["scan_id"]))
        .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    await session.commit()
    response = await client.get(f"/api/scans/{created['scan_id']}", headers=witness.headers)
    assert response.status_code == 410
    assert response.json()["code"] == "scan_expired"


async def test_get_consumed_scan_reports_consumed(
    client: AsyncClient, session: AsyncSession
) -> None:
    witness, doer = await members(session, 2)
    s = await scan(session, witness, doer)
    await report(session, witness, s.scan_id)
    await session.commit()
    response = await client.get(f"/api/scans/{s.scan_id}", headers=witness.headers)
    assert response.status_code == 200
    assert response.json()["consumed"] is True


async def test_scan_rows_have_no_coordinates_in_response(
    client: AsyncClient, session: AsyncSession
) -> None:
    witness, doer = await members(session, 2)
    response = await _post_scan(client, witness, await _qr_body(session, doer))
    assert response.status_code == 201
    text = response.text
    assert "lat" not in text and "lng" not in text and "12.97" not in text
