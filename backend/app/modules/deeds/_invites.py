"""The invite path (docs/M2-DESIGN.md §3.2 #3, #7, #8, #9; §2.5; D5, D6, D9).

A reporter whose doer isn't on Good Deeds (or isn't at hand) creates a ``pending`` deed with no
doer and a single-use invite link. Whoever accepts the link (a new or an existing member) becomes
the doer, and the deed is verified and grouped. Unaccepted invites expire after
``invite_expiry_hours`` (``expire_invites``, run by the ``deeds.expire_invites`` job).

Only the SHA-256 of the 256-bit raw token is stored; the raw token is returned on create and on
reissue only.

Lock order. Accept: ``reporter_month`` (the reporter, read unlocked first; D21), then the
invite row, then ``shared_device``, then the deed row (still pending, so grouping code never
touches it), then the per-doer ``same_act`` advisory lock. Withdrawal (``_edits``) takes the
invite row first. Create takes ``reporter_month`` (D16), then ``invites``.

Invites are switched off by config (D17, ``invites_enabled``): the router calls
``ensure_enabled`` on create, preview, accept and reissue. The expiry job keeps running.
"""

import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import advisory_xact_lock
from app.core.errors import Conflict, Forbidden, Gone, NotFound, TooManyRequests
from app.core.events import emit
from app.core.ratelimit import RateLimiter
from app.core.security import new_opaque_token, sha256_hex
from app.modules.deeds import _plausibility as plaus
from app.modules.deeds._areas import resolve_area_id
from app.modules.deeds._common import (
    constraint_name,
    ist_day,
    ist_month,
    lat_lng,
    point_ewkt,
    utcnow,
)
from app.modules.deeds._grouping import assign_group, upgrade_prize_rank
from app.modules.deeds._limits import DeedLimits
from app.modules.deeds._qr_deeds import validate_content
from app.modules.deeds._reporter_limit import (
    ensure_under_monthly_limits,
    pair_limit_reached,
    reported_this_month,
)
from app.modules.deeds._views import deed_out
from app.modules.deeds.events import DeedRejected, DeedSubmitted, DeedVerified
from app.modules.deeds.models import (
    Deed,
    DeedStatus,
    Invite,
    InviteStatus,
    VerificationEvent,
    VerificationEventKind,
    VerificationMethod,
)
from app.modules.deeds.schemas import DeedOut, InviteDeedCreate, InviteOut, InvitePreview, UserRef
from app.modules.identity import service as identity

# A raw token is 43 URL-safe characters; anything much longer is not worth hashing.
_MAX_TOKEN_LENGTH = 200
EXPIRE_BATCH_SIZE = 200


# --------------------------------------------------- errors


def _invite_invalid() -> NotFound:
    return NotFound("This invite link isn't valid.", code="invite_invalid")


def _invite_expired() -> Gone:
    return Gone("This invite has expired.", code="invite_expired")


def _invite_used() -> Gone:
    return Gone("This invite has already been accepted.", code="invite_used")


def _invite_cancelled() -> Gone:
    return Gone("The person who sent this invite withdrew it.", code="invite_cancelled")


def _invite_not_found() -> NotFound:
    return NotFound("We couldn't find that invite.", code="invite_not_found")


# --------------------------------------------------- helpers


def invite_url(raw_token: str) -> str:
    return f"{get_settings().web_base_url.rstrip('/')}/invite/{raw_token}"


def invite_out(invite: Invite, raw_token: str | None = None) -> InviteOut:
    return InviteOut(
        id=invite.id,
        deed_id=invite.deed_id,
        status=invite.status,
        sent_via=invite.sent_via,
        created_at=invite.created_at,
        expires_at=invite.expires_at,
        accepted_at=invite.accepted_at,
        url=invite_url(raw_token) if raw_token is not None else None,
    )


async def invite_for_deed(session: AsyncSession, deed_id: uuid.UUID) -> InviteOut | None:
    """The deed's invite without its link (the raw token is never stored)."""
    invite = await session.scalar(select(Invite).where(Invite.deed_id == deed_id))
    return None if invite is None else invite_out(invite)


def _hash_token(raw: str) -> str | None:
    return None if not raw or len(raw) > _MAX_TOKEN_LENGTH else sha256_hex(raw)


