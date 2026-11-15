"""Background jobs of the deeds module (Procrastinate). Organised by section."""

from app.core.db import SessionLocal
from app.core.jobs import job_app
from app.modules.deeds import _invites, _retention

# --------------------------------------------------- invite expiry (B6)


@job_app.periodic(cron="*/15 * * * *", periodic_id="deeds_expire_invites")
@job_app.task(name="deeds.expire_invites", queueing_lock="deeds_expire_invites")
async def expire_invites(timestamp: int) -> int:
    """Every 15 minutes: pending invites past their expiry become ``expired`` and their deeds
    ``unverified``. One batch per transaction, until a batch comes back short."""
    total = 0
    while True:
        async with SessionLocal() as session, session.begin():
            count = await _invites.expire_invites(session)
        total += count
        if count < _invites.EXPIRE_BATCH_SIZE:
            return total


# --------------------------------------------------- scan retention (B9)


@job_app.periodic(cron="37 3 * * *", periodic_id="deeds_scan_retention")
@job_app.task(name="deeds.purge_unconsumed_scans", queueing_lock="deeds_scan_retention")
async def purge_unconsumed_scans(timestamp: int) -> int:
    """Nightly: delete unconsumed scans older than ``unconsumed_scan_retention_days``. One batch
    per transaction, until a batch comes back short."""
    total = 0
    while True:
        async with SessionLocal() as session, session.begin():
            count = await _retention.purge_unconsumed_scans_batch(session)
        total += count
        if count < _retention.PURGE_BATCH_SIZE:
            return total
