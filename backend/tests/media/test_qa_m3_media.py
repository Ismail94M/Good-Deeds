"""QA5/QA6 (M3): media rules the module tests cover only in parts (docs/M3-DESIGN.md §8 "Media",
"Pipeline privacy", "Reports and admin"; PO decisions Q29 to Q34, Q42, Q44).

- QA6 through the real worker and storage stand-in: GPS/XMP/ICC/IPTC JPEGs, EXIF orientation 6
  and 8, PNG with alpha, animated WebP, a decompression bomb, truncated and wrong-magic files.
- Retention (Q32): restore on day 89 through the API; a restored-then-removed-again photo counts
  from its latest removal.
- Conflict of interest for an admin (not only a moderator).
- IDOR: other people's upload, photo, deed and self-log IDs on every media endpoint.
- No coordinates, phone numbers, IPs or user IDs in any media response.
- No photo action changes the deed, its feed post or its public counts.

Everything runs inside the test's rolled-back transaction (in-memory storage).
"""

import io
import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.storage import MemoryStorage
from app.modules.deeds.models import Deed
from app.modules.feed._projection import sync_post
from app.modules.feed.models import FeedPost
from app.modules.identity.models import Role, User
from app.modules.media import _cleanup, service
from app.modules.media.models import (
    PhotoReportCase,
    PhotoStatus,
    PhotoTakedown,
    ReportCaseStatus,
)
from tests.deeds.helpers import CENTRE, Member, make_member, scan_and_report
from tests.media import images
from tests.media.helpers import (
    drain,
    fetch,
    limits,
    process_all,
    queued_photo,
    ready_photo,
    self_log,
    uploaded_photo,
)

VISITOR = ("198.51.100.77", 1234)


async def _deed(session: AsyncSession) -> tuple[Member, Member, uuid.UUID]:
    reporter, doer = await make_member(session), await make_member(session)
    deed = await scan_and_report(session, reporter, doer)
    await sync_post(session, deed.id)
    await session.commit()
    return reporter, doer, deed.id


async def _with_role(session: AsyncSession, m: Member, role: Role) -> Member:
    user = await session.get(User, m.user_id)
    assert user is not None
    user.role = role
    await session.commit()
    return m


async def _open_case(client: AsyncClient, session: AsyncSession, photo_id: uuid.UUID) -> uuid.UUID:
    stranger = await make_member(session)
    r = await client.post(
        f"/api/media/photos/{photo_id}/reports",
        json={"reason": "not_consented"},
        headers=stranger.headers,
    )
    assert r.status_code == 202, r.text
    case_id = await session.scalar(
        select(PhotoReportCase.id).where(
            PhotoReportCase.photo_id == photo_id, PhotoReportCase.status == ReportCaseStatus.OPEN
        )
    )
    assert case_id is not None
    return case_id


async def _decide(
    client: AsyncClient, case_id: uuid.UUID, who: Member, action: str = "remove"
) -> Any:
    return await client.post(
        f"/api/admin/photo-reports/{case_id}/decision",
        json={"action": action, "note": "Person asked us"},
        headers=who.headers,
    )


async def _restore(client: AsyncClient, photo_id: uuid.UUID, who: Member) -> Any:
    return await client.post(
        f"/api/admin/photos/{photo_id}/restore",
        json={"note": "Consent confirmed"},
        headers=who.headers,
    )


# =================================================== QA6: the worker with fixture images


