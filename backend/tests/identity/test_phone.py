import re
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.events import OutboxEvent
from app.core.security import keyed_hash
from app.core.sms import ConsoleSmsProvider
from app.modules.identity import service
from app.modules.identity.models import PhoneHistory, PhoneOtp, User
from tests.identity.helpers import (
    Session,
    onboard_phone,
    otp_from,
    signup,
    start_phone,
    valid_phone,
    verify_phone,
)


async def _backdate_otps(session: AsyncSession, minutes: int, **where: object) -> None:
    """Move matching OTP requests into the past (instead of sleeping)."""
    query = update(PhoneOtp).values(created_at=PhoneOtp.created_at - timedelta(minutes=minutes))
    for column, value in where.items():
        query = query.where(getattr(PhoneOtp, column) == value)
    await session.execute(query)
    await session.commit()


async def _latest_otp(session: AsyncSession, s: Session) -> PhoneOtp:
    session.expire_all()
    otp = await session.scalar(
        select(PhoneOtp).where(PhoneOtp.user_id == s.user_id).order_by(PhoneOtp.created_at.desc())
    )
    assert otp is not None
    return otp


def _wrong(code: str) -> str:
    return f"{(int(code) + 1) % 1_000_000:06d}"


@pytest.mark.parametrize("phone", ["12345", "+91 98765", "abcdefgh", "12345678", "+1 555 0100"])
async def test_invalid_number_rejected(client: AsyncClient, phone: str) -> None:
    s = await signup(client)
    response = await start_phone(client, s, phone)
    assert response.status_code in (400, 422), response.text


@pytest.mark.parametrize(
    "phone",
    [
        "+91 80 2222 3333",  # Bangalore landline
        "+91 12345 67890",  # fixed line
        "+91 1800 425 1234",  # toll free
        "+91 1860 500 1234",  # premium / shared cost
        "+91 140 123 4567",  # other non-mobile type
        "+1 650 253 0000",  # US
        "+44 7911 123456",  # foreign mobile
    ],
)
async def test_only_indian_mobile_numbers_accepted(
    client: AsyncClient, sms: ConsoleSmsProvider, phone: str
) -> None:
    s = await signup(client)
    response = await start_phone(client, s, phone)
    assert response.status_code == 400, response.text
    assert response.json()["code"] == "invalid_phone"
    assert sms.sent == {}
    verify = await verify_phone(client, s, phone, "123456")
    assert verify.status_code == 400
    assert verify.json()["code"] == "invalid_phone"


async def test_start_and_verify_happy_path(
    client: AsyncClient, session: AsyncSession, sms: ConsoleSmsProvider
) -> None:
    s = await signup(client)
    started = await start_phone(client, s, "98765 43210")  # national format, default region IN
    assert started.status_code == 200, started.text
    body = started.json()
    assert body["phone_e164"] == "+919876543210"
    assert body["resend_after_seconds"] == get_settings().otp_resend_seconds

    otp = await _latest_otp(session, s)
    code = otp_from(sms, "+919876543210")
    assert code not in otp.code_hash  # stored only as a keyed hash

    verified = await verify_phone(client, s, "+91 98765 43210", code)
    assert verified.status_code == 200, verified.text
    assert verified.json()["phone_e164"] == "+919876543210"
    assert verified.json()["onboarding"]["phone"] is True

    user = await session.get(User, s.user_id)
    assert user is not None and user.phone_verified_at is not None
    events = (
        await session.scalars(
            select(OutboxEvent).where(OutboxEvent.event_type == "identity.phone_verified")
        )
    ).all()
    assert [e for e in events if e.payload["user_id"] == str(s.user_id)]

    # Once verified, nothing more can be confirmed on this account.
    again = await verify_phone(client, s, "+919876543210", code)
    assert again.status_code == 409
    assert again.json()["code"] == "phone_already_verified"


