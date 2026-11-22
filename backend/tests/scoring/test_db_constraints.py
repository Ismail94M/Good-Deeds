"""Constraints and triggers on the scoring tables hold even if service code is bypassed
(docs/M4-DESIGN.md §2.3, D48, D51, D57, D62, D64, D70; migration 0020), and the seeded
``scoring`` v1 and ``leaderboard`` v1 config (§2.6, PO answers §11).

Services map ``IntegrityError`` to error codes by constraint name, so each test checks the name.
"""

import importlib.util
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Connection, create_engine, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.runtime_config import ConfigVersion
from app.modules.deeds.models import Region
from app.modules.identity.models import User
from app.modules.scoring.models import (
    EvaluationMethod,
    EvaluationMode,
    EvidenceLevel,
    HeroTitle,
    ImpactEvaluation,
    LeaderboardEntry,
    LeaderboardSnapshot,
    LedgerKind,
    LedgerReason,
    LedgerRole,
    MonthFinalization,
    PointsLedger,
    ScoringEvidence,
    TextCheck,
    TitleLevel,
    UserBadge,
)
from tests.conftest import BACKEND_DIR
from tests.deeds.test_db_constraints import _pending_deed, _users

MONTH = date(2026, 11, 1)
DAY = date(2026, 11, 20)
VERIFIED_AT = datetime(2026, 11, 20, 6, 30, tzinfo=UTC)
M20 = BACKEND_DIR / "alembic" / "versions" / "2026_11_22_0100-0020_scoring.py"


def _now() -> datetime:
    return datetime.now(UTC)


async def _city_id(session: AsyncSession) -> int:
    region_id = await session.scalar(select(Region.id).where(Region.code == "IN-KA-BLR"))
    assert region_id is not None
    return region_id


async def _world(session: AsyncSession) -> dict[str, Any]:
    reporter, doer = await _users(session, 2)
    deed = _pending_deed(reporter)
    session.add(deed)
    await session.flush()
    return {"reporter": reporter, "doer": doer, "deed": deed, "region": await _city_id(session)}


def _entry(w: dict[str, Any], **overrides: Any) -> PointsLedger:
    doer: User = w["doer"]
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "user_id": doer.id,
        "deed_id": w["deed"].id,
        "role": LedgerRole.DOER,
        "kind": LedgerKind.AWARD,
        "points": 10,
        "deed_units": 1,
        "counts_for_prize_rank": True,
        "region_id": w["region"],
        "reason": LedgerReason.VERIFIED,
        "breakdown": {"base": 10, "pair_ordinal": 1, "pair_percent": 100},
        "counterpart_user_id": w["reporter"].id,
        "verified_at": VERIFIED_AT,
        "earned_on": DAY,
        "period_month": MONTH,
        "scoring_config_version": 1,
    }
    values.update(overrides)
    return PointsLedger(**values)


def _reversal_of(entry: PointsLedger, **overrides: Any) -> PointsLedger:
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "user_id": entry.user_id,
        "deed_id": entry.deed_id,
        "role": entry.role,
        "kind": LedgerKind.REVERSAL,
        "points": -entry.points,
        "deed_units": -entry.deed_units,
        "counts_for_prize_rank": entry.counts_for_prize_rank,
        "region_id": entry.region_id,
        "reason": LedgerReason.WITHDRAWN,
        "breakdown": {},
        "counterpart_user_id": entry.counterpart_user_id,
        "reverses_entry_id": entry.id,
        "verified_at": entry.verified_at,
        "earned_on": entry.earned_on,
        "period_month": entry.period_month,
        "scoring_config_version": 1,
    }
    values.update(overrides)
    return PointsLedger(**values)


async def _add(session: AsyncSession, *rows: Any) -> None:
    session.add_all(rows)
    await session.flush()


async def _refused(session: AsyncSession, match: str, *rows: Any) -> None:
    with pytest.raises(IntegrityError, match=match):
        async with session.begin_nested():
            await _add(session, *rows)


# --- points_ledger ------------------------------------------------------------------------------


