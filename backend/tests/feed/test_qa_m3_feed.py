"""QA5 (M3): feed and social rules that the module tests cover only in parts (docs/M3-DESIGN.md
§8 "Feed", "Social", "Events", "Config"; PO decisions Q24, Q26, Q28, Q36, Q38, Q40, Q41, Q42,
Q43).

- Top scores from real API likes and comments (Q43 self-likes weigh nothing, comments by the doer
  count, a deleted comment lowers the score at the next refresh).
- A battery of every feed action changes no ``deeds`` column, same-act counter, verification
  event, revision, or public deed count, and every event it emits carries IDs only.
- One walk over every feed endpoint's JSON: no coordinates, phone numbers, ward codes or user IDs.
- Tampered and cross-list cursors are 400 ``invalid_cursor`` (never a 500).

Everything runs inside the test's rolled-back transaction; checks are by this test's IDs.
Snapshots are computed with an explicit ``now`` in the future, so other files' rows can't enter
the windows.
"""

import base64
import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import OutboxEvent
from app.modules.deeds.models import Deed, DeedRevision, SameActGroup, VerificationEvent
from app.modules.feed import _top
from app.modules.feed._limits import load_limits
from app.modules.feed.models import CommentReportCase, FeedTopEntry, TopPeriod
from app.modules.identity.models import Role, User
from tests.deeds.helpers import CENTRE, Member, make_member, members
from tests.feed.conftest import FakePhotos
from tests.feed.helpers import post_of, verified_deed
from tests.feed.test_top import publish_feed

NOW = datetime(2092, 3, 1, 12, 0, tzinfo=UTC)
FUTURE = datetime(2092, 2, 28, 12, 0, tzinfo=UTC)  # inside every window at NOW


async def _reviewer(session: AsyncSession, role: Role = Role.MODERATOR) -> Member:
    m = await make_member(session)
    user = await session.get(User, m.user_id)
    assert user is not None
    user.role = role
    await session.commit()
    return m


async def _score(
    session: AsyncSession, period: TopPeriod, now: datetime, deed_id: uuid.UUID
) -> Any:
    _, limits = await load_limits(session)
    snap = await _top.compute_snapshot(session, period, limits, now=now)
    return await session.scalar(
        select(FeedTopEntry.score).where(
            FeedTopEntry.snapshot_id == snap.id, FeedTopEntry.deed_id == deed_id
        )
    )


# --------------------------------------------------- Top from real likes and comments (Q26, Q43)


async def test_top_score_from_real_likes_and_comments(
    client: AsyncClient, session: AsyncSession
) -> None:
    reporter, doer, a, b = await members(session, 4)
    deed_id = await verified_deed(session, reporter, doer, verified_at=FUTURE)
    await session.commit()
    _, limits = await load_limits(session)
    like_w, comment_w = limits.top_like_weight, limits.top_comment_weight
    like = f"/api/feed/deeds/{deed_id}/like"
    comments = f"/api/feed/deeds/{deed_id}/comments"

    for who in (doer, reporter, a, b):
        assert (await client.put(like, headers=who.headers)).status_code == 200
    by_doer = await client.post(comments, headers=doer.headers, json={"body": "Thank you all"})
    by_a = await client.post(comments, headers=a.headers, json={"body": "Lovely to see"})
    assert by_doer.status_code == by_a.status_code == 201
    post = await post_of(session, deed_id)
    assert post is not None
    counts = (post.like_count, post.top_like_count, post.comment_count, post.top_comment_count)
    assert counts == (4, 2, 2, 1)

    # The doer's and reporter's likes (Q43) and comments (L1) weigh nothing.
    expected = 2 * like_w + comment_w
    for period in TopPeriod:
        assert await _score(session, period, NOW, deed_id) == expected

    # A deleted comment lowers the score at the next refresh; a self-unlike changes nothing.
    r = await client.delete(f"/api/feed/comments/{by_a.json()['id']}", headers=a.headers)
    assert r.status_code == 204
    assert (await client.delete(like, headers=reporter.headers)).json()["like_count"] == 3
    later = NOW + timedelta(minutes=5)
    for period in TopPeriod:
        assert await _score(session, period, later, deed_id) == 2 * like_w
    # The doer deleting their own comment changes no score; b unliking removes the rest.
    await client.delete(f"/api/feed/comments/{by_doer.json()['id']}", headers=doer.headers)
    await client.delete(like, headers=b.headers)
    assert await _score(session, TopPeriod.WEEK, later + timedelta(minutes=5), deed_id) == like_w


