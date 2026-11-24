"""The whole photo flow against a real S3-compatible store (SeaweedFS locally): presigned PUT,
complete, attach, worker, signed GET, then the immediate cut-off of issued URLs on uploader
and doer removal (§4.7, Q29, Q30, Q31).

Runs only when GD_TEST_STORAGE_URL is set (see tests/test_storage_integration.py)."""

import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import cast

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.storage import AddressingStyle, S3Storage, get_storage
from app.main import app
from app.modules.feed._projection import sync_post
from app.modules.identity.models import Role, User
from app.modules.media import _worker, service
from app.modules.media.models import PhotoReportCase
from tests.deeds.helpers import Member, make_member, scan_and_report
from tests.media import images
from tests.media.helpers import fetch

STORAGE_URL = os.environ.get("GD_TEST_STORAGE_URL", "")
pytestmark = pytest.mark.skipif(not STORAGE_URL, reason="GD_TEST_STORAGE_URL is not set")


@pytest.fixture
async def s3(client: AsyncClient) -> AsyncIterator[S3Storage]:
    store = S3Storage(
        endpoint_url=STORAGE_URL,
        public_endpoint_url=STORAGE_URL,
        region=os.environ.get("GD_TEST_STORAGE_REGION", "us-east-1"),
        bucket=os.environ.get("GD_TEST_STORAGE_BUCKET", "gooddeeds-media"),
        access_key_id=os.environ.get("GD_TEST_STORAGE_ACCESS_KEY", "gooddeeds-dev"),
        secret_access_key=os.environ.get(
            "GD_TEST_STORAGE_SECRET_KEY", "gooddeeds-dev-secret-not-for-production"
        ),
        addressing_style=cast(
            AddressingStyle, os.environ.get("GD_TEST_STORAGE_ADDRESSING_STYLE", "path")
        ),
    )
    app.dependency_overrides[get_storage] = lambda: store
    yield store
    app.dependency_overrides.pop(get_storage, None)


