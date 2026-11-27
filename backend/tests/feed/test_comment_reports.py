"""Comment reports, moderator review and the author's removal notices (docs/M3-DESIGN.md §3.4a,
§3.4b #1, #2, #5, #6, D45, Q34, Q38; test plan §8 "Comment deletion and reports",
"Comment-removed notice").

Robust to rows other test files committed: queues are checked by this test's case ids and by
count differences, never by absolute totals.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditLog
from app.core.events import OutboxEvent
from app.modules.feed import _comments, _moderation
from app.modules.feed._limits import load_limits
from app.modules.feed._shared_limits import load_shared
from app.modules.feed.models import (
    CommentDecision,
    CommentDeletedRole,
    CommentReport,
    CommentReportCase,
    FeedComment,
    ReportCaseStatus,
)
from app.modules.identity.models import Role, User
from tests.deeds.helpers import Member, members
from tests.feed.helpers import post_of, set_post, verified_deed

DETAIL_KEYS = {"case", "comment", "deed", "reports", "decisions", "author_removed_last_90_days"}


async def _reviewer(session: AsyncSession, role: Role = Role.MODERATOR) -> Member:
    (m,) = await members(session, 1)
    await session.execute(update(User).where(User.id == m.user_id).values(role=role))
    await session.commit()
    return m


async def _setup(session: AsyncSession) -> tuple[Member, Member, Member, uuid.UUID, uuid.UUID]:
    """reporter, doer, comment author, deed id, comment id (live, committed)."""
    reporter, doer, author = await members(session, 3)
    deed_id = await verified_deed(session, reporter, doer)
    _, limits = await load_limits(session)
    out = await _comments.post_comment(
        session, deed_id, author.user_id, "Not a nice thing to say here", limits=limits
    )
    await session.commit()
    return reporter, doer, author, deed_id, out.id


async def _report(
    client: AsyncClient, who: Member, comment_id: uuid.UUID, reason: str = "spam", **extra: Any
) -> Any:
    return await client.post(
        f"/api/feed/comments/{comment_id}/reports",
        headers=who.headers,
        json={"reason": reason, **extra},
    )


async def _case(session: AsyncSession, comment_id: uuid.UUID) -> CommentReportCase:
    case = await session.scalar(
        select(CommentReportCase)
        .where(CommentReportCase.comment_id == comment_id)
        .order_by(CommentReportCase.opened_at.desc())
        .execution_options(populate_existing=True)
    )
    assert case is not None
    return case


async def _comment(session: AsyncSession, comment_id: uuid.UUID) -> FeedComment:
    c = await session.scalar(
        select(FeedComment)
        .where(FeedComment.id == comment_id)
        .execution_options(populate_existing=True)
    )
    assert c is not None
    return c


# --------------------------------------------------- #13 member reports


async def test_report_opens_a_case_and_never_hides(
    client: AsyncClient, session: AsyncSession
) -> None:
    _, _, author, deed_id, comment_id = await _setup(session)
    a, b = await members(session, 2)
    r = await _report(client, a, comment_id, "spam", details="Looks like an advert")
    assert r.status_code == 202 and r.json() == {"status": "received"}
    dup = await _report(client, a, comment_id, "threat_or_harassment")
    assert dup.status_code == 202 and dup.json() == r.json()
    case = await _case(session, comment_id)
    assert (case.status, case.report_count, case.priority) == (ReportCaseStatus.OPEN, 1, 3)

    await _report(client, b, comment_id, "hate")
    case = await _case(session, comment_id)
    assert (case.report_count, case.priority) == (2, 2)
    (c,) = await members(session, 1)
    await _report(client, c, comment_id, "other")
    case = await _case(session, comment_id)
    assert (case.report_count, case.priority) == (3, 2)  # never goes down
    rows = await session.scalar(
        select(func.count())
        .select_from(CommentReport)
        .where(CommentReport.comment_id == comment_id)
    )
    assert rows == 3

    # Still visible to everyone, and the author's view is unchanged.
    listed = await client.get(f"/api/feed/deeds/{deed_id}/comments", headers=author.headers)
    assert [i["id"] for i in listed.json()["items"]] == [str(comment_id)]
    assert set(listed.json()["items"][0]) == {
        "id",
        "author",
        "body",
        "created_at",
        "mine",
        "can_delete",
    }
    assert (await post_of(session, deed_id)).comment_count == 1  # type: ignore[union-attr]


async def test_report_refusals(client: AsyncClient, session: AsyncSession) -> None:
    _, doer, author, deed_id, comment_id = await _setup(session)
    (member,) = await members(session, 1)
    own = await _report(client, author, comment_id)
    assert own.status_code == 404 and own.json()["code"] == "comment_not_found"
    missing = await _report(client, member, uuid.uuid4())
    assert missing.json() == own.json()
    bad = await _report(client, member, comment_id, "rude")
    assert bad.status_code == 422 and bad.json()["code"] == "invalid_reason"
    long = await _report(client, member, comment_id, details="x" * 501)
    assert long.status_code == 422 and long.json()["code"] == "details_too_long"

    _, limits = await load_limits(session)
    gone = await _comments.post_comment(session, deed_id, member.user_id, "Bye", limits=limits)
    await _comments.delete_comment(session, gone.id, member.user_id)
    await session.commit()
    assert (await _report(client, doer, gone.id)).status_code == 404

    await set_post(session, deed_id, hidden_by_doer_at=datetime.now(UTC))
    await session.commit()
    hidden = await _report(client, member, comment_id)
    assert hidden.status_code == 404 and hidden.json() == own.json()
    assert (await _report(client, doer, comment_id)).status_code == 202  # the doer still sees it


async def test_report_needs_onboarding_and_is_rate_limited(
    client: AsyncClient, session: AsyncSession
) -> None:
    from tests.deeds.helpers import make_member
    from tests.feed.test_top import publish_feed

    *_, comment_id = await _setup(session)
    newcomer = await make_member(session, onboarded=False)
    r = await _report(client, newcomer, comment_id)
    assert r.status_code == 403 and r.json()["code"] == "onboarding_required"
    await publish_feed(session, rate_comment_report_user="1/3600")
    (member,) = await members(session, 1)
    assert (await _report(client, member, comment_id)).status_code == 202
    assert (await _report(client, member, comment_id)).status_code == 429


async def test_deleting_closes_the_case(client: AsyncClient, session: AsyncSession) -> None:
    _, doer, _, _, comment_id = await _setup(session)
    (member,) = await members(session, 1)
    await _report(client, member, comment_id)
    r = await client.delete(f"/api/feed/comments/{comment_id}", headers=doer.headers)
    assert r.status_code == 204
    case = await _case(session, comment_id)
    assert case.status == ReportCaseStatus.CLOSED and case.decision is None


# --------------------------------------------------- #14 to #17 admin


async def test_roles(client: AsyncClient, session: AsyncSession) -> None:
    *_, comment_id = await _setup(session)
    (member,) = await members(session, 1)
    await _report(client, member, comment_id)
    case = await _case(session, comment_id)
    paths = [
        ("GET", "/api/admin/comment-reports", None),
        ("GET", f"/api/admin/comment-reports/{case.id}", None),
        (
            "POST",
            f"/api/admin/comment-reports/{case.id}/decision",
            {"action": "keep", "note": "Fine here"},
        ),
        ("POST", f"/api/admin/comments/{comment_id}/restore", {"note": "Fine here"}),
    ]
    for method, path, body in paths:
        r = await client.request(method, path, headers=member.headers, json=body)
        assert r.status_code == 403 and r.json()["code"] == "forbidden", path
        assert (await client.request(method, path, json=body)).status_code == 401
    for role in (Role.MODERATOR, Role.ADMIN):
        reviewer = await _reviewer(session, role)
        for method, path, _ in paths[:2]:
            assert (await client.request(method, path, headers=reviewer.headers)).status_code == 200


async def test_queue_and_detail(client: AsyncClient, session: AsyncSession) -> None:
    reporter, doer, author, _, comment_id = await _setup(session)
    _, _, _, _, urgent_id = await _setup(session)
    a, b = await members(session, 2)
    moderator = await _reviewer(session)
    before = (await client.get("/api/admin/comment-reports", headers=moderator.headers)).json()
    await _report(client, a, comment_id, "spam", details="Advert")
    await _report(client, b, comment_id, "hate")
    await _report(client, a, urgent_id, "threat_or_harassment")

    body = (
        await client.get("/api/admin/comment-reports?limit=50", headers=moderator.headers)
    ).json()
    assert body["open_count"] == before["open_count"] + 2
    mine = [
        i
        for i in body["items"]
        if i["case_id"]
        in {str((await _case(session, comment_id)).id), str((await _case(session, urgent_id)).id)}
    ]
    assert [i["priority"] for i in mine] == [1, 2]  # most serious first
    item = mine[1]
    assert item["top_reason"] == "hate" and item["report_count"] == 2
    assert item["comment_excerpt"] == "Not a nice thing to say here"
    assert item["author"]["handle"] == author.handle
    assert item["deed"]["doer"]["handle"] == doer.handle

    case = await _case(session, comment_id)
    detail = (
        await client.get(f"/api/admin/comment-reports/{case.id}", headers=moderator.headers)
    ).json()
    assert set(detail) == DETAIL_KEYS
    assert detail["comment"]["state"] == "visible" and detail["comment"]["restorable_until"] is None
    assert detail["deed"]["reporter"]["handle"] == reporter.handle and detail["deed"]["listed"]
    assert [(r["reason"], r["details"], r["reporter"]) for r in detail["reports"]] == [
        ("spam", "Advert", {"kind": "member", "handle": a.handle}),
        ("hate", None, {"kind": "member", "handle": b.handle}),
    ]
    assert detail["decisions"] == [] and detail["author_removed_last_90_days"] == 0
    missing = await client.get(
        f"/api/admin/comment-reports/{uuid.uuid4()}", headers=moderator.headers
    )
    assert missing.status_code == 404 and missing.json()["code"] == "case_not_found"
    bad = await client.get("/api/admin/comment-reports?cursor=nope", headers=moderator.headers)
    assert bad.status_code == 400


async def test_remove_then_restore(client: AsyncClient, session: AsyncSession) -> None:
    _, doer, author, deed_id, comment_id = await _setup(session)
    (member,) = await members(session, 1)
    moderator, other_moderator = await _reviewer(session), await _reviewer(session)
    await _report(client, member, comment_id)
    case = await _case(session, comment_id)
    path = f"/api/admin/comment-reports/{case.id}/decision"

    for note in ("", "four"):
        r = await client.post(
            path, headers=moderator.headers, json={"action": "remove", "note": note}
        )
        assert r.status_code == 422 and r.json()["code"] == "note_required"
    r = await client.post(
        path, headers=moderator.headers, json={"action": "remove", "note": "x" * 1001}
    )
    assert r.status_code == 422 and r.json()["code"] == "note_too_long"

    r = await client.post(
        path, headers=moderator.headers, json={"action": "remove", "note": "Harassment of the doer"}
    )
    assert r.status_code == 200, r.text
    detail = r.json()
    assert detail["case"]["status"] == "resolved" and detail["case"]["decision"] == "removed"
    assert detail["comment"]["state"] == "removed"
    assert detail["comment"]["body"] == "Not a nice thing to say here"  # kept for moderators
    assert detail["comment"]["restorable_until"] is not None
    assert detail["author_removed_last_90_days"] == 1
    assert [d["action"] for d in detail["decisions"]] == ["remove"]

    comment = await _comment(session, comment_id)
    assert comment.deleted_role == CommentDeletedRole.MODERATION
    assert (await post_of(session, deed_id)).comment_count == 0  # type: ignore[union-attr]
    listed = await client.get(f"/api/feed/deeds/{deed_id}/comments", headers=member.headers)
    assert listed.json()["items"] == []
    again = await client.post(
        path, headers=moderator.headers, json={"action": "keep", "note": "Second look"}
    )
    assert again.status_code == 409 and again.json()["code"] == "case_not_open"

    # The author is told, without who reported, who decided or the note.
    notices = (await client.get("/api/feed/me/comment-notices", headers=author.headers)).json()
    assert notices == [
        {
            "comment_id": str(comment_id),
            "deed_id": str(deed_id),
            "doer": {"handle": doer.handle, "display_name": doer.display_name, "avatar": None},
            "excerpt": "Not a nice thing to say here",
            "removed_at": notices[0]["removed_at"],
        }
    ]
    assert "Harassment" not in str(notices)

    restore = f"/api/admin/comments/{comment_id}/restore"
    r = await client.post(
        restore, headers=other_moderator.headers, json={"note": "Context was fine"}
    )
    assert r.status_code == 200 and r.json()["comment"]["state"] == "visible"
    assert r.json()["comment"]["body"] == "Not a nice thing to say here"
    assert [d["action"] for d in r.json()["decisions"]] == ["remove", "restore"]
    assert (await post_of(session, deed_id)).comment_count == 1  # type: ignore[union-attr]
    restored = await _comment(session, comment_id)
    assert restored.deleted_at is None and restored.author_deleted_at is None
    assert (await client.get("/api/feed/me/comment-notices", headers=author.headers)).json() == []
    r = await client.post(restore, headers=moderator.headers, json={"note": "Context was fine"})
    assert r.status_code == 409 and r.json()["code"] == "not_restorable"

    decisions = await session.scalar(
        select(func.count())
        .select_from(CommentDecision)
        .where(CommentDecision.comment_id == comment_id)
    )
    audits = list(
        await session.scalars(
            select(AuditLog.action).where(
                AuditLog.entity_type == "comment", AuditLog.entity_id == str(comment_id)
            )
        )
    )
    events = list(
        await session.scalars(
            select(OutboxEvent.payload).where(
                OutboxEvent.event_type == "feed.comment_moderation_decided",
                OutboxEvent.payload["comment_id"].astext == str(comment_id),
            )
        )
    )
    assert decisions == 2
    assert sorted(audits) == ["comment.removed", "comment.restored"]
    assert sorted(e["action"] for e in events) == ["remove", "restore"]
    assert set(events[0]) == {"comment_id", "case_id", "deed_id", "author_id", "action"}


async def _counters(session: AsyncSession, deed_id: uuid.UUID) -> tuple[int, int]:
    post = await post_of(session, deed_id)
    assert post is not None
    return post.comment_count, post.top_comment_count


async def test_author_delete_after_moderation_removal_keeps_the_text(
    client: AsyncClient, session: AsyncSession
) -> None:
    """L3: the author's delete no longer erases text moderation removed, so the 30-day evidence
    stays; the author gets the same 204 as any delete. A later restore undoes only the
    moderation: the comment stays deleted as the author's own, erased then, counts unchanged."""
    _, _, author, deed_id, comment_id = await _setup(session)
    (member,) = await members(session, 1)
    moderator = await _reviewer(session)
    await _report(client, member, comment_id)
    case = await _case(session, comment_id)
    r = await client.post(
        f"/api/admin/comment-reports/{case.id}/decision",
        headers=moderator.headers,
        json={"action": "remove", "note": "Harassment of the doer"},
    )
    assert r.status_code == 200
    assert await _counters(session, deed_id) == (0, 0)

    delete = f"/api/feed/comments/{comment_id}"
    for _ in range(2):
        r = await client.delete(delete, headers=author.headers)
        assert r.status_code == 204 and r.content == b""
    comment = await _comment(session, comment_id)
    assert comment.body == "Not a nice thing to say here"
    assert comment.deleted_role == CommentDeletedRole.MODERATION
    assert comment.deleted_by == moderator.user_id
    asked_at = comment.author_deleted_at
    assert asked_at is not None  # the first request; the second changes nothing
    assert await _counters(session, deed_id) == (0, 0)  # counted once
    notices = await client.get("/api/feed/me/comment-notices", headers=author.headers)
    assert [n["comment_id"] for n in notices.json()] == [str(comment_id)]

    r = await client.post(
        f"/api/admin/comments/{comment_id}/restore",
        headers=moderator.headers,
        json={"note": "Context was fine"},
    )
    assert r.status_code == 200
    detail = r.json()
    assert detail["comment"]["state"] == "deleted"
    assert detail["comment"]["body"] is None and detail["comment"]["restorable_until"] is None
    assert [d["action"] for d in detail["decisions"]] == ["remove", "restore"]
    assert detail["author_removed_last_90_days"] == 0
    comment = await _comment(session, comment_id)
    assert comment.body is None and comment.deleted_role == CommentDeletedRole.AUTHOR
    assert (comment.deleted_by, comment.deleted_at) == (author.user_id, asked_at)
    assert await _counters(session, deed_id) == (0, 0)
    assert (await client.get("/api/feed/me/comment-notices", headers=author.headers)).json() == []
    listed = await client.get(f"/api/feed/deeds/{deed_id}/comments", headers=member.headers)
    assert listed.json()["items"] == []
    again = await client.post(
        f"/api/admin/comments/{comment_id}/restore",
        headers=moderator.headers,
        json={"note": "Context was fine"},
    )
    assert again.status_code == 409 and again.json()["code"] == "not_restorable"
    assert (await client.delete(delete, headers=author.headers)).status_code == 204
    assert await _counters(session, deed_id) == (0, 0)


