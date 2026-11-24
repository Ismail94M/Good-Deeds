from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.sms import ConsoleSmsProvider, get_sms_provider
from app.main import app
from app.modules.identity._oauth import get_oauth_verifiers
from tests.identity.helpers import FakeOAuth


@pytest.fixture
def sms() -> ConsoleSmsProvider:
    return ConsoleSmsProvider()


@pytest.fixture
def oauth() -> FakeOAuth:
    return FakeOAuth()


@pytest.fixture
async def client(
    session: AsyncSession, sms: ConsoleSmsProvider, oauth: FakeOAuth
) -> AsyncIterator[AsyncClient]:
    """Like the root `client`, but a failed request rolls back its uncommitted work.

    That matches the real `get_session` (closing the session rolls back), so tests can
    prove what a failing request did or did not persist.
    """

    async def _session() -> AsyncIterator[AsyncSession]:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise

    def _sms() -> ConsoleSmsProvider:
        return sms

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_sms_provider] = _sms
    app.dependency_overrides[get_oauth_verifiers] = oauth.verifiers
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    for dep in (get_session, get_sms_provider, get_oauth_verifiers):
        app.dependency_overrides.pop(dep, None)
