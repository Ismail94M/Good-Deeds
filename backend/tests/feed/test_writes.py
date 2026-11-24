"""Feed writes (docs/M3-DESIGN.md §3.4 #3 to #9, #11, Q24, Q41, Q42, Q43; test plan §8 "Social",
"Feed" visibility, "Events")."""

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditLog
from app.core.events import OutboxEvent
from app.modules.deeds.models import Deed, SameActGroup, VerificationEvent
from app.modules.feed import _comments, _top
from app.modules.feed._limits import load_limits
from app.modules.feed.models import (
    CommentDeletedRole,
    CommentReportCase,
    FeedComment,
    FeedLike,
    FeedSave,
    FeedShare,
    FeedTopEntry,
    Follow,
    ReportCaseStatus,
    TopPeriod,
)
from tests.deeds.helpers import Member, make_member, members
from tests.feed.helpers import follow, post_of, set_post, verified_deed, withdraw_directly
from tests.feed.test_top import NOW, publish_feed


async def _events(session: AsyncSession, event_type: str) -> list[dict[str, Any]]:
    rows = await session.scalars(select(OutboxEvent).where(OutboxEvent.event_type == event_type))
    return [r.payload for r in rows]


async def _counts(session: AsyncSession, deed_id: uuid.UUID) -> tuple[int, int, int]:
    post = await post_of(session, deed_id)
    assert post is not None
    return post.like_count, post.top_like_count, post.comment_count


async def _deed(session: AsyncSession) -> tuple[Member, Member, uuid.UUID]:
    reporter, doer = await members(session, 2)
    deed_id = await verified_deed(session, reporter, doer)
    await session.commit()
    return reporter, doer, deed_id


# --------------------------------------------------- likes (#3, Q43)


async def test_likes_and_self_likes(client: AsyncClient, session: AsyncSession) -> None:
    reporter, doer, deed_id = await _deed(session)
    (member,) = await members(session, 1)
    path = f"/api/feed/deeds/{deed_id}/like"

    r = await client.put(path, headers=doer.headers)
    assert r.status_code == 200 and r.json() == {"liked": True, "like_count": 1}
    assert await _counts(session, deed_id) == (1, 0, 0)  # shown, but no Top weight
    await client.put(path, headers=reporter.headers)
    assert await _counts(session, deed_id) == (2, 0, 0)
    r = await client.put(path, headers=member.headers)
    assert r.json() == {"liked": True, "like_count": 3}
    assert await _counts(session, deed_id) == (3, 1, 0)

    # Idempotent: a repeat changes nothing and emits nothing more.
    assert (await client.put(path, headers=member.headers)).json()["like_count"] == 3
    liked = [p for p in await _events(session, "feed.deed_liked") if p["deed_id"] == str(deed_id)]
    assert len(liked) == 3
    assert set(liked[0]) == {"deed_id", "user_id"}

    # Unlike reverses exactly what the like did.
    assert (await client.delete(path, headers=doer.headers)).json() == {
        "liked": False,
        "like_count": 2,
    }
    assert await _counts(session, deed_id) == (2, 1, 0)  # the member's like still counts
    await client.delete(path, headers=member.headers)
    assert await _counts(session, deed_id) == (1, 0, 0)
    assert (await client.delete(path, headers=member.headers)).json()["like_count"] == 1

    others = await session.scalar(
        select(func.count())
        .select_from(FeedLike)
        .where(
            FeedLike.deed_id == deed_id,
            FeedLike.user_id.not_in([doer.user_id, reporter.user_id]),
        )
    )
    assert (await _counts(session, deed_id))[1] == others


async def test_like_save_share_on_unviewable_deed_is_404(
    client: AsyncClient, session: AsyncSession
) -> None:
    _, doer, deed_id = await _deed(session)
    (member,) = await members(session, 1)
    await set_post(session, deed_id, hidden_by_doer_at=datetime.now(UTC))
    await session.commit()
    for method, path, body in (
        ("PUT", "like", None),
        ("DELETE", "like", None),
        ("PUT", "save", None),
        ("POST", "comments", {"body": "Lovely"}),
        ("POST", "shares", {"target": "whatsapp"}),
    ):
        r = await client.request(
            method, f"/api/feed/deeds/{deed_id}/{path}", headers=member.headers, json=body
        )
        assert r.status_code == 404, (method, path)
        assert r.json()["code"] == "deed_not_found"
    # The doer can still like and save their hidden deed.
    assert (
        await client.put(f"/api/feed/deeds/{deed_id}/like", headers=doer.headers)
    ).status_code == 200
    missing = await client.put(f"/api/feed/deeds/{uuid.uuid4()}/like", headers=member.headers)
    assert missing.status_code == 404


