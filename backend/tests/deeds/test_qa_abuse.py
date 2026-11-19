"""QA1: abuse cases from docs/M2-DESIGN.md §8: IDOR, replays, client-set server fields, the
manual-code brute-force defences and config tampering."""

import contextlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.security import constant_time_equal
from app.main import app
from app.modules.deeds import _edits, _invites, _qr_deeds, _scans
from app.modules.deeds.models import Deed, DeedStatus, Invite, InviteStatus, QrScan
from app.modules.identity import _qr, service
from app.modules.identity.models import DoerCode
from tests.deeds.helpers import (
    Member,
    code_for,
    deed_body,
    location,
    make_member,
    members,
    publish_limits,
    qr_window_seconds,
    scan,
    scan_and_report,
    token_for,
)
from tests.deeds.invite_helpers import accept, create_invite, invite_body, invite_row, withdraw

# --------------------------------------------------- IDOR: 404, identical to an unknown ID


def _same_404(a: Response, b: Response) -> None:
    assert a.status_code == b.status_code == 404, (a.text, b.text)
    assert a.json() == b.json()


@pytest.mark.usefixtures("invites_on")
async def test_every_owned_resource_gives_the_same_404_to_others_as_to_unknown_ids(
    client: AsyncClient, session: AsyncSession
) -> None:
    reporter, doer, stranger = await members(session, 3)
    qr_deed = await scan_and_report(session, reporter, doer)
    pending, _ = await create_invite(session, reporter)
    assert pending.invite is not None
    log = await client.post(
        "/api/me/self-logs",
        json={
            "category": "animals",
            "description": "Fed the street dogs near the park",
            "occurred_at": datetime.now(UTC).isoformat(),
        },
        headers=reporter.headers,
    )
    assert log.status_code == 201
    log_id = log.json()["id"]
    scanned = await scan(session, stranger, doer, east_m=10)
    await session.commit()
    unknown = uuid.uuid4()

    edit = {"description": "Somebody else's words entirely"}
    for intruder in (doer, stranger):
        for deed_id in (qr_deed.id, pending.id):
            _same_404(
                await client.patch(f"/api/deeds/{deed_id}", json=edit, headers=intruder.headers),
                await client.patch(f"/api/deeds/{unknown}", json=edit, headers=intruder.headers),
            )
            _same_404(
                await client.post(f"/api/deeds/{deed_id}/withdraw", headers=intruder.headers),
                await client.post(f"/api/deeds/{unknown}/withdraw", headers=intruder.headers),
            )
        _same_404(
            await client.post(
                f"/api/invites/{pending.invite.id}/reissue", headers=intruder.headers
            ),
            await client.post(f"/api/invites/{unknown}/reissue", headers=intruder.headers),
        )
        _same_404(
            await client.patch(f"/api/me/self-logs/{log_id}", json=edit, headers=intruder.headers),
            await client.patch(f"/api/me/self-logs/{unknown}", json=edit, headers=intruder.headers),
        )
        _same_404(
            await client.delete(f"/api/me/self-logs/{log_id}", headers=intruder.headers),
            await client.delete(f"/api/me/self-logs/{unknown}", headers=intruder.headers),
        )
    # Only the reporter or the doer may read a deed; a pending deed only its reporter.
    _same_404(
        await client.get(f"/api/deeds/{pending.id}", headers=doer.headers),
        await client.get(f"/api/deeds/{unknown}", headers=doer.headers),
    )
    _same_404(
        await client.get(f"/api/deeds/{qr_deed.id}", headers=stranger.headers),
        await client.get(f"/api/deeds/{unknown}", headers=stranger.headers),
    )
    # Someone else's scan can't be read or turned into a deed.
    _same_404(
        await client.get(f"/api/scans/{scanned.scan_id}", headers=reporter.headers),
        await client.get(f"/api/scans/{unknown}", headers=reporter.headers),
    )
    _same_404(
        await client.post("/api/deeds", json=deed_body(scanned.scan_id), headers=reporter.headers),
        await client.post("/api/deeds", json=deed_body(unknown), headers=reporter.headers),
    )

    # Nothing changed.
    for deed_id, status in ((qr_deed.id, DeedStatus.VERIFIED), (pending.id, DeedStatus.PENDING)):
        row = await session.get(Deed, deed_id, populate_existing=True)
        assert row is not None and row.status == status and row.last_edited_at is None
    scan_row = await session.get(QrScan, scanned.scan_id, populate_existing=True)
    assert scan_row is not None and scan_row.consumed_at is None


