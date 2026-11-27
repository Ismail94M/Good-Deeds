"""QA12: notification rules, boundaries and abuse (docs/M6-DESIGN.md §8 "Notifications" and
"Reviews (D101)"; PO Q89 to Q93 and the approved defaults). Complements the other files here."""

import json
import re
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import events
from app.core.events import DomainEvent, OutboxEvent, emit, handlers_for, run_handler
from app.modules.deeds import service as deeds
from app.modules.feed.service import (
    CommentModerationDecided,
    CommentPosted,
    DeedLiked,
    UserFollowed,
)
from app.modules.fraud._limits import load_fraud
from app.modules.fraud._reminders import remind_explanations
from app.modules.fraud.service import MemberReviewUpdate
from app.modules.identity.service import PasswordChanged
from app.modules.media.service import (
    AvatarModerationDecided,
    PhotoModerationDecided,
    PhotoRemovedByDoer,
)
from app.modules.notifications import handlers
from app.modules.notifications._limits import load_notifications
from app.modules.notifications._write import Outcome
from app.modules.notifications.models import Notification, NotificationKind
from app.modules.registry import load_modules
from app.modules.scoring.service import AwardWithdrawn, BadgeAwarded, HeroTitleAwarded
from tests.deeds.helpers import Member, make_member, members
from tests.feed.helpers import verified_deed, withdraw_directly
from tests.fraud.appeal_helpers import appeal, appeal_of, confirm, decide_appeal, notice_of, pair
from tests.fraud.review_helpers import admin, ask, reciprocal_case, ring_case
from tests.fraud.test_review_updates import dismiss
from tests.notifications.helpers import rows_for, send
from tests.notifications.test_handlers import comment, posted, verified_event

K = NotificationKind
PREFIX = "app.modules.notifications."
UNAVAILABLE_DEED = "This deed is no longer available"
# Words a member must never read about a review (M5 D101), as whole words.
FORBIDDEN = re.compile(
    r"\b(fraud\w*|flag\w*|case\w*|ring|rings|detector\w*|reciprocal|concentration|"
    r"admin\w*|moderator\w*|suspic\w*|collu\w*|cheat\w*|investigat\w*)\b",
    re.I,
)


async def _list(client: AsyncClient, member: Member) -> tuple[str, list[dict[str, Any]]]:
    resp = await client.get("/api/notifications", headers=member.headers)
    assert resp.status_code == 200, resp.text
    items: list[dict[str, Any]] = resp.json()["items"]
    return resp.text, items


async def _run_ours(session: AsyncSession, rows: list[OutboxEvent]) -> int:
    """Run the notifications handlers for these outbox rows (as the worker would)."""
    ran = 0
    for row in rows:
        for name in handlers_for(row.event_type):
            if name.startswith(PREFIX) and await run_handler(session, row.id, name):
                ran += 1
    await session.flush()
    return ran


# --------------------------------------------------- what notifications hears


def test_notifications_hears_exactly_the_catalogue_events() -> None:
    """§4.1/§5: only these events. Not DeedWithdrawn (Q93), DeedRejected, DeedHoldChanged,
    FlagRaised and the other case events (D112), PointsChanged, or anything about a pending
    invite deed (only ``DeedVerified`` names a doer)."""
    load_modules()
    heard = {
        t for t in events._event_types if any(name.startswith(PREFIX) for name in handlers_for(t))
    }
    assert heard == {
        deeds.DeedVerified.event_type,
        DeedLiked.event_type,
        CommentPosted.event_type,
        UserFollowed.event_type,
        CommentModerationDecided.event_type,
        PhotoModerationDecided.event_type,
        PhotoRemovedByDoer.event_type,
        AvatarModerationDecided.event_type,
        BadgeAwarded.event_type,
        HeroTitleAwarded.event_type,
        # Member-safe twin of the revocations (no case data): award rows read neutrally.
        AwardWithdrawn.event_type,
        PasswordChanged.event_type,
        MemberReviewUpdate.event_type,
        "fraud.reviews_seen",
    }


# --------------------------------------------------- idempotency


