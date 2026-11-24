"""leaderboard: City boards open

PO decision 2026-11-24 (D76): the City level opens beside Area. No schema change: publishes the
next ``leaderboard`` version = the latest values with the ``city`` level's ``enabled`` set to true
(the reverse of 0022). The city level kept its minimums and titles while locked, so City boards
use the same scoring and tie-breaks as every board (D59, D67) and award "City Kindness Hero" (#1)
and "Top 3" (#2, #3) at month end. State and Country stay disabled ("Coming soon").

Revision ID: 0025
Revises: 0024
Create Date: 2026-11-24 00:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0025"
down_revision: str | Sequence[str] | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NAMESPACE = "leaderboard"
UPGRADE_REASON = "PO 2026-11-24: City leaderboards open beside Area (D76)"
DOWNGRADE_REASON = "Rollback of 0025: City shown as locked again"


def _set_city_enabled(enabled: bool, reason: str) -> None:
    """Append the next ``leaderboard`` version = the latest with the city level's ``enabled`` set,
    if the latest differs. config_versions is append-only, so both directions append; re-running
    is safe and a version published by hand in between is built on."""
    op.get_bind().execute(
        sa.text(
            "INSERT INTO config_versions (namespace, version, values, reason, created_by) "
            "SELECT latest.namespace, latest.version + 1, "
            "jsonb_set(latest.values, '{levels}', ("
            "  SELECT jsonb_agg(CASE WHEN lvl->>'kind' = 'city' "
            "    THEN jsonb_set(lvl, '{enabled}', to_jsonb(CAST(:enabled AS boolean))) "
            "    ELSE lvl END ORDER BY ord) "
            "  FROM jsonb_array_elements(latest.values->'levels') WITH ORDINALITY AS t(lvl, ord)"
            ")), :reason, NULL "
            "FROM config_versions AS latest "
            "WHERE latest.namespace = CAST(:namespace AS varchar) "
            "AND latest.version = (SELECT max(version) FROM config_versions "
            "WHERE namespace = CAST(:namespace AS varchar)) "
            "AND EXISTS (SELECT 1 FROM jsonb_array_elements(latest.values->'levels') AS l(lvl) "
            "  WHERE lvl->>'kind' = 'city' "
            "  AND CAST(lvl->>'enabled' AS boolean) IS DISTINCT FROM CAST(:enabled AS boolean))"
        ),
        {"namespace": NAMESPACE, "enabled": enabled, "reason": reason},
    )


def upgrade() -> None:
    """Upgrade schema."""
    _set_city_enabled(True, UPGRADE_REASON)


def downgrade() -> None:
    """Downgrade schema."""
    _set_city_enabled(False, DOWNGRADE_REASON)
