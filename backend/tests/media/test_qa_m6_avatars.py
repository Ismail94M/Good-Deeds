"""QA12: profile-picture rules, boundaries and abuse (docs/M6-DESIGN.md §8 "Profile pictures",
D116 to D123; PO Q86, Q87, Q94, Q95). Complements ``test_avatars.py``."""

import uuid
from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.storage import MemoryStorage
from app.modules.feed._projection import sync_post
from app.modules.identity.models import Role
from app.modules.media import _keys
from app.modules.media._cleanup import purge_removed
from app.modules.media.events import AvatarChanged
from app.modules.media.models import (
    PhotoPurpose,
    PhotoRejectReason,
    PhotoReportCase,
    PhotoStatus,
    PhotoTakedown,
)
from tests.deeds.helpers import Member, make_member, publish_limits, scan_and_report
from tests.deeds.invite_helpers import create_invite
from tests.media import images
from tests.media.helpers import fetch, limits, process_all, publish_media, uploaded_photo
from tests.media.test_avatars import (
    _apply_changes,
    _events,
    _live_avatar,
    _ref,
    _set,
    _upload,
    _with_role,
)


async def _report(
    client: AsyncClient,
    session: AsyncSession,
    photo_id: uuid.UUID,
    reporter: Member,
    reason: str = "inappropriate",
) -> PhotoReportCase:
    resp = await client.post(
        f"/api/media/photos/{photo_id}/reports", json={"reason": reason}, headers=reporter.headers
    )
    assert resp.status_code == 202, resp.text
    case = await session.scalar(
        select(PhotoReportCase)
        .where(PhotoReportCase.photo_id == photo_id)
        .order_by(PhotoReportCase.opened_at.desc())
        .execution_options(populate_existing=True)
    )
    assert case is not None
    return case


async def _decide(
    client: AsyncClient, who: Member, case_id: uuid.UUID, action: str = "remove"
) -> int:
    resp = await client.post(
        f"/api/admin/photo-reports/{case_id}/decision",
        json={"action": action, "note": "Reviewed against the guidelines"},
        headers=who.headers,
    )
    return resp.status_code


async def _removed_avatar(
    client: AsyncClient, session: AsyncSession, storage: MemoryStorage, owner: Member
) -> tuple[uuid.UUID, Member]:
    """A live picture, reported and removed by an admin (held, Q95). Returns (photo, admin)."""
    reporter = await make_member(session)
    admin = await _with_role(session, Role.ADMIN)
    live = await _live_avatar(client, session, storage, owner)
    case = await _report(client, session, live.id, reporter)
    assert await _decide(client, admin, case.id) == 200
    await _apply_changes(session, owner.user_id)
    return live.id, admin


# --------------------------------------------------- Q86: signed-out callers never get a picture


async def test_public_pages_never_carry_a_picture_signed_in_pages_do(
    client: AsyncClient, session: AsyncSession, storage: MemoryStorage
) -> None:
    reporter, doer, viewer = [await make_member(session) for _ in range(3)]
    r_live = await _live_avatar(client, session, storage, reporter)
    d_live = await _live_avatar(client, session, storage, doer)
    deed = await scan_and_report(session, reporter, doer)
    await sync_post(session, deed.id)
    await session.commit()
    r_ref, d_ref = _keys.avatar_ref(r_live.id, 1), _keys.avatar_ref(d_live.id, 1)

    # Signed in: the references are there (so the null below is the rule, not missing data).
    inside = (await client.get(f"/api/feed/deeds/{deed.id}", headers=viewer.headers)).json()
    assert inside["doer"]["avatar"] == d_ref and inside["reporter"]["avatar"] == r_ref

    public = await client.get(f"/api/public/deeds/{deed.id}")
    assert public.status_code == 200
    body = public.json()
    assert body["doer"]["avatar"] is None and body["reporter"]["avatar"] is None
    assert str(r_live.id) not in public.text and str(d_live.id) not in public.text
    for person in (reporter, doer):
        profile = await client.get(f"/api/users/{person.handle}")
        assert profile.status_code == 200 and profile.json()["avatar"] is None
        assert "avatar_" not in profile.text
    # The signed-out invite preview names the reporter, but never with a picture (SEC6-L2).
    await publish_limits(session, invites_enabled=True)
    _, token = await create_invite(session, reporter)
    await session.commit()
    preview = await client.get(f"/api/invites/by-token/{token}")
    assert preview.status_code == 200, preview.text
    assert preview.json()["reporter"]["handle"] == reporter.handle
    assert preview.json()["reporter"]["avatar"] is None
    assert str(r_live.id) not in preview.text
    # Visitors can't sign a reference either.
    resp = await client.post("/api/media/avatars/urls", json={"refs": [r_ref]})
    assert resp.status_code == 401


