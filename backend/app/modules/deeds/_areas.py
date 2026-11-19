"""Area names for deeds (docs/M3-DESIGN.md §2.1, D39).

A deed's ``area_id`` is resolved from its own point when it is created (QR) or accepted (invite
with a location): the lowest-id area covering the point, else the nearest area within
``area_fallback_m``, else null (shown as "Bangalore"). The same rule as migration 0013's backfill.

The lookup runs only in our own database (PostGIS); coordinates never leave this module and are
never sent to an external geocoder. If the lookup fails for any reason, the deed is still created
with no area, and the log line names the error type only: a database error's message would carry
the query parameters, which include the coordinates.
"""

import logging
from collections.abc import Iterable

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.deeds.models import Area

logger = logging.getLogger(__name__)

# Shown when a deed has no area (no location, outside every area, or a failed lookup).
DEFAULT_AREA_NAME = "Bangalore"

_LOOKUP = (
    "SELECT COALESCE("
    "(SELECT a.id FROM areas AS a WHERE ST_Covers(a.boundary, p.loc) ORDER BY a.id LIMIT 1), "
    "(SELECT a.id FROM areas AS a WHERE ST_DWithin(a.boundary, p.loc, :fallback_m) "
    "ORDER BY ST_Distance(a.boundary, p.loc), a.id LIMIT 1)) "
    "FROM (SELECT CAST(:loc AS geography) AS loc) AS p"
)


async def resolve_area_id(
    session: AsyncSession, location: str | None, fallback_m: int
) -> int | None:
    """The area for a point given as EWKT (``deeds._common.point_ewkt``), or None. Never raises:
    it runs in a savepoint, so a failed lookup leaves the caller's transaction usable."""
    if location is None:
        return None
    try:
        async with session.begin_nested():
            value = await session.scalar(text(_LOOKUP), {"loc": location, "fallback_m": fallback_m})
    except Exception as exc:  # any failure means "no area", never a failed deed
        logger.warning(
            "Area lookup failed (%s); the deed is shown as Bangalore.", type(exc).__name__
        )
        return None
    return None if value is None else int(value)


async def area_names(session: AsyncSession, area_ids: Iterable[int | None]) -> dict[int, str]:
    """Display names by id, in one query. Never ``ward_code``."""
    ids = {i for i in area_ids if i is not None}
    if not ids:
        return {}
    rows = await session.execute(select(Area.id, Area.name).where(Area.id.in_(ids)))
    return {row.id: row.name for row in rows}


def display_name(area_id: int | None, names: dict[int, str]) -> str:
    return names.get(area_id, DEFAULT_AREA_NAME) if area_id is not None else DEFAULT_AREA_NAME