async def test_top_window_change_by_publish_affects_every_period_without_code(
    session: AsyncSession,
) -> None:
    """``top_week_days`` / ``top_year_days`` follow config too (month is covered elsewhere)."""
    reporter, doer = await members(session, 2)
    deed_id = await verified_deed(session, reporter, doer, verified_at=NOW - timedelta(days=5))
    await session.commit()
    assert await _score(session, TopPeriod.WEEK, NOW, deed_id) is not None
    recent = await verified_deed(session, reporter, doer, verified_at=NOW - timedelta(days=2))
    await session.commit()
    # Windows must still grow (week <= month <= year), so all three shrink below 5 days.
    await publish_feed(session, top_week_days=3, top_month_days=4, top_year_days=4)
    later = NOW + timedelta(seconds=1)
    for period in TopPeriod:
        assert await _score(session, period, later, deed_id) is None, period
        assert await _score(session, period, later, recent) is not None, period


# --------------------------------------------------- nothing scored changes; events carry IDs only


async def _scored_state(
    client: AsyncClient, session: AsyncSession, deed_id: uuid.UUID, people: list[Member]
) -> tuple[Any, ...]:
    deed = (
        await session.scalars(
            select(Deed).where(Deed.id == deed_id).execution_options(populate_existing=True)
        )
    ).one()
    group = await session.get(SameActGroup, deed.same_act_group_id, populate_existing=True)
    assert group is not None
    deed_cols = tuple(getattr(deed, c.key) for c in Deed.__table__.columns if c.key != "location")
    group_cols = tuple(
        getattr(group, c.key) for c in SameActGroup.__table__.columns if c.key != "centre"
    )
    events = await session.scalar(
        select(func.count())
        .select_from(VerificationEvent)
        .where(VerificationEvent.deed_id == deed_id)
    )
    revisions = await session.scalar(
        select(func.count()).select_from(DeedRevision).where(DeedRevision.deed_id == deed_id)
    )
    counts = []
    for who in people:
        r = await client.get(f"/api/users/{who.handle}/deed-counts", headers=who.headers)
        assert r.status_code == 200, r.text
        counts.append(r.json())
    return deed_cols, group_cols, events, revisions, counts


_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_FORBIDDEN_PAYLOAD_KEYS = {
    "body",
    "text",
    "description",
    "excerpt",
    "note",
    "details",
    "lat",
    "lng",
    "location",
    "phone",
    "phone_e164",
    "ip",
    "ip_hash",
    "device_hash",
    "handle",
    "display_name",
}