async def test_author_may_still_erase_after_a_doer_or_reporter_delete(
    client: AsyncClient, session: AsyncSession
) -> None:
    reporter, doer, author, deed_id, comment_id = await _setup(session)
    _, limits = await load_limits(session)
    second = await _comments.post_comment(session, deed_id, author.user_id, "Again", limits=limits)
    await session.commit()
    for who, cid, role in (
        (doer, comment_id, CommentDeletedRole.DOER),
        (reporter, second.id, CommentDeletedRole.REPORTER),
    ):
        r = await client.delete(f"/api/feed/comments/{cid}", headers=who.headers)
        assert r.status_code == 204
        assert (await _comment(session, cid)).body is not None
        r = await client.delete(f"/api/feed/comments/{cid}", headers=author.headers)
        assert r.status_code == 204
        comment = await _comment(session, cid)
        assert comment.body is None
        assert comment.deleted_role == role  # who deleted it first stays recorded
    assert await _counters(session, deed_id) == (0, 0)


async def test_moderation_counters_skip_the_doer_and_reporter(
    client: AsyncClient, session: AsyncSession
) -> None:
    """L1: removing and restoring a doer's or reporter's comment moves ``comment_count`` only; a
    third member's moves both."""
    reporter, doer, _, deed_id, by_member = await _setup(session)
    (filer,) = await members(session, 1)
    moderator = await _reviewer(session)
    _, limits = await load_limits(session)
    by_doer = (
        await _comments.post_comment(session, deed_id, doer.user_id, "Thanks all", limits=limits)
    ).id
    by_reporter = (
        await _comments.post_comment(session, deed_id, reporter.user_id, "Saw it", limits=limits)
    ).id
    await session.commit()
    assert await _counters(session, deed_id) == (3, 1)

    steps = [(by_doer, (2, 1), (3, 1)), (by_reporter, (2, 1), (3, 1)), (by_member, (2, 0), (3, 1))]
    for comment_id, removed, restored in steps:
        await _report(client, filer, comment_id)
        case = await _case(session, comment_id)
        r = await client.post(
            f"/api/admin/comment-reports/{case.id}/decision",
            headers=moderator.headers,
            json={"action": "remove", "note": "Spam link here"},
        )
        assert r.status_code == 200
        assert await _counters(session, deed_id) == removed
        r = await client.post(
            f"/api/admin/comments/{comment_id}/restore",
            headers=moderator.headers,
            json={"note": "Context was fine"},
        )
        assert r.status_code == 200
        assert await _counters(session, deed_id) == restored


