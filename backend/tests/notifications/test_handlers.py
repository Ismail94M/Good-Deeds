"""The notifications event handlers (docs/M6-DESIGN.md §4.1, B50, B51): each catalogue kind is
written once per event, for the right people, never for the actor; and how it reads."""

import re
import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import emit, handlers_for, run_handler
from app.modules.deeds import service as deeds
from app.modules.deeds.models import Deed
from app.modules.feed.models import CommentDeletedRole, FeedComment
from app.modules.feed.service import (
    CommentModerationDecided,
    CommentPosted,
    DeedLiked,
    UserFollowed,
)
from app.modules.fraud.service import MemberReviewUpdate, ReviewsSeen
from app.modules.identity.service import PasswordChanged
from app.modules.media.service import (
    AvatarModerationDecided,
    PhotoModerationDecided,
    PhotoRemovedByDoer,
)
from app.modules.notifications import handlers
from app.modules.notifications._limits import load_notifications
from app.modules.notifications._reads import list_page
from app.modules.notifications.models import (
    MuteTarget,
    NotificationCategory,
    NotificationKind,
    NotificationMute,
)
from app.modules.notifications.schemas import NotificationItem
from app.modules.scoring.service import AwardWithdrawn, BadgeAwarded, HeroTitleAwarded
from tests.deeds.helpers import Member, members
from tests.feed.helpers import verified_deed, withdraw_directly
from tests.notifications.helpers import publish_notifications, rows_for

K = NotificationKind
NOTIFICATIONS = "app.modules.notifications."


async def verified_event(
    session: AsyncSession, deed_id: uuid.UUID, method: str = "qr_scan"
) -> deeds.DeedVerified:
    deed = await session.get(Deed, deed_id)
    assert deed is not None and deed.doer_user_id is not None and deed.verified_at is not None
    assert deed.same_act_group_id is not None
    return deeds.DeedVerified(
        deed_id=deed.id,
        reporter_id=deed.reporter_user_id,
        doer_id=deed.doer_user_id,
        method="invite_accept" if method == "invite_accept" else "qr_scan",
        verified_at=deed.verified_at,
        same_act_group_id=deed.same_act_group_id,
        counts_for_points=True,
        prize_rank_eligible=True,
        limits_version=1,
    )


async def items_for(session: AsyncSession, member: Member) -> list[NotificationItem]:
    _, config = await load_notifications(session)
    page = await list_page(
        session,
        viewer_id=member.user_id,
        viewer_handle=member.handle,
        unread_only=False,
        cursor=None,
        config=config,
    )
    return page.items


async def comment(
    session: AsyncSession,
    deed_id: uuid.UUID,
    author: Member,
    *,
    at: datetime | None = None,
    deleted: bool = False,
) -> uuid.UUID:
    now = at or datetime.now(UTC)
    row = FeedComment(
        deed_id=deed_id,
        author_id=author.user_id,
        body=None if deleted else "So kind!",
        created_at=now,
        deleted_at=now if deleted else None,
        deleted_by=author.user_id if deleted else None,
        deleted_role=CommentDeletedRole.AUTHOR if deleted else None,
    )
    session.add(row)
    await session.flush()
    return row.id


async def posted(session: AsyncSession, deed_id: uuid.UUID, author: Member) -> uuid.UUID:
    """A new comment and its ``CommentPosted`` handled."""
    comment_id = await comment(session, deed_id, author)
    await handlers.on_comment_posted(
        session, CommentPosted(deed_id=deed_id, comment_id=comment_id, author_id=author.user_id)
    )
    await session.flush()
    return comment_id


# --------------------------------------------------- deeds


async def test_recognised_goes_to_the_doer_once_through_the_outbox(session: AsyncSession) -> None:
    doer, reporter = await members(session, 2)
    deed_id = await verified_deed(session, reporter, doer)
    row = emit(session, await verified_event(session, deed_id))
    await session.flush()
    ours = [n for n in handlers_for(row.event_type) if n.startswith(NOTIFICATIONS)]
    assert ours == ["app.modules.notifications.handlers.on_deed_verified"]
    assert await run_handler(session, row.id, ours[0])
    assert not await run_handler(session, row.id, ours[0])
    # Handling the event again by hand is harmless too (dedupe key).
    await handlers.on_deed_verified(session, await verified_event(session, deed_id))
    [notice] = await rows_for(session, doer)
    assert notice.kind == K.RECOGNISED and notice.actor_ids == [reporter.user_id]
    assert notice.deed_id == deed_id and notice.category == NotificationCategory.ABOUT_YOU
    # A QR reporter's deed is verified as they send it: nothing for them (A21).
    assert await rows_for(session, reporter) == []
    [item] = await items_for(session, doer)
    assert item.text == f"{reporter.display_name} recognised you for a good deed: Animals."
    assert item.link is not None and item.link.kind == "deed"