async def test_every_handler_rerun_or_redelivered_writes_each_row_once(
    session: AsyncSession,
) -> None:
    doer, reporter, fan, author, liker = await members(session, 5)
    deed_id = await verified_deed(session, reporter, doer)
    earlier = await comment(session, deed_id, fan)
    comment_id = await comment(session, deed_id, author)
    assert earlier != comment_id
    photo_id, case_id = uuid.uuid4(), uuid.uuid4()
    region = await deeds.default_region(session)
    at = datetime.now(UTC)
    made: list[DomainEvent] = [
        await verified_event(session, deed_id),
        DeedLiked(deed_id=deed_id, user_id=liker.user_id),
        CommentPosted(deed_id=deed_id, comment_id=comment_id, author_id=author.user_id),
        UserFollowed(follower_id=liker.user_id, followed_id=doer.user_id),
        CommentModerationDecided(
            comment_id=comment_id,
            case_id=case_id,
            deed_id=deed_id,
            author_id=author.user_id,
            action="remove",
        ),
        PhotoModerationDecided(
            photo_id=photo_id,
            case_id=case_id,
            deed_id=deed_id,
            uploader_id=reporter.user_id,
            action="remove",
            removal_reason="private_details",
        ),
        PhotoRemovedByDoer(
            photo_id=uuid.uuid4(),
            deed_id=deed_id,
            uploader_id=reporter.user_id,
            doer_id=doer.user_id,
        ),
        AvatarModerationDecided(
            photo_id=uuid.uuid4(),
            case_id=case_id,
            user_id=fan.user_id,
            action="remove",
            removal_reason="impersonation",
        ),
        BadgeAwarded(user_id=doer.user_id, badge_slug="kind_5", badge_label="5 deeds"),
        HeroTitleAwarded(
            user_id=doer.user_id,
            period_month=date(2026, 10, 1),
            region_id=region.id,
            level_kind="ward",
            place=1,
            title_key="hero",
            title_label=None,
        ),
        PasswordChanged(user_id=reporter.user_id, method="phone_otp", changed_at=at),
        MemberReviewUpdate(
            user_id=liker.user_id, kind="decision_notice", item_id=uuid.uuid4(), reply_due_at=None
        ),
    ]
    first = [emit(session, e) for e in made]
    await session.flush()
    assert await _run_ours(session, first) >= len(made)
    people = (doer, reporter, fan, author, liker)

    async def snapshot() -> dict[uuid.UUID, list[tuple[str, int]]]:
        return {
            p.user_id: sorted((r.kind.value, r.actor_count) for r in await rows_for(session, p))
            for p in people
        }

    before = await snapshot()
    # A retry of the same jobs does nothing (processed_events) ...
    assert await _run_ours(session, first) == 0
    # ... and a duplicate delivery of the same events (new outbox rows) writes nothing new.
    again = [emit(session, e) for e in made]
    await session.flush()
    await _run_ours(session, again)
    assert await snapshot() == before
    # Every kind fired, each to the right person only.
    assert sorted(k for k, _ in before[doer.user_id]) == [
        "badge_awarded",
        "comment",
        "follow",
        "like",
        "recognised",
        "title_awarded",
    ]
    assert sorted(k for k, _ in before[reporter.user_id]) == [
        "comment",
        "like",
        "password_changed",
        "photo_removed",
        "photo_removed_by_doer",
    ]
    assert sorted(k for k, _ in before[fan.user_id]) == ["avatar_removed", "reply"]
    assert sorted(k for k, _ in before[author.user_id]) == ["comment_removed"]
    assert sorted(k for k, _ in before[liker.user_id]) == ["review_update"]


# --------------------------------------------------- Q93: withdrawal


async def test_withdrawal_turns_every_deed_row_into_no_names_and_sends_nothing(
    client: AsyncClient, session: AsyncSession
) -> None:
    doer, reporter, liker, commenter, earlier = await members(session, 5)
    deed_id = await verified_deed(session, reporter, doer)
    await handlers.on_deed_verified(session, await verified_event(session, deed_id))
    await handlers.on_deed_liked(session, DeedLiked(deed_id=deed_id, user_id=liker.user_id))
    await posted(session, deed_id, earlier)
    await posted(session, deed_id, commenter)
    counts = {p.user_id: len(await rows_for(session, p)) for p in (doer, reporter, earlier)}

    await withdraw_directly(session, deed_id)
    # Withdrawing creates no notification for anyone (no handler, no new row).
    for person in (doer, reporter, earlier):
        assert len(await rows_for(session, person)) == counts[person.user_id]
    names = [p.display_name for p in (doer, reporter, liker, commenter)] + [
        p.handle for p in (doer, reporter, liker, commenter)
    ]
    for person in (doer, reporter, earlier):
        raw, items = await _list(client, person)
        assert items, person.handle
        for item in items:
            assert item["text"] == UNAVAILABLE_DEED
            assert item["actors"] == [] and item["actor_count"] == 0 and item["link"] is None
            assert not any(item["can_mute"].values())
        for name in names:
            assert name not in raw
        assert str(deed_id) not in raw


