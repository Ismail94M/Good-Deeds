"""The feed's one call into ``media`` (docs/M3-DESIGN.md §3.4 #1 "Composition", §4.7).

Kept in one place so a change to ``media.service.public_photos`` touches one function, and tests
can replace it. The caller must already have checked that the deeds are viewable: media only
filters the photos (ready, deed photos, not taken down).
"""

import uuid
from collections.abc import Iterable

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.storage import ObjectStorage
from app.modules.feed.schemas import PhotoOut
from app.modules.media import service as media


async def public_photos(
    session: AsyncSession, deed_ids: Iterable[uuid.UUID], storage: ObjectStorage
) -> dict[uuid.UUID, list[PhotoOut]]:
    """Each deed's public photos in carousel order (an empty list when none)."""
    ids = list(deed_ids)
    if not ids:
        return {}
    found = await media.public_photos(session, ids, storage=storage)
    return {
        deed_id: [PhotoOut.model_validate(p.model_dump()) for p in found.get(deed_id, [])]
        for deed_id in ids
    }
