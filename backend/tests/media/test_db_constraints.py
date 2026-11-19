"""Constraints on the media tables hold even if service code is bypassed (M3-DESIGN §2.2,
migration 0014). Services map ``IntegrityError`` to error codes by constraint name, so each test
checks the name."""

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.deeds.models import Deed, SelfLog
from app.modules.identity.models import User
from app.modules.media.models import (
    PHOTO_REPORT_PRIORITY,
    CaseDecision,
    ObjectMove,
    Photo,
    PhotoDecision,
    PhotoDecisionAction,
    PhotoPurpose,
    PhotoReport,
    PhotoReportCase,
    PhotoReportReason,
    PhotoStatus,
    PhotoTakedown,
    PhotoType,
    ReportCaseStatus,
)
from tests.deeds.test_db_constraints import _users, _verified_qr_deed

SHA = "ab" * 32


def _now() -> datetime:
    return datetime.now(UTC)


def _photo(uploader: User, **overrides: Any) -> Photo:
    photo_id = uuid.uuid4()
    values: dict[str, Any] = {
        "id": photo_id,
        "uploader_user_id": uploader.id,
        "purpose": PhotoPurpose.DEED,
        "status": PhotoStatus.PENDING_UPLOAD,
        "declared_type": PhotoType.JPEG,
        "declared_bytes": 1000,
        "staging_key": f"staging/{photo_id}",
    }
    values.update(overrides)
    return Photo(**values)


def _ready(uploader: User, deed: Deed, position: int, **overrides: Any) -> Photo:
    values: dict[str, Any] = {
        "deed_id": deed.id,
        "position": position,
        "status": PhotoStatus.READY,
        "variant_keys": {"thumb": "photos/x/thumb-1.webp", "display": "photos/x/display-1.webp"},
        "width": 1280,
        "height": 960,
    }
    values.update(overrides)
    return _photo(uploader, **values)


async def _rejects(session: AsyncSession, constraint: str, *objects: Any) -> None:
    with pytest.raises(IntegrityError, match=constraint):
        async with session.begin_nested():
            session.add_all(objects)
            await session.flush()


async def _deed(session: AsyncSession) -> tuple[User, User, Deed]:
    reporter, doer = await _users(session, 2)
    return reporter, doer, await _verified_qr_deed(session, reporter, doer)


async def _self_log(session: AsyncSession, user: User) -> SelfLog:
    log = SelfLog(
        user_id=user.id, category="animals", description="Fed a stray dog", occurred_at=_now()
    )
    session.add(log)
    await session.flush()
    return log


# --- photos -------------------------------------------------------------------------------------


async def test_defaults_and_valid_rows(session: AsyncSession) -> None:
    reporter, _, deed = await _deed(session)
    photo = _ready(reporter, deed, 1, content_sha256=SHA)
    session.add(photo)
    await session.flush()
    await session.refresh(photo)
    assert photo.takedown is PhotoTakedown.NONE
    assert photo.blur_regions == []
    assert photo.attempts == 0
    assert photo.created_at is not None


async def test_photo_has_at_most_one_owner(session: AsyncSession) -> None:
    reporter, _, deed = await _deed(session)
    log = await _self_log(session, reporter)
    await _rejects(
        session, "ck_photos_one_owner", _photo(reporter, deed_id=deed.id, self_log_id=log.id)
    )


async def test_owner_matches_purpose(session: AsyncSession) -> None:
    reporter, _, deed = await _deed(session)
    log = await _self_log(session, reporter)
    await _rejects(
        session,
        "ck_photos_owner_matches_purpose",
        _photo(reporter, purpose=PhotoPurpose.SELF_LOG, deed_id=deed.id),
    )
    await _rejects(session, "ck_photos_owner_matches_purpose", _photo(reporter, self_log_id=log.id))


async def test_takedown_only_on_deed_photos(session: AsyncSession) -> None:
    (user,) = await _users(session, 1)
    await _rejects(
        session,
        "ck_photos_takedown_deed_only",
        _photo(user, purpose=PhotoPurpose.SELF_LOG, takedown=PhotoTakedown.DOER_REMOVED),
    )


