"""Fraud event handlers (docs/M5-DESIGN.md D82, §5).

``check_verified_deed`` runs every enabled detector on each newly verified deed. It is
idempotent twice over: ``run_handler`` records the (event, handler) pair, and ``raise_flag``
only inserts with ``ON CONFLICT DO NOTHING``, so a retry or a later sweep changes nothing.

Scoring's ``ReporterConcentrationExceeded``, ``DeedNeedsReview`` and ``PointsOverridden`` get
their subscribers here when scoring emits them (later M5 tasks): a concentration or content
detector turns the event into a ``Finding`` through the same ``run_on_event``.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import subscribe
from app.modules.deeds.service import DeedVerified
from app.modules.fraud._detect import run_on_event


@subscribe(DeedVerified)
async def check_verified_deed(session: AsyncSession, event: DeedVerified) -> None:
    await run_on_event(session, event)