# --------------------------------------------------- D106: no free text in rows or answers


async def test_comment_text_never_reaches_a_notification(
    client: AsyncClient, session: AsyncSession
) -> None:
    doer, reporter, author = await members(session, 3)
    deed_id = await verified_deed(session, reporter, doer)
    await posted(session, deed_id, author)
    for person in (doer, reporter):
        raw, _ = await _list(client, person)
        assert "So kind!" not in raw
        for row in await rows_for(session, person):
            assert "So kind" not in json.dumps(row.data)


# --------------------------------------------------- D113: mutes


async def test_muting_a_person_is_silent_and_keeps_recognitions(
    client: AsyncClient, session: AsyncSession
) -> None:
    me, them = await members(session, 2)
    await session.commit()
    resp = await client.put(
        "/api/notifications/mutes",
        json={"type": "person", "target": them.handle},
        headers=me.headers,
    )
    assert resp.status_code == 204
    # Silent: the muted person hears nothing, and no event is written about it.
    assert await rows_for(session, them) == []
    muted_events = await session.scalars(
        select(OutboxEvent.event_type).where(OutboxEvent.event_type.like("notifications.%"))
    )
    assert list(muted_events) == []
    # Their likes and follows are muted; their recognition of me still arrives.
    deed_id = await verified_deed(session, them, me)
    await handlers.on_deed_verified(session, await verified_event(session, deed_id))
    await handlers.on_deed_liked(session, DeedLiked(deed_id=deed_id, user_id=them.user_id))
    await handlers.on_user_followed(
        session, UserFollowed(follower_id=them.user_id, followed_id=me.user_id)
    )
    await session.flush()
    assert [r.kind for r in await rows_for(session, me)] == [K.RECOGNISED]


async def test_mutes_of_one_person_dont_affect_another(session: AsyncSession) -> None:
    doer, reporter, liker = await members(session, 3)
    deed_id = await verified_deed(session, reporter, doer)
    from app.modules.notifications.models import MuteTarget, NotificationMute

    session.add(
        NotificationMute(user_id=doer.user_id, target_type=MuteTarget.DEED, target_id=deed_id)
    )
    await session.flush()
    await handlers.on_deed_liked(session, DeedLiked(deed_id=deed_id, user_id=liker.user_id))
    await session.flush()
    assert await rows_for(session, doer) == []
    assert [r.kind for r in await rows_for(session, reporter)] == [K.LIKE]


# --------------------------------------------------- D115: caps at the seeded values


async def test_activity_cap_is_30_a_day_and_about_you_and_reviews_have_none(
    session: AsyncSession,
) -> None:
    me, reporter, actor = await members(session, 3)
    _, config = await load_notifications(session)
    assert config.daily_caps.activity == 30
    now = datetime.now(UTC)
    deed_ids = [await verified_deed(session, reporter, me) for _ in range(11)]
    outcomes = []
    for deed_id in deed_ids[:10]:
        for kind in (K.LIKE, K.COMMENT, K.REPLY):
            outcomes.append(await send(session, kind, me, actor=actor, deed_id=deed_id, now=now))
    assert outcomes == [Outcome.CREATED] * 30
    # The 31st new Activity row of the day is not created ...
    assert await send(session, K.LIKE, me, actor=actor, deed_id=deed_ids[10], now=now) == (
        Outcome.CAPPED
    )
    # ... but an existing unread group still grows.
    [other] = await members(session, 1)
    assert await send(session, K.LIKE, me, actor=other, deed_id=deed_ids[0], now=now) == (
        Outcome.GROUPED
    )
    # A new follower group is also Activity: capped too.
    assert await send(session, K.FOLLOW, me, actor=other, now=now) == Outcome.CAPPED
    # The capped like counts once the next day allows it (its claim was given back).
    tomorrow = now + timedelta(days=1)
    assert await send(session, K.LIKE, me, actor=actor, deed_id=deed_ids[10], now=tomorrow) == (
        Outcome.CREATED
    )
    for _ in range(35):
        assert await send(session, K.RECOGNISED, me, actor=reporter, now=now) == Outcome.CREATED
        assert (
            await send(session, K.REVIEW_UPDATE, me, data={"review_kind": "review_closed"}, now=now)
            == Outcome.CREATED
        )
    rows = await rows_for(session, me)
    assert sum(r.kind in {K.LIKE, K.COMMENT, K.REPLY, K.FOLLOW} for r in rows) == 31


# --------------------------------------------------- D101: an end-to-end text scan of reviews


