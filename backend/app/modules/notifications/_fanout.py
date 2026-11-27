"""The network fan-out of a verified deed (docs/M6-DESIGN.md D110, §4.2; PO Q89).

When a deed is verified, the people connected to its reporter or doer (``network.audience`` and
``network.roles``; Q89: their followers) get one grouped *Your network* row a day: "Priya, Ravi
and 3 others you follow shared good deeds today".

- ``start_fanout`` runs in the ``DeedVerified`` handler. An actor (the reporter or the doer)
  starts at most ``network.max_fanouts_per_actor_day`` fan-outs per IST day: each started
  fan-out claims a ``notification_dedupe`` key ``network_fanout:{day}:{deed_id}`` for that
  actor, counted under an advisory lock. Actors over the cap are left out; with nobody left
  there is no fan-out. Otherwise a ``notification_fanouts`` row (``running``) records the deed
  and its actors.
- ``fanout_step`` (the ``notifications.fanout_network`` job) takes one running fan-out (``SKIP
  LOCKED``, oldest first) and does one batch of ``network.fanout_batch`` people from
  ``feed.service.follower_ids_page``, saving the cursor, so a restart resumes where it stopped.
  Each person is checked with ``feed.service.visible_deed_ids`` before ``notify`` (which skips
  inactive people, *Your network* switched off, a muted actor, and the daily cap of one row).
  The deed's own reporter and doer get their *About you* rows instead, never a network row.
  At ``network.max_recipients_per_event`` people it stops with status ``capped``.
- Network rows store **no deed id**: they link to Home (Stories), so one hidden or withdrawn
  deed never turns the day's row into "This deed is no longer available".
"""

import uuid
from datetime import date, datetime
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.deeds import service as deeds
from app.modules.feed.service import follower_ids_page, visible_deed_ids
from app.modules.notifications._limits import NotificationsConfig
from app.modules.notifications._write import Notice, ist_day, notify
from app.modules.notifications.models import (
    FanoutStatus,
    NotificationDedupe,
    NotificationFanout,
    NotificationKind,
)

# ``network.audience`` (config) -> the feed's name for it (``feed.service.Audience``).
FeedAudience = Literal["followers", "mutual", "followers_or_following"]
_AUDIENCE: dict[str, FeedAudience] = {
    "followers_of_actor": "followers",
    "mutual": "mutual",
    "followers_of_actor_or_followed_by": "followers_or_following",
}
_KEY_PREFIX = "network_fanout"


def _fanout_key(day: date, deed_id: uuid.UUID) -> str:
    return f"{_KEY_PREFIX}:{day.isoformat()}:{deed_id}"


async def _claim_actor_slot(
    session: AsyncSession,
    actor_id: uuid.UUID,
    deed_id: uuid.UUID,
    day: date,
    config: NotificationsConfig,
    now: datetime,
) -> bool:
    """Count this deed against the actor's fan-outs of ``day``; False when the cap is reached.
    A re-run for the same deed finds its own key and is allowed again."""
    lock = f"{_KEY_PREFIX}:{actor_id}:{day.isoformat()}"
    await session.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(lock, 0))))
    key = _fanout_key(day, deed_id)
    keys = set(
        await session.scalars(
            select(NotificationDedupe.key).where(
                NotificationDedupe.user_id == actor_id,
                NotificationDedupe.key.startswith(f"{_KEY_PREFIX}:{day.isoformat()}:"),
            )
        )
    )
    if key in keys:
        return True
    if len(keys) >= config.network.max_fanouts_per_actor_day:
        return False
    await session.execute(
        insert(NotificationDedupe)
        .values(user_id=actor_id, key=key, created_at=now)
        .on_conflict_do_nothing()
    )
    return True


async def start_fanout(
    session: AsyncSession,
    event: deeds.DeedVerified,
    *,
    config: NotificationsConfig,
    now: datetime,
) -> bool:
    """Record the fan-out of a newly verified deed (the job does the work). Returns whether one
    was started. The caller commits."""
    roles = {"reporter": event.reporter_id, "doer": event.doer_id}
    wanted = sorted({roles[r] for r in config.network.roles})
    day = ist_day(event.verified_at, config)
    actors = [
        a for a in wanted if await _claim_actor_slot(session, a, event.deed_id, day, config, now)
    ]
    if not actors:
        return False
    await session.execute(
        insert(NotificationFanout)
        .values(
            deed_id=event.deed_id,
            actor_ids=actors,
            recipients=0,
            status=FanoutStatus.RUNNING,
            created_at=now,
        )
        .on_conflict_do_nothing(index_elements=[NotificationFanout.deed_id])
    )
    return True


def _finish(fanout: NotificationFanout, status: FanoutStatus, now: datetime) -> None:
    fanout.status = status
    fanout.finished_at = max(now, fanout.created_at)


async def _run_batch(
    session: AsyncSession, fanout: NotificationFanout, config: NotificationsConfig, now: datetime
) -> None:
    deed = await deeds.get_deed(session, fanout.deed_id)
    if deed is None or deed.status != deeds.DeedStatus.VERIFIED:
        _finish(fanout, FanoutStatus.DONE, now)
        return
    rules = config.network
    room = rules.max_recipients_per_event - fanout.recipients
    if room <= 0:
        _finish(fanout, FanoutStatus.CAPPED, now)
        return
    audience = _AUDIENCE[rules.audience]
    limit = min(rules.fanout_batch, room)
    page = await follower_ids_page(
        session,
        fanout.actor_ids,
        after=fanout.cursor,
        limit=limit,
        audience=audience,
    )
    parties = {deed.reporter_id, deed.doer_id}
    for person in page:
        if person.user_id in parties:
            continue
        # Decision (a): only people who can see the deed now hear about it.
        if fanout.deed_id not in await visible_deed_ids(session, person.user_id, [fanout.deed_id]):
            continue
        for actor in person.of:
            if actor not in fanout.actor_ids:
                continue
            await notify(
                session,
                Notice(
                    kind=NotificationKind.NETWORK,
                    recipient_id=person.user_id,
                    actor_id=actor,
                    # Decision (b): no deed id; the row links to Home (Stories).
                    data={"day": ist_day(now, config).isoformat()},
                ),
                config=config,
                now=now,
            )
    if page:
        fanout.cursor = page[-1].user_id
        fanout.recipients += len(page)
    if len(page) < limit:
        _finish(fanout, FanoutStatus.DONE, now)
    elif fanout.recipients >= rules.max_recipients_per_event:
        more = await follower_ids_page(
            session, fanout.actor_ids, after=fanout.cursor, limit=1, audience=audience
        )
        _finish(fanout, FanoutStatus.CAPPED if more else FanoutStatus.DONE, now)


async def fanout_step(
    session: AsyncSession, *, config: NotificationsConfig, now: datetime
) -> NotificationFanout | None:
    """One batch of the oldest running fan-out nobody else is working on, or None when there
    is none. The caller commits (one batch per transaction)."""
    fanout = await session.scalar(
        select(NotificationFanout)
        .where(NotificationFanout.status == FanoutStatus.RUNNING)
        .order_by(NotificationFanout.created_at, NotificationFanout.deed_id)
        .limit(1)
        .with_for_update(skip_locked=True)
        .execution_options(populate_existing=True)
    )
    if fanout is None:
        return None
    await _run_batch(session, fanout, config, now)
    await session.flush()
    return fanout
