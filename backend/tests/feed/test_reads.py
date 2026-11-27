"""Feed reads (docs/M3-DESIGN.md §3.4 #1, #2, #10, #11, #12, §3.4b #7; test plan §8 "Feed",
"Social").

Posts that must come first in Nearby are published in the future (2090), so data committed by
other test files can't get between them.
"""

import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.modules.deeds._limits import load_limits
from app.modules.feed.models import FeedLike, Follow, PostState
from tests.deeds.helpers import Member, make_member, members
from tests.feed.conftest import FakePhotos
from tests.feed.helpers import (
    follow,
    save,
    set_post,
    verified_deed,
    withdraw_directly,
)

FUTURE = datetime(2090, 1, 1, tzinfo=UTC)
POST_KEYS = {
    "deed_id",
    "doer",
    "reporter",
    "category",
    "category_label",
    "area_name",
    "occurred_at",
    "published_at",
    "description",
    "photos",
    "like_count",
    "comment_count",
    "liked",
    "saved",
    "following_doer",
    "my_role",
    "rank",
    "share_url",
}


async def _get(client: AsyncClient, path: str, who: Member | None = None) -> Any:
    response = await client.get(path, headers=who.headers if who else {})
    assert response.status_code == 200, response.text
    return response.json()


async def _ids(client: AsyncClient, path: str, who: Member) -> list[str]:
    return [item["deed_id"] for item in (await _get(client, path, who))["items"]]


def _no_coordinates_or_ids(body: Any, *user_ids: uuid.UUID) -> None:
    text = json.dumps(body)
    for key in ('"lat"', '"lng"', '"location"', '"ward_code"', '"doer_id"', '"reporter_id"'):
        assert key not in text
    assert re.search(r"\b1[0-3]\.\d{3,}\s*,\s*7[6-8]\.\d{3,}", text) is None
    for user_id in user_ids:
        assert str(user_id) not in text


# --------------------------------------------------- Nearby


