"""Scoring event handlers (docs/M4-DESIGN.md §5). Every one ends in the idempotent
``reconcile_deed``, which reads the deed's current state, so the order the handlers run in,
duplicates and retries don't change the ledger (D49).

Deliberately **not** subscribed: any photo removal or moderation event (Q29, D51: a removed
photo never changes points) and every ``feed`` event (visibility never reaches scoring, D47).
``DeedPhotoReady`` is the only ``media`` import scoring has, through ``media.service``: the event
class only, never photo state (D47).

M5: ``DeedHoldChanged`` (``fraud.service``) re-scores a deed whose prize hold was placed or
released (docs/M5-DESIGN.md D79); ``reconcile_deed`` reads the hold itself, so the event's
``held`` flag is never trusted and a late or repeated event changes nothing.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import subscribe
from app.modules.deeds.service import (
    DeedCountsForPointsChanged,
    DeedEdited,
    DeedPrizeRankChanged,
    DeedRejected,
    DeedVerified,
    DeedWithdrawn,
    revision_time,
    scoring_inputs,
    withdraw_window_hours,
)
from app.modules.fraud.service import DeedHoldChanged
from app.modules.media.service import DeedPhotoReady
from app.modules.scoring._evidence import record_photo_ready
from app.modules.scoring._ledger import reconcile_deed
from app.modules.scoring.models import LedgerReason

# Edits to other fields (people helped) never change points.
SCORED_FIELDS = frozenset({"category", "description"})


@subscribe(DeedVerified)
async def score_verified_deed(session: AsyncSession, event: DeedVerified) -> None:
    await reconcile_deed(session, event.deed_id)


@subscribe(DeedCountsForPointsChanged)
async def rescore_counts_for_points(
    session: AsyncSession, event: DeedCountsForPointsChanged
) -> None:
    await reconcile_deed(session, event.deed_id, hint=LedgerReason.PROMOTED)


@subscribe(DeedPrizeRankChanged)
async def rescore_prize_rank(session: AsyncSession, event: DeedPrizeRankChanged) -> None:
    await reconcile_deed(session, event.deed_id)


@subscribe(DeedHoldChanged)
async def rescore_held_deed(session: AsyncSession, event: DeedHoldChanged) -> None:
    await reconcile_deed(session, event.deed_id)


@subscribe(DeedEdited)
async def rescore_edited_deed(session: AsyncSession, event: DeedEdited) -> None:
    """Only an edit of the category or description saved inside the withdrawal window re-scores
    (D56); after it, edits change the text and never the points."""
    if not SCORED_FIELDS.intersection(event.changed_fields):
        return
    inputs = await scoring_inputs(session, event.deed_id)
    if inputs is None or inputs.verified_at is None:
        return  # a pending deed is scored, with its current text, when it is verified
    saved_at = await revision_time(session, event.revision_id)
    window_end = inputs.verified_at + timedelta(hours=await withdraw_window_hours(session))
    if saved_at is None or saved_at >= window_end:  # half-open, like withdrawal
        return
    await reconcile_deed(session, event.deed_id)


@subscribe(DeedWithdrawn)
async def reverse_withdrawn_deed(session: AsyncSession, event: DeedWithdrawn) -> None:
    await reconcile_deed(session, event.deed_id)


@subscribe(DeedRejected)
async def reverse_rejected_deed(session: AsyncSession, event: DeedRejected) -> None:
    await reconcile_deed(session, event.deed_id)


@subscribe(DeedPhotoReady)
async def record_photo_evidence(session: AsyncSession, event: DeedPhotoReady) -> None:
    """Sets the photo high-water mark once (D51), then reconciles. ``reconcile_deed`` gives the
    bonus only for evidence recorded inside the withdrawal window, so a late photo is recorded
    (for the AI) without moving points."""
    if await record_photo_ready(
        session,
        deed_id=event.deed_id,
        photo_id=event.photo_id,
        attached_at=event.attached_at,
        # When the photo became ready, not when this handler ran (SEC3 L5). Events emitted
        # before ``ready_at`` existed fall back to the handling time.
        ready_at=event.ready_at or datetime.now(UTC),
    ):
        await reconcile_deed(session, event.deed_id)
