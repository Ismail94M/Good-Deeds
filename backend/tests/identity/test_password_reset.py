"""Forgot password through the account's verified phone (PO decision 2026-11-17, M2).

POST /api/auth/password/forgot never reveals whether an account exists; POST
/api/auth/password/reset sets the password, ends every session and audits it.
"""

import json
import re
import uuid
from datetime import UTC, datetime, timedelta

from httpx import AsyncClient, Response
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditLog
from app.core.ratelimit import InMemoryRateLimiter
from app.core.security import keyed_hash
from app.core.sms import ConsoleSmsProvider
from app.modules.identity.models import OtpPurpose, PhoneOtp, User, UserStatus
from app.modules.identity.service import phone_hash
from tests.identity.helpers import (
    PASSWORD,
    FakeOAuth,
    Session,
    bearer,
    login,
    onboard_phone,
    onboarded_user,
    publish_limits,
    refresh,
    session_from,
    signup,
    signup_payload,
    valid_phone,
    verify_phone,
)

NEW_PASSWORD = "a-brand-new-password"
FORGOT = "/api/auth/password/forgot"
RESET = "/api/auth/password/reset"
# The forgot answer for every valid-format number (resend_after_seconds = otp_resend_seconds).
SENT = {"sent": True, "resend_after_seconds": 30}
RESET_SMS = re.compile(
    r"^Your Good Deeds password reset code is (\d{6})\. It expires in (\d+) minutes\. "
    r"Never share this code\.$"
)


async def forgot(client: AsyncClient, phone: str) -> Response:
    client.cookies.clear()
    return await client.post(FORGOT, json={"phone": phone})


async def reset(
    client: AsyncClient, phone: str, code: str, new_password: str = NEW_PASSWORD
) -> Response:
    client.cookies.clear()
    return await client.post(
        RESET, json={"phone": phone, "code": code, "new_password": new_password}
    )


def reset_code(sms: ConsoleSmsProvider, phone: str) -> str:
    match = RESET_SMS.match(sms.sent[phone])
    assert match is not None, sms.sent[phone]
    return match.group(1)


def other_code(code: str) -> str:
    return f"{(int(code) + 1) % 1_000_000:06d}"


async def eligible(client: AsyncClient, sms: ConsoleSmsProvider, index: int) -> Session:
    s = await onboarded_user(client, sms, index)
    sms.sent.clear()
    return s


def assert_invalid_code(response: Response) -> None:
    assert response.status_code == 400, response.text
    assert response.json() == {
        "detail": "That code is not correct or has expired. Please request a new one.",
        "code": "otp_incorrect",
    }


# --------------------------------------------------- happy path


async def test_happy_path(
    client: AsyncClient, session: AsyncSession, sms: ConsoleSmsProvider
) -> None:
    phone = valid_phone(40)
    s = await eligible(client, sms, 40)

    sent = await forgot(client, phone)
    assert sent.status_code == 200
    assert sent.json() == SENT
    match = RESET_SMS.match(sms.sent[phone])
    assert match is not None
    assert match.group(2) == "10"  # otp_ttl_minutes from config
    code = match.group(1)

    done = await reset(client, phone, code)
    assert done.status_code == 200, done.text
    assert done.json() == {"reset": True}
    assert "set-cookie" not in done.headers  # does not sign the user in

    assert s.email is not None
    assert (await login(client, s.email, PASSWORD)).status_code == 401
    assert (await login(client, s.email, NEW_PASSWORD)).status_code == 200

    otp = await session.scalar(
        select(PhoneOtp).where(
            PhoneOtp.user_id == s.user_id, PhoneOtp.purpose == OtpPurpose.PASSWORD_RESET
        )
    )
    assert otp is not None and otp.consumed_at is not None


async def test_phone_accepted_in_same_formats_as_phone_start(
    client: AsyncClient, sms: ConsoleSmsProvider
) -> None:
    phone = valid_phone(41)
    await eligible(client, sms, 41)
    local = phone.removeprefix("+91")
    assert (await forgot(client, f"0{local[:5]} {local[5:]}")).json() == SENT
    code = reset_code(sms, phone)
    assert (await reset(client, local, code)).status_code == 200


async def test_reset_ends_all_sessions(
    client: AsyncClient, session: AsyncSession, sms: ConsoleSmsProvider
) -> None:
    phone = valid_phone(42)
    s = await eligible(client, sms, 42)
    assert s.email is not None
    second = session_from(await login(client, s.email))
    version_before = await session.get(User, s.user_id)
    assert version_before is not None
    old_version = version_before.token_version

    await forgot(client, phone)
    assert (await reset(client, phone, reset_code(sms, phone))).status_code == 200

    for old in (s, second):
        me = await client.get("/api/users/me", headers=bearer(old.access))
        assert me.status_code == 401
        assert (await refresh(client, old.refresh)).status_code == 401
    await session.refresh(version_before)
    assert version_before.token_version > old_version


