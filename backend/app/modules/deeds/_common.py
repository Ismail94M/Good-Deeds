"""Small helpers shared by the deeds module's private files."""

from datetime import UTC, date, datetime, timedelta
from typing import Any

from geoalchemy2 import Geometry
from sqlalchemy import ColumnElement, cast, func
from sqlalchemy.exc import IntegrityError

from app.modules.deeds._plausibility import Point
from app.modules.identity.service import LAUNCH_TZ


def utcnow() -> datetime:
    return datetime.now(UTC)


def ist_day(at: datetime) -> date:
    """The launch-city (IST) calendar day of ``at``, used for every per-day rule."""
    return at.astimezone(LAUNCH_TZ).date()


def ist_month(at: datetime) -> tuple[datetime, datetime]:
    """Start (inclusive) and end (exclusive) of the IST calendar month containing ``at``."""
    local = at.astimezone(LAUNCH_TZ)
    start = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = (start + timedelta(days=32)).replace(day=1)
    return start, end


def point_ewkt(lat: float, lng: float) -> str:
    """A value for a ``geography(Point, 4326)`` column. Note the order: longitude first."""
    return f"SRID=4326;POINT({lng!r} {lat!r})"


def lat_lng(column: Any) -> tuple[ColumnElement[float], ColumnElement[float]]:
    """``(lat, lng)`` SQL expressions for a geography column (internal use only: coordinates
    are never returned by the API)."""
    geom = cast(column, Geometry)
    return func.ST_Y(geom), func.ST_X(geom)


def to_point(lat: float | None, lng: float | None) -> Point | None:
    return None if lat is None or lng is None else Point(lat=float(lat), lng=float(lng))


def constraint_name(exc: IntegrityError) -> str | None:
    """The violated constraint's name (constraint names are part of the error contract)."""
    diag = getattr(exc.orig, "diag", None)
    name = getattr(diag, "constraint_name", None)
    return name if isinstance(name, str) else None