async def test_writes_need_onboarding(client: AsyncClient, session: AsyncSession) -> None:
    """Q42: reading works, every write here is 403 ``onboarding_required``. Hide/show and
    deleting a comment need only a signed-in party (security review L2, tests/feed/test_sec2.py)."""
    reporter, doer, deed_id = await _deed(session)
    newcomer = await make_member(session, onboarded=False)
    comment = FeedComment(deed_id=deed_id, author_id=reporter.user_id, body="Well done")
    session.add(comment)
    await session.commit()
    for method, path, body in (
        ("PUT", f"/api/feed/deeds/{deed_id}/like", None),
        ("DELETE", f"/api/feed/deeds/{deed_id}/like", None),
        ("PUT", f"/api/feed/deeds/{deed_id}/save", None),
        ("POST", f"/api/feed/deeds/{deed_id}/comments", {"body": "Nice"}),
        ("POST", f"/api/feed/deeds/{deed_id}/shares", {"target": "copy_link"}),
        ("PUT", f"/api/users/{doer.handle}/follow", None),
        ("DELETE", f"/api/me/followers/{doer.handle}", None),
    ):
        r = await client.request(method, path, headers=newcomer.headers, json=body)
        assert r.status_code == 403, (method, path)
        assert r.json()["code"] == "onboarding_required"
    r = await client.get(f"/api/feed/deeds/{deed_id}/comments", headers=newcomer.headers)
    assert r.status_code == 200


async def test_like_rate_limit(client: AsyncClient, session: AsyncSession) -> None:
    _, _, deed_id = await _deed(session)
    (member,) = await members(session, 1)
    await publish_feed(session, rate_like_user="2/3600")
    path = f"/api/feed/deeds/{deed_id}/like"
    assert (await client.put(path, headers=member.headers)).status_code == 200
    assert (await client.delete(path, headers=member.headers)).status_code == 200
    assert (await client.put(path, headers=member.headers)).status_code == 429


# --------------------------------------------------- saves (#4)


async def test_saves_are_private_and_idempotent(client: AsyncClient, session: AsyncSession) -> None:
    _, _, deed_id = await _deed(session)
    (member,) = await members(session, 1)
    path = f"/api/feed/deeds/{deed_id}/save"
    for _ in range(2):
        r = await client.put(path, headers=member.headers)
        assert r.status_code == 200 and r.json() == {"saved": True}
    rows = await session.scalar(
        select(func.count()).select_from(FeedSave).where(FeedSave.deed_id == deed_id)
    )
    assert rows == 1
    detail = (await client.get(f"/api/feed/deeds/{deed_id}", headers=member.headers)).json()
    assert detail["saved"] is True and "save_count" not in json.dumps(detail)
    for _ in range(2):
        assert (await client.delete(path, headers=member.headers)).json() == {"saved": False}


# --------------------------------------------------- comments (#5 to #7)


async def test_post_and_list_comments(client: AsyncClient, session: AsyncSession) -> None:
    _, doer, deed_id = await _deed(session)
    a, b = await members(session, 2)
    path = f"/api/feed/deeds/{deed_id}/comments"
    r = await client.post(path, headers=a.headers, json={"body": "  So kind of you!  "})
    assert r.status_code == 201, r.text
    first = r.json()
    assert first["body"] == "So kind of you!"
    assert first["author"] == {"handle": a.handle, "display_name": a.display_name}
    assert first["mine"] is True and first["can_delete"] is True
    await client.post(path, headers=b.headers, json={"body": "Thank you"})
    assert (await _counts(session, deed_id))[2] == 2
    posted = [
        p for p in await _events(session, "feed.comment_posted") if p["deed_id"] == str(deed_id)
    ]
    assert len(posted) == 2 and set(posted[0]) == {"deed_id", "comment_id", "author_id"}

    as_b = (await client.get(path, headers=b.headers)).json()
    assert [c["body"] for c in as_b["items"]] == ["So kind of you!", "Thank you"]
    assert [(c["mine"], c["can_delete"]) for c in as_b["items"]] == [(False, False), (True, True)]
    as_doer = (await client.get(path, headers=doer.headers)).json()
    assert all(c["can_delete"] for c in as_doer["items"])
    page = (await client.get(f"{path}?limit=1", headers=b.headers)).json()
    nxt = (
        await client.get(f"{path}?limit=1&cursor={page['next_cursor']}", headers=b.headers)
    ).json()
    assert [c["body"] for c in page["items"] + nxt["items"]] == ["So kind of you!", "Thank you"]
    assert str(a.user_id) not in json.dumps(as_b)


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ("   ", "comment_empty"),
        ("you are an idiot", "comment_not_kind"),
        ("see example.com", "comment_has_link"),
        ("call me on 98765 43210", "comment_has_phone"),
    ],
)
async def test_refused_comments(
    client: AsyncClient, session: AsyncSession, body: str, code: str
) -> None:
    _, _, deed_id = await _deed(session)
    (member,) = await members(session, 1)
    r = await client.post(
        f"/api/feed/deeds/{deed_id}/comments", headers=member.headers, json={"body": body}
    )
    assert r.status_code == 422 and r.json()["code"] == code
    assert "idiot" not in r.json()["detail"]
    assert (await _counts(session, deed_id))[2] == 0