async def test_keep_changes_nothing(client: AsyncClient, session: AsyncSession) -> None:
    _, _, _, deed_id, comment_id = await _setup(session)
    (member,) = await members(session, 1)
    moderator = await _reviewer(session)
    await _report(client, member, comment_id)
    case = await _case(session, comment_id)
    r = await client.post(
        f"/api/admin/comment-reports/{case.id}/decision",
        headers=moderator.headers,
        json={"action": "keep", "note": "Not against the rules"},
    )
    assert r.status_code == 200 and r.json()["case"]["decision"] == "kept"
    assert (await _comment(session, comment_id)).deleted_at is None
    assert (await post_of(session, deed_id)).comment_count == 1  # type: ignore[union-attr]
    # A new report opens a new case.
    (other,) = await members(session, 1)
    await _report(client, other, comment_id)
    new_case = await _case(session, comment_id)
    assert new_case.id != case.id and new_case.status == ReportCaseStatus.OPEN


@pytest.mark.parametrize("who", ["author", "doer", "reporter"])
async def test_conflict_of_interest(client: AsyncClient, session: AsyncSession, who: str) -> None:
    reporter, doer, author, _, comment_id = await _setup(session)
    involved = {"author": author, "doer": doer, "reporter": reporter}[who]
    await session.execute(update(User).where(User.id == involved.user_id).values(role=Role.ADMIN))
    await session.commit()
    (member,) = await members(session, 1)
    await _report(client, member, comment_id)
    case_id = (await _case(session, comment_id)).id  # failed requests expire loaded rows
    r = await client.post(
        f"/api/admin/comment-reports/{case_id}/decision",
        headers=involved.headers,
        json={"action": "remove", "note": "Removing this one"},
    )
    assert r.status_code == 403 and r.json()["code"] == "conflict_of_interest"
    moderator = await _reviewer(session)
    await client.post(
        f"/api/admin/comment-reports/{case_id}/decision",
        headers=moderator.headers,
        json={"action": "remove", "note": "Removing this one"},
    )
    r = await client.post(
        f"/api/admin/comments/{comment_id}/restore",
        headers=involved.headers,
        json={"note": "Bringing it back"},
    )
    assert r.status_code == 403 and r.json()["code"] == "conflict_of_interest"


