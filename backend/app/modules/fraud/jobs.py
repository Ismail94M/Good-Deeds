"""Background jobs of the fraud module (Procrastinate; docs/M5-DESIGN.md D82, §5).

Jobs run on the ``fraud`` queue, so detection can get its own worker later.
"""

from datetime import UTC, datetime

from app.core.jobs import job_app
from app.modules.fraud._detect import nightly_windows, sweep_detector
from app.modules.fraud.models import DetectorRunTrigger


# Procrastinate's cron is in UTC: 21:00 UTC is 02:30 IST.
@job_app.periodic(cron="0 21 * * *", periodic_id="fraud_sweep")
@job_app.task(name="fraud.sweep", queue="fraud", queueing_lock="fraud_sweep")
async def sweep(timestamp: int) -> list[str]:
    """Nightly at 02:30 IST: re-run every enabled detector over its window, one transaction and
    one ``fraud_detector_runs`` row each. The safety net after a config change or an outage;
    raising is idempotent, so cases the per-deed handler already opened don't change."""
    now = datetime.now(UTC)
    statuses: list[str] = []
    for detector, window in await nightly_windows(now):
        run = await sweep_detector(detector, window, DetectorRunTrigger.SWEEP)
        if run is not None:
            statuses.append(f"{detector.name}:{run.status.value}")
    return statuses
