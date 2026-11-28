"""Badges and tier (docs/M4-DESIGN.md D70, §4.6, PO Q53, Q55; §8): awarded only once the
counting deed is past its 24 h window, idempotent and permanent; tier from non-provisional
all-time doer points (49/50 boundary)."""

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import OutboxEvent
from app.modules.scoring import service
from app.modules.scoring._badges import award_badges
from app.modules.scoring.models import UserBadge
from tests.deeds.helpers import members
from tests.scoring.board_helpers import city, counted, ist, reverse

WINDOW = timedelta(hours=24)


async def _badges(session: AsyncSession, user_id: object) -> dict[str, object]:
    rows = await session.scalars(select(UserBadge).where(UserBadge.user_id == user_id))
    return {b.badge_slug: b.trigger_deed_id for b in rows}


async def test_badges_wait_for_the_window_then_award_once(session: AsyncSession) -> None:
    doer, reporter = await members(session, 2)
    verified = ist(2026, 9, 3)
    deed = await counted(
        session,
        doer,
        reporter,
        region_id=await city(session),
        verified_at=verified,
        reporter_row=True,
    )

    assert await award_badges(session, now=verified + WINDOW - timedelta(seconds=1)) == 0
    assert await _badges(session, doer.user_id) == {}

    assert await award_badges(session, now=verified + WINDOW) == 2
    assert await _badges(session, doer.user_id) == {"first_recognised": deed}
    assert await _badges(session, reporter.user_id) == {"first_witness": deed}
    events = await session.scalars(
        select(OutboxEvent).where(OutboxEvent.event_type == "scoring.badge_awarded")
    )
    payloads = sorted((e.payload for e in events), key=lambda p: p["badge_slug"])
    assert [p["badge_slug"] for p in payloads] == ["first_recognised", "first_witness"]
    # M6A: the configured label travels with the event, for notifications.
    assert [p["badge_label"] for p in payloads] == ["First recognition", "First witness"]

    # Idempotent, and permanent: a later reversal never removes a badge.
    assert await award_badges(session, now=verified + 2 * WINDOW) == 0
    await reverse(session, deed)
    assert await award_badges(session, now=verified + 3 * WINDOW) == 0
    assert await _badges(session, doer.user_id) == {"first_recognised": deed}


async def test_threshold_badge_names_the_deed_that_crossed_it(session: AsyncSession) -> None:
    doer, reporter = await members(session, 2)
    bangalore = await city(session)
    deeds = [
        await counted(session, doer, reporter, region_id=bangalore, verified_at=ist(2026, 9, day))
        for day in range(1, 7)
    ]
    await reverse(session, deeds[0])  # withdrawn inside its window: never counted
    # Deeds 2..6 count; the 5th of them (deeds[5]) crosses kind_5, once its window ends.
    assert await award_badges(session, now=ist(2026, 9, 6, 11)) == 1  # first_recognised only
    assert await award_badges(session, now=ist(2026, 9, 7, 12)) == 1
    badges = await _badges(session, doer.user_id)
    assert badges == {"first_recognised": deeds[1], "kind_5": deeds[5]}


async def test_tier_from_non_provisional_doer_points(session: AsyncSession) -> None:
    a, b, reporter = await members(session, 3)
    bangalore = await city(session)
    verified = ist(2026, 9, 3)
    await counted(session, a, reporter, region_id=bangalore, verified_at=verified, points=49)
    await counted(session, b, reporter, region_id=bangalore, verified_at=verified, points=50)
    # A provisional deed doesn't count yet.
    late = verified + timedelta(hours=12)
    await counted(session, a, reporter, region_id=bangalore, verified_at=late, points=10)

    now = verified + WINDOW
    tiers = await service.tier_for(session, [a.user_id, b.user_id, reporter.user_id], now=now)
    assert (tiers[a.user_id].slug, tiers[b.user_id].slug) == ("seed", "sprout")
    assert tiers[reporter.user_id].slug == "seed"  # reporter points never make a tier

    later = await service.tier_for(session, [a.user_id], now=late + WINDOW)
    assert later[a.user_id].slug == "sprout"
