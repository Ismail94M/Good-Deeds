"""Nightly purge of unconsumed scans (docs/M2-DESIGN.md B9, §8; PRD Q8: 7 days)."""

from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.jobs import job_app
from app.modules.deeds._retention import purge_unconsumed_scans_batch
from app.modules.deeds.models import QrScan
from tests.deeds.helpers import count, members, publish_limits, report, scan


async def test_purges_only_old_unconsumed_scans(session: AsyncSession) -> None:
    doer, old_w, fresh_w, used_w, edge_w = await members(session, 5)
    now = datetime.now(UTC)
    old = await scan(session, old_w, doer, at=now - timedelta(days=8))
    # Consumed scans are evidence for their deed and are never purged.
    used = await scan(session, used_w, doer, at=now - timedelta(days=9), east_m=10)
    await report(session, used_w, used.scan_id, at=now - timedelta(days=9))
    # Just inside the window (its submit window closed less than 7 days ago).
    edge = await scan(session, edge_w, doer, at=now - timedelta(days=7) + timedelta(minutes=1))
    fresh = await scan(session, fresh_w, doer, at=now - timedelta(hours=1))

    deleted = await purge_unconsumed_scans_batch(session, now=now)
    assert deleted == 1
    assert await count(session, QrScan, QrScan.id == old.scan_id) == 0
    for kept in (used, edge, fresh):
        assert await count(session, QrScan, QrScan.id == kept.scan_id) == 1
    # Idempotent.
    assert await purge_unconsumed_scans_batch(session, now=now) == 0


async def test_retention_comes_from_config_and_runs_in_batches(session: AsyncSession) -> None:
    doer, *witnesses = await members(session, 4)
    now = datetime.now(UTC)
    scans = [
        await scan(session, w, doer, at=now - timedelta(days=2, minutes=i))
        for i, w in enumerate(witnesses)
    ]
    assert await purge_unconsumed_scans_batch(session, now=now) == 0
    await publish_limits(session, unconsumed_scan_retention_days=1)
    assert await purge_unconsumed_scans_batch(session, now=now, batch_size=2) == 2
    assert await purge_unconsumed_scans_batch(session, now=now, batch_size=2) == 1
    for s in scans:
        assert await count(session, QrScan, QrScan.id == s.scan_id) == 0


async def test_open_scans_are_never_purged(session: AsyncSession) -> None:
    """Even with zero retention, a scan that can still be consumed stays."""
    doer, witness = await members(session, 2)
    await publish_limits(session, unconsumed_scan_retention_days=0)
    open_scan = await scan(session, witness, doer)
    assert await purge_unconsumed_scans_batch(session) == 0
    assert await count(session, QrScan, QrScan.id == open_scan.scan_id) == 1


def test_job_is_registered_nightly() -> None:
    task = job_app.tasks["deeds.purge_unconsumed_scans"]
    assert task.queueing_lock == "deeds_scan_retention"
    periodic = [p for p in job_app.periodic_registry.periodic_tasks.values() if p.task is task]
    assert len(periodic) == 1
    assert periodic[0].cron == "37 3 * * *"