async def test_award_reversal_and_new_award(session: AsyncSession) -> None:
    w = await _world(session)
    award = _entry(w)
    await _add(session, award)
    await _add(session, _reversal_of(award, reason=LedgerReason.EDITED))
    await _add(session, _entry(w, points=4, reason=LedgerReason.EDITED))
    total = await session.scalar(
        text("SELECT sum(points) FROM points_ledger WHERE deed_id = :d"), {"d": w["deed"].id}
    )
    assert total == 4


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE points_ledger SET points = 12 WHERE id = :id",
        "DELETE FROM points_ledger WHERE id = :id",
    ],
)
async def test_ledger_is_append_only(session: AsyncSession, statement: str) -> None:
    w = await _world(session)
    award = _entry(w)
    await _add(session, award)
    with pytest.raises(DBAPIError, match="append-only"):
        async with session.begin_nested():
            await session.execute(text(statement), {"id": award.id})


async def test_reporter_rows_never_count_for_prize_rank(session: AsyncSession) -> None:
    w = await _world(session)
    reporter_row = _entry(
        w,
        user_id=w["reporter"].id,
        counterpart_user_id=w["doer"].id,
        role=LedgerRole.REPORTER,
        points=2,
        deed_units=0,
        counts_for_prize_rank=True,
    )
    await _refused(session, "ck_points_ledger_reporter_never_prize_rank", reporter_row)
    reporter_row = _entry(
        w,
        user_id=w["reporter"].id,
        counterpart_user_id=w["doer"].id,
        role=LedgerRole.REPORTER,
        points=2,
        deed_units=0,
        counts_for_prize_rank=False,
    )
    await _add(session, reporter_row)


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        ({"points": 0}, "ck_points_ledger_points_by_kind"),
        ({"points": -1}, "ck_points_ledger_points_by_kind"),
        ({"kind": LedgerKind.DECISION, "points": 1, "deed_units": 0}, "points_by_kind"),
        ({"kind": LedgerKind.DECISION, "points": 0, "deed_units": 1}, "deed_units_by_kind"),
        ({"deed_units": 2}, "ck_points_ledger_deed_units_by_kind"),
        ({"deed_units": -1}, "ck_points_ledger_deed_units_by_kind"),
        (
            {"role": LedgerRole.REPORTER, "counts_for_prize_rank": False, "deed_units": 1},
            "ck_points_ledger_reporter_no_deed_units",
        ),
        ({"kind": LedgerKind.REVERSAL, "points": -10, "deed_units": -1}, "reversal_has_target"),
        ({"period_month": date(2026, 12, 1)}, "ck_points_ledger_period_month_of_earned_on"),
        ({"period_month": date(2026, 11, 2)}, "ck_points_ledger_period_month_of_earned_on"),
        ({"breakdown": []}, "ck_points_ledger_breakdown_object"),
        ({"reason": "vibes"}, "ck_points_ledger_ledger_reason"),
        ({"role": "witness"}, "ck_points_ledger_ledger_role"),
        ({"scoring_config_version": 0}, "ck_points_ledger_config_version_positive"),
    ],
)
async def test_entry_checks(
    session: AsyncSession, overrides: dict[str, Any], constraint: str
) -> None:
    w = await _world(session)
    await _refused(session, constraint, _entry(w, **overrides))


async def test_counterpart_is_not_the_user(session: AsyncSession) -> None:
    w = await _world(session)
    await _refused(
        session,
        "ck_points_ledger_counterpart_not_self",
        _entry(w, counterpart_user_id=w["doer"].id),
    )


async def test_capped_decision_counts_nothing(session: AsyncSession) -> None:
    w = await _world(session)
    decision = _entry(
        w,
        kind=LedgerKind.DECISION,
        points=0,
        deed_units=0,
        reason=LedgerReason.DOER_DAILY_CAP,
    )
    await _add(session, decision)
    # It is the active doer entry: another award needs it reversed first.
    await _refused(session, "already has an active doer entry", _entry(w))


async def test_an_entry_is_reversed_at_most_once(session: AsyncSession) -> None:
    w = await _world(session)
    award = _entry(w)
    await _add(session, award, _reversal_of(award))
    await _refused(session, "uq_points_ledger_reverses_entry_id", _reversal_of(award))


