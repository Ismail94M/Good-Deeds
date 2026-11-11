from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_session
from app.core.errors import AppError, NotFound, Unauthorized
from app.core.ratelimit import Limiter, client_ip
from app.core.sms import SmsProvider, get_sms_provider
from app.modules.identity import service
from app.modules.identity._oauth import OAuthError, OAuthVerifier, get_oauth_verifiers
from app.modules.identity.models import User, UserStatus
from app.modules.identity.schemas import (
    AgeAndConsent,
    AuthConfig,
    LoginRequest,
    Me,
    OAuthLoginRequest,
    PhoneStartRequest,
    PhoneStartResponse,
    PhoneVerifyRequest,
    ProfileUpdate,
    PublicProfile,
    QrCodeResponse,
    SignupRequest,
    TokenResponse,
)
from app.modules.identity.service import CurrentUser, DeviceId

router = APIRouter()
Session = Annotated[AsyncSession, Depends(get_session)]

REFRESH_COOKIE = "gd_refresh"
REFRESH_COOKIE_PATH = "/api/auth"


def _set_refresh_cookie(response: Response, raw: str) -> None:
    settings = get_settings()
    response.set_cookie(
        REFRESH_COOKIE,
        raw,
        max_age=settings.refresh_token_days * 86400,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path=REFRESH_COOKIE_PATH,
    )


def _clear_refresh_cookie(response: Response) -> None:
    settings = get_settings()
    response.delete_cookie(
        REFRESH_COOKIE,
        path=REFRESH_COOKIE_PATH,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
    )


async def _start_session(
    session: AsyncSession, response: Response, user: User, device_id: str | None
) -> TokenResponse:
    access, expires, refresh = await service.issue_session(session, user, device_id)
    await session.commit()
    _set_refresh_cookie(response, refresh)
    return TokenResponse(access_token=access, expires_at=expires)


# --------------------------------------------------- auth


@router.get("/auth/config", response_model=AuthConfig, tags=["auth"])
async def auth_config() -> AuthConfig:
    s = get_settings()
    return AuthConfig(
        google_client_id=s.google_client_id or None, facebook_app_id=s.facebook_app_id or None
    )


@router.post(
    "/auth/signup", response_model=TokenResponse, status_code=status.HTTP_201_CREATED, tags=["auth"]
)
async def signup(
    data: SignupRequest,
    request: Request,
    response: Response,
    session: Session,
    device_id: DeviceId,
    limiter: Limiter,
) -> TokenResponse:
    await limiter.hit(f"signup:ip:{client_ip(request)}", get_settings().rate_signup_ip)
    user = await service.register_with_password(session, data)
    return await _start_session(session, response, user, device_id)


@router.post("/auth/login", response_model=TokenResponse, tags=["auth"])
async def login(
    data: LoginRequest,
    request: Request,
    response: Response,
    session: Session,
    device_id: DeviceId,
    limiter: Limiter,
) -> TokenResponse:
    settings = get_settings()
    await limiter.hit(f"login:ip:{client_ip(request)}", settings.rate_login_ip)
    await limiter.hit(f"login:email:{data.email.lower()}", settings.rate_login_email)
    user = await service.authenticate_password(session, data.email, data.password)
    return await _start_session(session, response, user, device_id)


@router.post("/auth/oauth/{provider}", response_model=TokenResponse, tags=["auth"])
async def oauth_login(
    provider: str,
    data: OAuthLoginRequest,
    request: Request,
    response: Response,
    session: Session,
    device_id: DeviceId,
    limiter: Limiter,
    verifiers: Annotated[dict[str, OAuthVerifier], Depends(get_oauth_verifiers)],
) -> TokenResponse:
    await limiter.hit(f"oauth:ip:{client_ip(request)}", get_settings().rate_oauth_ip)
    verifier = verifiers.get(provider)
    if verifier is None:
        raise AppError("Unknown sign-in provider.", code="unknown_provider")
    try:
        identity = await verifier.verify(data.token)
    except OAuthError as exc:
        raise Unauthorized(
            "We couldn't sign you in with that account. Please try again.", code="oauth_failed"
        ) from exc
    user = await service.login_with_oauth(session, identity)
    return await _start_session(session, response, user, device_id)


