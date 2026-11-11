"""Identity public interface. Other modules may import only this file from identity.

Exposes: current_user / require_onboarded dependencies, user lookups, and QR/short-ID
resolution (used by Verification in M2). Everything else here backs the identity router.
"""

import secrets
import time
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Annotated
from zoneinfo import ZoneInfo

import jwt
import phonenumbers
from fastapi import Depends, Header
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from phonenumbers import PhoneNumberType
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_session
from app.core.errors import AppError, Conflict, Forbidden, NotFound, TooManyRequests, Unauthorized
from app.core.events import emit
from app.core.security import (
    constant_time_equal,
    create_access_token,
    decode_access_token,
    hash_password,
    keyed_hash,
    new_opaque_token,
    sha256_hex,
    verify_password,
)
from app.core.sms import SmsProvider
from app.modules.identity import _qr
from app.modules.identity._oauth import OAuthIdentity
from app.modules.identity.events import PhoneVerified, UserRegistered
from app.modules.identity.models import (
    Consent,
    DoerCode,
    OAuthAccount,
    PhoneHistory,
    PhoneOtp,
    RefreshToken,
    Role,
    User,
    UserDevice,
    UserStatus,
)
from app.modules.identity.schemas import (
    AgeAndConsent,
    Me,
    OnboardingStatus,
    PhoneStartResponse,
    ProfileUpdate,
    PublicProfile,
    QrCodeResponse,
    SignupRequest,
)

MIN_AGE_YEARS = 18
LAUNCH_TZ = ZoneInfo("Asia/Kolkata")
# Bump a version when the document changes; users are asked to accept again.
CURRENT_CONSENTS: dict[str, str] = {"terms": "2026-11", "privacy": "2026-11"}
_MOBILE_TYPES = {PhoneNumberType.MOBILE, PhoneNumberType.FIXED_LINE_OR_MOBILE}


def _now() -> datetime:
    return datetime.now(UTC)


# --------------------------------------------------- lookups


async def get_user(session: AsyncSession, user_id: uuid.UUID) -> User | None:
    return await session.get(User, user_id)


async def get_user_by_handle(session: AsyncSession, handle: str) -> User | None:
    return await session.scalar(select(User).where(User.handle == handle.lower()))


async def device_hashes(session: AsyncSession, user_id: uuid.UUID) -> set[str]:
    """Device fingerprints seen for a user. A weak fraud signal: clients choose the ID."""
    rows = await session.scalars(
        select(UserDevice.device_hash).where(UserDevice.user_id == user_id)
    )
    return set(rows)


def phone_hash(phone_e164: str) -> str:
    return keyed_hash("phone", phone_e164)


def public_profile(user: User) -> PublicProfile:
    return PublicProfile(
        handle=user.handle,
        display_name=user.display_name,
        city=user.city,
        area=user.area,
        bio=user.bio,
        joined_at=user.created_at,
    )


# --------------------------------------------------- age, consent, onboarding


def is_adult(date_of_birth: date, today: date | None = None) -> bool:
    """18+ on today's date in Bangalore (not UTC, which lags IST until 05:30)."""
    today = today or datetime.now(LAUNCH_TZ).date()
    years = (
        today.year
        - date_of_birth.year
        - ((today.month, today.day) < (date_of_birth.month, date_of_birth.day))
    )
    return years >= MIN_AGE_YEARS


async def _record_age_and_consent(session: AsyncSession, user: User, data: AgeAndConsent) -> None:
    if not is_adult(data.date_of_birth):
        raise AppError("You must be 18 or older to use Good Deeds.", code="underage")
    user.adult_declared_at = user.adult_declared_at or _now()
    for consent_type, version in CURRENT_CONSENTS.items():
        await session.execute(
            insert(Consent)
            .values(id=uuid.uuid4(), user_id=user.id, consent_type=consent_type, version=version)
            .on_conflict_do_nothing(index_elements=["user_id", "consent_type", "version"])
        )


async def _has_current_consents(session: AsyncSession, user_id: uuid.UUID) -> bool:
    result = await session.execute(
        select(Consent.consent_type, Consent.version).where(
            Consent.user_id == user_id, Consent.withdrawn_at.is_(None)
        )
    )
    granted = {(row[0], row[1]) for row in result}
    return all((t, v) in granted for t, v in CURRENT_CONSENTS.items())