def _ensure_usable(invite: Invite, now: datetime) -> None:
    """Pending and not past its expiry (the job may not have run yet)."""
    if invite.status == InviteStatus.ACCEPTED:
        raise _invite_used()
    if invite.status == InviteStatus.CANCELLED:
        raise _invite_cancelled()
    if invite.status == InviteStatus.EXPIRED or invite.expires_at <= now:
        raise _invite_expired()


def ensure_enabled(limits: DeedLimits) -> None:
    """D17: invites are switched off by config. Checked after the rate limits and before any
    lookup on create, preview, accept and reissue."""
    if not limits.invites_enabled:
        raise Forbidden(
            "Invites are switched off for now. Ask the person to show you their QR code "
            "or ID and code instead.",
            code="invites_disabled",
        )


# --------------------------------------------------- rate limits


async def limit_create(limiter: RateLimiter, limits: DeedLimits, ip: str) -> None:
    """The account side of invite creation is the daily count (``invites_per_day``)."""
    await limiter.hit(f"invite:create:ip:{ip}", limits.rate_invite_create_ip)


async def limit_preview(limiter: RateLimiter, limits: DeedLimits, ip: str) -> None:
    await limiter.hit(f"invite:preview:ip:{ip}", limits.rate_invite_preview_ip)


async def limit_accept(
    limiter: RateLimiter, limits: DeedLimits, user_id: uuid.UUID, ip: str
) -> None:
    await limiter.hit(f"invite:accept:u:{user_id}", limits.rate_invite_accept_user)
    await limiter.hit(f"invite:accept:ip:{ip}", limits.rate_invite_accept_ip)


async def limit_reissue(
    limiter: RateLimiter, limits: DeedLimits, user_id: uuid.UUID, ip: str
) -> None:
    await limiter.hit(f"invite:reissue:u:{user_id}", limits.rate_invite_reissue_user)
    await limiter.hit(f"invite:reissue:ip:{ip}", limits.rate_invite_reissue_ip)


# --------------------------------------------------- create (§3.2 #3, invite request)


async def _count_today(session: AsyncSession, reporter_id: uuid.UUID, now: datetime) -> int:
    """Every invite created today counts, whatever its status now."""
    stmt = select(func.count()).where(
        Invite.reporter_user_id == reporter_id, Invite.created_day == ist_day(now)
    )
    return int(await session.scalar(stmt) or 0)


async def create_invite_deed(
    session: AsyncSession,
    reporter_id: uuid.UUID,
    data: InviteDeedCreate,
    *,
    limits: DeedLimits,
    limits_version: int,
    now: datetime | None = None,
) -> DeedOut:
    now = now or utcnow()
    description = validate_content(
        limits, data.category, data.description, data.beneficiaries_count
    )
    # D5: up to 7 days ago, no 2-hour limit.
    plaus.ensure_occurred_at(
        data.occurred_at, *plaus.invite_occurred_range(now, limits.invite_occurred_max_age_days)
    )
    location: str | None = None
    if data.location is not None:
        # Where the deed happened, not where the reporter is moving: no travel check (D5).
        loc = data.location
        plaus.ensure_accuracy(loc.accuracy_m, limits.max_location_accuracy_m)
        centre = plaus.Point(lat=limits.service_area.lat, lng=limits.service_area.lng)
        plaus.ensure_in_service_area(
            plaus.Point(lat=loc.lat, lng=loc.lng), centre, limits.service_area.radius_km
        )
        location = point_ewkt(loc.lat, loc.lng)

    # D16/D19: the total monthly limit across both paths (takes the ``reporter_month`` lock
    # first). The doer is unknown until acceptance, so only the total is checked here (D21).
    await ensure_under_monthly_limits(session, reporter_id, None, limits, now)
    # Serialise this reporter's creates so concurrent requests can't all pass the count.
    await advisory_xact_lock(session, "invites", str(reporter_id))
    if await _count_today(session, reporter_id, now) >= limits.invites_per_day:
        raise TooManyRequests(
            f"You can send {limits.invites_per_day} invites a day. Try again tomorrow.",
            code="invite_daily_limit",
        )

    deed = Deed(
        reporter_user_id=reporter_id,
        doer_user_id=None,
        category=data.category,
        description=description,
        beneficiaries_count=data.beneficiaries_count,
        location=location,
        location_accuracy_m=data.location.accuracy_m if data.location is not None else None,
        occurred_at=data.occurred_at,
        reported_at=now,
        report_day=ist_day(now),
        status=DeedStatus.PENDING,
        verification_method=VerificationMethod.INVITE_ACCEPT,
        limits_version=limits_version,
    )
    session.add(deed)
    await session.flush()
    raw = new_opaque_token()
    invite = Invite(
        deed_id=deed.id,
        reporter_user_id=reporter_id,
        token_hash=sha256_hex(raw),
        sent_via=data.sent_via,
        status=InviteStatus.PENDING,
        created_at=now,
        created_day=ist_day(now),
        expires_at=now + timedelta(hours=limits.invite_expiry_hours),
    )
    session.add(invite)
    emit(
        session,
        DeedSubmitted(
            deed_id=deed.id,
            reporter_id=reporter_id,
            doer_id=None,
            method="invite_accept",
            status="pending",
            category=deed.category,
            occurred_at=deed.occurred_at,
        ),
    )
    await session.flush()
    return await deed_out(session, deed, reporter_id, invite=invite_out(invite, raw))