async def _upload(client: AsyncClient, store: S3Storage, m: Member, data: bytes) -> uuid.UUID:
    resp = await client.post(
        "/api/media/uploads",
        json={"purpose": "deed", "content_type": "image/jpeg", "size_bytes": len(data)},
        headers=m.headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    up = body["upload"]
    async with httpx.AsyncClient() as http:
        put = await http.put(up["url"], content=data, headers=up["headers"])
        assert put.status_code == 200, put.text
        # The store refuses another type than the signed one.
        wrong = await http.put(
            up["url"], content=data, headers={**up["headers"], "Content-Type": "image/png"}
        )
        assert wrong.status_code in (400, 403)
    photo_id = uuid.UUID(body["photo_id"])
    resp = await client.post(f"/api/media/uploads/{photo_id}/complete", headers=m.headers)
    assert resp.status_code == 200, resp.text
    return photo_id


async def _cleanup(store: S3Storage, photo_ids: list[uuid.UUID]) -> None:
    future = datetime.now(UTC) + timedelta(days=1)
    for pid in photo_ids:
        for prefix in (f"staging/{pid}", f"photos/{pid}/", f"held/{pid}/"):
            async for key in store.list_older_than(prefix, future):
                await store.delete(key)


async def test_issued_urls_stop_working_right_after_removal(
    client: AsyncClient, session: AsyncSession, s3: S3Storage
) -> None:
    reporter, doer = await make_member(session), await make_member(session)
    deed = await scan_and_report(session, reporter, doer)
    await session.commit()
    a = images.to_bytes(images.noise((800, 600), seed=1), "JPEG")
    b = images.to_bytes(images.noise((800, 600), seed=2), "JPEG")
    ids = [await _upload(client, s3, reporter, a), await _upload(client, s3, reporter, b)]
    try:
        resp = await client.post(
            f"/api/media/deeds/{deed.id}/photos",
            json={
                "photos": [
                    {
                        "photo_id": str(ids[0]),
                        "blur_regions": [{"x": 0.2, "y": 0.2, "w": 0.4, "h": 0.4}],
                    },
                    {"photo_id": str(ids[1]), "blur_regions": []},
                ]
            },
            headers=reporter.headers,
        )
        assert resp.status_code == 200, resp.text
        while await _worker.process_batch(session, s3) == _worker.CLAIM_BATCH:
            pass

        # Only blurred variants remain in the bucket: no staging object (Q31).
        assert await s3.head(f"staging/{ids[0]}") is None
        photos = (await service.public_photos(session, [deed.id], storage=s3))[deed.id]
        assert [p.id for p in photos] == ids
        async with httpx.AsyncClient() as http:
            for p in photos:
                for url in (p.thumb_url, p.display_url):
                    got = await http.get(url)
                    assert got.status_code == 200
                    assert got.headers["content-type"] == "image/webp"
                    for marker in images.FORBIDDEN_MARKERS:
                        assert marker not in got.content

            # The doer removes the first photo, the uploader the second: every URL already
            # issued returns 404 from the store at once.
            r1 = await client.delete(f"/api/media/photos/{ids[0]}", headers=doer.headers)
            r2 = await client.delete(f"/api/media/photos/{ids[1]}", headers=reporter.headers)
            assert (r1.status_code, r2.status_code) == (204, 204)
            for p in photos:
                for url in (p.thumb_url, p.display_url):
                    assert (await http.get(url)).status_code in (403, 404)
        for pid in ids:
            assert (await fetch(session, pid)).objects_purged_at is not None
    finally:
        await _cleanup(s3, ids)


async def test_admin_removal_cuts_off_urls_and_restore_issues_new_ones(
    client: AsyncClient, session: AsyncSession, s3: S3Storage
) -> None:
    reporter, doer, admin = [await make_member(session) for _ in range(3)]
    user = await session.get(User, admin.user_id)
    assert user is not None
    user.role = Role.ADMIN
    deed = await scan_and_report(session, reporter, doer)
    await sync_post(session, deed.id)
    await session.commit()
    data = images.to_bytes(images.noise((800, 600), seed=3), "JPEG")
    photo_id = await _upload(client, s3, reporter, data)
    try:
        resp = await client.post(
            f"/api/media/deeds/{deed.id}/photos",
            json={"photos": [{"photo_id": str(photo_id), "blur_regions": []}]},
            headers=reporter.headers,
        )
        assert resp.status_code == 200
        while await _worker.process_batch(session, s3) == _worker.CLAIM_BATCH:
            pass
        [before] = (await service.public_photos(session, [deed.id], storage=s3))[deed.id]

        resp = await client.post(
            f"/api/media/photos/{photo_id}/reports", json={"reason": "not_consented"}
        )
        assert resp.status_code == 202
        case_id = await session.scalar(
            select(PhotoReportCase.id).where(PhotoReportCase.photo_id == photo_id)
        )
        resp = await client.post(
            f"/api/admin/photo-reports/{case_id}/decision",
            json={"action": "remove", "note": "Person asked"},
            headers=admin.headers,
        )
        assert resp.status_code == 200, resp.text
        held_url = resp.json()["photo"]["display_url"]
        async with httpx.AsyncClient() as http:
            # Every URL issued before the removal is dead at once; the admin's held URL works.
            for url in (before.thumb_url, before.display_url):
                assert (await http.get(url)).status_code in (403, 404)
            assert (await http.get(held_url)).status_code == 200

            resp = await client.post(
                f"/api/admin/photos/{photo_id}/restore",
                json={"note": "Consent confirmed"},
                headers=admin.headers,
            )
            assert resp.status_code == 200, resp.text
            [after] = (await service.public_photos(session, [deed.id], storage=s3))[deed.id]
            assert after.display_url != before.display_url
            assert (await http.get(after.display_url)).status_code == 200
            assert (await http.get(after.thumb_url)).status_code == 200
            # Links from before the removal never work again; the held copy is gone too.
            assert (await http.get(before.display_url)).status_code in (403, 404)
            assert (await http.get(held_url)).status_code in (403, 404)
    finally:
        await _cleanup(s3, [photo_id])