async def test_feed_actions_change_nothing_scored_and_events_carry_ids_only(
    client: AsyncClient, session: AsyncSession
) -> None:
    reporter, doer, a, b = await members(session, 4)
    moderator = await _reviewer(session)
    deed_id = await verified_deed(session, reporter, doer, verified_at=FUTURE)
    await session.commit()
    people = [reporter, doer]
    before = await _scored_state(client, session, deed_id, people)
    started = await session.scalar(select(func.count()).select_from(OutboxEvent))
    assert started is not None
    base = f"/api/feed/deeds/{deed_id}"

    # Every member-side action, by the doer, the reporter and others.
    for who in (doer, reporter, a, b):
        assert (await client.put(f"{base}/like", headers=who.headers)).status_code == 200
        assert (await client.put(f"{base}/save", headers=who.headers)).status_code == 200
    assert (await client.delete(f"{base}/like", headers=a.headers)).status_code == 200
    assert (await client.delete(f"{base}/save", headers=a.headers)).status_code == 200
    posted = []
    for who, body in ((a, "So kind"), (b, "Wonderful"), (doer, "Thanks"), (a, "Again, thanks")):
        r = await client.post(f"{base}/comments", headers=who.headers, json={"body": body})
        assert r.status_code == 201, r.text
        posted.append(r.json()["id"])
    for who, comment_id in ((a, posted[0]), (reporter, posted[2])):
        r = await client.delete(f"/api/feed/comments/{comment_id}", headers=who.headers)
        assert r.status_code == 204
    for who in (doer, reporter):
        assert (await client.post(f"{base}/hide", headers=who.headers)).status_code == 200
        assert (await client.post(f"{base}/show", headers=who.headers)).status_code == 200
    r = await client.post(f"{base}/shares", headers=a.headers, json={"target": "whatsapp"})
    assert r.status_code == 204
    for target in (doer, reporter):
        r = await client.put(f"/api/users/{target.handle}/follow", headers=a.headers)
        assert r.status_code == 200
    r = await client.delete(f"/api/me/followers/{a.handle}", headers=doer.headers)
    assert r.status_code == 204

    # Comment report, moderator removal and restore.
    r = await client.post(
        f"/api/feed/comments/{posted[1]}/reports", headers=a.headers, json={"reason": "spam"}
    )
    assert r.status_code == 202
    case_id = await session.scalar(
        select(CommentReportCase.id).where(CommentReportCase.comment_id == uuid.UUID(posted[1]))
    )
    r = await client.post(
        f"/api/admin/comment-reports/{case_id}/decision",
        headers=moderator.headers,
        json={"action": "remove", "note": "Looks like spam"},
    )
    assert r.status_code == 200, r.text
    r = await client.post(
        f"/api/admin/comments/{posted[1]}/restore",
        headers=moderator.headers,
        json={"note": "It was fine after all"},
    )
    assert r.status_code == 200, r.text

    # Top snapshots with changed weights and windows.
    await publish_feed(session, top_like_weight=7, top_comment_weight=1, top_week_days=2)
    _, limits = await load_limits(session)
    for period in TopPeriod:
        await _top.compute_snapshot(session, period, limits, now=NOW)
    await session.commit()

    assert await _scored_state(client, session, deed_id, people) == before

    # The events these actions emitted carry IDs (and small enums) only.
    rows = list(
        await session.scalars(
            select(OutboxEvent)
            .where(OutboxEvent.event_type.like("feed.%"))
            .order_by(OutboxEvent.occurred_at.desc())
            .limit(200)
        )
    )
    ours = [
        e
        for e in rows
        if str(deed_id) in json.dumps(e.payload) or str(a.user_id) in json.dumps(e.payload)
    ]
    kinds = {e.event_type for e in ours}
    assert {
        "feed.deed_liked",
        "feed.comment_posted",
        "feed.user_followed",
        "feed.comment_moderation_decided",
    } <= kinds
    text = json.dumps([e.payload for e in ours])
    assert "So kind" not in text and "Looks like spam" not in text and "fine after" not in text
    for event in ours:
        assert not _FORBIDDEN_PAYLOAD_KEYS & set(event.payload), event.event_type
        for key, value in event.payload.items():
            if key.endswith("_id"):
                assert isinstance(value, str) and _UUID.match(value), (event.event_type, key)
            else:
                assert isinstance(value, str | int | None) and len(str(value)) <= 40, (
                    event.event_type,
                    key,
                    value,
                )
    # Deed likes: one event per real insert (doer, reporter, a, b), none for the unlike.
    likes = [e for e in ours if e.event_type == "feed.deed_liked"]
    assert len(likes) == 4


# --------------------------------------------------- no coordinates or private data anywhere


_COORD_KEYS = (
    '"lat"',
    '"lng"',
    '"latitude"',
    '"longitude"',
    '"location"',
    '"coordinates"',
    '"geom"',
    '"centre"',
    '"ward_code"',
    '"phone"',
    '"phone_e164"',
    '"device_hash"',
    '"ip_hash"',
    '"accuracy_m"',
)


def _assert_clean(path: str, body: Any, user_ids: list[uuid.UUID], phones: list[str]) -> None:
    text = json.dumps(body)
    for key in _COORD_KEYS:
        assert key not in text, (path, key)
    # Bangalore-looking "12.97, 77.59" pairs, and the seeded centre's digits.
    assert re.search(r"1[0-4]\.\d{3,}\D{1,6}7[5-9]\.\d{3,}", text) is None, path
    assert str(CENTRE[0]) not in text and str(CENTRE[1]) not in text, path
    for user_id in user_ids:
        assert str(user_id) not in text, (path, "user id")
    for phone in phones:
        assert phone[-10:] not in text, (path, "phone")


