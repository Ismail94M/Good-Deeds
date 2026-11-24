"""Location and time plausibility checks (docs/M2-DESIGN.md §3.5). Pure functions.

Location comes from the client and can be spoofed, so these checks are a speed bump, not proof.
Distances are haversine on a sphere; PostGIS is used only for same-act grouping.

Each ``ensure_*`` function raises an ``AppError`` with the contract's error code; the ``*_ok``
and measuring functions return values so that results can be recorded as evidence.
"""

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.core.errors import AppError

EARTH_RADIUS_M = 6_371_008.8
# How far in the future a client-supplied occurred_at may be (clock skew).
FUTURE_TOLERANCE = timedelta(seconds=60)


@dataclass(frozen=True)
class Point:
    lat: float
    lng: float


@dataclass(frozen=True)
class Fix:
    """A position at a time (e.g. a previous scan)."""

    point: Point
    at: datetime


def distance_m(a: Point, b: Point) -> float:
    """Great-circle distance in metres (haversine)."""
    lat1, lat2 = math.radians(a.lat), math.radians(b.lat)
    dlat = lat2 - lat1
    dlng = math.radians(b.lng - a.lng)
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(h)))


# --------------------------------------------------- accuracy and service area


def accuracy_ok(accuracy_m: int, max_accuracy_m: int) -> bool:
    return accuracy_m <= max_accuracy_m


def ensure_accuracy(accuracy_m: int, max_accuracy_m: int) -> None:
    if not accuracy_ok(accuracy_m, max_accuracy_m):
        raise AppError(
            "Your location isn't precise enough. Move to an open spot and try again.",
            code="location_too_imprecise",
        )


def service_area_km(point: Point, centre: Point) -> float:
    return distance_m(point, centre) / 1000


def in_service_area(point: Point, centre: Point, radius_km: float) -> bool:
    return service_area_km(point, centre) <= radius_km


def ensure_in_service_area(point: Point, centre: Point, radius_km: float) -> None:
    if not in_service_area(point, centre, radius_km):
        raise AppError("Good Deeds isn't available in this area yet.", code="outside_service_area")


# --------------------------------------------------- travel


def speed_kmh(previous: Fix, current: Fix) -> float:
    """Apparent speed between two fixes, for evidence. Elapsed time is floored at 1 s."""
    elapsed_s = max(1.0, (current.at - previous.at).total_seconds())
    return distance_m(previous.point, current.point) / elapsed_s * 3.6


def travel_ok(previous: Fix, current: Fix, max_speed_kmh: float, slack_m: float) -> bool:
    """``distance - slack <= max_speed * elapsed``. The slack absorbs GPS error, so nearby
    fixes are always plausible however little time has passed."""
    elapsed_s = max(0.0, (current.at - previous.at).total_seconds())
    reachable_m = max_speed_kmh / 3.6 * elapsed_s
    return distance_m(previous.point, current.point) - slack_m <= reachable_m


def ensure_travel(previous: Fix | None, current: Fix, max_speed_kmh: float, slack_m: float) -> None:
    if previous is not None and not travel_ok(previous, current, max_speed_kmh, slack_m):
        raise AppError(
            "This scan doesn't match where you or they were a moment ago.",
            code="implausible_travel",
        )


# --------------------------------------------------- time


def occurred_at_ok(occurred_at: datetime, earliest: datetime, latest: datetime) -> bool:
    return earliest <= occurred_at <= latest


def ensure_occurred_at(occurred_at: datetime, earliest: datetime, latest: datetime) -> None:
    if not occurred_at_ok(occurred_at, earliest, latest):
        raise AppError("That time isn't possible for this deed.", code="implausible_time")


def qr_occurred_range(scanned_at: datetime, max_age_minutes: int) -> tuple[datetime, datetime]:
    """QR path: from ``max_age_minutes`` before the scan to 60 s after it."""
    return scanned_at - timedelta(minutes=max_age_minutes), scanned_at + FUTURE_TOLERANCE


def invite_occurred_range(now: datetime, max_age_days: int) -> tuple[datetime, datetime]:
    """Invite path (D5): up to ``max_age_days`` ago, and at most 60 s in the future."""
    return now - timedelta(days=max_age_days), now + FUTURE_TOLERANCE
