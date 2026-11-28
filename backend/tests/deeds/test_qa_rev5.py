"""QA of revision 5 (docs/M2-DESIGN.md §0.3 D19-D27, §7 "Monthly limits" and "Doer view", §8
"Revision 5 checks"), on top of the developer tests.

- The IST month edge on every path: the locked pair check on deed create, invite acceptance
  (to the microsecond of ``reported_at``), the options count, the scan read-back, and deeds in
  the first 5.5 hours of an IST month (still the previous month in UTC).
- ``month_resets_at`` over the API across New Year, on all three endpoints that return it.
- Config changes apply on the next request (no restart, no cache), in both directions.
- Bypass attempts: switching between the QR and the typed ID + code, a rotated QR key, and
  withdrawing real reports to free pair slots.
- Error bodies: ``doer`` only on the two scan-time errors; never for a suspended or revoked
  doer, a shared phone, ``already_scanned``, the total limit, or the deed-create and accept
  paths. A static check that no other code passes ``extra``.
- D24 beyond list and detail: edit/withdraw/scan endpoints, the public counts and profile,
  the doer's ``same_act.report_count``, and the ``deeds.service`` views other modules will use.
- Migration 0008: downgrade, re-upgrade, idempotence, and building on a hand-published version.
"""

import ast
import importlib.util
import json
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from httpx import AsyncClient
from sqlalchemy import Connection, create_engine, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import AppError
from app.modules.deeds import _reads, _scans
from app.modules.deeds import service as deeds_service
from app.modules.deeds._limits import DeedLimits, load_limits
from app.modules.deeds.models import Deed, DeedStatus, InviteStatus, QrScan
from app.modules.identity import service as identity_service
from app.modules.identity.models import DoerCode, User, UserStatus
from tests.deeds.helpers import (
    Member,
    code_for,
    count,
    deed_body,
    location,
    make_member,
    members,
    publish_limits,
    report,
    scan,
    scan_and_report,
    seed_deeds,
    token_for,
)
from tests.deeds.invite_helpers import accept, create_invite, invite_row, withdraw

BACKEND_DIR = Path(__file__).resolve().parents[2]
PAIR = 15  # seeded pair limit (D19); test_config.py checks the row
TOTAL = 150
OCT_1 = datetime(2026, 10, 1, 6, 0, tzinfo=UTC)  # 11:30 IST
OCT_20 = datetime(2026, 10, 20, 6, 0, tzinfo=UTC)
NOV_1_IST = datetime(2026, 10, 31, 18, 30, tzinfo=UTC)  # 00:00 IST on 1 November
NOV_2 = datetime(2026, 11, 2, 6, 0, tzinfo=UTC)
DAY = timedelta(days=1)
_US = timedelta(microseconds=1)


# ======================================================================= helpers


async def _expect(coro: Any, code: str, status: int = 429) -> AppError:
    with pytest.raises(AppError) as exc:
        await coro
    assert exc.value.code == code, exc.value
    assert exc.value.status_code == status
    return exc.value


async def _seed_now(
    session: AsyncSession, witness: Member, doer: Member | None, n: int, day_offset: int = 0
) -> None:
    """``n`` deeds reported in the last moments (this IST month), committed, on unrelated
    report days (the real month may be too young for one per day)."""
    step = timedelta(milliseconds=50)
    await seed_deeds(
        session,
        witness,
        n,
        start=datetime.now(UTC) - (n + 1) * step,
        step=step,
        doer=doer,
        unrelated_days=True,
        day_offset=day_offset,
    )
    await session.commit()


async def _post_scan(client: AsyncClient, witness: Member, body: dict[str, Any]) -> Any:
    return await client.post("/api/scans", json=body, headers=witness.headers)


async def _qr(session: AsyncSession, doer: Member) -> dict[str, Any]:
    return {"token": await token_for(session, doer), "location": location()}


async def _manual(session: AsyncSession, doer: Member) -> dict[str, Any]:
    return {
        "short_id": doer.short_id,
        "code": await code_for(session, doer),
        "location": location(),
    }


