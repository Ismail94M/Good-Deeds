import uuid
from collections.abc import AsyncIterator, Iterable, Iterator
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.storage import MemoryStorage, ObjectStorage, get_storage
from app.main import app
from app.modules.feed import _photos
from app.modules.feed.schemas import PhotoOut

# The real adapter, kept before the autouse fixture below replaces it.
REAL_PUBLIC_PHOTOS = _photos.public_photos


class FakePhotos:
    """Stands in for ``media.service.public_photos`` (built in parallel, B20): tests choose which
    deeds have photos, and every call is recorded."""

    def __init__(self) -> None:
        self.by_deed: dict[uuid.UUID, list[PhotoOut]] = {}
        self.calls: list[list[uuid.UUID]] = []

    def add(self, deed_id: uuid.UUID) -> PhotoOut:
        photo = PhotoOut(
            id=uuid.uuid4(),
            thumb_url=f"https://storage.test/thumb/{uuid.uuid4().hex}.webp",
            display_url=f"https://storage.test/display/{uuid.uuid4().hex}.webp",
            width=1280,
            height=960,
            urls_expire_at=datetime.now(UTC) + timedelta(minutes=60),
        )
        self.by_deed.setdefault(deed_id, []).append(photo)
        return photo

    async def __call__(
        self, session: AsyncSession, deed_ids: Iterable[uuid.UUID], storage: ObjectStorage
    ) -> dict[uuid.UUID, list[PhotoOut]]:
        ids = list(deed_ids)
        self.calls.append(ids)
        return {deed_id: list(self.by_deed.get(deed_id, [])) for deed_id in ids}


@pytest.fixture(autouse=True)
def photos(monkeypatch: pytest.MonkeyPatch) -> FakePhotos:
    fake = FakePhotos()
    monkeypatch.setattr(_photos, "public_photos", fake)
    return fake


@pytest.fixture(autouse=True)
def storage() -> Iterator[MemoryStorage]:
    store = MemoryStorage()
    app.dependency_overrides[get_storage] = lambda: store
    yield store
    app.dependency_overrides.pop(get_storage, None)


@pytest.fixture
async def client(session: AsyncSession) -> AsyncIterator[AsyncClient]:
    """A failed request rolls back its uncommitted work, as the real ``get_session`` does."""

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
async def invites_on(session: AsyncSession) -> None:
    """Invites are off by default (D17); turn them on for this test (rolled back with it)."""
    from tests.deeds.helpers import publish_limits

    await publish_limits(session, invites_enabled=True)