async def test_no_phone_or_code_in_responses_or_audit(
    client: AsyncClient, session: AsyncSession, sms: ConsoleSmsProvider
) -> None:
    phone = valid_phone(43)
    s = await eligible(client, sms, 43)
    sent = await forgot(client, phone)
    code = reset_code(sms, phone)
    wrong = await reset(client, phone, other_code(code))
    done = await reset(client, phone, code)
    digits = phone.removeprefix("+91")
    for response in (sent, wrong, done):
        assert digits not in response.text
        assert code not in response.text

    entries = list(
        await session.scalars(select(AuditLog).where(AuditLog.entity_id == str(s.user_id)))
    )
    resets = [e for e in entries if e.action == "user.password_reset"]
    assert len(resets) == 1
    assert resets[0].actor_id == s.user_id
    for entry in entries:
        dumped = json.dumps([entry.old_value, entry.new_value, entry.reason, entry.entity_id])
        assert digits not in dumped
        assert code not in dumped


# --------------------------------------------------- forgot never reveals accounts


async def test_unknown_number_same_response_no_sms(
    client: AsyncClient, sms: ConsoleSmsProvider
) -> None:
    phone = valid_phone(44)
    response = await forgot(client, phone)
    assert response.status_code == 200
    assert response.json() == SENT
    assert phone not in sms.sent


async def test_google_only_account_same_response_no_sms(
    client: AsyncClient, session: AsyncSession, sms: ConsoleSmsProvider, oauth: FakeOAuth
) -> None:
    token = oauth.google.add("google-sub-reset", email="g.reset@example.com")
    signed_in = await client.post("/api/auth/oauth/google", json={"token": token})
    assert signed_in.status_code == 200, signed_in.text
    s = session_from(signed_in)
    phone = valid_phone(45)
    await onboard_phone(client, sms, s, phone)
    user = await session.get(User, s.user_id)
    assert user is not None and user.password_hash is None and user.phone_e164 == phone
    sms.sent.clear()

    response = await forgot(client, phone)
    assert response.status_code == 200
    assert response.json() == SENT
    assert phone not in sms.sent
    assert_invalid_code(await reset(client, phone, "123456"))


async def test_suspended_and_blocked_accounts_same_response_no_sms(
    client: AsyncClient, session: AsyncSession, sms: ConsoleSmsProvider
) -> None:
    for index, status in ((46, UserStatus.SUSPENDED), (47, UserStatus.BLOCKED)):
        phone = valid_phone(index)
        s = await eligible(client, sms, index)
        await session.execute(update(User).where(User.id == s.user_id).values(status=status))
        await session.commit()

        response = await forgot(client, phone)
        assert response.status_code == 200
        assert response.json() == SENT
        assert phone not in sms.sent


async def test_suspended_after_code_sent_cannot_reset(
    client: AsyncClient, session: AsyncSession, sms: ConsoleSmsProvider
) -> None:
    phone = valid_phone(48)
    s = await eligible(client, sms, 48)
    await forgot(client, phone)
    code = reset_code(sms, phone)
    await session.execute(
        update(User).where(User.id == s.user_id).values(status=UserStatus.SUSPENDED)
    )
    await session.commit()
    assert_invalid_code(await reset(client, phone, code))


async def test_unverified_number_gets_no_code(client: AsyncClient, sms: ConsoleSmsProvider) -> None:
    """A number with a pending (unverified) phone-verify code is not a verified phone."""
    s = await signup(client)
    phone = valid_phone(49)
    await client.post("/api/users/me/phone/start", json={"phone": phone}, headers=s.headers)
    sms.sent.clear()
    assert (await forgot(client, phone)).json() == SENT
    assert phone not in sms.sent


async def test_invalid_phone_format_matches_phone_start(
    client: AsyncClient, sms: ConsoleSmsProvider
) -> None:
    s = await signup(client)
    for bad in ("12345678", "+14155550123"):
        start = await client.post(
            "/api/users/me/phone/start", json={"phone": bad}, headers=s.headers
        )
        response = await forgot(client, bad)
        assert response.status_code == start.status_code == 400
        assert response.json() == start.json()
        assert response.json()["code"] == "invalid_phone"
        reset_response = await reset(client, bad, "123456")
        assert reset_response.json() == start.json()
    too_short = await forgot(client, "123")
    start_short = await client.post(
        "/api/users/me/phone/start", json={"phone": "123"}, headers=s.headers
    )
    assert too_short.status_code == start_short.status_code == 422


