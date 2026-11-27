"""Public interface of the media module for other modules (docs/M3-DESIGN.md §3.3, §4.7).

Only this module creates GET URLs for photos (D34). URLs are signed with a stable signing time
(``url_step_minutes``) and valid for ``url_ttl_minutes``, so the same image keeps the same URL
for a whole step and browsers cache it. Callers check visibility first: ``public_photos`` only
filters the photos themselves (ready, deed photos, not taken down, not being moved).

Profile pictures (M6A, D117): other modules hold only the opaque ``avatar_ref`` they get from
``identity.public_summaries``; ``avatar_urls`` turns references into signed URLs, and signs only
the owner's live picture on an active account.

This file and media's private files never import ``feed`` (D30; tests/test_architecture.py).
"""

import uuid
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.storage import ObjectStorage, get_storage, stable_signing_time
from app.modules.identity import service as identity
from app.modules.media import _keys
from app.modules.media._limits import MediaLimits, load_limits
from app.modules.media.events import AvatarChanged, AvatarModerationDecided, DeedPhotoReady
from app.modules.media.models import (
    ObjectMove,
    Photo,
    PhotoPurpose,
    PhotoStatus,
    PhotoTakedown,
)
from app.modules.media.schemas import AvatarUrls, PhotoOut

__all__ = [
    "AvatarChanged",  # identity stores the reference (M6A §4.4)
    "AvatarModerationDecided",  # notifications tells the owner (Q95)
    "AvatarUrls",
    "DeedPhotoReady",  # the event scoring subscribes to (M4 §5, D47)
    "PhotoOut",
    "SignedVariants",
    "admin_photo_urls",
    "admin_signed",
    "avatar_urls",
    "public_photos",
    "sign_avatar",
    "sign_variants",
]

AVATAR_SM, AVATAR_MD, AVATAR_LG = "avatar_sm", "avatar_md", "avatar_lg"


@dataclass(frozen=True)
class SignedVariants:
    thumb_url: str
    display_url: str
    expires_at: datetime


def sign_variants(
    storage: ObjectStorage,
    limits: MediaLimits,
    variant_keys: dict[str, str],
    *,
    now: datetime | None = None,
) -> SignedVariants:
    """Stable signed GET URLs for a photo's ``thumb`` and ``display`` keys (D34)."""
    signed_at = stable_signing_time(now or datetime.now(UTC), limits.url_step_minutes)
    expires_s = limits.url_ttl_minutes * 60

    def sign(key: str) -> str:
        return storage.presign_get(key, signed_at=signed_at, expires_s=expires_s)

    return SignedVariants(
        thumb_url=sign(variant_keys["thumb"]),
        display_url=sign(variant_keys["display"]),
        expires_at=signed_at + timedelta(seconds=expires_s),
    )


def _public_photo(
    photo: Photo, storage: ObjectStorage, limits: MediaLimits, now: datetime | None
) -> PhotoOut:
    signed = sign_variants(storage, limits, _keys.variant_keys_of(photo.variant_keys), now=now)
    assert photo.width is not None and photo.height is not None  # ck_photos_ready_has_variants
    return PhotoOut(
        id=photo.id,
        thumb_url=signed.thumb_url,
        display_url=signed.display_url,
        width=photo.width,
        height=photo.height,
        urls_expire_at=signed.expires_at,
    )


async def public_photos(
    session: AsyncSession,
    deed_ids: Iterable[uuid.UUID],
    *,
    storage: ObjectStorage | None = None,
    now: datetime | None = None,
) -> dict[uuid.UUID, list[PhotoOut]]:
    """The photos viewers may see on each deed, in carousel order, with signed URLs. Every
    requested ID is in the result (an empty list when it has none). One query.

    Only ``ready`` deed photos with ``takedown = 'none'`` and no pending object move: a photo
    removed by its uploader, the doer or an admin is simply absent (Q33). The caller must have
    checked that the deeds themselves are viewable."""
    ids = list(dict.fromkeys(deed_ids))
    result: dict[uuid.UUID, list[PhotoOut]] = {deed_id: [] for deed_id in ids}
    if not ids:
        return result
    _, limits = await load_limits(session)
    store = storage if storage is not None else get_storage()
    rows = await session.scalars(
        select(Photo)
        .where(
            Photo.deed_id.in_(ids),
            Photo.purpose == PhotoPurpose.DEED,
            Photo.status == PhotoStatus.READY,
            Photo.takedown == PhotoTakedown.NONE,
            Photo.object_move.is_(None),
        )
        .order_by(Photo.deed_id, Photo.position, Photo.id)
    )
    grouped: dict[uuid.UUID, list[PhotoOut]] = defaultdict(list)
    for photo in rows:
        assert photo.deed_id is not None
        grouped[photo.deed_id].append(_public_photo(photo, store, limits, now))
    result.update(grouped)
    return result


