"""Age + consent, onboarding status, profile edits and public profiles."""

from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sms import ConsoleSmsProvider
from app.modules.identity import service
from app.modules.identity.models import Consent, Role, User, UserStatus
from tests.identity.helpers import (
    FakeOAuth,
    adult_dob,
    onboard_phone,
    session_from,
    signup,
    valid_phone,
    years_ago,
)

# --------------------------------------------------------------------------- age + consent


async def _oauth_user_headers(client: AsyncClient, oauth: FakeOAuth) -> dict[str, str]:
    response = await client.post(
        "/api/auth/oauth/google", json={"token": oauth.google.add("g-profile")}
    )
    assert response.status_code == 200
    return session_from(response).headers


async def test_age_consent_underage_rejected_and_nothing_recorded(
    client: AsyncClient, session: AsyncSession, oauth: FakeOAuth
) -> None:
    headers = await _oauth_user_headers(client, oauth)
    dob = years_ago(18) + timedelta(days=1)
    response = await client.post(
        "/api/users/me/age-consent",
        json={"date_of_birth": dob.isoformat(), "accept_terms": True, "accept_privacy": True},
        headers=headers,
    )
    assert response.status_code == 400
    assert response.json()["code"] == "underage"
    me = (await client.get("/api/users/me", headers=headers)).json()
    assert me["onboarding"]["adult"] is False
    assert me["onboarding"]["consents"] is False