async def test_no_feed_response_contains_coordinates_phones_or_user_ids(
    client: AsyncClient, session: AsyncSession, photos: FakePhotos
) -> None:
    reporter, doer, viewer, author = await members(session, 4)
    moderator = await _reviewer(session)
    deed_id = await verified_deed(session, reporter, doer, verified_at=FUTURE)
    photos.add(deed_id)
    await session.commit()
    base = f"/api/feed/deeds/{deed_id}"
    writes: list[tuple[str, str, Any, Member]] = [
        ("PUT", f"{base}/like", None, viewer),
        ("PUT", f"{base}/save", None, viewer),
        ("PUT", f"/api/users/{doer.handle}/follow", None, viewer),
        ("POST", f"{base}/comments", {"body": "What a kind thing to do"}, author),
        ("POST", f"{base}/hide", None, doer),
        ("POST", f"{base}/show", None, doer),
    ]
    responses: list[tuple[str, Any]] = []
    comment_id = None
    for method, path, body, who in writes:
        r = await client.request(method, path, json=body, headers=who.headers)
        assert r.status_code in (200, 201), (path, r.text)
        responses.append((path, r.json()))
        if path.endswith("/comments"):
            comment_id = r.json()["id"]
    await client.put(f"/api/users/{viewer.handle}/follow", headers=doer.headers)
    r = await client.post(
        f"/api/feed/comments/{comment_id}/reports", headers=viewer.headers, json={"reason": "spam"}
    )
    assert r.status_code == 202
    case_id = await session.scalar(
        select(CommentReportCase.id).where(CommentReportCase.comment_id == uuid.UUID(comment_id))
    )
    decided = await client.post(
        f"/api/admin/comment-reports/{case_id}/decision",
        headers=moderator.headers,
        json={"action": "remove", "note": "Spam posting"},
    )
    assert decided.status_code == 200
    responses.append(("decision", decided.json()))
    _, limits = await load_limits(session)
    for period in TopPeriod:
        await _top.compute_snapshot(session, period, limits, now=datetime.now(UTC))
    await session.commit()

    reads: list[tuple[str, Member | None]] = [
        ("/api/feed?tab=nearby", viewer),
        ("/api/feed?tab=following", viewer),
        ("/api/feed?tab=top&period=week", viewer),
        ("/api/feed?tab=top&period=month", viewer),
        ("/api/feed?tab=top&period=year", viewer),
        (base, viewer),
        (base, doer),
        (base, reporter),
        (f"/api/public/deeds/{deed_id}", None),
        (f"{base}/comments", viewer),
        (f"/api/users/{doer.handle}/social", viewer),
        (f"/api/users/{doer.handle}/social", None),
        (f"/api/feed/users/{doer.handle}/deeds", viewer),
        ("/api/me/saved", viewer),
        ("/api/me/followers", doer),
        ("/api/me/following", viewer),
        ("/api/feed/options", viewer),
        ("/api/feed/me/notices", viewer),
        ("/api/feed/me/comment-notices", author),
        ("/api/admin/comment-reports?status=open", moderator),
        ("/api/admin/comment-reports?status=resolved", moderator),
        (f"/api/admin/comment-reports/{case_id}", moderator),
    ]
    for path, reader in reads:
        r = await client.get(path, headers=reader.headers if reader else {})
        assert r.status_code == 200, (path, r.text)
        responses.append((path, r.json()))

    everyone = [reporter, doer, viewer, author, moderator]
    phones = list(
        await session.scalars(
            select(User.phone_e164).where(User.id.in_([m.user_id for m in everyone]))
        )
    )
    assert all(phones)
    for path, body in responses:
        _assert_clean(path, body, [m.user_id for m in everyone], [p for p in phones if p])
    # The deed shows an area name, never more precise data.
    detail = next(b for p, b in responses if p == base)
    assert isinstance(detail["area_name"], str) and detail["area_name"]


# --------------------------------------------------- cursors (role file: tampered cursors)


def _b64(raw: str) -> str:
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