async def test_one_active_entry_per_deed_and_role(session: AsyncSession) -> None:
    w = await _world(session)
    award = _entry(w)
    await _add(session, award)
    await _refused(session, "already has an active doer entry", _entry(w, points=12))
    # The reporter's entry for the same deed is separate.
    await _add(
        session,
        _entry(
            w,
            user_id=w["reporter"].id,
            counterpart_user_id=w["doer"].id,
            role=LedgerRole.REPORTER,
            points=2,
            deed_units=0,
            counts_for_prize_rank=False,
        ),
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"points": -9},
        {"points": 0, "deed_units": 0},
        {"deed_units": 0},
        {"counts_for_prize_rank": False},
        {"role": LedgerRole.REPORTER, "counts_for_prize_rank": False, "deed_units": 0},
        {"earned_on": date(2026, 11, 21)},
        {"verified_at": VERIFIED_AT + timedelta(seconds=1)},
        {"region": "other"},
        {"user": "reporter"},
    ],
)
async def test_a_reversal_must_mirror_its_entry(
    session: AsyncSession, overrides: dict[str, Any]
) -> None:
    w = await _world(session)
    award = _entry(w)
    await _add(session, award)
    if overrides.pop("region", None):
        ward = await session.scalar(select(Region.id).where(Region.kind == "ward").limit(1))
        overrides["region_id"] = ward
    if overrides.pop("user", None):
        overrides["user_id"] = w["reporter"].id
        overrides["counterpart_user_id"] = w["doer"].id
    await _refused(session, "reversal must negate", _reversal_of(award, **overrides))


async def test_a_reversal_cannot_be_reversed(session: AsyncSession) -> None:
    w = await _world(session)
    award = _entry(w)
    reversal = _reversal_of(award)
    await _add(session, award, reversal)
    again = _reversal_of(reversal, points=10, deed_units=1)
    again.kind = LedgerKind.REVERSAL
    await _refused(session, "points_by_kind|reversal must negate", again)


async def test_ledger_foreign_keys(session: AsyncSession) -> None:
    w = await _world(session)
    await _refused(session, "fk_points_ledger_region_id_regions", _entry(w, region_id=999_999))
    await _refused(session, "fk_points_ledger_deed_id_deeds", _entry(w, deed_id=uuid.uuid4()))
    await _refused(
        session,
        "fk_points_ledger_evaluation_id_impact_evaluations",
        _entry(w, evaluation_id=uuid.uuid4()),
    )


# --- impact_evaluations -------------------------------------------------------------------------


def _evaluation(deed_id: uuid.UUID, **overrides: Any) -> ImpactEvaluation:
    values: dict[str, Any] = {
        "deed_id": deed_id,
        "method": EvaluationMethod.RULE_BASED,
        "mode": EvaluationMode.LIVE,
        "rule_points": 10,
        "text_check": TextCheck.OK,
        "evidence_level": EvidenceLevel.TEXT_ONLY,
        "scoring_config_version": 1,
    }
    values.update(overrides)
    return ImpactEvaluation(**values)


def _ai(deed_id: uuid.UUID, **overrides: Any) -> ImpactEvaluation:
    values: dict[str, Any] = {
        "method": EvaluationMethod.AI,
        "mode": EvaluationMode.SHADOW,
        "rule_points": None,
        "text_check": None,
        "impact_score": 40,
        "raw_score": 95,
        "sub_scores": {"impact": 90},
        "evidence_cap_applied": True,
        "model_version": "claude-opus-5-5",
        "prompt_version": "v1",
        "input_hash": "a" * 64,
        "evaluation_confidence": 0.8,
    }
    values.update(overrides)
    return _evaluation(deed_id, **values)


async def test_rule_based_and_shadow_ai_evaluations(session: AsyncSession) -> None:
    w = await _world(session)
    evaluation = _evaluation(w["deed"].id)
    await _add(session, evaluation, _ai(w["deed"].id))
    await _add(session, _entry(w, evaluation_id=evaluation.id))


