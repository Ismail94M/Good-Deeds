"""Security review fixes in the feed (SEC2: M1, L2, L8, L9, L11).

Robust to rows other test files committed: queues are checked by this test's case ids.
"""

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import engine
from app.modules.identity.models import Role, User
from tests.deeds.helpers import Member, make_member, members
from tests.feed.helpers import verified_deed
from tests.feed.test_top import publish_feed


async def _make_reviewer(session: AsyncSession, member: Member) -> None:
    await session.execute(update(User).where(User.id == member.user_id).values(role=Role.MODERATOR))
    await session.commit()


async def _comment(client: AsyncClient, deed_id: uuid.UUID, author: Member) -> str:
    r = await client.post(
        f"/api/feed/deeds/{deed_id}/comments", headers=author.headers, json={"body": "Hmm okay"}
    )
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


async def _case_id(client: AsyncClient, reviewer: Member, comment_id: str) -> str | None:
    """The case of ``comment_id`` in ``reviewer``'s open queue, or None if hidden from them."""
    body = (
        await client.get("/api/admin/comment-reports?limit=50", headers=reviewer.headers)
    ).json()
    found = [i["case_id"] for i in body["items"] if i["comment_excerpt"] == "Hmm okay"]
    return found[0] if found else None


# --------------------------------------------------- M1 and L11: parties never see a case


@pytest.mark.parametrize("party", ["author", "doer", "reporter", "filer"])
async def test_parties_never_see_or_decide_a_case(
    client: AsyncClient, session: AsyncSession, party: str
) -> None:
    reporter, doer, author, filer, outsider = await members(session, 5)
    deed_id = await verified_deed(session, reporter, doer)
    await session.commit()
    comment_id = await _comment(client, deed_id, author)
    r = await client.post(
        f"/api/feed/comments/{comment_id}/reports", headers=filer.headers, json={"reason": "spam"}
    )
    assert r.status_code == 202
    who = {"author": author, "doer": doer, "reporter": reporter, "filer": filer}[party]
    await _make_reviewer(session, who)
    await _make_reviewer(session, outsider)

    case_id = await _case_id(client, outsider, comment_id)
    assert case_id is not None
    outsider_counts = (
        await client.get("/api/admin/comment-reports", headers=outsider.headers)
    ).json()["open_count"]
    party_view = (
        await client.get("/api/admin/comment-reports?limit=50", headers=who.headers)
    ).json()
    assert case_id not in {i["case_id"] for i in party_view["items"]}
    assert party_view["open_count"] == outsider_counts - 1
    detail = await client.get(f"/api/admin/comment-reports/{case_id}", headers=who.headers)
    assert detail.status_code == 404 and detail.json()["code"] == "case_not_found"

    decide = await client.post(
        f"/api/admin/comment-reports/{case_id}/decision",
        headers=who.headers,
        json={"action": "remove", "note": "Removing this"},
    )
    assert decide.status_code == 403 and decide.json()["code"] == "conflict_of_interest"
    ok = await client.post(
        f"/api/admin/comment-reports/{case_id}/decision",
        headers=outsider.headers,
        json={"action": "remove", "note": "Removing this"},
    )
    assert ok.status_code == 200
    restore = await client.post(
        f"/api/admin/comments/{comment_id}/restore",
        headers=who.headers,
        json={"note": "Bring it back"},
    )
    assert restore.status_code == 403 and restore.json()["code"] == "conflict_of_interest"


# --------------------------------------------------- L2: parties act without onboarding


async def test_hide_show_and_own_delete_need_only_sign_in(
    client: AsyncClient, session: AsyncSession
) -> None:
    reporter = (await members(session, 1))[0]
    doer = await make_member(session, onboarded=False)
    stranger = await make_member(session, onboarded=False)
    deed_id = await verified_deed(session, reporter, doer)
    await session.commit()
    hide = await client.post(f"/api/feed/deeds/{deed_id}/hide", headers=doer.headers)
    assert hide.status_code == 200 and hide.json()["hidden_by_me"] is True
    show = await client.post(f"/api/feed/deeds/{deed_id}/show", headers=doer.headers)
    assert show.status_code == 200 and show.json()["listed"] is True
    # The party check still applies.
    assert (
        await client.post(f"/api/feed/deeds/{deed_id}/hide", headers=stranger.headers)
    ).status_code == 404

    comment_id = await _comment(client, deed_id, reporter)
    # The doer (not onboarded) may delete any comment on their deed; a stranger may not.
    assert (
        await client.delete(f"/api/feed/comments/{comment_id}", headers=stranger.headers)
    ).status_code == 404
    assert (
        await client.delete(f"/api/feed/comments/{comment_id}", headers=doer.headers)
    ).status_code == 204


