import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tests.deeds.helpers import publish_limits


@pytest.fixture
async def invites_on(session: AsyncSession) -> None:
    """Invites are off by default (D17); turn them on for this test (rolled back with it)."""
    await publish_limits(session, invites_enabled=True)
