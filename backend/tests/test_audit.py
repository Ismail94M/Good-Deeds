import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditLog, record_audit


async def test_audit_entries_are_saved(session: AsyncSession) -> None:
    actor = uuid.uuid4()
    entry = record_audit(
        session,
        entity_type="impact_evaluation",
        entity_id="42",
        action="override",
        actor_id=actor,
        old_value={"score": 30},
        new_value={"score": 55},
        reason="Photo shows more people helped",
    )
    await session.flush()

    stored = await session.get(AuditLog, entry.id)
    assert stored is not None
    assert stored.new_value == {"score": 55}
    assert stored.actor_id == actor


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE audit_log SET reason = 'changed' WHERE id = :id",
        "DELETE FROM audit_log WHERE id = :id",
    ],
)
async def test_audit_log_is_append_only(session: AsyncSession, statement: str) -> None:
    entry = record_audit(session, entity_type="deed", entity_id="1", action="verify", actor_id=None)
    await session.flush()

    with pytest.raises(DBAPIError, match="append-only"):
        async with session.begin_nested():
            await session.execute(text(statement), {"id": entry.id})