async def test_invite_deed_tells_the_reporter_it_was_verified(session: AsyncSession) -> None:
    doer, reporter = await members(session, 2)
    deed_id = await verified_deed(session, reporter, doer)
    await handlers.on_deed_verified(
        session, await verified_event(session, deed_id, "invite_accept")
    )
    await session.flush()
    [mine] = await rows_for(session, reporter)
    assert mine.kind == K.DEED_VERIFIED and mine.actor_ids == [doer.user_id]
    [item] = await items_for(session, reporter)
    assert item.text == f"Your deed about {doer.display_name} was verified."


async def test_withdrawal_sends_nothing_and_the_row_never_names_anyone(
    session: AsyncSession,
) -> None:
    """PO Q93: no notification on withdrawal; the recognition reads "This deed is no longer
    available", with no names, no link and no mute options."""
    doer, reporter = await members(session, 2)
    deed_id = await verified_deed(session, reporter, doer)
    await handlers.on_deed_verified(session, await verified_event(session, deed_id))
    await session.flush()
    await withdraw_directly(session, deed_id)
    assert not any(
        n.startswith(NOTIFICATIONS) for n in handlers_for(deeds.DeedWithdrawn.event_type)
    )
    [item] = await items_for(session, doer)
    assert item.text == "This deed is no longer available"
    assert item.actors == [] and item.actor_count == 0 and item.link is None
    assert reporter.display_name not in item.text
    assert item.can_mute.person is None and item.can_mute.deed is None
    assert await rows_for(session, reporter) == []


# --------------------------------------------------- feed


async def test_likes_go_to_doer_and_reporter_grouped_with_names(session: AsyncSession) -> None:
    doer, reporter, a, b, c = await members(session, 5)
    deed_id = await verified_deed(session, reporter, doer)
    for liker in (a, b, c, a):
        await handlers.on_deed_liked(session, DeedLiked(deed_id=deed_id, user_id=liker.user_id))
    await session.flush()
    for person, phrase in ((doer, "the deed about you"), (reporter, "the deed you recognised")):
        [row] = await rows_for(session, person)
        assert row.kind == K.LIKE and row.actor_count == 3
        [item] = await items_for(session, person)
        assert item.text == (
            f"{c.display_name}, {b.display_name} and {a.display_name} liked {phrase}."
        )
        assert item.can_mute.deed == deed_id
    # The reporter liking their own deed notifies only the doer.
    other = await verified_deed(session, reporter, doer)
    await handlers.on_deed_liked(session, DeedLiked(deed_id=other, user_id=reporter.user_id))
    await session.flush()
    assert len(await rows_for(session, doer)) == 2
    assert len(await rows_for(session, reporter)) == 1


async def test_comments_and_also_commented_replies(session: AsyncSession) -> None:
    doer, reporter, a, b, c = await members(session, 5)
    deed_id = await verified_deed(session, reporter, doer)
    await posted(session, deed_id, a)
    await posted(session, deed_id, b)
    # The doer and reporter get one comment row each, grouped.
    for person in (doer, reporter):
        [row] = await rows_for(session, person)
        assert row.kind == K.COMMENT and row.actor_count == 2
    # a commented before b: a gets a reply row; b doesn't hear about their own comment.
    [reply] = await rows_for(session, a)
    assert reply.kind == K.REPLY and reply.actor_ids == [b.user_id]
    assert await rows_for(session, b) == []
    [item] = await items_for(session, a)
    assert item.text == f"{b.display_name} also commented on a deed you commented on."
    # The doer commenting adds to a's and b's replies, never a reply row for the doer.
    await posted(session, deed_id, doer)
    assert [r.kind for r in await rows_for(session, doer)] == [K.COMMENT]
    assert (await rows_for(session, a))[0].actor_count == 2
    assert [r.kind for r in await rows_for(session, b)] == [K.REPLY]
    # c muted the deed: c comments, then a comments; c hears nothing.
    session.add(NotificationMute(user_id=c.user_id, target_type=MuteTarget.DEED, target_id=deed_id))
    await posted(session, deed_id, c)
    await posted(session, deed_id, a)
    assert await rows_for(session, c) == []