async def test_ai_can_never_be_live(session: AsyncSession) -> None:
    """D62: a DB insert of an ``ai`` row with ``mode='live'`` fails (test plan §8)."""
    w = await _world(session)
    await _refused(
        session, "ck_impact_evaluations_ai_is_shadow", _ai(w["deed"].id, mode=EvaluationMode.LIVE)
    )
    with pytest.raises(IntegrityError, match="ck_impact_evaluations_ai_is_shadow"):
        async with session.begin_nested():
            await session.execute(
                text(
                    "INSERT INTO impact_evaluations (id, deed_id, method, mode, impact_score, "
                    "raw_score, sub_scores, evidence_level, model_version, prompt_version, "
                    "scoring_config_version, input_hash) VALUES (:id, :d, 'ai', 'live', 40, 40, "
                    "'{}', 'photo', 'm', 'v1', 1, :h)"
                ),
                {"id": uuid.uuid4(), "d": w["deed"].id, "h": "b" * 64},
            )


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        ({"mode": EvaluationMode.SHADOW}, "ck_impact_evaluations_rule_based_is_live"),
        ({"rule_points": None}, "ck_impact_evaluations_rule_based_fields"),
        ({"text_check": None}, "ck_impact_evaluations_rule_based_fields"),
        ({"impact_score": 40}, "ck_impact_evaluations_rule_based_fields"),
        ({"rule_points": -1}, "ck_impact_evaluations_rule_points_non_negative"),
        ({"impact_explanation": "x" * 1501}, "ck_impact_evaluations_explanation_length"),
        ({"estimated_beneficiaries": -1}, "beneficiaries_non_negative"),
        ({"evidence_level": "rumour"}, "ck_impact_evaluations_evidence_level"),
    ],
)
async def test_rule_based_evaluation_checks(
    session: AsyncSession, overrides: dict[str, Any], constraint: str
) -> None:
    w = await _world(session)
    await _refused(session, constraint, _evaluation(w["deed"].id, **overrides))


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        ({"impact_score": None}, "ck_impact_evaluations_ai_fields"),
        ({"input_hash": None}, "ck_impact_evaluations_ai_fields"),
        ({"rule_points": 10}, "ck_impact_evaluations_ai_fields"),
        ({"impact_score": 96}, "ck_impact_evaluations_scores_valid"),  # above the raw score
        ({"raw_score": 101, "impact_score": 40}, "ck_impact_evaluations_scores_valid"),
        ({"sub_scores": [1, 2]}, "ck_impact_evaluations_sub_scores_object"),
        ({"evaluation_confidence": 1.5}, "ck_impact_evaluations_confidence_range"),
        ({"input_hash": "Z" * 64}, "ck_impact_evaluations_input_hash_hex"),
    ],
)
async def test_ai_evaluation_checks(
    session: AsyncSession, overrides: dict[str, Any], constraint: str
) -> None:
    w = await _world(session)
    await _refused(session, constraint, _ai(w["deed"].id, **overrides))


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE impact_evaluations SET rule_points = 4 WHERE id = :id",
        "DELETE FROM impact_evaluations WHERE id = :id",
    ],
)
async def test_evaluations_are_append_only(session: AsyncSession, statement: str) -> None:
    w = await _world(session)
    evaluation = _evaluation(w["deed"].id)
    await _add(session, evaluation)
    with pytest.raises(DBAPIError, match="append-only"):
        async with session.begin_nested():
            await session.execute(text(statement), {"id": evaluation.id})


# --- scoring_evidence ---------------------------------------------------------------------------


async def test_photo_evidence_is_set_once(session: AsyncSession) -> None:
    w = await _world(session)
    deed_id = w["deed"].id
    await _add(session, ScoringEvidence(deed_id=deed_id))
    first = uuid.uuid4()
    await session.execute(
        text(
            "UPDATE scoring_evidence SET first_photo_ready_at = now(), first_photo_id = :p, "
            "updated_at = now() WHERE deed_id = :d"
        ),
        {"p": first, "d": deed_id},
    )
    # Touching other columns is fine.
    await session.execute(
        text("UPDATE scoring_evidence SET updated_at = now() WHERE deed_id = :d"), {"d": deed_id}
    )
    for change in (
        "first_photo_ready_at = NULL, first_photo_id = NULL",
        "first_photo_ready_at = now() + interval '1 hour'",
        f"first_photo_id = '{uuid.uuid4()}'",
    ):
        with pytest.raises(IntegrityError, match="set once"):
            async with session.begin_nested():
                await session.execute(
                    text(f"UPDATE scoring_evidence SET {change} WHERE deed_id = :d"), {"d": deed_id}
                )
    with pytest.raises(DBAPIError, match="never deleted"):
        async with session.begin_nested():
            await session.execute(
                text("DELETE FROM scoring_evidence WHERE deed_id = :d"), {"d": deed_id}
            )


