"""Constraints on the deeds tables hold even if service code is bypassed (M2-DESIGN §2.2 to §2.7).

Services map ``IntegrityError`` to error codes by constraint name, so each test checks the name.
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.deeds.models import (
    Deed,
    DeedRevision,
    DeedStatus,
    Invite,
    InviteSentVia,
    InviteStatus,
    QrScan,
    SameActGroup,
    ScanMethod,
    SelfLog,
    VerificationEvent,
    VerificationEventKind,
    VerificationMethod,
)
from app.modules.identity.models import User

POINT = "SRID=4326;POINT(77.5946 12.9716)"
TODAY = date(2026, 11, 15)


def _now() -> datetime:
    return datetime.now(UTC)


async def _users(session: AsyncSession, n: int) -> list[User]:
    users = [User(display_name="Deed Test", handle=f"dd{uuid.uuid4().hex[:12]}") for _ in range(n)]
    session.add_all(users)
    await session.flush()
    return users


def _scan(witness: User, doer: User, **overrides: Any) -> QrScan:
    now = _now()
    values: dict[str, Any] = {
        "witness_user_id": witness.id,
        "doer_user_id": doer.id,
        "scan_method": ScanMethod.QR,
        "token_window": 1000,
        "scanned_at": now,
        "scan_day": TODAY,
        "location": POINT,
        "accuracy_m": 30,
        "expires_at": now + timedelta(minutes=15),
        "limits_version": 1,
    }
    values.update(overrides)
    return QrScan(**values)


def _group(doer: User, **overrides: Any) -> SameActGroup:
    values: dict[str, Any] = {
        "doer_user_id": doer.id,
        "centre": POINT,
        "anchor_at": _now(),
        "radius_m": 200,
        "window_minutes": 120,
        "cap": 5,
        "limits_version": 1,
    }
    values.update(overrides)
    return SameActGroup(**values)


def _pending_deed(reporter: User, **overrides: Any) -> Deed:
    """A pending invite deed: the simplest valid row (no doer, scan or group)."""
    values: dict[str, Any] = {
        "reporter_user_id": reporter.id,
        "category": "animals",
        "description": "Fed a stray dog",
        "occurred_at": _now(),
        "report_day": TODAY,
        "status": DeedStatus.PENDING,
        "verification_method": VerificationMethod.INVITE_ACCEPT,
        "limits_version": 1,
    }
    values.update(overrides)
    return Deed(**values)


async def _verified_qr_deed(
    session: AsyncSession, reporter: User, doer: User, **overrides: Any
) -> Deed:
    scan = _scan(reporter, doer, token_window=uuid.uuid4().int % 10**9)
    group = _group(doer)
    session.add_all([scan, group])
    await session.flush()
    values: dict[str, Any] = {
        "doer_user_id": doer.id,
        "status": DeedStatus.VERIFIED,
        "verification_method": VerificationMethod.QR_SCAN,
        "verified_at": _now(),
        "scan_id": scan.id,
        "same_act_group_id": group.id,
        "location": POINT,
    }
    values.update(overrides)
    deed = _pending_deed(reporter, **values)
    session.add(deed)
    await session.flush()
    return deed


async def _rejects(session: AsyncSession, constraint: str, *objects: Any) -> None:
    with pytest.raises(IntegrityError, match=constraint):
        async with session.begin_nested():
            session.add_all(objects)
            await session.flush()


# --- qr_scans ---------------------------------------------------------------------------------


async def test_valid_scan_is_saved(session: AsyncSession) -> None:
    witness, doer = await _users(session, 2)
    session.add(_scan(witness, doer))
    await session.flush()


async def test_scan_single_use_per_window_and_witness(session: AsyncSession) -> None:
    witness, doer = await _users(session, 2)
    session.add(_scan(witness, doer, scan_day=TODAY))
    await session.flush()
    await _rejects(
        session,
        "uq_qr_scans_doer_window_witness",
        _scan(witness, doer, scan_day=TODAY + timedelta(days=1)),
    )


async def test_scan_once_per_pair_per_day(session: AsyncSession) -> None:
    witness, doer = await _users(session, 2)
    session.add(_scan(witness, doer, token_window=1))
    await session.flush()
    await _rejects(session, "uq_qr_scans_doer_witness_day", _scan(witness, doer, token_window=2))


async def test_scan_next_day_and_other_witness_allowed(session: AsyncSession) -> None:
    witness, other, doer = await _users(session, 3)
    session.add_all(
        [
            _scan(witness, doer, token_window=1),
            _scan(witness, doer, token_window=2, scan_day=TODAY + timedelta(days=1)),
            _scan(other, doer, token_window=1),
        ]
    )
    await session.flush()


async def test_self_scan_rejected(session: AsyncSession) -> None:
    (user,) = await _users(session, 1)
    await _rejects(session, "ck_qr_scans_not_self", _scan(user, user))


async def test_scan_accuracy_must_be_positive(session: AsyncSession) -> None:
    witness, doer = await _users(session, 2)
    await _rejects(session, "ck_qr_scans_accuracy_positive", _scan(witness, doer, accuracy_m=0))


async def test_scan_method_must_be_known(session: AsyncSession) -> None:
    witness, doer = await _users(session, 2)
    scan = _scan(witness, doer)
    session.add(scan)
    await session.flush()
    with pytest.raises(IntegrityError, match="ck_qr_scans_scan_method"):
        async with session.begin_nested():
            await session.execute(
                text("UPDATE qr_scans SET scan_method = 'telepathy' WHERE id = :id"),
                {"id": scan.id},
            )


# --- same_act_groups --------------------------------------------------------------------------


async def test_group_counted_cannot_exceed_cap(session: AsyncSession) -> None:
    (doer,) = await _users(session, 1)
    await _rejects(
        session,
        "ck_same_act_groups_counted_within_cap",
        _group(doer, cap=5, verified_count=6, counted_count=6, distinct_reporters=6),
    )


async def test_group_counted_cannot_exceed_verified(session: AsyncSession) -> None:
    (doer,) = await _users(session, 1)
    await _rejects(
        session,
        "ck_same_act_groups_counted_within_cap",
        _group(doer, verified_count=1, counted_count=2, distinct_reporters=1),
    )


async def test_group_counters_cannot_go_negative(session: AsyncSession) -> None:
    (doer,) = await _users(session, 1)
    await _rejects(
        session, "ck_same_act_groups_counters_valid", _group(doer, distinct_reporters=-1)
    )


async def test_group_null_centre_allowed(session: AsyncSession) -> None:
    (doer,) = await _users(session, 1)
    session.add(_group(doer, centre=None))
    await session.flush()


# --- deeds ------------------------------------------------------------------------------------


async def test_valid_pending_and_verified_deeds_are_saved(session: AsyncSession) -> None:
    reporter, doer = await _users(session, 2)
    session.add(_pending_deed(reporter))
    await session.flush()
    await _verified_qr_deed(session, reporter, doer)


async def test_deed_reporter_cannot_be_doer(session: AsyncSession) -> None:
    (user,) = await _users(session, 1)
    await _rejects(
        session,
        "ck_deeds_not_self",
        _pending_deed(user, doer_user_id=user.id, status=DeedStatus.UNVERIFIED),
    )


async def test_verified_deed_needs_doer_time_and_group(session: AsyncSession) -> None:
    reporter, doer = await _users(session, 2)
    group = _group(doer)
    session.add(group)
    await session.flush()
    base: dict[str, Any] = {
        "status": DeedStatus.VERIFIED,
        "doer_user_id": doer.id,
        "verified_at": _now(),
        "same_act_group_id": group.id,
    }
    for missing in ("doer_user_id", "verified_at", "same_act_group_id"):
        await _rejects(
            session,
            "ck_deeds_verified_has_doer",
            _pending_deed(reporter, **{**base, missing: None}),
        )


async def test_pending_deed_must_be_invite_without_doer(session: AsyncSession) -> None:
    reporter, doer = await _users(session, 2)
    await _rejects(
        session, "ck_deeds_pending_is_invite", _pending_deed(reporter, doer_user_id=doer.id)
    )


async def test_qr_deed_needs_scan(session: AsyncSession) -> None:
    reporter, doer = await _users(session, 2)
    await _rejects(
        session,
        "ck_deeds_qr_has_scan",
        _pending_deed(
            reporter,
            doer_user_id=doer.id,
            status=DeedStatus.UNVERIFIED,
            verification_method=VerificationMethod.QR_SCAN,
            location=POINT,
        ),
    )


async def test_qr_deed_needs_location(session: AsyncSession) -> None:
    reporter, doer = await _users(session, 2)
    scan = _scan(reporter, doer)
    session.add(scan)
    await session.flush()
    await _rejects(
        session,
        "ck_deeds_qr_has_location",
        _pending_deed(
            reporter,
            doer_user_id=doer.id,
            status=DeedStatus.UNVERIFIED,
            verification_method=VerificationMethod.QR_SCAN,
            scan_id=scan.id,
        ),
    )


async def test_counts_for_points_needs_group(session: AsyncSession) -> None:
    (reporter,) = await _users(session, 1)
    await _rejects(
        session, "ck_deeds_counts_needs_group", _pending_deed(reporter, counts_for_points=True)
    )


async def test_withdrawn_status_and_timestamp_go_together(session: AsyncSession) -> None:
    (reporter,) = await _users(session, 1)
    await _rejects(
        session, "ck_deeds_withdrawn_at", _pending_deed(reporter, status=DeedStatus.WITHDRAWN)
    )
    await _rejects(session, "ck_deeds_withdrawn_at", _pending_deed(reporter, withdrawn_at=_now()))
    session.add(_pending_deed(reporter, status=DeedStatus.WITHDRAWN, withdrawn_at=_now()))
    await session.flush()


@pytest.mark.parametrize("count", [0, 10001])
async def test_beneficiaries_count_range(session: AsyncSession, count: int) -> None:
    (reporter,) = await _users(session, 1)
    await _rejects(
        session, "ck_deeds_beneficiaries_range", _pending_deed(reporter, beneficiaries_count=count)
    )


async def test_beneficiaries_count_bounds_allowed(session: AsyncSession) -> None:
    (reporter,) = await _users(session, 1)
    session.add_all(
        [
            _pending_deed(reporter, beneficiaries_count=1),
            _pending_deed(reporter, beneficiaries_count=10000),
        ]
    )
    await session.flush()


async def test_status_must_be_known(session: AsyncSession) -> None:
    (reporter,) = await _users(session, 1)
    deed = _pending_deed(reporter)
    session.add(deed)
    await session.flush()
    with pytest.raises(IntegrityError, match="ck_deeds_deed_status"):
        async with session.begin_nested():
            await session.execute(
                text("UPDATE deeds SET status = 'approved' WHERE id = :id"), {"id": deed.id}
            )


async def test_one_deed_per_pair_per_report_day(session: AsyncSession) -> None:
    reporter, doer = await _users(session, 2)
    group = _group(doer)
    session.add(group)
    await session.flush()
    accepted: dict[str, Any] = {
        "doer_user_id": doer.id,
        "status": DeedStatus.VERIFIED,
        "verified_at": _now(),
        "same_act_group_id": group.id,
    }
    session.add(_pending_deed(reporter, **accepted))
    await session.flush()
    await _rejects(session, "uq_deeds_pair_day", _pending_deed(reporter, **accepted))
    # Withdrawing does not free the pair-day.
    await _rejects(
        session,
        "uq_deeds_pair_day",
        _pending_deed(
            reporter,
            doer_user_id=doer.id,
            status=DeedStatus.WITHDRAWN,
            withdrawn_at=_now(),
        ),
    )
    # The next IST day is fine.
    session.add(_pending_deed(reporter, **accepted, report_day=TODAY + timedelta(days=1)))
    await session.flush()


async def test_many_pending_deeds_per_reporter_per_day(session: AsyncSession) -> None:
    (reporter,) = await _users(session, 1)
    session.add_all([_pending_deed(reporter) for _ in range(3)])
    await session.flush()


async def test_scan_backs_only_one_deed(session: AsyncSession) -> None:
    reporter, doer = await _users(session, 2)
    deed = await _verified_qr_deed(session, reporter, doer)
    await _rejects(
        session,
        "uq_deeds_scan_id",
        _pending_deed(
            reporter,
            doer_user_id=doer.id,
            status=DeedStatus.UNVERIFIED,
            verification_method=VerificationMethod.QR_SCAN,
            scan_id=deed.scan_id,
            location=POINT,
            report_day=TODAY + timedelta(days=1),
        ),
    )


async def test_counts_for_points_can_be_promoted(session: AsyncSession) -> None:
    """D13: a report is promoted after a counted report is withdrawn, so the flag is mutable."""
    reporter, doer = await _users(session, 2)
    deed = await _verified_qr_deed(session, reporter, doer)
    assert deed.counts_for_points is False
    await session.execute(update(Deed).where(Deed.id == deed.id).values(counts_for_points=True))
    stored = await session.scalar(select(Deed.counts_for_points).where(Deed.id == deed.id))
    assert stored is True


async def test_user_with_deeds_cannot_be_deleted(session: AsyncSession) -> None:
    reporter, doer = await _users(session, 2)
    await _verified_qr_deed(session, reporter, doer)
    for user in (reporter, doer):
        with pytest.raises(IntegrityError, match="fk_"):
            async with session.begin_nested():
                await session.execute(text("DELETE FROM users WHERE id = :id"), {"id": user.id})


# --- invites ----------------------------------------------------------------------------------


def _invite(deed: Deed, reporter: User, **overrides: Any) -> Invite:
    now = _now()
    values: dict[str, Any] = {
        "deed_id": deed.id,
        "reporter_user_id": reporter.id,
        "token_hash": uuid.uuid4().hex + uuid.uuid4().hex,
        "sent_via": InviteSentVia.WHATSAPP,
        "status": InviteStatus.PENDING,
        "created_day": TODAY,
        "expires_at": now + timedelta(days=7),
    }
    values.update(overrides)
    return Invite(**values)


async def _pending_deed_saved(session: AsyncSession, reporter: User) -> Deed:
    deed = _pending_deed(reporter)
    session.add(deed)
    await session.flush()
    return deed


async def test_valid_invite_is_saved(session: AsyncSession) -> None:
    reporter, doer = await _users(session, 2)
    deed = await _pending_deed_saved(session, reporter)
    session.add(
        _invite(
            deed,
            reporter,
            status=InviteStatus.ACCEPTED,
            accepted_by=doer.id,
            accepted_at=_now(),
            shared_device=True,
        )
    )
    await session.flush()


async def test_accepted_invite_needs_accepter_and_time(session: AsyncSession) -> None:
    reporter, doer = await _users(session, 2)
    deed = await _pending_deed_saved(session, reporter)
    for bad in (
        {"status": InviteStatus.ACCEPTED},
        {"status": InviteStatus.ACCEPTED, "accepted_by": doer.id},
        {"status": InviteStatus.PENDING, "accepted_by": doer.id, "accepted_at": _now()},
    ):
        await _rejects(session, "ck_invites_accepted_together", _invite(deed, reporter, **bad))


async def test_reporter_cannot_accept_own_invite(session: AsyncSession) -> None:
    (reporter,) = await _users(session, 1)
    deed = await _pending_deed_saved(session, reporter)
    await _rejects(
        session,
        "ck_invites_not_self",
        _invite(
            deed,
            reporter,
            status=InviteStatus.ACCEPTED,
            accepted_by=reporter.id,
            accepted_at=_now(),
        ),
    )


async def test_one_invite_per_deed(session: AsyncSession) -> None:
    (reporter,) = await _users(session, 1)
    deed = await _pending_deed_saved(session, reporter)
    session.add(_invite(deed, reporter))
    await session.flush()
    await _rejects(session, "uq_invites_deed_id", _invite(deed, reporter))


async def test_invite_token_hash_unique(session: AsyncSession) -> None:
    (reporter,) = await _users(session, 1)
    first = await _pending_deed_saved(session, reporter)
    second = await _pending_deed_saved(session, reporter)
    session.add(_invite(first, reporter, token_hash="a" * 64))
    await session.flush()
    await _rejects(session, "uq_invites_token_hash", _invite(second, reporter, token_hash="a" * 64))


# --- append-only ledgers ----------------------------------------------------------------------


async def _ledger_rows(session: AsyncSession) -> tuple[VerificationEvent, DeedRevision]:
    reporter, doer = await _users(session, 2)
    deed = await _verified_qr_deed(session, reporter, doer)
    event = VerificationEvent(
        deed_id=deed.id,
        kind=VerificationEventKind.QR_VERIFIED,
        actor_user_id=reporter.id,
        scan_id=deed.scan_id,
        evidence={"accuracy_m": 30},
    )
    revision = DeedRevision(
        deed_id=deed.id,
        editor_user_id=reporter.id,
        old_values={"description": "Fed a stray dog"},
        new_values={"description": "Fed two stray dogs"},
    )
    session.add_all([event, revision])
    await session.flush()
    return event, revision


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE verification_events SET evidence = '{}' WHERE id = :event",
        "DELETE FROM verification_events WHERE id = :event",
        "UPDATE deed_revisions SET new_values = '{}' WHERE id = :revision",
        "DELETE FROM deed_revisions WHERE id = :revision",
    ],
)
async def test_ledgers_are_append_only(session: AsyncSession, statement: str) -> None:
    event, revision = await _ledger_rows(session)
    params = {"event": event.id, "revision": revision.id}
    with pytest.raises(DBAPIError, match="append-only"):
        async with session.begin_nested():
            await session.execute(text(statement), params)


async def test_verification_event_kind_must_be_known(session: AsyncSession) -> None:
    reporter, doer = await _users(session, 2)
    deed = await _verified_qr_deed(session, reporter, doer)
    with pytest.raises(IntegrityError, match="ck_verification_events_verification_event_kind"):
        async with session.begin_nested():
            await session.execute(
                text(
                    "INSERT INTO verification_events (id, deed_id, kind, evidence) "
                    "VALUES (:id, :deed, 'vibes', '{}')"
                ),
                {"id": uuid.uuid4(), "deed": deed.id},
            )


# --- self_logs --------------------------------------------------------------------------------


async def test_self_logs_are_deleted_with_their_user(session: AsyncSession) -> None:
    (user,) = await _users(session, 1)
    log = SelfLog(
        user_id=user.id, category="animals", description="Fed a stray dog", occurred_at=_now()
    )
    session.add(log)
    await session.flush()
    await session.execute(text("DELETE FROM users WHERE id = :id"), {"id": user.id})
    remaining = await session.scalar(
        text("SELECT count(*) FROM self_logs WHERE id = :id"), {"id": log.id}
    )
    assert remaining == 0


# --- schema shape -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("table", "index"),
    [
        ("qr_scans", "ix_qr_scans_location"),
        ("same_act_groups", "ix_same_act_groups_centre"),
        ("deeds", "ix_deeds_location"),
    ],
)
async def test_location_columns_have_gist_indexes(
    session: AsyncSession, table: str, index: str
) -> None:
    indexdef = await session.scalar(
        text("SELECT indexdef FROM pg_indexes WHERE tablename = :t AND indexname = :i"),
        {"t": table, "i": index},
    )
    assert indexdef is not None
    assert "USING gist" in indexdef


async def test_location_columns_are_geography_points(session: AsyncSession) -> None:
    rows = (
        await session.execute(
            text(
                "SELECT f_table_name, type, srid FROM geography_columns "
                "WHERE f_table_name IN ('qr_scans', 'same_act_groups', 'deeds', 'self_logs')"
            )
        )
    ).all()
    assert sorted(r[0] for r in rows) == ["deeds", "qr_scans", "same_act_groups", "self_logs"]
    assert {(r[1], r[2]) for r in rows} == {("Point", 4326)}


async def test_verified_count_index_is_partial(session: AsyncSession) -> None:
    """D15: the profile count reads only counted verified reports."""
    indexdef = await session.scalar(
        text("SELECT indexdef FROM pg_indexes WHERE indexname = 'ix_deeds_doer_counted'")
    )
    assert indexdef is not None
    assert "WHERE" in indexdef and "verified" in indexdef and "counts_for_points" in indexdef
    old = await session.scalar(
        text("SELECT indexdef FROM pg_indexes WHERE indexname = 'ix_deeds_doer_verified'")
    )
    assert old is None


async def test_monthly_limit_count_has_an_index(session: AsyncSession) -> None:
    """D16 counts a reporter's deeds by ``reported_at``."""
    indexdef = await session.scalar(
        text("SELECT indexdef FROM pg_indexes WHERE indexname = 'ix_deeds_reporter_reported_at'")
    )
    assert indexdef is not None
    assert "(reporter_user_id, reported_at DESC)" in indexdef
