"""Reads for the fraud module: the deed graph, pair signals, related users (docs/M5-DESIGN.md
§3.1)."""

import dataclasses
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.deeds import _qr_deeds, _scans
from app.modules.deeds import service as deeds
from app.modules.deeds._limits import load_limits
from app.modules.deeds.models import Deed, DeedStatus, QrScan
from app.modules.deeds.schemas import DeedOut, Location, QrDeedCreate, ScanCreate
from tests.deeds.helpers import Member, location, members, scan_and_report, token_for
from tests.deeds.invite_helpers import accept, create_invite, share_device, withdraw

DAY = timedelta(days=1)


def _t0() -> datetime:
    # Far enough back for several reports a day apart, inside the current limits.
    return datetime.now(UTC) - timedelta(days=6)


def _at(out: DeedOut) -> datetime:
    assert out.verified_at is not None
    return out.verified_at


async def _report_from(
    session: AsyncSession,
    witness: Member,
    doer: Member,
    *,
    at: datetime,
    device_id: str | None,
    ip: str,
) -> DeedOut:
    """A QR report whose scan came from ``device_id`` and ``ip``."""
    version, limits = await load_limits(session)
    s = await _scans.create_scan(
        session,
        witness.user_id,
        ScanCreate(
            token=await token_for(session, doer, at), location=Location.model_validate(location())
        ),
        limits=limits,
        limits_version=version,
        device_id=device_id,
        ip=ip,
        now=at,
    )
    data = QrDeedCreate(
        method="qr_scan",
        scan_id=s.scan_id,
        category="animals",
        description="Helped a stray dog across the road",
    )
    return await _qr_deeds.create_qr_deed(
        session, witness.user_id, data, limits=limits, limits_version=version, now=at
    )


# --------------------------------------------------- verified_edges


async def test_edges_are_verified_deeds_in_an_inclusive_window_in_order(
    session: AsyncSession,
) -> None:
    a, b, c = await members(session, 3)
    t0 = _t0()
    first = await scan_and_report(session, a, b, at=t0)
    second = await scan_and_report(session, b, c, at=t0 + DAY)
    third = await scan_and_report(session, c, a, at=t0 + 2 * DAY)
    users = {a.user_id, b.user_id, c.user_id}

    edges = await deeds.verified_edges(session, since=_at(first), until=_at(third), user_ids=users)
    assert [e.deed_id for e in edges] == [first.id, second.id, third.id]
    assert edges[0] == deeds.Edge(
        deed_id=first.id,
        reporter_id=a.user_id,
        doer_id=b.user_id,
        verified_at=_at(first),
        counts_for_points=True,
    )
    # Both ends inclusive; a microsecond outside drops the edge.
    inner = await deeds.verified_edges(
        session,
        since=_at(first) + timedelta(microseconds=1),
        until=_at(third) - timedelta(microseconds=1),
        user_ids=users,
    )
    assert [e.deed_id for e in inner] == [second.id]


async def test_edges_skip_deeds_that_are_no_longer_verified(session: AsyncSession) -> None:
    a, b, c = await members(session, 3)
    t0 = datetime.now(UTC) - timedelta(hours=3)
    kept = await scan_and_report(session, a, b, at=t0)
    gone = await scan_and_report(session, c, b, at=t0 + timedelta(minutes=1), east_m=5)
    await withdraw(session, c, gone.id)
    edges = await deeds.verified_edges(
        session, since=t0 - DAY, until=t0 + DAY, user_ids={b.user_id}
    )
    assert [e.deed_id for e in edges] == [kept.id]


async def test_edges_filter_by_reporter_doer_or_either(session: AsyncSession) -> None:
    a, b, c, d = await members(session, 4)
    t0 = _t0()
    ab = await scan_and_report(session, a, b, at=t0)
    ba = await scan_and_report(session, b, a, at=t0 + DAY)
    cd = await scan_and_report(session, c, d, at=t0 + 2 * DAY)
    since, until = t0 - DAY, t0 + 3 * DAY

    async def ids(user_ids: set[uuid.UUID] | None, match: deeds.EdgeMatch) -> list[uuid.UUID]:
        edges = await deeds.verified_edges(
            session, since=since, until=until, user_ids=user_ids, match=match
        )
        return [e.deed_id for e in edges if e.deed_id in {ab.id, ba.id, cd.id}]

    assert await ids({a.user_id}, "reporter") == [ab.id]  # out-edges of a BFS frontier
    assert await ids({a.user_id}, "doer") == [ba.id]
    assert await ids({a.user_id}, "any") == [ab.id, ba.id]
    assert await ids({a.user_id, c.user_id}, "reporter") == [ab.id, cd.id]
    assert await ids(set(), "any") == []
    assert await ids(None, "any") == [ab.id, ba.id, cd.id]
    limited = await deeds.verified_edges(
        session, since=since, until=until, user_ids={a.user_id, b.user_id}, limit=1
    )
    assert [e.deed_id for e in limited] == [ab.id]


# --------------------------------------------------- pair_signals