async def test_ready_needs_variants_and_size(session: AsyncSession) -> None:
    reporter, _, deed = await _deed(session)
    await _rejects(
        session, "ck_photos_ready_has_variants", _ready(reporter, deed, 1, variant_keys=None)
    )
    await _rejects(session, "ck_photos_ready_has_variants", _ready(reporter, deed, 1, width=None))


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        ({"declared_bytes": 0}, "ck_photos_declared_bytes_positive"),
        ({"position": 0}, "ck_photos_position_positive"),
        ({"attempts": -1}, "ck_photos_attempts_non_negative"),
        ({"width": 0}, "ck_photos_dimensions_positive"),
        ({"content_sha256": "Z" * 64}, "ck_photos_content_sha256_hex"),
        ({"blur_regions": {"x": 1}}, "ck_photos_blur_regions_array"),
        ({"status": PhotoStatus.DELETED}, "ck_photos_deleted_at"),
        ({"deleted_at": datetime(2026, 11, 19, tzinfo=UTC)}, "ck_photos_deleted_at"),
        ({"takedown": PhotoTakedown.ADMIN_REMOVED}, "ck_photos_removed_has_time"),
    ],
)
async def test_photo_value_checks(
    session: AsyncSession, overrides: dict[str, Any], constraint: str
) -> None:
    (user,) = await _users(session, 1)
    await _rejects(session, constraint, _photo(user, **overrides))


@pytest.mark.parametrize(
    ("column", "value", "constraint"),
    [
        ("status", "lost", "ck_photos_photo_status"),
        ("purpose", "avatar", "ck_photos_photo_purpose"),
        ("takedown", "hidden", "ck_photos_photo_takedown"),
        ("declared_type", "image/gif", "ck_photos_photo_declared_type"),
        ("object_move", "copy", "ck_photos_photo_object_move"),
        ("reject_reason", "ugly", "ck_photos_photo_reject_reason"),
    ],
)
async def test_photo_enums_are_checked(
    session: AsyncSession, column: str, value: str, constraint: str
) -> None:
    (user,) = await _users(session, 1)
    photo = _photo(user)
    session.add(photo)
    await session.flush()
    with pytest.raises(IntegrityError, match=constraint):
        async with session.begin_nested():
            await session.execute(
                text(f"UPDATE photos SET {column} = :v WHERE id = :id"),
                {"v": value, "id": photo.id},
            )


async def test_one_live_photo_per_position(session: AsyncSession) -> None:
    reporter, _, deed = await _deed(session)
    session.add(_ready(reporter, deed, 1))
    await session.flush()
    await _rejects(session, "uq_photos_deed_position", _ready(reporter, deed, 1))
    await _rejects(
        session, "uq_photos_deed_position", _ready(reporter, deed, 1, status=PhotoStatus.QUEUED)
    )
    # A photo taken down, rejected or deleted frees its slot.
    session.add_all(
        [
            _ready(
                reporter,
                deed,
                1,
                takedown=PhotoTakedown.ADMIN_REMOVED,
                removed_at=_now(),
                object_move=ObjectMove.HOLD,
            ),
            _photo(reporter, deed_id=deed.id, position=1, status=PhotoStatus.REJECTED),
            _photo(
                reporter, deed_id=deed.id, position=1, status=PhotoStatus.DELETED, deleted_at=_now()
            ),
            _ready(reporter, deed, 2),
        ]
    )
    await session.flush()


async def test_one_live_photo_per_self_log_position(session: AsyncSession) -> None:
    (user,) = await _users(session, 1)
    log = await _self_log(session, user)
    values: dict[str, Any] = {
        "purpose": PhotoPurpose.SELF_LOG,
        "self_log_id": log.id,
        "position": 1,
        "status": PhotoStatus.QUEUED,
    }
    session.add(_photo(user, **values))
    await session.flush()
    await _rejects(session, "uq_photos_self_log_position", _photo(user, **values))