async def test_photo_time_and_id_go_together(session: AsyncSession) -> None:
    w = await _world(session)
    await _refused(
        session,
        "ck_scoring_evidence_photo_pair",
        ScoringEvidence(deed_id=w["deed"].id, first_photo_ready_at=_now()),
    )


# --- month_finalizations ------------------------------------------------------------------------


def _finalization(**overrides: Any) -> MonthFinalization:
    cutoff = datetime(2026, 12, 1, 19, 30, tzinfo=UTC)
    values: dict[str, Any] = {
        "period_month": MONTH,
        "cutoff_at": cutoff,
        "finalized_at": cutoff + timedelta(minutes=5),
        "config_version": 1,
        "snapshot_count": 2,
        "titles_awarded": 3,
    }
    values.update(overrides)
    return MonthFinalization(**values)


async def test_month_finalization_is_written_once(session: AsyncSession) -> None:
    await _add(session, _finalization())
    await _refused(session, "pk_month_finalizations", _finalization())
    for statement in (
        "UPDATE month_finalizations SET titles_awarded = 0 WHERE period_month = :m",
        "DELETE FROM month_finalizations WHERE period_month = :m",
    ):
        with pytest.raises(DBAPIError, match="append-only"):
            async with session.begin_nested():
                await session.execute(text(statement), {"m": MONTH})


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        ({"period_month": date(2026, 11, 2)}, "ck_month_finalizations_first_of_month"),
        (
            {"finalized_at": datetime(2026, 12, 1, tzinfo=UTC)},
            "ck_month_finalizations_after_cutoff",
        ),
        ({"snapshot_count": -1}, "ck_month_finalizations_counts_non_negative"),
    ],
)
async def test_month_finalization_checks(
    session: AsyncSession, overrides: dict[str, Any], constraint: str
) -> None:
    await _refused(session, constraint, _finalization(**overrides))


# --- leaderboards -------------------------------------------------------------------------------


def _snapshot(region_id: int, *, final: bool, **overrides: Any) -> LeaderboardSnapshot:
    values: dict[str, Any] = {
        "region_id": region_id,
        "period_month": MONTH,
        "config_version": 1,
        "final": final,
        "entry_count": 1,
    }
    values.update(overrides)
    return LeaderboardSnapshot(**values)


def _board_entry(snapshot: LeaderboardSnapshot, user: User, **overrides: Any) -> LeaderboardEntry:
    values: dict[str, Any] = {
        "snapshot_id": snapshot.id,
        "user_id": user.id,
        "rank": 1,
        "rank_score": 21,
        "deed_count": 1,
        "doer_points": 20,
        "distinct_reporters": 1,
        "distinct_days": 1,
    }
    values.update(overrides)
    return LeaderboardEntry(**values)


async def test_one_final_snapshot_per_region_and_month(session: AsyncSession) -> None:
    w = await _world(session)
    region = w["region"]
    await _add(session, _snapshot(region, final=False), _snapshot(region, final=False))
    await _add(session, _snapshot(region, final=True))
    await _refused(session, "uq_leaderboard_snapshots_final", _snapshot(region, final=True))
    # Another month is separate.
    await _add(session, _snapshot(region, final=True, period_month=date(2026, 10, 1)))