async def test_tampered_and_cross_list_cursors_are_refused_never_500(
    client: AsyncClient, session: AsyncSession
) -> None:
    reporter, doer, viewer = await members(session, 3)
    moderator = await _reviewer(session)
    deed_id = await verified_deed(session, reporter, doer, verified_at=FUTURE)
    _, limits = await load_limits(session)
    week_id = (
        await _top.compute_snapshot(session, TopPeriod.WEEK, limits, now=datetime.now(UTC))
    ).id
    await session.commit()
    await publish_feed(session, rate_feed_read_user="100000/60")
    some_id = uuid.uuid4()

    refused = {
        "garbage": "nonsense",
        "not base64": "%%%",
        "two parts of junk": _b64("x|y"),
        "naive timestamp": _b64(f"2026-01-01T00:00:00|{some_id}"),
        "bad uuid": _b64("2026-01-01T00:00:00+00:00|not-a-uuid"),
        "extra part": _b64(f"2026-01-01T00:00:00+00:00|{some_id}|1"),
        "binary": base64.urlsafe_b64encode(b"\xff\xfe\xfd\xfc").decode(),
        "huge": "A" * 5000,
    }
    keyset_paths = [
        "/api/feed?tab=nearby",
        "/api/feed?tab=following",
        "/api/me/saved",
        f"/api/feed/users/{doer.handle}/deeds",
        f"/api/feed/deeds/{deed_id}/comments",
        "/api/me/followers",
        "/api/me/following",
    ]
    for path in keyset_paths:
        sep = "&" if "?" in path else "?"
        for label, cursor in refused.items():
            r = await client.get(f"{path}{sep}cursor={cursor}", headers=viewer.headers)
            assert r.status_code == 400, (path, label, r.status_code, r.text)
            assert r.json()["code"] == "invalid_cursor", (path, label)
        # A Top or admin-queue cursor on a keyset list.
        for label, cursor in {
            "top cursor": _b64(f"top|week|{week_id}|1"),
            "queue cursor": _b64(f"queue|open|1|2026-01-01T00:00:00+00:00|{some_id}"),
        }.items():
            r = await client.get(f"{path}{sep}cursor={cursor}", headers=viewer.headers)
            assert r.status_code == 400, (path, label, r.text)

    # Extreme but well-formed keyset values are just an empty or ordinary page.
    for at in (
        "9999-12-31T23:59:59.999999-14:00",
        "0001-01-01T00:00:00+14:00",
        "2026-01-01T00:00:00+00:00\x00",  # CPython's parser stops at a NUL: still a valid time
    ):
        for path in keyset_paths:
            sep = "&" if "?" in path else "?"
            r = await client.get(
                f"{path}{sep}cursor={_b64(f'{at}|{some_id}')}", headers=viewer.headers
            )
            assert r.status_code in (200, 400), (path, at, r.status_code, r.text)

    top = "/api/feed?tab=top&period=week"
    for label, cursor in {
        **refused,
        "keyset cursor": _b64(f"2026-01-01T00:00:00+00:00|{some_id}"),
        "other period": _b64(f"top|month|{week_id}|1"),
        "unknown snapshot": _b64(f"top|week|{some_id}|1"),
        "rank 0": _b64(f"top|week|{week_id}|0"),
        "negative rank": _b64(f"top|week|{week_id}|-5"),
        "rank not a number": _b64(f"top|week|{week_id}|one"),
    }.items():
        r = await client.get(f"{top}&cursor={cursor}", headers=viewer.headers)
        assert r.status_code == 400, (label, r.status_code, r.text)
        assert r.json()["code"] == "invalid_cursor", label
    queue = "/api/admin/comment-reports?status=open"
    for label, cursor in {
        **refused,
        "resolved-queue cursor": _b64(f"queue|resolved|1|2026-01-01T00:00:00+00:00|{some_id}"),
        "keyset cursor": _b64(f"2026-01-01T00:00:00+00:00|{some_id}"),
        "priority not a number": _b64(f"queue|open|x|2026-01-01T00:00:00+00:00|{some_id}"),
    }.items():
        r = await client.get(f"{queue}&cursor={cursor}", headers=moderator.headers)
        assert r.status_code == 400, (label, r.status_code, r.text)


