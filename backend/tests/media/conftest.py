from collections.abc import AsyncIterator, Iterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.storage import MemoryStorage, get_storage
from app.main import app


@pytest.fixture
def storage() -> Iterator[MemoryStorage]:
    """In-memory object storage, also used by the API through ``get_storage``."""
    store = MemoryStorage()
    app.dependency_overrides[get_storage] = lambda: store
    yield store
    app.dependency_overrides.pop(get_storage, None)


@pytest.fixture
async def client(session: AsyncSession, storage: MemoryStorage) -> AsyncIterator[AsyncClient]:
    """Like the deeds `client`: a failed request rolls back its uncommitted work, as the real
    `get_session` does. Commit setup data before calling the API."""

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