# --------------------------------------------------- validation (M3 pipeline reused)


async def test_bad_files_are_rejected_and_the_old_picture_stays(
    client: AsyncClient, session: AsyncSession, storage: MemoryStorage
) -> None:
    owner = await make_member(session)
    await publish_media(session, rate_avatar_change_user="50/86400")
    live = await _live_avatar(client, session, storage, owner)
    cases: list[tuple[bytes, str, set[PhotoRejectReason]]] = [
        (images.animated_webp((300, 300)), "image/webp", {PhotoRejectReason.ANIMATED}),
        (images.animated_png((300, 300)), "image/png", {PhotoRejectReason.ANIMATED}),
        # A decompression bomb: the header claims 50 MP and 400 MP.
        (images.png_header_only(7072, 7072), "image/png", {PhotoRejectReason.TOO_MANY_PIXELS}),
        (images.png_header_only(20000, 20000), "image/png", {PhotoRejectReason.TOO_MANY_PIXELS}),
        # Declared JPEG, really a PNG.
        (
            images.to_bytes(images.noise((400, 400)), "PNG"),
            "image/jpeg",
            {PhotoRejectReason.UNSUPPORTED_TYPE},
        ),
        (b"\xff\xd8\xff\xe0 not really a jpeg" * 40, "image/jpeg", {PhotoRejectReason.CORRUPT}),
    ]
    for data, content_type, reasons in cases:
        bad = await uploaded_photo(
            session,
            storage,
            owner,
            data=data,
            content_type=content_type,
            purpose=PhotoPurpose.AVATAR,
        )
        assert (await _set(client, owner, bad.id)).status_code == 202
        await process_all(session, storage)
        row = await fetch(session, bad.id)
        assert row.status == PhotoStatus.REJECTED, (content_type, row.status)
        assert row.reject_reason in reasons, (content_type, row.reject_reason)
        assert not [k for k in storage.objects if k.startswith(f"photos/{bad.id}/")]
        mine = (await client.get("/api/media/me/avatar", headers=owner.headers)).json()
        assert mine["current"] is not None
        assert mine["pending"] == {"status": "rejected", "reject_reason": row.reject_reason}
    assert (await fetch(session, live.id)).status == PhotoStatus.READY
    assert await _ref(session, owner.user_id) == _keys.avatar_ref(live.id, 1)


async def test_upload_type_and_size_are_checked_at_create(
    client: AsyncClient, session: AsyncSession, storage: MemoryStorage
) -> None:
    owner = await make_member(session)
    max_bytes = (await limits(session)).upload_max_bytes

    async def create(content_type: str, size: int) -> int:
        resp = await client.post(
            "/api/media/uploads",
            json={"purpose": "avatar", "content_type": content_type, "size_bytes": size},
            headers=owner.headers,
        )
        return resp.status_code

    assert await create("image/jpeg", max_bytes) == 201  # at the limit
    assert await create("image/jpeg", max_bytes + 1) in (400, 413, 422)
    assert await create("image/gif", 1000) in (400, 422)
    assert await create("image/svg+xml", 1000) in (400, 422)


async def test_every_variant_is_free_of_metadata(
    client: AsyncClient, session: AsyncSession, storage: MemoryStorage
) -> None:
    owner = await make_member(session)
    live = await _live_avatar(
        client, session, storage, owner, images.jpeg_with_metadata(images.noise((640, 480)))
    )
    keys = _keys.variant_keys_of(live.variant_keys)
    assert set(keys) == {"avatar_sm", "avatar_md", "avatar_lg"}
    for name, key in keys.items():
        data = storage.objects[key].data
        for marker in images.FORBIDDEN_MARKERS:
            assert marker not in data, (name, marker)
        assert set(images.webp_chunks(data)) <= {b"VP8 ", b"VP8L", b"VP8X", b"ALPH"}, name