@pytest.mark.parametrize(
    "which",
    [
        "top rank",
        "comment queue priority",
        "photo queue priority",
    ],
)
async def test_out_of_range_numbers_in_cursors_never_500(
    client: AsyncClient, session: AsyncSession, which: str
) -> None:
    reporter, doer, viewer = await members(session, 3)
    moderator = await _reviewer(session)
    await verified_deed(session, reporter, doer, verified_at=FUTURE)
    _, limits = await load_limits(session)
    week_id = (
        await _top.compute_snapshot(session, TopPeriod.WEEK, limits, now=datetime.now(UTC))
    ).id
    await session.commit()
    big = 40_000  # past SMALLINT
    at = "2026-01-01T00:00:00+00:00"
    path, who = {
        "top rank": (
            f"/api/feed?tab=top&period=week&cursor={_b64(f'top|week|{week_id}|{big}')}",
            viewer,
        ),
        "comment queue priority": (
            "/api/admin/comment-reports?status=open"
            f"&cursor={_b64(f'queue|open|{big}|{at}|{uuid.uuid4()}')}",
            moderator,
        ),
        "photo queue priority": (
            f"/api/admin/photo-reports?status=open&cursor={_b64(f'open|{big}|{at}|{uuid.uuid4()}')}",
            moderator,
        ),
    }[which]
    r = await client.get(path, headers=who.headers)
    if which == "top rank":
        assert r.status_code == 400, r.text
        assert r.json()["code"] == "invalid_cursor"
    else:  # a priority past every real one is just an empty page
        assert r.status_code in (200, 400), r.text


@pytest.mark.parametrize("limit", ["0", "51", "-1", "abc"])
async def test_page_size_outside_bounds_is_422(
    client: AsyncClient, session: AsyncSession, limit: str
) -> None:
    (viewer,) = await members(session, 1)
    for path in ("/api/feed?tab=nearby", "/api/me/saved", "/api/me/followers"):
        sep = "&" if "?" in path else "?"
        r = await client.get(f"{path}{sep}limit={limit}", headers=viewer.headers)
        assert r.status_code == 422, (path, limit, r.text)


# --------------------------------------------------- IDOR: other people's IDs (feed)


async def test_other_peoples_ids_on_feed_endpoints(
    client: AsyncClient, session: AsyncSession
) -> None:
    """Every feed endpoint that takes an ID refuses it for someone it doesn't belong to, with the
    same answer as an unknown ID (no existence oracle)."""
    reporter, doer, author, stranger = await members(session, 4)
    deed_id = await verified_deed(session, reporter, doer, verified_at=FUTURE)
    hidden = await verified_deed(session, reporter, doer, verified_at=FUTURE)
    await session.commit()
    r = await client.post(
        f"/api/feed/deeds/{hidden}/comments", headers=author.headers, json={"body": "Well done"}
    )
    hidden_comment = r.json()["id"]
    await client.post(f"/api/feed/deeds/{hidden}/hide", headers=doer.headers)
    r = await client.post(
        f"/api/feed/deeds/{deed_id}/comments", headers=author.headers, json={"body": "Great job"}
    )
    comment = r.json()["id"]
    unknown = uuid.uuid4()

    async def same(method: str, path_of: Any, body: Any = None) -> None:
        mine = await client.request(method, path_of(hidden), json=body, headers=stranger.headers)
        other = await client.request(method, path_of(unknown), json=body, headers=stranger.headers)
        assert mine.status_code == other.status_code == 404, (path_of(hidden), mine.text)
        assert mine.json() == other.json()

    # A hidden deed is just as unknown to a stranger as a random ID, on every deed endpoint.
    for method, suffix, body in (
        ("GET", "", None),
        ("GET", "/comments", None),
        ("PUT", "/like", None),
        ("DELETE", "/like", None),
        ("PUT", "/save", None),
        ("POST", "/comments", {"body": "Hello there"}),
        ("POST", "/hide", None),
        ("POST", "/show", None),
        ("POST", "/shares", {"target": "copy_link"}),
    ):
        await same(method, lambda d, s=suffix: f"/api/feed/deeds/{d}{s}", body)
    # Unsave is always allowed (it clears only the caller's own row): the same 200 for both.
    unsave = [
        await client.delete(f"/api/feed/deeds/{d}/save", headers=stranger.headers)
        for d in (hidden, unknown)
    ]
    assert [u.status_code for u in unsave] == [200, 200]
    assert unsave[0].json() == unsave[1].json() == {"saved": False}
    pub_hidden = await client.get(f"/api/public/deeds/{hidden}")
    pub_unknown = await client.get(f"/api/public/deeds/{unknown}")
    assert pub_hidden.status_code == pub_unknown.status_code == 404
    assert pub_hidden.json() == pub_unknown.json()

    # Someone else's comment: delete, report on a hidden deed, notice seen.
    for cid in (comment, hidden_comment):
        mine = await client.delete(f"/api/feed/comments/{cid}", headers=stranger.headers)
        other = await client.delete(f"/api/feed/comments/{unknown}", headers=stranger.headers)
        assert mine.status_code == other.status_code == 404 and mine.json() == other.json()
    rep = await client.post(
        f"/api/feed/comments/{hidden_comment}/reports",
        headers=stranger.headers,
        json={"reason": "spam"},
    )
    rep_unknown = await client.post(
        f"/api/feed/comments/{unknown}/reports", headers=stranger.headers, json={"reason": "spam"}
    )
    assert rep.status_code == rep_unknown.status_code == 404 and rep.json() == rep_unknown.json()
    seen = await client.post(
        f"/api/feed/me/comment-notices/{comment}/seen", headers=stranger.headers
    )
    assert seen.status_code == 404
    # Removing a "follower" who doesn't follow me changes nothing for anyone.
    await client.put(f"/api/users/{doer.handle}/follow", headers=author.headers)
    r = await client.delete(f"/api/me/followers/{author.handle}", headers=stranger.headers)
    assert r.status_code in (204, 404)
    fans = (await client.get("/api/me/followers", headers=doer.headers)).json()["items"]
    assert [f["user"]["handle"] for f in fans] == [author.handle]
    # The comments are still there for the deed's people.
    listed = await client.get(f"/api/feed/deeds/{deed_id}/comments", headers=doer.headers)
    assert [c["id"] for c in listed.json()["items"]] == [comment]