# --------------------------------------------------- preview (§3.2 #8)


async def preview(
    session: AsyncSession, raw_token: str, now: datetime | None = None
) -> InvitePreview:
    """Public. Deed content is shown only for a usable invite; never the doer or a location."""
    now = now or utcnow()
    token_hash = _hash_token(raw_token)
    invite = (
        None
        if token_hash is None
        else await session.scalar(select(Invite).where(Invite.token_hash == token_hash))
    )
    if invite is None:
        raise _invite_invalid()
    _ensure_usable(invite, now)
    deed = await session.get(Deed, invite.deed_id)
    assert deed is not None
    reporter = (await identity.public_summaries(session, [invite.reporter_user_id])).get(
        invite.reporter_user_id
    )
    if reporter is None:
        raise _invite_invalid()
    return InvitePreview(
        status="pending",
        reporter=UserRef(handle=reporter.handle, display_name=reporter.display_name),
        category=deed.category,
        description=deed.description,
        occurred_at=deed.occurred_at,
        expires_at=invite.expires_at,
    )


# --------------------------------------------------- accept (§3.2 #9)


async def _shared_device_accepts(
    session: AsyncSession, reporter_id: uuid.UUID, accepter_id: uuid.UUID, now: datetime
) -> int:
    start, end = ist_month(now)
    stmt = select(func.count()).where(
        Invite.reporter_user_id == reporter_id,
        Invite.accepted_by == accepter_id,
        Invite.shared_device.is_(True),
        Invite.status == InviteStatus.ACCEPTED,
        Invite.accepted_at >= start,
        Invite.accepted_at < end,
    )
    return int(await session.scalar(stmt) or 0)


async def _invite_reporter(session: AsyncSession, raw_token: str) -> uuid.UUID | None:
    """Step 0 (D21): the invite's reporter, read without a lock so ``reporter_month`` can be
    taken before the invite row, as on every other path that creates or verifies a deed."""
    token_hash = _hash_token(raw_token)
    if token_hash is None:
        return None
    return await session.scalar(
        select(Invite.reporter_user_id).where(Invite.token_hash == token_hash)
    )