@pytest.mark.parametrize("status", [DeedStatus.REMOVED])
async def test_removed_deeds_cannot_be_edited_or_withdrawn(
    client: AsyncClient, session: AsyncSession, status: DeedStatus
) -> None:
    reporter, doer = await members(session, 2)
    deed = await scan_and_report(session, reporter, doer)
    await session.execute(update(Deed).where(Deed.id == deed.id).values(status=status))
    await session.commit()
    edit = await client.patch(
        f"/api/deeds/{deed.id}", json={"category": "community"}, headers=reporter.headers
    )
    assert edit.status_code == 409 and edit.json()["code"] == "deed_not_editable"
    gone = await client.post(f"/api/deeds/{deed.id}/withdraw", headers=reporter.headers)
    assert gone.status_code == 409 and gone.json()["code"] == "deed_not_withdrawable"


# --------------------------------------------------- replays


@pytest.mark.usefixtures("invites_on")
async def test_old_invite_token_after_reissue_cannot_be_accepted(
    client: AsyncClient, session: AsyncSession
) -> None:
    reporter, doer = await members(session, 2)
    created, old = await create_invite(session, reporter)
    assert created.invite is not None
    await session.commit()
    reissued = await client.post(
        f"/api/invites/{created.invite.id}/reissue", headers=reporter.headers
    )
    assert reissued.status_code == 200
    new = reissued.json()["url"].rsplit("/", 1)[1]

    stale = await client.post(f"/api/invites/by-token/{old}/accept", headers=doer.headers)
    assert stale.status_code == 404 and stale.json()["code"] == "invite_invalid"
    assert (await invite_row(session, created.id)).status == InviteStatus.PENDING
    fresh = await client.post(f"/api/invites/by-token/{new}/accept", headers=doer.headers)
    assert fresh.status_code == 200, fresh.text


@pytest.mark.usefixtures("invites_on")
async def test_accepted_invite_cannot_be_taken_over_by_another_member(
    client: AsyncClient, session: AsyncSession
) -> None:
    reporter, doer, thief = await members(session, 3)
    created, token = await create_invite(session, reporter)
    await session.commit()
    url = f"/api/invites/by-token/{token}/accept"
    assert (await client.post(url, headers=doer.headers)).status_code == 200
    replay = await client.post(url, headers=thief.headers)
    assert replay.status_code == 410 and replay.json()["code"] == "invite_used"
    deed = await session.get(Deed, created.id, populate_existing=True)
    assert deed is not None and deed.doer_user_id == doer.user_id
    # The reporter can't reissue an accepted invite to get a second acceptance either.
    assert created.invite is not None
    again = await client.post(f"/api/invites/{created.invite.id}/reissue", headers=reporter.headers)
    assert again.status_code == 409 and again.json()["code"] == "invite_not_pending"


async def test_reissue_boundaries_at_expiry_and_after_cancel(session: AsyncSession) -> None:
    reporter = await make_member(session)
    at = datetime(2026, 11, 1, 6, 0, tzinfo=UTC)
    created, _ = await create_invite(session, reporter, at=at)
    assert created.invite is not None
    expires = at + timedelta(hours=168)
    # One second before expiry: fine, and the expiry stays put.
    out = await _invites.reissue(
        session, reporter.user_id, created.invite.id, now=expires - timedelta(seconds=1)
    )
    assert out.expires_at == created.invite.expires_at
    # At expiry (before the job has run): refused.
    with pytest.raises(AppError) as exc:
        await _invites.reissue(session, reporter.user_id, created.invite.id, now=expires)
    assert exc.value.code == "invite_not_pending"

    other, _ = await create_invite(session, reporter, at=at + timedelta(minutes=1))
    assert other.invite is not None
    await withdraw(session, reporter, other.id, at=at + timedelta(minutes=2))
    with pytest.raises(AppError) as exc:
        await _invites.reissue(
            session, reporter.user_id, other.invite.id, now=at + timedelta(minutes=3)
        )
    assert exc.value.code == "invite_not_pending"


