"""The photo evidence record (docs/M4-DESIGN.md D51, §4.3).

``scoring_evidence.first_photo_ready_at`` is a high-water mark: set once, by the
``DeedPhotoReady`` handler, for the first deed photo that finished processing and was attached
within ``photo_bonus_window_hours`` of the report. A trigger forbids changing or clearing it, and
no scoring code listens to any photo removal (PRD Q29), so removing a photo never changes points.

Only photos that become ready inside the withdrawal window earn the bonus (``_ledger.py``
compares this time with the window end); a later one is still recorded, for the AI (M4b).
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.deeds import service as deeds
from app.modules.scoring._limits import load_scoring
from app.modules.scoring.models import ScoringEvidence


async def record_photo_ready(
    session: AsyncSession,
    *,
    deed_id: uuid.UUID,
    photo_id: uuid.UUID,
    attached_at: datetime,
    ready_at: datetime,
) -> bool:
    """Set the deed's first photo once, at ``ready_at``: when the media worker finished the
    photo, not when this handler runs, so a worker backlog never decides the bonus (SEC3 L5).
    Returns True if this call set it; False if it was already set, the photo was attached
    after the photo window, or the deed doesn't exist."""
    inputs = await deeds.scoring_inputs(session, deed_id)
    if inputs is None:
        return False
    _, config = await load_scoring(session)
    if attached_at > inputs.reported_at + timedelta(hours=config.photo_bonus_window_hours):
        return False
    now = datetime.now(UTC)
    stmt = (
        insert(ScoringEvidence)
        .values(
            deed_id=deed_id, first_photo_ready_at=ready_at, first_photo_id=photo_id, updated_at=now
        )
        .on_conflict_do_update(
            index_elements=[ScoringEvidence.deed_id],
            set_={"first_photo_ready_at": ready_at, "first_photo_id": photo_id, "updated_at": now},
            where=ScoringEvidence.first_photo_ready_at.is_(None),
        )
        .returning(ScoringEvidence.deed_id)
    )
    return (await session.execute(stmt)).first() is not None


async def first_photo_ready_at(session: AsyncSession, deed_id: uuid.UUID) -> datetime | None:
    return await session.scalar(
        select(ScoringEvidence.first_photo_ready_at).where(ScoringEvidence.deed_id == deed_id)
    )
