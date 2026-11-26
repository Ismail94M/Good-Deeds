"""Conflict of interest for the scoring admin actions (docs/M5-DESIGN.md D90).

An admin may not override, approve, read the scoring of, or revoke anything for a deed or person
they are involved with: a deed they did or reported, or a person they have a verified deed with
(either role) in the last ``fraud.coi_lookback_days``. They get 404, as for an unknown id, so
they can't learn that anything exists. Computed here from ``deeds.service.related_users`` (the
same source as the fraud rule), so scoring needs nothing from fraud's internals.
"""

import uuid
from collections.abc import Iterable
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.deeds import service as deeds


async def blocked_users(
    session: AsyncSession, admin_id: uuid.UUID, lookback_days: int, now: datetime
) -> set[uuid.UUID]:
    """The admin and everyone related to them inside the lookback."""
    related = await deeds.related_users(session, admin_id, now - timedelta(days=lookback_days))
    return {admin_id, *related}


async def conflicted(
    session: AsyncSession,
    admin_id: uuid.UUID,
    user_ids: Iterable[uuid.UUID | None],
    lookback_days: int,
    now: datetime,
) -> bool:
    people = {u for u in user_ids if u is not None}
    if admin_id in people:
        return True
    return bool(people & await blocked_users(session, admin_id, lookback_days, now))