async def test_wrong_code_attempt_count_persists(
    client: AsyncClient, session: AsyncSession, sms: ConsoleSmsProvider
) -> None:
    s = await signup(client)
    phone = valid_phone(10)
    await start_phone(client, s, phone)
    code = otp_from(sms, phone)

    response = await verify_phone(client, s, phone, _wrong(code))
    assert response.status_code == 400
    assert response.json()["code"] == "otp_incorrect"
    # The client fixture rolls back uncommitted work on failure, like production.
    assert (await _latest_otp(session, s)).attempts == 1
    user = await session.get(User, s.user_id)
    assert user is not None and user.phone_e164 is None


async def test_lockout_after_max_attempts(
    client: AsyncClient, session: AsyncSession, sms: ConsoleSmsProvider
) -> None:
    s = await signup(client)
    phone = valid_phone(11)
    await start_phone(client, s, phone)
    code = otp_from(sms, phone)
    max_attempts = get_settings().otp_max_attempts

    for _ in range(max_attempts):
        response = await verify_phone(client, s, phone, _wrong(code))
        assert response.status_code == 400
        assert response.json()["code"] == "otp_incorrect"

    # Even the right code is refused once the code is locked.
    locked = await verify_phone(client, s, phone, code)
    assert locked.status_code == 429
    assert locked.json()["code"] == "otp_locked"
    assert (await _latest_otp(session, s)).attempts == max_attempts

    # A fresh code works again.
    await _backdate_otps(session, 1, user_id=s.user_id)
    assert (await start_phone(client, s, phone)).status_code == 200
    assert (await verify_phone(client, s, phone, otp_from(sms, phone))).status_code == 200


async def test_right_code_on_last_allowed_attempt_succeeds(
    client: AsyncClient, sms: ConsoleSmsProvider
) -> None:
    s = await signup(client)
    phone = valid_phone(12)
    await start_phone(client, s, phone)
    code = otp_from(sms, phone)
    for _ in range(get_settings().otp_max_attempts - 1):
        await verify_phone(client, s, phone, _wrong(code))
    assert (await verify_phone(client, s, phone, code)).status_code == 200


