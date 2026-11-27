"""Identity event handlers (docs/M6-DESIGN.md §4.4).

``media`` owns profile pictures; identity only keeps the pointer (``users.avatar_ref``) that
``public_summaries`` hands to every module's ``UserRef`` (D117). The only thing identity reads
from ``media.service`` is the ``AvatarChanged`` event class (D104).
"""

from sqlalchemy import or_, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import subscribe
from app.modules.identity.models import User
from app.modules.media.service import AvatarChanged


@subscribe(AvatarChanged)
async def on_avatar_changed(session: AsyncSession, event: AvatarChanged) -> None:
    """Store the new reference unless a newer change is already stored, so events handled out
    of order (or retried) never overwrite a newer picture."""
    await session.execute(
        update(User)
        .where(
            User.id == event.user_id,
            or_(User.avatar_changed_at.is_(None), User.avatar_changed_at <= event.changed_at),
        )
        .values(avatar_ref=event.avatar_ref, avatar_changed_at=event.changed_at)
        .execution_options(synchronize_session=False)
    )
