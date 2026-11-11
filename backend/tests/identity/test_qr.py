import time
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import NotFound
from app.core.sms import ConsoleSmsProvider
from app.modules.identity import _qr, service
from app.modules.identity.models import Consent, DoerCode, User, UserStatus
from tests.identity.helpers import Session, onboarded_user, signup

WINDOW = get_settings().qr_window_seconds
GRACE = get_settings().qr_grace_windows


async def _user(session: AsyncSession, s: Session) -> User:
    user = await session.get(User, s.user_id)
    assert user is not None
    return user


async def _set_short_id(session: AsyncSession, s: Session, short_id: str) -> None:
    await session.execute(
        update(DoerCode).where(DoerCode.user_id == s.user_id).values(short_id=short_id)
    )
    await session.commit()


async def test_qr_forbidden_before_onboarding(client: AsyncClient) -> None:
    s = await signup(client)
    response = await client.get("/api/users/me/qr", headers=s.headers)
    assert response.status_code == 403
    assert response.json()["code"] == "onboarding_required"


async def test_rotate_before_onboarding_changes_nothing(
    client: AsyncClient, session: AsyncSession
) -> None:
    s = await signup(client)
    response = await client.post("/api/users/me/qr/rotate", headers=s.headers)
    assert response.status_code == 403
    session.expire_all()
    code = await session.get(DoerCode, s.user_id)
    assert code is not None and code.key_version == 1