async def _scans_of(session: AsyncSession, witness: Member, doer: Member) -> int:
    return await count(
        session,
        QrScan,
        QrScan.witness_user_id == witness.user_id,
        QrScan.doer_user_id == doer.user_id,
    )


def _no_doer(response: Any) -> None:
    assert set(response.json()) == {"detail", "code"}, response.text


# ======================================================================= the IST month, every path


async def test_locked_pair_check_on_deed_create_follows_the_ist_month(
    session: AsyncSession,
) -> None:
    """A scan at 23:58 IST on 31 October with 14 about Ahmed passes. The 15th then lands
    elsewhere: sending at 23:59:59.999999 IST is refused (October is full) and the scan stays
    unused; the same scan sent at 00:00:00 IST on 1 November counts for November."""
    witness, ahmed = await make_member(session), await make_member(session)
    await seed_deeds(session, witness, PAIR - 1, start=OCT_1, step=DAY, doer=ahmed)
    s = await scan(session, witness, ahmed, at=NOV_1_IST - timedelta(minutes=2))
    assert s.pair_reports_remaining == 1
    await seed_deeds(
        session, witness, 1, start=OCT_20, doer=ahmed, status=DeedStatus.WITHDRAWN
    )  # the 15th, on 20 October
    await _expect(report(session, witness, s.scan_id, at=NOV_1_IST - _US), "pair_monthly_limit")
    row = await session.get(QrScan, s.scan_id, populate_existing=True)
    assert row is not None and row.consumed_at is None
    out = await report(session, witness, s.scan_id, at=NOV_1_IST)
    assert out.status == "verified" and out.reported_at == NOV_1_IST


async def test_deeds_in_the_first_hours_of_an_ist_month_count_for_that_month(
    session: AsyncSession,
) -> None:
    """00:00-05:29 IST on 1 November is still 31 October in UTC. Fifteen reports in those
    hours fill November's pair, not October's."""
    witness, ahmed = await make_member(session), await make_member(session)
    await seed_deeds(
        session,
        witness,
        PAIR,
        start=NOV_1_IST + timedelta(minutes=1),
        step=timedelta(minutes=1),
        doer=ahmed,
        unrelated_days=True,
    )
    seeded = list(
        await session.scalars(
            select(Deed.reported_at).where(Deed.reporter_user_id == witness.user_id)
        )
    )
    assert len(seeded) == PAIR
    assert all(at.astimezone(UTC).date().day == 31 for at in seeded)  # UTC: still October
    october = await scan(session, witness, ahmed, at=NOV_1_IST - timedelta(minutes=30))
    assert october.pair_reports_remaining == PAIR  # October is empty
    error = await _expect(scan(session, witness, ahmed, at=NOV_2), "pair_monthly_limit")
    assert error.extra["doer"]["handle"] == ahmed.handle


@pytest.mark.usefixtures("invites_on")
async def test_accept_pair_month_follows_reported_at_to_the_microsecond(
    session: AsyncSession,
) -> None:
    """October's pair is full. An invite created at 23:59:59.999999 IST on 31 October counts in
    October (refused on 2 November); one created at 00:00:00 IST on 1 November (still 31
    October in UTC) counts in November (accepted)."""
    reporter, doer = await make_member(session), await make_member(session)
    await seed_deeds(session, reporter, PAIR, start=OCT_1, step=DAY, doer=doer)
    late, late_token = await create_invite(session, reporter, at=NOV_1_IST - _US)
    _, early_token = await create_invite(session, reporter, at=NOV_1_IST)
    await _expect(accept(session, late_token, doer, at=NOV_2), "pair_monthly_limit")
    assert (await invite_row(session, late.id)).status == InviteStatus.PENDING
    out = await accept(session, early_token, doer, at=NOV_2)
    assert out.status == "verified" and out.reported_at == NOV_1_IST