async def test_comment_length_follows_config(client: AsyncClient, session: AsyncSession) -> None:
    _, _, deed_id = await _deed(session)
    (member,) = await members(session, 1)
    path = f"/api/feed/deeds/{deed_id}/comments"
    assert (
        await client.post(path, headers=member.headers, json={"body": "a" * 300})
    ).status_code == 201
    r = await client.post(path, headers=member.headers, json={"body": "a" * 301})
    assert r.status_code == 422 and r.json()["code"] == "comment_too_long"
    await publish_feed(session, comment_max_chars=280)
    r = await client.post(path, headers=member.headers, json={"body": "a" * 281})
    assert r.json()["code"] == "comment_too_long"


async def test_comment_rate_limits_by_account_and_ip(
    client: AsyncClient, session: AsyncSession
) -> None:
    _, _, deed_id = await _deed(session)
    a, b = await members(session, 2)
    path = f"/api/feed/deeds/{deed_id}/comments"
    for i in range(5):
        r = await client.post(path, headers=a.headers, json={"body": f"Thanks {i}"})
        assert r.status_code == 201
    r = await client.post(path, headers=a.headers, json={"body": "Thanks again"})
    assert r.status_code == 429 and r.json()["code"] == "rate_limited"
    assert (await client.post(path, headers=b.headers, json={"body": "Hi"})).status_code == 201
    await publish_feed(session, rate_comment_ip="1/3600")
    (c,) = await members(session, 1)
    r = await client.post(path, headers=c.headers, json={"body": "Hello"})
    assert r.status_code == 429  # same IP as a and b


async def test_delete_comment_rules_and_retention(
    client: AsyncClient, session: AsyncSession
) -> None:
    reporter, doer, deed_id = await _deed(session)
    author, other = await members(session, 2)
    path = f"/api/feed/deeds/{deed_id}/comments"
    ids = []
    for _ in range(3):
        r = await client.post(path, headers=author.headers, json={"body": "Lovely deed"})
        ids.append(r.json()["id"])
    assert (await _counts(session, deed_id))[2] == 3

    r = await client.delete(f"/api/feed/comments/{ids[0]}", headers=other.headers)
    assert r.status_code == 404 and r.json()["code"] == "comment_not_found"
    missing = await client.delete(f"/api/feed/comments/{uuid.uuid4()}", headers=other.headers)
    assert missing.json() == r.json()

    # Author: erased at once; deleting twice is 204 and counts once.
    for _ in range(2):
        r = await client.delete(f"/api/feed/comments/{ids[0]}", headers=author.headers)
        assert r.status_code == 204
    # Doer and reporter: kept for the retention period.
    assert (
        await client.delete(f"/api/feed/comments/{ids[1]}", headers=doer.headers)
    ).status_code == 204
    assert (
        await client.delete(f"/api/feed/comments/{ids[2]}", headers=reporter.headers)
    ).status_code == 204
    assert (await _counts(session, deed_id))[2] == 0
    rows = {
        str(c.id): c
        for c in await session.scalars(
            select(FeedComment)
            .where(FeedComment.deed_id == deed_id)
            .execution_options(populate_existing=True)
        )
    }
    assert rows[ids[0]].body is None and rows[ids[0]].deleted_role == CommentDeletedRole.AUTHOR
    assert (
        rows[ids[1]].body == "Lovely deed" and rows[ids[1]].deleted_role == CommentDeletedRole.DOER
    )
    assert rows[ids[2]].deleted_role == CommentDeletedRole.REPORTER
    assert rows[ids[2]].deleted_by == reporter.user_id
    assert (await client.get(path, headers=other.headers)).json()["items"] == []

    # The erasure job: not before the retention period, then the text is gone.
    _, limits = await load_limits(session)
    deleted_at = rows[ids[1]].deleted_at
    assert deleted_at is not None
    early = deleted_at + timedelta(days=limits.deleted_comment_retention_days) - timedelta(hours=1)
    await _comments.erase_deleted_batch(session, limits, now=early)
    await session.refresh(rows[ids[1]])
    assert rows[ids[1]].body == "Lovely deed"
    late = deleted_at + timedelta(days=limits.deleted_comment_retention_days, minutes=1)
    assert await _comments.erase_deleted_batch(session, limits, now=late) >= 2
    for i in ids[1:]:
        await session.refresh(rows[i])
        assert rows[i].body is None


