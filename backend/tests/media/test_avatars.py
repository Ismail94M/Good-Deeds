"""Profile pictures (M6A, docs/M6-DESIGN.md §3.3 P1 to P6, §4.3, §4.4, §8 "Profile pictures"):
upload with ``purpose = avatar``, the server-side square crop and variants, replace and
remove, the batched URL endpoint, reports and the admin queue, restore, and identity's
pointer."""

import io
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import OutboxEvent
from app.core.storage import MemoryStorage
from app.modules.identity.handlers import on_avatar_changed
from app.modules.identity.models import Role, User, UserStatus
from app.modules.identity.service import public_summaries
from app.modules.media import _keys, _pipeline
from app.modules.media.events import AvatarChanged
from app.modules.media.models import (
    Photo,
    PhotoPurpose,
    PhotoReportCase,
    PhotoStatus,
    PhotoTakedown,
)
from tests.deeds.helpers import Member, make_member
from tests.media import images
from tests.media.helpers import fetch, process_all, publish_media, uploaded_photo

RED, BLUE = (220, 20, 20), (20, 20, 220)
FULL = {"x": 0.0, "y": 0.0, "size": 1.0}


def _halves(size: tuple[int, int] = (800, 400)) -> bytes:
    """Left half red, right half blue."""
    img = Image.new("RGB", size, RED)
    img.paste(Image.new("RGB", (size[0] // 2, size[1]), BLUE), (size[0] // 2, 0))
    return images.to_bytes(img, "JPEG", quality=95)


async def _upload(
    session: AsyncSession, storage: MemoryStorage, owner: Member, data: bytes | None = None
) -> Photo:
    return await uploaded_photo(
        session,
        storage,
        owner,
        data=data if data is not None else images.to_bytes(images.noise((600, 600)), "JPEG"),
        purpose=PhotoPurpose.AVATAR,
    )


async def _set(
    client: AsyncClient, owner: Member, photo_id: uuid.UUID, crop: dict[str, float] = FULL
) -> Any:
    return await client.put(
        "/api/media/me/avatar",
        json={"photo_id": str(photo_id), "crop": crop},
        headers=owner.headers,
    )


async def _events(session: AsyncSession, user_id: uuid.UUID, event_type: str) -> list[Any]:
    """The person's events of a type, oldest first (by the payload's ``changed_at`` when it has
    one: rows written in one test transaction share ``occurred_at``)."""
    rows = await session.scalars(select(OutboxEvent).where(OutboxEvent.event_type == event_type))
    found = [r.payload for r in rows if r.payload.get("user_id") == str(user_id)]
    return sorted(found, key=lambda p: str(p.get("changed_at", "")))


async def _apply_changes(session: AsyncSession, user_id: uuid.UUID) -> None:
    """Run identity's handler over the person's AvatarChanged events, in order."""
    for payload in await _events(session, user_id, AvatarChanged.event_type):
        await on_avatar_changed(session, AvatarChanged.model_validate(payload))
    await session.commit()


async def _ref(session: AsyncSession, user_id: uuid.UUID) -> str | None:
    user = await session.scalar(
        select(User).where(User.id == user_id).execution_options(populate_existing=True)
    )
    assert user is not None
    return user.avatar_ref


async def _live_avatar(
    client: AsyncClient,
    session: AsyncSession,
    storage: MemoryStorage,
    owner: Member,
    data: bytes | None = None,
) -> Photo:
    photo = await _upload(session, storage, owner, data)
    assert (await _set(client, owner, photo.id)).status_code == 202
    await process_all(session, storage)
    photo = await fetch(session, photo.id)
    assert photo.status == PhotoStatus.READY, photo.reject_reason
    await _apply_changes(session, owner.user_id)
    session.expunge(photo)  # a failed request's rollback can't expire it
    return photo


async def _with_role(session: AsyncSession, role: Role) -> Member:
    m = await make_member(session)
    user = await session.get(User, m.user_id)
    assert user is not None
    user.role = role
    await session.commit()
    return m


# --------------------------------------------------- pipeline (pure)


def test_crop_box_rules() -> None:
    crop = _pipeline.AvatarCrop
    # size is a fraction of the shorter side; x of the width, y of the height.
    assert _pipeline.crop_box(crop(0.5, 0.0, 1.0), 800, 400, 256) == (400, 0, 800, 400)
    # A small overshoot from rounding is moved back inside.
    assert _pipeline.crop_box(crop(0.502, 0.0, 1.0), 800, 400, 256) == (400, 0, 800, 400)
    with pytest.raises(_pipeline.CropInvalid):
        _pipeline.crop_box(crop(0.7, 0.0, 1.0), 800, 400, 256)
    with pytest.raises(_pipeline.CropTooSmall):
        _pipeline.crop_box(crop(0.0, 0.0, 0.5), 800, 400, 256)  # 200 px
    with pytest.raises(_pipeline.CropTooSmall):
        _pipeline.crop_box(crop(0.0, 0.0, 1.0), 300, 200, 256)  # image too small


def test_upright_size_reads_the_header_and_orientation() -> None:
    plain = images.to_bytes(images.noise((600, 300)), "JPEG")
    assert _pipeline.upright_size(plain, "image/jpeg") == (600, 300)
    turned = images.jpeg_with_metadata(images.noise((600, 300)), orientation=6)
    assert _pipeline.upright_size(turned, "image/jpeg") == (300, 600)
    assert _pipeline.upright_size(b"not an image", "image/jpeg") is None


# --------------------------------------------------- P1, P2: upload and set


async def test_upload_and_set_crops_on_the_server(
    client: AsyncClient, session: AsyncSession, storage: MemoryStorage
) -> None:
    owner = await make_member(session)
    created = await client.post(
        "/api/media/uploads",
        json={"purpose": "avatar", "content_type": "image/jpeg", "size_bytes": 1000},
        headers=owner.headers,
    )
    assert created.status_code == 201
    created_row = await fetch(session, uuid.UUID(created.json()["photo_id"]))
    assert created_row.purpose == PhotoPurpose.AVATAR

    photo = await _upload(session, storage, owner, images.jpeg_with_metadata(_halves_img()))
    # Keep the right (blue) half: nothing red may reach any variant.
    resp = await _set(client, owner, photo.id, {"x": 0.5, "y": 0.0, "size": 1.0})
    assert resp.status_code == 202 and resp.json() == {"status": "processing"}
    assert (await _set(client, owner, photo.id, FULL)).status_code == 202  # retry: same answer
    await process_all(session, storage)

    photo = await fetch(session, photo.id)
    assert photo.status == PhotoStatus.READY
    keys = _keys.variant_keys_of(photo.variant_keys)
    assert set(keys) == {"avatar_sm", "avatar_md", "avatar_lg"}
    assert staging_gone(storage, photo)
    for name, side in (("avatar_sm", 96), ("avatar_md", 256), ("avatar_lg", 400)):
        obj = storage.objects[keys[name]]
        assert obj.cache_control == _keys.AVATAR_CACHE_CONTROL
        img = Image.open(io.BytesIO(obj.data))
        assert img.size == (side, side), name
        assert not img.getexif() and "exif" not in img.info and "xmp" not in img.info
        r, _, b = img.convert("RGB").resize((1, 1)).getpixel((0, 0))  # type: ignore[misc]
        assert b > 150 and r < 80, name

    [event] = await _events(session, owner.user_id, AvatarChanged.event_type)
    ref = _keys.avatar_ref(photo.id, 1)
    assert event["avatar_ref"] == ref and event["reason"] == "ready"
    await _apply_changes(session, owner.user_id)
    assert await _ref(session, owner.user_id) == ref
    me = (await client.get("/api/users/me", headers=owner.headers)).json()
    assert me["avatar"] == ref


def _halves_img() -> Image.Image:
    return Image.open(io.BytesIO(_halves()))


def staging_gone(storage: MemoryStorage, photo: Photo) -> bool:
    return photo.staging_key not in storage.objects


async def test_set_errors(
    client: AsyncClient, session: AsyncSession, storage: MemoryStorage
) -> None:
    owner, other = await make_member(session), await make_member(session)
    pending = await uploaded_photo(
        session, storage, owner, purpose=PhotoPurpose.AVATAR, status=PhotoStatus.PENDING_UPLOAD
    )
    resp = await _set(client, owner, pending.id)
    assert (resp.status_code, resp.json()["code"]) == (409, "upload_not_complete")

    small = await _upload(
        session, storage, owner, images.to_bytes(images.noise((300, 200)), "JPEG")
    )
    resp = await _set(client, owner, small.id)
    assert (resp.status_code, resp.json()["code"]) == (422, "image_too_small")

    photo = await _upload(session, storage, owner, _halves())
    resp = await _set(client, owner, photo.id, {"x": 0.8, "y": 0.0, "size": 1.0})
    assert (resp.status_code, resp.json()["code"]) == (422, "crop_invalid")
    assert (await _set(client, other, photo.id)).status_code == 404  # not yours
    deed_photo = await uploaded_photo(session, storage, owner)
    assert (await _set(client, owner, deed_photo.id)).status_code == 404  # not an avatar upload
    assert (await fetch(session, photo.id)).status == PhotoStatus.UPLOADED


async def test_avatars_disabled_and_change_limit(
    client: AsyncClient, session: AsyncSession, storage: MemoryStorage
) -> None:
    owner = await make_member(session)
    await publish_media(session, rate_avatar_change_user="1/86400")
    photo = await _upload(session, storage, owner)
    assert (await _set(client, owner, photo.id)).status_code == 202
    assert (await _set(client, owner, photo.id)).status_code == 429

    await publish_media(session, avatars_enabled=False)
    resp = await client.post(
        "/api/media/uploads",
        json={"purpose": "avatar", "content_type": "image/jpeg", "size_bytes": 1000},
        headers=owner.headers,
    )
    assert (resp.status_code, resp.json()["code"]) == (409, "avatars_disabled")


async def test_a_rejected_file_keeps_the_old_picture(
    client: AsyncClient, session: AsyncSession, storage: MemoryStorage
) -> None:
    owner = await make_member(session)
    live = await _live_avatar(client, session, storage, owner)
    bad = await uploaded_photo(
        session,
        storage,
        owner,
        data=images.png_header_only(600, 600),
        content_type="image/png",
        purpose=PhotoPurpose.AVATAR,
    )
    assert (await _set(client, owner, bad.id)).status_code == 202
    await process_all(session, storage)
    assert (await fetch(session, bad.id)).status == PhotoStatus.REJECTED
    assert (await fetch(session, live.id)).status == PhotoStatus.READY
    mine = (await client.get("/api/media/me/avatar", headers=owner.headers)).json()
    assert mine["current"] is not None
    assert mine["pending"]["status"] == "rejected"


# --------------------------------------------------- replace and remove (P3, P4)


async def test_replace_deletes_the_old_picture_at_once(
    client: AsyncClient, session: AsyncSession, storage: MemoryStorage
) -> None:
    owner = await make_member(session)
    old = await _live_avatar(client, session, storage, owner)
    old_keys = list(_keys.variant_keys_of(old.variant_keys).values())
    new = await _live_avatar(client, session, storage, owner)

    old = await fetch(session, old.id)
    assert old.status == PhotoStatus.DELETED and old.objects_purged_at is not None
    assert not any(k in storage.objects for k in old_keys)
    assert await _ref(session, owner.user_id) == _keys.avatar_ref(new.id, 1)
    mine = (await client.get("/api/media/me/avatar", headers=owner.headers)).json()
    assert mine["current"]["md_url"] and mine["pending"] is None and mine["removed_notice"] is None


async def test_an_older_upload_never_replaces_a_newer_picture(
    client: AsyncClient, session: AsyncSession, storage: MemoryStorage
) -> None:
    owner = await make_member(session)
    first = await _upload(session, storage, owner)
    assert (await _set(client, owner, first.id)).status_code == 202
    newer = await _live_avatar(client, session, storage, owner)
    # ``first`` was processed in the same worker run; whichever finished last, the picture set
    # last is the live one.
    assert (await fetch(session, newer.id)).status == PhotoStatus.READY
    assert (await fetch(session, first.id)).status == PhotoStatus.DELETED
    assert await _ref(session, owner.user_id) == _keys.avatar_ref(newer.id, 1)


async def test_owner_removal(
    client: AsyncClient, session: AsyncSession, storage: MemoryStorage
) -> None:
    owner = await make_member(session)
    live = await _live_avatar(client, session, storage, owner)
    keys = list(_keys.variant_keys_of(live.variant_keys).values())
    # The generic photo delete doesn't take a profile picture (identity would never hear).
    assert (
        await client.delete(f"/api/media/photos/{live.id}", headers=owner.headers)
    ).status_code == 404
    assert (await client.delete("/api/media/me/avatar", headers=owner.headers)).status_code == 204
    assert not any(k in storage.objects for k in keys)
    assert (await fetch(session, live.id)).status == PhotoStatus.DELETED
    await _apply_changes(session, owner.user_id)
    assert await _ref(session, owner.user_id) is None
    assert (await client.delete("/api/media/me/avatar", headers=owner.headers)).status_code == 204
    mine = (await client.get("/api/media/me/avatar", headers=owner.headers)).json()
    assert mine == {"current": None, "pending": None, "removed_notice": None}


# --------------------------------------------------- P5: batched URLs


async def test_avatar_urls(
    client: AsyncClient, session: AsyncSession, storage: MemoryStorage
) -> None:
    owner, viewer = await make_member(session), await make_member(session)
    live = await _live_avatar(client, session, storage, owner)
    ref = _keys.avatar_ref(live.id, 1)
    stale = _keys.avatar_ref(live.id, 2)
    body = {"refs": [ref, stale, "junk", ref]}

    assert (await client.post("/api/media/avatars/urls", json=body)).status_code == 401
    resp = await client.post("/api/media/avatars/urls", json=body, headers=viewer.headers)
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert set(items) == {ref, stale, "junk"}
    assert items[stale] is None and items["junk"] is None
    urls = items[ref]
    key = _keys.variant_keys_of(live.variant_keys)["avatar_md"]
    assert key in urls["md_url"] and "expires=86400" in urls["md_url"]

    too_many = {"refs": [_keys.avatar_ref(uuid.uuid4(), 1) for _ in range(101)]}
    resp = await client.post("/api/media/avatars/urls", json=too_many, headers=viewer.headers)
    assert (resp.status_code, resp.json()["code"]) == (422, "too_many_refs")

    user = await session.get(User, owner.user_id)
    assert user is not None
    user.status = UserStatus.SUSPENDED
    await session.commit()
    resp = await client.post("/api/media/avatars/urls", json=body, headers=viewer.headers)
    assert resp.json()["items"][ref] is None
    assert (await public_summaries(session, [owner.user_id]))[owner.user_id].avatar_ref is None


async def test_avatar_sign_rate_limit(
    client: AsyncClient, session: AsyncSession, storage: MemoryStorage
) -> None:
    viewer = await make_member(session)
    await publish_media(session, rate_avatar_sign_user="1/60")
    body: dict[str, list[str]] = {"refs": []}
    assert (
        await client.post("/api/media/avatars/urls", json=body, headers=viewer.headers)
    ).status_code == 200
    assert (
        await client.post("/api/media/avatars/urls", json=body, headers=viewer.headers)
    ).status_code == 429


async def test_public_profile_shows_the_picture_to_signed_in_viewers_only(
    client: AsyncClient, session: AsyncSession, storage: MemoryStorage
) -> None:
    owner, viewer = await make_member(session), await make_member(session)
    live = await _live_avatar(client, session, storage, owner)
    signed_out = (await client.get(f"/api/users/{owner.handle}")).json()
    assert signed_out["avatar"] is None
    signed_in = (await client.get(f"/api/users/{owner.handle}", headers=viewer.headers)).json()
    assert signed_in["avatar"] == _keys.avatar_ref(live.id, 1)


# --------------------------------------------------- identity pointer (§4.4)


async def test_out_of_order_events_never_overwrite_a_newer_picture(session: AsyncSession) -> None:
    owner = await make_member(session)
    now = datetime.now(UTC)
    newer = AvatarChanged(user_id=owner.user_id, avatar_ref="b", changed_at=now, reason="ready")
    older = AvatarChanged(
        user_id=owner.user_id, avatar_ref="a", changed_at=now - timedelta(seconds=5), reason="ready"
    )
    await on_avatar_changed(session, newer)
    await on_avatar_changed(session, older)
    await session.commit()
    assert await _ref(session, owner.user_id) == "b"


# --------------------------------------------------- reports, admin queue, restore (P6)


async def test_report_remove_cooldown_and_restore(
    client: AsyncClient, session: AsyncSession, storage: MemoryStorage
) -> None:
    owner, reporter, kid_reporter = [await make_member(session) for _ in range(3)]
    admin = await _with_role(session, Role.ADMIN)
    live = await _live_avatar(client, session, storage, owner)
    live_id = live.id

    target = await client.get(
        f"/api/users/{owner.handle}/avatar-report-target", headers=reporter.headers
    )
    assert target.status_code == 200 and target.json() == {"photo_id": str(live_id)}
    assert (
        await client.get(f"/api/users/{owner.handle}/avatar-report-target", headers=owner.headers)
    ).status_code == 404
    url = f"/api/media/photos/{live_id}/reports"
    assert (await client.post(url, json={"reason": "impersonation"})).status_code == 404  # visitor
    assert (
        await client.post(url, json={"reason": "impersonation"}, headers=owner.headers)
    ).status_code == 400  # own picture
    assert (
        await client.post(url, json={"reason": "impersonation"}, headers=reporter.headers)
    ).status_code == 202
    case = await session.scalar(select(PhotoReportCase).where(PhotoReportCase.photo_id == live_id))
    assert case is not None and case.priority == 2
    assert (
        await client.post(
            url, json={"reason": "child_no_guardian_consent"}, headers=kid_reporter.headers
        )
    ).status_code == 202
    await session.refresh(case)
    assert case.priority == 1  # children first (Q94)

    queue = await client.get(
        "/api/admin/photo-reports", params={"purpose": "avatar"}, headers=admin.headers
    )
    items = [i for i in queue.json()["items"] if i["case_id"] == str(case.id)]
    assert len(items) == 1
    assert items[0]["purpose"] == "avatar" and items[0]["deed"] is None
    assert items[0]["owner"]["handle"] == owner.handle
    assert items[0]["owner"]["avatar"] == _keys.avatar_ref(live_id, 1)
    deeds_only = await client.get(
        "/api/admin/photo-reports", params={"purpose": "deed"}, headers=admin.headers
    )
    assert all(i["purpose"] == "deed" for i in deeds_only.json()["items"])
    detail = (await client.get(f"/api/admin/photo-reports/{case.id}", headers=admin.headers)).json()
    assert detail["purpose"] == "avatar" and detail["deed"] is None
    assert detail["owner"]["handle"] == owner.handle and detail["photo"]["display_url"]

    resp = await client.post(
        f"/api/admin/photo-reports/{case.id}/decision",
        json={"action": "remove", "note": "Not the account holder"},
        headers=admin.headers,
    )
    assert resp.status_code == 200
    live = await fetch(session, live_id)
    assert live.takedown == PhotoTakedown.ADMIN_REMOVED and live.object_move is None
    held = [_keys.held_key(k) for k in _keys.variant_keys_of(live.variant_keys).values()]
    assert all(k in storage.objects for k in held)
    [decided] = await _events(session, owner.user_id, "media.avatar_moderation_decided")
    assert (
        decided["action"] == "remove" and decided["removal_reason"] == "child_no_guardian_consent"
    )
    await _apply_changes(session, owner.user_id)
    assert await _ref(session, owner.user_id) is None

    mine = (await client.get("/api/media/me/avatar", headers=owner.headers)).json()
    assert mine["current"] is None
    assert mine["removed_notice"]["reason_label"] == "Shows a child"
    # Q95: a new picture only after the cooldown.
    again = await _upload(session, storage, owner)
    resp = await _set(client, owner, again.id)
    assert (resp.status_code, resp.json()["code"]) == (409, "cooldown")
    assert "available_at" in resp.json()

    # Restore while there is no newer picture: a new version, so a new reference.
    resp = await client.post(
        f"/api/admin/photos/{live_id}/restore",
        json={"note": "Reviewed again, it is fine"},
        headers=admin.headers,
    )
    assert resp.status_code == 200
    live = await fetch(session, live_id)
    assert live.takedown == PhotoTakedown.NONE and live.object_move is None
    await _apply_changes(session, owner.user_id)
    assert await _ref(session, owner.user_id) == _keys.avatar_ref(live_id, 2)


async def test_restore_refused_when_a_newer_picture_is_live(
    client: AsyncClient, session: AsyncSession, storage: MemoryStorage
) -> None:
    owner, reporter = await make_member(session), await make_member(session)
    admin = await _with_role(session, Role.MODERATOR)
    await publish_media(session, avatar_reupload_cooldown_hours=0)
    first = await _live_avatar(client, session, storage, owner)
    assert (
        await client.post(
            f"/api/media/photos/{first.id}/reports",
            json={"reason": "inappropriate"},
            headers=reporter.headers,
        )
    ).status_code == 202
    case = await session.scalar(select(PhotoReportCase).where(PhotoReportCase.photo_id == first.id))
    assert case is not None
    assert (
        await client.post(
            f"/api/admin/photo-reports/{case.id}/decision",
            json={"action": "remove", "note": "Not allowed here"},
            headers=admin.headers,
        )
    ).status_code == 200
    await _live_avatar(client, session, storage, owner)
    resp = await client.post(
        f"/api/admin/photos/{first.id}/restore",
        json={"note": "Reviewed again, it is fine"},
        headers=admin.headers,
    )
    assert (resp.status_code, resp.json()["code"]) == (409, "avatar_replaced")


async def test_deed_photos_cant_be_reported_for_impersonation(
    client: AsyncClient, session: AsyncSession, storage: MemoryStorage
) -> None:
    from app.modules.feed._projection import sync_post
    from tests.deeds.helpers import scan_and_report
    from tests.media.helpers import ready_photo

    reporter, doer, other = [await make_member(session) for _ in range(3)]
    deed = await scan_and_report(session, reporter, doer)
    await sync_post(session, deed.id)
    await session.commit()
    photo = await ready_photo(session, storage, reporter, deed_id=deed.id)
    resp = await client.post(
        f"/api/media/photos/{photo.id}/reports",
        json={"reason": "impersonation"},
        headers=other.headers,
    )
    assert (resp.status_code, resp.json()["code"]) == (422, "invalid_reason")
    # The public share page never carries picture references (D119).
    public = (await client.get(f"/api/public/deeds/{deed.id}")).json()
    assert public["doer"]["avatar"] is None and public["reporter"]["avatar"] is None