async def test_qr_token_resolves_to_doer(
    client: AsyncClient, session: AsyncSession, sms: ConsoleSmsProvider
) -> None:
    s = await onboarded_user(client, sms, 50)
    response = await client.get("/api/users/me/qr", headers=s.headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["display_id"] == f"GD-{body['short_id']}"
    assert body["scan_url"].endswith(f"/scan/{body['token']}")
    assert 1 <= body["refresh_after_seconds"] <= WINDOW

    doer = await service.resolve_qr_token(session, body["token"])
    assert doer.id == s.user_id


async def test_qr_token_valid_within_grace_and_invalid_just_after(
    client: AsyncClient, session: AsyncSession, sms: ConsoleSmsProvider
) -> None:
    s = await onboarded_user(client, sms, 51)
    user = await _user(session, s)
    window = 60_000_000  # a fixed window, far from "now"
    minted_at = float(window * WINDOW) + 3.0
    qr = await service.qr_code(session, user, now=minted_at)
    expiry = float((window + 1 + GRACE) * WINDOW)
    assert qr.expires_at == datetime.fromtimestamp(expiry, UTC)
    assert qr.refresh_after_seconds == WINDOW - 3

    for now in (minted_at, float(window * WINDOW), expiry - 0.001):
        assert (await service.resolve_qr_token(session, qr.token, now=now)).id == s.user_id
    for now in (expiry, expiry + WINDOW, float(window * WINDOW) - 0.001):
        with pytest.raises(NotFound):
            await service.resolve_qr_token(session, qr.token, now=now)


async def test_tampered_or_swapped_token_rejected(
    client: AsyncClient, session: AsyncSession, sms: ConsoleSmsProvider
) -> None:
    a = await onboarded_user(client, sms, 52)
    b = await onboarded_user(client, sms, 53)
    now = time.time()
    token_a = (await service.qr_code(session, await _user(session, a), now=now)).token
    token_b = (await service.qr_code(session, await _user(session, b), now=now)).token
    short_a, window_a, sig_a = token_a.split(".")
    short_b, _, sig_b = token_b.split(".")

    last = sig_a[-1]
    tampered_sig = sig_a[:-1] + ("A" if last != "A" else "B")
    candidates = [
        f"{short_a}.{window_a}.{tampered_sig}",
        f"{short_b}.{window_a}.{sig_a}",  # B's ID with A's (valid-looking) signature
        f"{short_a}.{window_a}.{sig_b}",
        f"{short_a}.{int(window_a) + 1}.{sig_a}",
        f"{short_a}.{window_a}",
        f"{short_a}.{window_a}.{sig_a}.extra",
        f"{short_a}.-{window_a}.{sig_a}",
        f"{short_a}.{window_a}.",
        "",
        "garbage",
        f"ZZZZZZZ.{window_a}.{sig_a}",
    ]
    for token in candidates:
        with pytest.raises(NotFound):
            await service.resolve_qr_token(session, token, now=now)
    assert (await service.resolve_qr_token(session, token_a, now=now)).id == a.user_id


@pytest.mark.parametrize(
    "token",
    [
        "ABCDEFG.\u00b2.0123456789abcdef012345",  # superscript two
        "ABCDEFG.\uff11\uff12.0123456789abcdef012345",  # full-width digits
        "ABCDEFG.\u0967\u0968.0123456789abcdef012345",  # Devanagari digits
        "ABCDEFG.123." + "a" * 5000,  # oversized
        "ABCDEFG.1234567890123.0123456789abcdef012345",  # 13-digit window
        "abcdefg.123.0123456789abcdef012345",  # lowercase short ID
        "ABCDEFU.123.0123456789abcdef012345",  # U not in alphabet
        "ABCDEFG.123.0123456789ABCDEF012345",  # uppercase hex
        "ABCDEFG.123.0123456789abcdef01234g",  # non-hex signature
        "ABCDEFG.123.0123456789abcdef01234",  # short signature
        "ABCDEFG.12 3.0123456789abcdef012345",
        "ABCDEFG.+123.0123456789abcdef012345",
        "ABCDEFG.123.0123456789abcdef012345\x00",
    ],
)
async def test_malformed_tokens_are_not_found_never_500(session: AsyncSession, token: str) -> None:
    with pytest.raises(NotFound) as exc:
        await service.resolve_qr_token(session, token)
    assert exc.value.code == "qr_invalid"
    assert _qr.parse_token(token) is None


async def test_twelve_digit_window_parses_but_is_out_of_range(
    client: AsyncClient, session: AsyncSession, sms: ConsoleSmsProvider
) -> None:
    s = await onboarded_user(client, sms, 57)
    code = await session.get(DoerCode, s.user_id)
    assert code is not None
    token = _qr.make_token(code.short_id, code.key_version, 999_999_999_999)
    assert _qr.parse_token(token) is not None
    with pytest.raises(NotFound):
        await service.resolve_qr_token(session, token)


async def test_doer_must_be_fully_onboarded_to_resolve(
    client: AsyncClient, session: AsyncSession, sms: ConsoleSmsProvider
) -> None:
    s = await onboarded_user(client, sms, 58)
    now = time.time()
    token = (await service.qr_code(session, await _user(session, s), now=now)).token
    code = await session.get(DoerCode, s.user_id)
    assert code is not None
    # Withdrawing consent makes the account incomplete again.
    await session.execute(
        update(Consent).where(Consent.user_id == s.user_id).values(withdrawn_at=datetime.now(UTC))
    )
    await session.commit()
    with pytest.raises(NotFound):
        await service.resolve_qr_token(session, token, now=now)
    with pytest.raises(NotFound):
        await service.resolve_short_id(session, code.short_id)


async def test_not_onboarded_user_short_id_does_not_resolve(
    client: AsyncClient, session: AsyncSession
) -> None:
    s = await signup(client)  # adult + consents, but no verified phone
    code = await session.get(DoerCode, s.user_id)
    assert code is not None
    with pytest.raises(NotFound):
        await service.resolve_short_id(session, code.short_id)
    token = _qr.make_token(code.short_id, code.key_version, _qr.current_window())
    with pytest.raises(NotFound):
        await service.resolve_qr_token(session, token)


async def test_rotate_invalidates_old_tokens(
    client: AsyncClient, session: AsyncSession, sms: ConsoleSmsProvider
) -> None:
    s = await onboarded_user(client, sms, 54)
    old = (await client.get("/api/users/me/qr", headers=s.headers)).json()["token"]
    rotated = await client.post("/api/users/me/qr/rotate", headers=s.headers)
    assert rotated.status_code == 200
    new = rotated.json()["token"]
    assert new != old

    session.expire_all()
    with pytest.raises(NotFound):
        await service.resolve_qr_token(session, old)
    assert (await service.resolve_qr_token(session, new)).id == s.user_id
    code = await session.get(DoerCode, s.user_id)
    assert code is not None and code.key_version == 2


async def test_revoked_code_or_inactive_user_does_not_resolve(
    client: AsyncClient, session: AsyncSession, sms: ConsoleSmsProvider
) -> None:
    revoked = await onboarded_user(client, sms, 55)
    suspended = await onboarded_user(client, sms, 56)
    now = time.time()
    t1 = (await service.qr_code(session, await _user(session, revoked), now=now)).token
    t2 = (await service.qr_code(session, await _user(session, suspended), now=now)).token
    c1 = await session.get(DoerCode, revoked.user_id)
    c2 = await session.get(DoerCode, suspended.user_id)
    assert c1 is not None and c2 is not None

    await session.execute(
        update(DoerCode)
        .where(DoerCode.user_id == revoked.user_id)
        .values(revoked_at=datetime.now(UTC))
    )
    await session.execute(
        update(User).where(User.id == suspended.user_id).values(status=UserStatus.SUSPENDED)
    )
    await session.commit()

    for token, short_id in ((t1, c1.short_id), (t2, c2.short_id)):
        with pytest.raises(NotFound):
            await service.resolve_qr_token(session, token, now=now)
        with pytest.raises(NotFound):
            await service.resolve_short_id(session, short_id)

    response = await client.get("/api/users/me/qr", headers=revoked.headers)
    assert response.status_code == 403
    assert response.json()["code"] == "qr_revoked"


# --------------------------------------------------------------------------- short IDs


@pytest.mark.parametrize(
    "typed",
    [
        "10K3M9Q",
        "10k3m9q",
        "GD-10K3M9Q",
        "gd-10k3m9q",
        "gd10k3m9q",
        " GD-10K 3M9Q ",
        "lok3m9q",  # L -> 1, O -> 0
        "IOK3M9Q",  # I -> 1, O -> 0
        "gd-lok3m9q",
    ],
)
async def test_resolve_short_id_accepts_typing_variants(
    client: AsyncClient, session: AsyncSession, sms: ConsoleSmsProvider, typed: str
) -> None:
    s = await onboarded_user(client, sms, 70)
    await _set_short_id(session, s, "10K3M9Q")
    assert (await service.resolve_short_id(session, typed)).id == s.user_id


@pytest.mark.parametrize(
    "typed",
    [
        "",
        "GD-",
        "10K3M9",
        "10K3M9QQ",
        "GD-10K3M9QQ",
        "10K3M9U",
        "10K3M9!",
        "GDX10K3M9Q",
        chr(0x967) + chr(0x966) + "K3M9Q",
    ],
)
async def test_resolve_short_id_rejects_malformed(
    client: AsyncClient, session: AsyncSession, sms: ConsoleSmsProvider, typed: str
) -> None:
    s = await onboarded_user(client, sms, 71)
    await _set_short_id(session, s, "10K3M9Q")
    with pytest.raises(NotFound):
        await service.resolve_short_id(session, typed)


async def test_resolve_short_id_rejects_unknown(session: AsyncSession) -> None:
    unknown = _qr.new_short_id()
    with pytest.raises(NotFound):
        await service.resolve_short_id(session, unknown)


async def test_short_id_starting_with_gd_works_without_prefix(
    client: AsyncClient, session: AsyncSession, sms: ConsoleSmsProvider
) -> None:
    s = await onboarded_user(client, sms, 72)
    await _set_short_id(session, s, "GD12345")
    for typed in ("GD12345", "gd12345", "GD-GD12345", "gdgd12345"):
        assert (await service.resolve_short_id(session, typed)).id == s.user_id


def test_new_short_ids_use_unambiguous_alphabet() -> None:
    for _ in range(200):
        short_id = _qr.new_short_id()
        assert len(short_id) == _qr.SHORT_ID_LENGTH
        assert not set(short_id) & set("ILOU")
        assert _qr.normalize_short_id(short_id) == short_id


async def test_qr_requires_login(client: AsyncClient) -> None:
    assert (await client.get("/api/users/me/qr")).status_code == 401
    assert (await client.post("/api/users/me/qr/rotate")).status_code == 401
