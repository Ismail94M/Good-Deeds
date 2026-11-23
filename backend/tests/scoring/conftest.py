import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.scoring._limits import load_leaderboard
from tests.deeds.helpers import publish_limits
from tests.scoring.board_helpers import publish_board


@pytest.fixture
async def invites_on(session: AsyncSession) -> None:
    """Invites are off by default (D17); turn them on for this test (rolled back with it)."""
    await publish_limits(session, invites_enabled=True)


@pytest.fixture
async def city_board_on(session: AsyncSession) -> None:
    """City boards are off in the seeded config (PO 2026-11-23, migration 0022: Area only, City
    locked). Tests of the level hierarchy turn City on so the code that serves it once it opens
    stays tested (rolled back with the test)."""
    _, current = await load_leaderboard(session)
    levels = tuple(
        level.model_copy(update={"enabled": True}) if level.kind == "city" else level
        for level in current.levels
    )
    await publish_board(session, levels=levels)