async def test_final_snapshots_and_their_entries_never_change(session: AsyncSession) -> None:
    w = await _world(session)
    provisional = _snapshot(w["region"], final=False)
    final = _snapshot(w["region"], final=True)
    await _add(session, provisional, final)
    await _add(session, _board_entry(provisional, w["doer"]), _board_entry(final, w["doer"]))
    for statement, params in (
        ("UPDATE leaderboard_snapshots SET entry_count = 5 WHERE id = :id", {"id": final.id}),
        ("DELETE FROM leaderboard_snapshots WHERE id = :id", {"id": final.id}),
        ("UPDATE leaderboard_entries SET rank = 2 WHERE snapshot_id = :id", {"id": final.id}),
        ("DELETE FROM leaderboard_entries WHERE snapshot_id = :id", {"id": final.id}),
    ):
        with pytest.raises(DBAPIError, match="never change"):
            async with session.begin_nested():
                await session.execute(text(statement), params)
    # A provisional snapshot can be replaced: its entries go with it.
    await session.execute(
        text("UPDATE leaderboard_entries SET rank = 2 WHERE snapshot_id = :id"),
        {"id": provisional.id},
    )
    await session.execute(
        text("DELETE FROM leaderboard_snapshots WHERE id = :id"), {"id": provisional.id}
    )
    left = await session.scalar(
        text("SELECT count(*) FROM leaderboard_entries WHERE snapshot_id = :id"),
        {"id": provisional.id},
    )
    assert left == 0


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        ({"rank": 0}, "ck_leaderboard_entries_rank_positive"),
        ({"deed_count": 0}, "ck_leaderboard_entries_deed_count_positive"),
        ({"distinct_days": -1}, "ck_leaderboard_entries_distinct_non_negative"),
    ],
)
async def test_board_entry_checks(
    session: AsyncSession, overrides: dict[str, Any], constraint: str
) -> None:
    w = await _world(session)
    snapshot = _snapshot(w["region"], final=False)
    await _add(session, snapshot)
    await _refused(session, constraint, _board_entry(snapshot, w["doer"], **overrides))


async def test_snapshot_checks(session: AsyncSession) -> None:
    w = await _world(session)
    await _refused(
        session,
        "ck_leaderboard_snapshots_first_of_month",
        _snapshot(w["region"], final=False, period_month=DAY),
    )
    await _refused(
        session,
        "ck_leaderboard_snapshots_entry_count_non_negative",
        _snapshot(w["region"], final=False, entry_count=-1),
    )


# --- hero_titles and user_badges ----------------------------------------------------------------


async def _title(session: AsyncSession, w: dict[str, Any], **overrides: Any) -> HeroTitle:
    if "snapshot" not in w:
        w["snapshot"] = _snapshot(w["region"], final=True)
        await _add(session, w["snapshot"])
    values: dict[str, Any] = {
        "period_month": MONTH,
        "region_id": w["region"],
        "level_kind": TitleLevel.CITY,
        "place": 1,
        "title_key": "hero",
        "user_id": w["doer"].id,
        "snapshot_id": w["snapshot"].id,
        "config_version": 1,
    }
    values.update(overrides)
    return HeroTitle(**values)


def _badge(w: dict[str, Any], **overrides: Any) -> UserBadge:
    values: dict[str, Any] = {
        "user_id": w["doer"].id,
        "badge_slug": "first_recognised",
        "config_version": 1,
        "trigger_deed_id": w["deed"].id,
    }
    values.update(overrides)
    return UserBadge(**values)


async def test_one_title_per_user_region_and_month(session: AsyncSession) -> None:
    w = await _world(session)
    title = await _title(session, w)
    await _add(session, title)
    second = HeroTitle(
        period_month=MONTH,
        region_id=w["region"],
        level_kind=TitleLevel.CITY,
        place=2,
        title_key="top3",
        user_id=w["doer"].id,
        snapshot_id=title.snapshot_id,
        config_version=1,
    )
    await _refused(session, "uq_hero_titles_month_region_user", second)
    # A ward title in the same month is allowed.
    ward = await session.scalar(select(Region.id).where(Region.kind == "ward").limit(1))
    assert ward is not None
    ward_snapshot = _snapshot(ward, final=True)
    await _add(session, ward_snapshot)
    await _add(
        session,
        HeroTitle(
            period_month=MONTH,
            region_id=ward,
            level_kind=TitleLevel.WARD,
            place=1,
            title_key="hero",
            user_id=w["doer"].id,
            snapshot_id=ward_snapshot.id,
            config_version=1,
        ),
    )


async def test_one_badge_per_user_and_slug(session: AsyncSession) -> None:
    w = await _world(session)
    await _add(session, _badge(w))
    await _refused(session, "uq_user_badges_user_badge", _badge(w))
    await _add(session, _badge(w, badge_slug="kind_5"))


