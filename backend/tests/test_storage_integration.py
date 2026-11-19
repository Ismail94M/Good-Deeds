"""S3Storage against a real store (SeaweedFS locally, or a GCS test bucket).

Runs only when GD_TEST_STORAGE_URL is set, e.g. with `docker compose up -d storage storage-init`:
    GD_TEST_STORAGE_URL=http://localhost:8333 uv run pytest tests/test_storage_integration.py
Keys default to the SeaweedFS dev identity (infra/seaweedfs/s3.json); override with
GD_TEST_STORAGE_ACCESS_KEY, GD_TEST_STORAGE_SECRET_KEY, GD_TEST_STORAGE_BUCKET,
GD_TEST_STORAGE_REGION and GD_TEST_STORAGE_ADDRESSING_STYLE (e.g. for GCS: region `auto`).
Every test works under its own random prefix and deletes what it wrote.
"""

import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import cast

import httpx
import pytest

from app.core.storage import (
    AddressingStyle,
    ObjectNotFound,
    ObjectTooLarge,
    S3Storage,
    move,
    stable_signing_time,
)

STORAGE_URL = os.environ.get("GD_TEST_STORAGE_URL", "")
WEB_ORIGIN = "http://localhost:5173"
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 2044

pytestmark = pytest.mark.skipif(not STORAGE_URL, reason="GD_TEST_STORAGE_URL is not set")


@pytest.fixture
async def storage() -> AsyncIterator[S3Storage]:
    yield S3Storage(
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


@pytest.fixture
async def prefix(storage: S3Storage) -> AsyncIterator[str]:
    """A random prefix per test; everything under it (and under held/) is deleted after."""
    value = f"it-{uuid.uuid4().hex}/"
    yield value
    for root in (value, f"held/{value}"):
        future = datetime.now(UTC) + timedelta(days=1)
        async for key in storage.list_older_than(root, future):
            await storage.delete(key)


@pytest.fixture
async def http() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(timeout=10) as client:
        yield client


def _signed_now() -> datetime:
    return stable_signing_time(datetime.now(UTC), 30)


async def test_presigned_put_accepts_only_the_signed_type_and_length(
    storage: S3Storage, prefix: str, http: httpx.AsyncClient
) -> None:
    key = f"{prefix}staging/upload"
    req = storage.presign_put(key, "image/jpeg", len(JPEG), 600)

    wrong_type = await http.put(req.url, content=JPEG, headers={"Content-Type": "image/png"})
    assert wrong_type.status_code == 403
    longer = await http.put(req.url, content=JPEG + b"x", headers={"Content-Type": "image/jpeg"})
    assert longer.status_code == 403
    assert await storage.head(key) is None

    ok = await http.put(req.url, content=JPEG, headers=req.headers)
    assert ok.status_code == 200
    info = await storage.head(key)
    assert info is not None
    assert (info.size, info.content_type) == (len(JPEG), "image/jpeg")
    assert await storage.get_range(key, 0, 15) == JPEG[:16]


async def test_cors_preflight_from_the_web_origin(
    storage: S3Storage, prefix: str, http: httpx.AsyncClient
) -> None:
    req = storage.presign_put(f"{prefix}staging/cors", "image/jpeg", len(JPEG), 600)
    preflight = await http.options(
        req.url,
        headers={
            "Origin": WEB_ORIGIN,
            "Access-Control-Request-Method": "PUT",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == WEB_ORIGIN
    assert "PUT" in preflight.headers["access-control-allow-methods"]
    assert "content-type" in preflight.headers["access-control-allow-headers"].lower()

    put = await http.put(req.url, content=JPEG, headers={**req.headers, "Origin": WEB_ORIGIN})
    assert put.status_code == 200
    assert put.headers["access-control-allow-origin"] == WEB_ORIGIN

    url = storage.presign_get(f"{prefix}staging/cors", signed_at=_signed_now(), expires_s=3600)
    get = await http.get(url, headers={"Origin": WEB_ORIGIN})
    assert get.status_code == 200
    assert get.headers["access-control-allow-origin"] == WEB_ORIGIN


async def test_signed_get_serves_the_object_until_it_expires(
    storage: S3Storage, prefix: str, http: httpx.AsyncClient
) -> None:
    key = f"{prefix}photos/p/display-1.webp"
    await storage.put_bytes(key, b"RIFF0000WEBPVP8 ", "image/webp", "private, max-age=3600")

    url = storage.presign_get(key, signed_at=_signed_now(), expires_s=3600)
    resp = await http.get(url)
    assert resp.status_code == 200
    assert resp.content == b"RIFF0000WEBPVP8 "
    assert resp.headers["content-type"] == "image/webp"
    assert resp.headers["cache-control"] == "private, max-age=3600"

    tampered = url.replace("X-Amz-Expires=3600", "X-Amz-Expires=7200")
    assert (await http.get(tampered)).status_code == 403
    expired = storage.presign_get(
        key, signed_at=datetime.now(UTC) - timedelta(minutes=61), expires_s=3600
    )
    assert (await http.get(expired)).status_code == 403


async def test_delete_and_move_cut_off_issued_urls_at_once(
    storage: S3Storage, prefix: str, http: httpx.AsyncClient
) -> None:
    """§4.7: removal deletes the objects or moves them to held/, so issued URLs return 404."""
    deleted = f"{prefix}photos/a/display-1.webp"
    held_src = f"{prefix}photos/b/display-1.webp"
    for key in (deleted, held_src):
        await storage.put_bytes(key, b"img", "image/webp", "private, max-age=3600")
    signed_at = _signed_now()
    deleted_url = storage.presign_get(deleted, signed_at=signed_at, expires_s=3600)
    held_url = storage.presign_get(held_src, signed_at=signed_at, expires_s=3600)
    assert (await http.get(deleted_url)).status_code == 200
    assert (await http.get(held_url)).status_code == 200

    await storage.delete(deleted)
    await storage.delete(deleted)  # idempotent
    assert (await http.get(deleted_url)).status_code == 404

    held_dst = f"held/{held_src}"
    await move(storage, held_src, held_dst)
    await move(storage, held_src, held_dst)  # a retried move is a no-op
    assert (await http.get(held_url)).status_code == 404
    admin_url = storage.presign_get(held_dst, signed_at=signed_at, expires_s=3600)
    assert (await http.get(admin_url)).content == b"img"


async def test_reads_and_listing(storage: S3Storage, prefix: str) -> None:
    key = f"{prefix}staging/blob"
    await storage.put_bytes(key, b"0123456789", "image/png", "private")
    assert await storage.get_bytes(key, 10) == b"0123456789"
    with pytest.raises(ObjectTooLarge):
        await storage.get_bytes(key, 9)
    with pytest.raises(ObjectNotFound):
        await storage.get_bytes(f"{prefix}missing", 10)
    with pytest.raises(ObjectNotFound):
        await storage.get_range(f"{prefix}missing", 0, 1)
    assert await storage.head(f"{prefix}missing") is None
    assert await storage.copy(f"{prefix}missing", f"{prefix}other") is False

    later = datetime.now(UTC) + timedelta(minutes=5)
    earlier = datetime.now(UTC) - timedelta(minutes=5)
    assert [k async for k in storage.list_older_than(f"{prefix}staging/", later)] == [key]
    assert [k async for k in storage.list_older_than(f"{prefix}staging/", earlier)] == []