# --------------------------------------------------- notices (Q41) and Q42


async def test_public_feed_notice_for_existing_member_survives_new_sessions(
    client: AsyncClient, session: AsyncSession
) -> None:
    """An existing (onboarded) member gets the notice too; once seen it is gone for a token
    issued later (another device), and other members still see theirs."""
    from app.core.security import create_access_token

    member, other = await members(session, 2)
    assert (await client.get("/api/feed/me/notices", headers=member.headers)).json() == [
        "public_feed_intro"
    ]
    r = await client.post("/api/feed/me/notices/public_feed_intro/seen", headers=member.headers)
    assert r.status_code == 204
    second_device, _ = create_access_token(member.user_id, "member", 0)
    headers = {"Authorization": f"Bearer {second_device}"}
    assert (await client.get("/api/feed/me/notices", headers=headers)).json() == []
    assert (await client.get("/api/feed/me/notices", headers=other.headers)).json() == [
        "public_feed_intro"
    ]
    for path in ("/api/feed/me/notices", "/api/feed/me/notices/public_feed_intro/seen"):
        method = "GET" if path.endswith("notices") else "POST"
        assert (await client.request(method, path)).status_code == 401


async def test_not_onboarded_member_can_report_a_comment_never(
    client: AsyncClient, session: AsyncSession
) -> None:
    """Q42: comment reports need onboarding (photo reports don't: tests/media)."""
    reporter, doer, author = await members(session, 3)
    newcomer = await make_member(session, onboarded=False)
    deed_id = await verified_deed(session, reporter, doer, verified_at=FUTURE)
    await session.commit()
    r = await client.post(
        f"/api/feed/deeds/{deed_id}/comments", headers=author.headers, json={"body": "Lovely"}
    )
    for method, path, body in (
        ("POST", f"/api/feed/comments/{r.json()['id']}/reports", {"reason": "spam"}),
        ("DELETE", f"/api/feed/deeds/{deed_id}/save", None),
        ("DELETE", f"/api/users/{doer.handle}/follow", None),
    ):
        resp = await client.request(method, path, json=body, headers=newcomer.headers)
        assert resp.status_code == 403 and resp.json()["code"] == "onboarding_required", path
    # They can still read comments and the notices.
    assert (
        await client.get(f"/api/feed/deeds/{deed_id}/comments", headers=newcomer.headers)
    ).status_code == 200
    assert (
        await client.get("/api/feed/me/comment-notices", headers=newcomer.headers)
    ).status_code == 200


