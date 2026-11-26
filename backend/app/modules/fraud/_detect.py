"""Running the detectors (docs/M5-DESIGN.md D82, §4.3): per event (the ``DeedVerified`` handler)
and as a sweep (the nightly job and the CLI). Detectors only return findings; every finding goes
through ``_cases.raise_flag``. Detectors come from the one registry, ``detectors.DETECTORS``,
read at call time (D102).
"""

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import SessionLocal
from app.core.events import DomainEvent
from app.modules.fraud import detectors
from app.modules.fraud._cases import RaiseResult, raise_flag
from app.modules.fraud._limits import FraudConfig, load_fraud
from app.modules.fraud.detectors.base import Detector, SweepWindow
from app.modules.fraud.models import DetectorRunStatus, DetectorRunTrigger, FraudDetectorRun

log = logging.getLogger(__name__)


def enabled_detectors(config: FraudConfig) -> list[Detector]:
    return [d for d in detectors.DETECTORS if config.enabled(d.name)]


async def run_on_event(session: AsyncSession, event: DomainEvent) -> list[RaiseResult]:
    """Every enabled detector checks ``event``; each finding opens or joins a case."""
    version, config = await load_fraud(session)
    results: list[RaiseResult] = []
    for detector in enabled_detectors(config):
        for finding in await detector.on_event(session, event, config):
            raised = await raise_flag(session, finding, config, config_version=version)
            if raised is not None:
                results.append(raised)
    return results


async def run_sweep(
    session: AsyncSession,
    detector: Detector,
    window: SweepWindow,
    *,
    trigger: DetectorRunTrigger,
) -> FraudDetectorRun | None:
    """Sweep one detector over ``window`` and record it in ``fraud_detector_runs``. Returns
    ``None`` (and records nothing) when the detector is switched off. The caller commits."""
    version, config = await load_fraud(session)
    if not config.enabled(detector.name):
        return None
    run = FraudDetectorRun(
        detector=detector.name,
        trigger=trigger,
        window_start=window.since,
        window_end=window.until,
        config_version=version,
        started_at=datetime.now(UTC),
    )
    session.add(run)
    await session.flush()
    result = await detector.sweep(session, window, config)
    opened = added = 0
    for finding in result.findings:
        raised = await raise_flag(session, finding, config, config_version=version)
        if raised is not None:
            opened += raised.created
            added += raised.deeds_added
    run.edges_scanned = result.edges_scanned
    run.cases_opened = opened
    run.deeds_added = added
    run.status = DetectorRunStatus.TRUNCATED if result.truncated else DetectorRunStatus.DONE
    run.finished_at = datetime.now(UTC)
    await session.flush()
    return run


async def _record_failure(
    detector: Detector, window: SweepWindow, trigger: DetectorRunTrigger
) -> None:
    async with SessionLocal() as session, session.begin():
        version, _ = await load_fraud(session)
        now = datetime.now(UTC)
        session.add(
            FraudDetectorRun(
                detector=detector.name,
                trigger=trigger,
                window_start=window.since,
                window_end=window.until,
                config_version=version,
                started_at=now,
                finished_at=now,
                status=DetectorRunStatus.FAILED,
            )
        )


async def sweep_detector(
    detector: Detector, window: SweepWindow, trigger: DetectorRunTrigger
) -> FraudDetectorRun | None:
    """``run_sweep`` in its own transaction; a failure is recorded as a ``failed`` run (in a
    separate transaction) and re-raised."""
    try:
        async with SessionLocal() as session, session.begin():
            return await run_sweep(session, detector, window, trigger=trigger)
    except Exception:
        log.exception("fraud sweep %s failed", detector.name)
        await _record_failure(detector, window, trigger)
        raise


async def nightly_windows(now: datetime) -> list[tuple[Detector, SweepWindow]]:
    """Each enabled detector with its sweep window ending ``now``."""
    async with SessionLocal() as session:
        _, config = await load_fraud(session)
    return [
        (d, SweepWindow(since=now - timedelta(days=d.sweep_days(config)), until=now))
        for d in enabled_detectors(config)
    ]
