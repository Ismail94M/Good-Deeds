"""Event handlers of the notifications module (docs/M6-DESIGN.md §4.1, §5, D106 to D115).

Each handler turns one event into rows through ``_write.notify``, in the handler's transaction
(``run_handler`` records the event as processed in the same one, so a re-run writes nothing).
``notify`` skips the actor themselves, inactive people, switched-off categories and mutes.
Rows hold ids, codes and numbers only; the text is built on read (``_render``).

Never subscribe to ``FlagRaised``, ``FlagCaseResolved``, ``DeedHoldChanged``,
``FlagDecisionFinal``, ``HeroTitleRevoked`` or ``PointsChanged`` (D112;
``tests/test_architecture.py`` checks it). Withdrawals, hides and points changes are never
notified (PO Q93): the earlier recognition row reads "This deed is no longer available".
"""

import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import cast, func, select, update
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import subscribe
from app.modules.deeds import service as deeds
from app.modules.feed.service import (
    CommentModerationDecided,
    CommentPosted,
    DeedLiked,
    UserFollowed,
    earlier_commenter_ids,
)
from app.modules.fraud.service import MemberReviewUpdate, ReviewsSeen
from app.modules.identity import service as identity
from app.modules.media.service import (
    AvatarModerationDecided,
    PhotoModerationDecided,
    PhotoRemovedByDoer,
)
from app.modules.notifications._fanout import start_fanout
from app.modules.notifications._limits import NotificationsConfig, load_notifications
from app.modules.notifications._write import Notice, notify
from app.modules.notifications.models import Notification, NotificationDedupe, NotificationKind
from app.modules.scoring.service import AwardWithdrawn, BadgeAwarded, HeroTitleAwarded

K = NotificationKind


def _now() -> datetime:
    return datetime.now(UTC)


async def _config(session: AsyncSession) -> NotificationsConfig:
    _, config = await load_notifications(session)
    return config


async def _to_parties(
    session: AsyncSession,
    kind: NotificationKind,
    deed_id: uuid.UUID,
    actor_id: uuid.UUID,
    *,
    ref_id: uuid.UUID | None = None,
    config: NotificationsConfig,
    now: datetime,
) -> set[uuid.UUID]:
    """Notify the deed's doer and reporter (never the actor). Returns who they are."""
    deed = await deeds.get_deed(session, deed_id)
    if deed is None or deed.doer_id is None:
        return set()
    parties = {deed.doer_id, deed.reporter_id}
    for recipient in sorted(parties):
        await notify(
            session,
            Notice(
                kind=kind,
                recipient_id=recipient,
                actor_id=actor_id,
                deed_id=deed_id,
                ref_id=ref_id,
            ),
            config=config,
            now=now,
        )
    return parties


# --------------------------------------------------- deeds


@subscribe(deeds.DeedVerified)
async def on_deed_verified(session: AsyncSession, event: deeds.DeedVerified) -> None:
    """The doer: "{reporter} recognised you". The reporter of an invite deed: "Your deed about
    {doer} was verified" (A21; a QR deed is verified as it is sent). Then the network fan-out
    (D110), done by ``notifications.fanout_network``."""
    config, now = await _config(session), _now()
    await notify(
        session,
        Notice(
            kind=K.RECOGNISED,
            recipient_id=event.doer_id,
            actor_id=event.reporter_id,
            deed_id=event.deed_id,
            dedupe_key=f"recognised:{event.deed_id}",
        ),
        config=config,
        now=now,
    )
    if event.method == "invite_accept":
        await notify(
            session,
            Notice(
                kind=K.DEED_VERIFIED,
                recipient_id=event.reporter_id,
                actor_id=event.doer_id,
                deed_id=event.deed_id,
                dedupe_key=f"deed_verified:{event.deed_id}",
            ),
            config=config,
            now=now,
        )
    await start_fanout(session, event, config=config, now=now)


# --------------------------------------------------- feed


@subscribe(DeedLiked)
async def on_deed_liked(session: AsyncSession, event: DeedLiked) -> None:
    """Likes, grouped per deed with the likers' names, to the doer and reporter (Q92)."""
    config, now = await _config(session), _now()
    await _to_parties(session, K.LIKE, event.deed_id, event.user_id, config=config, now=now)


