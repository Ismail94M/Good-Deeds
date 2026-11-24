"""Versioned runtime config rows (PRD §12 limits live here, not in code).

Each namespace (``qr``, ``deeds``, ...) has a series of versions in ``config_versions``. Every
version is a full snapshot of the namespace's values, and the highest version is the active
one. The table is append-only: a rollback is published as a new version with the old values.
"""

import uuid
from datetime import datetime
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict
from sqlalchemy import CheckConstraint, DateTime, Integer, String, Text, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.core.audit import record_audit
from app.core.db import Base, advisory_xact_lock


class ConfigVersion(Base):
    __tablename__ = "config_versions"
    __table_args__ = (CheckConstraint("version >= 1", name="version_positive"),)

    namespace: Mapped[str] = mapped_column(String(40), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    values: Mapped[dict[str, Any]] = mapped_column(JSONB)
    reason: Mapped[str] = mapped_column(Text)
    # No foreign key: the actor may be deleted later. Null means a migration or the system.
    created_by: Mapped[uuid.UUID | None]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RuntimeConfig(BaseModel):
    """Base for a namespace's typed values. Subclasses set ``namespace`` and declare every key
    without defaults, so the database row is the only source of the values."""

    namespace: ClassVar[str]
    model_config = ConfigDict(extra="forbid", frozen=True)


async def _latest(session: AsyncSession, namespace: str) -> ConfigVersion | None:
    result = await session.execute(
        select(ConfigVersion)
        .where(ConfigVersion.namespace == namespace)
        .order_by(ConfigVersion.version.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def load[T: RuntimeConfig](session: AsyncSession, model: type[T]) -> tuple[int, T]:
    """Return ``(version, values)`` for the active version of ``model.namespace``.

    Fails closed: raises ``RuntimeError`` if the namespace has no rows, and a pydantic
    ``ValidationError`` if the stored values don't match the model.
    """
    row = await _latest(session, model.namespace)
    if row is None:
        raise RuntimeError(f"No config rows for namespace {model.namespace!r}")
    return row.version, model.model_validate(row.values)


async def publish(
    session: AsyncSession,
    values: RuntimeConfig,
    *,
    actor_id: uuid.UUID | None,
    reason: str,
) -> int:
    """Publish ``values`` as the next version of its namespace and audit it. Returns the new
    version number. The caller commits."""
    namespace = type(values).namespace
    # Serialise publishers of the same namespace so two of them can't both pick max+1.
    await advisory_xact_lock(session, "config_versions", namespace)
    previous = await _latest(session, namespace)
    version = 1 if previous is None else previous.version + 1
    new_values = values.model_dump(mode="json")
    session.add(
        ConfigVersion(
            namespace=namespace,
            version=version,
            values=new_values,
            reason=reason,
            created_by=actor_id,
        )
    )
    record_audit(
        session,
        entity_type="config",
        entity_id=namespace,
        action="config.published",
        actor_id=actor_id,
        old_value=None if previous is None else previous.values,
        new_value=new_values,
        reason=reason,
    )
    await session.flush()
    return version
