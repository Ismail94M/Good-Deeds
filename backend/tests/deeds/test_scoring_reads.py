"""``deeds.service`` reads for scoring (docs/M4-DESIGN.md §3.1): ``scoring_inputs``,
``pair_ordinal``, ``revision_time``, ``withdraw_window_hours`` and the region reads (D65, D66)."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.deeds import _edits
from app.modules.deeds import service as deeds
from app.modules.deeds._limits import load_limits
from app.modules.deeds.models import Area, Deed, DeedRevision
from app.modules.deeds.schemas import DeedEdit
from tests.deeds.helpers import members, scan_and_report
from tests.deeds.invite_helpers import withdraw

# 1 November 2026 00:00 IST.
NOV_1 = datetime(2026, 10, 31, 18, 30, tzinfo=UTC)
DAY = timedelta(days=1)


async def test_scoring_inputs_reads_the_deed_live(session: AsyncSession) -> None:
    witness, doer = await members(session, 2)
    out = await scan_and_report(session, witness, doer)
    inputs = await deeds.scoring_inputs(session, out.id)
    assert inputs is not None
    assert inputs.status == deeds.DeedStatus.VERIFIED
    assert (inputs.reporter_id, inputs.doer_id) == (witness.user_id, doer.user_id)
    assert inputs.category == out.category and inputs.description == out.description
    assert inputs.verified_at == out.verified_at and inputs.reported_at == out.reported_at
    assert inputs.counts_for_points is True
    assert inputs.same_act_group_id is not None

    await session.execute(update(Deed).where(Deed.id == out.id).values(category="community"))
    changed = await deeds.scoring_inputs(session, out.id)
    assert changed is not None and changed.category == "community"


async def test_scoring_inputs_unknown_deed(session: AsyncSession) -> None:
    assert await deeds.scoring_inputs(session, uuid.uuid4()) is None


async def test_pair_ordinal_counts_every_verified_report_of_the_pair_this_ist_month(
    session: AsyncSession,
) -> None:
    witness, doer, other = await members(session, 3)
    # The last October report (23:59 IST on 31 Oct) is in another month.
    october = await scan_and_report(session, witness, doer, at=NOV_1 - timedelta(minutes=1))
    nov = [await scan_and_report(session, witness, doer, at=NOV_1 + i * DAY) for i in range(4)]
    # Another reporter about the same doer is another pair.
    await scan_and_report(session, other, doer, at=NOV_1 + timedelta(hours=1))
    assert october.verified_at is not None
    pair = (doer.user_id, witness.user_id)
    assert await deeds.pair_ordinal(session, *pair, october.verified_at) == 1

    ordinals = []
    for d in nov:
        assert d.verified_at is not None
        ordinals.append(
            await deeds.pair_ordinal(session, doer.user_id, witness.user_id, d.verified_at)
        )
    assert ordinals == [1, 2, 3, 4]

    # A withdrawal never changes another report's ordinal.
    await withdraw(session, witness, nov[1].id, at=NOV_1 + DAY + timedelta(hours=1))
    assert nov[3].verified_at is not None
    assert await deeds.pair_ordinal(session, *pair, nov[3].verified_at) == 4


async def test_revision_time_and_window_hours(session: AsyncSession) -> None:
    witness, doer = await members(session, 2)
    out = await scan_and_report(session, witness, doer)
    _, limits = await load_limits(session)
    await _edits.edit_deed(
        session,
        witness.user_id,
        out.id,
        DeedEdit(description="Carried an old man's shopping up three flights"),
        limits=limits,
    )
    revision = await session.scalar(select(DeedRevision).where(DeedRevision.deed_id == out.id))
    assert revision is not None
    assert await deeds.revision_time(session, revision.id) == revision.created_at
    assert await deeds.withdraw_window_hours(session) == limits.withdraw_window_hours == 24


async def test_deed_region_is_the_ward_or_the_city(session: AsyncSession) -> None:
    witness, doer, other = await members(session, 3)
    in_ward = await scan_and_report(session, witness, doer)
    nowhere = await scan_and_report(session, other, doer, east_m=50)
    area = await session.scalar(select(Area).order_by(Area.id).limit(1))
    assert area is not None
    await session.execute(update(Deed).where(Deed.id == in_ward.id).values(area_id=area.id))
    await session.execute(update(Deed).where(Deed.id == nowhere.id).values(area_id=None))

    ward = await deeds.deed_region(session, in_ward.id)
    assert ward is not None
    assert (ward.id, ward.kind) == (area.region_id, deeds.RegionKind.WARD)
    city = await deeds.deed_region(session, nowhere.id)
    assert city is not None
    assert (city.code, city.kind) == ("IN-KA-BLR", deeds.RegionKind.CITY)

    chain = await deeds.region_ancestors(session, ward.id)
    assert [r.kind for r in chain] == [
        deeds.RegionKind.WARD,
        deeds.RegionKind.CITY,
        deeds.RegionKind.STATE,
        deeds.RegionKind.COUNTRY,
    ]
    assert chain[1] == city and [r.code for r in chain[2:]] == ["IN-KA", "IN"]
    assert await deeds.region_ancestors(session, -1) == []
    assert await deeds.region_by_code(session, "IN-KA-BLR") == city
    assert await deeds.region_by_code(session, "NOPE") is None


async def test_regions_search_by_kind_and_name(session: AsyncSession) -> None:
    area = await session.scalar(select(Area).order_by(Area.id).limit(1))
    assert area is not None
    found = await deeds.regions(session, deeds.RegionKind.WARD, area.name[:4].lower())
    assert area.region_id in {r.id for r in found}
    assert all(area.name[:4].lower() in r.name.lower() for r in found)
    assert all(r.kind == deeds.RegionKind.WARD for r in found)
    cities = await deeds.regions(session, deeds.RegionKind.CITY)
    assert [c.code for c in cities] == ["IN-KA-BLR"]
    # LIKE wildcards are matched literally.
    assert await deeds.regions(session, deeds.RegionKind.WARD, "%") == []
    assert len(await deeds.regions(session, deeds.RegionKind.WARD, limit=5)) == 5