async def test_options_remaining_flips_at_ist_midnight(session: AsyncSession) -> None:
    me = await make_member(session)
    await seed_deeds(session, me, 10, start=OCT_20, step=timedelta(minutes=1))
    _, limits = await load_limits(session)
    before = await _reads.options(session, me.user_id, limits=limits, now=NOV_1_IST - _US)
    after = await _reads.options(session, me.user_id, limits=limits, now=NOV_1_IST)
    assert before.reports_remaining_this_month == TOTAL - 10
    assert after.reports_remaining_this_month == TOTAL
    assert before.month_resets_at == after.month_resets_at - timedelta(days=30)


async def test_scan_read_back_recomputes_the_pair_across_ist_midnight(
    session: AsyncSession,
) -> None:
    """``GET /api/scans/{id}`` recomputes at read time: a scan made at 23:58 IST with 14 used
    shows 1 left; read again at 00:01 IST it shows the new month's 15 and the next reset."""
    witness, ahmed = await make_member(session), await make_member(session)
    await seed_deeds(session, witness, PAIR - 1, start=OCT_1, step=DAY, doer=ahmed)
    s = await scan(session, witness, ahmed, at=NOV_1_IST - timedelta(minutes=2))
    assert s.pair_reports_remaining == 1
    _, limits = await load_limits(session)
    detail = await _scans.get_scan(
        session, witness.user_id, s.scan_id, limits=limits, now=NOV_1_IST + timedelta(minutes=1)
    )
    assert detail.pair_reports_remaining == PAIR
    assert detail.month_resets_at == datetime(2026, 12, 1, tzinfo=identity_service.LAUNCH_TZ)


# ======================================================================= month_resets_at, API


@pytest.mark.parametrize(
    ("now", "resets"),
    [
        (datetime(2026, 12, 15, 6, 30, tzinfo=UTC), "2027-01-01T00:00:00+05:30"),
        # 23:59:59 IST on 31 December, and 00:00 IST on 1 January (still 2026 in UTC).
        (datetime(2026, 12, 31, 18, 29, 59, tzinfo=UTC), "2027-01-01T00:00:00+05:30"),
        (datetime(2026, 12, 31, 18, 30, tzinfo=UTC), "2027-02-01T00:00:00+05:30"),
    ],
)
async def test_api_month_resets_at_across_new_year(
    client: AsyncClient,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    now: datetime,
    resets: str,
) -> None:
    monkeypatch.setattr(_scans, "utcnow", lambda: now)
    monkeypatch.setattr(_reads, "utcnow", lambda: now)
    witness, doer = await make_member(session), await make_member(session)
    body = {"token": await token_for(session, doer, at=now), "location": location()}
    created = await _post_scan(client, witness, body)
    assert created.status_code == 201, created.text
    assert created.json()["month_resets_at"] == resets
    detail = await client.get(f"/api/scans/{created.json()['scan_id']}", headers=witness.headers)
    assert detail.status_code == 200, detail.text
    assert detail.json()["month_resets_at"] == resets
    options = await client.get("/api/deeds/options", headers=witness.headers)
    assert options.json()["month_resets_at"] == resets


# ======================================================================= config, no restart


async def test_api_pair_limit_change_applies_to_the_next_request(
    client: AsyncClient, session: AsyncSession
) -> None:
    witness, ahmed = await make_member(session), await make_member(session)
    await _seed_now(session, witness, ahmed, 1)
    await publish_limits(session, reporter_doer_monthly_limit=1)
    refused = await _post_scan(client, witness, await _qr(session, ahmed))
    assert refused.status_code == 429 and refused.json()["code"] == "pair_monthly_limit"
    options = (await client.get("/api/deeds/options", headers=witness.headers)).json()
    assert options["reporter_doer_monthly_limit"] == 1

    await publish_limits(session, reporter_doer_monthly_limit=2)
    allowed = await _post_scan(client, witness, await _qr(session, ahmed))
    assert allowed.status_code == 201, allowed.text
    assert allowed.json()["pair_reports_remaining"] == 1
    options = (await client.get("/api/deeds/options", headers=witness.headers)).json()
    assert options["reporter_doer_monthly_limit"] == 2