async def _comment_counts(session: AsyncSession, deed_id: uuid.UUID) -> tuple[int, int]:
    post = await post_of(session, deed_id)
    assert post is not None
    return post.comment_count, post.top_comment_count


async def test_self_comments_show_but_carry_no_top_weight(
    client: AsyncClient, session: AsyncSession
) -> None:
    """L1: the doer's and reporter's comments count in ``comment_count`` (shown) but not in
    ``top_comment_count``; every delete reverses exactly what its post did."""
    reporter, doer, deed_id = await _deed(session)
    member, other = await members(session, 2)
    path = f"/api/feed/deeds/{deed_id}/comments"

    async def post(who: Member) -> str:
        r = await client.post(path, headers=who.headers, json={"body": "Lovely deed"})
        assert r.status_code == 201
        return str(r.json()["id"])

    by_doer = await post(doer)
    assert await _comment_counts(session, deed_id) == (1, 0)
    by_reporter = await post(reporter)
    assert await _comment_counts(session, deed_id) == (2, 0)
    by_member = await post(member)
    by_other = await post(other)
    assert await _comment_counts(session, deed_id) == (4, 2)
    r = await client.get(f"/api/feed/deeds/{deed_id}", headers=member.headers)
    assert r.json()["comment_count"] == 4  # shown, self-comments included

    async def delete(cid: str, who: Member) -> None:
        r = await client.delete(f"/api/feed/comments/{cid}", headers=who.headers)
        assert r.status_code == 204

    # Author deletes, twice: one step down.
    await delete(by_member, member)
    await delete(by_member, member)
    assert await _comment_counts(session, deed_id) == (3, 1)
    # The doer deletes a third member's comment; the reporter deletes the doer's.
    await delete(by_other, doer)
    assert await _comment_counts(session, deed_id) == (2, 0)
    await delete(by_doer, reporter)
    assert await _comment_counts(session, deed_id) == (1, 0)
    # The reporter deletes their own.
    await delete(by_reporter, reporter)
    assert await _comment_counts(session, deed_id) == (0, 0)


async def test_self_comments_do_not_move_top_order(session: AsyncSession) -> None:
    reporter, doer = await members(session, 2)
    (member,) = await members(session, 1)
    _, limits = await load_limits(session)
    self_talk = await verified_deed(session, reporter, doer, verified_at=NOW - timedelta(hours=1))
    for who in (doer, reporter, doer, reporter):
        await _comments.post_comment(session, self_talk, who.user_id, "Thanks", limits=limits)
    # Older, so it would lose a tie: it wins only on the third member's comment.
    liked = await verified_deed(session, reporter, doer, verified_at=NOW - timedelta(hours=2))
    await _comments.post_comment(session, liked, member.user_id, "Lovely", limits=limits)
    snap = await _top.compute_snapshot(session, TopPeriod.WEEK, limits, now=NOW)
    entries = {
        deed_id: (rank, score)
        for deed_id, rank, score in await session.execute(
            select(FeedTopEntry.deed_id, FeedTopEntry.rank, FeedTopEntry.score).where(
                FeedTopEntry.snapshot_id == snap.id,
                FeedTopEntry.deed_id.in_([self_talk, liked]),
            )
        )
    }
    assert entries[liked][1] == limits.top_comment_weight
    # Zero score: ranked below, or not in the snapshot at all if it is full.
    assert self_talk not in entries or entries[self_talk] > (entries[liked][0], 0)
    assert entries.get(self_talk, (0, 0))[1] == 0