async def test_review_updates_never_reveal_case_details_end_to_end(
    client: AsyncClient, session: AsyncSession
) -> None:
    """Real fraud flows emit the five kinds; the notifications handlers run from the outbox; and
    every review row each member can read is scanned: no fraud wording, no case id or type, no
    other subject and no admin, in the text or anywhere in the answer or the row."""
    decider, second = await admin(session), await admin(session)
    # Ring: decision notices for two, "finished looking" for the third.
    ring = await ring_case(session)
    first, middle, third = ring.deeds
    await confirm(
        session, decider, ring.case_id, {first: "exclude", middle: "release", third: "release"}
    )
    # Pair: a decision, an appeal and its outcome.
    p = await pair(session)
    await confirm(session, decider, p.case_id, {p.a_to_b: "reject", p.b_to_a: "release"})
    notice = await notice_of(session, p.case_id, p.a)
    assert (await appeal(client, p.a, notice.id)).status_code == 200
    filed = await appeal_of(session, notice.id)
    assert (await decide_appeal(client, second, filed.id, "upheld")).status_code == 200
    # Reciprocal: asked, reminded once, then dismissed.
    r = await reciprocal_case(session)
    asked_at = datetime.now(UTC)
    await ask(session, decider, r.case_id, now=asked_at)
    _, fraud_config = await load_fraud(session)
    due = asked_at + timedelta(days=fraud_config.explanation.reply_days)
    lead = fraud_config.explanation.reminder_days_before_due
    at = due - timedelta(days=lead) + timedelta(minutes=1)
    assert await remind_explanations(session, config=fraud_config, now=at) == 2
    assert await remind_explanations(session, config=fraud_config, now=at + timedelta(hours=2)) == 0
    await session.commit()
    await dismiss(session, decider, r.case_id)

    updates = list(
        await session.scalars(
            select(OutboxEvent).where(OutboxEvent.event_type == MemberReviewUpdate.event_type)
        )
    )
    assert {u.payload["kind"] for u in updates} == {
        "explanation_requested",
        "explanation_reminder",
        "decision_notice",
        "appeal_decided",
        "review_closed",
    }
    assert await _run_ours(session, updates) == len(updates)
    assert await _run_ours(session, updates) == 0

    cases = {ring.case_id, p.case_id, r.case_id}
    admins = [decider, second]
    groups = [(ring.case_id, ring.people), (p.case_id, [p.a, p.b]), (r.case_id, [r.a, r.b])]
    for _case_id, people in groups:
        for person in people:
            raw, items = await _list(client, person)
            reviews = [i for i in items if i["kind"] == "review_update"]
            mine = [u for u in updates if u.payload["user_id"] == str(person.user_id)]
            assert len(reviews) == len(mine) > 0, person.handle
            forbidden_people = [m for m in people if m.user_id != person.user_id] + admins
            for item in reviews:
                blob = json.dumps(item)
                assert not FORBIDDEN.search(item["text"]), item["text"]
                assert not FORBIDDEN.search(blob.replace('"can_mute"', "")), blob
                assert item["actors"] == [] and item["actor_count"] == 0
                assert item["link"] == {"kind": "reviews", "target": None}
                assert not any(item["can_mute"].values())
                for other in forbidden_people:
                    assert other.display_name not in blob and other.handle not in blob
                for c in cases:
                    assert str(c) not in blob
            for c in cases:
                assert str(c) not in raw
            for row in await rows_for(session, person):
                if row.kind != K.REVIEW_UPDATE:
                    continue
                assert row.deed_id is None and row.actor_ids == []
                assert row.ref_id not in cases
                assert set(row.data) <= {"review_kind", "reply_due_at"}
                assert not FORBIDDEN.search(json.dumps(row.data))
                assert row.dedupe_key is not None and not any(
                    str(c) in row.dedupe_key for c in cases
                )


async def test_review_rows_need_no_category_choice_and_cant_be_switched_off(
    client: AsyncClient, session: AsyncSession
) -> None:
    me = await make_member(session)
    await session.commit()
    for category in ("reviews", "account"):
        resp = await client.put(
            "/api/notifications/preferences",
            json={"category": category, "on": False},
            headers=me.headers,
        )
        assert (resp.status_code, resp.json()["code"]) == (409, "category_locked")
    await handlers.on_review_update(
        session,
        MemberReviewUpdate(user_id=me.user_id, kind="explanation_requested", item_id=uuid.uuid4()),
    )
    await session.flush()
    rows = await session.scalars(select(Notification).where(Notification.user_id == me.user_id))
    assert [r.kind for r in rows] == [K.REVIEW_UPDATE]