async def test_restore_window_and_erasure(session: AsyncSession) -> None:
    _, _, _, _, comment_id = await _setup(session)
    (member, moderator) = await members(session, 2)
    _, limits = await load_limits(session)
    shared = await load_shared(session)
    from app.modules.feed import _reports

    await _reports.report_comment(session, comment_id, member.user_id, "spam", None, shared=shared)
    case = await _case(session, comment_id)
    removed_at = datetime.now(UTC)
    await _moderation.decide(
        session, moderator.user_id, case.id, "remove", "Spam link", shared=shared, now=removed_at
    )
    days = limits.deleted_comment_retention_days
    # Day 29: still kept and restorable; after the erasure job past day 30: not restorable.
    await _comments.erase_deleted_batch(session, limits, now=removed_at + timedelta(days=days - 1))
    assert (await _comment(session, comment_id)).body is not None
    with pytest.raises(Exception) as late:
        await _moderation.restore(
            session,
            moderator.user_id,
            comment_id,
            "Too late now",
            limits=limits,
            shared=shared,
            now=removed_at + timedelta(days=days, minutes=1),
        )
    assert getattr(late.value, "code", None) == "not_restorable"
    await _comments.erase_deleted_batch(session, limits, now=removed_at + timedelta(days=days + 1))
    assert (await _comment(session, comment_id)).body is None