async def test_api_total_limit_change_applies_to_the_next_request(
    client: AsyncClient, session: AsyncSession
) -> None:
    witness, doer = await make_member(session), await make_member(session)
    await _seed_now(session, witness, None, 2)
    await publish_limits(session, reporter_monthly_limit=2)
    refused = await _post_scan(client, witness, await _qr(session, doer))
    assert refused.status_code == 429 and refused.json()["code"] == "monthly_report_limit"
    _no_doer(refused)
    options = (await client.get("/api/deeds/options", headers=witness.headers)).json()
    assert options["reports_remaining_this_month"] == 0

    await publish_limits(session, reporter_monthly_limit=3)
    options = (await client.get("/api/deeds/options", headers=witness.headers)).json()
    assert options["reports_remaining_this_month"] == 1
    allowed = await _post_scan(client, witness, await _qr(session, doer))
    assert allowed.status_code == 201, allowed.text
    sent = await client.post(
        "/api/deeds", json=deed_body(allowed.json()["scan_id"]), headers=witness.headers
    )
    assert sent.status_code == 201, sent.text


# ======================================================================= bypass attempts


async def test_qr_and_manual_code_share_one_pair_count(session: AsyncSession) -> None:
    """The pair counts deeds, not methods: 13 seeded, the 14th by QR, the 15th by typed code;
    the 16th is refused by both, and neither records a scan."""
    witness, ahmed = await make_member(session), await make_member(session)
    await seed_deeds(session, witness, PAIR - 2, start=OCT_1, step=DAY, doer=ahmed)
    by_qr = await scan(session, witness, ahmed, at=OCT_20)
    assert by_qr.pair_reports_remaining == 2
    await report(session, witness, by_qr.scan_id, at=OCT_20)
    typed = await scan(session, witness, ahmed, at=OCT_20 + DAY, manual=True)
    assert typed.pair_reports_remaining == 1
    await report(session, witness, typed.scan_id, at=OCT_20 + DAY)
    before = await _scans_of(session, witness, ahmed)
    for manual in (False, True):
        await _expect(
            scan(session, witness, ahmed, at=OCT_20 + 2 * DAY, manual=manual),
            "pair_monthly_limit",
        )
    assert await _scans_of(session, witness, ahmed) == before


@pytest.mark.parametrize("first_manual", [False, True])
async def test_switching_method_does_not_reopen_the_pair_day(
    session: AsyncSession, first_manual: bool
) -> None:
    witness, ahmed = await make_member(session), await make_member(session)
    await scan(session, witness, ahmed, at=OCT_20, manual=first_manual)
    later = OCT_20 + timedelta(seconds=61)  # a later token window, the same IST day
    await _expect(
        scan(session, witness, ahmed, at=later, manual=not first_manual),
        "already_recognized_today",
        status=409,
    )


async def test_rotating_the_qr_key_does_not_reset_the_pair(
    client: AsyncClient, session: AsyncSession
) -> None:
    """The pair is keyed by the doer's account, not their code. After the doer rotates, the
    new token and code are still refused; the old token is just invalid (no ``doer``)."""
    witness, ahmed = await make_member(session), await make_member(session)
    await _seed_now(session, witness, ahmed, PAIR)
    old = await token_for(session, ahmed)
    rotated = await client.post("/api/users/me/qr/rotate", headers=ahmed.headers)
    assert rotated.status_code == 200, rotated.text
    fresh = rotated.json()
    for body in (
        {"token": fresh["token"], "location": location()},
        {"short_id": fresh["short_id"], "code": fresh["manual_code"], "location": location()},
    ):
        response = await _post_scan(client, witness, body)
        assert response.status_code == 429, response.text
        assert response.json()["code"] == "pair_monthly_limit"
    stale = await _post_scan(client, witness, {"token": old, "location": location()})
    assert stale.status_code == 404 and stale.json()["code"] == "qr_invalid"
    _no_doer(stale)