@pytest.mark.parametrize("table", ["hero_titles", "user_badges"])
async def test_titles_and_badges_can_only_be_revoked_once(
    session: AsyncSession, table: str
) -> None:
    w = await _world(session)
    row: HeroTitle | UserBadge = await _title(session, w) if table == "hero_titles" else _badge(w)
    await _add(session, row)
    for statement in (
        f"UPDATE {table} SET config_version = 2 WHERE id = :id",
        f"UPDATE {table} SET revoked_at = now(), revoked_reason = 'fraud', "
        f"user_id = :other WHERE id = :id",
    ):
        with pytest.raises(DBAPIError, match="can only be revoked"):
            async with session.begin_nested():
                await session.execute(text(statement), {"id": row.id, "other": w["reporter"].id})
    with pytest.raises(DBAPIError, match="never deleted"):
        async with session.begin_nested():
            await session.execute(text(f"DELETE FROM {table} WHERE id = :id"), {"id": row.id})
    with pytest.raises(IntegrityError, match=f"ck_{table}_revoked_has_reason"):
        async with session.begin_nested():
            await session.execute(
                text(f"UPDATE {table} SET revoked_at = now() WHERE id = :id"), {"id": row.id}
            )
    await session.execute(
        text(
            f"UPDATE {table} SET revoked_at = now(), revoked_reason = 'confirmed fraud', "
            "revoked_by = :admin WHERE id = :id"
        ),
        {"id": row.id, "admin": w["reporter"].id},
    )
    with pytest.raises(DBAPIError, match="can only be revoked"):
        async with session.begin_nested():
            await session.execute(
                text(f"UPDATE {table} SET revoked_reason = 'changed' WHERE id = :id"),
                {"id": row.id},
            )


async def test_title_checks(session: AsyncSession) -> None:
    w = await _world(session)
    await _refused(session, "ck_hero_titles_place_positive", await _title(session, w, place=0))
    await _refused(
        session, "ck_hero_titles_first_of_month", await _title(session, w, period_month=DAY)
    )
    await _refused(
        session, "ck_hero_titles_title_level", await _title(session, w, level_kind="galaxy")
    )
    await _refused(
        session,
        "ck_hero_titles_revoked_by_only_if_revoked",
        await _title(session, w, revoked_by=w["reporter"].id),
    )


# --- config seeds -------------------------------------------------------------------------------


async def test_scoring_v1_has_the_po_values(session: AsyncSession) -> None:
    row = await session.get(ConfigVersion, ("scoring", 1))
    assert row is not None and row.created_by is None and "Q45" in row.reason
    v = row.values
    categories = await session.get(ConfigVersion, ("deeds", 7))
    assert categories is not None
    slugs = {c["slug"] for c in categories.values["categories"]}
    # Q45 = C: every category gives 10, and the seeds agree with the deed categories (§2.6).
    assert v["category_base_points"] == dict.fromkeys(slugs, 10)
    assert (v["min_points"], v["photo_bonus"], v["photo_bonus_window_hours"]) == (4, 2, 24)
    assert (v["reporter_points"], v["reporter_points_beyond_cap"]) == (2, 1)
    assert (v["doer_daily_cap"], v["reporter_daily_cap"]) == (10, 10)
    assert v["timezone"] == "Asia/Kolkata"
    assert v["pair_window"] == "ist_month" and v["pair_roles"] == ["doer", "reporter"]
    assert v["pair_multipliers"] == [
        {"from_ordinal": 1, "percent": 100},
        {"from_ordinal": 4, "percent": 75},
        {"from_ordinal": 8, "percent": 50},
    ]
    assert v["text_rules"]["spam_patterns"] == [r"(?i)\b(follow me|subscribe|whatsapp me|dm me)\b"]
    assert await session.get(ConfigVersion, ("scoring", 2)) is None