def admin_signed(
    photo: Photo, storage: ObjectStorage, limits: MediaLimits, *, now: datetime | None = None
) -> SignedVariants | None:
    """Admin-only URLs for a photo row: the live keys, or the ``held/`` copies once an admin
    removal has moved them. None when no variants are stored (never processed, deleted or
    purged)."""
    if photo.status != PhotoStatus.READY or photo.objects_purged_at is not None:
        return None
    keys = _keys.variant_keys_of(photo.variant_keys)
    if not keys:
        return None
    held = (
        photo.takedown == PhotoTakedown.ADMIN_REMOVED and photo.object_move != ObjectMove.HOLD
    ) or photo.object_move == ObjectMove.RELEASE  # a restore still copying back
    if held:
        keys = {name: _keys.held_key(key) for name, key in keys.items()}
    if photo.purpose == PhotoPurpose.AVATAR:
        keys = {"thumb": keys[AVATAR_MD], "display": keys[AVATAR_LG]}
    return sign_variants(storage, limits, keys, now=now)


async def admin_photo_urls(
    session: AsyncSession,
    photo_id: uuid.UUID,
    *,
    storage: ObjectStorage | None = None,
    now: datetime | None = None,
) -> SignedVariants | None:
    """Signed URLs for the admin review page only (§3.3 #12); see ``admin_signed``. Never call
    this from a non-admin route."""
    photo = await session.get(Photo, photo_id)
    if photo is None:
        return None
    _, limits = await load_limits(session)
    return admin_signed(photo, storage if storage is not None else get_storage(), limits, now=now)


# --------------------------------------------------- profile pictures (M6A §3.3 P5, D117, D118)


def sign_avatar(
    storage: ObjectStorage,
    limits: MediaLimits,
    variant_keys: dict[str, str],
    *,
    now: datetime | None = None,
) -> AvatarUrls:
    """Stable signed GET URLs for a profile picture's three sizes, with the avatar TTL and step
    (D118: the same URL for 12 h, valid for 24 h)."""
    signed_at = stable_signing_time(now or datetime.now(UTC), limits.avatar_url_step_minutes)
    expires_s = limits.avatar_url_ttl_minutes * 60

    def sign(name: str) -> str:
        return storage.presign_get(variant_keys[name], signed_at=signed_at, expires_s=expires_s)

    return AvatarUrls(
        sm_url=sign(AVATAR_SM),
        md_url=sign(AVATAR_MD),
        lg_url=sign(AVATAR_LG),
        expires_at=signed_at + timedelta(seconds=expires_s),
    )


def live_avatar_version(photo: Photo) -> int | None:
    """The version of a profile picture that may be shown: ready, not taken down, no move
    pending, objects still stored. None otherwise."""
    if (
        photo.purpose != PhotoPurpose.AVATAR
        or photo.status != PhotoStatus.READY
        or photo.takedown != PhotoTakedown.NONE
        or photo.object_move is not None
        or photo.objects_purged_at is not None
    ):
        return None
    keys = _keys.variant_keys_of(photo.variant_keys)
    if AVATAR_MD not in keys:
        return None
    return _keys.key_version(keys[AVATAR_MD])


async def avatar_urls(
    session: AsyncSession,
    refs: Iterable[str],
    *,
    storage: ObjectStorage | None = None,
    limits: MediaLimits | None = None,
    now: datetime | None = None,
) -> dict[str, AvatarUrls | None]:
    """Signed URLs for each reference, in one query (plus one for account states). A reference
    gets None when it is malformed, isn't its owner's live picture any more (replaced, removed,
    restored under a new version) or its owner's account isn't active (D117, D122). Every
    requested reference is in the result. The caller checks the viewer is signed in (D119)."""
    wanted = list(dict.fromkeys(refs))
    result: dict[str, AvatarUrls | None] = dict.fromkeys(wanted)
    parsed = {ref: p for ref in wanted if (p := _keys.parse_avatar_ref(ref)) is not None}
    if not parsed:
        return result
    if limits is None:
        _, limits = await load_limits(session)
    store = storage if storage is not None else get_storage()
    rows = {
        photo.id: photo
        for photo in await session.scalars(
            select(Photo).where(
                Photo.id.in_({photo_id for photo_id, _ in parsed.values()}),
                Photo.purpose == PhotoPurpose.AVATAR,
                Photo.status == PhotoStatus.READY,
                Photo.takedown == PhotoTakedown.NONE,
                Photo.object_move.is_(None),
            )
        )
    }
    active = await identity.active_user_ids(session, {p.uploader_user_id for p in rows.values()})
    for ref, (photo_id, version) in parsed.items():
        photo = rows.get(photo_id)
        if photo is None or photo.uploader_user_id not in active:
            continue
        if live_avatar_version(photo) != version:
            continue
        result[ref] = sign_avatar(store, limits, _keys.variant_keys_of(photo.variant_keys), now=now)
    return result