async def test_withdrawing_real_reports_frees_no_pair_slot(session: AsyncSession) -> None:
    """Fifteen real QR reports about Ahmed on 1-15 October, every one withdrawn: the pair is
    still full for both methods, and the total still counts all fifteen."""
    witness, ahmed = await make_member(session), await make_member(session)
    deeds = [
        await scan_and_report(session, witness, ahmed, at=OCT_1 + i * DAY) for i in range(PAIR)
    ]
    for i, d in enumerate(deeds):
        # Each inside its 24 h withdrawal window (M4 D64).
        out = await withdraw(session, witness, d.id, at=OCT_1 + i * DAY + timedelta(hours=1))
        assert out.status == "withdrawn"
    for manual in (False, True):
        await _expect(scan(session, witness, ahmed, at=OCT_20, manual=manual), "pair_monthly_limit")
    _, limits = await load_limits(session)
    options = await _reads.options(session, witness.user_id, limits=limits, now=OCT_20)
    assert options.reports_remaining_this_month == TOTAL - PAIR


# ======================================================================= error bodies (D27)


async def test_suspended_or_revoked_doer_never_reveals_a_full_pair(
    client: AsyncClient, session: AsyncSession
) -> None:
    witness = await make_member(session)
    suspended, revoked = await members(session, 2)
    for doer in (suspended, revoked):
        await _seed_now(session, witness, doer, PAIR, day_offset=0 if doer is suspended else 100)
    bodies = [
        await _qr(session, suspended),
        await _manual(session, suspended),
        await _qr(session, revoked),
        await _manual(session, revoked),
    ]
    await session.execute(
        update(User).where(User.id == suspended.user_id).values(status=UserStatus.SUSPENDED)
    )
    await session.execute(
        update(DoerCode)
        .where(DoerCode.user_id == revoked.user_id)
        .values(revoked_at=datetime.now(UTC))
    )
    await session.commit()
    for body in bodies:
        response = await _post_scan(client, witness, body)
        assert response.status_code == 404, response.text
        assert response.json()["code"] in ("qr_invalid", "doer_code_invalid")
        _no_doer(response)


async def test_shared_phone_at_a_full_pair_is_cannot_verify_self_without_doer(
    client: AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    witness, ahmed = await make_member(session), await make_member(session)
    await _seed_now(session, witness, ahmed, PAIR)
    real = identity_service.phone_hashes

    async def shared(s: AsyncSession, user_id: uuid.UUID) -> set[str]:
        return await real(s, user_id) | {"shared-hash"}

    monkeypatch.setattr(identity_service, "phone_hashes", shared)
    for body in (await _qr(session, ahmed), await _manual(session, ahmed)):
        response = await _post_scan(client, witness, body)
        assert response.status_code == 409, response.text
        assert response.json()["code"] == "cannot_verify_self"
        _no_doer(response)


async def test_total_limit_is_answered_before_the_code_is_resolved(
    client: AsyncClient, session: AsyncSession
) -> None:
    """At both limits, a right code and a wrong code get the same ``monthly_report_limit``
    without ``doer``: the total says nothing about whose code it is."""
    witness, ahmed = await make_member(session), await make_member(session)
    await _seed_now(session, witness, ahmed, PAIR)
    await _seed_now(session, witness, None, TOTAL - PAIR)
    right = await _manual(session, ahmed)
    wrong = {**right, "code": f"{(int(right['code']) + 1) % 1_000_000:06d}"}
    answers = []
    for body in (await _qr(session, ahmed), right, wrong):
        response = await _post_scan(client, witness, body)
        assert response.status_code == 429, response.text
        assert response.json()["code"] == "monthly_report_limit"
        _no_doer(response)
        answers.append(response.json())
    assert answers[1] == answers[2]


async def test_already_scanned_never_names_the_doer(
    client: AsyncClient, session: AsyncSession
) -> None:
    witness, ahmed = await make_member(session), await make_member(session)
    body = await _qr(session, ahmed)
    first = await _post_scan(client, witness, body)
    assert first.status_code == 201, first.text
    again = await _post_scan(client, witness, body)
    assert again.status_code == 409 and again.json()["code"] == "already_scanned"
    _no_doer(again)


async def test_deed_create_already_recognized_today_has_no_doer(session: AsyncSession) -> None:
    """D27: ``doer`` is for the scan-time errors only. Two scans straddling IST midnight (one
    per IST day), both sent on the new day: the second hits ``uq_deeds_pair_day``."""
    witness, ahmed = await make_member(session), await make_member(session)
    late = await scan(session, witness, ahmed, at=datetime(2026, 10, 20, 18, 25, tzinfo=UTC))
    early = await scan(session, witness, ahmed, at=datetime(2026, 10, 20, 18, 31, tzinfo=UTC))
    sent = datetime(2026, 10, 20, 18, 32, tzinfo=UTC)
    await report(session, witness, early.scan_id, at=sent)
    error = await _expect(
        report(session, witness, late.scan_id, at=sent), "already_recognized_today", status=409
    )
    assert error.extra == {}


@pytest.mark.usefixtures("invites_on")
async def test_accept_already_recognized_today_has_no_doer(session: AsyncSession) -> None:
    """The reporter already recorded the accepter on the invite's report day."""
    reporter, doer = await make_member(session), await make_member(session)
    await scan_and_report(session, reporter, doer, at=OCT_20)
    _, token = await create_invite(session, reporter, at=OCT_20 + timedelta(hours=1))
    error = await _expect(
        accept(session, token, doer, at=OCT_20 + timedelta(hours=2)),
        "already_recognized_today",
        status=409,
    )
    assert error.extra == {}


@pytest.mark.usefixtures("invites_on")
async def test_accept_pair_monthly_limit_has_no_doer(session: AsyncSession) -> None:
    reporter, doer = await make_member(session), await make_member(session)
    await seed_deeds(session, reporter, PAIR, start=OCT_1, step=DAY, doer=doer)
    _, token = await create_invite(session, reporter, at=OCT_20)
    error = await _expect(
        accept(session, token, doer, at=OCT_20 + timedelta(hours=1)), "pair_monthly_limit"
    )
    assert error.extra == {}


def _calls_with_extra(path: Path) -> list[tuple[str, int]]:
    """(function called, line) for every call in ``path`` that passes ``extra=``, excluding
    pydantic's ``ConfigDict(extra=...)`` / ``SettingsConfigDict(extra=...)``."""
    found = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Call) and any(k.arg == "extra" for k in node.keywords):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "?")
            if name not in ("ConfigDict", "SettingsConfigDict"):
                found.append((name, node.lineno))
    return found