@router.post("/auth/refresh", response_model=TokenResponse, tags=["auth"])
async def refresh(
    request: Request,
    response: Response,
    session: Session,
    device_id: DeviceId,
    limiter: Limiter,
    gd_refresh: Annotated[str | None, Cookie()] = None,
) -> TokenResponse | JSONResponse:
    await limiter.hit(f"refresh:ip:{client_ip(request)}", get_settings().rate_refresh_ip)
    try:
        if not gd_refresh:
            raise Unauthorized("Please log in.", code="not_authenticated")
        _, access, expires, new_raw = await service.rotate_refresh_token(
            session, gd_refresh, device_id
        )
    except Unauthorized as exc:
        # Return the error ourselves: a raised exception would drop the cookie deletion,
        # and the browser would keep replaying a dead token.
        failed = JSONResponse(
            status_code=exc.status_code, content={"detail": exc.detail, "code": exc.code}
        )
        _clear_refresh_cookie(failed)
        return failed
    await session.commit()
    _set_refresh_cookie(response, new_raw)
    return TokenResponse(access_token=access, expires_at=expires)


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT, tags=["auth"])
async def logout(
    response: Response, session: Session, gd_refresh: Annotated[str | None, Cookie()] = None
) -> Response:
    if gd_refresh:
        await service.revoke_refresh_token(session, gd_refresh)
        await session.commit()
    response.status_code = status.HTTP_204_NO_CONTENT
    _clear_refresh_cookie(response)
    return response


# --------------------------------------------------- me


@router.get("/users/me", response_model=Me, tags=["users"])
async def get_me(user: CurrentUser, session: Session) -> Me:
    result = await service.me(session, user)
    await session.commit()
    return result


@router.patch("/users/me", response_model=Me, tags=["users"])
async def update_me(data: ProfileUpdate, user: CurrentUser, session: Session) -> Me:
    await service.update_profile(session, user, data)
    result = await service.me(session, user)
    await session.commit()
    return result


@router.post("/users/me/age-consent", response_model=Me, tags=["users"])
async def age_consent(
    data: AgeAndConsent, user: CurrentUser, session: Session, device_id: DeviceId
) -> Me:
    await service.complete_age_and_consent(session, user, data, device_id)
    result = await service.me(session, user)
    await session.commit()
    return result


@router.post("/users/me/phone/start", response_model=PhoneStartResponse, tags=["users"])
async def phone_start(
    data: PhoneStartRequest,
    request: Request,
    user: CurrentUser,
    session: Session,
    limiter: Limiter,
    sms: Annotated[SmsProvider, Depends(get_sms_provider)],
) -> PhoneStartResponse:
    settings = get_settings()
    await limiter.hit(f"phone_start:ip:{client_ip(request)}", settings.rate_phone_start_ip)
    await limiter.hit("sms:global", settings.rate_sms_global)
    result = await service.start_phone_verification(session, user, data.phone, sms)
    await session.commit()
    return result


@router.post("/users/me/phone/verify", response_model=Me, tags=["users"])
async def phone_verify(
    data: PhoneVerifyRequest, user: CurrentUser, session: Session, limiter: Limiter
) -> Me:
    await limiter.hit(f"phone_verify:user:{user.id}", get_settings().rate_phone_verify_user)
    await service.confirm_phone(session, user, data.phone, data.code)
    result = await service.me(session, user)
    await session.commit()
    return result


@router.get("/users/me/qr", response_model=QrCodeResponse, tags=["users"])
async def my_qr(user: CurrentUser, session: Session) -> QrCodeResponse:
    result = await service.qr_code(session, user)
    await session.commit()
    return result


@router.post("/users/me/qr/rotate", response_model=QrCodeResponse, tags=["users"])
async def rotate_qr(user: CurrentUser, session: Session) -> QrCodeResponse:
    await service.qr_code(session, user)  # same onboarding gate as viewing the code
    await service.rotate_qr_key(session, user)
    result = await service.qr_code(session, user)
    await session.commit()
    return result


@router.get("/users/{handle}", response_model=PublicProfile, tags=["users"])
async def get_profile(handle: str, session: Session) -> PublicProfile:
    user = await service.get_user_by_handle(session, handle)
    if user is None or user.status != UserStatus.ACTIVE:
        raise NotFound("We couldn't find that person.", code="user_not_found")
    return service.public_profile(user)