async def test_pair_signals_from_scan_device_and_network(session: AsyncSession) -> None:
    a, b, c, d = await members(session, 4)
    t0 = _t0()
    # a and b scanned from one phone and one network; c from its own; d never scanned.
    await _report_from(session, a, c, at=t0, device_id="phone-1", ip="198.51.100.7")
    await _report_from(session, b, c, at=t0 + DAY, device_id="phone-1", ip="198.51.100.7")
    await _report_from(session, c, a, at=t0 + 2 * DAY, device_id="phone-2", ip="198.51.100.8")
    await _report_from(session, c, b, at=t0 + 3 * DAY, device_id=None, ip="198.51.100.7")

    pairs = [(a.user_id, b.user_id), (b.user_id, a.user_id), (c.user_id, a.user_id)]
    pairs += [(a.user_id, d.user_id)]
    signals = await deeds.pair_signals(session, pairs)
    assert set(signals) == set(pairs)
    assert signals[(a.user_id, b.user_id)] == deeds.PairSignals(True, True)
    assert signals[(b.user_id, a.user_id)] == deeds.PairSignals(True, True)  # symmetric
    # c shared a's network once (no device on that scan), never a's device.
    assert signals[(c.user_id, a.user_id)] == deeds.PairSignals(False, True)
    assert signals[(a.user_id, d.user_id)] == deeds.PairSignals(False, False)


async def test_pair_signals_without_any_shared_hash(session: AsyncSession) -> None:
    a, b = await members(session, 2)
    t0 = _t0()
    await _report_from(session, a, b, at=t0, device_id="phone-a", ip="198.51.100.21")
    await _report_from(session, b, a, at=t0 + DAY, device_id="phone-b", ip="198.51.100.22")
    signals = await deeds.pair_signals(session, [(a.user_id, b.user_id)])
    assert signals == {(a.user_id, b.user_id): deeds.PairSignals(False, False)}
    assert await deeds.pair_signals(session, []) == {}


async def test_pair_signals_from_a_shared_device_invite(session: AsyncSession) -> None:
    reporter, doer = await members(session, 2)
    await share_device(session, reporter, doer)
    _, token = await create_invite(session, reporter)
    await accept(session, token, doer, device_id="family-phone")
    signals = await deeds.pair_signals(
        session, [(reporter.user_id, doer.user_id), (doer.user_id, reporter.user_id)]
    )
    assert signals[(reporter.user_id, doer.user_id)].shared_device is True
    assert signals[(doer.user_id, reporter.user_id)].shared_device is True
    assert signals[(reporter.user_id, doer.user_id)].shared_network is False


async def test_pair_signals_never_return_hashes(session: AsyncSession) -> None:
    a, b, c = await members(session, 3)
    t0 = _t0()
    await _report_from(session, a, c, at=t0, device_id="phone-1", ip="198.51.100.7")
    await _report_from(session, b, c, at=t0 + DAY, device_id="phone-1", ip="198.51.100.7")
    signals = await deeds.pair_signals(session, [(a.user_id, b.user_id)])
    hashes = {
        h
        for row in await session.scalars(select(QrScan))
        if row.witness_user_id in (a.user_id, b.user_id)
        for h in (row.device_hash, row.ip_hash)
        if h is not None
    }
    assert hashes
    for value in signals.values():
        fields = dataclasses.asdict(value)
        assert set(fields) == {"shared_device", "shared_network"}
        assert all(isinstance(v, bool) for v in fields.values())
        assert not hashes & {str(v) for v in fields.values()}


# --------------------------------------------------- related_users


async def test_related_users_lookback_is_inclusive_and_covers_both_roles(
    session: AsyncSession,
) -> None:
    me, early, reporter, doer, stranger = await members(session, 5)
    t0 = _t0()
    old = await scan_and_report(session, early, me, at=t0)
    as_doer = await scan_and_report(session, reporter, me, at=t0 + DAY)
    await scan_and_report(session, me, doer, at=t0 + 2 * DAY)
    await scan_and_report(session, stranger, early, at=t0 + 3 * DAY)

    since = _at(as_doer)
    assert await deeds.related_users(session, me.user_id, since) == {
        reporter.user_id,
        doer.user_id,
    }
    assert await deeds.related_users(session, me.user_id, _at(old)) == {
        early.user_id,
        reporter.user_id,
        doer.user_id,
    }
    later = since + timedelta(microseconds=1)
    assert await deeds.related_users(session, me.user_id, later) == {doer.user_id}


async def test_related_users_keep_deeds_verified_once(session: AsyncSession) -> None:
    me, other = await members(session, 2)
    t0 = datetime.now(UTC) - timedelta(hours=3)
    out = await scan_and_report(session, other, me, at=t0)
    await session.execute(update(Deed).where(Deed.id == out.id).values(status=DeedStatus.REJECTED))
    assert await deeds.related_users(session, me.user_id, t0 - DAY) == {other.user_id}
    assert await deeds.related_users(session, me.user_id, t0 + DAY) == set()