def test_only_the_scan_service_passes_extra() -> None:
    """D27 allows ``extra`` on exactly two scan-time errors. Every ``extra=`` in the app is in
    ``_scans.py`` (the two errors) or the pass-through in ``pair_limit_reached``, plus the
    reporter limit's 429 and the profile-picture cooldown 409 (``available_at`` only)."""
    uses: dict[str, list[str]] = {}
    for path in (BACKEND_DIR / "app").rglob("*.py"):
        calls = _calls_with_extra(path)
        if calls:
            uses[path.relative_to(BACKEND_DIR).as_posix()] = sorted(n for n, _ in calls)
    assert uses == {
        "app/modules/deeds/_scans.py": ["Conflict", "pair_limit_reached"],
        "app/modules/deeds/_reporter_limit.py": ["TooManyRequests"],
        "app/modules/media/_avatars.py": ["Conflict"],
    }
    # The profile-picture cooldown 409 (M6A Q95) passes exactly ``available_at``.
    avatars = (BACKEND_DIR / "app/modules/media/_avatars.py").read_text(encoding="utf-8")
    assert avatars.count("extra=") == 1
    assert 'extra={"available_at": until.isoformat()}' in avatars


# ======================================================================= D24, other endpoints


async def _act(
    session: AsyncSession, doer: Member, n: int, at: datetime = OCT_20
) -> tuple[list[Member], list[Any]]:
    witnesses = await members(session, n)
    deeds = [
        await scan_and_report(session, w, doer, at=at + timedelta(minutes=i), east_m=i * 5)
        for i, w in enumerate(witnesses)
    ]
    return witnesses, deeds