async def test_image_exactly_at_the_minimum_side_is_accepted(
    client: AsyncClient, session: AsyncSession, storage: MemoryStorage
) -> None:
    owner = await make_member(session)
    side = (await limits(session)).avatar_min_side_px
    ok = await _upload(session, storage, owner, images.to_bytes(images.noise((side, side)), "JPEG"))
    assert (await _set(client, owner, ok.id)).status_code == 202
    small = await _upload(
        session, storage, owner, images.to_bytes(images.noise((side - 1, side + 50)), "JPEG")
    )
    resp = await _set(client, owner, small.id)
    assert (resp.status_code, resp.json()["code"]) == (422, "image_too_small")
    # Crops outside 0..1 or not square in pixels are refused before queueing.
    wide = await _upload(session, storage, owner, images.to_bytes(images.noise((600, 600)), "JPEG"))
    for crop in (
        {"x": -0.1, "y": 0.0, "size": 1.0},
        {"x": 0.0, "y": 0.0, "size": 1.2},
        {"x": 0.5, "y": 0.5, "size": 0.9},
    ):
        resp = await _set(client, owner, wide.id, crop)
        assert resp.status_code == 422, crop
    assert (await fetch(session, wide.id)).status == PhotoStatus.UPLOADED


# --------------------------------------------------- D123: 5 changes a day


async def test_five_changes_a_day_with_the_seeded_limit(
    client: AsyncClient, session: AsyncSession, storage: MemoryStorage
) -> None:
    owner = await make_member(session)
    assert (await limits(session)).rate_avatar_change_user == "5/86400"
    photos = [await _upload(session, storage, owner) for _ in range(6)]
    for photo in photos[:5]:
        assert (await _set(client, owner, photo.id)).status_code == 202
    resp = await _set(client, owner, photos[5].id)
    assert resp.status_code == 429
    assert (await fetch(session, photos[5].id)).status == PhotoStatus.UPLOADED
    # The limit is per person.
    other = await make_member(session)
    theirs = await _upload(session, storage, other)
    assert (await _set(client, other, theirs.id)).status_code == 202


# --------------------------------------------------- Q95: 24 h wait, 90-day hold, restore


async def test_cooldown_ends_exactly_24_hours_after_the_removal(
    client: AsyncClient, session: AsyncSession, storage: MemoryStorage
) -> None:
    owner = await make_member(session)
    removed_id, _ = await _removed_avatar(client, session, storage, owner)
    hours = (await limits(session)).avatar_reupload_cooldown_hours
    assert hours == 24
    candidate = await _upload(session, storage, owner)

    async def removed_ago(delta: timedelta) -> None:
        row = await fetch(session, removed_id)
        row.removed_at = datetime.now(UTC) - delta
        await session.commit()

    await removed_ago(timedelta(hours=hours) - timedelta(minutes=2))
    resp = await _set(client, owner, candidate.id)
    assert (resp.status_code, resp.json()["code"]) == (409, "cooldown")

    await removed_ago(timedelta(hours=hours) + timedelta(minutes=1))
    assert (await _set(client, owner, candidate.id)).status_code == 202
    await process_all(session, storage)
    assert (await fetch(session, candidate.id)).status == PhotoStatus.READY
    # The removed one stays held (not deleted by the new picture).
    held = await fetch(session, removed_id)
    assert held.takedown == PhotoTakedown.ADMIN_REMOVED and held.status == PhotoStatus.READY


async def test_an_upload_queued_before_an_admin_removal_never_goes_live_in_the_wait(
    client: AsyncClient, session: AsyncSession, storage: MemoryStorage
) -> None:
    """SEC6-L3: the 24 h wait is checked again when the worker would make a picture live, so an
    upload queued just before the removal is rejected (``cooldown``) and the owner is told."""
    owner, reporter = await make_member(session), await make_member(session)
    admin = await _with_role(session, Role.ADMIN)
    live = await _live_avatar(client, session, storage, owner)
    case = await _report(client, session, live.id, reporter)
    queued = await _upload(session, storage, owner)
    assert (await _set(client, owner, queued.id)).status_code == 202  # before the removal
    assert await _decide(client, admin, case.id) == 200
    await process_all(session, storage)

    row = await fetch(session, queued.id)
    assert (row.status, row.reject_reason) == (PhotoStatus.REJECTED, PhotoRejectReason.COOLDOWN)
    assert not any(k.startswith(f"photos/{queued.id}/") for k in storage.objects)
    assert row.staging_key not in storage.objects
    ready = [
        e
        for e in await _events(session, owner.user_id, AvatarChanged.event_type)
        if e["reason"] == "ready"
    ]
    assert [e["avatar_ref"] for e in ready] == [_keys.avatar_ref(live.id, 1)]
    await _apply_changes(session, owner.user_id)
    assert await _ref(session, owner.user_id) is None
    mine = (await client.get("/api/media/me/avatar", headers=owner.headers)).json()
    assert mine["current"] is None
    assert mine["pending"] == {"status": "rejected", "reject_reason": "cooldown"}
    assert mine["removed_notice"] is not None


