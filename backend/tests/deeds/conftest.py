from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.main import app


@pytest.fixture
async def client(session: AsyncSession) -> AsyncIterator[AsyncClient]:
    """Like the root `client`, but a failed request rolls back its uncommitted work, as the
    real `get_session` does. Commit setup data before calling the API."""

    async def _session() -> AsyncIterator[AsyncSession]:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise

    app.dependency_overrides[get_session] = _session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.pop(get_session, None)


@pytest.fixture
async def other_ip(client: AsyncClient) -> AsyncIterator[AsyncClient]:
    """A second client on another IP, sharing the same overrides as `client`."""
    transport = ASGITransport(app=app, client=("203.0.113.9", 4321))
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
