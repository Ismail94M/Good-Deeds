"""Unit tests for the pure plausibility checks (docs/M2-DESIGN.md §3.5)."""

import math
from datetime import UTC, datetime, timedelta

import pytest

from app.core.errors import AppError
from app.modules.deeds import _plausibility as p

T0 = datetime(2026, 11, 15, 6, 0, tzinfo=UTC)
BLR = p.Point(lat=12.9716, lng=77.5946)


def _north(point: p.Point, metres: float) -> p.Point:
    """A point `metres` due north on the haversine sphere (exact for these tests)."""
    return p.Point(lat=point.lat + math.degrees(metres / p.EARTH_RADIUS_M), lng=point.lng)


def _code(exc: pytest.ExceptionInfo[AppError]) -> str | None:
    return exc.value.code


def test_distance_is_symmetric_and_zero_for_same_point() -> None:
    other = _north(BLR, 1234)
    assert p.distance_m(BLR, BLR) == 0
    assert p.distance_m(BLR, other) == pytest.approx(1234, abs=0.01)
    assert p.distance_m(other, BLR) == pytest.approx(p.distance_m(BLR, other))


def test_distance_known_value() -> None:
    # Bangalore to Chennai is about 290 km in a straight line.
    chennai = p.Point(lat=13.0827, lng=80.2707)
    assert 285_000 < p.distance_m(BLR, chennai) < 295_000


# --------------------------------------------------- accuracy


def test_accuracy_at_limit_passes_and_limit_plus_one_fails() -> None:
    p.ensure_accuracy(500, 500)
    with pytest.raises(AppError) as exc:
        p.ensure_accuracy(501, 500)
    assert _code(exc) == "location_too_imprecise"
    assert exc.value.status_code == 400


# --------------------------------------------------- service area


def test_service_area_edge() -> None:
    inside = _north(BLR, 40_000 - 1)
    outside = _north(BLR, 40_000 + 1)
    p.ensure_in_service_area(inside, BLR, 40)
    with pytest.raises(AppError) as exc:
        p.ensure_in_service_area(outside, BLR, 40)
    assert _code(exc) == "outside_service_area"


def test_service_area_km_is_reported() -> None:
    assert p.service_area_km(_north(BLR, 6200), BLR) == pytest.approx(6.2, abs=1e-6)


# --------------------------------------------------- travel


def _travel(metres: float, seconds: float) -> tuple[p.Fix, p.Fix]:
    return p.Fix(BLR, T0), p.Fix(_north(BLR, metres), T0 + timedelta(seconds=seconds))


def test_travel_at_max_speed_plus_slack_passes_and_beyond_fails() -> None:
    # 80 km/h for 60 s = 1333.33 m, plus 1000 m slack.
    limit = 80 / 3.6 * 60 + 1000
    ok_prev, ok_cur = _travel(limit - 0.01, 60)
    bad_prev, bad_cur = _travel(limit + 0.01, 60)
    assert p.travel_ok(ok_prev, ok_cur, 80, 1000)
    assert not p.travel_ok(bad_prev, bad_cur, 80, 1000)
    p.ensure_travel(ok_prev, ok_cur, 80, 1000)
    with pytest.raises(AppError) as exc:
        p.ensure_travel(bad_prev, bad_cur, 80, 1000)
    assert _code(exc) == "implausible_travel"


def test_travel_within_slack_passes_with_no_time_elapsed() -> None:
    assert p.travel_ok(*_travel(999, 0), 80, 1000)
    assert not p.travel_ok(*_travel(1001, 0), 80, 1000)


def test_forwarded_screenshot_case() -> None:
    # Two witnesses 15 km apart, 30 s apart: impossible. 100 m apart: fine.
    assert not p.travel_ok(*_travel(15_000, 30), 80, 1000)
    assert p.travel_ok(*_travel(100, 30), 80, 1000)


def test_no_previous_fix_always_passes() -> None:
    p.ensure_travel(None, p.Fix(BLR, T0), 80, 1000)


def test_speed_for_evidence() -> None:
    assert p.speed_kmh(*_travel(1000, 3600)) == pytest.approx(1.0, abs=1e-6)
    # Elapsed time is floored at 1 s, so the figure is finite.
    assert p.speed_kmh(*_travel(10, 0)) == pytest.approx(36.0, abs=1e-6)


# --------------------------------------------------- time


def test_qr_occurred_at_bounds() -> None:
    earliest, latest = p.qr_occurred_range(T0, 120)
    assert earliest == T0 - timedelta(hours=2)
    assert latest == T0 + timedelta(seconds=60)
    p.ensure_occurred_at(earliest, earliest, latest)
    p.ensure_occurred_at(latest, earliest, latest)
    for bad in (earliest - timedelta(seconds=1), latest + timedelta(seconds=1)):
        with pytest.raises(AppError) as exc:
            p.ensure_occurred_at(bad, earliest, latest)
        assert _code(exc) == "implausible_time"


def test_invite_occurred_at_bounds() -> None:
    earliest, latest = p.invite_occurred_range(T0, 7)
    assert earliest == T0 - timedelta(days=7)
    assert latest == T0 + timedelta(seconds=60)
    assert p.occurred_at_ok(earliest, earliest, latest)
    assert not p.occurred_at_ok(earliest - timedelta(seconds=1), earliest, latest)
    assert not p.occurred_at_ok(latest + timedelta(seconds=1), earliest, latest)