@pytest.mark.parametrize("orientation", [6, 8])
async def test_worker_strips_metadata_and_stores_upright_dimensions(
    session: AsyncSession, storage: MemoryStorage, orientation: int
) -> None:
    """A rotated JPEG with GPS, camera, time, XMP, ICC, comment and IPTC: the stored variants
    are upright (width/height stored upright), metadata-free in every byte, and nothing else
    is left in the bucket."""
    reporter, _, deed_id = await _deed(session)
    upright = images.solid((800, 600), (128, 128, 128))
    upright.paste((255, 0, 0), (0, 0, 200, 150))  # red block top-left when upright
    stored = upright.transpose(
        Image.Transpose.ROTATE_90 if orientation == 6 else Image.Transpose.ROTATE_270
    )
    data = images.jpeg_with_metadata(stored, orientation=orientation)
    source = Image.open(io.BytesIO(data))
    assert source.getexif().get_ifd(0x8825)  # the fixture really carries GPS
    assert b"http://ns.adobe.com/xap" in data and b"Exif" in data
    photo = await queued_photo(session, storage, reporter, deed_id=deed_id, data=data)

    await process_all(session, storage)

    row = await fetch(session, photo.id)
    assert row.status == PhotoStatus.READY, row.reject_reason
    assert (row.width, row.height) == (800, 600)
    assert row.variant_keys is not None
    assert sorted(storage.objects) == sorted(row.variant_keys.values())
    for name, key in row.variant_keys.items():
        raw = storage.objects[key].data
        for marker in images.FORBIDDEN_MARKERS:
            assert marker not in raw, (name, marker)
        img = Image.open(io.BytesIO(raw))
        img.load()
        assert img.width > img.height, name  # landscape again
        assert not {"exif", "xmp", "icc_profile", "comment", "photoshop"} & set(img.info)
        assert len(img.getexif()) == 0
        r, g, b = img.convert("RGB").getpixel((img.width // 16, img.height // 16))  # type: ignore[misc]
        assert r > 180 and g < 80 and b < 80, (name, (r, g, b))


async def test_worker_flattens_png_alpha_onto_an_opaque_rgb_variant(
    session: AsyncSession, storage: MemoryStorage
) -> None:
    owner = await make_member(session)
    log_id = await self_log(session, owner)
    photo = await queued_photo(
        session,
        storage,
        owner,
        self_log_id=log_id,
        data=images.png_with_alpha((400, 300)),
        content_type="image/png",
    )
    await process_all(session, storage)
    row = await fetch(session, photo.id)
    assert row.status == PhotoStatus.READY and row.variant_keys is not None
    img = Image.open(io.BytesIO(storage.objects[row.variant_keys["display"]].data))
    img.load()
    assert img.mode == "RGB"
    left = img.getpixel((10, img.height // 2))
    right = img.getpixel((img.width - 10, img.height // 2))
    assert all(c > 235 for c in left)  # type: ignore[union-attr]  # transparent -> white
    assert right[0] > 200 and right[1] < 60  # type: ignore[index]  # opaque red stays red


def _truncated_jpeg() -> bytes:
    full = images.to_bytes(images.noise((800, 600)), "JPEG")
    return full[: len(full) // 2]


@pytest.mark.parametrize(
    ("label", "data", "content_type", "reason"),
    [
        ("animated webp", images.animated_webp(), "image/webp", "animated"),
        ("animated png", images.animated_png(), "image/png", "animated"),
        ("50 MP bomb", images.png_header_only(7072, 7072), "image/png", "too_many_pixels"),
        ("400 MP bomb", images.png_header_only(20000, 20000), "image/png", "too_many_pixels"),
        ("truncated", _truncated_jpeg(), "image/jpeg", "corrupt"),
        ("100x100", images.to_bytes(images.noise((100, 100)), "JPEG"), "image/jpeg", "corrupt"),
        (
            "png declared jpeg",
            images.to_bytes(images.noise((400, 300)), "PNG"),
            "image/jpeg",
            "unsupported_type",
        ),
        (
            "gif behind a jpeg magic",
            b"\xff\xd8\xff" + images.to_bytes(images.noise((400, 300)).convert("P"), "GIF"),
            "image/jpeg",
            "corrupt",
        ),
    ],
)
async def test_worker_rejects_hostile_files_and_keeps_nothing(
    session: AsyncSession,
    storage: MemoryStorage,
    label: str,
    data: bytes,
    content_type: str,
    reason: str,
) -> None:
    reporter, _, deed_id = await _deed(session)
    photo = await queued_photo(
        session, storage, reporter, deed_id=deed_id, data=data, content_type=content_type
    )
    await process_all(session, storage)
    row = await fetch(session, photo.id)
    assert (row.status, row.reject_reason) == (PhotoStatus.REJECTED, reason), label
    assert row.variant_keys is None and row.staging_deleted_at is not None
    assert storage.objects == {}, label  # no staging copy, no variant
    # A rejected photo never shows anywhere and doesn't use a slot.
    assert (await service.public_photos(session, [deed_id], storage=storage))[deed_id] == []


async def test_wrong_magic_at_complete_is_rejected_before_any_processing(
    client: AsyncClient, session: AsyncSession, storage: MemoryStorage
) -> None:
    """A WebP declared as JPEG and a text file declared as PNG are refused at complete (magic
    number), and the staging object is deleted."""
    owner = await make_member(session)
    for data, declared in (
        (images.to_bytes(images.noise((400, 300)), "WEBP"), "image/jpeg"),
        (b"<html><script>alert(1)</script></html>" * 10, "image/png"),
    ):
        photo = await uploaded_photo(
            session,
            storage,
            owner,
            data=data,
            content_type=declared,
            status=PhotoStatus.PENDING_UPLOAD,
        )
        r = await client.post(f"/api/media/uploads/{photo.id}/complete", headers=owner.headers)
        assert r.status_code == 400 and r.json()["code"] == "unsupported_type", r.text
        row = await fetch(session, photo.id)
        assert row.status == PhotoStatus.REJECTED
        assert photo.staging_key not in storage.objects


# =================================================== retention (Q32)


async def test_restore_on_day_89_through_the_api_gives_new_urls(
    client: AsyncClient, session: AsyncSession, storage: MemoryStorage
) -> None:
    reporter, _, deed_id = await _deed(session)
    photo = await ready_photo(session, storage, reporter, deed_id=deed_id)
    moderator = await _with_role(session, await make_member(session), Role.MODERATOR)
    case_id = await _open_case(client, session, photo.id)
    before = (await service.public_photos(session, [deed_id], storage=storage))[deed_id]
    assert (await _decide(client, case_id, moderator)).status_code == 200
    lim = await limits(session)
    row = await fetch(session, photo.id)
    row.removed_at = datetime.now(UTC) - timedelta(days=lim.removed_photo_retention_days - 1)
    await session.commit()
    # The nightly job on day 89 leaves it alone ...
    await drain(lambda: _cleanup.purge_removed(session, storage, lim), _cleanup.BATCH)
    assert (await fetch(session, photo.id)).objects_purged_at is None
    # ... and the restore works, with new URLs.
    r = await _restore(client, photo.id, moderator)
    assert r.status_code == 200, r.text
    assert r.json()["photo"]["state"] == "visible"
    after = (await service.public_photos(session, [deed_id], storage=storage))[deed_id]
    assert [p.id for p in after] == [photo.id]
    assert after[0].display_url != before[0].display_url


async def test_a_restored_then_removed_again_photo_counts_from_the_latest_removal(
    client: AsyncClient, session: AsyncSession, storage: MemoryStorage
) -> None:
    reporter, _, deed_id = await _deed(session)
    photo = await ready_photo(session, storage, reporter, deed_id=deed_id)
    admin = await _with_role(session, await make_member(session), Role.ADMIN)
    lim = await limits(session)
    days = lim.removed_photo_retention_days

    first = await _open_case(client, session, photo.id)
    assert (await _decide(client, first, admin)).status_code == 200
    row = await fetch(session, photo.id)
    row.removed_at = datetime.now(UTC) - timedelta(days=days - 10)  # removed 80 days ago
    await session.commit()
    assert (await _restore(client, photo.id, admin)).status_code == 200
    second = await _open_case(client, session, photo.id)
    assert (await _decide(client, second, admin)).status_code == 200
    removed_again = (await fetch(session, photo.id)).removed_at
    assert removed_again is not None and removed_again > datetime.now(UTC) - timedelta(minutes=5)

    # 90 days after the FIRST removal: still held and restorable.
    at = datetime.now(UTC) + timedelta(days=11)
    await drain(lambda: _cleanup.purge_removed(session, storage, lim, now=at), _cleanup.BATCH)
    assert (await fetch(session, photo.id)).objects_purged_at is None
    assert any(k.startswith(f"held/{photo.id}/") for k in storage.objects)
    # 90 days after the latest removal: purged, rows kept.
    late = removed_again + timedelta(days=days, minutes=1)
    await drain(lambda: _cleanup.purge_removed(session, storage, lim, now=late), _cleanup.BATCH)
    row = await fetch(session, photo.id)
    assert row.objects_purged_at == late and row.takedown == PhotoTakedown.ADMIN_REMOVED
    assert not any(str(photo.id) in k for k in storage.objects)
    assert (await _restore(client, photo.id, admin)).json()["code"] == "not_restorable"


# =================================================== roles (Q34)


@pytest.mark.parametrize("role", [Role.ADMIN, Role.MODERATOR])
@pytest.mark.parametrize("who", ["doer", "reporter"])
async def test_conflict_of_interest_holds_for_admins_too(
    client: AsyncClient, session: AsyncSession, storage: MemoryStorage, role: Role, who: str
) -> None:
    reporter, doer, deed_id = await _deed(session)
    photo = await ready_photo(session, storage, reporter, deed_id=deed_id)
    other = await _with_role(session, await make_member(session), Role.ADMIN)
    involved = await _with_role(session, doer if who == "doer" else reporter, role)
    case_id = await _open_case(client, session, photo.id)
    for action in ("keep", "remove"):
        r = await _decide(client, case_id, involved, action)
        assert (r.status_code, r.json()["code"]) == (403, "conflict_of_interest")
    # SEC2 M1: they don't see the case at all (404, the same as a case that doesn't exist).
    r = await client.get(f"/api/admin/photo-reports/{case_id}", headers=involved.headers)
    assert (r.status_code, r.json()["code"]) == (404, "case_not_found")
    assert (await _decide(client, case_id, other)).status_code == 200
    r = await _restore(client, photo.id, involved)
    assert (r.status_code, r.json()["code"]) == (403, "conflict_of_interest")
    assert (await fetch(session, photo.id)).takedown == PhotoTakedown.ADMIN_REMOVED


# =================================================== IDOR: other people's IDs


async def test_other_peoples_ids_on_every_media_endpoint(
    client: AsyncClient, session: AsyncSession, storage: MemoryStorage
) -> None:
    owner, doer, deed_id = await _deed(session)
    stranger, stranger_doer, stranger_deed = await _deed(session)
    upload = await uploaded_photo(session, storage, owner)
    deed_photo = await ready_photo(session, storage, owner, deed_id=deed_id)
    log_id = await self_log(session, owner)
    log_photo = await ready_photo(session, storage, owner, self_log_id=log_id)
    unknown = uuid.uuid4()

    async def same(method: str, path: str, unknown_path: str, body: Any = None) -> None:
        mine = await client.request(method, path, json=body, headers=stranger.headers)
        other = await client.request(method, unknown_path, json=body, headers=stranger.headers)
        assert mine.status_code == other.status_code == 404, (path, mine.text)
        assert mine.json() == other.json(), path

    up, unk = f"/api/media/uploads/{upload.id}", f"/api/media/uploads/{unknown}"
    await same("POST", f"{up}/complete", f"{unk}/complete")
    await same("DELETE", up, unk)
    attach = {"photos": [{"photo_id": str(upload.id), "blur_regions": []}]}
    await same(
        "POST", f"/api/media/deeds/{deed_id}/photos", f"/api/media/deeds/{unknown}/photos", attach
    )
    await same("GET", f"/api/media/deeds/{deed_id}/photos", f"/api/media/deeds/{unknown}/photos")
    await same(
        "GET", f"/api/media/self-logs/{log_id}/photos", f"/api/media/self-logs/{unknown}/photos"
    )
    await same(
        "POST",
        f"/api/media/self-logs/{log_id}/photos",
        f"/api/media/self-logs/{unknown}/photos",
        attach,
    )
    for photo_id in (deed_photo.id, log_photo.id):
        await same("DELETE", f"/api/media/photos/{photo_id}", f"/api/media/photos/{unknown}")
    report = {"reason": "other"}
    await same(
        "POST",
        f"/api/media/photos/{log_photo.id}/reports",
        f"/api/media/photos/{unknown}/reports",
        report,
    )
    # Someone else's upload on the stranger's own deed: refused, and it stays theirs.
    r = await client.post(
        f"/api/media/deeds/{stranger_deed}/photos", json=attach, headers=stranger.headers
    )
    assert (r.status_code, r.json()["code"]) == (409, "photo_not_uploaded")
    # The stranger's deed's doer can't remove the owner's photos either.
    r = await client.delete(f"/api/media/photos/{deed_photo.id}", headers=stranger_doer.headers)
    assert r.status_code == 404

    # Nothing changed for the owner.
    row = await fetch(session, upload.id)
    assert (row.status, row.deed_id, row.uploader_user_id) == (
        PhotoStatus.UPLOADED,
        None,
        owner.user_id,
    )
    for p in (deed_photo, log_photo):
        assert (await fetch(session, p.id)).status == PhotoStatus.READY
    assert (
        await client.get(f"/api/media/deeds/{deed_id}/photos", headers=doer.headers)
    ).status_code == 200


# =================================================== no coordinates or private data


_PRIVATE_KEYS = (
    '"lat"',
    '"lng"',
    '"location"',
    '"coordinates"',
    '"ward_code"',
    '"phone"',
    '"ip"',
    '"ip_hash"',
    '"reporter_ip_hash"',
    '"device_hash"',
    '"content_sha256"',
    '"staging_key"',
    '"variant_keys"',
    '"uploader_user_id"',
    '"reporter_user_id"',
)


async def test_no_media_response_contains_coordinates_ips_phones_or_user_ids(
    client: AsyncClient, session: AsyncSession, storage: MemoryStorage
) -> None:
    reporter, doer, deed_id = await _deed(session)
    first = await ready_photo(session, storage, reporter, deed_id=deed_id, position=1)
    second = await ready_photo(session, storage, reporter, deed_id=deed_id, position=2)
    third = await ready_photo(session, storage, reporter, deed_id=deed_id, position=3)
    log_id = await self_log(session, reporter)
    await ready_photo(session, storage, reporter, self_log_id=log_id)
    moderator = await _with_role(session, await make_member(session), Role.MODERATOR)
    member = await make_member(session)
    # A member report and a visitor report on one photo, a kept case on another, a doer removal.
    r = await client.post(
        f"/api/media/photos/{first.id}/reports",
        json={"reason": "private_details", "details": "My neighbour's house"},
        headers=member.headers,
    )
    assert r.status_code == 202
    from httpx import ASGITransport

    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app, client=VISITOR), base_url="http://test"
    ) as visitor:
        r = await visitor.post(f"/api/media/photos/{first.id}/reports", json={"reason": "other"})
        assert r.status_code == 202
    kept_case = await _open_case(client, session, second.id)
    assert (await _decide(client, kept_case, moderator, "keep")).status_code == 200
    assert (
        await client.delete(f"/api/media/photos/{third.id}", headers=doer.headers)
    ).status_code == 204
    case_id = await session.scalar(
        select(PhotoReportCase.id).where(
            PhotoReportCase.photo_id == first.id, PhotoReportCase.status == ReportCaseStatus.OPEN
        )
    )

    reads: list[tuple[str, Member]] = [
        ("/api/media/options", reporter),
        (f"/api/media/deeds/{deed_id}/photos", reporter),
        (f"/api/media/deeds/{deed_id}/photos", doer),
        (f"/api/media/self-logs/{log_id}/photos", reporter),
        ("/api/admin/photo-reports?status=open", moderator),
        ("/api/admin/photo-reports?status=resolved", moderator),
        (f"/api/admin/photo-reports/{case_id}", moderator),
        (f"/api/admin/photo-reports/{kept_case}", moderator),
    ]
    up = await client.post(
        "/api/media/uploads",
        json={"purpose": "deed", "content_type": "image/jpeg", "size_bytes": 1000},
        headers=reporter.headers,
    )
    assert up.status_code == 201
    bodies: list[tuple[str, Any]] = [("upload", up.json())]
    for path, who in reads:
        resp = await client.get(path, headers=who.headers)
        assert resp.status_code == 200, (path, resp.text)
        bodies.append((path, resp.json()))

    people = [reporter, doer, moderator, member]
    phones = [
        p
        for p in await session.scalars(
            select(User.phone_e164).where(User.id.in_([m.user_id for m in people]))
        )
        if p
    ]
    for path, body in bodies:
        text = json.dumps(body)
        for key in _PRIVATE_KEYS:
            assert key not in text, (path, key)
        assert re.search(r"1[0-4]\.\d{3,}\D{1,6}7[5-9]\.\d{3,}", text) is None, path
        assert str(CENTRE[0]) not in text and str(CENTRE[1]) not in text, path
        assert VISITOR[0] not in text, path
        for m in people:
            assert str(m.user_id) not in text, (path, "user id")
        for phone in phones:
            assert phone[-10:] not in text, (path, "phone")
    # The visitor is shown as a visitor, the member by handle only (§3.3 #12).
    detail = dict(bodies)[f"/api/admin/photo-reports/{case_id}"]
    kinds = sorted(r["reporter"]["kind"] for r in detail["reports"])
    assert kinds == ["member", "visitor"]
    assert all(set(r["reporter"]) <= {"kind", "handle"} for r in detail["reports"])
    # The doer's view never shows the uploader's outcomes; the reporter's shows the removal.
    as_doer = (await client.get(f"/api/media/deeds/{deed_id}/photos", headers=doer.headers)).json()
    as_reporter = (
        await client.get(f"/api/media/deeds/{deed_id}/photos", headers=reporter.headers)
    ).json()
    assert as_doer["outcomes"] == []
    assert {o["decision"] for o in as_reporter["outcomes"]} == {"kept", "removed_by_doer"}


# =================================================== nothing scored changes


async def _scored(
    client: AsyncClient, session: AsyncSession, deed_id: uuid.UUID, people: list[Member]
) -> Any:
    deed = await session.scalar(
        select(Deed).where(Deed.id == deed_id).execution_options(populate_existing=True)
    )
    post = await session.scalar(
        select(FeedPost)
        .where(FeedPost.deed_id == deed_id)
        .execution_options(populate_existing=True)
    )
    assert deed is not None and post is not None
    counts = [
        (await client.get(f"/api/users/{m.handle}/deed-counts", headers=m.headers)).json()
        for m in people
    ]
    return (
        tuple(getattr(deed, c.key) for c in Deed.__table__.columns if c.key != "location"),
        tuple(getattr(post, c.key) for c in FeedPost.__table__.columns),
        counts,
    )


async def test_no_photo_action_changes_the_deed_its_post_or_counts(
    client: AsyncClient, session: AsyncSession, storage: MemoryStorage
) -> None:
    """Attach, process, report (member and visitor), keep, remove, restore, doer removal and
    uploader removal: the deed row, its feed post and the public deed counts never change."""
    reporter, doer, deed_id = await _deed(session)
    admin = await _with_role(session, await make_member(session), Role.ADMIN)
    people = [reporter, doer]
    before = await _scored(client, session, deed_id, people)

    a, b, c = [await uploaded_photo(session, storage, reporter) for _ in range(3)]
    r = await client.post(
        f"/api/media/deeds/{deed_id}/photos",
        json={
            "photos": [
                {"photo_id": str(a.id), "blur_regions": [{"x": 0.1, "y": 0.1, "w": 0.3, "h": 0.3}]},
                {"photo_id": str(b.id), "blur_regions": []},
                {"photo_id": str(c.id), "blur_regions": []},
            ]
        },
        headers=reporter.headers,
    )
    assert r.status_code == 200, r.text
    await process_all(session, storage)
    for p in (a, b, c):
        assert (await fetch(session, p.id)).status == PhotoStatus.READY
    case = await _open_case(client, session, a.id)
    assert (await _decide(client, case, admin, "keep")).status_code == 200
    case = await _open_case(client, session, a.id)
    assert (await _decide(client, case, admin, "remove")).status_code == 200
    assert (await _restore(client, a.id, admin)).status_code == 200
    assert (
        await client.delete(f"/api/media/photos/{b.id}", headers=doer.headers)
    ).status_code == 204
    assert (
        await client.delete(f"/api/media/photos/{c.id}", headers=reporter.headers)
    ).status_code == 204

    assert await _scored(client, session, deed_id, people) == before


# =================================================== Q33: viewers see nothing where a photo was


async def test_removed_photos_leave_no_trace_in_feed_and_public_json(
    client: AsyncClient, session: AsyncSession, storage: MemoryStorage
) -> None:
    """Three photos; the doer removes one and a moderator another: the feed card, the deed page
    and the public page (real photo adapter) list only the remaining photo, with no field or
    word that tells a photo was removed."""
    reporter, doer, deed_id = await _deed(session)
    viewer = await make_member(session)
    moderator = await _with_role(session, await make_member(session), Role.MODERATOR)
    kept = await ready_photo(session, storage, reporter, deed_id=deed_id, position=1)
    by_doer = await ready_photo(session, storage, reporter, deed_id=deed_id, position=2)
    by_mod = await ready_photo(session, storage, reporter, deed_id=deed_id, position=3)
    assert (
        await client.delete(f"/api/media/photos/{by_doer.id}", headers=doer.headers)
    ).status_code == 204
    case_id = await _open_case(client, session, by_mod.id)
    assert (await _decide(client, case_id, moderator)).status_code == 200

    bodies = []
    for path, who in (
        (f"/api/feed/deeds/{deed_id}", viewer),
        (f"/api/feed/deeds/{deed_id}", doer),
        (f"/api/public/deeds/{deed_id}", None),
    ):
        r = await client.get(path, headers=who.headers if who else {})
        assert r.status_code == 200, r.text
        bodies.append(r.json())
    nearby = await client.get("/api/feed?tab=nearby&limit=50", headers=viewer.headers)
    card = next((i for i in nearby.json()["items"] if i["deed_id"] == str(deed_id)), None)
    if card is not None:  # other files' newer posts may push it off the first page
        bodies.append(card)
    for body in bodies:
        assert [p["id"] for p in body["photos"]] == [str(kept.id)]
        text = json.dumps(body).lower()
        assert "removed" not in text and "takedown" not in text and "photo_count" not in text
        assert str(by_doer.id) not in text and str(by_mod.id) not in text