async def test_consumed_scan_stays_used_after_its_deed_is_withdrawn(
    client: AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The clock is frozen (QA, M3): 5 s into a 30 s QR window, at 12:00 IST. With the real clock
    # the "previous window" token below sometimes fell into the first scan's window (when the
    # test crossed a window boundary), or the IST day changed between the two scans.
    window = await qr_window_seconds(session)
    now = datetime(2026, 11, 20, 6, 30, 5, tzinfo=UTC)
    assert now.timestamp() % window == 5
    for module in (_scans, _qr_deeds, _edits):
        monkeypatch.setattr(module, "utcnow", lambda: now)
    witness, doer = await members(session, 2)
    s = await scan(session, witness, doer, at=now)
    await session.commit()
    first = await client.post("/api/deeds", json=deed_body(s.scan_id), headers=witness.headers)
    assert first.status_code == 201, first.text
    deed_id = first.json()["id"]
    assert (
        await client.post(f"/api/deeds/{deed_id}/withdraw", headers=witness.headers)
    ).status_code == 200
    replay = await client.post("/api/deeds", json=deed_body(s.scan_id), headers=witness.headers)
    assert replay.status_code == 409 and replay.json()["code"] == "scan_used"
    # And a fresh scan the same IST day is refused too: withdrawal can't retry the checks.
    # (A token from the previous window, still inside grace, so it isn't refused as the same
    # window.)
    earlier = now - timedelta(seconds=window)
    assert (
        _qr.current_window(window, earlier.timestamp())
        == _qr.current_window(window, now.timestamp()) - 1
    )
    rescan = await client.post(
        "/api/scans",
        json={"token": await token_for(session, doer, earlier), "location": location()},
        headers=witness.headers,
    )
    assert rescan.status_code == 409 and rescan.json()["code"] == "already_recognized_today"


async def test_same_token_replayed_by_the_same_witness_on_a_new_ist_day(
    session: AsyncSession,
) -> None:
    """A token minted at 23:59:40 IST is still inside grace at 00:00:20 IST (a new pair-day);
    only the per-window rule stops a second scan, and a third witness far away is stopped by
    the doer travel check."""
    witness, far_witness, doer = await members(session, 3)
    minted = datetime(2026, 11, 20, 18, 29, 40, tzinfo=UTC)
    await scan(session, witness, doer, at=minted + timedelta(seconds=5), minted_at=minted)
    with pytest.raises(AppError) as exc:
        await scan(session, witness, doer, at=minted + timedelta(seconds=40), minted_at=minted)
    assert exc.value.code == "already_scanned"
    with pytest.raises(AppError) as exc:
        await scan(
            session,
            far_witness,
            doer,
            at=minted + timedelta(seconds=30),
            minted_at=minted,
            north_m=15_000,
        )
    assert exc.value.code == "implausible_travel"


# --------------------------------------------------- client input can't set server fields


@pytest.mark.parametrize(
    "extra",
    [
        {"status": "verified"},
        {"doer_user_id": str(uuid.uuid4())},
        {"counts_for_points": True},
        {"prize_rank_eligible": True},
        {"reporter_user_id": str(uuid.uuid4())},
        {"verified_at": "2026-11-15T10:00:00+05:30"},
    ],
)
async def test_invite_create_rejects_server_controlled_fields(
    client: AsyncClient, session: AsyncSession, extra: dict[str, Any]
) -> None:
    reporter = await make_member(session)
    response = await client.post("/api/deeds", json=invite_body(**extra), headers=reporter.headers)
    assert response.status_code == 422, response.text


@pytest.mark.usefixtures("invites_on")
async def test_scan_and_accept_ignore_a_client_supplied_doer(
    client: AsyncClient, session: AsyncSession
) -> None:
    witness, doer, decoy = await members(session, 3)
    body = {
        "token": await token_for(session, doer),
        "location": location(),
        "doer_user_id": str(decoy.user_id),
        "witness_user_id": str(decoy.user_id),
    }
    response = await client.post("/api/scans", json=body, headers=witness.headers)
    assert response.status_code == 201
    row = await session.get(QrScan, uuid.UUID(response.json()["scan_id"]))
    assert row is not None
    assert (row.doer_user_id, row.witness_user_id) == (doer.user_id, witness.user_id)

    reporter = await make_member(session)
    created, token = await create_invite(session, reporter)
    await session.commit()
    accepted = await client.post(
        f"/api/invites/by-token/{token}/accept",
        json={"doer_user_id": str(decoy.user_id), "status": "verified"},
        headers=doer.headers,
    )
    assert accepted.status_code == 200
    deed = await session.get(Deed, created.id, populate_existing=True)
    assert deed is not None and deed.doer_user_id == doer.user_id


# --------------------------------------------------- manual-code brute force (§8, D11)


async def test_lookalike_spellings_share_the_per_target_counter(
    client: AsyncClient, session: AsyncSession
) -> None:
    """Every spelling that resolves to the same doer (case, prefix, spaces, dashes, O for 0,
    I/L for 1) must hit one ``manual:t`` counter, or an attacker gets fresh guesses per
    spelling."""
    doer = await make_member(session)
    await session.execute(
        update(DoerCode).where(DoerCode.user_id == doer.user_id).values(short_id="10K1M0Q")
    )
    await session.commit()
    doer = Member(**{**doer.__dict__, "short_id": "10K1M0Q"})
    spellings = ["10K1M0Q", "gd-10k1m0q", "GD 10K1 M0Q", "IOK1MOQ", "lok-lmoq"]
    for spelling in spellings:
        assert service.normalize_short_id(spelling) == "10K1M0Q", spelling
    await publish_limits(session, rate_manual_target=f"{len(spellings)}/900")
    right = await code_for(session, doer)
    wrong = f"{(int(right) + 1) % 1_000_000:06d}"
    guessers = await members(session, len(spellings) + 1)
    for spelling, who in zip(spellings, guessers, strict=False):
        r = await client.post(
            "/api/scans",
            json={"short_id": spelling, "code": wrong, "location": location()},
            headers=who.headers,
        )
        assert r.status_code == 404, r.text
    limited = await client.post(
        "/api/scans",
        json={"short_id": "lOk lmOq", "code": right, "location": location()},
        headers=guessers[-1].headers,
    )
    assert limited.status_code == 429 and limited.json()["code"] == "rate_limited"


async def test_manual_code_is_compared_against_every_window_even_on_a_match(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No early exit: the work is the same whether the code matches the current window, an
    older one, nothing, or the ID is unknown (timing doesn't reveal which)."""
    doer = await make_member(session)
    calls: list[int] = []
    real = constant_time_equal

    def spy(a: str, b: str) -> bool:
        calls.append(1)
        return real(a, b)

    monkeypatch.setattr(service, "constant_time_equal", spy)
    now = datetime.now(UTC).timestamp()
    window = _qr.current_window(30, now)
    current = _qr.manual_code(doer.short_id, doer.key_version, window)
    previous = _qr.manual_code(doer.short_id, doer.key_version, window - 1)
    counts = []
    for short_id, code in (
        (doer.short_id, current),
        (doer.short_id, previous),
        (doer.short_id, "000000" if "000000" not in (current, previous) else "999999"),
        ("ZZZZZZZ" if doer.short_id != "ZZZZZZZ" else "YYYYYYY", current),
    ):
        calls.clear()
        with contextlib.suppress(AppError):
            await service.resolve_manual_code(session, short_id, code, now=now)
        counts.append(len(calls))
    assert counts == [2, 2, 2, 2]  # grace_windows=1: the current and one earlier window


# --------------------------------------------------- config tampering (§8)


def test_no_http_endpoint_can_change_config() -> None:
    """Config changes only through a migration or ``publish``; any route that mentions config
    (e.g. the public auth config) may only read."""
    for route in app.routes:
        path = getattr(route, "path", "")
        if "config" in path.lower():
            assert getattr(route, "methods", set()) <= {"GET", "HEAD"}, path


async def test_a_suspended_witness_cannot_scan_or_report(
    client: AsyncClient, session: AsyncSession
) -> None:
    from app.modules.identity.models import User, UserStatus

    witness, doer = await members(session, 2)
    s = await scan(session, witness, doer)
    await session.execute(
        update(User).where(User.id == witness.user_id).values(status=UserStatus.SUSPENDED)
    )
    await session.commit()
    r = await client.post("/api/deeds", json=deed_body(s.scan_id), headers=witness.headers)
    assert r.status_code in (401, 403), r.text
    row = await session.get(QrScan, s.scan_id, populate_existing=True)
    assert row is not None and row.consumed_at is None


async def test_invite_with_a_shared_device_header_is_recorded_for_fraud_checks(
    session: AsyncSession,
) -> None:
    reporter, doer = await members(session, 2)
    from tests.deeds.invite_helpers import share_device

    await share_device(session, reporter, device="kitchen-tablet")
    created, token = await create_invite(session, reporter)
    await accept(session, token, doer, device_id="kitchen-tablet")
    invite: Invite = await invite_row(session, created.id)
    assert invite.shared_device is True