async def _lock_invite(session: AsyncSession, raw_token: str) -> Invite | None:
    token_hash = _hash_token(raw_token)
    if token_hash is None:
        return None
    stmt = (
        select(Invite)
        .where(Invite.token_hash == token_hash)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return (await session.scalars(stmt)).first()


async def _deed_location(session: AsyncSession, deed: Deed) -> str | None:
    if deed.location is None:
        return None
    lat, lng = lat_lng(Deed.location)
    row = (await session.execute(select(lat, lng).where(Deed.id == deed.id))).one()
    return point_ewkt(row[0], row[1])


async def accept(
    session: AsyncSession,
    raw_token: str,
    *,
    accepter_id: uuid.UUID,
    accepter_phone_e164: str | None,
    device_id: str | None,
    limits: DeedLimits,
    limits_version: int,
    now: datetime | None = None,
) -> DeedOut:
    """Accept an invite as its doer. Rate limits are the caller's job (they come first).

    Lock order: ``reporter_month`` (reporter) -> invite row -> ``shared_device`` -> pending deed
    row -> ``same_act`` (doer)."""
    now = now or utcnow()
    # 0. Unlocked reporter read, then the reporter's ``reporter_month`` lock (D21), so this
    # path serialises with the reporter's own submits and the pair count below can't race.
    reporter_id = await _invite_reporter(session, raw_token)
    if reporter_id is None:
        raise _invite_invalid()
    await advisory_xact_lock(session, "reporter_month", str(reporter_id))
    # 1. Row lock: concurrent accepts (and withdrawal) serialise on the invite. A reissue between
    # step 0 and here rotates the token, so the lookup finds nothing (404).
    invite = await _lock_invite(session, raw_token)
    if invite is None:
        raise _invite_invalid()
    # An invite's reporter never changes, so this can't differ; re-checked anyway so the lock
    # taken above is always the locked invite's reporter's (§8).
    if invite.reporter_user_id != reporter_id:
        raise _invite_invalid()
    # 2.
    _ensure_usable(invite, now)
    # 2b. A suspended or blocked reporter's invites can't be accepted. Same 404 as an unknown
    # token, so the response reveals nothing about the reporter's account (security L8).
    if not await identity.is_active(session, reporter_id):
        raise _invite_invalid()
    # 3.
    if accepter_id == reporter_id:
        raise Forbidden("You can't accept your own invite.", code="cannot_accept_own_invite")
    # 3b. Pair monthly limit (D21) for the month the report already counts in (its
    # ``reported_at``), not the month of acceptance. The pending deed has no doer yet, so it
    # isn't in the count. Refused: the invite stays pending (and later expires).
    reported_at = await session.scalar(select(Deed.reported_at).where(Deed.id == invite.deed_id))
    if reported_at is None:  # every invite has its deed (FK); never reached
        raise _invite_invalid()
    about_accepter = await reported_this_month(
        session, reporter_id, reported_at, doer_id=accepter_id
    )
    if about_accepter >= limits.reporter_doer_monthly_limit:
        raise pair_limit_reached(limits.reporter_doer_monthly_limit)
    # 4. Defense in depth: phone_history already makes a number belong to one account ever.
    if await identity.phone_hashes(session, accepter_id) & await identity.phone_hashes(
        session, reporter_id
    ):
        raise Forbidden("This invite can't be accepted from this account.", code="same_phone")
    # 5. Shared device (D6): allowed, at most N accepted invites per pair per IST month.
    accepter_devices = await identity.device_hashes(session, accepter_id)
    if device_id:
        accepter_devices.add(identity.device_hash(device_id))
    shared_device = bool(accepter_devices & await identity.device_hashes(session, reporter_id))
    if shared_device:
        await advisory_xact_lock(session, "shared_device", str(reporter_id), str(accepter_id))
        used = await _shared_device_accepts(session, reporter_id, accepter_id, now)
        if used >= limits.shared_device_pair_monthly_limit:
            raise Forbidden(
                "Too many invites between these accounts on a shared device this month.",
                code="same_device_limit",
            )

    deed = (
        await session.scalars(
            select(Deed)
            .where(Deed.id == invite.deed_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).one()
    if deed.status != DeedStatus.PENDING:  # the invite status should have caught this
        raise _invite_cancelled() if deed.status == DeedStatus.WITHDRAWN else _invite_expired()

    # 6. Verify and group (§3.4); the area (D39) is resolved from the deed's point at
    # acceptance (a failed lookup leaves it null).
    location = await _deed_location(session, deed)
    area_id = await resolve_area_id(session, location, limits.area_fallback_m)
    assignment = await assign_group(
        session,
        doer_id=accepter_id,
        reporter_id=reporter_id,
        location=location,
        # L7-A: group by when the report was made (server time), not the reporter's
        # backdatable "When?" and not the acceptance time, which can be days later.
        grouped_at=deed.reported_at,
        limits=limits,
        limits_version=limits_version,
        exclude_deed_id=deed.id,
    )
    deed.doer_user_id = accepter_id
    deed.status = DeedStatus.VERIFIED
    deed.verified_at = now
    deed.same_act_group_id = assignment.group.id
    deed.counts_for_points = assignment.counts_for_points
    deed.prize_rank_eligible = False
    deed.area_id = area_id
    try:
        async with session.begin_nested():
            await session.flush()
    except IntegrityError as exc:
        if constraint_name(exc) == "uq_deeds_pair_day":
            raise Conflict(
                "They were already recognised by this person on the day this was reported.",
                code="already_recognized_today",
            ) from exc
        raise

    # 7.
    invite.status = InviteStatus.ACCEPTED
    invite.accepted_by = accepter_id
    invite.accepted_at = now
    invite.shared_device = shared_device
    invite.accepted_phone_hash = (
        identity.phone_hash(accepter_phone_e164) if accepter_phone_e164 else None
    )
    # 8. Evidence: figures only, no coordinates.
    session.add(
        VerificationEvent(
            deed_id=deed.id,
            kind=VerificationEventKind.INVITE_ACCEPTED,
            actor_user_id=accepter_id,
            evidence={
                "shared_device": shared_device,
                "has_location": deed.location is not None,
                "hours_to_accept": round((now - invite.created_at).total_seconds() / 3600, 1),
            },
        )
    )
    # 9. Not prize-rank eligible until a second independent report of the same act.
    emit(
        session,
        DeedVerified(
            deed_id=deed.id,
            reporter_id=reporter_id,
            doer_id=accepter_id,
            method="invite_accept",
            verified_at=now,
            same_act_group_id=assignment.group.id,
            counts_for_points=assignment.counts_for_points,
            prize_rank_eligible=False,
            limits_version=limits_version,
        ),
    )
    await session.flush()
    await upgrade_prize_rank(session, assignment.group)
    return await deed_out(session, deed, accepter_id, group=assignment.group)


# --------------------------------------------------- reissue (§3.2 #7)


async def reissue(
    session: AsyncSession,
    reporter_id: uuid.UUID,
    invite_id: uuid.UUID,
    now: datetime | None = None,
) -> InviteOut:
    """A fresh link for a pending invite. ``expires_at`` and the daily count don't change, and
    the old link stops working."""
    now = now or utcnow()
    invite = (
        await session.scalars(
            select(Invite)
            .where(Invite.id == invite_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).first()
    if invite is None or invite.reporter_user_id != reporter_id:
        raise _invite_not_found()
    if invite.status != InviteStatus.PENDING or invite.expires_at <= now:
        raise Conflict("Only a pending invite can get a new link.", code="invite_not_pending")
    raw = new_opaque_token()
    invite.token_hash = sha256_hex(raw)
    invite.reissued_at = now
    await session.flush()
    return invite_out(invite, raw)


# --------------------------------------------------- expiry job


async def _due_invites(session: AsyncSession, now: datetime, batch_size: int) -> list[Invite]:
    stmt = (
        select(Invite)
        .where(Invite.status == InviteStatus.PENDING, Invite.expires_at <= now)
        .order_by(Invite.expires_at, Invite.id)
        .limit(batch_size)
        .with_for_update(skip_locked=True)
        .execution_options(populate_existing=True)
    )
    return list(await session.scalars(stmt))


async def expire_invites(
    session: AsyncSession, now: datetime | None = None, batch_size: int = EXPIRE_BATCH_SIZE
) -> int:
    """Expire one batch of pending invites past ``expires_at``: the invite becomes ``expired``
    and its deed ``unverified``, with a ``verification_events`` row and ``DeedRejected``.

    ``SKIP LOCKED`` lets concurrent runs (and in-flight accepts or withdrawals, which hold the
    invite row) proceed without waiting; a skipped invite is picked up by a later run. Returns
    the number expired; the caller commits and repeats until it is below ``batch_size``.
    """
    now = now or utcnow()
    invites = await _due_invites(session, now, batch_size)
    for invite in invites:
        invite.status = InviteStatus.EXPIRED
        deed = (
            await session.scalars(
                select(Deed)
                .where(Deed.id == invite.deed_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).one()
        if deed.status != DeedStatus.PENDING:
            continue
        deed.status = DeedStatus.UNVERIFIED
        evidence: dict[str, Any] = {"expires_at": invite.expires_at.isoformat()}
        session.add(
            VerificationEvent(
                deed_id=deed.id,
                kind=VerificationEventKind.INVITE_EXPIRED,
                actor_user_id=None,
                evidence=evidence,
            )
        )
        emit(
            session,
            DeedRejected(
                deed_id=deed.id,
                reporter_id=deed.reporter_user_id,
                doer_id=None,
                new_status="unverified",
                reason="invite_expired",
            ),
        )
    await session.flush()
    return len(invites)