async def test_expired_code_rejected(
    client: AsyncClient, session: AsyncSession, sms: ConsoleSmsProvider
) -> None:
    s = await signup(client)
    phone = valid_phone(13)
    await start_phone(client, s, phone)
    code = otp_from(sms, phone)
    await session.execute(
        update(PhoneOtp)
        .where(PhoneOtp.user_id == s.user_id)
        .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    await session.commit()
    response = await verify_phone(client, s, phone, code)
    assert response.status_code == 400
    assert response.json()["code"] == "otp_expired"


async def test_code_for_other_number_rejected(client: AsyncClient, sms: ConsoleSmsProvider) -> None:
    s = await signup(client)
    await start_phone(client, s, valid_phone(14))
    code = otp_from(sms, valid_phone(14))
    response = await verify_phone(client, s, valid_phone(15), code)
    assert response.status_code == 400
    assert response.json()["code"] == "otp_expired"


async def test_other_users_code_cannot_be_used(
    client: AsyncClient, sms: ConsoleSmsProvider
) -> None:
    owner = await signup(client)
    thief = await signup(client)
    phone = valid_phone(16)
    await start_phone(client, owner, phone)
    response = await verify_phone(client, thief, phone, otp_from(sms, phone))
    assert response.status_code == 400


async def test_resend_too_soon_429(client: AsyncClient, session: AsyncSession) -> None:
    s = await signup(client)
    assert (await start_phone(client, s, valid_phone(17))).status_code == 200
    response = await start_phone(client, s, valid_phone(17))
    assert response.status_code == 429
    assert response.json()["code"] == "otp_resend_too_soon"
    # A different number doesn't bypass the wait.
    assert (await start_phone(client, s, valid_phone(18))).status_code == 429
    # Once the resend interval has passed it is allowed again.
    await _backdate_otps(session, 1, user_id=s.user_id)
    assert (await start_phone(client, s, valid_phone(17))).status_code == 200


async def test_hourly_limit_per_user(client: AsyncClient, session: AsyncSession) -> None:
    s = await signup(client)
    limit = get_settings().otp_max_per_hour
    for i in range(limit):
        response = await start_phone(client, s, valid_phone(20 + i))
        assert response.status_code == 200, (i, response.text)
        await _backdate_otps(session, 1, user_id=s.user_id)

    blocked = await start_phone(client, s, valid_phone(30))
    assert blocked.status_code == 429
    assert blocked.json()["code"] == "otp_rate_limited"

    # Requests older than an hour no longer count.
    await _backdate_otps(session, 60, user_id=s.user_id)
    assert (await start_phone(client, s, valid_phone(30))).status_code == 200


async def test_hourly_limit_per_phone_across_accounts(
    client: AsyncClient, session: AsyncSession
) -> None:
    phone = valid_phone(40)
    for _ in range(get_settings().otp_max_per_hour):
        s = await signup(client)
        assert (await start_phone(client, s, phone)).status_code == 200

    another = await signup(client)
    blocked = await start_phone(client, another, phone)
    assert blocked.status_code == 429
    assert blocked.json()["code"] == "otp_rate_limited"

    await _backdate_otps(session, 61, phone_e164=phone)
    assert (await start_phone(client, another, phone)).status_code == 200


async def test_number_on_another_account_looks_like_success_but_sends_no_code(
    client: AsyncClient, session: AsyncSession, sms: ConsoleSmsProvider
) -> None:
    first = await signup(client)
    phone = valid_phone(41)
    await onboard_phone(client, sms, first, phone)
    sms.sent.clear()

    second = await signup(client)
    fresh = await signup(client)
    taken = await start_phone(client, second, "098765 43241")  # same number, other format
    free = await start_phone(client, fresh, valid_phone(47))
    assert taken.status_code == free.status_code == 200
    assert set(taken.json()) == set(free.json())
    assert taken.json()["phone_e164"] == phone
    assert taken.json()["resend_after_seconds"] == free.json()["resend_after_seconds"]

    # The number's owner gets an informational SMS, with no code in it.
    message = sms.sent[phone]
    assert "already linked" in message and "no code was sent" in message
    assert re.search(r"\d{6}", message) is None

    # An OTP row is still written, so the rate limits apply to this number too.
    otp = await _latest_otp(session, second)
    assert otp.phone_e164 == phone

    for guess in ("000000", "123456", "999999"):
        response = await verify_phone(client, second, phone, guess)
        assert response.status_code == 400
        assert response.json()["code"] == "otp_incorrect"
    user = await session.get(User, second.user_id)
    assert user is not None
    await session.refresh(user)
    assert user.phone_e164 is None


async def test_taken_number_still_counts_toward_per_phone_limit(
    client: AsyncClient, sms: ConsoleSmsProvider
) -> None:
    owner = await signup(client)
    phone = valid_phone(48)
    await onboard_phone(client, sms, owner, phone)
    # The owner's own verification used one of the hourly slots for this number.
    for _ in range(get_settings().otp_max_per_hour - 1):
        s = await signup(client)
        assert (await start_phone(client, s, phone)).status_code == 200
    blocked = await start_phone(client, await signup(client), phone)
    assert blocked.status_code == 429
    assert blocked.json()["code"] == "otp_rate_limited"


async def test_number_on_another_account_409_at_verify(
    client: AsyncClient, session: AsyncSession, sms: ConsoleSmsProvider
) -> None:
    first = await signup(client)
    second = await signup(client)
    phone = valid_phone(42)
    await start_phone(client, second, phone)
    second_code = otp_from(sms, phone)
    await onboard_phone(client, sms, first, phone)

    response = await verify_phone(client, second, phone, second_code)
    assert response.status_code == 409
    assert response.json()["code"] == "phone_taken"
    user = await session.get(User, second.user_id)
    assert user is not None
    await session.refresh(user)
    assert user.phone_e164 is None


async def test_verified_phone_cannot_be_changed(
    client: AsyncClient, session: AsyncSession, sms: ConsoleSmsProvider
) -> None:
    s = await signup(client)
    await onboard_phone(client, sms, s, valid_phone(43))
    await _backdate_otps(session, 1, user_id=s.user_id)
    sms.sent.clear()

    for phone in (valid_phone(44), valid_phone(43)):
        started = await start_phone(client, s, phone)
        assert started.status_code == 409
        assert started.json()["code"] == "phone_already_verified"
        verified = await verify_phone(client, s, phone, "123456")
        assert verified.status_code == 409
        assert verified.json()["code"] == "phone_already_verified"
    assert sms.sent == {}
    me = (await client.get("/api/users/me", headers=s.headers)).json()
    assert me["phone_e164"] == valid_phone(43)


# --------------------------------------------------------------------------- phone_history


async def test_verify_writes_phone_history_hash_only(
    client: AsyncClient, session: AsyncSession, sms: ConsoleSmsProvider
) -> None:
    s = await signup(client)
    phone = valid_phone(60)
    await onboard_phone(client, sms, s, phone)
    rows = (
        await session.scalars(select(PhoneHistory).where(PhoneHistory.user_id == s.user_id))
    ).all()
    assert [r.phone_hash for r in rows] == [keyed_hash("phone", phone)]
    assert rows[0].phone_hash == service.phone_hash(phone)
    assert phone not in rows[0].phone_hash and phone[3:] not in rows[0].phone_hash


async def test_phone_history_is_append_only(
    client: AsyncClient, session: AsyncSession, sms: ConsoleSmsProvider
) -> None:
    s = await signup(client)
    await onboard_phone(client, sms, s, valid_phone(61))
    for statement in (
        "UPDATE phone_history SET user_id = gen_random_uuid() WHERE user_id = :id",
        "DELETE FROM phone_history WHERE user_id = :id",
    ):
        with pytest.raises(DBAPIError, match="append-only"):
            async with session.begin_nested():
                await session.execute(text(statement), {"id": s.user_id})
    assert await session.scalar(
        select(PhoneHistory.user_id).where(PhoneHistory.user_id == s.user_id)
    )


async def test_number_in_history_stays_owned_after_user_row_releases_it(
    client: AsyncClient, session: AsyncSession, sms: ConsoleSmsProvider
) -> None:
    """Even if the number is cleared from the owner's row, history keeps it theirs."""
    first = await signup(client)
    phone = valid_phone(62)
    await onboard_phone(client, sms, first, phone)
    await session.execute(
        update(User).where(User.id == first.user_id).values(phone_e164=None, phone_verified_at=None)
    )
    await session.commit()
    sms.sent.clear()

    second = await signup(client)
    started = await start_phone(client, second, phone)
    assert started.status_code == 200
    assert "no code was sent" in sms.sent[phone]
    response = await verify_phone(client, second, phone, "123456")
    assert response.status_code == 400
    assert response.json()["code"] == "otp_incorrect"

    # The original owner can verify the same number again (no duplicate history row).
    await _backdate_otps(session, 1, user_id=first.user_id)
    session.expire_all()
    await onboard_phone(client, sms, first, phone)
    count = await session.scalar(
        select(func.count())
        .select_from(PhoneHistory)
        .where(PhoneHistory.phone_hash == service.phone_hash(phone))
    )
    assert count == 1


async def test_phone_endpoints_require_login(client: AsyncClient) -> None:
    phone = valid_phone(45)
    assert (
        await client.post("/api/users/me/phone/start", json={"phone": phone})
    ).status_code == 401
    response = await client.post(
        "/api/users/me/phone/verify", json={"phone": phone, "code": "123456"}
    )
    assert response.status_code == 401


@pytest.mark.parametrize("code", ["12345", "1234567", "abcdef", ""])
async def test_malformed_code_422(client: AsyncClient, code: str) -> None:
    s = await signup(client)
    assert (await verify_phone(client, s, valid_phone(46), code)).status_code == 422