async def test_deleting_closes_an_open_report_case(session: AsyncSession) -> None:
    _, doer, deed_id = await _deed(session)
    (author,) = await members(session, 1)
    _, limits = await load_limits(session)
    out = await _comments.post_comment(session, deed_id, author.user_id, "Great", limits=limits)
    case = CommentReportCase(
        comment_id=out.id,
        deed_id=deed_id,
        status=ReportCaseStatus.OPEN,
        priority=3,
        report_count=1,
        opened_at=datetime.now(UTC),
    )
    session.add(case)
    await session.flush()
    await _comments.delete_comment(session, out.id, doer.user_id)
    await session.refresh(case)
    assert case.status == ReportCaseStatus.CLOSED and case.resolved_at is not None


# --------------------------------------------------- hide / show (#8, Q24)


async def test_separate_hides(client: AsyncClient, session: AsyncSession) -> None:
    reporter, doer, deed_id = await _deed(session)
    (stranger,) = await members(session, 1)

    async def snapshot() -> tuple[Any, ...]:
        deed = (
            await session.scalars(
                select(Deed).where(Deed.id == deed_id).execution_options(populate_existing=True)
            )
        ).one()
        group = await session.get(SameActGroup, deed.same_act_group_id, populate_existing=True)
        assert group is not None
        events = await session.scalar(
            select(func.count())
            .select_from(VerificationEvent)
            .where(VerificationEvent.deed_id == deed_id)
        )
        return (
            deed.status,
            deed.updated_at,
            deed.counts_for_points,
            deed.prize_rank_eligible,
            group.verified_count,
            group.counted_count,
            events,
            await _counts(session, deed_id),
        )

    before = await snapshot()
    hide, show = f"/api/feed/deeds/{deed_id}/hide", f"/api/feed/deeds/{deed_id}/show"
    assert (await client.post(hide, headers=stranger.headers)).status_code == 404

    r = await client.post(hide, headers=doer.headers)
    assert r.status_code == 200
    assert r.json() == {
        "listed": False,
        "hidden_by_me": True,
        "hidden_by_other": False,
        "can_hide": False,
        "can_show": True,
    }
    assert (await client.get(f"/api/public/deeds/{deed_id}")).status_code == 404
    # The reporter can't undo the doer's hide.
    r = await client.post(show, headers=reporter.headers)
    assert r.json()["listed"] is False and r.json()["hidden_by_other"] is True
    await client.post(hide, headers=reporter.headers)
    await client.post(show, headers=doer.headers)
    post = await post_of(session, deed_id)
    assert post is not None and not post.listed  # the reporter's hide remains
    r = await client.post(show, headers=reporter.headers)
    assert r.json()["listed"] is True
    assert (await client.get(f"/api/public/deeds/{deed_id}")).status_code == 200

    audits = list(
        await session.scalars(
            select(AuditLog).where(
                AuditLog.entity_type == "deed_visibility", AuditLog.entity_id == str(deed_id)
            )
        )
    )
    # One row per real change (the reporter's show of the doer's hide changed nothing). Rows of
    # one test transaction share created_at, so compare without order.
    assert sorted((a.action, a.new_value["role"]) for a in audits if a.new_value) == sorted(
        [
            ("hidden", "doer"),
            ("hidden", "reporter"),
            ("shown", "doer"),
            ("shown", "reporter"),
        ]
    )
    assert len(audits) == 4
    assert await snapshot() == before

    await withdraw_directly(session, deed_id)
    await session.commit()
    r = await client.post(hide, headers=doer.headers)
    assert r.status_code == 409 and r.json()["code"] == "deed_not_published"


# --------------------------------------------------- shares (#9)


async def test_shares(client: AsyncClient, session: AsyncSession) -> None:
    _, doer, deed_id = await _deed(session)
    (member,) = await members(session, 1)
    path = f"/api/feed/deeds/{deed_id}/shares"
    for target in ("whatsapp", "copy_link", "web_share"):
        r = await client.post(path, headers=member.headers, json={"target": target})
        assert r.status_code == 204
    assert (
        await client.post(path, headers=member.headers, json={"target": "sms"})
    ).status_code == 422
    shares = list(await session.scalars(select(FeedShare).where(FeedShare.deed_id == deed_id)))
    assert sorted(s.target.value for s in shares) == ["copy_link", "web_share", "whatsapp"]
    await set_post(session, deed_id, hidden_by_doer_at=datetime.now(UTC))
    await session.commit()
    r = await client.post(path, headers=doer.headers, json={"target": "whatsapp"})
    assert r.status_code == 409 and r.json()["code"] == "not_shareable"