async def test_replies_skip_old_and_deleted_comments_and_can_be_switched_off(
    session: AsyncSession,
) -> None:
    doer, reporter, old, gone, late = await members(session, 5)
    deed_id = await verified_deed(session, reporter, doer)
    _, config = await load_notifications(session)
    lookback = timedelta(days=config.replies.lookback_days)
    await comment(session, deed_id, old, at=datetime.now(UTC) - lookback - timedelta(hours=1))
    await comment(session, deed_id, gone, deleted=True)
    await posted(session, deed_id, late)
    assert await rows_for(session, old) == []
    assert await rows_for(session, gone) == []

    await publish_notifications(session, replies={"enabled": False})
    await posted(session, deed_id, doer)
    assert await rows_for(session, late) == []


async def test_also_commented_reaches_at_most_the_configured_recent_commenters(
    session: AsyncSession,
) -> None:
    """SEC6-M1: per new comment, at most ``replies.max_recipients_per_comment`` earlier
    commenters hear about it, those who commented most recently first. The doer and reporter
    (who get their own comment rows) don't use up the cap; mutes still apply."""
    doer, reporter, author, *earlier = await members(session, 8)
    deed_id = await verified_deed(session, reporter, doer)
    _, config = await load_notifications(session)
    assert config.replies.max_recipients_per_comment == 200
    await publish_notifications(session, replies={"max_recipients_per_comment": 2})
    start = datetime.now(UTC) - timedelta(hours=2)
    for i, person in enumerate(earlier):  # earlier[-1] commented last
        await comment(session, deed_id, person, at=start + timedelta(minutes=i))
    await comment(session, deed_id, doer, at=start + timedelta(minutes=30))
    # The most recent commenter muted the deed: they still take a place, and hear nothing.
    session.add(
        NotificationMute(
            user_id=earlier[-1].user_id, target_type=MuteTarget.DEED, target_id=deed_id
        )
    )
    await posted(session, deed_id, author)

    told = [p for p in earlier if [r.kind for r in await rows_for(session, p)] == [K.REPLY]]
    assert told == [earlier[-2]]
    for person in earlier[:-2]:
        assert await rows_for(session, person) == []
    assert [r.kind for r in await rows_for(session, doer)] == [K.COMMENT]


async def test_new_followers_group_per_day(session: AsyncSession) -> None:
    target, a, b = await members(session, 3)
    for follower in (a, b, a):
        await handlers.on_user_followed(
            session, UserFollowed(follower_id=follower.user_id, followed_id=target.user_id)
        )
    await session.flush()
    [row] = await rows_for(session, target)
    assert row.kind == K.FOLLOW and row.actor_count == 2
    [item] = await items_for(session, target)
    assert item.text == f"{b.display_name} and {a.display_name} started following you."
    assert item.link is not None and item.link.kind == "connections"


async def test_comment_removed_tells_the_author(session: AsyncSession) -> None:
    doer, reporter, author = await members(session, 3)
    deed_id = await verified_deed(session, reporter, doer)
    comment_id = await comment(session, deed_id, author)
    for action in ("keep", "remove", "remove"):
        await handlers.on_comment_moderation(
            session,
            CommentModerationDecided(
                comment_id=comment_id,
                case_id=None,
                deed_id=deed_id,
                author_id=author.user_id,
                action=action,
            ),
        )
    await session.flush()
    [row] = await rows_for(session, author)
    assert row.kind == K.COMMENT_REMOVED and row.category == NotificationCategory.ACCOUNT
    [item] = await items_for(session, author)
    assert item.link is not None and item.link.kind == "guidelines"


# --------------------------------------------------- media


async def test_photo_outcomes_go_to_the_uploader(session: AsyncSession) -> None:
    doer, reporter = await members(session, 2)
    deed_id = await verified_deed(session, reporter, doer)
    kept, removed = uuid.uuid4(), uuid.uuid4()
    for photo_id, action in ((kept, "keep"), (removed, "remove"), (removed, "restore")):
        await handlers.on_photo_moderation(
            session,
            PhotoModerationDecided(
                photo_id=photo_id,
                case_id=uuid.uuid4(),
                deed_id=deed_id,
                uploader_id=reporter.user_id,
                action=action,
                removal_reason=None,
            ),
        )
    await handlers.on_photo_removed_by_doer(
        session,
        PhotoRemovedByDoer(
            photo_id=uuid.uuid4(),
            deed_id=deed_id,
            uploader_id=reporter.user_id,
            doer_id=doer.user_id,
        ),
    )
    await session.flush()
    kinds = sorted(r.kind.value for r in await rows_for(session, reporter))
    assert kinds == ["photo_kept", "photo_removed", "photo_removed_by_doer"]
    texts = {i.text for i in await items_for(session, reporter)}
    assert f"{doer.display_name} removed 1 photo from this deed." in texts
    assert await rows_for(session, doer) == []


