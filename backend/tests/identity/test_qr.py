import time
import uuid
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFound
from app.core.runtime_config import load, publish
from app.core.security import keyed_hash
from app.core.sms import ConsoleSmsProvider
from app.modules.identity import _qr, service
from app.modules.identity.models import Consent, DoerCode, User, UserStatus
from app.modules.identity.schemas import UserSummary
from tests.identity.helpers import Session, onboarded_user, signup, valid_phone

# The seeded `qr` config (migration 0004).
WINDOW = 30
GRACE = 1
# A fixed window far from "now", so tests don't depend on the clock.
FIXED_WINDOW = 60_000_000


def _at(window: int, offset: float = 3.0) -> float:
    return float(window * WINDOW) + offset


async def _user(session: AsyncSession, s: Session) -> User:
    user = await session.get(User, s.user_id)
    assert user is not None
    return user


async def _code(session: AsyncSession, s: Session) -> DoerCode:
    code = await session.get(DoerCode, s.user_id)
    assert code is not None
    return code


async def _set_short_id(session: AsyncSession, s: Session, short_id: str) -> None:
    await session.execute(
        update(DoerCode).where(DoerCode.user_id == s.user_id).values(short_id=short_id)
    )
    await session.commit()


async def _publish_qr(session: AsyncSession, window_seconds: int, grace_windows: int) -> None:
    await publish(
        session,
        _qr.QrConfig(window_seconds=window_seconds, grace_windows=grace_windows),
        actor_id=None,
        reason="test",
    )
    await session.commit()


async def test_seeded_qr_config_is_30s_with_one_grace_window(session: AsyncSession) -> None:
    _, config = await load(session, _qr.QrConfig)
    assert config == _qr.QrConfig(window_seconds=WINDOW, grace_windows=GRACE)


def test_resolve_short_id_is_not_public() -> None:
    # The static ID alone must never count as verification (M1 review H3).
    assert not hasattr(service, "resolve_short_id")


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


