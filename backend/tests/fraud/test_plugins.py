"""Detectors are plug-ins (docs/M5-DESIGN.md D102), plus the module's config, service reads,
review deadline, job and CLI wiring."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import DomainEvent
from app.modules.deeds.service import DeedVerified
from app.modules.fraud import detectors
from app.modules.fraud import service as fraud
from app.modules.fraud._cases import month_review_deadline, raise_flag
from app.modules.fraud._detect import run_sweep
from app.modules.fraud._limits import FraudConfig, load_fraud
from app.modules.fraud.detectors.base import Finding, SweepResult, SweepWindow
from app.modules.fraud.models import (
    DecisionNotice,
    DetectorRunStatus,
    DetectorRunTrigger,
    FlagCase,
    FlagCaseOutcome,
    FlagCaseStatus,
    FlagCaseType,
    NoticeKind,
    PrizeHold,
)
from tests.deeds.helpers import make_member, members
from tests.fraud.helpers import (
    DAY,
    case_deeds,
    cases_for,
    days_ago,
    deed,
    publish_fraud,
    run_fraud,
)

IST = ZoneInfo("Asia/Kolkata")


class DummyDetector:
    """A detector that lives only in this test: it flags the doer of every verified deed."""

    name = "dummy"
    case_type = FlagCaseType.CONTENT

    async def on_event(
        self, session: AsyncSession, event: DomainEvent, config: FraudConfig
    ) -> list[Finding]:
        if not isinstance(event, DeedVerified):
            return []
        return [
            Finding(
                self.case_type,
                frozenset({event.doer_id}),
                frozenset({event.deed_id}),
                {"text_check": "dummy"},
            )
        ]

    async def sweep(
        self, session: AsyncSession, window: SweepWindow, config: FraudConfig
    ) -> SweepResult:
        return SweepResult(edges_scanned=7)

    def sweep_days(self, config: FraudConfig) -> int:
        return 1


@pytest.fixture
def dummy(monkeypatch: pytest.MonkeyPatch) -> DummyDetector:
    plugin = DummyDetector()
    monkeypatch.setattr(detectors, "DETECTORS", [*detectors.DETECTORS, plugin])
    return plugin


async def test_a_new_detector_needs_only_a_registry_entry_and_its_config_key(
    session: AsyncSession, dummy: DummyDetector
) -> None:
    a, b = await members(session, 2)
    first = await deed(session, a, b, days_ago(3))
    await run_fraud(session, first)
    # Registered but not switched on: unknown config keys are off.
    assert await cases_for(session, FlagCaseType.CONTENT, b) == []

    _, current = await load_fraud(session)
    await publish_fraud(session, detectors_enabled={**current.detectors_enabled, "dummy": True})
    second = await deed(session, a, b, days_ago(2))
    await run_fraud(session, second)
    [case] = await cases_for(session, FlagCaseType.CONTENT, b)
    assert await case_deeds(session, case.id) == {second}
    assert case.signal == {"text_check": "dummy"}

    run = await run_sweep(
        session,
        dummy,
        SweepWindow(since=days_ago(1), until=datetime.now(UTC)),
        trigger=DetectorRunTrigger.SWEEP,
    )
    assert run is not None
    assert (run.detector, run.status, run.edges_scanned) == ("dummy", DetectorRunStatus.DONE, 7)


def test_findings_carry_numbers_and_codes_only() -> None:
    user, deed_id = uuid.uuid4(), uuid.uuid4()
    with pytest.raises(ValueError, match="number or a code"):
        Finding(FlagCaseType.RING, frozenset({user}), frozenset({deed_id}), {"x": [1]})  # type: ignore[dict-item]
    with pytest.raises(ValueError, match="short code"):
        Finding(FlagCaseType.RING, frozenset({user}), frozenset({deed_id}), {"x": "y" * 41})
    with pytest.raises(ValueError, match="subject"):
        Finding(FlagCaseType.RING, frozenset(), frozenset({deed_id}))
    with pytest.raises(ValueError, match="deed"):
        Finding(FlagCaseType.RING, frozenset({user}), frozenset())


def test_registry_names_are_unique_and_valid() -> None:
    names = [d.name for d in detectors.DETECTORS]
    assert names == ["reciprocal", "ring"]
    assert detectors.detector_by_name("ring") is detectors.DETECTORS[1]
    assert detectors.detector_by_name("nope") is None


# --------------------------------------------------- config


async def _values(session: AsyncSession) -> dict[str, Any]:
    _, config = await load_fraud(session)
    return config.model_dump(mode="json")


async def test_seeded_config_loads_and_holds_nothing_on_open(session: AsyncSession) -> None:
    version, config = await load_fraud(session)
    assert version >= 1
    assert not any(config.hold_on_open.values())
    assert config.reciprocal.window_days == 30 and config.ring.max_size == 5


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"ring": {"min_size": 2}}, "min_size"),
        ({"ring": {"max_size": 7}}, "max_size"),
        ({"ring": {"min_size": 5, "max_size": 4}}, "must not exceed"),
        ({"ring": {"min_internal_share_pct": 0}}, "min_internal_share_pct"),
        ({"ring": {"min_internal_share_pct": 101}}, "min_internal_share_pct"),
        ({"reciprocal": {"window_days": 0}}, "window_days"),
        ({"reciprocal": {"window_days": 366}}, "window_days"),
        ({"explanation": {"reply_days": 2}}, "reply_days"),
        ({"appeal": {"window_days": 91}}, "window_days"),
        ({"member_reason_labels": {"case": "x"}}, "label for"),
        ({"detectors_enabled": {"ring": True}}, "missing"),
        ({"timezone": "Mars/Base"}, "timezone"),
    ],
)
async def test_config_validation(
    session: AsyncSession, change: dict[str, Any], message: str
) -> None:
    values = await _values(session)
    for key, value in change.items():
        if key in ("detectors_enabled", "member_reason_labels") or not isinstance(value, dict):
            values[key] = value
        else:
            values[key] = {**values[key], **value}
    with pytest.raises(ValidationError, match=message):
        FraudConfig.model_validate(values)


# --------------------------------------------------- service reads


async def test_held_reads_and_open_case_users(session: AsyncSession) -> None:
    a, b = await members(session, 2)
    made = [await deed(session, a, b, days_ago(5)), await deed(session, b, a, days_ago(4))]
    await run_fraud(session, *made)
    [case] = await cases_for(session, FlagCaseType.RECIPROCAL, a, b)
    assert await fraud.held_deed_ids(session, made) == set()
    assert await fraud.held_deed_ids(session, []) == set()
    # A hold is written only by a confirmed decision (B41); simulate one.
    session.add(PrizeHold(deed_id=made[0], case_id=case.id))
    await session.flush()
    assert await fraud.is_held(session, made[0])
    assert not await fraud.is_held(session, made[1])
    assert await fraud.held_deed_ids(session, made) == {made[0]}
    outsider = await make_member(session)
    assert await fraud.open_case_user_ids(session, {a.user_id, outsider.user_id}) == {a.user_id}
    assert await fraud.open_case_user_ids(session, []) == set()


async def test_confirmed_case_for_user_needs_a_final_notice(session: AsyncSession) -> None:
    admin = await make_member(session)
    a, b = await members(session, 2)
    made = [await deed(session, a, b, days_ago(5)), await deed(session, b, a, days_ago(4))]
    await run_fraud(session, *made)
    [case] = await cases_for(session, FlagCaseType.RECIPROCAL, a, b)
    assert not await fraud.confirmed_case_for_user(session, case.id, a.user_id)

    now = datetime.now(UTC)
    case.status = FlagCaseStatus.RESOLVED
    case.outcome = FlagCaseOutcome.CONFIRMED
    case.resolved_at = now
    case.resolved_by = admin.user_id
    notice = DecisionNotice(
        user_id=a.user_id,
        kind=NoticeKind.CASE,
        case_id=case.id,
        items=[{"deed_id": str(made[0]), "effect": "exclude"}],
        reason_key="case",
        appeal_until=now + timedelta(days=30),
    )
    session.add(notice)
    await session.flush()
    # Confirmed, but the appeal window is still open.
    assert not await fraud.confirmed_case_for_user(session, case.id, a.user_id)
    notice.final_at = now + timedelta(seconds=1)
    await session.flush()
    assert await fraud.confirmed_case_for_user(session, case.id, a.user_id)
    # b has no adverse outcome (no notice); an unknown case is never confirmed.
    assert not await fraud.confirmed_case_for_user(session, case.id, b.user_id)
    assert not await fraud.confirmed_case_for_user(session, uuid.uuid4(), a.user_id)


async def test_concentration_rule_reads_the_fraud_config(session: AsyncSession) -> None:
    rule = await fraud.concentration_rule(session)
    assert (rule.enabled, rule.max_reporter_share_pct, rule.min_doer_points) == (True, 30, 50)
    _, current = await load_fraud(session)
    await publish_fraud(
        session,
        detectors_enabled={**current.detectors_enabled, "concentration": False},
        concentration={"max_reporter_share_pct": 40},
    )
    rule2 = await fraud.concentration_rule(session)
    assert not rule2.enabled and rule2.max_reporter_share_pct == 40
    assert rule2.config_version == rule.config_version + 1


# --------------------------------------------------- review deadline


def test_month_review_deadline_is_ist_month_end_plus_a_day() -> None:
    at = datetime(2026, 11, 30, 20, 0, tzinfo=UTC)  # 1 Dec 01:30 IST
    assert month_review_deadline(at, IST) == datetime(2027, 1, 2, 0, 0, tzinfo=IST)
    at = datetime(2026, 11, 15, 6, 0, tzinfo=UTC)
    assert month_review_deadline(at, IST) == datetime(2026, 12, 2, 0, 0, tzinfo=IST)


async def test_review_by_is_the_earliest_deadline_still_ahead(session: AsyncSession) -> None:
    a, b = await members(session, 2)
    made = [await deed(session, a, b, days_ago(3)), await deed(session, b, a, days_ago(2))]
    version, config = await load_fraud(session)
    finding = Finding(
        FlagCaseType.RECIPROCAL, frozenset({a.user_id, b.user_id}), frozenset(made), {"n": 1}
    )
    now = datetime.now(UTC)
    raised = await raise_flag(session, finding, config, config_version=version, now=now)
    assert raised is not None and raised.created
    case = await session.scalar(
        select(FlagCase)
        .where(FlagCase.id == raised.case_id)
        .execution_options(populate_existing=True)
    )
    assert case is not None and case.detector_config_version == version
    expected = min(
        d for d in (month_review_deadline(now - n * DAY, IST) for n in (2, 3)) if d > now
    )
    assert case.review_by == expected

    # Long after every month's deadline, a join leaves review_by null.
    later = await deed(session, a, b, days_ago(1))
    joined = await raise_flag(
        session,
        Finding(finding.case_type, finding.subject_ids, frozenset({later}), {"n": 2}),
        config,
        config_version=version,
        now=now + timedelta(days=90),
    )
    assert joined is not None and not joined.created and joined.deeds_added == 1
    await session.refresh(case)
    assert case.review_by is None and case.deed_count == 3 and case.signal == {"n": 2}


# --------------------------------------------------- job and CLI wiring


def test_nightly_sweep_job_is_registered_on_the_fraud_queue() -> None:
    from app.core.jobs import job_app
    from app.modules.fraud import jobs  # noqa: F401  (registers the task)

    task = job_app.tasks["fraud.sweep"]
    assert task.queue == "fraud"
    periodic = [p for p in job_app.periodic_registry.periodic_tasks.values() if p.task is task]
    assert [p.cron for p in periodic] == ["0 21 * * *"]  # 02:30 IST


def test_cli_offers_exactly_the_registered_detectors() -> None:
    from app.modules.fraud import cli

    with pytest.raises(SystemExit):
        cli.main(["sweep", "--detector", "nope", "--since", "2026-11-01"])
    with pytest.raises(SystemExit):
        cli.main(["sweep", "--detector", "ring", "--since", "yesterday"])
