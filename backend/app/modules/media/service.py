"""Public interface of the media module for other modules (docs/M3-DESIGN.md §3.3, §4.7).

Only this module creates GET URLs for photos (D34). URLs are signed with a stable signing time
(``url_step_minutes``) and valid for ``url_ttl_minutes``, so the same image keeps the same URL
for a whole step and browsers cache it. Callers check visibility first: ``public_photos`` only
filters the photos themselves (ready, deed photos, not taken down, not being moved).

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
from app.modules.media import _keys
from app.modules.media._limits import MediaLimits, load_limits
from app.modules.media.models import (
    ObjectMove,
    Photo,
    PhotoPurpose,
    PhotoStatus,
    PhotoTakedown,
)
from app.modules.media.schemas import PhotoOut

__all__ = [
    "PhotoOut",
    "SignedVariants",
    "admin_photo_urls",
    "admin_signed",
    "public_photos",
    "sign_variants",
]


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