async def test_held_for_90_days_then_purged_and_not_restorable(
    client: AsyncClient, session: AsyncSession, storage: MemoryStorage
) -> None:
    owner = await make_member(session)
    removed_id, admin = await _removed_avatar(client, session, storage, owner)
    row = await fetch(session, removed_id)
    held = [_keys.held_key(k) for k in _keys.variant_keys_of(row.variant_keys).values()]
    live_keys = list(_keys.variant_keys_of(row.variant_keys).values())
    assert all(k in storage.objects for k in held)
    assert not any(k in storage.objects for k in live_keys)  # old links die at once
    assert row.removed_at is not None
    removed_at = row.removed_at
    media_limits = await limits(session)
    assert media_limits.removed_photo_retention_days == 90

    await purge_removed(session, storage, media_limits, now=removed_at + timedelta(days=89))
    assert all(k in storage.objects for k in held)
    assert (await fetch(session, removed_id)).objects_purged_at is None

    await purge_removed(
        session, storage, media_limits, now=removed_at + timedelta(days=90, hours=1)
    )
    assert not any(k in storage.objects for k in held)
    assert (await fetch(session, removed_id)).objects_purged_at is not None
    await session.commit()
    resp = await client.post(
        f"/api/admin/photos/{removed_id}/restore",
        json={"note": "Reviewed again, it is fine"},
        headers=admin.headers,
    )
    assert (resp.status_code, resp.json()["code"]) == (409, "not_restorable")
    await _apply_changes(session, owner.user_id)
    assert await _ref(session, owner.user_id) is None


async def test_restore_is_refused_once_a_newer_picture_is_live_after_the_cooldown(
    client: AsyncClient, session: AsyncSession, storage: MemoryStorage
) -> None:
    owner = await make_member(session)
    removed_id, admin = await _removed_avatar(client, session, storage, owner)
    row = await fetch(session, removed_id)
    row.removed_at = datetime.now(UTC) - timedelta(hours=25)
    await session.commit()
    newer = await _live_avatar(client, session, storage, owner)
    resp = await client.post(
        f"/api/admin/photos/{removed_id}/restore",
        json={"note": "Reviewed again, it is fine"},
        headers=admin.headers,
    )
    assert (resp.status_code, resp.json()["code"]) == (409, "avatar_replaced")
    assert await _ref(session, owner.user_id) == _keys.avatar_ref(newer.id, 1)
    assert (await fetch(session, removed_id)).takedown == PhotoTakedown.ADMIN_REMOVED


# --------------------------------------------------- Q87, Q94, D121: reports and who decides


async def test_children_first_then_impersonation_in_the_avatar_queue(
    client: AsyncClient, session: AsyncSession, storage: MemoryStorage
) -> None:
    first, second, third, reporter = [await make_member(session) for _ in range(4)]
    admin = await _with_role(session, Role.ADMIN)
    other_live = await _live_avatar(client, session, storage, first)
    imp_live = await _live_avatar(client, session, storage, second)
    kid_live = await _live_avatar(client, session, storage, third)
    other = await _report(client, session, other_live.id, reporter, "other")
    imp = await _report(client, session, imp_live.id, reporter, "impersonation")
    kid = await _report(client, session, kid_live.id, reporter, "child_no_guardian_consent")
    assert (kid.priority, imp.priority) == (1, 2)
    assert other.priority > imp.priority

    queue = await client.get(
        "/api/admin/photo-reports", params={"purpose": "avatar"}, headers=admin.headers
    )
    order = [i["case_id"] for i in queue.json()["items"]]
    ours = [c for c in order if c in {str(kid.id), str(imp.id), str(other.id)}]
    assert ours == [str(kid.id), str(imp.id), str(other.id)]
    # A report never hides the picture by itself (M3 D42).
    assert await _ref(session, third.user_id) == _keys.avatar_ref(kid_live.id, 1)