# --------------------------------------------------- rate limits not covered elsewhere


@pytest.mark.parametrize(
    ("spec", "method", "suffix", "body"),
    [
        ("rate_save_user", "PUT", "/save", None),
        ("rate_hide_user", "POST", "/hide", None),
        ("rate_share_user", "POST", "/shares", {"target": "copy_link"}),
        ("rate_like_ip", "PUT", "/like", None),
        ("rate_comment_user_daily", "POST", "/comments", {"body": "Thank you"}),
    ],
)
async def test_each_write_rate_spec_comes_from_config(
    client: AsyncClient,
    session: AsyncSession,
    spec: str,
    method: str,
    suffix: str,
    body: Any,
) -> None:
    reporter, doer, other = await members(session, 3)
    deed_id = await verified_deed(session, reporter, doer, verified_at=FUTURE)
    await session.commit()
    await publish_feed(session, **{spec: "2/86400"})
    path = f"/api/feed/deeds/{deed_id}{suffix}"
    # Two hits are allowed, the third is refused. Hide and show share the hide key; for the
    # per-IP spec, three different accounts share the test client's IP.
    first = (method, path, body, doer)
    second = (
        ("POST", f"/api/feed/deeds/{deed_id}/show", None, doer)
        if suffix == "/hide"
        else (method, path, body, reporter if spec == "rate_like_ip" else doer)
    )
    third = (method, path, body, other if spec == "rate_like_ip" else doer)
    for m, p, b, who in (first, second):
        r = await client.request(m, p, json=b, headers=who.headers)
        assert r.status_code in (200, 201, 204), (spec, r.text)
    m, p, b, who = third
    r = await client.request(m, p, json=b, headers=who.headers)
    assert (r.status_code, r.json()["code"]) == (429, "rate_limited"), spec


async def test_follow_rate_limits_by_account_and_ip(
    client: AsyncClient, session: AsyncSession
) -> None:
    me, a, b, c, other = await members(session, 5)
    await publish_feed(session, rate_follow_user="2/3600")
    assert (
        await client.put(f"/api/users/{a.handle}/follow", headers=me.headers)
    ).status_code == 200
    assert (
        await client.delete(f"/api/users/{a.handle}/follow", headers=me.headers)
    ).status_code == 200
    r = await client.put(f"/api/users/{b.handle}/follow", headers=me.headers)
    assert (r.status_code, r.json()["code"]) == (429, "rate_limited")
    r = await client.delete(f"/api/me/followers/{c.handle}", headers=me.headers)
    assert r.status_code == 429  # removing a follower uses the same keys
    await publish_feed(session, rate_follow_user="100/3600", rate_follow_ip="1/3600")
    r = await client.put(f"/api/users/{c.handle}/follow", headers=other.headers)
    assert r.status_code == 429  # same IP as me, already over 1


@pytest.mark.parametrize("flag", ["comment_block_links", "comment_block_phone_numbers"])
async def test_link_and_phone_rules_each_have_their_own_switch(
    client: AsyncClient, session: AsyncSession, flag: str
) -> None:
    """Q37 through the API: with a flag off, that kind of comment is accepted; the other rule
    still applies."""
    reporter, doer, member = await members(session, 3)
    deed_id = await verified_deed(session, reporter, doer, verified_at=FUTURE)
    await session.commit()
    path = f"/api/feed/deeds/{deed_id}/comments"
    link, phone = "see www.example.com for more", "call +91 98450-12345 any time"
    for body, code in ((link, "comment_has_link"), (phone, "comment_has_phone")):
        r = await client.post(path, headers=member.headers, json={"body": body})
        assert (r.status_code, r.json()["code"]) == (422, code)
    await publish_feed(session, **{flag: False})
    allowed, still = (link, phone) if flag == "comment_block_links" else (phone, link)
    assert (
        await client.post(path, headers=member.headers, json={"body": allowed})
    ).status_code == 201
    assert (
        await client.post(path, headers=member.headers, json={"body": still})
    ).status_code == 422