# --------------------------------------------------- L8: NUL characters


async def test_nul_characters_are_refused(client: AsyncClient, session: AsyncSession) -> None:
    reporter, doer, author, member, moderator = await members(session, 5)
    deed_id = await verified_deed(session, reporter, doer)
    await session.commit()
    r = await client.post(
        f"/api/feed/deeds/{deed_id}/comments", headers=author.headers, json={"body": "Hi\x00there"}
    )
    assert r.status_code == 422 and r.json()["code"] == "invalid_text"
    comment_id = await _comment(client, deed_id, author)
    r = await client.post(
        f"/api/feed/comments/{comment_id}/reports",
        headers=member.headers,
        json={"reason": "spam", "details": "bad\x00"},
    )
    assert r.status_code == 422 and r.json()["code"] == "invalid_text"
    await client.post(
        f"/api/feed/comments/{comment_id}/reports", headers=member.headers, json={"reason": "spam"}
    )
    await _make_reviewer(session, moderator)
    case_id = await _case_id(client, moderator, comment_id)
    r = await client.post(
        f"/api/admin/comment-reports/{case_id}/decision",
        headers=moderator.headers,
        json={"action": "remove", "note": "Note\x00here"},
    )
    assert r.status_code == 422 and r.json()["code"] == "invalid_text"
    await client.post(
        f"/api/admin/comment-reports/{case_id}/decision",
        headers=moderator.headers,
        json={"action": "remove", "note": "Note here"},
    )
    r = await client.post(
        f"/api/admin/comments/{comment_id}/restore",
        headers=moderator.headers,
        json={"note": "Back\x00again"},
    )
    assert r.status_code == 422 and r.json()["code"] == "invalid_text"


def test_database_errors_hide_parameters() -> None:
    assert engine.sync_engine.hide_parameters is True


# --------------------------------------------------- L9: rate limits


async def test_comment_delete_and_report_ip_are_rate_limited(
    client: AsyncClient, session: AsyncSession
) -> None:
    reporter, doer, author, a, b = await members(session, 5)
    deed_id = await verified_deed(session, reporter, doer)
    await session.commit()
    first, second = await _comment(client, deed_id, author), await _comment(client, deed_id, author)
    await publish_feed(session, rate_comment_user_daily="1/86400", rate_comment_ip="1/3600")
    assert (
        await client.delete(f"/api/feed/comments/{first}", headers=author.headers)
    ).status_code == 204
    limited = await client.delete(f"/api/feed/comments/{second}", headers=author.headers)
    assert limited.status_code == 429
    # Reports: the IP key (same client IP for both members).
    r = await client.post(
        f"/api/feed/comments/{second}/reports", headers=a.headers, json={"reason": "spam"}
    )
    assert r.status_code == 202
    r = await client.post(
        f"/api/feed/comments/{second}/reports", headers=b.headers, json={"reason": "spam"}
    )
    assert r.status_code == 429


async def test_notices_and_admin_reads_are_rate_limited(
    client: AsyncClient, session: AsyncSession
) -> None:
    member, moderator = await members(session, 2)
    await _make_reviewer(session, moderator)
    await publish_feed(session, rate_feed_read_user="2/900")
    assert (await client.get("/api/feed/me/notices", headers=member.headers)).status_code == 200
    assert (
        await client.get("/api/feed/me/comment-notices", headers=member.headers)
    ).status_code == 200
    assert (
        await client.post("/api/feed/me/notices/public_feed_intro/seen", headers=member.headers)
    ).status_code == 429
    for _ in range(2):
        assert (
            await client.get("/api/admin/comment-reports", headers=moderator.headers)
        ).status_code == 200
    assert (
        await client.get(f"/api/admin/comment-reports/{uuid.uuid4()}", headers=moderator.headers)
    ).status_code == 429
