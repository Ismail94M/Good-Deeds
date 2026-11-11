"""Constraints on the identity tables hold even if service code is bypassed."""

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.identity.models import Consent, DoerCode, OAuthAccount, User


def _user(**overrides: Any) -> User:
    values: dict[str, Any] = {
        "email": f"db.{uuid.uuid4().hex[:8]}@example.com",
        "display_name": "DB Test",
        "handle": f"db{uuid.uuid4().hex[:10]}",
    }
    values.update(overrides)
    return User(**values)


async def _rejects(session: AsyncSession, *objects: Any) -> None:
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            session.add_all(objects)
            await session.flush()


async def test_uppercase_email_rejected(session: AsyncSession) -> None:
    await _rejects(session, _user(email="Upper@Example.com"))


async def test_uppercase_handle_rejected(session: AsyncSession) -> None:
    await _rejects(session, _user(handle="UpperHandle"))


async def test_phone_without_verified_at_rejected(session: AsyncSession) -> None:
    await _rejects(session, _user(phone_e164="+919876543260"))


async def test_verified_at_without_phone_rejected(session: AsyncSession) -> None:
    await _rejects(session, _user(phone_verified_at=datetime.now(UTC)))


async def test_phone_unique_across_users(session: AsyncSession) -> None:
    now = datetime.now(UTC)
    session.add(_user(phone_e164="+919876543261", phone_verified_at=now))
    await session.flush()
    await _rejects(session, _user(phone_e164="+919876543261", phone_verified_at=now))


async def test_email_and_handle_unique(session: AsyncSession) -> None:
    session.add(_user(email="uniq@example.com", handle="uniqhandle"))
    await session.flush()
    await _rejects(session, _user(email="uniq@example.com"))
    await _rejects(session, _user(handle="uniqhandle"))


async def test_valid_user_and_null_email_accepted(session: AsyncSession) -> None:
    session.add(_user(email=None, phone_e164="+919876543262", phone_verified_at=datetime.now(UTC)))
    await session.flush()


async def test_short_id_and_oauth_subject_unique(session: AsyncSession) -> None:
    a, b = _user(), _user()
    session.add_all([a, b])
    await session.flush()
    session.add(DoerCode(user_id=a.id, short_id="ABCDEFG", key_version=1))
    session.add(OAuthAccount(user_id=a.id, provider="google", subject="dup-sub"))
    await session.flush()
    await _rejects(session, DoerCode(user_id=b.id, short_id="ABCDEFG", key_version=1))
    await _rejects(session, OAuthAccount(user_id=b.id, provider="google", subject="dup-sub"))
    await _rejects(
        session,
        Consent(user_id=a.id, consent_type="terms", version="v1"),
        Consent(user_id=a.id, consent_type="terms", version="v1"),
    )


async def test_invalid_role_or_status_rejected(session: AsyncSession) -> None:

    user = _user()
    session.add(user)
    await session.flush()
    for column in ("role", "status"):
        with pytest.raises(IntegrityError):
            async with session.begin_nested():
                await session.execute(
                    text(f"UPDATE users SET {column} = 'superuser' WHERE id = :id"),
                    {"id": user.id},
                )