async def test_age_consent_exact_birthday_passes(client: AsyncClient, oauth: FakeOAuth) -> None:
    headers = await _oauth_user_headers(client, oauth)
    response = await client.post(
        "/api/users/me/age-consent",
        json={
            "date_of_birth": years_ago(18).isoformat(),
            "accept_terms": True,
            "accept_privacy": True,
        },
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["onboarding"]["adult"] is True


@pytest.mark.parametrize("field", ["accept_terms", "accept_privacy"])
async def test_age_consent_requires_both_consents(
    client: AsyncClient, oauth: FakeOAuth, field: str
) -> None:
    headers = await _oauth_user_headers(client, oauth)
    body = {"date_of_birth": adult_dob(), "accept_terms": True, "accept_privacy": True}
    body[field] = False
    response = await client.post("/api/users/me/age-consent", json=body, headers=headers)
    assert response.status_code == 422


async def test_age_consent_is_idempotent(
    client: AsyncClient, session: AsyncSession, oauth: FakeOAuth
) -> None:
    headers = await _oauth_user_headers(client, oauth)
    body = {"date_of_birth": adult_dob(), "accept_terms": True, "accept_privacy": True}
    first = await client.post("/api/users/me/age-consent", json=body, headers=headers)
    second = await client.post("/api/users/me/age-consent", json=body, headers=headers)
    assert first.status_code == second.status_code == 200
    user_id = first.json()["id"]
    rows = (await session.scalars(select(Consent).where(Consent.user_id == user_id))).all()
    assert len(rows) == len(service.CURRENT_CONSENTS)


async def test_age_consent_requires_login(client: AsyncClient) -> None:
    body = {"date_of_birth": adult_dob(), "accept_terms": True, "accept_privacy": True}
    assert (await client.post("/api/users/me/age-consent", json=body)).status_code == 401


async def test_new_consent_version_requires_re_acceptance(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    s = await signup(client)
    monkeypatch.setitem(service.CURRENT_CONSENTS, "privacy", "2027-01")
    me = (await client.get("/api/users/me", headers=s.headers)).json()
    assert me["onboarding"]["consents"] is False
    body = {"date_of_birth": adult_dob(), "accept_terms": True, "accept_privacy": True}
    again = await client.post("/api/users/me/age-consent", json=body, headers=s.headers)
    assert again.json()["onboarding"]["consents"] is True


async def test_withdrawn_consent_is_not_counted(client: AsyncClient, session: AsyncSession) -> None:
    s = await signup(client)
    await session.execute(
        update(Consent)
        .where(Consent.user_id == s.user_id, Consent.consent_type == "privacy")
        .values(withdrawn_at=Consent.granted_at)
    )
    await session.commit()
    me = (await client.get("/api/users/me", headers=s.headers)).json()
    assert me["onboarding"]["consents"] is False


# --------------------------------------------------------------------------- onboarding flags


async def test_onboarding_flags_across_password_flow(
    client: AsyncClient, sms: ConsoleSmsProvider
) -> None:
    s = await signup(client)
    me = (await client.get("/api/users/me", headers=s.headers)).json()
    assert me["onboarding"] == {"adult": True, "consents": True, "phone": False, "complete": False}
    await onboard_phone(client, sms, s, valid_phone(1))
    me = (await client.get("/api/users/me", headers=s.headers)).json()
    assert me["onboarding"] == {"adult": True, "consents": True, "phone": True, "complete": True}


async def test_onboarding_flags_across_oauth_flow(
    client: AsyncClient, sms: ConsoleSmsProvider, oauth: FakeOAuth
) -> None:
    response = await client.post(
        "/api/auth/oauth/google", json={"token": oauth.google.add("g-flow")}
    )
    s = session_from(response)

    async def flags() -> dict[str, bool]:
        result: dict[str, bool] = (await client.get("/api/users/me", headers=s.headers)).json()[
            "onboarding"
        ]
        return result

    assert await flags() == {"adult": False, "consents": False, "phone": False, "complete": False}
    # Phone first, then age + consent: complete only once all three are done.
    await onboard_phone(client, sms, s, valid_phone(2))
    assert await flags() == {"adult": False, "consents": False, "phone": True, "complete": False}
    body = {"date_of_birth": adult_dob(), "accept_terms": True, "accept_privacy": True}
    await client.post("/api/users/me/age-consent", json=body, headers=s.headers)
    assert await flags() == {"adult": True, "consents": True, "phone": True, "complete": True}


# --------------------------------------------------------------------------- profile edits


async def test_patch_profile_changes_handle_and_fields(
    client: AsyncClient, session: AsyncSession
) -> None:
    s = await signup(client)
    response = await client.patch(
        "/api/users/me",
        json={"handle": "NewHandle_9", "display_name": "  Asha R  ", "area": "Indiranagar"},
        headers=s.headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["handle"] == "newhandle_9"
    assert body["display_name"] == "Asha R"
    assert body["area"] == "Indiranagar"
    assert (await client.get("/api/users/newhandle_9")).status_code == 200
    assert s.handle is not None
    assert (await client.get(f"/api/users/{s.handle}")).status_code == 404


async def test_patch_profile_handle_taken(client: AsyncClient) -> None:
    await signup(client, handle="taken_one")
    s = await signup(client)
    response = await client.patch("/api/users/me", json={"handle": "TAKEN_ONE"}, headers=s.headers)
    assert response.status_code == 409
    assert response.json()["code"] == "handle_taken"


async def test_patch_profile_same_handle_is_fine(client: AsyncClient) -> None:
    s = await signup(client)
    response = await client.patch("/api/users/me", json={"handle": s.handle}, headers=s.headers)
    assert response.status_code == 200


@pytest.mark.parametrize("payload", [{"handle": "admin"}, {"handle": "a b"}, {"display_name": ""}])
async def test_patch_profile_validation(client: AsyncClient, payload: dict[str, str]) -> None:
    s = await signup(client)
    assert (await client.patch("/api/users/me", json=payload, headers=s.headers)).status_code == 422


async def test_patch_profile_clears_area_and_bio(client: AsyncClient) -> None:
    s = await signup(client)
    await client.patch(
        "/api/users/me", json={"area": "Jayanagar", "bio": "I help."}, headers=s.headers
    )
    response = await client.patch(
        "/api/users/me", json={"area": "", "bio": "   "}, headers=s.headers
    )
    assert response.status_code == 200
    assert response.json()["area"] is None
    assert response.json()["bio"] is None


async def test_patch_profile_omitted_fields_unchanged(client: AsyncClient) -> None:
    s = await signup(client)
    await client.patch("/api/users/me", json={"bio": "Keep me"}, headers=s.headers)
    response = await client.patch("/api/users/me", json={"area": "HSR"}, headers=s.headers)
    assert response.json()["bio"] == "Keep me"


async def test_patch_profile_requires_login(client: AsyncClient) -> None:
    assert (await client.patch("/api/users/me", json={"bio": "x"})).status_code == 401


# --------------------------------------------------------------------------- public profile


async def test_public_profile_shows_only_public_fields(
    client: AsyncClient, session: AsyncSession, sms: ConsoleSmsProvider
) -> None:
    s = await signup(client, handle="publicperson")
    await onboard_phone(client, sms, s, valid_phone(3))
    await client.patch(
        "/api/users/me", json={"bio": "Hello", "area": "Koramangala"}, headers=s.headers
    )
    await session.execute(update(User).where(User.id == s.user_id).values(role=Role.MODERATOR))
    await session.commit()

    response = await client.get("/api/users/PublicPerson")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"handle", "display_name", "city", "area", "bio", "joined_at"}
    assert body["handle"] == "publicperson"
    assert body["city"] == "Bangalore"
    text = response.text
    assert s.email is not None
    for secret in (s.email, valid_phone(3), valid_phone(3)[3:], str(s.user_id), "moderator"):
        assert secret not in text


async def test_public_profile_unknown_404(client: AsyncClient) -> None:
    response = await client.get("/api/users/nobody_at_all")
    assert response.status_code == 404
    assert response.json()["code"] == "user_not_found"


@pytest.mark.parametrize("status", [UserStatus.SUSPENDED, UserStatus.BLOCKED])
async def test_public_profile_inactive_user_404(
    client: AsyncClient, session: AsyncSession, status: UserStatus
) -> None:
    s = await signup(client)
    await session.execute(update(User).where(User.id == s.user_id).values(status=status))
    await session.commit()
    assert (await client.get(f"/api/users/{s.handle}")).status_code == 404


async def test_users_me_is_not_shadowed_by_public_profile(client: AsyncClient) -> None:
    assert (await client.get("/api/users/me")).status_code == 401