async def onboarding_status(session: AsyncSession, user: User) -> OnboardingStatus:
    consents = await _has_current_consents(session, user.id)
    adult = user.adult_declared_at is not None
    phone = user.phone_verified_at is not None
    return OnboardingStatus(
        adult=adult, consents=consents, phone=phone, complete=adult and consents and phone
    )


async def complete_age_and_consent(
    session: AsyncSession, user: User, data: AgeAndConsent, device_id: str | None
) -> None:
    await _record_age_and_consent(session, user, data)
    await session.flush()
    # The device is only recorded once the privacy notice (which covers it) is accepted.
    await record_device(session, user.id, device_id)


async def me(session: AsyncSession, user: User) -> Me:
    status = await onboarding_status(session, user)
    code = await _ensure_doer_code(session, user)
    return Me(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        handle=user.handle,
        city=user.city,
        area=user.area,
        bio=user.bio,
        phone_e164=user.phone_e164,
        role=user.role,
        # The ID identifies a doer, so it is only shown once the account is fully set up.
        short_id=_qr.SHORT_ID_PREFIX + code.short_id if status.complete else None,
        created_at=user.created_at,
        onboarding=status,
    )


# --------------------------------------------------- registration


async def _handle_taken(
    session: AsyncSession, handle: str, exclude: uuid.UUID | None = None
) -> bool:
    query = select(User.id).where(User.handle == handle)
    if exclude is not None:
        query = query.where(User.id != exclude)
    return await session.scalar(query) is not None


async def _generate_handle(session: AsyncSession, name: str | None) -> str:
    base = "".join(c for c in (name or "").lower() if c.isascii() and c.isalnum())[:20]
    base = base or "member"
    if len(base) < 3:
        base = f"{base}member"
    for _ in range(20):
        candidate = f"{base}{secrets.randbelow(10_000):04d}"
        if not await _handle_taken(session, candidate):
            return candidate
    return f"member{uuid.uuid4().hex[:12]}"


async def _ensure_doer_code(session: AsyncSession, user: User) -> DoerCode:
    code = await session.get(DoerCode, user.id)
    if code is not None:
        return code
    for _ in range(10):
        short_id = _qr.new_short_id()
        if await session.scalar(select(DoerCode.user_id).where(DoerCode.short_id == short_id)):
            continue
        code = DoerCode(user_id=user.id, short_id=short_id, key_version=1)
        session.add(code)
        await session.flush()
        return code
    raise RuntimeError("Could not allocate a unique short ID")


def _unique_violation(exc: IntegrityError) -> Conflict:
    text = str(exc.orig)
    if "uq_users_handle" in text:
        return Conflict("That handle is taken. Please choose another.", code="handle_taken")
    if "uq_users_email" in text:
        return Conflict(
            "An account with this email already exists. Try logging in.", code="email_taken"
        )
    return Conflict("That conflicts with an existing account.", code="conflict")


async def register_with_password(session: AsyncSession, data: SignupRequest) -> User:
    email = data.email.lower()
    if await session.scalar(select(User.id).where(User.email == email)):
        raise Conflict(
            "An account with this email already exists. Try logging in.", code="email_taken"
        )
    if await _handle_taken(session, data.handle):
        raise Conflict("That handle is taken. Please choose another.", code="handle_taken")
    if not is_adult(data.date_of_birth):
        raise AppError("You must be 18 or older to use Good Deeds.", code="underage")

    user = User(
        email=email,
        password_hash=await hash_password(data.password),
        display_name=data.display_name,
        handle=data.handle,
    )
    session.add(user)
    try:
        await session.flush()
    except IntegrityError as exc:  # a concurrent signup took the email or handle
        raise _unique_violation(exc) from exc
    await _record_age_and_consent(session, user, data)
    await _ensure_doer_code(session, user)
    emit(session, UserRegistered(user_id=user.id, method="password"))
    await session.flush()
    return user


async def authenticate_password(session: AsyncSession, email: str, password: str) -> User:
    user = await session.scalar(select(User).where(User.email == email.lower()))
    # Always run argon2, even for unknown emails, so timing doesn't reveal accounts.
    ok = await verify_password(user.password_hash if user else None, password)
    if user is None or not ok:
        raise Unauthorized("Incorrect email or password.", code="bad_credentials")
    _ensure_active(user)
    return user


