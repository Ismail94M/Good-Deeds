"""Running the detectors (docs/M5-DESIGN.md D82, §4.3): per event (the ``DeedVerified`` handler)
and as a sweep (the nightly job and the CLI). Detectors only return findings; every finding goes
through ``_cases.raise_flag``. Detectors come from the one registry, ``detectors.DETECTORS``,
read at call time (D102).
"""

import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import SessionLocal
from app.core.events import DomainEvent
from app.modules.deeds.service import verified_edges
from app.modules.fraud import detectors
from app.modules.fraud._cases import RaiseResult, raise_flag
from app.modules.fraud._limits import FraudConfig, load_fraud
from app.modules.fraud.detectors.base import Detector, SweepWindow
from app.modules.fraud.models import DetectorRunStatus, DetectorRunTrigger, FraudDetectorRun

log = logging.getLogger(__name__)

# The smallest step of ``verified_at`` (Postgres keeps microseconds).
_TICK = timedelta(microseconds=1)


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


async def chunk_windows(
    session: AsyncSession, window: SweepWindow, chunk_edges: int
) -> AsyncIterator[SweepWindow]:
    """``window`` cut into consecutive windows of at most ``chunk_edges`` verified deeds each
    (more only when many deeds share one ``verified_at``), so a sweep works in bounded steps
    (SEC5-L7). A window with no deeds gives itself once."""
    since = window.since
    yielded = False
    while since <= window.until:
        batch = await verified_edges(
            session, since=since, until=window.until, limit=chunk_edges + 1
        )
        if len(batch) <= chunk_edges:
            if batch or not yielded:
                yield SweepWindow(since=since, until=window.until)
            return
        boundary = batch[chunk_edges].verified_at
        if boundary > since:
            yield SweepWindow(since=since, until=boundary - _TICK)
            since = boundary
        else:  # more than ``chunk_edges`` deeds at one instant: take them all at once
            yield SweepWindow(since=since, until=since)
            since = since + _TICK
        yielded = True


async def run_sweep(
    session: AsyncSession,
    detector: Detector,
    window: SweepWindow,
    *,
    trigger: DetectorRunTrigger,
    commit_chunks: bool = False,
) -> FraudDetectorRun | None:
    """Sweep one detector over ``window`` and record it in ``fraud_detector_runs``. Returns
    ``None`` (and records nothing) when the detector is switched off.

    The window is swept in chunks of at most ``sweep_chunk_edges`` deeds (SEC5-L7). With
    ``commit_chunks`` the run row and each chunk's cases are committed as they go, so the sweep
    never holds one long transaction (raising is idempotent, so a re-run after a failure changes
    nothing that was already raised); otherwise the caller commits."""
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
    run_id = run.id
    if commit_chunks:
        await session.commit()
    scanned = opened = added = 0
    truncated = False
    async for chunk in chunk_windows(session, window, config.sweep_chunk_edges):
        result = await detector.sweep(session, chunk, config)
        for finding in result.findings:
            raised = await raise_flag(session, finding, config, config_version=version)
            if raised is not None:
                opened += raised.created
                added += raised.deeds_added
        scanned += result.edges_scanned
        truncated = truncated or result.truncated
        if commit_chunks:
            await session.commit()
    row = await session.get(FraudDetectorRun, run_id)
    assert row is not None
    run = row
    run.edges_scanned = scanned
    run.cases_opened = opened
    run.deeds_added = added
    run.status = DetectorRunStatus.TRUNCATED if truncated else DetectorRunStatus.DONE
    run.finished_at = datetime.now(UTC)
    await session.flush()
    return run


async def _record_failure(
    detector: Detector, window: SweepWindow, trigger: DetectorRunTrigger
) -> None:
    """Mark the run that was started (committed, still ``running``) ``failed``, or add a failed
    row if none was committed."""
    async with SessionLocal() as session, session.begin():
        now = datetime.now(UTC)
        run = await session.scalar(
            select(FraudDetectorRun)
            .where(
                FraudDetectorRun.detector == detector.name,
                FraudDetectorRun.trigger == trigger,
                FraudDetectorRun.window_start == window.since,
                FraudDetectorRun.window_end == window.until,
                FraudDetectorRun.status == DetectorRunStatus.RUNNING,
            )
            .order_by(FraudDetectorRun.started_at.desc())
            .limit(1)
            .with_for_update()
        )
        if run is not None:
            run.status = DetectorRunStatus.FAILED
            run.finished_at = now
            return
        version, _ = await load_fraud(session)
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
    """``run_sweep`` committing chunk by chunk; a failure marks the run ``failed`` (in a
    separate transaction) and is re-raised."""
    try:
        async with SessionLocal() as session:
            run = await run_sweep(session, detector, window, trigger=trigger, commit_chunks=True)
            await session.commit()
            return run
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