async def test_nearby_pages_every_post_once_in_order(
    client: AsyncClient, session: AsyncSession
) -> None:
    reporter, doer, viewer = await members(session, 3)
    keys: list[tuple[datetime, uuid.UUID]] = []
    for i in range(45):
        # Groups of three share a published_at, so the deed_id tie-break matters.
        at = FUTURE + timedelta(minutes=i // 3)
        keys.append((at, await verified_deed(session, reporter, doer, verified_at=at)))
    await session.commit()
    expected = [str(d) for _, d in sorted(keys, reverse=True)]
    seen: list[str] = []
    cursor: str | None = None
    for page_no in range(3):
        path = "/api/feed?tab=nearby" + (f"&cursor={cursor}" if cursor else "")
        body = await _get(client, path, viewer)
        assert len(body["items"]) == 20 or page_no == 2
        seen += [i["deed_id"] for i in body["items"]]
        cursor = body["next_cursor"]
        if page_no == 0:
            # A post published between page loads doesn't duplicate anything.
            await verified_deed(session, reporter, doer, verified_at=FUTURE + timedelta(days=1))
            await session.commit()
    assert seen[:45] == expected


async def test_bad_cursor_is_400(client: AsyncClient, session: AsyncSession) -> None:
    (viewer,) = await members(session, 1)
    for path in ("/api/feed?tab=nearby&cursor=nonsense", "/api/me/saved?cursor=%%%"):
        response = await client.get(path, headers=viewer.headers)
        assert response.status_code == 400, path
        assert response.json()["code"] == "invalid_cursor"


async def test_feed_tab_and_period_are_validated(
    client: AsyncClient, session: AsyncSession
) -> None:
    (viewer,) = await members(session, 1)
    for path in (
        "/api/feed?tab=nearby&period=week",
        "/api/feed?tab=following&period=month",
        "/api/feed?tab=top&period=decade",
        "/api/feed?tab=popular",
        "/api/feed",
    ):
        response = await client.get(path, headers=viewer.headers)
        assert response.status_code == 422, path
    assert (await client.get("/api/feed?tab=nearby")).status_code == 401


async def test_non_onboarded_member_can_read(client: AsyncClient, session: AsyncSession) -> None:
    """Q42: reading needs only a signed-in user."""
    reporter, doer = await members(session, 2)
    newcomer = await make_member(session, onboarded=False)
    deed_id = await verified_deed(session, reporter, doer, verified_at=FUTURE)
    await session.commit()
    for path in (
        "/api/feed?tab=nearby",
        "/api/feed?tab=following",
        "/api/feed?tab=top",
        f"/api/feed/deeds/{deed_id}",
        f"/api/feed/users/{doer.handle}/deeds",
        f"/api/users/{doer.handle}/social",
        "/api/me/saved",
        "/api/feed/options",
    ):
        response = await client.get(path, headers=newcomer.headers)
        assert response.status_code == 200, (path, response.text)


# --------------------------------------------------- composition


async def test_post_shape_and_viewer_fields(
    client: AsyncClient, session: AsyncSession, photos: FakePhotos
) -> None:
    reporter, doer, viewer = await members(session, 3)
    deed_id = await verified_deed(session, reporter, doer, verified_at=FUTURE)
    photo = photos.add(deed_id)
    await set_post(session, deed_id, like_count=4, top_like_count=3, comment_count=2)
    session.add(FeedLike(deed_id=deed_id, user_id=viewer.user_id))
    await save(session, viewer, deed_id)
    await follow(session, viewer, doer)
    await session.commit()

    body = await _get(client, "/api/feed?tab=nearby", viewer)
    item = next(i for i in body["items"] if i["deed_id"] == str(deed_id))
    assert set(item) == POST_KEYS
    assert item["doer"] == {
        "handle": doer.handle,
        "display_name": doer.display_name,
        "avatar": None,
    }
    assert item["reporter"] == {
        "handle": reporter.handle,
        "display_name": reporter.display_name,
        "avatar": None,
    }
    assert item["category"] == "animals"
    labels = {c.slug: c.label for c in (await load_limits(session))[1].categories}
    assert item["category_label"] == labels["animals"] != "animals"
    assert item["description"] == "Helped a stray dog across the road"
    assert isinstance(item["area_name"], str) and item["area_name"]
    assert (item["like_count"], item["comment_count"]) == (4, 2)
    assert (item["liked"], item["saved"], item["following_doer"]) == (True, True, True)
    assert item["my_role"] is None and item["rank"] is None
    assert item["share_url"] == f"{get_settings().web_base_url.rstrip('/')}/d/{deed_id}"
    assert [p["id"] for p in item["photos"]] == [str(photo.id)]
    _no_coordinates_or_ids(body, reporter.user_id, doer.user_id, viewer.user_id)

    as_doer = await _get(client, "/api/feed?tab=nearby", doer)
    mine = next(i for i in as_doer["items"] if i["deed_id"] == str(deed_id))
    assert mine["my_role"] == "doer" and not mine["liked"] and not mine["saved"]
    as_reporter = await _get(client, "/api/feed?tab=nearby", reporter)
    assert (
        next(i for i in as_reporter["items"] if i["deed_id"] == str(deed_id))["my_role"]
        == "reporter"
    )


async def test_withdrawn_deed_disappears_everywhere_before_its_handler_runs(
    client: AsyncClient, session: AsyncSession
) -> None:
    """D37 live check: the post still says ``published`` but the deed is withdrawn."""
    reporter, doer, viewer = await members(session, 3)
    deed_id = await verified_deed(session, reporter, doer, verified_at=FUTURE)
    await save(session, viewer, deed_id)
    await session.commit()
    assert str(deed_id) in await _ids(client, "/api/feed?tab=nearby", viewer)

    await withdraw_directly(session, deed_id)
    await session.commit()
    assert str(deed_id) not in await _ids(client, "/api/feed?tab=nearby", viewer)
    assert str(deed_id) not in await _ids(client, "/api/me/saved", viewer)
    assert str(deed_id) not in await _ids(client, f"/api/feed/users/{doer.handle}/deeds", viewer)
    for who in (viewer, doer, reporter):
        response = await client.get(f"/api/feed/deeds/{deed_id}", headers=who.headers)
        assert response.status_code == 404
        assert response.json()["code"] == "deed_not_found"
    assert (await client.get(f"/api/public/deeds/{deed_id}")).status_code == 404


# --------------------------------------------------- Following (Q25 = A)


async def test_following_shows_followed_doers_and_reporters(
    client: AsyncClient, session: AsyncSession
) -> None:
    a, b, c, d, viewer = await members(session, 5)
    by_a = await verified_deed(session, a, b, verified_at=FUTURE)  # reporter a, doer b
    of_c = await verified_deed(session, d, c, verified_at=FUTURE)  # reporter d, doer c
    other = await verified_deed(session, d, b, verified_at=FUTURE)  # reporter d, doer b
    await session.commit()
    assert await _ids(client, "/api/feed?tab=following", viewer) == []

    await follow(session, viewer, a)  # a reporter
    await follow(session, viewer, c)  # a doer
    await session.commit()
    ids = await _ids(client, "/api/feed?tab=following", viewer)
    assert set(ids) == {str(by_a), str(of_c)}
    assert str(other) not in ids

    await session.execute(delete(Follow).where(Follow.follower_id == viewer.user_id))
    await session.commit()
    assert await _ids(client, "/api/feed?tab=following", viewer) == []


# --------------------------------------------------- deed page (#2)


async def test_deed_page_and_visibility(client: AsyncClient, session: AsyncSession) -> None:
    reporter, doer, stranger = await members(session, 3)
    deed_id = await verified_deed(session, reporter, doer, verified_at=FUTURE)
    await session.commit()

    body = await _get(client, f"/api/feed/deeds/{deed_id}", stranger)
    assert set(body) == POST_KEYS | {"beneficiaries_count", "verification", "visibility"}
    assert body["visibility"] is None
    assert body["verification"] == "qr_scan" and body["beneficiaries_count"] == 2
    _no_coordinates_or_ids(body, reporter.user_id, doer.user_id)

    as_doer = await _get(client, f"/api/feed/deeds/{deed_id}", doer)
    assert as_doer["visibility"] == {
        "listed": True,
        "hidden_by_me": False,
        "hidden_by_other": False,
        "can_hide": True,
        "can_show": False,
    }

    await set_post(session, deed_id, hidden_by_doer_at=datetime.now(UTC))
    await session.commit()
    response = await client.get(f"/api/feed/deeds/{deed_id}", headers=stranger.headers)
    assert response.status_code == 404 and response.json()["code"] == "deed_not_found"
    assert (await _get(client, f"/api/feed/deeds/{deed_id}", doer))["visibility"] == {
        "listed": False,
        "hidden_by_me": True,
        "hidden_by_other": False,
        "can_hide": False,
        "can_show": True,
    }
    assert (await _get(client, f"/api/feed/deeds/{deed_id}", reporter))["visibility"] == {
        "listed": False,
        "hidden_by_me": False,
        "hidden_by_other": True,
        "can_hide": True,
        "can_show": False,
    }
    assert str(deed_id) not in await _ids(client, "/api/feed?tab=nearby", doer)

    # Held (M5) or withdrawn: not even the doer or reporter.
    await set_post(session, deed_id, state=PostState.HELD)
    await session.commit()
    assert (await client.get(f"/api/feed/deeds/{deed_id}", headers=doer.headers)).status_code == 404
    missing = await client.get(f"/api/feed/deeds/{uuid.uuid4()}", headers=doer.headers)
    assert missing.status_code == 404 and missing.json() == response.json()


# --------------------------------------------------- public page (#10)


async def test_public_page(client: AsyncClient, session: AsyncSession, photos: FakePhotos) -> None:
    reporter, doer = await members(session, 2)
    deed_id = await verified_deed(session, reporter, doer, verified_at=FUTURE)
    photos.add(deed_id)
    hidden = await verified_deed(session, reporter, doer, verified_at=FUTURE)
    await set_post(session, hidden, hidden_by_reporter_at=datetime.now(UTC))
    withdrawn = await verified_deed(session, reporter, doer, verified_at=FUTURE)
    await withdraw_directly(session, withdrawn)
    await session.commit()

    response = await client.get(f"/api/public/deeds/{deed_id}")  # no auth
    assert response.status_code == 200, response.text
    assert set(response.json()) == {
        "deed_id",
        "doer",
        "reporter",
        "category",
        "category_label",
        "area_name",
        "occurred_at",
        "description",
        "photos",
        "like_count",
        "comment_count",
    }
    _no_coordinates_or_ids(response.json(), reporter.user_id, doer.user_id)
    headers = {
        "cache-control": "no-store",
        "referrer-policy": "strict-origin-when-cross-origin",
        "x-robots-tag": "noindex, nofollow",
    }
    for key, value in headers.items():
        assert response.headers[key] == value

    bodies = []
    for gone in (hidden, withdrawn, uuid.uuid4()):
        r = await client.get(f"/api/public/deeds/{gone}")
        assert r.status_code == 404
        for key, value in headers.items():
            assert r.headers[key] == value
        bodies.append(r.json())
    assert bodies[0] == bodies[1] == bodies[2]
    assert bodies[0]["code"] == "deed_unavailable"


async def test_public_page_is_rate_limited_per_ip(
    client: AsyncClient, session: AsyncSession
) -> None:
    from tests.feed.test_top import publish_feed

    await publish_feed(session, rate_public_deed_ip="2/900")
    for _ in range(2):
        assert (await client.get(f"/api/public/deeds/{uuid.uuid4()}")).status_code == 404
    limited = await client.get(f"/api/public/deeds/{uuid.uuid4()}")
    assert limited.status_code == 429
    assert limited.headers["x-robots-tag"] == "noindex, nofollow"


# --------------------------------------------------- profiles and social (#11)


async def test_user_deeds_lists_listed_deeds_as_doer(
    client: AsyncClient, session: AsyncSession
) -> None:
    doer, reporter, viewer = await members(session, 3)
    older = await verified_deed(session, reporter, doer, verified_at=FUTURE)
    newer = await verified_deed(session, reporter, doer, verified_at=FUTURE + timedelta(hours=1))
    hidden = await verified_deed(session, reporter, doer, verified_at=FUTURE)
    await set_post(session, hidden, hidden_by_doer_at=datetime.now(UTC))
    as_reporter = await verified_deed(session, doer, reporter, verified_at=FUTURE)
    await session.commit()
    ids = await _ids(client, f"/api/feed/users/{doer.handle}/deeds", viewer)
    assert ids == [str(newer), str(older)]
    assert str(as_reporter) not in ids
    response = await client.get("/api/feed/users/nobody-here/deeds", headers=viewer.headers)
    assert response.status_code == 404 and response.json()["code"] == "user_not_found"


async def test_social_counts_only(client: AsyncClient, session: AsyncSession) -> None:
    target, a, b, c = await members(session, 4)
    await follow(session, a, target)
    await follow(session, b, target)
    await follow(session, target, c)
    await session.commit()
    path = f"/api/users/{target.handle}/social"
    assert await _get(client, path) == {
        "followers": 2,
        "following": 1,
        "is_following": None,
        "is_me": False,
    }
    assert (await _get(client, path, a))["is_following"] is True
    assert (await _get(client, path, c))["is_following"] is False
    assert (await _get(client, path, target))["is_me"] is True
    assert (await client.get("/api/users/nobody-here/social")).status_code == 404
    # Q36: nobody else's lists.
    for other in ("followers", "following"):
        assert (await client.get(f"/api/users/{target.handle}/{other}")).status_code == 404
    bad = await client.get(path, headers={"Authorization": "Bearer nonsense"})
    assert bad.status_code == 401


# --------------------------------------------------- saved (#12)


async def test_saved_newest_first_and_hidden_drops_out(
    client: AsyncClient, session: AsyncSession
) -> None:
    reporter, doer, viewer = await members(session, 3)
    first = await verified_deed(session, reporter, doer, verified_at=FUTURE)
    second = await verified_deed(session, reporter, doer, verified_at=FUTURE)
    now = datetime.now(UTC)
    await save(session, viewer, first, at=now - timedelta(minutes=5))
    await save(session, viewer, second, at=now)
    await save(session, doer, first, at=now)
    await session.commit()
    assert await _ids(client, "/api/me/saved", viewer) == [str(second), str(first)]

    await set_post(session, first, hidden_by_doer_at=now)
    await session.commit()
    assert await _ids(client, "/api/me/saved", viewer) == [str(second)]
    # The doer still sees their own hidden deed in their saves.
    assert await _ids(client, "/api/me/saved", doer) == [str(first)]

    await set_post(session, first, hidden_by_doer_at=None)
    await session.commit()
    assert await _ids(client, "/api/me/saved", viewer) == [str(second), str(first)]
    # Paging with a limit.
    body = await _get(client, "/api/me/saved?limit=1", viewer)
    assert [i["deed_id"] for i in body["items"]] == [str(second)]
    nxt = await _get(client, f"/api/me/saved?limit=1&cursor={body['next_cursor']}", viewer)
    assert [i["deed_id"] for i in nxt["items"]] == [str(first)] and nxt["next_cursor"] is None


# --------------------------------------------------- options (§3.4b #7)


async def test_feed_options(client: AsyncClient, session: AsyncSession) -> None:
    (viewer,) = await members(session, 1)
    body = await _get(client, "/api/feed/options", viewer)
    assert body["comment_max_chars"] == 300
    assert body["top_badge_ranks"] == 3
    assert body["top_periods"] == [
        {"id": "week", "days": 7},
        {"id": "month", "days": 30},
        {"id": "year", "days": 365},
    ]
    assert [(r["id"], r["priority"]) for r in body["comment_report_reasons"]] == [
        ("threat_or_harassment", 1),
        ("personal_info", 1),
        ("hate", 2),
        ("spam", 3),
        ("other", 3),
    ]
    assert all(r["label"] for r in body["comment_report_reasons"])
    assert body["report_details_max"] == 500
    assert (await client.get("/api/feed/options")).status_code == 401


@pytest.mark.parametrize("path", ["/api/feed?tab=nearby", "/api/me/saved"])
async def test_feed_reads_are_rate_limited(
    client: AsyncClient, session: AsyncSession, path: str
) -> None:
    from tests.feed.test_top import publish_feed

    (viewer,) = await members(session, 1)
    await publish_feed(session, rate_feed_read_user="2/900")
    for _ in range(2):
        assert (await client.get(path, headers=viewer.headers)).status_code == 200
    assert (await client.get(path, headers=viewer.headers)).status_code == 429


async def test_photo_adapter_calls_media_service(session: AsyncSession) -> None:
    """The real adapter (the other tests stub it): every requested deed gets a list."""
    from app.core.storage import MemoryStorage
    from tests.feed.conftest import REAL_PUBLIC_PHOTOS

    reporter, doer = await members(session, 2)
    deed_id = await verified_deed(session, reporter, doer)
    found = await REAL_PUBLIC_PHOTOS(session, [deed_id], MemoryStorage())
    assert found == {deed_id: []}
    assert await REAL_PUBLIC_PHOTOS(session, [], MemoryStorage()) == {}


async def test_category_label_follows_config_on_every_shape(
    client: AsyncClient, session: AsyncSession
) -> None:
    """§3.4b #11: the label comes from the deeds config; an unknown slug falls back to itself."""
    from app.modules.deeds._limits import Category
    from tests.deeds.helpers import publish_limits

    reporter, doer, viewer = await members(session, 3)
    _, limits = await load_limits(session)
    renamed = tuple(
        Category(slug=c.slug, label="Kind to animals") if c.slug == "animals" else c
        for c in limits.categories
    )
    await publish_limits(session, categories=renamed)
    deed_id = await verified_deed(session, reporter, doer, verified_at=FUTURE)
    retired = await verified_deed(
        session, reporter, doer, verified_at=FUTURE, category="retired-slug"
    )
    await session.commit()
    detail = await _get(client, f"/api/feed/deeds/{deed_id}", viewer)
    assert detail["category_label"] == "Kind to animals"
    public = await _get(client, f"/api/public/deeds/{deed_id}")
    assert public["category_label"] == "Kind to animals"
    nearby = await _get(client, "/api/feed?tab=nearby", viewer)
    by_id = {i["deed_id"]: i for i in nearby["items"]}
    assert by_id[str(deed_id)]["category_label"] == "Kind to animals"
    assert by_id[str(retired)]["category_label"] == "retired-slug"
