"""leaderboard: Area boards only

PO decision 2026-11-23: only Area (ward) leaderboards are open at launch. City, State and Country
are shown in the app as locked, upcoming levels. No schema change: publishes the next
``leaderboard`` version = the latest values with the ``city`` level's ``enabled`` set to false.
The city level keeps its minimums and titles, so opening it later is one config version that sets
``enabled`` back to true (state and country are already disabled; they need titles first).

While a level is disabled: no boards are computed or finalized for it (so no City titles are
awarded), its board and Heroes reads return 404, and it is left out of "Your leaderboards".
Titles already awarded stay (finalized months are immutable, QA2).

Revision ID: 0022
Revises: 0021
Create Date: 2026-11-23 00:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0022"
down_revision: str | Sequence[str] | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NAMESPACE = "leaderboard"
UPGRADE_REASON = "PO 2026-11-23: Area (ward) leaderboards only; City shown as locked"
DOWNGRADE_REASON = "Rollback of 0022: City leaderboards enabled again"


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
    _set_city_enabled(False, UPGRADE_REASON)


def downgrade() -> None:
    """Downgrade schema."""
    _set_city_enabled(True, DOWNGRADE_REASON)