async def test_only_uninvolved_reviewers_decide_an_avatar_report(
    client: AsyncClient, session: AsyncSession, storage: MemoryStorage
) -> None:
    member = await make_member(session)
    owner = await _with_role(session, Role.MODERATOR)  # a moderator's own picture
    reporting_mod = await _with_role(session, Role.MODERATOR)
    other_mod = await _with_role(session, Role.MODERATOR)
    live = await _live_avatar(client, session, storage, owner)
    case_id = (await _report(client, session, live.id, reporting_mod, "impersonation")).id

    assert await _decide(client, member, case_id) == 403  # members never decide
    assert await _decide(client, owner, case_id) in (403, 404)  # nobody decides their own
    assert await _decide(client, reporting_mod, case_id) in (403, 404)  # nor what they reported
    for who in (owner, reporting_mod):
        detail = await client.get(f"/api/admin/photo-reports/{case_id}", headers=who.headers)
        assert detail.status_code == 404
    assert (await fetch(session, live.id)).takedown == PhotoTakedown.NONE

    assert await _decide(client, other_mod, case_id, "keep") == 200
    assert (await fetch(session, live.id)).takedown == PhotoTakedown.NONE
    assert await _events(session, owner.user_id, "media.avatar_moderation_decided")


async def test_inactive_owner_has_no_report_target_and_visitors_cant_report(
    client: AsyncClient, session: AsyncSession, storage: MemoryStorage
) -> None:
    from tests.notifications.helpers import set_status

    owner, reporter = await make_member(session), await make_member(session)
    not_onboarded = await make_member(session, onboarded=False)
    live = await _live_avatar(client, session, storage, owner)
    url = f"/api/users/{owner.handle}/avatar-report-target"
    assert (await client.get(url)).status_code == 401
    assert (await client.get(url, headers=reporter.headers)).status_code == 200
    resp = await client.post(
        f"/api/media/photos/{live.id}/reports",
        json={"reason": "impersonation"},
        headers=not_onboarded.headers,
    )
    assert resp.status_code in (403, 404)

    from app.modules.identity.models import UserStatus

    await set_status(session, owner, UserStatus.SUSPENDED)
    await session.commit()
    assert (await client.get(url, headers=reporter.headers)).status_code == 404


# --------------------------------------------------- D116: an old upload finishing late


async def test_an_older_upload_finishing_late_never_replaces_the_newer_picture(
    client: AsyncClient, session: AsyncSession, storage: MemoryStorage
) -> None:
    owner = await make_member(session)
    older = await _upload(session, storage, owner)
    assert (await _set(client, owner, older.id)).status_code == 202
    # Hold the older one back from the worker, so the newer one finishes first.
    row = await fetch(session, older.id)
    row.status = PhotoStatus.UPLOADED
    await session.commit()
    newer = await _live_avatar(client, session, storage, owner)

    row = await fetch(session, older.id)
    row.status = PhotoStatus.QUEUED  # the slow job finally runs
    await session.commit()
    await process_all(session, storage)

    assert (await fetch(session, older.id)).status == PhotoStatus.DELETED
    assert (await fetch(session, newer.id)).status == PhotoStatus.READY
    assert not [k for k in storage.objects if k.startswith(f"photos/{older.id}/")]
    refs = [
        e["avatar_ref"] for e in await _events(session, owner.user_id, AvatarChanged.event_type)
    ]
    assert refs == [_keys.avatar_ref(newer.id, 1)]
    await _apply_changes(session, owner.user_id)
    assert await _ref(session, owner.user_id) == _keys.avatar_ref(newer.id, 1)


async def test_removing_my_picture_also_drops_an_upload_still_processing(
    client: AsyncClient, session: AsyncSession, storage: MemoryStorage
) -> None:
    owner = await make_member(session)
    live = await _live_avatar(client, session, storage, owner)
    pending = await _upload(session, storage, owner)
    assert (await _set(client, owner, pending.id)).status_code == 202
    assert (await client.delete("/api/media/me/avatar", headers=owner.headers)).status_code == 204
    await process_all(session, storage)
    assert (await fetch(session, pending.id)).status == PhotoStatus.DELETED
    assert (await fetch(session, live.id)).status == PhotoStatus.DELETED
    await _apply_changes(session, owner.user_id)
    assert await _ref(session, owner.user_id) is None
