"""Object moves after an admin removal or restore (§3.3 #13, #14, §4.7).

- ``hold`` (admin removal): every variant moves from ``photos/{id}/…`` to ``held/{id}/…``, so
  every URL already issued returns 404 at once while the photo stays restorable.
- ``release`` (restore): every variant moves back under ``photos/{id}/`` with a **new version
  suffix**, so links from before the removal never work again; ``variant_keys`` then points at
  the new keys and the photo is listed again (``public_photos`` skips rows with a move pending).

Each move runs with the photo row locked, so a removal and a restore of the same photo never
interleave, and every step is safe to repeat: a request runs it right after its commit, and
``media.move_objects`` retries anything left (every minute).
"""

import logging
import re
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.storage import ObjectNotFound, ObjectStorage
from app.modules.media import _keys
from app.modules.media.models import ObjectMove, Photo

log = logging.getLogger(__name__)

MOVE_BATCH = 20
_VERSIONED = re.compile(r"^(?P<stem>photos/[^/]+/[a-z][a-z0-9_]*)-(?P<v>\d+)\.webp$")


def next_version_key(key: str) -> str:
    """``photos/{id}/display-1.webp`` -> ``photos/{id}/display-2.webp``."""
    match = _VERSIONED.match(key)
    if match is None:
        raise ValueError("not a versioned variant key")
    return f"{match['stem']}-{int(match['v']) + 1}.webp"


async def _relocate(storage: ObjectStorage, sources: list[str], dst: str) -> None:
    """Copy the first source that exists to ``dst``, then delete every source. When no source
    exists, ``dst`` must (an earlier attempt finished the copy)."""
    for src in sources:
        if await storage.copy(src, dst):
            break
    else:
        if await storage.head(dst) is None:
            raise ObjectNotFound(dst)
    for src in sources:
        await storage.delete(src)


async def _hold(storage: ObjectStorage, photo: Photo) -> None:
    for key in _keys.variant_keys_of(photo.variant_keys).values():
        await _relocate(storage, [key], _keys.held_key(key))


async def _release(storage: ObjectStorage, photo: Photo) -> None:
    keys = _keys.variant_keys_of(photo.variant_keys)
    new_keys = {name: next_version_key(key) for name, key in keys.items()}
    for name, key in keys.items():
        # Normally the held copy; the live key if the hold never finished.
        await _relocate(storage, [_keys.held_key(key), key], new_keys[name])
    photo.variant_keys = new_keys


async def run_move(session: AsyncSession, storage: ObjectStorage, photo_id: uuid.UUID) -> bool:
    """Finish the photo's pending move, if any, and clear ``object_move``. Commits. Returns
    False (and leaves the move for the job) when storage fails."""
    photo = await session.scalar(
        select(Photo)
        .where(Photo.id == photo_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if photo is None or photo.object_move is None:
        await session.commit()
        return True
    try:
        if photo.object_move == ObjectMove.HOLD:
            await _hold(storage, photo)
        else:
            await _release(storage, photo)
    except Exception as exc:
        await session.rollback()
        log.warning("photo %s: object move failed (%s); retried later", photo_id, type(exc))
        return False
    photo.object_move = None
    await session.commit()
    return True


async def move_pending(session: AsyncSession, storage: ObjectStorage) -> int:
    """One batch of rows with a pending move (``media.move_objects``). Returns rows found. The
    job runs one batch per minute: a move that keeps failing must not make it spin."""
    ids = list(
        await session.scalars(
            select(Photo.id)
            .where(Photo.object_move.is_not(None))
            .order_by(Photo.id)
            .limit(MOVE_BATCH)
        )
    )
    await session.commit()
    for photo_id in ids:
        await run_move(session, storage, photo_id)
    return len(ids)
