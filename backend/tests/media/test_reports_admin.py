"""Photo reports, the admin queue and review, decisions and restore, and roles (§3.3 #10 to #14,
§3.4b, §8 "Reports and admin", D42, D43, Q32, Q34, Q44)."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditLog
from app.core.events import OutboxEvent
from app.core.ratelimit import InMemoryRateLimiter
from app.core.security import create_access_token
from app.core.storage import MemoryStorage
from app.main import app
from app.modules.deeds.models import Deed
from app.modules.feed._projection import sync_post
from app.modules.feed.models import FeedPost
from app.modules.identity import cli
from app.modules.identity.models import Role, User
from app.modules.identity.service import set_role
from app.modules.media import _cleanup, service
from app.modules.media.models import (
    PhotoDecision,
    PhotoReport,
    PhotoReportCase,
    PhotoTakedown,
    ReportCaseStatus,
)
from tests.deeds.helpers import Member, make_member, scan_and_report
from tests.media.helpers import drain, fetch, limits, publish_media, ready_photo, self_log

VISITOR_IP_CLIENT = ("198.51.100.77", 1234)


@pytest.fixture
async def world(session: AsyncSession, storage: MemoryStorage) -> dict[str, Any]:
    reporter, doer = await make_member(session), await make_member(session)
    deed = await scan_and_report(session, reporter, doer)
    await sync_post(session, deed.id)
    await session.commit()
    photo = await ready_photo(session, storage, reporter, deed_id=deed.id)
    admin = await _with_role(session, Role.ADMIN)
    moderator = await _with_role(session, Role.MODERATOR)
    return {
        "reporter": reporter,
        "doer": doer,
        "deed_id": deed.id,
        "photo": photo,
        "admin": admin,
        "moderator": moderator,
        "stranger": await make_member(session),
    }


async def _with_role(session: AsyncSession, role: Role) -> Member:
    m = await make_member(session)
    user = await session.get(User, m.user_id)
    assert user is not None
    user.role = role
    await session.commit()
    return m


def _report_url(photo_id: uuid.UUID) -> str:
    return f"/api/media/photos/{photo_id}/reports"


async def _report(
    client: AsyncClient,
    photo_id: uuid.UUID,
    m: Member | None = None,
    reason: str = "other",
    **kw: Any,
) -> Any:
    headers = m.headers if m is not None else {}
    return await client.post(_report_url(photo_id), json={"reason": reason, **kw}, headers=headers)


async def _case(session: AsyncSession, photo_id: uuid.UUID) -> PhotoReportCase:
    case = await session.scalar(
        select(PhotoReportCase)
        .where(PhotoReportCase.photo_id == photo_id)
        .order_by(PhotoReportCase.opened_at.desc())
        .execution_options(populate_existing=True)
    )
    assert case is not None
    return case


async def _snapshot(session: AsyncSession, photo_id: uuid.UUID, deed_id: uuid.UUID) -> Any:
    photo = await fetch(session, photo_id)
    deed = await session.scalar(
        select(Deed).where(Deed.id == deed_id).execution_options(populate_existing=True)
    )
    post = await session.scalar(
        select(FeedPost)
        .where(FeedPost.deed_id == deed_id)
        .execution_options(populate_existing=True)
    )
    assert deed is not None and post is not None
    return _cols(photo), _cols(deed, ("location",)), _cols(post)


def _cols(obj: Any, skip: tuple[str, ...] = ()) -> tuple[Any, ...]:
    return tuple(getattr(obj, c.key) for c in type(obj).__table__.columns if c.key not in skip)


# --------------------------------------------------- reports (#10)


async def test_visitor_and_member_reports(
    client: AsyncClient, session: AsyncSession, world: dict[str, Any]
) -> None:
    photo, deed_id = world["photo"], world["deed_id"]
    before = await _snapshot(session, photo.id, deed_id)

    resp = await _report(client, photo.id, reason="not_consented", details="  That's me  ")
    assert (resp.status_code, resp.json()) == (202, {"status": "received"})
    resp = await _report(client, photo.id, world["stranger"], reason="other")
    assert resp.status_code == 202

    case = await _case(session, photo.id)
    assert (case.status, case.report_count, case.priority) == (ReportCaseStatus.OPEN, 2, 2)
    reports = list(await session.scalars(select(PhotoReport).where(PhotoReport.case_id == case.id)))
    visitor = next(r for r in reports if r.reporter_user_id is None)
    assert visitor.details == "That's me" and len(visitor.reporter_ip_hash) == 64
    # D42: nothing else changed.
    assert await _snapshot(session, photo.id, deed_id) == before


async def test_duplicates_create_nothing(
    client: AsyncClient, session: AsyncSession, world: dict[str, Any]
) -> None:
    photo, stranger = world["photo"], world["stranger"]
    for _ in range(2):
        assert (await _report(client, photo.id, stranger)).status_code == 202
        assert (await _report(client, photo.id)).status_code == 202
    case = await _case(session, photo.id)
    assert case.report_count == 2
    count = await session.scalar(
        select(func.count()).select_from(PhotoReport).where(PhotoReport.case_id == case.id)
    )
    assert count == 2


async def test_priority_only_goes_up(
    client: AsyncClient, session: AsyncSession, world: dict[str, Any]
) -> None:
    photo = world["photo"]
    people = [await make_member(session) for _ in range(4)]
    await _report(client, photo.id, people[0], reason="other")
    assert (await _case(session, photo.id)).priority == 3
    await _report(client, photo.id, people[1], reason="inappropriate")
    assert (await _case(session, photo.id)).priority == 2
    await _report(client, photo.id, people[2], reason="child_no_guardian_consent")
    assert (await _case(session, photo.id)).priority == 1
    await _report(client, photo.id, people[3], reason="other")
    assert (await _case(session, photo.id)).priority == 1  # never lowered


async def test_what_can_be_reported(
    client: AsyncClient, session: AsyncSession, storage: MemoryStorage, world: dict[str, Any]
) -> None:
    photo, deed_id = world["photo"], world["deed_id"]
    # The uploader on their own photo.
    resp = await _report(client, photo.id, world["reporter"])
    assert (resp.status_code, resp.json()["code"]) == (400, "own_photo")
    # Unknown photo, self-log photo.
    assert (await _report(client, uuid.uuid4())).status_code == 404
    owner = await make_member(session)
    private = await ready_photo(session, storage, owner, self_log_id=await self_log(session, owner))
    resp = await _report(client, private.id, world["stranger"])
    assert (resp.status_code, resp.json()["code"]) == (404, "photo_not_found")
    # Validation.
    assert (await _report(client, photo.id, reason="spam")).status_code == 422
    await publish_media(session, report_details_max=10)
    resp = await _report(client, photo.id, details="x" * 11)
    assert (resp.status_code, resp.json()["code"]) == (422, "details_too_long")

    # A hidden deed: visitors and strangers 404; the doer (who can see it) may report.
    post = await session.get(FeedPost, deed_id)
    assert post is not None
    post.hidden_by_doer_at = datetime.now(UTC)
    await session.commit()
    assert (await _report(client, photo.id)).status_code == 404
    assert (await _report(client, photo.id, world["stranger"])).status_code == 404
    assert (await _report(client, photo.id, world["doer"])).status_code == 202
    post.hidden_by_doer_at = None
    await session.commit()

    # A removed photo.
    row = await fetch(session, photo.id)
    row.takedown, row.removed_at = PhotoTakedown.ADMIN_REMOVED, datetime.now(UTC)
    await session.commit()
    assert (await _report(client, photo.id)).status_code == 404


async def test_not_onboarded_member_reports_as_visitor(
    client: AsyncClient, session: AsyncSession, world: dict[str, Any]
) -> None:
    newbie = await make_member(session, onboarded=False)
    assert (await _report(client, world["photo"].id, newbie)).status_code == 202
    [report] = (
        await session.scalars(select(PhotoReport).where(PhotoReport.photo_id == world["photo"].id))
    ).all()
    assert report.reporter_user_id is None


async def test_report_rate_limits(
    client: AsyncClient,
    session: AsyncSession,
    world: dict[str, Any],
    rate_limiter: InMemoryRateLimiter,
) -> None:
    await publish_media(session, rate_report_ip="2/3600", rate_report_user="1/3600")
    photo = world["photo"]
    assert (await _report(client, photo.id, world["stranger"])).status_code == 202
    resp = await _report(client, photo.id, world["stranger"])
    assert (resp.status_code, resp.json()["code"]) == (429, "rate_limited")
    # Visitors on this IP: the member's second try stopped at the account key, so the IP key
    # has one hit; the next visitor report is the second, then the IP limit trips.
    assert (await _report(client, photo.id)).status_code == 202
    assert (await _report(client, photo.id)).status_code == 429
    await publish_media(
        session,
        rate_report_ip="100/3600",
        rate_report_ip_daily="1/86400",
        rate_report_user="9/3600",
    )
    rate_limiter.counts.clear()
    assert (await _report(client, photo.id)).status_code == 202
    assert (await _report(client, photo.id)).status_code == 429  # daily visitor key
    assert (
        await _report(client, photo.id, world["moderator"])
    ).status_code == 202  # members aren't


# --------------------------------------------------- roles and the queue (#11, #12)


async def test_members_get_403_on_every_admin_route(
    client: AsyncClient, world: dict[str, Any]
) -> None:
    m = world["stranger"]
    case_id, photo_id = uuid.uuid4(), world["photo"].id
    calls = [
        client.get("/api/admin/photo-reports", headers=m.headers),
        client.get(f"/api/admin/photo-reports/{case_id}", headers=m.headers),
        client.post(
            f"/api/admin/photo-reports/{case_id}/decision",
            json={"action": "keep", "note": "fine by me"},
            headers=m.headers,
        ),
        client.post(
            f"/api/admin/photos/{photo_id}/restore", json={"note": "bring back"}, headers=m.headers
        ),
    ]
    for call in calls:
        resp = await call
        assert (resp.status_code, resp.json()["code"]) == (403, "forbidden")
    assert (await client.get("/api/admin/photo-reports")).status_code == 401


async def test_no_http_route_changes_a_role() -> None:
    paths = [getattr(r, "path", "") for r in app.routes]
    assert not [p for p in paths if "role" in p.lower()]


async def test_queue_order_counts_and_pages(
    client: AsyncClient, session: AsyncSession, storage: MemoryStorage, world: dict[str, Any]
) -> None:
    reporter, deed_id = world["reporter"], world["deed_id"]
    photos = [world["photo"]] + [
        await ready_photo(session, storage, reporter, deed_id=deed_id, position=i)
        for i in range(2, 5)
    ]
    reasons = ["other", "private_details", "child_no_guardian_consent", "not_consented"]
    for photo, reason in zip(photos, reasons, strict=True):
        assert (
            await _report(client, photo.id, world["stranger"], reason=reason)
        ).status_code == 202

    mod = world["moderator"]
    # Page through the whole queue two at a time; other test runs may have left committed cases,
    # so only this test's cases are checked (in queue order) and counts are compared as deltas.
    ours = {str(c.id) for c in [await _case(session, p.id) for p in photos]}
    pages, cursor = [], ""
    while True:
        page = (
            await client.get(f"/api/admin/photo-reports?limit=2{cursor}", headers=mod.headers)
        ).json()
        pages.append(page)
        if page["next_cursor"] is None:
            break
        cursor = f"&cursor={page['next_cursor']}"
    first = pages[0]
    assert len(pages) >= 2 and all(len(p["items"]) <= 2 for p in pages)
    seen = [i["case_id"] for p in pages for i in p["items"]]
    assert len(seen) == len(set(seen))  # no item twice across pages
    items = [i for p in pages for i in p["items"] if i["case_id"] in ours]
    assert first["open_count"] >= 4
    # Child first, then privacy and harm (oldest first), then the rest.
    assert [(i["priority"], i["top_reason"]) for i in items] == [
        (1, "child_no_guardian_consent"),
        (2, "private_details"),
        (2, "not_consented"),
        (3, "other"),
    ]
    item = items[0]
    assert item["status"] == "open" and item["report_count"] == 1
    assert item["photo_thumb_url"].startswith("memory://")
    assert item["deed"] == {
        "doer": {"handle": world["doer"].handle, "display_name": world["doer"].display_name},
        "category": "animals",
        "area_name": item["deed"]["area_name"],
    }
    assert item["deed"]["area_name"]
    resp = await client.get("/api/admin/photo-reports?cursor=bogus", headers=mod.headers)
    assert (resp.status_code, resp.json()["code"]) == (400, "invalid_cursor")
    resolved = (
        await client.get("/api/admin/photo-reports?status=resolved", headers=mod.headers)
    ).json()
    assert not ours & {i["case_id"] for i in resolved["items"]}
    # A cursor from the open queue is refused on the resolved queue.
    resp = await client.get(
        f"/api/admin/photo-reports?status=resolved&cursor={first['next_cursor']}",
        headers=mod.headers,
    )
    assert resp.status_code == 400


async def test_detail(client: AsyncClient, session: AsyncSession, world: dict[str, Any]) -> None:
    photo = world["photo"]
    await _report(client, photo.id, world["stranger"], reason="private_details", details="Address")
    await _report(client, photo.id, reason="child_no_guardian_consent")
    case = await _case(session, photo.id)
    resp = await client.get(f"/api/admin/photo-reports/{case.id}", headers=world["admin"].headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["case"] == {
        "case_id": str(case.id),
        "status": "open",
        "priority": 1,
        "top_reason": "child_no_guardian_consent",
        "report_count": 2,
        "opened_at": body["case"]["opened_at"],
        "resolved_at": None,
        "decision": None,
        "removal_reason": None,
    }
    p = body["photo"]
    assert p["id"] == str(photo.id) and p["state"] == "visible"
    assert (p["position"], p["photo_count"], p["blurred_count"]) == (1, 1, 0)
    assert p["uploader"]["handle"] == world["reporter"].handle
    assert p["restorable_until"] is None and p["purged_at"] is None
    assert p["display_url"].startswith("memory://")
    assert body["deed"]["listed"] is True and body["deed"]["status"] == "verified"
    assert body["deed"]["reporter"]["handle"] == world["reporter"].handle
    assert [r["reporter"] for r in body["reports"]] == [
        {"kind": "member", "handle": world["stranger"].handle},
        {"kind": "visitor", "handle": None},
    ]
    assert body["reports"][0]["details"] == "Address"
    assert body["decisions"] == []
    missing = await client.get(
        f"/api/admin/photo-reports/{uuid.uuid4()}", headers=world["admin"].headers
    )
    assert (missing.status_code, missing.json()["code"]) == (404, "case_not_found")


# --------------------------------------------------- decisions (#13)


async def _open_case(
    client: AsyncClient, session: AsyncSession, world: dict[str, Any]
) -> uuid.UUID:
    await _report(client, world["photo"].id, world["stranger"], reason="not_consented")
    return (await _case(session, world["photo"].id)).id


def _decide(case_id: uuid.UUID) -> str:
    return f"/api/admin/photo-reports/{case_id}/decision"


async def test_keep(client: AsyncClient, session: AsyncSession, world: dict[str, Any]) -> None:
    case_id = await _open_case(client, session, world)
    photo = world["photo"]
    before = await _snapshot(session, photo.id, world["deed_id"])
    mod = world["moderator"]
    resp = await client.post(
        _decide(case_id), json={"action": "keep", "note": "abcd"}, headers=mod.headers
    )
    assert (resp.status_code, resp.json()["code"]) == (422, "note_required")
    resp = await client.post(
        _decide(case_id),
        json={"action": "keep", "note": "  Looks fine to me  "},
        headers=mod.headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["case"]["status"], body["case"]["decision"]) == ("resolved", "kept")
    assert body["decisions"][0]["action"] == "keep"
    assert body["decisions"][0]["note"] == "Looks fine to me"
    assert await _snapshot(session, photo.id, world["deed_id"]) == before
    [audit] = (
        await session.scalars(select(AuditLog).where(AuditLog.entity_id == str(photo.id)))
    ).all()
    assert (audit.action, audit.actor_id, audit.reason) == (
        "photo.kept",
        mod.user_id,
        "Looks fine to me",
    )
    resp = await client.post(
        _decide(case_id), json={"action": "remove", "note": "changed my mind"}, headers=mod.headers
    )
    assert (resp.status_code, resp.json()["code"]) == (409, "case_not_open")
    # The uploader sees "kept"; a new report opens a new case that shows the earlier decision.
    owner = (
        await client.get(
            f"/api/media/deeds/{world['deed_id']}/photos", headers=world["reporter"].headers
        )
    ).json()
    assert [o["decision"] for o in owner["outcomes"]] == ["kept"]
    await _report(client, photo.id, reason="other")
    new_case = await _case(session, photo.id)
    assert new_case.id != case_id
    detail = (
        await client.get(f"/api/admin/photo-reports/{new_case.id}", headers=mod.headers)
    ).json()
    assert [d["action"] for d in detail["decisions"]] == ["keep"]


async def test_remove_moves_objects_to_held_and_restore_brings_new_urls(
    client: AsyncClient, session: AsyncSession, storage: MemoryStorage, world: dict[str, Any]
) -> None:
    case_id = await _open_case(client, session, world)
    photo, deed_id, admin = world["photo"], world["deed_id"], world["admin"]
    old_keys = list((await fetch(session, photo.id)).variant_keys.values())  # type: ignore[union-attr]
    old_urls = (await service.public_photos(session, [deed_id], storage=storage))[deed_id]
    assert len(old_urls) == 1

    resp = await client.post(
        _decide(case_id), json={"action": "remove", "note": "Person asked"}, headers=admin.headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["case"]["decision"], body["case"]["removal_reason"]) == (
        "removed",
        "not_consented",
    )
    assert body["photo"]["state"] == "removed"
    assert f"/held/{photo.id}/display-1.webp" in body["photo"]["display_url"]
    assert body["photo"]["restorable_until"] is not None
    row = await fetch(session, photo.id)
    assert (row.takedown, row.object_move) == (PhotoTakedown.ADMIN_REMOVED, None)
    # Old URLs are dead (objects gone from photos/), held copies exist.
    assert not any(k in storage.objects for k in old_keys)
    assert all(k.replace("photos/", "held/", 1) in storage.objects for k in old_keys)
    assert (await service.public_photos(session, [deed_id], storage=storage))[deed_id] == []
    owner_view = (
        await client.get(f"/api/media/deeds/{deed_id}/photos", headers=world["reporter"].headers)
    ).json()
    assert owner_view["photos"] == []
    assert [(o["decision"], o["removal_reason"]) for o in owner_view["outcomes"]] == [
        ("removed", "not_consented")
    ]
    [event] = (
        await session.scalars(
            select(OutboxEvent).where(OutboxEvent.event_type == "media.photo_moderation_decided")
        )
    ).all()
    assert event.payload == {
        "photo_id": str(photo.id),
        "case_id": str(case_id),
        "deed_id": str(deed_id),
        "uploader_id": str(world["reporter"].user_id),
        "action": "remove",
        "removal_reason": "not_consented",
    }

    # Restore: conflict first, then a real restore.
    resp = await client.post(
        f"/api/admin/photos/{photo.id}/restore", json={"note": "xx"}, headers=admin.headers
    )
    assert resp.status_code == 422
    resp = await client.post(
        f"/api/admin/photos/{photo.id}/restore",
        json={"note": "Consent confirmed"},
        headers=world["moderator"].headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["photo"]["state"] == "visible"
    assert [d["action"] for d in body["decisions"]] == ["remove", "restore"]
    row = await fetch(session, photo.id)
    assert row.variant_keys == {
        "thumb": f"photos/{photo.id}/thumb-2.webp",
        "display": f"photos/{photo.id}/display-2.webp",
    }
    assert sorted(storage.objects) == sorted(row.variant_keys.values())  # held gone, old dead
    new_urls = (await service.public_photos(session, [deed_id], storage=storage))[deed_id]
    assert new_urls[0].display_url != old_urls[0].display_url
    owner_view = (
        await client.get(f"/api/media/deeds/{deed_id}/photos", headers=world["reporter"].headers)
    ).json()
    assert [o["decision"] for o in owner_view["outcomes"]] == ["kept"]  # latest decision wins
    actions = list(
        await session.scalars(
            select(AuditLog.action)
            .where(AuditLog.entity_id == str(photo.id))
            .order_by(AuditLog.created_at)
        )
    )
    assert actions == ["photo.removed", "photo.restored"]
    # Restoring again: nothing to restore.
    resp = await client.post(
        f"/api/admin/photos/{photo.id}/restore",
        json={"note": "Again please"},
        headers=admin.headers,
    )
    assert (resp.status_code, resp.json()["code"]) == (409, "not_restorable")


async def test_conflict_of_interest_on_decide_and_restore(
    client: AsyncClient, session: AsyncSession, world: dict[str, Any]
) -> None:
    case_id = await _open_case(client, session, world)
    for who in ("doer", "reporter"):
        user = await session.get(User, world[who].user_id)
        assert user is not None
        user.role = Role.MODERATOR
    await session.commit()
    for who in ("doer", "reporter"):
        resp = await client.post(
            _decide(case_id),
            json={"action": "remove", "note": "My own deed"},
            headers=world[who].headers,
        )
        assert (resp.status_code, resp.json()["code"]) == (403, "conflict_of_interest")
    resp = await client.post(
        _decide(case_id),
        json={"action": "remove", "note": "Person asked"},
        headers=world["admin"].headers,
    )
    assert resp.status_code == 200
    for who in ("doer", "reporter"):
        resp = await client.post(
            f"/api/admin/photos/{world['photo'].id}/restore",
            json={"note": "Bring it back"},
            headers=world[who].headers,
        )
        assert (resp.status_code, resp.json()["code"]) == (403, "conflict_of_interest")


async def test_doer_removed_and_purged_photos_cant_be_restored(
    client: AsyncClient, session: AsyncSession, storage: MemoryStorage, world: dict[str, Any]
) -> None:
    admin, photo = world["admin"], world["photo"]
    assert (
        await client.delete(f"/api/media/photos/{photo.id}", headers=world["doer"].headers)
    ).status_code == 204
    resp = await client.post(
        f"/api/admin/photos/{photo.id}/restore",
        json={"note": "Bring it back"},
        headers=admin.headers,
    )
    assert (resp.status_code, resp.json()["code"]) == (409, "not_restorable")
    resp = await client.post(
        f"/api/admin/photos/{uuid.uuid4()}/restore",
        json={"note": "Bring it back"},
        headers=admin.headers,
    )
    assert (resp.status_code, resp.json()["code"]) == (404, "photo_not_found")


async def test_retention_day_89_restores_day_91_is_purged(
    client: AsyncClient, session: AsyncSession, storage: MemoryStorage, world: dict[str, Any]
) -> None:
    lim = await limits(session)
    case_id = await _open_case(client, session, world)
    photo, admin = world["photo"], world["admin"]
    await client.post(
        _decide(case_id), json={"action": "remove", "note": "Person asked"}, headers=admin.headers
    )
    removed_at = (await fetch(session, photo.id)).removed_at
    assert removed_at is not None

    # Day 89: the nightly job leaves it.
    day89 = removed_at + timedelta(days=lim.removed_photo_retention_days - 1)
    await drain(lambda: _cleanup.purge_removed(session, storage, lim, now=day89), _cleanup.BATCH)
    assert (await fetch(session, photo.id)).objects_purged_at is None
    assert storage.objects
    # Day 91: held copies deleted, objects_purged_at set; rows stay.
    day91 = removed_at + timedelta(days=lim.removed_photo_retention_days + 1)
    await drain(lambda: _cleanup.purge_removed(session, storage, lim, now=day91), _cleanup.BATCH)
    row = await fetch(session, photo.id)
    assert row.objects_purged_at == day91
    assert storage.objects == {}
    resp = await client.post(
        f"/api/admin/photos/{photo.id}/restore",
        json={"note": "Too late now"},
        headers=admin.headers,
    )
    assert (resp.status_code, resp.json()["code"]) == (409, "not_restorable")
    detail = (await client.get(f"/api/admin/photo-reports/{case_id}", headers=admin.headers)).json()
    assert detail["photo"]["purged_at"] is not None and detail["photo"]["restorable_until"] is None
    assert detail["photo"]["display_url"] is None
    assert (
        await session.scalar(
            select(func.count())
            .select_from(PhotoDecision)
            .where(PhotoDecision.photo_id == photo.id)
        )
        == 1
    )


async def test_retention_comes_from_config(
    client: AsyncClient, session: AsyncSession, storage: MemoryStorage, world: dict[str, Any]
) -> None:
    case_id = await _open_case(client, session, world)
    await client.post(
        _decide(case_id),
        json={"action": "remove", "note": "Person asked"},
        headers=world["admin"].headers,
    )
    lim = await publish_media(session, removed_photo_retention_days=1)
    soon = datetime.now(UTC) + timedelta(days=1, minutes=1)
    await drain(lambda: _cleanup.purge_removed(session, storage, lim, now=soon), _cleanup.BATCH)
    assert (await fetch(session, world["photo"].id)).objects_purged_at == soon


async def test_photo_decisions_are_append_only(
    client: AsyncClient, session: AsyncSession, world: dict[str, Any]
) -> None:
    case_id = await _open_case(client, session, world)
    await client.post(
        _decide(case_id),
        json={"action": "keep", "note": "Looks fine"},
        headers=world["admin"].headers,
    )
    with pytest.raises(DBAPIError):
        async with session.begin_nested():
            await session.execute(text("UPDATE photo_decisions SET note = 'changed note'"))
    with pytest.raises(DBAPIError):
        async with session.begin_nested():
            await session.execute(text("DELETE FROM photo_decisions"))


async def test_admin_decisions_are_rate_limited(
    client: AsyncClient, session: AsyncSession, world: dict[str, Any]
) -> None:
    await publish_media(session, rate_admin_decision_user="1/3600")
    case_id = await _open_case(client, session, world)
    await client.post(
        _decide(case_id),
        json={"action": "keep", "note": "Looks fine"},
        headers=world["admin"].headers,
    )
    resp = await client.post(
        f"/api/admin/photos/{world['photo'].id}/restore",
        json={"note": "Again"},
        headers=world["admin"].headers,
    )
    assert (resp.status_code, resp.json()["code"]) == (429, "rate_limited")


async def test_uploader_deleting_closes_the_open_case(
    client: AsyncClient, session: AsyncSession, world: dict[str, Any]
) -> None:
    case_id = await _open_case(client, session, world)
    before = (await client.get("/api/admin/photo-reports", headers=world["admin"].headers)).json()[
        "open_count"
    ]
    await client.delete(f"/api/media/photos/{world['photo'].id}", headers=world["reporter"].headers)
    resp = await client.post(
        _decide(case_id),
        json={"action": "remove", "note": "Person asked"},
        headers=world["admin"].headers,
    )
    assert (resp.status_code, resp.json()["code"]) == (409, "case_not_open")
    open_count = (
        await client.get("/api/admin/photo-reports", headers=world["admin"].headers)
    ).json()["open_count"]
    assert open_count == before - 1
    assert (await _case(session, world["photo"].id)).status == ReportCaseStatus.CLOSED


# --------------------------------------------------- roles (set_role + CLI)


async def test_set_role_audits_and_kills_old_tokens(
    client: AsyncClient, session: AsyncSession, world: dict[str, Any]
) -> None:
    m = world["stranger"]
    user = await session.get(User, m.user_id)
    assert user is not None
    old_version = user.token_version
    assert await set_role(session, user, Role.MODERATOR, reason="Trusted reviewer") is True
    await session.commit()
    new_version = user.token_version
    assert new_version == old_version + 1
    [audit] = (
        await session.scalars(
            select(AuditLog).where(
                AuditLog.entity_id == str(m.user_id), AuditLog.action == "role.changed"
            )
        )
    ).all()
    assert (audit.old_value, audit.new_value, audit.reason) == (
        {"role": "member"},
        {"role": "moderator"},
        "Trusted reviewer",
    )
    # The old token stops working; a new one carries the role.
    assert (await client.get("/api/admin/photo-reports", headers=m.headers)).status_code == 401
    token, _ = create_access_token(m.user_id, "moderator", new_version)
    resp = await client.get(
        "/api/admin/photo-reports", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    # Unchanged role: nothing written.
    user = await session.get(User, m.user_id)
    assert user is not None
    assert await set_role(session, user, Role.MODERATOR, reason="again") is False
    with pytest.raises(ValueError):
        await set_role(session, user, Role.ADMIN, reason="  ")


async def test_cli_unknown_handle(capsys: pytest.CaptureFixture[str], migrated_db: None) -> None:
    assert await cli._set_role("no-such-handle-xyz", Role.ADMIN, "testing", "Test Operator") == 1
    assert "No user" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        cli.main(["set-role", "someone", "superuser", "--reason", "x", "--operator", "Op"])
    with pytest.raises(SystemExit):
        cli.main(["set-role", "someone", "admin", "--operator", "Op"])  # --reason is required