async def test_author_history_counts_only_moderation_removals(session: AsyncSession) -> None:
    _, doer, author, deed_id, comment_id = await _setup(session)
    (member, moderator) = await members(session, 2)
    _, limits = await load_limits(session)
    shared = await load_shared(session)
    from app.modules.feed import _reports

    own = await _comments.post_comment(session, deed_id, author.user_id, "Oops", limits=limits)
    await _comments.delete_comment(session, own.id, author.user_id)
    by_doer = await _comments.post_comment(session, deed_id, author.user_id, "Hmm", limits=limits)
    await _comments.delete_comment(session, by_doer.id, doer.user_id)
    await _reports.report_comment(session, comment_id, member.user_id, "spam", None, shared=shared)
    case = await _case(session, comment_id)
    detail = await _moderation.case_detail(session, case.id, moderator.user_id, limits=limits)
    assert detail.author_removed_last_90_days == 0
    await _moderation.decide(
        session, moderator.user_id, case.id, "remove", "Spam posting", shared=shared
    )
    detail = await _moderation.case_detail(session, case.id, moderator.user_id, limits=limits)
    assert detail.author_removed_last_90_days == 1


async def test_notices_rules(client: AsyncClient, session: AsyncSession) -> None:
    _, doer, author, deed_id, comment_id = await _setup(session)
    (member, moderator, stranger) = await members(session, 3)
    shared = await load_shared(session)
    _, limits = await load_limits(session)
    from app.modules.feed import _reports

    # Deletions by the author, doer or reporter never create a notice.
    by_doer = await _comments.post_comment(session, deed_id, author.user_id, "Hmm", limits=limits)
    await _comments.delete_comment(session, by_doer.id, doer.user_id)
    await _reports.report_comment(session, comment_id, member.user_id, "spam", None, shared=shared)
    case = await _case(session, comment_id)
    await _moderation.decide(
        session, moderator.user_id, case.id, "remove", "Spam posting", shared=shared
    )
    await session.commit()
    notices = (await client.get("/api/feed/me/comment-notices", headers=author.headers)).json()
    assert [n["comment_id"] for n in notices] == [str(comment_id)]

    seen = f"/api/feed/me/comment-notices/{comment_id}/seen"
    assert (await client.post(seen, headers=stranger.headers)).status_code == 404
    for _ in range(2):
        assert (await client.post(seen, headers=author.headers)).status_code == 204
    assert (await client.get("/api/feed/me/comment-notices", headers=author.headers)).json() == []

    # Expiry after report_outcome_display_days.
    await session.execute(
        update(FeedComment)
        .where(FeedComment.id == comment_id)
        .values(
            removal_notice_seen_at=None,
            deleted_at=datetime.now(UTC) - timedelta(days=shared.report_outcome_display_days + 1),
        )
    )
    await session.commit()
    assert (await client.get("/api/feed/me/comment-notices", headers=author.headers)).json() == []


