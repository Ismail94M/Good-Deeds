"""Races that slip past the service's "is it taken?" checks hit the DB unique
constraints and must come back as 409, never 500."""

import secrets

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.identity import service
from app.modules.identity.models import User
from tests.identity.helpers import FakeOAuth, signup, signup_payload


async def _never_taken(session: AsyncSession, handle: str, exclude: object = None) -> bool:
    """Simulate the race: the check ran before the other request committed."""
    return False


async def test_concurrent_duplicate_handle_on_signup_is_409(
    client: AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await signup(client, handle="racer")  # committed by the "other" request
    monkeypatch.setattr(service, "_handle_taken", _never_taken)

    payload = signup_payload(handle="racer")
    response = await client.post("/api/auth/signup", json=payload)
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "handle_taken"
    count = await session.scalar(
        select(func.count()).select_from(User).where(User.handle == "racer")
    )
    assert count == 1
    assert await session.scalar(select(User.id).where(User.email == payload["email"])) is None


async def test_concurrent_duplicate_handle_on_profile_update_is_409(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await signup(client, handle="wanted")
    s = await signup(client)
    monkeypatch.setattr(service, "_handle_taken", _never_taken)

    response = await client.patch("/api/users/me", json={"handle": "wanted"}, headers=s.headers)
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "handle_taken"
    me = (await client.get("/api/users/me", headers=s.headers)).json()
    assert me["handle"] == s.handle


async def test_concurrent_generated_handle_on_oauth_is_409(
    client: AsyncClient, oauth: FakeOAuth, monkeypatch: pytest.MonkeyPatch
) -> None:
    await signup(client, handle="ravikumar0000")
    monkeypatch.setattr(service, "_handle_taken", _never_taken)
    monkeypatch.setattr(secrets, "randbelow", lambda _n: 0)

    token = oauth.google.add("g-race", name="Ravi Kumar")
    response = await client.post("/api/auth/oauth/google", json={"token": token})
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "handle_taken"