async def test_avatar_removal_tells_the_owner_why(session: AsyncSession) -> None:
    [owner] = await members(session, 1)
    photo_id = uuid.uuid4()
    for action in ("keep", "remove", "restore"):
        await handlers.on_avatar_moderation(
            session,
            AvatarModerationDecided(
                photo_id=photo_id,
                case_id=None,
                user_id=owner.user_id,
                action=action,
                removal_reason="impersonation",
            ),
        )
    await session.flush()
    [row] = await rows_for(session, owner)
    assert row.kind == K.AVATAR_REMOVED and row.data == {"removal_reason": "impersonation"}
    [item] = await items_for(session, owner)
    assert item.text == (
        "Your profile picture was removed because it didn't follow our photo guidelines. "
        "Reason: Not this person / impersonation."
    )
    assert item.link is not None and (item.link.kind, item.link.target) == ("guidelines", "photo")


# --------------------------------------------------- scoring


async def test_badges_use_the_label_from_the_event(session: AsyncSession) -> None:
    [owner] = await members(session, 1)
    labelled = BadgeAwarded(user_id=owner.user_id, badge_slug="kind_5", badge_label="5 deeds")
    await handlers.on_badge_awarded(session, labelled)
    await handlers.on_badge_awarded(session, labelled)
    # An event written before ``badge_label`` existed still loads and reads neutrally.
    old = BadgeAwarded.model_validate({"user_id": str(owner.user_id), "badge_slug": "kind_25"})
    assert old.badge_label is None
    await handlers.on_badge_awarded(session, old)
    await session.flush()
    texts = sorted(i.text for i in await items_for(session, owner))
    assert texts == ["You earned a new badge.", "You earned the 5 deeds badge."]


async def test_titles_read_with_region_and_month(session: AsyncSession) -> None:
    [owner] = await members(session, 1)
    city = await deeds.default_region(session)
    month = date(2026, 10, 1)
    for level, key, label in (
        ("ward", "hero", "Kindness Hero · {region} · {month}"),
        ("city", "hero", "{region} City Kindness Hero · {month}"),
    ):
        await handlers.on_title_awarded(
            session,
            HeroTitleAwarded(
                user_id=owner.user_id,
                period_month=month,
                region_id=city.id,
                level_kind=level,
                place=1,
                title_key=key,
                title_label=label,
            ),
        )
    await session.flush()
    texts = sorted(i.text for i in await items_for(session, owner))
    assert texts == [
        f"You earned the title {city.name} City Kindness Hero · October 2026!",
        f"You're Kindness Hero of {city.name} for October 2026!",
    ]


async def test_a_withdrawn_title_or_badge_reads_no_longer_available(
    session: AsyncSession,
) -> None:
    """A title or badge revoked later (scoring's member-safe ``AwardWithdrawn``): the award row
    reads "This is no longer available", without the title, the badge or a link. Other awards
    are untouched; an award heard after its withdrawal writes nothing."""
    owner, other = await members(session, 2)
    city = await deeds.default_region(session)
    month = date(2026, 10, 1)
    title = HeroTitleAwarded(
        user_id=owner.user_id,
        period_month=month,
        region_id=city.id,
        level_kind="ward",
        place=1,
        title_key="hero",
        title_label="Kindness Hero · {region} · {month}",
    )
    await handlers.on_title_awarded(session, title)
    await handlers.on_title_awarded(session, title.model_copy(update={"user_id": other.user_id}))
    for slug in ("kind_5", "kind_25"):
        await handlers.on_badge_awarded(
            session, BadgeAwarded(user_id=owner.user_id, badge_slug=slug, badge_label=slug)
        )
    await session.flush()

    await handlers.on_award_withdrawn(
        session,
        AwardWithdrawn(
            user_id=owner.user_id,
            award="title",
            period_month=month,
            level_kind="ward",
            region_id=city.id,
            title_key="hero",
        ),
    )
    await handlers.on_award_withdrawn(
        session, AwardWithdrawn(user_id=owner.user_id, award="badge", badge_slug="kind_5")
    )
    await session.flush()
    items = {i.text: i for i in await items_for(session, owner)}
    assert set(items) == {"This is no longer available", "You earned the kind_25 badge."}
    gone = items["This is no longer available"]
    assert gone.link is None and gone.actors == []
    assert len(await rows_for(session, owner)) == 3  # rows stay; two read neutrally
    assert city.name not in " ".join(items)
    # Someone else's same title is theirs, still shown.
    [theirs] = await items_for(session, other)
    assert theirs.text == f"You're Kindness Hero of {city.name} for October 2026!"

    # Heard out of order: the withdrawal first, then the award: no row at all.
    await handlers.on_award_withdrawn(
        session, AwardWithdrawn(user_id=other.user_id, award="badge", badge_slug="kind_100")
    )
    await handlers.on_badge_awarded(
        session, BadgeAwarded(user_id=other.user_id, badge_slug="kind_100", badge_label="100")
    )
    await session.flush()
    assert [r.kind for r in await rows_for(session, other)] == [K.TITLE_AWARDED]


