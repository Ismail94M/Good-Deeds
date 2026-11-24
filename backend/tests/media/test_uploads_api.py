"""Options, create, complete and discard (§3.3 #1 to #4, §8 "Media")."""

import uuid
from typing import Any

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ratelimit import InMemoryRateLimiter
from app.core.storage import MemoryStorage
from app.modules.media.models import PhotoStatus
from tests.deeds.helpers import Member, make_member
from tests.media import images
from tests.media.helpers import fetch, publish_media


def _body(**overrides: Any) -> dict[str, Any]:
    return {"purpose": "deed", "content_type": "image/jpeg", "size_bytes": 2048} | overrides


async def _create(client: AsyncClient, m: Member, **overrides: Any) -> dict[str, Any]:
    resp = await client.post("/api/media/uploads", json=_body(**overrides), headers=m.headers)
    assert resp.status_code == 201, resp.text
    body: dict[str, Any] = resp.json()
    return body


async def test_options(client: AsyncClient, session: AsyncSession) -> None:
    m = await make_member(session)
    resp = await client.get("/api/media/options", headers=m.headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["photos_enabled"] is True
    assert body["photos_per_owner"] == 4
    assert body["photo_edit_window_hours"] == 24
    assert body["upload_allowed_types"] == ["image/jpeg", "image/png", "image/webp"]
    assert body["upload_max_bytes"] == 10485760
    assert body["blur_max_regions"] == 8
    assert body["blur_min_region"] == 0.02
    assert body["decision_note_max"] == 1000
    assert [(r["id"], r["priority"]) for r in body["report_reasons"]] == [
        ("not_consented", 2),
        ("child_no_guardian_consent", 1),
        ("private_details", 2),
        ("inappropriate", 2),
        ("other", 3),
    ]
    assert all(r["label"] for r in body["report_reasons"])
    # The blur editor follows config.
    await publish_media(session, blur_min_region=0.05, blur_max_regions=3)
    body = (await client.get("/api/media/options", headers=m.headers)).json()
    assert (body["blur_min_region"], body["blur_max_regions"]) == (0.05, 3)
    not_onboarded = await make_member(session, onboarded=False)
    resp = await client.get("/api/media/options", headers=not_onboarded.headers)
    assert resp.status_code == 403


async def test_create_signs_a_put_for_the_declared_type_and_length(
    client: AsyncClient, session: AsyncSession
) -> None:
    m = await make_member(session)
    body = await _create(client, m, content_type="image/png", size_bytes=4321)
    photo_id = uuid.UUID(body["photo_id"])
    assert body["upload"]["method"] == "PUT"
    assert body["upload"]["headers"] == {"Content-Type": "image/png", "Content-Length": "4321"}
    assert f"staging/{photo_id}" in body["upload"]["url"]
    assert "expires=600" in body["upload"]["url"]
    row = await fetch(session, photo_id)
    assert row.status == PhotoStatus.PENDING_UPLOAD
    assert row.uploader_user_id == m.user_id
    assert row.staging_key == f"staging/{photo_id}"
    assert row.upload_ip_hash and len(row.upload_ip_hash) == 64


async def test_create_validation(client: AsyncClient, session: AsyncSession) -> None:
    m = await make_member(session)
    cases: list[tuple[dict[str, Any], int, str]] = [
        ({"content_type": "image/gif"}, 400, "unsupported_type"),
        ({"content_type": "image/heic"}, 400, "unsupported_type"),
        ({"size_bytes": 10485761}, 400, "too_large"),
    ]
    for overrides, status, code in cases:
        resp = await client.post("/api/media/uploads", json=_body(**overrides), headers=m.headers)
        assert (resp.status_code, resp.json().get("code")) == (status, code)
    resp = await client.post("/api/media/uploads", json=_body(size_bytes=0), headers=m.headers)
    assert resp.status_code == 422
    resp = await client.post("/api/media/uploads", json=_body(purpose="x"), headers=m.headers)
    assert resp.status_code == 422
    # Exactly the maximum is fine.
    await _create(client, m, size_bytes=10485760)


async def test_pending_upload_cap(client: AsyncClient, session: AsyncSession) -> None:
    m = await make_member(session)
    for _ in range(12):
        await _create(client, m)
    resp = await client.post("/api/media/uploads", json=_body(), headers=m.headers)
    assert (resp.status_code, resp.json()["code"]) == (429, "too_many_pending_uploads")
    # Another user is unaffected; a cap from config applies at once.
    other = await make_member(session)
    await _create(client, other)
    await publish_media(session, max_pending_uploads_per_user=1)
    resp = await client.post("/api/media/uploads", json=_body(), headers=other.headers)
    assert resp.status_code == 429


async def test_photos_disabled(client: AsyncClient, session: AsyncSession) -> None:
    m = await make_member(session)
    await publish_media(session, photos_enabled=False)
    resp = await client.post("/api/media/uploads", json=_body(), headers=m.headers)
    assert (resp.status_code, resp.json()["code"]) == (403, "photos_disabled")
    assert (await client.get("/api/media/options", headers=m.headers)).json()[
        "photos_enabled"
    ] is False


async def test_create_is_rate_limited(
    client: AsyncClient, session: AsyncSession, rate_limiter: InMemoryRateLimiter
) -> None:
    m = await make_member(session)
    await publish_media(session, rate_upload_user="2/3600", max_pending_uploads_per_user=50)
    await _create(client, m)
    await _create(client, m)
    resp = await client.post("/api/media/uploads", json=_body(), headers=m.headers)
    assert (resp.status_code, resp.json()["code"]) == (429, "rate_limited")


async def _uploaded(
    client: AsyncClient, storage: MemoryStorage, m: Member, data: bytes, ctype: str
) -> uuid.UUID:
    body = await _create(client, m, content_type=ctype, size_bytes=len(data))
    photo_id = uuid.UUID(body["photo_id"])
    await storage.put_bytes(f"staging/{photo_id}", data, ctype, "")
    return photo_id


async def test_complete(client: AsyncClient, session: AsyncSession, storage: MemoryStorage) -> None:
    m = await make_member(session)
    data = images.to_bytes(images.noise((400, 300)), "JPEG")
    photo_id = await _uploaded(client, storage, m, data, "image/jpeg")
    url = f"/api/media/uploads/{photo_id}/complete"

    other = await make_member(session)
    assert (await client.post(url, headers=other.headers)).status_code == 404

    resp = await client.post(url, headers=m.headers)
    assert resp.status_code == 200
    assert resp.json() == {"photo_id": str(photo_id), "status": "uploaded"}
    row = await fetch(session, photo_id)
    assert row.status == PhotoStatus.UPLOADED and row.uploaded_at is not None
    # Idempotent.
    assert (await client.post(url, headers=m.headers)).status_code == 200


async def test_complete_missing_object(client: AsyncClient, session: AsyncSession) -> None:
    m = await make_member(session)
    body = await _create(client, m)
    resp = await client.post(f"/api/media/uploads/{body['photo_id']}/complete", headers=m.headers)
    assert (resp.status_code, resp.json()["code"]) == (409, "upload_missing")
    assert (await fetch(session, uuid.UUID(body["photo_id"]))).status == (
        PhotoStatus.PENDING_UPLOAD
    )


async def test_complete_size_mismatch_rejects_and_deletes(
    client: AsyncClient, session: AsyncSession, storage: MemoryStorage
) -> None:
    m = await make_member(session)
    body = await _create(client, m, size_bytes=5000)
    photo_id = uuid.UUID(body["photo_id"])
    await storage.put_bytes(f"staging/{photo_id}", b"\xff\xd8\xff" + b"0" * 100, "image/jpeg", "")
    resp = await client.post(f"/api/media/uploads/{photo_id}/complete", headers=m.headers)
    assert (resp.status_code, resp.json()["code"]) == (400, "size_mismatch")
    row = await fetch(session, photo_id)
    assert (row.status, row.reject_reason) == (PhotoStatus.REJECTED, "size_mismatch")
    assert row.staging_deleted_at is not None
    assert storage.objects == {}


async def test_complete_wrong_magic_rejects_and_deletes(
    client: AsyncClient, session: AsyncSession, storage: MemoryStorage
) -> None:
    m = await make_member(session)
    png = images.to_bytes(images.noise((400, 300)), "PNG")
    photo_id = await _uploaded(client, storage, m, png, "image/jpeg")  # a PNG declared as JPEG
    resp = await client.post(f"/api/media/uploads/{photo_id}/complete", headers=m.headers)
    assert (resp.status_code, resp.json()["code"]) == (400, "unsupported_type")
    row = await fetch(session, photo_id)
    assert (row.status, row.reject_reason) == (PhotoStatus.REJECTED, "unsupported_type")
    assert storage.objects == {}
    # A rejected upload can't be completed again.
    resp = await client.post(f"/api/media/uploads/{photo_id}/complete", headers=m.headers)
    assert resp.status_code == 404


async def test_complete_accepts_each_supported_type(
    client: AsyncClient, session: AsyncSession, storage: MemoryStorage
) -> None:
    m = await make_member(session)
    for fmt, ctype in [("JPEG", "image/jpeg"), ("PNG", "image/png"), ("WEBP", "image/webp")]:
        data = images.to_bytes(images.noise((300, 300)), fmt)
        photo_id = await _uploaded(client, storage, m, data, ctype)
        resp = await client.post(f"/api/media/uploads/{photo_id}/complete", headers=m.headers)
        assert resp.status_code == 200, fmt


async def test_discard(client: AsyncClient, session: AsyncSession, storage: MemoryStorage) -> None:
    m = await make_member(session)
    data = images.to_bytes(images.noise((300, 300)), "JPEG")
    photo_id = await _uploaded(client, storage, m, data, "image/jpeg")
    other = await make_member(session)
    url = f"/api/media/uploads/{photo_id}"
    assert (await client.delete(url, headers=other.headers)).status_code == 404
    assert (await client.delete(url, headers=m.headers)).status_code == 204
    row = await fetch(session, photo_id)
    assert row.status == PhotoStatus.EXPIRED and row.staging_deleted_at is not None
    assert storage.objects == {}
    assert (await client.delete(url, headers=m.headers)).status_code == 204  # again
    # Discarded uploads don't count toward the pending cap.
    await publish_media(session, max_pending_uploads_per_user=1)
    await _create(client, m)