# --------------------------------------------------- follows (#11)


async def test_follow_unfollow_and_remove_follower(
    client: AsyncClient, session: AsyncSession
) -> None:
    a, b = await members(session, 2)
    path = f"/api/users/{b.handle}/follow"
    for _ in range(2):
        r = await client.put(path, headers=a.headers)
        assert r.status_code == 200
        assert r.json() == {"followers": 1, "following": 0, "is_following": True, "is_me": False}
    followed = [
        p
        for p in await _events(session, "feed.user_followed")
        if p["followed_id"] == str(b.user_id)
    ]
    assert followed == [{"follower_id": str(a.user_id), "followed_id": str(b.user_id)}]

    me = (await client.get("/api/me/following", headers=a.headers)).json()
    assert me["items"] == [
        {"user": {"handle": b.handle, "display_name": b.display_name}, "following": True}
    ]
    fans = (await client.get("/api/me/followers", headers=b.headers)).json()
    assert fans["items"] == [
        {"user": {"handle": a.handle, "display_name": a.display_name}, "following": False}
    ]

    events_before = len(await _events(session, "feed.user_followed"))
    r = await client.delete(f"/api/me/followers/{a.handle}", headers=b.headers)
    assert r.status_code == 204
    assert (await client.get("/api/me/followers", headers=b.headers)).json()["items"] == []
    assert len(await _events(session, "feed.user_followed")) == events_before  # quiet
    await client.put(path, headers=a.headers)
    r = await client.delete(path, headers=a.headers)
    assert r.json()["is_following"] is False and r.json()["followers"] == 0

    self_follow = await client.put(f"/api/users/{a.handle}/follow", headers=a.headers)
    assert self_follow.status_code == 400 and self_follow.json()["code"] == "cannot_follow_self"
    unknown = await client.put("/api/users/nobody-here/follow", headers=a.headers)
    assert unknown.status_code == 404 and unknown.json()["code"] == "user_not_found"


async def test_following_limit(client: AsyncClient, session: AsyncSession) -> None:
    me, x, y = await members(session, 3)
    await publish_feed(session, max_following=1)
    assert (
        await client.put(f"/api/users/{x.handle}/follow", headers=me.headers)
    ).status_code == 200
    r = await client.put(f"/api/users/{y.handle}/follow", headers=me.headers)
    assert r.status_code == 409 and r.json()["code"] == "following_limit"
    # Re-following someone already followed is still fine at the limit.
    assert (
        await client.put(f"/api/users/{x.handle}/follow", headers=me.headers)
    ).status_code == 200


async def test_connection_lists_page(client: AsyncClient, session: AsyncSession) -> None:
    me = (await members(session, 1))[0]
    fans = await members(session, 3)
    base = datetime.now(UTC)
    for i, fan in enumerate(fans):
        session.add(
            Follow(
                follower_id=fan.user_id,
                followed_id=me.user_id,
                created_at=base + timedelta(seconds=i),
            )
        )
    await follow(session, me, fans[0])
    await session.commit()
    first = (await client.get("/api/me/followers?limit=2", headers=me.headers)).json()
    rest = (
        await client.get(
            f"/api/me/followers?limit=2&cursor={first['next_cursor']}", headers=me.headers
        )
    ).json()
    handles = [i["user"]["handle"] for i in first["items"] + rest["items"]]
    assert handles == [fans[2].handle, fans[1].handle, fans[0].handle]
    assert rest["items"][0]["following"] is True and rest["next_cursor"] is None


# --------------------------------------------------- one-time notices (Q41)


async def test_notices(client: AsyncClient, session: AsyncSession) -> None:
    newcomer = await make_member(session, onboarded=False)
    assert (await client.get("/api/feed/me/notices", headers=newcomer.headers)).json() == [
        "public_feed_intro"
    ]
    for _ in range(2):
        r = await client.post(
            "/api/feed/me/notices/public_feed_intro/seen", headers=newcomer.headers
        )
        assert r.status_code == 204
    assert (await client.get("/api/feed/me/notices", headers=newcomer.headers)).json() == []
    r = await client.post("/api/feed/me/notices/unknown/seen", headers=newcomer.headers)
    assert r.status_code == 404