async def test_leaderboard_v1_has_the_po_values(session: AsyncSession) -> None:
    row = await session.get(ConfigVersion, ("leaderboard", 1))
    assert row is not None and row.created_by is None and "Q58" in row.reason
    v = row.values
    assert v["weights"] == {"deeds": 1, "points": 1}
    assert v["shared_rank_style"] == "dense"  # Q58 = B
    assert (v["refresh_seconds"], v["finalize_margin_minutes"], v["page_size"]) == (300, 60, 50)
    levels = {lv["kind"]: lv for lv in v["levels"]}
    assert set(levels) == {"ward", "city", "state", "country"}
    ward, city = levels["ward"], levels["city"]
    assert ward["enabled"] and city["enabled"]
    assert not levels["state"]["enabled"] and not levels["country"]["enabled"]
    assert levels["state"]["titles"] == levels["country"]["titles"] == []
    assert (ward["min_counted_deeds"], ward["min_distinct_reporters"]) == (3, 2)
    assert (city["min_counted_deeds"], city["min_distinct_reporters"]) == (0, 0)
    assert [(t["key"], t["places"]) for t in ward["titles"]] == [("hero", [1])]
    assert [(t["key"], t["places"]) for t in city["titles"]] == [("hero", [1]), ("top3", [2, 3])]
    labels = {
        t["key"]: t["label"].format(region="Bangalore", month="November 2026")
        for t in city["titles"]
    }
    assert labels == {
        "hero": "Bangalore City Kindness Hero · November 2026",
        "top3": "Top 3 in Bangalore · November 2026",
    }
    assert [(t["slug"], t["min_points"]) for t in v["tiers"]] == [
        ("seed", 0),
        ("sprout", 50),
        ("tree", 250),
        ("forest", 1000),
    ]
    assert [(b["slug"], b["kind"], b["threshold"]) for b in v["badges"]] == [
        ("first_recognised", "doer_deeds", 1),
        ("kind_5", "doer_deeds", 5),
        ("kind_25", "doer_deeds", 25),
        ("kind_100", "doer_deeds", 100),
        ("first_witness", "reports", 1),
        ("witness_10", "reports", 10),
        ("witness_50", "reports", 50),
    ]


# --- migration ----------------------------------------------------------------------------------


@pytest.fixture
def sync_conn(migrated_db: None) -> Iterator[Connection]:
    """A sync connection in a transaction that is always rolled back."""
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


def _load_m20() -> Any:
    spec = importlib.util.spec_from_file_location("migration_0020", M20)
    assert spec is not None and spec.loader is not None
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_config_seed_is_safe_to_rerun(sync_conn: Connection) -> None:
    m = _load_m20()
    count = text(
        "SELECT count(*) FROM config_versions WHERE namespace IN ('scoring', 'leaderboard')"
    )
    assert sync_conn.execute(count).scalar_one() == 2
    with Operations.context(MigrationContext.configure(sync_conn)):
        m.seed_config()
    assert sync_conn.execute(count).scalar_one() == 2


def test_downgrade_and_upgrade_round_trip(sync_conn: Connection) -> None:
    """In a transaction that is rolled back: downgrade drops every scoring table, trigger,
    function and both config namespaces; upgrade restores them with the same seeds."""
    m = _load_m20()
    seeds = text(
        "SELECT namespace, version, values FROM config_versions "
        "WHERE namespace IN ('scoring', 'leaderboard') ORDER BY namespace"
    )
    before = sync_conn.execute(seeds).all()
    tables = text(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = ANY(:t)"
    ).bindparams(t=list(m.TABLES))
    functions = text("SELECT count(*) FROM pg_proc WHERE proname = ANY(:f)").bindparams(
        f=list(m._FUNCTIONS)
    )
    assert sync_conn.execute(tables).scalar_one() == len(m.TABLES)
    with Operations.context(MigrationContext.configure(sync_conn)):
        m.downgrade()
        assert sync_conn.execute(tables).scalar_one() == 0
        assert sync_conn.execute(functions).scalar_one() == 0
        assert sync_conn.execute(seeds).all() == []
        m.upgrade()
    assert sync_conn.execute(tables).scalar_one() == len(m.TABLES)
    assert sync_conn.execute(functions).scalar_one() == len(m._FUNCTIONS)
    assert sync_conn.execute(seeds).all() == before
    triggers = sync_conn.execute(
        text(
            "SELECT count(*) FROM pg_trigger WHERE NOT tgisinternal AND tgname = ANY(:names)"
        ).bindparams(names=[t[0] for t in m._TRIGGERS])
    ).scalar_one()
    assert triggers == len(m._TRIGGERS)
