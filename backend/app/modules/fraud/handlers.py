"""Fraud event handlers (docs/M5-DESIGN.md D82, §5).

``check_verified_deed`` runs every enabled detector on each newly verified deed. It is
idempotent twice over: ``run_handler`` records the (event, handler) pair, and ``raise_flag``
only inserts with ``ON CONFLICT DO NOTHING``, so a retry or a later sweep changes nothing.

Scoring detects the two patterns defined on its points and text rules and emits an event; the
handlers below turn each into a ``Finding`` and raise it through ``raise_flag`` like any
detector's (D85, D86). ``raise_flag`` honours ``detectors_enabled`` and opens or joins one live
case per (type, subjects), so a repeated event changes nothing. Opening a case holds nothing
(``hold_on_open`` is false for every type, PO principle 1). ``PointsOverridden`` gets its
subscriber with the notices (B45).
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import subscribe
from app.modules.deeds.service import DeedVerified
from app.modules.fraud._cases import raise_flag
from app.modules.fraud._detect import run_on_event
from app.modules.fraud._limits import load_fraud
from app.modules.fraud.detectors.base import Finding
from app.modules.fraud.models import FlagCaseType
from app.modules.scoring.service import DeedNeedsReview, ReporterConcentrationExceeded


@subscribe(DeedVerified)
async def check_verified_deed(session: AsyncSession, event: DeedVerified) -> None:
    await run_on_event(session, event)


async def _raise(session: AsyncSession, finding: Finding) -> None:
    version, config = await load_fraud(session)
    await raise_flag(session, finding, config, config_version=version)


@subscribe(ReporterConcentrationExceeded)
async def flag_reporter_concentration(
    session: AsyncSession, event: ReporterConcentrationExceeded
) -> None:
    """A ``concentration`` case for {doer, reporter} with the pair's prize-rank deeds that
    month (D85)."""
    if not event.deed_ids:
        return
    await _raise(
        session,
        Finding(
            case_type=FlagCaseType.CONCENTRATION,
            subject_ids=frozenset({event.doer_id, event.reporter_id}),
            deed_ids=frozenset(event.deed_ids),
            signal={
                "period_month": event.period_month.strftime("%Y-%m"),
                "share_bp": event.share_bp,
                "doer_points": event.doer_points,
            },
        ),
    )


@subscribe(DeedNeedsReview)
async def flag_content(session: AsyncSession, event: DeedNeedsReview) -> None:
    """A ``content`` case for the deed's doer and reporter: the rule-based text check flagged
    the deed (D86). The reporter wrote the text and would lose points on a rejection, so both
    are subjects and can explain and appeal (PO principle 3)."""
    await _raise(
        session,
        Finding(
            case_type=FlagCaseType.CONTENT,
            subject_ids=frozenset({event.doer_id, event.reporter_id}),
            deed_ids=frozenset({event.deed_id}),
            signal={"text_check": event.reason},
        ),
    )