# --------------------------------------------------- codes


async def test_wrong_code_rejected(client: AsyncClient, sms: ConsoleSmsProvider) -> None:
    phone = valid_phone(50)
    s = await eligible(client, sms, 50)
    await forgot(client, phone)
    code = reset_code(sms, phone)
    assert_invalid_code(await reset(client, phone, other_code(code)))
    assert s.email is not None
    assert (await login(client, s.email, PASSWORD)).status_code == 200
    # The real code still works after one wrong attempt.
    assert (await reset(client, phone, code)).status_code == 200


async def test_unknown_number_gives_same_error(client: AsyncClient) -> None:
    assert_invalid_code(await reset(client, valid_phone(51), "123456"))


async def test_expired_code_rejected(
    client: AsyncClient, session: AsyncSession, sms: ConsoleSmsProvider
) -> None:
    phone = valid_phone(52)
    s = await eligible(client, sms, 52)
    await forgot(client, phone)
    code = reset_code(sms, phone)
    await session.execute(
        update(PhoneOtp)
        .where(PhoneOtp.user_id == s.user_id, PhoneOtp.purpose == OtpPurpose.PASSWORD_RESET)
        .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    await session.commit()
    assert_invalid_code(await reset(client, phone, code))


async def test_used_code_rejected(client: AsyncClient, sms: ConsoleSmsProvider) -> None:
    phone = valid_phone(53)
    s = await eligible(client, sms, 53)
    await forgot(client, phone)
    code = reset_code(sms, phone)
    assert (await reset(client, phone, code)).status_code == 200
    assert_invalid_code(await reset(client, phone, code, "yet-another-password"))
    assert s.email is not None
    assert (await login(client, s.email, NEW_PASSWORD)).status_code == 200


async def test_newer_code_retires_older_one(
    client: AsyncClient, session: AsyncSession, sms: ConsoleSmsProvider
) -> None:
    phone = valid_phone(54)
    await eligible(client, sms, 54)
    await publish_limits(session, otp_resend_seconds=0)
    await forgot(client, phone)
    first = reset_code(sms, phone)
    await forgot(client, phone)
    second = reset_code(sms, phone)
    if first != second:  # 1 in a million they collide
        assert_invalid_code(await reset(client, phone, first))
    assert (await reset(client, phone, second)).status_code == 200


async def test_phone_verify_code_rejected_for_reset(
    client: AsyncClient, session: AsyncSession, sms: ConsoleSmsProvider
) -> None:
    phone = valid_phone(55)
    s = await eligible(client, sms, 55)
    # A live phone-verify code for this account and number, with a known value.
    otp_id = uuid.uuid4()
    code = "424242"
    session.add(
        PhoneOtp(
            id=otp_id,
            user_id=s.user_id,
            purpose=OtpPurpose.PHONE_VERIFY,
            phone_e164=phone,
            code_hash=keyed_hash("otp", str(otp_id), code),
            expires_at=datetime.now(UTC) + timedelta(minutes=10),
        )
    )
    await session.commit()
    assert_invalid_code(await reset(client, phone, code))


async def test_reset_code_rejected_for_phone_verify(
    client: AsyncClient, session: AsyncSession, sms: ConsoleSmsProvider
) -> None:
    s = await signup(client)
    phone = valid_phone(56)
    otp_id = uuid.uuid4()
    code = "515151"
    session.add(
        PhoneOtp(
            id=otp_id,
            user_id=s.user_id,
            purpose=OtpPurpose.PASSWORD_RESET,
            phone_e164=phone,
            code_hash=keyed_hash("otp_password_reset", str(otp_id), code),
            expires_at=datetime.now(UTC) + timedelta(minutes=10),
        )
    )
    await session.commit()
    response = await verify_phone(client, s, phone, code)
    assert response.status_code == 400
    assert response.json()["code"] == "otp_expired"
    user = await session.get(User, s.user_id)
    assert user is not None and user.phone_verified_at is None


async def test_attempt_limit_kills_the_code(
    client: AsyncClient, session: AsyncSession, sms: ConsoleSmsProvider
) -> None:
    await publish_limits(session, otp_max_attempts=2)
    phone = valid_phone(57)
    await eligible(client, sms, 57)
    await forgot(client, phone)
    code = reset_code(sms, phone)
    for _ in range(2):
        assert_invalid_code(await reset(client, phone, other_code(code)))
    # Even the right code is now refused, with the same error.
    assert_invalid_code(await reset(client, phone, code))


async def test_password_policy_matches_signup(client: AsyncClient, sms: ConsoleSmsProvider) -> None:
    phone = valid_phone(58)
    await eligible(client, sms, 58)
    await forgot(client, phone)
    code = reset_code(sms, phone)
    short = await reset(client, phone, code, "123456789")
    signup_short = await client.post("/api/auth/signup", json=signup_payload(password="123456789"))
    assert short.status_code == signup_short.status_code == 422
    assert short.json()["detail"][0]["type"] == signup_short.json()["detail"][0]["type"]
    assert short.json()["detail"][0]["msg"] == signup_short.json()["detail"][0]["msg"]
    # A policy failure does not use up the code.
    assert (await reset(client, phone, code)).status_code == 200


# --------------------------------------------------- rate limits


async def test_forgot_rate_limited_per_phone_by_hash(
    client: AsyncClient,
    session: AsyncSession,
    sms: ConsoleSmsProvider,
    rate_limiter: InMemoryRateLimiter,
) -> None:
    await publish_limits(session, rate_password_forgot_phone="2/3600")
    phone = valid_phone(59)  # no account: limits apply all the same
    for _ in range(2):
        assert (await forgot(client, phone)).status_code == 200
    blocked = await forgot(client, phone)
    assert blocked.status_code == 429
    assert blocked.json()["code"] == "rate_limited"
    # Another number is unaffected.
    assert (await forgot(client, valid_phone(60))).status_code == 200

    keys = [key for key, _ in rate_limiter.counts]
    assert f"pw_forgot:phone:{phone_hash(phone)}" in keys
    digits = phone.removeprefix("+91")
    assert not [key for key in keys if digits in key]


async def test_forgot_rate_limited_per_ip(client: AsyncClient, session: AsyncSession) -> None:
    await publish_limits(session, rate_password_forgot_ip="2/3600")
    assert (await forgot(client, valid_phone(61))).status_code == 200
    assert (await forgot(client, valid_phone(62))).status_code == 200
    blocked = await forgot(client, valid_phone(63))
    assert blocked.status_code == 429
    assert blocked.json()["code"] == "rate_limited"


async def test_reset_rate_limited_per_phone_by_hash(
    client: AsyncClient, session: AsyncSession, rate_limiter: InMemoryRateLimiter
) -> None:
    await publish_limits(session, rate_password_reset_phone="2/3600")
    phone = valid_phone(64)
    for _ in range(2):
        assert_invalid_code(await reset(client, phone, "123456"))
    blocked = await reset(client, phone, "123456")
    assert blocked.status_code == 429
    assert blocked.json()["code"] == "rate_limited"
    assert_invalid_code(await reset(client, valid_phone(65), "123456"))
    keys = [key for key, _ in rate_limiter.counts]
    assert f"pw_reset:phone:{phone_hash(phone)}" in keys
    assert not [key for key in keys if phone.removeprefix("+91") in key]


async def test_reset_rate_limited_per_ip(client: AsyncClient, session: AsyncSession) -> None:
    await publish_limits(session, rate_password_reset_ip="2/3600")
    assert_invalid_code(await reset(client, valid_phone(66), "123456"))
    assert_invalid_code(await reset(client, valid_phone(67), "123456"))
    blocked = await reset(client, valid_phone(68), "123456")
    assert blocked.status_code == 429
    assert blocked.json()["code"] == "rate_limited"


async def test_resend_cooldown_is_silent(
    client: AsyncClient, session: AsyncSession, sms: ConsoleSmsProvider
) -> None:
    """Inside otp_resend_seconds no new code is sent, but the answer is still 200."""
    phone = valid_phone(69)
    await eligible(client, sms, 69)
    await forgot(client, phone)
    first = reset_code(sms, phone)
    sms.sent.clear()
    again = await forgot(client, phone)
    assert again.status_code == 200 and again.json() == SENT
    assert phone not in sms.sent
    assert (await reset(client, phone, first)).status_code == 200


async def test_forgot_response_identical_for_eligible_and_not(
    client: AsyncClient, session: AsyncSession, sms: ConsoleSmsProvider
) -> None:
    """resend_after_seconds comes from config and is the same whether or not a code went out."""
    await publish_limits(session, otp_resend_seconds=45)
    await eligible(client, sms, 70)
    sent = await forgot(client, valid_phone(70))
    unknown = await forgot(client, valid_phone(71))
    assert valid_phone(70) in sms.sent and valid_phone(71) not in sms.sent
    assert sent.status_code == unknown.status_code == 200
    assert sent.json() == unknown.json() == {"sent": True, "resend_after_seconds": 45}
