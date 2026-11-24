"""leaderboard: featured city top performers

PO 2026-11-23 (M4-DESIGN D72): a "Featured City Top Performers" section under the Area search
shows the top members of a featured city, ranked by the board rules over the month's counted
deeds. Members count for a city by their registered city (``users.city``). No schema change:
publishes the next ``leaderboard`` version = the latest plus ``featured_cities`` with Bengaluru
(registered as "Bangalore" or "Bengaluru"), top 5, points shown (the PO's exception to D69 for
this section). More cities are added by appending to the list in a later config version.

Downgrade appends a version without ``featured_cities`` (config_versions is append-only).

Revision ID: 0023
Revises: 0022
Create Date: 2026-11-23 01:00:00

"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0023"
down_revision: str | Sequence[str] | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NAMESPACE = "leaderboard"
FEATURED_CITIES = [
    {
        "key": "bengaluru",
        "name": "Bengaluru",
        "registered_city_names": ["Bangalore", "Bengaluru"],
        "top_n": 5,
        "show_points": True,
        "enabled": True,
    }
]

_LATEST = (
    "FROM config_versions AS latest "
    "WHERE latest.namespace = CAST(:namespace AS varchar) "
    "AND latest.version = (SELECT max(version) FROM config_versions "
    "WHERE namespace = CAST(:namespace AS varchar)) "
)


def upgrade() -> None:
    """Upgrade schema."""
    op.get_bind().execute(
        sa.text(
            "INSERT INTO config_versions (namespace, version, values, reason, created_by) "
            "SELECT latest.namespace, latest.version + 1, "
            "latest.values || jsonb_build_object('featured_cities', CAST(:cities AS jsonb)), "
            ":reason, NULL " + _LATEST + "AND NOT (latest.values ? 'featured_cities')"
        ),
        {
            "namespace": NAMESPACE,
            "cities": json.dumps(FEATURED_CITIES),
            "reason": "PO 2026-11-23: Featured City Top Performers, Bengaluru top 5 (D72)",
        },
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.get_bind().execute(
        sa.text(
            "INSERT INTO config_versions (namespace, version, values, reason, created_by) "
            "SELECT latest.namespace, latest.version + 1, latest.values - 'featured_cities', "
            ":reason, NULL " + _LATEST + "AND latest.values ? 'featured_cities'"
        ),
        {"namespace": NAMESPACE, "reason": "Rollback of 0023: featured cities removed"},
    )
