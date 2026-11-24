"""Feed event handlers (docs/M3-DESIGN.md §5): the projection follows the deed's status.

All three call the idempotent ``sync_post``, which reads the deed's current status, so the order
the handlers run in and retries don't matter. Deliberately **not** subscribed: ``DeedSubmitted``
(pending deeds aren't published), ``DeedEdited`` (content is read live, D38), and the scoring
events ``DeedPrizeRankChanged`` and ``DeedCountsForPointsChanged`` (scoring never reaches the
feed).
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import subscribe
from app.modules.deeds.service import DeedRejected, DeedVerified, DeedWithdrawn
from app.modules.feed._projection import sync_post


@subscribe(DeedVerified)
async def publish_verified_deed(session: AsyncSession, event: DeedVerified) -> None:
    await sync_post(session, event.deed_id)


@subscribe(DeedWithdrawn)
async def withdraw_post(session: AsyncSession, event: DeedWithdrawn) -> None:
    await sync_post(session, event.deed_id)


@subscribe(DeedRejected)
async def reject_post(session: AsyncSession, event: DeedRejected) -> None:
    await sync_post(session, event.deed_id)