async def test_deleting_a_self_log_detaches_its_photos(session: AsyncSession) -> None:
    (user,) = await _users(session, 1)
    log = await _self_log(session, user)
    photo = _photo(user, purpose=PhotoPurpose.SELF_LOG, self_log_id=log.id)
    session.add(photo)
    await session.flush()
    await session.execute(text("DELETE FROM self_logs WHERE id = :id"), {"id": log.id})
    await session.refresh(photo)
    assert photo.self_log_id is None


async def test_deed_with_photos_cannot_be_deleted(session: AsyncSession) -> None:
    reporter, _, deed = await _deed(session)
    session.add(_ready(reporter, deed, 1))
    await session.flush()
    with pytest.raises(IntegrityError, match="fk_photos_deed_id_deeds"):
        async with session.begin_nested():
            await session.execute(text("DELETE FROM deeds WHERE id = :id"), {"id": deed.id})


# --- photo reports ------------------------------------------------------------------------------


def _case(photo: Photo, **overrides: Any) -> PhotoReportCase:
    values: dict[str, Any] = {
        "photo_id": photo.id,
        "status": ReportCaseStatus.OPEN,
        "priority": 2,
        "opened_at": _now(),
    }
    values.update(overrides)
    return PhotoReportCase(**values)


async def _photo_with_case(session: AsyncSession) -> tuple[User, Photo, PhotoReportCase]:
    reporter, _, deed = await _deed(session)
    photo = _ready(reporter, deed, 1)
    session.add(photo)
    await session.flush()
    case = _case(photo)
    session.add(case)
    await session.flush()
    return reporter, photo, case


def test_priorities_follow_q44() -> None:
    assert PHOTO_REPORT_PRIORITY == {
        PhotoReportReason.CHILD_NO_GUARDIAN_CONSENT: 1,
        PhotoReportReason.NOT_CONSENTED: 2,
        PhotoReportReason.PRIVATE_DETAILS: 2,
        PhotoReportReason.INAPPROPRIATE: 2,
        PhotoReportReason.OTHER: 3,
    }


async def test_one_open_case_per_photo(session: AsyncSession) -> None:
    reporter, photo, case = await _photo_with_case(session)
    await _rejects(session, "uq_photo_report_cases_open", _case(photo))
    # Closed and resolved cases don't block a new open one.
    case.status = ReportCaseStatus.CLOSED
    await session.flush()
    session.add_all(
        [
            _case(
                photo,
                status=ReportCaseStatus.RESOLVED,
                decision=CaseDecision.KEPT,
                resolved_at=_now(),
                decided_by=reporter.id,
            ),
            _case(photo),
        ]
    )
    await session.flush()


@pytest.mark.parametrize(
    "overrides",
    [
        {"status": ReportCaseStatus.RESOLVED},
        {"status": ReportCaseStatus.RESOLVED, "decision": CaseDecision.KEPT, "resolved_at": _now()},
        {"decision": CaseDecision.KEPT},
    ],
)
async def test_resolved_iff_decision_time_and_decider(
    session: AsyncSession, overrides: dict[str, Any]
) -> None:
    _, photo, _ = await _photo_with_case(session)
    await _rejects(
        session,
        "ck_photo_report_cases_resolved_has_decision",
        _case(photo, **{"status": ReportCaseStatus.CLOSED, **overrides}),
    )


async def test_removal_reason_iff_removed(session: AsyncSession) -> None:
    reporter, photo, _ = await _photo_with_case(session)
    resolved: dict[str, Any] = {
        "status": ReportCaseStatus.RESOLVED,
        "resolved_at": _now(),
        "decided_by": reporter.id,
    }
    await _rejects(
        session,
        "ck_photo_report_cases_removal_reason_iff_removed",
        _case(photo, decision=CaseDecision.REMOVED, **resolved),
    )
    await _rejects(
        session,
        "ck_photo_report_cases_removal_reason_iff_removed",
        _case(
            photo,
            decision=CaseDecision.KEPT,
            removal_reason=PhotoReportReason.OTHER,
            **resolved,
        ),
    )
    session.add(
        _case(
            photo,
            decision=CaseDecision.REMOVED,
            removal_reason=PhotoReportReason.PRIVATE_DETAILS,
            **resolved,
        )
    )
    await session.flush()