async def test_comment_decisions_are_append_only(session: AsyncSession) -> None:
    _, _, _, _, comment_id = await _setup(session)
    (member, moderator) = await members(session, 2)
    shared = await load_shared(session)
    from app.modules.feed import _reports

    await _reports.report_comment(session, comment_id, member.user_id, "spam", None, shared=shared)
    case = await _case(session, comment_id)
    await _moderation.decide(
        session, moderator.user_id, case.id, "keep", "Fine as is", shared=shared
    )
    for statement in (
        "UPDATE comment_decisions SET note = 'changed note' WHERE comment_id = :c",
        "DELETE FROM comment_decisions WHERE comment_id = :c",
    ):
        with pytest.raises(DBAPIError):
            async with session.begin_nested():
                await session.execute(text(statement), {"c": comment_id})


async def test_moderators_cannot_use_the_member_delete(
    client: AsyncClient, session: AsyncSession
) -> None:
    """Moderators and admins remove comments only through a report decision."""
    *_, comment_id = await _setup(session)
    for role in (Role.MODERATOR, Role.ADMIN):
        reviewer = await _reviewer(session, role)
        r = await client.delete(f"/api/feed/comments/{comment_id}", headers=reviewer.headers)
        assert r.status_code == 404 and r.json()["code"] == "comment_not_found"