async def test_doer_cannot_reach_a_deed_through_edit_withdraw_or_scan(
    client: AsyncClient, session: AsyncSession
) -> None:
    """Every deed endpoint gives the doer of a withdrawn (or still verified) deed the same 404
    as a stranger, and the scan of them is the witness's alone."""
    doer, stranger = await make_member(session), await make_member(session)
    now = datetime.now(UTC) - timedelta(minutes=10)
    witnesses, deeds = await _act(session, doer, 2, at=now)
    await withdraw(session, witnesses[0], deeds[0].id)
    await session.commit()
    for deed in deeds:
        for method, url, body in (
            ("PATCH", f"/api/deeds/{deed.id}", {"description": "Something else entirely"}),
            ("POST", f"/api/deeds/{deed.id}/withdraw", None),
        ):
            as_doer = await client.request(method, url, json=body, headers=doer.headers)
            as_stranger = await client.request(method, url, json=body, headers=stranger.headers)
            assert as_doer.status_code == as_stranger.status_code == 404, as_doer.text
            assert as_doer.json() == as_stranger.json()
    scan_id = await session.scalar(
        select(QrScan.id).where(QrScan.doer_user_id == doer.user_id).limit(1)
    )
    response = await client.get(f"/api/scans/{scan_id}", headers=doer.headers)
    assert response.status_code == 404 and response.json()["code"] == "scan_not_found"


async def test_public_counts_and_profile_show_no_non_verified_deed(
    client: AsyncClient, session: AsyncSession
) -> None:
    doer = await make_member(session)
    witnesses, deeds = await _act(session, doer, 5)
    await withdraw(session, witnesses[0], deeds[0].id, at=OCT_20 + timedelta(hours=1))
    for deed, status in zip(
        deeds[1:4], (DeedStatus.REJECTED, DeedStatus.REMOVED, DeedStatus.UNVERIFIED), strict=True
    ):
        await session.execute(update(Deed).where(Deed.id == deed.id).values(status=status))
    await session.commit()
    counts = await client.get(f"/api/users/{doer.handle}/deed-counts")
    assert counts.json() == {"verified": 1}
    profile = await client.get(f"/api/users/{doer.handle}")
    assert profile.status_code == 200
    assert not {k for k in profile.json() if "deed" in k or "count" in k}


async def test_doers_report_count_drops_when_another_report_is_withdrawn(
    client: AsyncClient, session: AsyncSession
) -> None:
    doer = await make_member(session)
    witnesses, deeds = await _act(session, doer, 3, at=datetime.now(UTC) - timedelta(minutes=10))
    await withdraw(session, witnesses[0], deeds[0].id)
    await session.commit()
    seen = await client.get(f"/api/deeds/{deeds[1].id}", headers=doer.headers)
    assert seen.status_code == 200
    assert seen.json()["same_act"]["report_count"] == 2
    listed = await client.get("/api/me/deeds", params={"role": "doer"}, headers=doer.headers)
    assert {d["id"] for d in listed.json()["items"]} == {str(deeds[1].id), str(deeds[2].id)}


async def test_service_views_for_other_modules_exclude_withdrawn_deeds(
    session: AsyncSession,
) -> None:
    """M3's feed and profile will read ``deeds.service``; its defaults must hide the same deeds
    the doer can't see (withdrawn here)."""
    doer = await make_member(session)
    witnesses, deeds = await _act(session, doer, 3)
    await withdraw(session, witnesses[0], deeds[0].id, at=OCT_20 + timedelta(hours=1))
    views = await deeds_service.deeds_for_doer(session, doer.user_id)
    assert {v.id for v in views} == {deeds[1].id, deeds[2].id}
    view = await deeds_service.get_deed(session, deeds[1].id)
    assert view is not None and view.same_act_group_id is not None
    group_id = view.same_act_group_id
    group = await deeds_service.same_act_group_view(session, group_id)
    assert group is not None
    assert set(group.verified_deed_ids) == {deeds[1].id, deeds[2].id}
    assert group.verified_count == 2


# ======================================================================= migration 0008


