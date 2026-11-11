from app.core.db import SessionLocal
from app.core.jobs import job_app
from app.core.ratelimit import purge_old
from app.modules.identity import service


@job_app.periodic(cron="17 3 * * *", periodic_id="identity_retention")
@job_app.task(name="identity.purge_expired_records", queueing_lock="identity_retention")
async def purge_expired_records(timestamp: int) -> None:
    """Nightly data retention: old OTPs, dead sessions and rate-limit counters."""
    async with SessionLocal() as session, session.begin():
        await service.purge_expired_records(session)
        await purge_old(session)