async def test_qr_token_and_manual_code_resolve_to_doer(
    client: AsyncClient, session: AsyncSession, sms: ConsoleSmsProvider
) -> None:
    s = await onboarded_user(client, sms, 50)
    response = await client.get("/api/users/me/qr", headers=s.headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["display_id"] == f"GD-{body['short_id']}"
    assert body["scan_url"].endswith(f"/scan/{body['token']}")
    assert 1 <= body["refresh_after_seconds"] <= WINDOW
    assert len(body["manual_code"]) == 6 and body["manual_code"].isdigit()

    user = await _user(session, s)
    by_token = await service.resolve_qr_token(session, body["token"])
    assert by_token == service.ResolvedDoer(
        user_id=s.user_id,
        handle=user.handle,
        display_name=user.display_name,
        window=int(body["token"].split(".")[1]),
        short_id=body["short_id"],
    )
    by_code = await service.resolve_manual_code(session, body["display_id"], body["manual_code"])
    assert by_code.user_id == s.user_id
    assert by_code.short_id == body["short_id"]


async def test_qr_token_valid_within_grace_and_invalid_just_after(
    client: AsyncClient, session: AsyncSession, sms: ConsoleSmsProvider
) -> None:
    s = await onboarded_user(client, sms, 51)
    user = await _user(session, s)
    minted_at = _at(FIXED_WINDOW)
    qr = await service.qr_code(session, user, now=minted_at)
    expiry = float((FIXED_WINDOW + 1 + GRACE) * WINDOW)
    assert qr.expires_at == datetime.fromtimestamp(expiry, UTC)
    assert qr.refresh_after_seconds == WINDOW - 3

    for now in (minted_at, float(FIXED_WINDOW * WINDOW), expiry - 0.001):
        resolved = await service.resolve_qr_token(session, qr.token, now=now)
        assert resolved.user_id == s.user_id
        assert resolved.window == FIXED_WINDOW
    for now in (expiry, expiry + WINDOW, float(FIXED_WINDOW * WINDOW) - 0.001):
        with pytest.raises(NotFound):
            await service.resolve_qr_token(session, qr.token, now=now)


async def test_token_accepted_in_own_and_one_earlier_window_only(
    client: AsyncClient, session: AsyncSession, sms: ConsoleSmsProvider
) -> None:
    s = await onboarded_user(client, sms, 59)
    code = await _code(session, s)
    now = _at(FIXED_WINDOW)

    def token(window: int) -> str:
        return _qr.make_token(code.short_id, code.key_version, window)

    for window in (FIXED_WINDOW, FIXED_WINDOW - 1):
        assert (await service.resolve_qr_token(session, token(window), now=now)).window == window
    for window in (FIXED_WINDOW - 2, FIXED_WINDOW + 1):
        with pytest.raises(NotFound):
            await service.resolve_qr_token(session, token(window), now=now)


async def test_qr_window_and_grace_come_from_config(
    client: AsyncClient, session: AsyncSession, sms: ConsoleSmsProvider
) -> None:
    s = await onboarded_user(client, sms, 60)
    code = await _code(session, s)
    await _publish_qr(session, window_seconds=60, grace_windows=3)
    window = 30_000_000
    now = float(window * 60) + 5.0

    qr = await service.qr_code(session, await _user(session, s), now=now)
    assert qr.token.split(".")[1] == str(window)
    assert qr.refresh_after_seconds == 55
    assert qr.expires_at == datetime.fromtimestamp(float((window + 4) * 60), UTC)

    old = _qr.make_token(code.short_id, code.key_version, window - 3)
    assert (await service.resolve_qr_token(session, old, now=now)).user_id == s.user_id
    old_code = _qr.manual_code(code.short_id, code.key_version, window - 3)
    assert (
        await service.resolve_manual_code(session, code.short_id, old_code, now=now)
    ).window == (window - 3)
    too_old = _qr.make_token(code.short_id, code.key_version, window - 4)
    with pytest.raises(NotFound):
        await service.resolve_qr_token(session, too_old, now=now)

    # Turning grace off leaves only the current window.
    await _publish_qr(session, window_seconds=60, grace_windows=0)
    previous = _qr.make_token(code.short_id, code.key_version, window - 1)
    with pytest.raises(NotFound):
        await service.resolve_qr_token(session, previous, now=now)


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
    assert (await service.resolve_qr_token(session, token_a, now=now)).user_id == a.user_id


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
    code = await _code(session, s)
    token = _qr.make_token(code.short_id, code.key_version, 999_999_999_999)
    assert _qr.parse_token(token) is not None
    with pytest.raises(NotFound):
        await service.resolve_qr_token(session, token)


async def test_doer_must_be_fully_onboarded_to_resolve(
    client: AsyncClient, session: AsyncSession, sms: ConsoleSmsProvider
) -> None:
    s = await onboarded_user(client, sms, 58)
    now = time.time()
    qr = await service.qr_code(session, await _user(session, s), now=now)
    # Withdrawing consent makes the account incomplete again.
    await session.execute(
        update(Consent).where(Consent.user_id == s.user_id).values(withdrawn_at=datetime.now(UTC))
    )
    await session.commit()
    with pytest.raises(NotFound):
        await service.resolve_qr_token(session, qr.token, now=now)
    with pytest.raises(NotFound) as exc:
        await service.resolve_manual_code(session, qr.short_id, qr.manual_code, now=now)
    assert exc.value.code == "doer_code_invalid"


async def test_not_onboarded_user_does_not_resolve(
    client: AsyncClient, session: AsyncSession
) -> None:
    s = await signup(client)  # adult + consents, but no verified phone
    code = await _code(session, s)
    window = _qr.current_window(WINDOW)
    token = _qr.make_token(code.short_id, code.key_version, window)
    with pytest.raises(NotFound):
        await service.resolve_qr_token(session, token)
    manual = _qr.manual_code(code.short_id, code.key_version, window)
    with pytest.raises(NotFound):
        await service.resolve_manual_code(session, code.short_id, manual)


async def test_rotate_invalidates_old_tokens_and_codes(
    client: AsyncClient, session: AsyncSession, sms: ConsoleSmsProvider
) -> None:
    s = await onboarded_user(client, sms, 54)
    old = (await client.get("/api/users/me/qr", headers=s.headers)).json()
    rotated = await client.post("/api/users/me/qr/rotate", headers=s.headers)
    assert rotated.status_code == 200
    new = rotated.json()
    assert new["token"] != old["token"]

    session.expire_all()
    with pytest.raises(NotFound):
        await service.resolve_qr_token(session, old["token"])
    assert (await service.resolve_qr_token(session, new["token"])).user_id == s.user_id
    code = await _code(session, s)
    assert code.key_version == 2

    # The code for the same window under the old key no longer matches.
    window = int(old["token"].split(".")[1])
    stale = _qr.manual_code(code.short_id, 1, window)
    if stale != _qr.manual_code(code.short_id, 2, window):  # 1-in-a-million collision
        with pytest.raises(NotFound):
            await service.resolve_manual_code(session, code.short_id, stale, now=_at(window))
    resolved = await service.resolve_manual_code(session, code.short_id, new["manual_code"])
    assert resolved.user_id == s.user_id


async def test_revoked_code_or_inactive_user_does_not_resolve(
    client: AsyncClient, session: AsyncSession, sms: ConsoleSmsProvider
) -> None:
    revoked = await onboarded_user(client, sms, 55)
    suspended = await onboarded_user(client, sms, 56)
    now = time.time()
    q1 = await service.qr_code(session, await _user(session, revoked), now=now)
    q2 = await service.qr_code(session, await _user(session, suspended), now=now)

    await session.execute(
        update(DoerCode)
        .where(DoerCode.user_id == revoked.user_id)
        .values(revoked_at=datetime.now(UTC))
    )
    await session.execute(
        update(User).where(User.id == suspended.user_id).values(status=UserStatus.SUSPENDED)
    )
    await session.commit()

    for qr in (q1, q2):
        with pytest.raises(NotFound):
            await service.resolve_qr_token(session, qr.token, now=now)
        with pytest.raises(NotFound):
            await service.resolve_manual_code(session, qr.short_id, qr.manual_code, now=now)

    response = await client.get("/api/users/me/qr", headers=revoked.headers)
    assert response.status_code == 403
    assert response.json()["code"] == "qr_revoked"


# --------------------------------------------------------------------------- manual codes


async def test_manual_code_matches_current_and_previous_window_only(
    client: AsyncClient, session: AsyncSession, sms: ConsoleSmsProvider
) -> None:
    s = await onboarded_user(client, sms, 61)
    code = await _code(session, s)
    now = _at(FIXED_WINDOW)

    def manual(window: int) -> str:
        return _qr.manual_code(code.short_id, code.key_version, window)

    for window in (FIXED_WINDOW, FIXED_WINDOW - 1):
        resolved = await service.resolve_manual_code(
            session, code.short_id, manual(window), now=now
        )
        assert resolved.user_id == s.user_id
        assert resolved.window == window
    accepted = {manual(FIXED_WINDOW), manual(FIXED_WINDOW - 1)}
    for window in (FIXED_WINDOW - 2, FIXED_WINDOW - 3, FIXED_WINDOW + 1):
        if manual(window) in accepted:  # 1-in-a-million collision between windows
            continue
        with pytest.raises(NotFound) as exc:
            await service.resolve_manual_code(session, code.short_id, manual(window), now=now)
        assert exc.value.code == "doer_code_invalid"


async def test_qr_response_manual_code_has_same_window_as_token(
    client: AsyncClient, session: AsyncSession, sms: ConsoleSmsProvider
) -> None:
    s = await onboarded_user(client, sms, 62)
    qr = await service.qr_code(session, await _user(session, s), now=_at(FIXED_WINDOW))
    code = await _code(session, s)
    assert qr.manual_code == _qr.manual_code(code.short_id, code.key_version, FIXED_WINDOW)
    assert qr.token.split(".")[1] == str(FIXED_WINDOW)


def test_manual_code_is_six_digits_and_domain_separated() -> None:
    short_id = "10K3M9Q"
    for window in range(FIXED_WINDOW, FIXED_WINDOW + 200):
        manual = _qr.manual_code(short_id, 1, window)
        assert len(manual) == 6 and manual.isdigit()
        token = _qr.make_token(short_id, 1, window)
        signature = token.split(".")[2]
        # Not derived from the token signature (or the full "qr" HMAC behind it).
        full = keyed_hash("qr", short_id, "1", str(window))
        assert full.startswith(signature)
        assert manual != f"{int(full[:12], 16) % 1_000_000:06d}"
        assert manual not in token
    assert _qr.manual_code(short_id, 1, FIXED_WINDOW) == _qr.manual_code(short_id, 1, FIXED_WINDOW)


async def test_doer_code_invalid_is_identical_for_unknown_id_and_wrong_code(
    client: AsyncClient, session: AsyncSession, sms: ConsoleSmsProvider
) -> None:
    s = await onboarded_user(client, sms, 63)
    code = await _code(session, s)
    now = _at(FIXED_WINDOW)
    right = _qr.manual_code(code.short_id, code.key_version, FIXED_WINDOW)
    wrong = f"{(int(right) + 1) % 1_000_000:06d}"
    unknown_id = "ZZZZZZZ" if code.short_id != "ZZZZZZZ" else "YYYYYYY"

    errors = []
    for raw_id, typed in (
        (unknown_id, right),  # unknown ID
        (code.short_id, wrong),  # wrong code
        ("not-an-id!", right),  # malformed ID
        (code.short_id, "12345"),  # malformed code
        (code.short_id, ""),
        (code.short_id, "\u0967\u0968\u0969\u096a\u096b\u096c"),  # Devanagari digits
    ):
        with pytest.raises(NotFound) as exc:
            await service.resolve_manual_code(session, raw_id, typed, now=now)
        errors.append((exc.value.status_code, exc.value.code, exc.value.detail))
    assert len(set(errors)) == 1
    assert errors[0][:2] == (404, "doer_code_invalid")


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
async def test_manual_code_accepts_short_id_typing_variants(
    client: AsyncClient, session: AsyncSession, sms: ConsoleSmsProvider, typed: str
) -> None:
    s = await onboarded_user(client, sms, 70)
    await _set_short_id(session, s, "10K3M9Q")
    code = await _code(session, s)
    manual = _qr.manual_code("10K3M9Q", code.key_version, FIXED_WINDOW)
    now = _at(FIXED_WINDOW)
    resolved = await service.resolve_manual_code(session, typed, f" {manual} ", now=now)
    assert resolved.user_id == s.user_id
    assert resolved.short_id == "10K3M9Q"


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
async def test_manual_code_rejects_malformed_short_id(
    client: AsyncClient, session: AsyncSession, sms: ConsoleSmsProvider, typed: str
) -> None:
    s = await onboarded_user(client, sms, 71)
    await _set_short_id(session, s, "10K3M9Q")
    code = await _code(session, s)
    manual = _qr.manual_code("10K3M9Q", code.key_version, FIXED_WINDOW)
    with pytest.raises(NotFound):
        await service.resolve_manual_code(session, typed, manual, now=_at(FIXED_WINDOW))


async def test_short_id_starting_with_gd_works_without_prefix(
    client: AsyncClient, session: AsyncSession, sms: ConsoleSmsProvider
) -> None:
    s = await onboarded_user(client, sms, 72)
    await _set_short_id(session, s, "GD12345")
    code = await _code(session, s)
    manual = _qr.manual_code("GD12345", code.key_version, FIXED_WINDOW)
    for typed in ("GD12345", "gd12345", "GD-GD12345", "gdgd12345"):
        resolved = await service.resolve_manual_code(session, typed, manual, now=_at(FIXED_WINDOW))
        assert resolved.user_id == s.user_id


def test_new_short_ids_use_unambiguous_alphabet() -> None:
    for _ in range(200):
        short_id = _qr.new_short_id()
        assert len(short_id) == _qr.SHORT_ID_LENGTH
        assert not set(short_id) & set("ILOU")
        assert _qr.normalize_short_id(short_id) == short_id


async def test_qr_requires_login(client: AsyncClient) -> None:
    assert (await client.get("/api/users/me/qr")).status_code == 401
    assert (await client.post("/api/users/me/qr/rotate")).status_code == 401


# --------------------------------------------------------------------------- public surface


def test_device_hash_is_keyed_and_purpose_separated() -> None:
    assert service.device_hash("abc") == keyed_hash("device", "abc")
    assert service.device_hash("abc") != keyed_hash("phone", "abc")
    assert service.device_hash("abc") != service.device_hash("abd")


async def test_device_hash_matches_recorded_devices(
    client: AsyncClient, session: AsyncSession, sms: ConsoleSmsProvider
) -> None:
    s = await onboarded_user(client, sms, 64)
    await service.record_device(session, s.user_id, "device-xyz")
    assert service.device_hash("device-xyz") in await service.device_hashes(session, s.user_id)


async def test_phone_hashes_lists_verified_numbers(
    client: AsyncClient, session: AsyncSession, sms: ConsoleSmsProvider
) -> None:
    s = await onboarded_user(client, sms, 65)
    other = await onboarded_user(client, sms, 66)
    assert await service.phone_hashes(session, s.user_id) == {service.phone_hash(valid_phone(65))}
    assert (
        await service.phone_hashes(session, s.user_id)
        & await service.phone_hashes(session, other.user_id)
        == set()
    )
    not_verified = await signup(client)
    assert await service.phone_hashes(session, not_verified.user_id) == set()


async def test_public_summaries_batches_handles_and_names(
    client: AsyncClient, session: AsyncSession
) -> None:
    a = await signup(client)
    b = await signup(client)
    user_a, user_b = await _user(session, a), await _user(session, b)
    missing = uuid.uuid4()

    summaries = await service.public_summaries(session, [a.user_id, b.user_id, a.user_id, missing])
    assert summaries == {
        a.user_id: UserSummary(handle=user_a.handle, display_name=user_a.display_name),
        b.user_id: UserSummary(handle=user_b.handle, display_name=user_b.display_name),
    }
    assert set(UserSummary.model_fields) == {"handle", "display_name"}
    assert await service.public_summaries(session, []) == {}