def _load_migration() -> ModuleType:
    path = BACKEND_DIR / "alembic" / "versions" / "2026_11_17_0000-0008_deeds_v3.py"
    spec = importlib.util.spec_from_file_location("migration_0008", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def sync_conn(migrated_db: None) -> Iterator[Connection]:
    """A plain (sync) connection in a transaction that is always rolled back, so the migration
    functions can run through ``alembic.op`` without touching the shared test database."""
    engine = create_engine(get_settings().database_url)
    try:
        with engine.connect() as conn:
            trans = conn.begin()
            try:
                yield conn
            finally:
                trans.rollback()
    finally:
        engine.dispose()


def _latest(conn: Connection) -> tuple[int, dict[str, Any]]:
    row = conn.execute(
        text(
            "SELECT version, values FROM config_versions WHERE namespace = 'deeds' "
            "ORDER BY version DESC LIMIT 1"
        )
    ).one()
    return int(row[0]), dict(row[1])


def _run(conn: Connection, module: ModuleType, step: str) -> None:
    with Operations.context(MigrationContext.configure(conn)):
        getattr(module, step)()


# Keys added by migrations after 0008; the 0008 rollback rightly leaves them alone.
LATER_KEYS = {
    "rate_manual_target_daily",  # 0009
    "area_fallback_m",  # 0013
    "reporter_monthly_warn_at",  # 0018
    "reporter_doer_monthly_warn_at",  # 0018
    "withdraw_window_hours",  # 0019
    "fallback_region_code",  # 0019
}
# Keys that 0008 adds but a later migration removed again (0013 moved ``photos_enabled`` to the
# ``media`` namespace). Re-running 0008 on top of head brings them back, so they are compared
# separately and dropped before validating against the current DeedLimits.
REMOVED_LATER = {"photos_enabled"}


def _without(values: dict[str, Any], keys: set[str]) -> dict[str, Any]:
    return {k: v for k, v in values.items() if k not in keys}


def test_migration_0008_downgrade_and_upgrade_round_trip(sync_conn: Connection) -> None:
    m = _load_migration()
    version, v3 = _latest(sync_conn)
    DeedLimits.model_validate(v3)
    v2 = sync_conn.execute(
        text("SELECT values FROM config_versions WHERE namespace = 'deeds' AND version = 2")
    ).scalar_one()

    _run(sync_conn, m, "downgrade")
    down_version, down = _latest(sync_conn)
    assert down_version == version + 1  # appended, history kept
    # Exactly the pre-0008 values, apart from keys that later migrations (0009+) appended on top.
    assert {k: v for k, v in down.items() if k not in LATER_KEYS} == v2
    _run(sync_conn, m, "downgrade")  # already down: no new row
    assert _latest(sync_conn)[0] == down_version

    _run(sync_conn, m, "upgrade")
    up_version, up = _latest(sync_conn)
    assert up_version == down_version + 1
    assert _without(up, REMOVED_LATER) == v3
    assert up["photos_enabled"] is False
    DeedLimits.model_validate(_without(up, REMOVED_LATER))
    _run(sync_conn, m, "upgrade")  # idempotent
    assert _latest(sync_conn)[0] == up_version
    # Nothing was rewritten.
    n = sync_conn.execute(
        text("SELECT count(*) FROM config_versions WHERE namespace = 'deeds'")
    ).scalar_one()
    assert n == up_version


def test_migration_0008_upgrade_builds_on_a_hand_published_version(
    sync_conn: Connection,
) -> None:
    m = _load_migration()
    _run(sync_conn, m, "downgrade")
    version, down = _latest(sync_conn)
    sync_conn.execute(
        text(
            "INSERT INTO config_versions (namespace, version, values, reason, created_by) "
            "VALUES ('deeds', :v, CAST(:values AS jsonb), 'by hand', NULL)"
        ),
        {"v": version + 1, "values": json.dumps({**down, "same_act_cap": 7})},
    )
    _run(sync_conn, m, "upgrade")
    _, up = _latest(sync_conn)
    limits = DeedLimits.model_validate(_without(up, REMOVED_LATER))
    assert limits.same_act_cap == 7  # the hand-published value survives
    assert limits.reporter_monthly_limit == TOTAL
    assert limits.reporter_doer_monthly_limit == PAIR
    assert up["photos_enabled"] is False