async def login_with_oauth(session: AsyncSession, identity: OAuthIdentity) -> User:
    account = await session.scalar(
        select(OAuthAccount).where(
            OAuthAccount.provider == identity.provider, OAuthAccount.subject == identity.subject
        )
    )
    if account is not None:
        user = await session.get(User, account.user_id)
        assert user is not None
        _ensure_active(user)
        return user

    email = identity.email.lower() if identity.email and identity.email_verified else None
    if email and await session.scalar(select(User.id).where(User.email == email)):
        # Never link accounts automatically by email: a provider that mis-reports email
        # ownership would otherwise hand over someone else's account. Linking from inside
        # a logged-in session can be added later.
        raise Conflict(
            "An account with this email already exists. Log in the way you did before.",
            code="email_exists",
        )

    name = (identity.name or "").strip()[:60] or "Good Deeds member"
    user = User(
        email=email,
        email_verified_at=_now() if email else None,
        display_name=name,
        handle=await _generate_handle(session, identity.name),
    )
    session.add(user)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise _unique_violation(exc) from exc
    session.add(
        OAuthAccount(
            user_id=user.id, provider=identity.provider, subject=identity.subject, email=email
        )
    )
    await _ensure_doer_code(session, user)
    emit(session, UserRegistered(user_id=user.id, method=identity.provider))
    await session.flush()
    return user


def _ensure_active(user: User) -> None:
    if user.status != UserStatus.ACTIVE:
        raise Forbidden("This account is not active. Contact support.", code="account_inactive")


# --------------------------------------------------- sessions


async def record_device(
    session: AsyncSession, user_id: uuid.UUID, device_id: str | None
) -> str | None:
    """Record the device (keyed hash only), but only after the privacy notice is accepted."""
    if not device_id or not await _has_current_consents(session, user_id):
        return None
    device_hash = keyed_hash("device", device_id)
    await session.execute(
        insert(UserDevice)
        .values(user_id=user_id, device_hash=device_hash)
        .on_conflict_do_update(
            index_elements=["user_id", "device_hash"], set_={"last_seen_at": func.now()}
        )
    )
    return device_hash


async def issue_session(
    session: AsyncSession,
    user: User,
    device_id: str | None,
    session_started_at: datetime | None = None,
) -> tuple[str, datetime, str]:
    """Return (access_token, access_expires_at, raw_refresh_token)."""
    device_hash = await record_device(session, user.id, device_id)
    now = _now()
    raw = new_opaque_token()
    session.add(
        RefreshToken(
            user_id=user.id,
            token_hash=sha256_hex(raw),
            device_hash=device_hash,
            session_started_at=session_started_at or now,
            token_version=user.token_version,
            expires_at=now + timedelta(days=get_settings().refresh_token_days),
        )
    )
    access, expires = create_access_token(user.id, user.role.value, user.token_version)
    await session.flush()
    return access, expires, raw


_SESSION_ENDED = "Your session has ended. Please log in again."


async def rotate_refresh_token(
    session: AsyncSession, raw: str, device_id: str | None
) -> tuple[User, str, datetime, str]:
    settings = get_settings()
    now = _now()
    token = await session.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == sha256_hex(raw)).with_for_update()
    )
    if token is None:
        raise Unauthorized(_SESSION_ENDED, code="session_expired")
    user = await session.get(User, token.user_id)
    assert user is not None

    if token.revoked_at is not None:
        same_device = (
            device_id is not None
            and token.device_hash is not None
            and token.device_hash == keyed_hash("device", device_id)
        )
        recently = now - token.revoked_at < timedelta(seconds=settings.refresh_reuse_grace_seconds)
        # Only a token replaced by normal rotation qualifies: never one ended by logout or
        # by "revoke all" (theft detection), and never from before a token_version bump.
        rotated = token.revoked_reason == "rotated" and token.token_version == user.token_version
        if same_device and recently and rotated:
            # Two tabs on the same device refreshed at once: not theft. Start a sibling
            # session in the same chain instead of ending every session.
            _ensure_active(user)
            access, expires, new_raw = await issue_session(
                session, user, device_id, token.session_started_at
            )
            return user, access, expires, new_raw
        # A revoked token was replayed: assume theft and end every session for this user.
        await revoke_all_sessions(session, user)
        await session.commit()
        raise Unauthorized(_SESSION_ENDED, code="session_expired")

    session_age = now - token.session_started_at
    if token.expires_at <= now or session_age > timedelta(days=settings.session_max_days):
        raise Unauthorized(_SESSION_ENDED, code="session_expired")
    _ensure_active(user)
    if token.token_version != user.token_version:
        raise Unauthorized(_SESSION_ENDED, code="session_expired")
    token.revoked_at = now
    token.revoked_reason = "rotated"
    access, expires, new_raw = await issue_session(
        session, user, device_id, token.session_started_at
    )
    return user, access, expires, new_raw


