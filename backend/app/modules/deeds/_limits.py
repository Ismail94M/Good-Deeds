"""Typed values of the ``deeds`` config namespace (docs/M2-DESIGN.md §2.1).

Every key is required and has no default: the ``config_versions`` row (seeded as ``deeds`` v1 by
migration 0004, then v2 by 0007, v3 by 0008, v4 by 0009, v5 by 0013, v6 by 0018 and v7 by 0019) is
the only source of these values. Change them by publishing a new version.

v5 (docs/M3-DESIGN.md §2.1, D41): ``photos_enabled`` moved to the ``media`` namespace, and
``area_fallback_m`` was added.

v6 (migration 0018): the report-limit warning thresholds ``reporter_monthly_warn_at`` and
``reporter_doer_monthly_warn_at``, formerly frontend constants.

v7 (migration 0019, docs/M4-DESIGN.md §2.6): ``withdraw_window_hours`` and
``fallback_region_code``.
"""

from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.runtime_config import RuntimeConfig, load


class ServiceArea(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    lat: float
    lng: float
    radius_km: float


class Category(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    slug: str
    label: str


class DeedLimits(RuntimeConfig):
    """Config namespace ``deeds``."""

    namespace = "deeds"

    # Reporting (D16, D19): deeds a reporter may create per IST calendar month, both paths,
    # in total and about any one doer
    reporter_monthly_limit: int
    reporter_doer_monthly_limit: int
    # Warnings (D20, §5.5): the report hub warns when this many or fewer reports are left this
    # month in total; the deed form when this many or fewer about the scanned doer are left
    reporter_monthly_warn_at: int
    reporter_doer_monthly_warn_at: int
    # Invites (B6); switched off while ``invites_enabled`` is false (D17)
    invites_enabled: bool
    invites_per_day: int
    invite_expiry_hours: int
    invite_occurred_max_age_days: int
    shared_device_pair_monthly_limit: int
    # Same-act grouping
    same_act_radius_m: int
    same_act_window_minutes: int
    same_act_cap: int
    # QR scans and plausibility
    scan_submit_minutes: int
    qr_occurred_max_age_minutes: int
    max_location_accuracy_m: int
    max_travel_speed_kmh: int
    travel_slack_m: int
    service_area: ServiceArea
    # Area names (M3 D39): a point outside every area polygon takes the nearest area within
    # this many metres, else shows "Bangalore"
    area_fallback_m: int
    # Withdrawal window (M4 D64): a reporter may withdraw a verified deed only this many hours
    # after ``verified_at``; points settle then. Pending invites can be cancelled at any time.
    withdraw_window_hours: int
    # Leaderboards (M4 D66): a deed with no ward (``area_id`` null) counts from this region
    # (``regions.code``), the city
    fallback_region_code: str
    # Deed content
    description_min: int
    description_max: int
    beneficiaries_max: int
    categories: tuple[Category, ...]
    # Rate limits ("<count>/<seconds>"), an account key and an IP key per action (D1)
    rate_scan_witness: str
    rate_scan_ip: str
    rate_manual_witness: str
    rate_manual_ip: str
    rate_manual_target: str
    rate_manual_target_daily: str
    rate_invite_create_ip: str
    rate_invite_preview_ip: str
    rate_invite_accept_user: str
    rate_invite_accept_ip: str
    rate_invite_reissue_user: str
    rate_invite_reissue_ip: str
    rate_deed_edit_user: str
    rate_deed_edit_ip: str
    rate_self_log_user: str
    rate_self_log_ip: str
    rate_profile_counts_ip: str
    # Retention (B9)
    unconsumed_scan_retention_days: int

    def has_category(self, slug: str) -> bool:
        return any(c.slug == slug for c in self.categories)


async def load_limits(session: AsyncSession) -> tuple[int, DeedLimits]:
    """``(version, limits)`` of the active ``deeds`` config. Fails closed if it is missing."""
    return await load(session, DeedLimits)