# --------------------------------------------------- identity


async def test_password_changed_is_an_account_row_each_time(session: AsyncSession) -> None:
    [owner] = await members(session, 1)
    at = datetime.now(UTC)
    for when in (at, at, at + timedelta(minutes=5)):
        await handlers.on_password_changed(
            session, PasswordChanged(user_id=owner.user_id, method="phone_otp", changed_at=when)
        )
    await session.flush()
    rows = await rows_for(session, owner)
    assert [r.kind for r in rows] == [K.PASSWORD_CHANGED] * 2
    assert rows[0].category == NotificationCategory.ACCOUNT


# --------------------------------------------------- reviews


FORBIDDEN = re.compile(r"fraud|flag|case|ring|detector|reciprocal|concentration|admin", re.I)


async def test_review_updates_are_neutral_and_link_to_reviews(session: AsyncSession) -> None:
    [owner] = await members(session, 1)
    due = datetime(2026, 12, 5, 6, 0, tzinfo=UTC)
    for kind in (
        "explanation_requested",
        "explanation_reminder",
        "decision_notice",
        "appeal_decided",
        "review_closed",
    ):
        event = MemberReviewUpdate(
            user_id=owner.user_id,
            kind=kind,
            item_id=uuid.uuid4(),
            reply_due_at=due if kind.startswith("explanation") else None,
        )
        await handlers.on_review_update(session, event)
        await handlers.on_review_update(session, event)
    await session.flush()
    items = await items_for(session, owner)
    assert len(items) == 5
    texts = sorted(i.text for i in items)
    assert texts == [
        "A reminder: we'd like to hear from you about some of your recent deeds. "
        "Please reply by 5 December.",
        "There's an update for you in Reviews.",
        "There's an update for you in Reviews.",
        "There's an update for you in Reviews.",
        "We'd like to hear from you about some of your recent deeds. Please reply by 5 December.",
    ]
    for item in items:
        assert not FORBIDDEN.search(item.text), item.text
        assert item.link is not None and item.link.kind == "reviews"
        assert item.actors == [] and item.category == "reviews"


async def test_review_kinds_come_from_config(session: AsyncSession) -> None:
    [owner] = await members(session, 1)
    await publish_notifications(session, review_kinds=["explanation_requested"])
    for kind in ("explanation_requested", "review_closed"):
        await handlers.on_review_update(
            session,
            MemberReviewUpdate(user_id=owner.user_id, kind=kind, item_id=uuid.uuid4()),
        )
    await session.flush()
    rows = await rows_for(session, owner)
    assert [r.data["review_kind"] for r in rows] == ["explanation_requested"]


async def test_opening_reviews_marks_review_rows_read(session: AsyncSession) -> None:
    owner, reporter = await members(session, 2)
    deed_id = await verified_deed(session, reporter, owner)
    await handlers.on_deed_verified(session, await verified_event(session, deed_id))
    await handlers.on_review_update(
        session,
        MemberReviewUpdate(user_id=owner.user_id, kind="decision_notice", item_id=uuid.uuid4()),
    )
    await session.flush()
    seen = datetime.now(UTC) + timedelta(seconds=1)
    await handlers.on_reviews_seen(session, ReviewsSeen(user_id=owner.user_id, seen_at=seen))
    # A later update stays unread.
    await handlers.on_review_update(
        session,
        MemberReviewUpdate(user_id=owner.user_id, kind="appeal_decided", item_id=uuid.uuid4()),
    )
    await session.flush()
    rows = {r.data.get("review_kind", r.kind.value): r for r in await rows_for(session, owner)}
    assert rows["decision_notice"].read_at is not None
    assert rows["recognised"].read_at is None  # only review rows
    assert rows["appeal_decided"].read_at is None
