"""Background jobs of the scoring module (Procrastinate; docs/M4-DESIGN.md §5)."""

from app.core.db import SessionLocal
from app.core.jobs import job_app
from app.modules.scoring import _badges, _concentration, _finalize, _leaderboard, _overrides


@job_app.periodic(cron="* * * * *", periodic_id="scoring_refresh_leaderboards")
@job_app.task(name="scoring.refresh_leaderboards", queueing_lock="scoring_refresh_leaderboards")
async def refresh_leaderboards(timestamp: int) -> list[str]:
    """Every minute: refresh each open month whose newest snapshot is older than
    ``leaderboard.refresh_seconds`` (so the interval follows config)."""
    async with SessionLocal() as session, session.begin():
        return [m.isoformat() for m in await _leaderboard.refresh_due(session)]


@job_app.periodic(cron="*/15 * * * *", periodic_id="scoring_finalize_month")
@job_app.task(name="scoring.finalize_month", queueing_lock="scoring_finalize_month")
async def finalize_month(timestamp: int) -> list[str]:
    """Every 15 minutes: finalize past months that are due, oldest first, one transaction each.
    Stops at the first month that has to wait (D64)."""
    finalized: list[str] = []
    while True:
        async with SessionLocal() as session, session.begin():
            result = await _finalize.finalize_due(session)
        if result is None or result.outcome != _finalize.Outcome.FINALIZED:
            return finalized
        finalized.append(result.month.isoformat())


@job_app.periodic(cron="*/15 * * * *", periodic_id="scoring_award_badges")
@job_app.task(name="scoring.award_badges", queueing_lock="scoring_award_badges")
async def award_badges(timestamp: int) -> int:
    """Every 15 minutes: award badges whose threshold deed is past its window (D70)."""
    async with SessionLocal() as session, session.begin():
        return await _badges.award_badges(session)


@job_app.periodic(cron="*/15 * * * *", periodic_id="scoring_check_concentration")
@job_app.task(name="scoring.check_concentration", queueing_lock="scoring_check_concentration")
async def check_concentration(timestamp: int) -> int:
    """Every 15 minutes: signal reporters over the concentration limit in open months, once
    each (docs/M5-DESIGN.md D85, §4.4). Skipped while the check is off in the fraud config."""
    async with SessionLocal() as session, session.begin():
        return len(await _concentration.check_concentration(session))


@job_app.periodic(cron="7 * * * *", periodic_id="scoring_expire_override_requests")
@job_app.task(
    name="scoring.expire_override_requests", queueing_lock="scoring_expire_override_requests"
)
async def expire_override_requests(timestamp: int) -> int:
    """Hourly: big-cut override requests nobody decided in time become ``expired`` (Q77)."""
    async with SessionLocal() as session, session.begin():
        return await _overrides.expire_requests(session)
