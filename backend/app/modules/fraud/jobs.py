"""Background jobs of the fraud module (Procrastinate; docs/M5-DESIGN.md D82, §5).

Jobs run on the ``fraud`` queue, so detection can get its own worker later.
"""

from datetime import UTC, datetime

from app.core.db import SessionLocal
from app.core.jobs import job_app
from app.modules.fraud._detect import nightly_windows, sweep_detector
from app.modules.fraud._finalize import finalize_decisions as _finalize_decisions
from app.modules.fraud.models import DetectorRunTrigger


# Procrastinate's cron is in UTC: 21:00 UTC is 02:30 IST.
@job_app.periodic(cron="0 21 * * *", periodic_id="fraud_sweep")
@job_app.task(name="fraud.sweep", queue="fraud", queueing_lock="fraud_sweep")
async def sweep(timestamp: int) -> list[str]:
    """Nightly at 02:30 IST: re-run every enabled detector over its window, one
    ``fraud_detector_runs`` row each, in chunks of ``sweep_chunk_edges`` deeds committed one by
    one (SEC5-L7). The safety net after a config change or an outage, and the retry of per-deed
    ring checks that were truncated (with ``ring.retry_max_search_edges``); raising is
    idempotent, so cases the per-deed handler already opened don't change."""
    now = datetime.now(UTC)
    statuses: list[str] = []
    for detector, window in await nightly_windows(now):
        run = await sweep_detector(detector, window, DetectorRunTrigger.SWEEP)
        if run is not None:
            statuses.append(f"{detector.name}:{run.status.value}")
    return statuses


@job_app.periodic(cron="7 * * * *", periodic_id="fraud_finalize_decisions")
@job_app.task(
    name="fraud.finalize_decisions", queue="fraud", queueing_lock="fraud_finalize_decisions"
)
async def finalize_decisions(timestamp: int) -> int:
    """Hourly: notices past their appeal window become final; deeds whose rejection is final
    for everyone on them go to ``deeds`` (D100). Returns how many deeds were sent."""
    async with SessionLocal() as session, session.begin():
        result = await _finalize_decisions(session, datetime.now(UTC))
    return sum(len(ids) for ids in result.rejected.values())