@subscribe(CommentPosted)
async def on_comment_posted(session: AsyncSession, event: CommentPosted) -> None:
    """``comment`` to the doer and reporter; ``reply`` ("also commented", D111, Q90) to people
    who commented on the deed earlier, in the last ``replies.lookback_days``: at most
    ``replies.max_recipients_per_comment`` of them, those who commented most recently first,
    so one popular deed can't make a handler run thousands of writes (SEC6-M1)."""
    config, now = await _config(session), _now()
    parties = await _to_parties(
        session,
        K.COMMENT,
        event.deed_id,
        event.author_id,
        ref_id=event.comment_id,
        config=config,
        now=now,
    )
    if not config.replies.enabled or not parties:
        return
    earlier = await earlier_commenter_ids(
        session,
        event.deed_id,
        before=event.comment_id,
        since=now - timedelta(days=config.replies.lookback_days),
        exclude=parties | {event.author_id},
        limit=config.replies.max_recipients_per_comment,
    )
    for recipient in sorted(earlier - parties - {event.author_id}):
        await notify(
            session,
            Notice(
                kind=K.REPLY,
                recipient_id=recipient,
                actor_id=event.author_id,
                deed_id=event.deed_id,
                ref_id=event.comment_id,
            ),
            config=config,
            now=now,
        )


@subscribe(UserFollowed)
async def on_user_followed(session: AsyncSession, event: UserFollowed) -> None:
    """New followers, grouped per day."""
    config, now = await _config(session), _now()
    await notify(
        session,
        Notice(kind=K.FOLLOW, recipient_id=event.followed_id, actor_id=event.follower_id),
        config=config,
        now=now,
    )


@subscribe(CommentModerationDecided)
async def on_comment_moderation(session: AsyncSession, event: CommentModerationDecided) -> None:
    """A removed comment: the author is told, with a link to the community guidelines."""
    if event.action != "remove":
        return
    config, now = await _config(session), _now()
    await notify(
        session,
        Notice(
            kind=K.COMMENT_REMOVED,
            recipient_id=event.author_id,
            deed_id=event.deed_id,
            ref_id=event.comment_id,
            dedupe_key=f"comment_removed:{event.comment_id}:{event.case_id}",
        ),
        config=config,
        now=now,
    )


# --------------------------------------------------- media


@subscribe(PhotoModerationDecided)
async def on_photo_moderation(session: AsyncSession, event: PhotoModerationDecided) -> None:
    """The uploader hears the outcome of a review of their photo (keep or remove)."""
    kinds = {"keep": K.PHOTO_KEPT, "remove": K.PHOTO_REMOVED}
    kind = kinds.get(event.action)
    if kind is None:
        return
    config, now = await _config(session), _now()
    await notify(
        session,
        Notice(
            kind=kind,
            recipient_id=event.uploader_id,
            deed_id=event.deed_id,
            ref_id=event.photo_id,
            dedupe_key=f"{kind.value}:{event.photo_id}:{event.case_id}",
        ),
        config=config,
        now=now,
    )


@subscribe(PhotoRemovedByDoer)
async def on_photo_removed_by_doer(session: AsyncSession, event: PhotoRemovedByDoer) -> None:
    config, now = await _config(session), _now()
    await notify(
        session,
        Notice(
            kind=K.PHOTO_REMOVED_BY_DOER,
            recipient_id=event.uploader_id,
            actor_id=event.doer_id,
            deed_id=event.deed_id,
            ref_id=event.photo_id,
            dedupe_key=f"photo_removed_by_doer:{event.photo_id}",
        ),
        config=config,
        now=now,
    )


@subscribe(AvatarModerationDecided)
async def on_avatar_moderation(session: AsyncSession, event: AvatarModerationDecided) -> None:
    """An admin removed a profile picture: the owner is told why, with the photo guidelines
    (Q95). Keep and restore send nothing."""
    if event.action != "remove":
        return
    config, now = await _config(session), _now()
    await notify(
        session,
        Notice(
            kind=K.AVATAR_REMOVED,
            recipient_id=event.user_id,
            ref_id=event.photo_id,
            data={"removal_reason": event.removal_reason} if event.removal_reason else {},
            dedupe_key=f"avatar_removed:{event.photo_id}:{event.case_id}",
        ),
        config=config,
        now=now,
    )


# --------------------------------------------------- scoring


def _badge_key(slug: str) -> str:
    return f"badge:{slug}"


def _title_key(month: date, level_kind: str, region_id: int, title_key: str) -> str:
    return f"title:{month.isoformat()}:{level_kind}:{region_id}:{title_key}"


_WITHDRAWN = "withdrawn"


async def _was_withdrawn(session: AsyncSession, user_id: uuid.UUID, key: str) -> bool:
    """The award was withdrawn before its award event was handled (handlers may run out of
    order): don't write the award row at all."""
    found = await session.scalar(
        select(NotificationDedupe.key).where(
            NotificationDedupe.user_id == user_id,
            NotificationDedupe.key == f"{_WITHDRAWN}:{key}",
        )
    )
    return found is not None