@pytest.mark.parametrize("priority", [0, 4])
async def test_case_priority_range(session: AsyncSession, priority: int) -> None:
    _, photo, case = await _photo_with_case(session)
    case.status = ReportCaseStatus.CLOSED
    await session.flush()
    await _rejects(session, "ck_photo_report_cases_priority_range", _case(photo, priority=priority))


def _report(case: PhotoReportCase, **overrides: Any) -> PhotoReport:
    values: dict[str, Any] = {
        "case_id": case.id,
        "photo_id": case.photo_id,
        "reporter_ip_hash": "iphash",
        "reason": PhotoReportReason.NOT_CONSENTED,
    }
    values.update(overrides)
    return PhotoReport(**values)


async def test_one_report_per_member_and_per_visitor_ip(session: AsyncSession) -> None:
    _, _, case = await _photo_with_case(session)
    (member,) = await _users(session, 1)
    session.add_all([_report(case, reporter_user_id=member.id), _report(case)])
    await session.flush()
    await _rejects(
        session,
        "uq_photo_reports_case_member",
        _report(case, reporter_user_id=member.id, reporter_ip_hash="other"),
    )
    await _rejects(session, "uq_photo_reports_case_visitor", _report(case))
    # The same IP may report once as a visitor and once per member.
    (other,) = await _users(session, 1)
    session.add(_report(case, reporter_user_id=other.id))
    await session.flush()


async def test_report_reason_is_checked(session: AsyncSession) -> None:
    _, _, case = await _photo_with_case(session)
    report = _report(case)
    session.add(report)
    await session.flush()
    with pytest.raises(IntegrityError, match="ck_photo_reports_photo_report_reason"):
        async with session.begin_nested():
            await session.execute(
                text("UPDATE photo_reports SET reason = 'boring' WHERE id = :id"),
                {"id": report.id},
            )


# --- photo decisions (append-only) --------------------------------------------------------------


def _decision(photo: Photo, actor: User, **overrides: Any) -> PhotoDecision:
    values: dict[str, Any] = {
        "photo_id": photo.id,
        "action": PhotoDecisionAction.KEEP,
        "note": "Looks fine to me",
        "actor_user_id": actor.id,
    }
    values.update(overrides)
    return PhotoDecision(**values)


@pytest.mark.parametrize("note", ["", "    ", "abcd", "  ab  "])
async def test_decision_note_needs_five_characters(session: AsyncSession, note: str) -> None:
    reporter, photo, _ = await _photo_with_case(session)
    await _rejects(
        session, "ck_photo_decisions_note_min_length", _decision(photo, reporter, note=note)
    )


async def test_decision_removal_reason_iff_remove(session: AsyncSession) -> None:
    reporter, photo, _ = await _photo_with_case(session)
    await _rejects(
        session,
        "ck_photo_decisions_removal_reason_iff_remove",
        _decision(photo, reporter, action=PhotoDecisionAction.REMOVE),
    )
    await _rejects(
        session,
        "ck_photo_decisions_removal_reason_iff_remove",
        _decision(photo, reporter, removal_reason=PhotoReportReason.OTHER),
    )
    session.add(
        _decision(
            photo,
            reporter,
            action=PhotoDecisionAction.REMOVE,
            removal_reason=PhotoReportReason.NOT_CONSENTED,
        )
    )
    await session.flush()


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE photo_decisions SET note = 'changed note' WHERE id = :id",
        "DELETE FROM photo_decisions WHERE id = :id",
    ],
)
async def test_photo_decisions_are_append_only(session: AsyncSession, statement: str) -> None:
    reporter, photo, case = await _photo_with_case(session)
    decision = _decision(photo, reporter, case_id=case.id)
    session.add(decision)
    await session.flush()
    with pytest.raises(DBAPIError, match="append-only"):
        async with session.begin_nested():
            await session.execute(text(statement), {"id": decision.id})
