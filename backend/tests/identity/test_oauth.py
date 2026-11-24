import re
from datetime import UTC, datetime

from httpx import AsyncClient
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import OutboxEvent
from app.modules.identity.models import DoerCode, OAuthAccount, User, UserStatus
from tests.identity.helpers import (
    FakeOAuth,
    Session,
    adult_dob,
    session_from,
    set_cookie,
    signup,
)


async def _oauth(client: AsyncClient, provider: str, token: str) -> Session:
    response = await client.post(f"/api/auth/oauth/{provider}", json={"token": token})
    assert response.status_code == 200, response.text
    return session_from(response)


async def test_new_oauth_user_created_with_generated_handle_and_incomplete_onboarding(
    client: AsyncClient, session: AsyncSession, oauth: FakeOAuth
) -> None:
    token = oauth.google.add("g-sub-1", email="Ravi.K@Gmail.com", name="Ravi Kumar")
    s = await _oauth(client, "google", token)

    user = await session.get(User, s.user_id)
    assert user is not None
    assert re.fullmatch(r"ravikumar\d{4}", user.handle)
    assert user.email == "ravi.k@gmail.com"
    assert user.email_verified_at is not None
    assert user.password_hash is None
    assert user.adult_declared_at is None
    assert await session.get(DoerCode, s.user_id) is not None

    me = (await client.get("/api/users/me", headers=s.headers)).json()
    assert me["onboarding"] == {
        "adult": False,
        "consents": False,
        "phone": False,
        "complete": False,
    }
    events = (
        await session.scalars(
            select(OutboxEvent).where(OutboxEvent.event_type == "identity.user_registered")
        )
    ).all()
    assert [e.payload["method"] for e in events if e.payload["user_id"] == str(s.user_id)] == [
        "google"
    ]


async def test_oauth_handle_fallback_for_short_or_missing_name(
    client: AsyncClient, session: AsyncSession, oauth: FakeOAuth
) -> None:
    for subject, name, pattern in [
        ("s-none", None, r"member\d{4}"),
        ("s-short", "Al", r"almember\d{4}"),
        ("s-nonlatin", "राम", r"member\d{4}"),
    ]:
        s = await _oauth(client, "facebook", oauth.facebook.add(subject, name=name))
        user = await session.get(User, s.user_id)
        assert user is not None
        assert re.fullmatch(pattern, user.handle), user.handle
        assert user.email is None


async def test_same_subject_logs_back_into_same_user(
    client: AsyncClient, session: AsyncSession, oauth: FakeOAuth
) -> None:
    first = await _oauth(client, "google", oauth.google.add("g-same", email="same@example.com"))
    again = await _oauth(client, "google", oauth.google.add("g-same", email="same@example.com"))
    assert again.user_id == first.user_id
    count = await session.scalar(
        select(func.count()).select_from(OAuthAccount).where(OAuthAccount.subject == "g-same")
    )
    assert count == 1


async def test_same_subject_on_other_provider_is_a_different_user(
    client: AsyncClient, oauth: FakeOAuth
) -> None:
    g = await _oauth(client, "google", oauth.google.add("shared-sub"))
    f = await _oauth(client, "facebook", oauth.facebook.add("shared-sub"))
    assert g.user_id != f.user_id


async def _assert_email_exists(
    client: AsyncClient, session: AsyncSession, provider: str, token: str, subject: str
) -> None:
    response = await client.post(f"/api/auth/oauth/{provider}", json={"token": token})
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "email_exists"
    assert set_cookie(response) is None
    assert await session.scalar(select(OAuthAccount).where(OAuthAccount.subject == subject)) is None


async def test_no_auto_link_to_oauth_user_with_same_verified_email(
    client: AsyncClient, session: AsyncSession, oauth: FakeOAuth
) -> None:
    g = await _oauth(client, "google", oauth.google.add("g-link", email="link@example.com"))
    token = oauth.facebook.add("f-link", email="LINK@example.com")
    await _assert_email_exists(client, session, "facebook", token, "f-link")
    providers = set(
        await session.scalars(
            select(OAuthAccount.provider).where(OAuthAccount.user_id == g.user_id)
        )
    )
    assert providers == {"google"}


async def test_no_auto_link_to_password_user_even_with_verified_email(
    client: AsyncClient, session: AsyncSession, oauth: FakeOAuth
) -> None:
    pw = await signup(client, email="verified.pw@example.com")
    await session.execute(
        update(User).where(User.id == pw.user_id).values(email_verified_at=datetime.now(UTC))
    )
    await session.commit()
    token = oauth.google.add("g-pw", email="verified.pw@example.com")
    await _assert_email_exists(client, session, "google", token, "g-pw")


async def test_no_auto_link_to_password_account_with_unverified_email(
    client: AsyncClient, session: AsyncSession, oauth: FakeOAuth
) -> None:
    # Attacker pre-registers the victim's email with a password...
    squatter = await signup(client, email="victim@gmail.com")
    # ...then the real owner signs in with Google: no takeover, no link.
    token = oauth.google.add("g-victim", email="Victim@Gmail.com")
    await _assert_email_exists(client, session, "google", token, "g-victim")
    linked = await session.scalar(
        select(OAuthAccount).where(OAuthAccount.user_id == squatter.user_id)
    )
    assert linked is None


async def test_unverified_provider_email_never_links_or_claims_email(
    client: AsyncClient, session: AsyncSession, oauth: FakeOAuth
) -> None:
    owner = await _oauth(client, "google", oauth.google.add("g-owner", email="own@example.com"))
    token = oauth.facebook.add("f-imposter", email="own@example.com", email_verified=False)
    imposter = await _oauth(client, "facebook", token)
    assert imposter.user_id != owner.user_id
    user = await session.get(User, imposter.user_id)
    assert user is not None and user.email is None and user.email_verified_at is None


async def test_unknown_provider_400(client: AsyncClient) -> None:
    response = await client.post("/api/auth/oauth/twitter", json={"token": "x" * 20})
    assert response.status_code == 400
    assert response.json()["code"] == "unknown_provider"


async def test_verifier_failure_401(client: AsyncClient) -> None:
    response = await client.post("/api/auth/oauth/google", json={"token": "forged-token-xyz"})
    assert response.status_code == 401
    assert response.json()["code"] == "oauth_failed"


async def test_suspended_oauth_user_gets_403(
    client: AsyncClient, session: AsyncSession, oauth: FakeOAuth
) -> None:
    s = await _oauth(client, "google", oauth.google.add("g-susp"))
    await session.execute(
        update(User).where(User.id == s.user_id).values(status=UserStatus.SUSPENDED)
    )
    await session.commit()
    response = await client.post(
        "/api/auth/oauth/google", json={"token": oauth.google.add("g-susp")}
    )
    assert response.status_code == 403


async def test_age_consent_endpoint_completes_oauth_onboarding_steps(
    client: AsyncClient, oauth: FakeOAuth
) -> None:
    s = await _oauth(client, "google", oauth.google.add("g-age"))
    response = await client.post(
        "/api/users/me/age-consent",
        json={"date_of_birth": adult_dob(), "accept_terms": True, "accept_privacy": True},
        headers=s.headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["onboarding"] == {
        "adult": True,
        "consents": True,
        "phone": False,
        "complete": False,
    }