@subscribe(BadgeAwarded)
async def on_badge_awarded(session: AsyncSession, event: BadgeAwarded) -> None:
    config, now = await _config(session), _now()
    key = _badge_key(event.badge_slug)
    if await _was_withdrawn(session, event.user_id, key):
        return
    data: dict[str, str] = {"badge_slug": event.badge_slug}
    if event.badge_label:
        data["badge_label"] = event.badge_label
    await notify(
        session,
        Notice(
            kind=K.BADGE_AWARDED,
            recipient_id=event.user_id,
            data=data,
            dedupe_key=key,
        ),
        config=config,
        now=now,
    )


@subscribe(AwardWithdrawn)
async def on_award_withdrawn(session: AsyncSession, event: AwardWithdrawn) -> None:
    """A title or badge the person no longer holds (revoked after a final review decision):
    its award row reads "This is no longer available", without the title or badge (D109's rule
    for deeds, applied to awards). No new notification is sent. ``AwardWithdrawn`` is scoring's
    member-safe event (no case or reason); ``HeroTitleRevoked`` stays unheard (D112)."""
    if event.award == "badge" and event.badge_slug:
        key = _badge_key(event.badge_slug)
    elif (
        event.award == "title"
        and event.period_month is not None
        and event.level_kind is not None
        and event.region_id is not None
        and event.title_key is not None
    ):
        key = _title_key(event.period_month, event.level_kind, event.region_id, event.title_key)
    else:
        return
    await session.execute(
        insert(NotificationDedupe)
        .values(user_id=event.user_id, key=f"{_WITHDRAWN}:{key}", created_at=_now())
        .on_conflict_do_nothing()
    )
    await session.execute(
        update(Notification)
        .where(
            Notification.user_id == event.user_id,
            Notification.kind.in_([K.BADGE_AWARDED, K.TITLE_AWARDED]),
            Notification.dedupe_key == key,
        )
        .values(data=Notification.data.op("||")(cast({_WITHDRAWN: True}, JSONB)))
        .execution_options(synchronize_session=False)
    )


@subscribe(HeroTitleAwarded)
async def on_title_awarded(session: AsyncSession, event: HeroTitleAwarded) -> None:
    config, now = await _config(session), _now()
    if await _was_withdrawn(
        session,
        event.user_id,
        _title_key(event.period_month, event.level_kind, event.region_id, event.title_key),
    ):
        return
    data: dict[str, str | int] = {
        "period_month": event.period_month.isoformat(),
        "region_id": event.region_id,
        "level_kind": event.level_kind,
        "place": event.place,
        "title_key": event.title_key,
    }
    if event.title_label:
        data["title_label"] = event.title_label
    await notify(
        session,
        Notice(
            kind=K.TITLE_AWARDED,
            recipient_id=event.user_id,
            data=data,
            dedupe_key=_title_key(
                event.period_month, event.level_kind, event.region_id, event.title_key
            ),
        ),
        config=config,
        now=now,
    )


# --------------------------------------------------- identity


@subscribe(identity.PasswordChanged)
async def on_password_changed(session: AsyncSession, event: identity.PasswordChanged) -> None:
    """In-app "Your password was changed" (A26); the email comes in M6B."""
    config, now = await _config(session), _now()
    at = event.changed_at.isoformat() if event.changed_at else uuid.uuid4().hex
    await notify(
        session,
        Notice(
            kind=K.PASSWORD_CHANGED,
            recipient_id=event.user_id,
            dedupe_key=f"password_changed:{at}",
        ),
        config=config,
        now=now,
    )


# --------------------------------------------------- reviews (fraud's member-safe events only)


@subscribe(MemberReviewUpdate)
async def on_review_update(session: AsyncSession, event: MemberReviewUpdate) -> None:
    """Neutral "There's an update for you in Reviews" (D112, D101), linking to Profile ->
    Reviews. Which kinds notify is ``review_kinds`` (Q91: all five)."""
    config, now = await _config(session), _now()
    if event.kind not in config.review_kinds:
        return
    data: dict[str, str] = {"review_kind": event.kind}
    if event.reply_due_at is not None:
        data["reply_due_at"] = event.reply_due_at.isoformat()
    await notify(
        session,
        Notice(
            kind=K.REVIEW_UPDATE,
            recipient_id=event.user_id,
            ref_id=event.item_id,
            data=data,
            dedupe_key=f"review:{event.kind}:{event.item_id}",
        ),
        config=config,
        now=now,
    )


@subscribe(ReviewsSeen)
async def on_reviews_seen(session: AsyncSession, event: ReviewsSeen) -> None:
    """Opening Reviews marks the member's review rows written up to then as read (D112)."""
    await session.execute(
        update(Notification)
        .where(
            Notification.user_id == event.user_id,
            Notification.kind == K.REVIEW_UPDATE,
            Notification.read_at.is_(None),
            Notification.updated_at <= event.seen_at,
        )
        .values(read_at=func.greatest(Notification.created_at, event.seen_at))
        .execution_options(synchronize_session=False)
    )
