"""The ``media`` config values that comment reports share with photo reports (§2.5, §3.4a).

Comment reports and their review follow the photo-report rules, and their limits live once, in the
``media`` namespace: details length, decision note length, how long an outcome notice is shown,
and the admin decision rate. ``feed`` may not import media's private ``_limits``, so this reads
only those keys from the same versioned rows. Read-only: **never publish this model** (it holds a
subset of the namespace and would drop every other key).
"""

from pydantic import ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.runtime_config import RuntimeConfig, load
from app.modules.feed._limits import RATE_SPEC


class SharedReportLimits(RuntimeConfig):
    namespace = "media"
    # A subset of the namespace: the other keys are media's own.
    model_config = ConfigDict(extra="ignore", frozen=True)

    report_details_max: int = Field(gt=0, le=500)  # the details columns are varchar(500)
    decision_note_max: int = Field(gt=0)
    report_outcome_display_days: int = Field(gt=0)
    rate_admin_decision_user: str = Field(pattern=RATE_SPEC)


async def load_shared(session: AsyncSession) -> SharedReportLimits:
    """Fails closed, like every config read."""
    return (await load(session, SharedReportLimits))[1]
