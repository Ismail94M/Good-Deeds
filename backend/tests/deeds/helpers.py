"""Shared helpers for deeds tests (typed, so mypy strict covers them).

Members are created directly in the database (fully onboarded, with a doer code), which is much
faster than signing up through the API with argon2 and OTPs.
"""

import math
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import OutboxEvent
from app.core.runtime_config import load, publish
from app.core.security import create_access_token
from app.modules.deeds import _qr_deeds, _scans
from app.modules.deeds._limits import DeedLimits, load_limits
from app.modules.deeds.schemas import DeedOut, Location, QrDeedCreate, ScanCreate, ScanOut
from app.modules.identity import _qr, service
from app.modules.identity.models import Consent, DoerCode, PhoneHistory, User

# The seeded service-area centre (Bangalore).
CENTRE = (12.9716, 77.5946)
# Metres per degree of latitude near Bangalore on the WGS84 spheroid.
_M_PER_DEG_LAT = 111_132.92 - 559.82 * math.cos(math.radians(2 * CENTRE[0]))


@dataclass(frozen=True)
class Member:
    user_id: uuid.UUID
    handle: str
    display_name: str
    short_id: str
    key_version: int
    phone_hash: str
    access: str

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access}"}


async def make_member(
    session: AsyncSession,
    *,
    onboarded: bool = True,
    commit: bool = True,
    phone_history: bool = True,
) -> Member:
    now = datetime.now(UTC)
    phone = "+91" + "9" + "".join(secrets.choice("0123456789") for _ in range(9))
    user = User(
        display_name="Deed Tester",
        handle=f"dt{uuid.uuid4().hex[:12]}",
        token_version=0,
        adult_declared_at=now if onboarded else None,
        phone_e164=phone if onboarded else None,
        phone_verified_at=now if onboarded else None,
    )
    session.add(user)
    await session.flush()
    if onboarded:
        session.add_all(
            Consent(user_id=user.id, consent_type=t, version=v)
            for t, v in service.CURRENT_CONSENTS.items()
        )
        if phone_history:  # append-only, so tests that really commit leave it out
            session.add(PhoneHistory(phone_hash=service.phone_hash(phone), user_id=user.id))
    code = DoerCode(user_id=user.id, short_id=_qr.new_short_id(), key_version=1)
    session.add(code)
    await session.flush()
    if commit:
        await session.commit()
    access, _ = create_access_token(user.id, "member", 0)
    return Member(
        user_id=user.id,
        handle=user.handle,
        display_name=user.display_name,
        short_id=code.short_id,
        key_version=1,
        phone_hash=service.phone_hash(phone),
        access=access,
    )


async def members(session: AsyncSession, n: int) -> list[Member]:
    return [await make_member(session) for _ in range(n)]


# --------------------------------------------------- QR tokens and codes


async def qr_window_seconds(session: AsyncSession) -> int:
    _, config = await load(session, _qr.QrConfig)
    return config.window_seconds


async def token_for(session: AsyncSession, doer: Member, at: datetime | None = None) -> str:
    now = (at or datetime.now(UTC)).timestamp()
    window = _qr.current_window(await qr_window_seconds(session), now)
    return _qr.make_token(doer.short_id, doer.key_version, window)


async def code_for(session: AsyncSession, doer: Member, at: datetime | None = None) -> str:
    now = (at or datetime.now(UTC)).timestamp()
    window = _qr.current_window(await qr_window_seconds(session), now)
    return _qr.manual_code(doer.short_id, doer.key_version, window)


# --------------------------------------------------- locations


def offset(
    north_m: float = 0.0, east_m: float = 0.0, base: tuple[float, float] = CENTRE
) -> tuple[float, float]:
    lat = base[0] + north_m / _M_PER_DEG_LAT
    lng = base[1] + east_m / (_M_PER_DEG_LAT * math.cos(math.radians(base[0])))
    return lat, lng


def location(
    north_m: float = 0.0,
    east_m: float = 0.0,
    accuracy_m: int = 20,
    base: tuple[float, float] = CENTRE,
) -> dict[str, Any]:
    lat, lng = offset(north_m, east_m, base)
    return {"lat": lat, "lng": lng, "accuracy_m": accuracy_m}


# --------------------------------------------------- config


async def publish_limits(session: AsyncSession, **changes: Any) -> DeedLimits:
    _, current = await load_limits(session)
    new = current.model_copy(update=changes)
    await publish(session, new, actor_id=None, reason="test")
    await session.commit()
    return new


# --------------------------------------------------- service-level shortcuts (time control)


async def scan(
    session: AsyncSession,
    witness: Member,
    doer: Member,
    *,
    at: datetime | None = None,
    north_m: float = 0.0,
    east_m: float = 0.0,
    accuracy_m: int = 20,
    manual: bool = False,
    minted_at: datetime | None = None,
) -> ScanOut:
    """Scan at server time ``at`` with a token (or code) minted at ``minted_at`` (default:
    ``at``)."""
    at = at or datetime.now(UTC)
    minted = minted_at or at
    version, limits = await load_limits(session)
    loc = Location.model_validate(location(north_m, east_m, accuracy_m))
    data = (
        ScanCreate(short_id=doer.short_id, code=await code_for(session, doer, minted), location=loc)
        if manual
        else ScanCreate(token=await token_for(session, doer, minted), location=loc)
    )
    return await _scans.create_scan(
        session,
        witness.user_id,
        data,
        limits=limits,
        limits_version=version,
        device_id=None,
        ip="198.51.100.1",
        now=at,
    )


async def report(
    session: AsyncSession,
    witness: Member,
    scan_id: uuid.UUID,
    *,
    at: datetime | None = None,
    occurred_at: datetime | None = None,
    category: str = "animals",
) -> DeedOut:
    version, limits = await load_limits(session)
    data = QrDeedCreate(
        method="qr_scan",
        scan_id=scan_id,
        category=category,
        description="Helped a stray dog across the road",
        occurred_at=occurred_at,
    )
    return await _qr_deeds.create_qr_deed(
        session, witness.user_id, data, limits=limits, limits_version=version, now=at
    )


async def scan_and_report(
    session: AsyncSession,
    witness: Member,
    doer: Member,
    *,
    at: datetime | None = None,
    north_m: float = 0.0,
    east_m: float = 0.0,
    occurred_at: datetime | None = None,
) -> DeedOut:
    s = await scan(session, witness, doer, at=at, north_m=north_m, east_m=east_m)
    return await report(session, witness, s.scan_id, at=at, occurred_at=occurred_at)


def deed_body(scan_id: str | uuid.UUID, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "method": "qr_scan",
        "scan_id": str(scan_id),
        "category": "animals",
        "description": "Helped a stray dog across the road",
        "beneficiaries_count": 1,
    }
    body.update(overrides)
    return body


async def outbox(session: AsyncSession, event_type: str, deed_id: uuid.UUID) -> list[OutboxEvent]:
    rows = await session.scalars(
        select(OutboxEvent).where(
            OutboxEvent.event_type == event_type,
            OutboxEvent.payload["deed_id"].astext == str(deed_id),
        )
    )
    return list(rows)


async def count(session: AsyncSession, model: Any, *where: Any) -> int:
    return int(await session.scalar(select(func.count()).select_from(model).where(*where)) or 0)