async def revoke_refresh_token(session: AsyncSession, raw: str) -> None:
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.token_hash == sha256_hex(raw), RefreshToken.revoked_at.is_(None))
        .values(revoked_at=_now(), revoked_reason="logout")
    )


async def revoke_all_sessions(session: AsyncSession, user: User) -> None:
    """End every session, including access tokens already issued (via token_version)."""
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=_now(), revoked_reason="revoked_all")
    )
    user.token_version += 1
    await session.flush()


async def purge_expired_records(session: AsyncSession) -> None:
    """Data retention (DPDP): drop OTPs after a day and dead sessions after 30 days."""
    now = _now()
    await session.execute(delete(PhoneOtp).where(PhoneOtp.created_at < now - timedelta(days=1)))
    await session.execute(
        delete(RefreshToken).where(
            or_(
                RefreshToken.expires_at < now - timedelta(days=30),
                RefreshToken.revoked_at < now - timedelta(days=30),
            )
        )
    )


# --------------------------------------------------- dependencies

_bearer = HTTPBearer(auto_error=False)


async def current_user(
    session: Annotated[AsyncSession, Depends(get_session)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> User:
    if credentials is None:
        raise Unauthorized("Please log in.", code="not_authenticated")
    try:
        claims = decode_access_token(credentials.credentials)
        user_id = uuid.UUID(claims["sub"])
        token_version = int(claims.get("ver", -1))
    except (jwt.InvalidTokenError, KeyError, ValueError, TypeError) as exc:
        raise Unauthorized("Please log in again.", code="invalid_token") from exc
    user = await session.get(User, user_id)
    if user is None or user.token_version != token_version:
        raise Unauthorized("Please log in again.", code="invalid_token")
    _ensure_active(user)
    return user


CurrentUser = Annotated[User, Depends(current_user)]


async def require_onboarded(
    user: CurrentUser, session: Annotated[AsyncSession, Depends(get_session)]
) -> User:
    """For actions that need an adult, consenting user with a verified phone (e.g. reporting)."""
    if not (await onboarding_status(session, user)).complete:
        raise Forbidden("Please finish setting up your account first.", code="onboarding_required")
    return user


OnboardedUser = Annotated[User, Depends(require_onboarded)]


def require_role(*roles: Role):  # type: ignore[no-untyped-def]
    async def check(user: CurrentUser) -> User:
        if user.role not in roles:
            raise Forbidden("You don't have permission to do this.", code="forbidden")
        return user

    return check


def device_id_header(
    x_device_id: Annotated[str | None, Header(max_length=100)] = None,
) -> str | None:
    return x_device_id or None


DeviceId = Annotated[str | None, Depends(device_id_header)]


# --------------------------------------------------- profile


async def update_profile(session: AsyncSession, user: User, data: ProfileUpdate) -> User:
    if data.handle is not None and data.handle != user.handle:
        if await _handle_taken(session, data.handle, exclude=user.id):
            raise Conflict("That handle is taken. Please choose another.", code="handle_taken")
        user.handle = data.handle
    if data.display_name is not None:
        user.display_name = data.display_name
    if data.area is not None:
        user.area = data.area or None
    if data.bio is not None:
        user.bio = data.bio or None
    try:
        await session.flush()
    except IntegrityError as exc:
        raise _unique_violation(exc) from exc
    return user


# --------------------------------------------------- phone verification


def normalize_phone(raw: str) -> str:
    """Return the E.164 form of an Indian mobile number, or raise."""
    region = get_settings().default_phone_region
    try:
        parsed = phonenumbers.parse(raw, region)
    except phonenumbers.NumberParseException as exc:
        raise AppError("Please enter a valid Indian mobile number.", code="invalid_phone") from exc
    if (
        not phonenumbers.is_valid_number(parsed)
        or phonenumbers.region_code_for_number(parsed) != region
        or phonenumbers.number_type(parsed) not in _MOBILE_TYPES
    ):
        raise AppError("Please enter a valid Indian mobile number.", code="invalid_phone")
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


async def _phone_history_owner(session: AsyncSession, phone: str) -> uuid.UUID | None:
    return await session.scalar(
        select(PhoneHistory.user_id).where(PhoneHistory.phone_hash == phone_hash(phone))
    )


def _otp_hash(otp_id: uuid.UUID, code: str) -> str:
    return keyed_hash("otp", str(otp_id), code)


async def start_phone_verification(
    session: AsyncSession, user: User, raw_phone: str, sms: SmsProvider
) -> PhoneStartResponse:
    """Send a code. The response is identical whether or not the number is already in use,
    so this can't be used to find out who is on Good Deeds."""
    settings = get_settings()
    if user.phone_verified_at is not None:
        raise Conflict(
            "Your number is already verified. Contact support to change it.",
            code="phone_already_verified",
        )
    phone = normalize_phone(raw_phone)

    # Rate limits come first, before anything about the number is looked up.
    now = _now()
    hour_ago = now - timedelta(hours=1)
    count, latest = (
        await session.execute(
            select(func.count(), func.max(PhoneOtp.created_at)).where(
                PhoneOtp.user_id == user.id, PhoneOtp.created_at > hour_ago
            )
        )
    ).one()
    per_phone = await session.scalar(
        select(func.count()).where(PhoneOtp.phone_e164 == phone, PhoneOtp.created_at > hour_ago)
    )
    if count >= settings.otp_max_per_hour or (per_phone or 0) >= settings.otp_max_per_hour:
        raise TooManyRequests(
            "Too many codes requested. Please try again in an hour.", code="otp_rate_limited"
        )
    if latest is not None and (now - latest).total_seconds() < settings.otp_resend_seconds:
        raise TooManyRequests(
            "Please wait a few seconds before requesting another code.",
            code="otp_resend_too_soon",
        )

    owner = await _phone_history_owner(session, phone)
    taken = owner is not None and owner != user.id
    code = f"{secrets.randbelow(1_000_000):06d}"
    otp = PhoneOtp(
        id=uuid.uuid4(),
        user_id=user.id,
        phone_e164=phone,
        expires_at=now + timedelta(minutes=settings.otp_ttl_minutes),
        # A taken number gets a row (so limits still apply) whose hash matches no code.
        code_hash=new_opaque_token() if taken else "",
    )
    if not taken:
        otp.code_hash = _otp_hash(otp.id, code)
    session.add(otp)
    await session.flush()
    if taken:
        await sms.send(
            phone,
            "Someone tried to add this number to a Good Deeds account. It is already linked "
            "to an account, so no code was sent. If this wasn't you, you can ignore this.",
        )
    else:
        await sms.send(
            phone,
            f"Your Good Deeds code is {code}. It expires in {settings.otp_ttl_minutes} "
            "minutes. Never share this code.",
        )
    return PhoneStartResponse(
        phone_e164=phone,
        expires_at=otp.expires_at,
        resend_after_seconds=settings.otp_resend_seconds,
    )


async def confirm_phone(session: AsyncSession, user: User, raw_phone: str, code: str) -> None:
    settings = get_settings()
    if user.phone_verified_at is not None:
        raise Conflict(
            "Your number is already verified. Contact support to change it.",
            code="phone_already_verified",
        )
    phone = normalize_phone(raw_phone)
    otp = await session.scalar(
        select(PhoneOtp)
        .where(
            PhoneOtp.user_id == user.id,
            PhoneOtp.phone_e164 == phone,
            PhoneOtp.consumed_at.is_(None),
            PhoneOtp.expires_at > _now(),
        )
        .order_by(PhoneOtp.created_at.desc())
        .limit(1)
        .with_for_update()
    )
    if otp is None:
        raise AppError("This code has expired. Please request a new one.", code="otp_expired")
    if otp.attempts >= settings.otp_max_attempts:
        raise TooManyRequests(
            "Too many wrong attempts. Please request a new code.", code="otp_locked"
        )
    otp.attempts += 1
    if not constant_time_equal(otp.code_hash, _otp_hash(otp.id, code)):
        await session.commit()  # keep the attempt count even though we reject
        raise AppError("That code is not correct.", code="otp_incorrect")

    owner = await _phone_history_owner(session, phone)
    if owner is not None and owner != user.id:
        raise Conflict("This number is linked to another account.", code="phone_taken")
    otp.consumed_at = _now()
    user.phone_e164 = phone
    user.phone_verified_at = _now()
    if owner is None:
        session.add(PhoneHistory(phone_hash=phone_hash(phone), user_id=user.id))
    emit(session, PhoneVerified(user_id=user.id))
    try:
        await session.flush()
    except IntegrityError as exc:  # another account verified it at the same moment
        raise Conflict("This number is linked to another account.", code="phone_taken") from exc


# --------------------------------------------------- QR codes


async def qr_code(session: AsyncSession, user: User, now: float | None = None) -> QrCodeResponse:
    settings = get_settings()
    if not (await onboarding_status(session, user)).complete:
        raise Forbidden(
            "Finish setting up your account to get your QR code.", code="onboarding_required"
        )
    code = await _ensure_doer_code(session, user)
    if code.revoked_at is not None:
        raise Forbidden("Your QR code is disabled. Contact support.", code="qr_revoked")
    now = time.time() if now is None else now
    window = _qr.current_window(now)
    token = _qr.make_token(code.short_id, code.key_version, window)
    next_window_start = (window + 1) * settings.qr_window_seconds
    return QrCodeResponse(
        short_id=code.short_id,
        display_id=_qr.SHORT_ID_PREFIX + code.short_id,
        token=token,
        scan_url=f"{settings.web_base_url.rstrip('/')}/scan/{token}",
        expires_at=datetime.fromtimestamp(_qr.window_expiry(window), UTC),
        refresh_after_seconds=max(1, int(next_window_start - now)),
    )


async def rotate_qr_key(session: AsyncSession, user: User) -> None:
    """Invalidate every QR token issued so far (e.g. after a screenshot was shared)."""
    code = await _ensure_doer_code(session, user)
    code.key_version += 1
    await session.flush()


async def _active_doer(session: AsyncSession, short_id: str) -> tuple[DoerCode, User] | None:
    """A doer must be a fully onboarded account: adult, consenting, with a verified phone."""
    code = await session.scalar(select(DoerCode).where(DoerCode.short_id == short_id))
    if code is None or code.revoked_at is not None:
        return None
    user = await session.get(User, code.user_id)
    if user is None or user.status != UserStatus.ACTIVE:
        return None
    if not (await onboarding_status(session, user)).complete:
        return None
    return code, user


async def resolve_qr_token(session: AsyncSession, token: str, now: float | None = None) -> User:
    """The doer behind a scanned QR token. Raises NotFound if invalid or expired.

    M2 must also make each (token window, witness) pair single-use and check location
    and time plausibility (security review L2).
    """
    parsed = _qr.parse_token(token)
    found = await _active_doer(session, parsed.short_id) if parsed else None
    if (
        parsed is None
        or found is None
        or not _qr.signature_valid(parsed, found[0].key_version, now)
    ):
        raise NotFound(
            "This QR code is not valid or has expired. Ask them to show it again.",
            code="qr_invalid",
        )
    return found[1]


async def resolve_short_id(session: AsyncSession, raw: str) -> User:
    """The doer behind a manually typed ID. Raises NotFound if unknown.

    The short ID never rotates, so it identifies a doer but must NOT count as instant
    verification on its own. M2 pairs it with a rotating code (security review H3).
    """
    short_id = _qr.normalize_short_id(raw)
    found = await _active_doer(session, short_id) if short_id else None
    if found is None:
        raise NotFound(
            "We couldn't find anyone with that ID. Check it and try again.",
            code="short_id_unknown",
        )
    return found[1]
