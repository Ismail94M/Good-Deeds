"""Attach, owner reads with outcomes, and removal by the uploader (Q30) or the doer (Q29)
(§3.3 #5 to #9, §8 "Media")."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditLog
from app.core.events import OutboxEvent
from app.core.storage import MemoryStorage
from app.modules.deeds.models import Deed, DeedStatus, SameActGroup
from app.modules.media.models import (
    Photo,
    PhotoDecision,
    PhotoDecisionAction,
    PhotoReportCase,
    PhotoReportReason,
    PhotoStatus,
    PhotoTakedown,
    ReportCaseStatus,
)
from tests.deeds.helpers import Member, make_member, scan_and_report
from tests.media import images
from tests.media.helpers import (
    fetch,
    process_all,
    publish_media,
    ready_photo,
    self_log,
    uploaded_photo,
)


async def _setup(session: AsyncSession) -> tuple[Member, Member, uuid.UUID]:
    reporter, doer = await make_member(session), await make_member(session)
    deed = await scan_and_report(session, reporter, doer)
    await session.commit()
    return reporter, doer, deed.id


def _attach_body(*photo_ids: uuid.UUID, regions: list[dict[str, float]] | None = None) -> Any:
    return {"photos": [{"photo_id": str(i), "blur_regions": regions or []} for i in photo_ids]}


async def _attach(
    client: AsyncClient, m: Member, deed_id: uuid.UUID, *photo_ids: uuid.UUID, **kw: Any
) -> Any:
    return await client.post(
        f"/api/media/deeds/{deed_id}/photos", json=_attach_body(*photo_ids, **kw), headers=m.headers
    )


async def _set_reported_at(session: AsyncSession, deed_id: uuid.UUID, at: datetime) -> None:
    deed = await session.get(Deed, deed_id)
    assert deed is not None
    deed.reported_at = at
    await session.commit()


# --------------------------------------------------- attach to a deed


async def test_attach_queues_photos_with_positions_and_regions(
    client: AsyncClient, session: AsyncSession, storage: MemoryStorage
) -> None:
    reporter, _, deed_id = await _setup(session)
    a = await uploaded_photo(session, storage, reporter)
    b = await uploaded_photo(session, storage, reporter)
    region = {"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.3}
    resp = await client.post(
        f"/api/media/deeds/{deed_id}/photos",
        json={
            "photos": [
                {"photo_id": str(a.id), "blur_regions": [region]},
                {"photo_id": str(b.id), "blur_regions": []},
            ]
        },
        headers=reporter.headers,
    )
    assert resp.status_code == 200, resp.text
    photos = resp.json()["photos"]
    assert [(p["position"], p["status"], p["blurred_count"]) for p in photos] == [
        (1, "processing", 1),
        (2, "processing", 0),
    ]
    assert all(p["can_remove"] and p["thumb_url"] is None for p in photos)
    row = await fetch(session, a.id)
    assert (row.status, row.deed_id, row.blur_regions) == (PhotoStatus.QUEUED, deed_id, [region])
    assert row.attached_at is not None

    await process_all(session, storage)
    listing = (
        await client.get(f"/api/media/deeds/{deed_id}/photos", headers=reporter.headers)
    ).json()
    assert [p["status"] for p in listing["photos"]] == ["ready", "ready"]
    assert listing["photos"][0]["thumb_url"].startswith("memory://")


async def test_photo_limit_and_admin_removed_photos_dont_count(
    client: AsyncClient, session: AsyncSession, storage: MemoryStorage
) -> None:
    reporter, _, deed_id = await _setup(session)
    ids = [(await uploaded_photo(session, storage, reporter)).id for _ in range(5)]
    resp = await _attach(client, reporter, deed_id, *ids)
    assert (resp.status_code, resp.json()["code"]) == (409, "too_many_photos")
    assert (await _attach(client, reporter, deed_id, *ids[:3])).status_code == 200
    resp = await _attach(client, reporter, deed_id, *ids[3:5])
    assert (resp.status_code, resp.json()["code"]) == (409, "too_many_photos")
    assert (await _attach(client, reporter, deed_id, ids[3])).status_code == 200
    resp = await _attach(client, reporter, deed_id, ids[4])
    assert resp.status_code == 409

    # An admin removal frees a slot inside the window.
    removed = await fetch(session, ids[0])
    removed.takedown, removed.removed_at = PhotoTakedown.ADMIN_REMOVED, datetime.now(UTC)
    await session.commit()
    resp = await _attach(client, reporter, deed_id, ids[4])
    assert resp.status_code == 200
    assert resp.json()["photos"][0]["position"] == 5  # positions are never reused

    # The limit comes from config.
    await publish_media(session, photos_per_owner=6)
    extra = await uploaded_photo(session, storage, reporter)
    assert (await _attach(client, reporter, deed_id, extra.id)).status_code == 200


async def test_attach_window(
    client: AsyncClient, session: AsyncSession, storage: MemoryStorage
) -> None:
    reporter, _, deed_id = await _setup(session)
    photo = await uploaded_photo(session, storage, reporter)
    await _set_reported_at(session, deed_id, datetime.now(UTC) - timedelta(hours=24, seconds=2))
    resp = await _attach(client, reporter, deed_id, photo.id)
    assert (resp.status_code, resp.json()["code"]) == (409, "photo_window_closed")
    await _set_reported_at(session, deed_id, datetime.now(UTC) - timedelta(hours=23, minutes=59))
    assert (await _attach(client, reporter, deed_id, photo.id)).status_code == 200
    # The window comes from config.
    other = await uploaded_photo(session, storage, reporter)
    await publish_media(session, photo_edit_window_hours=1)
    resp = await _attach(client, reporter, deed_id, other.id)
    assert resp.json()["code"] == "photo_window_closed"


async def test_attach_window_boundary_in_the_service(
    session: AsyncSession, storage: MemoryStorage
) -> None:
    from app.core.errors import Conflict
    from app.modules.deeds import service as deeds
    from app.modules.media import _owners
    from app.modules.media._limits import load_limits
    from app.modules.media.schemas import AttachItem

    reporter, _, deed_id = await _setup(session)
    view = await deeds.get_deed(session, deed_id)
    assert view is not None
    _, limits = await load_limits(session)
    end = view.reported_at + timedelta(hours=24)
    photo = await uploaded_photo(session, storage, reporter)
    items = [AttachItem(photo_id=photo.id)]
    try:
        await _owners.attach_to_deed(
            session,
            storage,
            reporter.user_id,
            deed_id,
            items,
            limits=limits,
            now=end + timedelta(seconds=1),
        )
        raise AssertionError("window should be closed")
    except Conflict as exc:
        assert exc.code == "photo_window_closed"
    out = await _owners.attach_to_deed(
        session, storage, reporter.user_id, deed_id, items, limits=limits, now=end
    )
    assert len(out) == 1


async def test_attach_who_and_what(
    client: AsyncClient, session: AsyncSession, storage: MemoryStorage
) -> None:
    reporter, doer, deed_id = await _setup(session)
    stranger = await make_member(session)
    mine = await uploaded_photo(session, storage, reporter)
    # Doer and stranger: 404 (not their deed to add to).
    for m in (doer, stranger):
        own = await uploaded_photo(session, storage, m)
        resp = await _attach(client, m, deed_id, own.id)
        assert (resp.status_code, resp.json()["code"]) == (404, "deed_not_found")
    resp = await _attach(client, reporter, uuid.uuid4(), mine.id)
    assert resp.status_code == 404
    # Someone else's upload, a pending upload, a self-log upload, an unknown id: 409.
    theirs = await uploaded_photo(session, storage, stranger)
    pending = await uploaded_photo(session, storage, reporter, status=PhotoStatus.PENDING_UPLOAD)
    from app.modules.media.models import PhotoPurpose

    private = await uploaded_photo(session, storage, reporter, purpose=PhotoPurpose.SELF_LOG)
    for pid in (theirs.id, pending.id, private.id, uuid.uuid4()):
        resp = await _attach(client, reporter, deed_id, pid)
        assert (resp.status_code, resp.json()["code"]) == (409, "photo_not_uploaded")
    # The same photo twice: 422; attaching twice: the second time it's no longer uploaded.
    resp = await _attach(client, reporter, deed_id, mine.id, mine.id)
    assert resp.status_code == 422
    assert (await _attach(client, reporter, deed_id, mine.id)).status_code == 200
    assert (await _attach(client, reporter, deed_id, mine.id)).status_code == 409


async def test_attach_to_withdrawn_deed(
    client: AsyncClient, session: AsyncSession, storage: MemoryStorage
) -> None:
    reporter, _, deed_id = await _setup(session)
    deed = await session.get(Deed, deed_id)
    assert deed is not None
    deed.status, deed.withdrawn_at = DeedStatus.WITHDRAWN, datetime.now(UTC)
    await session.commit()
    photo = await uploaded_photo(session, storage, reporter)
    resp = await _attach(client, reporter, deed_id, photo.id)
    assert (resp.status_code, resp.json()["code"]) == (409, "deed_not_editable")


async def test_blur_region_validation(
    client: AsyncClient, session: AsyncSession, storage: MemoryStorage
) -> None:
    reporter, _, deed_id = await _setup(session)
    photo = await uploaded_photo(session, storage, reporter)
    bad = [
        [{"x": 0.9, "y": 0.1, "w": 0.2, "h": 0.2}],  # past the right edge
        [{"x": 0.1, "y": 0.9, "w": 0.2, "h": 0.2}],  # past the bottom
        [{"x": 0.1, "y": 0.1, "w": 0.01, "h": 0.2}],  # too small
        [{"x": -0.1, "y": 0.1, "w": 0.2, "h": 0.2}],
        [{"x": 0.1, "y": 0.1, "w": 0.2}],
        [{"x": 0.1, "y": 0.1, "w": 0.1, "h": 0.1}] * 9,  # too many
    ]
    for regions in bad:
        resp = await _attach(client, reporter, deed_id, photo.id, regions=regions)
        assert resp.status_code == 422, regions
    assert (await fetch(session, photo.id)).status == PhotoStatus.UPLOADED
    ok = [{"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}] + [
        {"x": 0.5, "y": 0.5, "w": 0.02, "h": 0.5}
    ] * 7
    assert (await _attach(client, reporter, deed_id, photo.id, regions=ok)).status_code == 200


# --------------------------------------------------- self-logs


async def test_self_log_photos(
    client: AsyncClient, session: AsyncSession, storage: MemoryStorage
) -> None:
    from app.modules.media.models import PhotoPurpose

    owner, stranger = await make_member(session), await make_member(session)
    log_id = await self_log(session, owner)
    photo = await uploaded_photo(session, storage, owner, purpose=PhotoPurpose.SELF_LOG)
    url = f"/api/media/self-logs/{log_id}/photos"
    region = {"x": 0.1, "y": 0.1, "w": 0.2, "h": 0.2}
    resp = await client.post(
        url, json=_attach_body(photo.id, regions=[region]), headers=owner.headers
    )
    assert (resp.status_code, resp.json()["code"]) == (422, "blur_not_allowed")
    resp = await client.post(url, json=_attach_body(photo.id), headers=stranger.headers)
    assert (resp.status_code, resp.json()["code"]) == (404, "self_log_not_found")
    deed_photo = await uploaded_photo(session, storage, owner)
    resp = await client.post(url, json=_attach_body(deed_photo.id), headers=owner.headers)
    assert resp.status_code == 409
    resp = await client.post(url, json=_attach_body(photo.id), headers=owner.headers)
    assert resp.status_code == 200
    # No time window on self-logs.
    await publish_media(session, photo_edit_window_hours=1)
    await process_all(session, storage)

    listing = await client.get(url, headers=owner.headers)
    assert listing.status_code == 200
    [item] = listing.json()["photos"]
    assert (item["status"], item["can_remove"]) == ("ready", True)
    assert (await client.get(url, headers=stranger.headers)).status_code == 404
    assert (
        await client.get(f"/api/media/self-logs/{uuid.uuid4()}/photos", headers=owner.headers)
    ).status_code == 404
    # Never public, never on a deed's photos.
    from app.modules.media import service

    assert all(
        not v for v in (await service.public_photos(session, [log_id], storage=storage)).values()
    )
    # Others can't remove it.
    resp = await client.delete(f"/api/media/photos/{photo.id}", headers=stranger.headers)
    assert resp.status_code == 404


# --------------------------------------------------- owner reads


async def test_deed_photos_access_and_flags(
    client: AsyncClient, session: AsyncSession, storage: MemoryStorage
) -> None:
    reporter, doer, deed_id = await _setup(session)
    stranger = await make_member(session)
    ready = await ready_photo(session, storage, reporter, deed_id=deed_id, position=1)
    url = f"/api/media/deeds/{deed_id}/photos"

    mine = (await client.get(url, headers=reporter.headers)).json()
    assert mine["can_add"] is True and mine["add_until"] is not None
    assert mine["outcomes"] == []
    [photo] = mine["photos"]
    assert (photo["id"], photo["can_remove"]) == (str(ready.id), True)
    # The doer of the verified deed sees the photos, may remove ready ones, can't add.
    theirs = (await client.get(url, headers=doer.headers)).json()
    assert theirs["can_add"] is False and theirs["add_until"] is None
    assert theirs["photos"][0]["can_remove"] is True
    assert (await client.get(url, headers=stranger.headers)).status_code == 404
    assert (
        await client.get(f"/api/media/deeds/{uuid.uuid4()}/photos", headers=reporter.headers)
    ).status_code == 404

    # Withdrawn: the reporter still sees them, the doer doesn't.
    deed = await session.get(Deed, deed_id)
    assert deed is not None
    deed.status, deed.withdrawn_at = DeedStatus.WITHDRAWN, datetime.now(UTC)
    await session.commit()
    assert (await client.get(url, headers=reporter.headers)).json()["can_add"] is False
    assert (await client.get(url, headers=doer.headers)).status_code == 404


async def test_failed_and_rejected_show_as_failed_admin_removed_hidden(
    client: AsyncClient, session: AsyncSession, storage: MemoryStorage
) -> None:
    reporter, doer, deed_id = await _setup(session)
    tiny = images.to_bytes(images.noise((100, 100)), "JPEG")
    bad = await uploaded_photo(session, storage, reporter, data=tiny)
    assert (await _attach(client, reporter, deed_id, bad.id)).status_code == 200
    gone = await ready_photo(session, storage, reporter, deed_id=deed_id, position=5)
    await process_all(session, storage)
    row = await fetch(session, gone.id)
    row.takedown, row.removed_at = PhotoTakedown.ADMIN_REMOVED, datetime.now(UTC)
    await session.commit()

    listing = (
        await client.get(f"/api/media/deeds/{deed_id}/photos", headers=reporter.headers)
    ).json()
    assert [(p["id"], p["status"]) for p in listing["photos"]] == [(str(bad.id), "failed")]
    doer_view = (
        await client.get(f"/api/media/deeds/{deed_id}/photos", headers=doer.headers)
    ).json()
    assert doer_view["photos"][0]["can_remove"] is False  # the doer removes ready photos only


async def test_open_reports_are_invisible_to_the_uploader(
    client: AsyncClient, session: AsyncSession, storage: MemoryStorage
) -> None:
    reporter, _, deed_id = await _setup(session)
    photo = await ready_photo(session, storage, reporter, deed_id=deed_id)
    url = f"/api/media/deeds/{deed_id}/photos"
    before = (await client.get(url, headers=reporter.headers)).content
    session.add(
        PhotoReportCase(
            photo_id=photo.id,
            status=ReportCaseStatus.OPEN,
            priority=1,
            report_count=3,
            opened_at=datetime.now(UTC),
        )
    )
    await session.commit()
    assert (await client.get(url, headers=reporter.headers)).content == before


async def test_outcomes_latest_decision_wins_and_doer_removals(
    client: AsyncClient, session: AsyncSession, storage: MemoryStorage
) -> None:
    reporter, doer, deed_id = await _setup(session)
    admin = await make_member(session)
    p1 = await ready_photo(session, storage, reporter, deed_id=deed_id, position=1)
    p2 = await ready_photo(session, storage, reporter, deed_id=deed_id, position=2)
    p3 = await ready_photo(session, storage, reporter, deed_id=deed_id, position=3)
    p4 = await ready_photo(session, storage, reporter, deed_id=deed_id, position=4)
    now = datetime.now(UTC)

    def decision(photo: Photo, action: PhotoDecisionAction, at: datetime) -> PhotoDecision:
        return PhotoDecision(
            photo_id=photo.id,
            action=action,
            removal_reason=PhotoReportReason.NOT_CONSENTED
            if action == PhotoDecisionAction.REMOVE
            else None,
            note="Reviewed carefully",
            actor_user_id=admin.user_id,
            created_at=at,
        )

    session.add_all(
        [
            decision(p1, PhotoDecisionAction.REMOVE, now - timedelta(hours=3)),
            decision(p2, PhotoDecisionAction.REMOVE, now - timedelta(hours=2)),
            decision(p2, PhotoDecisionAction.RESTORE, now - timedelta(hours=1)),  # reads kept
            decision(p3, PhotoDecisionAction.KEEP, now - timedelta(days=31)),  # too old
        ]
    )
    await session.commit()
    # The doer removes p4.
    assert (
        await client.delete(f"/api/media/photos/{p4.id}", headers=doer.headers)
    ).status_code == 204

    outcomes = (
        await client.get(f"/api/media/deeds/{deed_id}/photos", headers=reporter.headers)
    ).json()["outcomes"]
    assert [(o["decision"], o["removal_reason"], o["photo_count"]) for o in outcomes] == [
        ("removed_by_doer", None, 1),
        ("kept", None, 1),
        ("removed", "not_consented", 1),
    ]
    assert outcomes[0]["removed_by"] == {
        "handle": doer.handle,
        "display_name": doer.display_name,
        "avatar": None,
    }
    assert outcomes[1]["removed_by"] is None and outcomes[2]["removed_by"] is None
    # The doer's view has no outcomes.
    doer_view = (
        await client.get(f"/api/media/deeds/{deed_id}/photos", headers=doer.headers)
    ).json()
    assert doer_view["outcomes"] == []


# --------------------------------------------------- remove


async def _snapshot(session: AsyncSession, deed_id: uuid.UUID) -> tuple[Any, ...]:
    deed = await session.scalar(
        select(Deed).where(Deed.id == deed_id).execution_options(populate_existing=True)
    )
    assert deed is not None
    group = await session.scalar(
        select(SameActGroup)
        .where(SameActGroup.id == deed.same_act_group_id)
        .execution_options(populate_existing=True)
    )
    assert group is not None
    deed_cols = tuple(getattr(deed, c.key) for c in Deed.__table__.columns if c.key != "location")
    group_cols = tuple(
        getattr(group, c.key) for c in SameActGroup.__table__.columns if c.key != "centre"
    )
    return deed_cols, group_cols


async def test_uploader_removes_any_time_and_urls_die_at_once(
    client: AsyncClient, session: AsyncSession, storage: MemoryStorage
) -> None:
    reporter, _, deed_id = await _setup(session)
    keep = await ready_photo(session, storage, reporter, deed_id=deed_id, position=1)
    photo = await ready_photo(session, storage, reporter, deed_id=deed_id, position=2)
    session.add(
        PhotoReportCase(
            photo_id=photo.id,
            status=ReportCaseStatus.OPEN,
            priority=2,
            report_count=1,
            opened_at=datetime.now(UTC),
        )
    )
    await session.commit()
    keys = list((await fetch(session, photo.id)).variant_keys.values())  # type: ignore[union-attr]
    assert all(k in storage.objects for k in keys)
    # Day 3: adding is closed, removing isn't (Q30).
    await _set_reported_at(session, deed_id, datetime.now(UTC) - timedelta(days=3))
    before = await _snapshot(session, deed_id)
    extra = await uploaded_photo(session, storage, reporter)
    assert (await _attach(client, reporter, deed_id, extra.id)).json()[
        "code"
    ] == "photo_window_closed"

    resp = await client.delete(f"/api/media/photos/{photo.id}", headers=reporter.headers)
    assert resp.status_code == 204
    row = await fetch(session, photo.id)
    assert (row.status, row.takedown, row.deleted_by) == (
        PhotoStatus.DELETED,
        PhotoTakedown.NONE,
        reporter.user_id,
    )
    assert row.objects_purged_at is not None and row.deleted_at is not None
    assert not any(k in storage.objects for k in keys)  # every issued URL now 404s
    case = await session.scalar(select(PhotoReportCase).where(PhotoReportCase.photo_id == photo.id))
    assert case is not None and case.status == ReportCaseStatus.CLOSED
    assert await _snapshot(session, deed_id) == before
    # No doer audit row or event for an uploader removal.
    assert (
        await session.scalar(
            select(func.count()).select_from(AuditLog).where(AuditLog.entity_id == str(photo.id))
        )
        == 0
    )
    from app.modules.media import service

    listed = await service.public_photos(session, [deed_id], storage=storage)
    assert [p.id for p in listed[deed_id]] == [keep.id]
    # Removing again: gone.
    assert (
        await client.delete(f"/api/media/photos/{photo.id}", headers=reporter.headers)
    ).status_code == 404


async def test_doer_removal_is_final_and_audited(
    client: AsyncClient, session: AsyncSession, storage: MemoryStorage
) -> None:
    reporter, doer, deed_id = await _setup(session)
    first = await ready_photo(session, storage, reporter, deed_id=deed_id, position=1)
    data = images.to_bytes(images.noise((400, 300), seed=99), "JPEG")
    target = await ready_photo(session, storage, reporter, deed_id=deed_id, position=2, data=data)
    third = await ready_photo(session, storage, reporter, deed_id=deed_id, position=3)
    keys = list((await fetch(session, target.id)).variant_keys.values())  # type: ignore[union-attr]
    # After the 24-hour window too.
    await _set_reported_at(session, deed_id, datetime.now(UTC) - timedelta(days=2))
    before = await _snapshot(session, deed_id)

    resp = await client.delete(f"/api/media/photos/{target.id}", headers=doer.headers)
    assert resp.status_code == 204
    row = await fetch(session, target.id)
    assert (row.status, row.takedown, row.deleted_by) == (
        PhotoStatus.DELETED,
        PhotoTakedown.DOER_REMOVED,
        doer.user_id,
    )
    assert not any(k in storage.objects for k in keys)
    assert await _snapshot(session, deed_id) == before

    [audit] = (
        await session.scalars(select(AuditLog).where(AuditLog.entity_id == str(target.id)))
    ).all()
    assert (audit.entity_type, audit.action, audit.actor_id) == (
        "photo",
        "photo.doer_removed",
        doer.user_id,
    )
    assert audit.new_value == {
        "deed_id": str(deed_id),
        "position": 2,
        "uploader_id": str(reporter.user_id),
    }
    [event] = (
        await session.scalars(
            select(OutboxEvent).where(
                OutboxEvent.event_type == "media.photo_removed_by_doer",
                OutboxEvent.payload["photo_id"].astext == str(target.id),
            )
        )
    ).all()
    assert event.payload == {
        "photo_id": str(target.id),
        "deed_id": str(deed_id),
        "uploader_id": str(reporter.user_id),
        "doer_id": str(doer.user_id),
    }

    # The others stay, in order.
    from app.modules.media import service

    listed = await service.public_photos(session, [deed_id], storage=storage)
    assert [p.id for p in listed[deed_id]] == [first.id, third.id]
    reporter_view = (
        await client.get(f"/api/media/deeds/{deed_id}/photos", headers=reporter.headers)
    ).json()
    assert [p["id"] for p in reporter_view["photos"]] == [str(first.id), str(third.id)]

    # It doesn't count toward the limit; the same file can't come back, a different one can.
    await _set_reported_at(session, deed_id, datetime.now(UTC))
    same = await uploaded_photo(session, storage, reporter, data=data)
    different = await uploaded_photo(session, storage, reporter)
    assert (await _attach(client, reporter, deed_id, same.id, different.id)).status_code == 200
    await process_all(session, storage)
    assert (await fetch(session, same.id)).reject_reason == "removed_by_doer"
    assert (await fetch(session, different.id)).status == PhotoStatus.READY
    # The doer can remove the new one too.
    assert (
        await client.delete(f"/api/media/photos/{different.id}", headers=doer.headers)
    ).status_code == 204


async def test_who_may_remove(
    client: AsyncClient, session: AsyncSession, storage: MemoryStorage
) -> None:
    reporter, doer, deed_id = await _setup(session)
    stranger = await make_member(session)
    photo = await ready_photo(session, storage, reporter, deed_id=deed_id)
    url = f"/api/media/photos/{photo.id}"
    assert (await client.delete(url, headers=stranger.headers)).status_code == 404
    assert (
        await client.delete(f"/api/media/photos/{uuid.uuid4()}", headers=reporter.headers)
    ).status_code == 404

    # The doer can't remove a photo that isn't ready yet.
    queued = await uploaded_photo(session, storage, reporter)
    assert (await _attach(client, reporter, deed_id, queued.id)).status_code == 200
    assert (
        await client.delete(f"/api/media/photos/{queued.id}", headers=doer.headers)
    ).status_code == 404
    # ... nor an admin-removed photo, nor can the uploader.
    row = await fetch(session, photo.id)
    row.takedown, row.removed_at = PhotoTakedown.ADMIN_REMOVED, datetime.now(UTC)
    await session.commit()
    assert (await client.delete(url, headers=doer.headers)).status_code == 404
    assert (await client.delete(url, headers=reporter.headers)).status_code == 404

    # The doer of a withdrawn deed can't remove anything.
    other = await ready_photo(session, storage, reporter, deed_id=deed_id, position=9)
    deed = await session.get(Deed, deed_id)
    assert deed is not None
    deed.status, deed.withdrawn_at = DeedStatus.WITHDRAWN, datetime.now(UTC)
    await session.commit()
    assert (
        await client.delete(f"/api/media/photos/{other.id}", headers=doer.headers)
    ).status_code == 404
    # The reporter (uploader) still can.
    assert (
        await client.delete(f"/api/media/photos/{other.id}", headers=reporter.headers)
    ).status_code == 204


async def test_doer_cant_remove_self_log_photos(
    client: AsyncClient, session: AsyncSession, storage: MemoryStorage
) -> None:
    owner, doer = await make_member(session), await make_member(session)
    photo = await ready_photo(session, storage, owner, self_log_id=await self_log(session, owner))
    assert (
        await client.delete(f"/api/media/photos/{photo.id}", headers=doer.headers)
    ).status_code == 404
    assert (
        await client.delete(f"/api/media/photos/{photo.id}", headers=owner.headers)
    ).status_code == 204


class _BrokenDeletes(MemoryStorage):
    async def delete(self, key: str) -> None:
        raise ConnectionError("storage down")


async def test_failed_object_delete_leaves_the_row_for_the_purge_job(
    client: AsyncClient, session: AsyncSession, storage: MemoryStorage
) -> None:
    from app.core.storage import get_storage
    from app.main import app

    reporter, _, deed_id = await _setup(session)
    photo = await ready_photo(session, storage, reporter, deed_id=deed_id)
    broken = _BrokenDeletes(objects=dict(storage.objects))
    app.dependency_overrides[get_storage] = lambda: broken
    resp = await client.delete(f"/api/media/photos/{photo.id}", headers=reporter.headers)
    assert resp.status_code == 204
    row = await fetch(session, photo.id)
    assert row.status == PhotoStatus.DELETED and row.objects_purged_at is None


async def test_photo_writes_are_rate_limited(
    client: AsyncClient, session: AsyncSession, storage: MemoryStorage
) -> None:
    reporter, _, deed_id = await _setup(session)
    await publish_media(session, rate_photo_write_user="1/3600")
    photo = await uploaded_photo(session, storage, reporter)
    assert (await _attach(client, reporter, deed_id, photo.id)).status_code == 200
    resp = await client.delete(f"/api/media/photos/{photo.id}", headers=reporter.headers)
    assert (resp.status_code, resp.json()["code"]) == (429, "rate_limited")
