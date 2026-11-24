"""app.core.storage without a network: the stable SigV4 signer (checked against botocore's own
output), presigned PUT headers, S3 error mapping (GCS-style 404s) and MemoryStorage."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qs, urlsplit

import boto3
import botocore.auth
import pytest
from botocore.config import Config
from botocore.stub import Stubber

from app.core.storage import (
    AddressingStyle,
    MemoryStorage,
    ObjectNotFound,
    ObjectTooLarge,
    S3Storage,
    move,
    stable_signing_time,
)

ACCESS_KEY = "GOOG1EXAMPLEACCESSKEY"
SECRET_KEY = "example-secret-key-for-signing-tests"
BUCKET = "gooddeeds-media"
FROZEN = datetime(2026, 11, 19, 10, 30, tzinfo=UTC)


def _storage(**overrides: Any) -> S3Storage:
    values: dict[str, Any] = {
        "endpoint_url": "http://storage:8333",
        "public_endpoint_url": "http://localhost:8333",
        "region": "us-east-1",
        "bucket": BUCKET,
        "access_key_id": ACCESS_KEY,
        "secret_access_key": SECRET_KEY,
        "addressing_style": "path",
    }
    values.update(overrides)
    return S3Storage(**values)


def _query(url: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


@pytest.fixture
def frozen_botocore(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """botocore's signers read the clock through botocore.auth.get_current_datetime."""
    monkeypatch.setattr(botocore.auth, "get_current_datetime", lambda: FROZEN.replace(tzinfo=None))
    yield


# --- stable_signing_time ---


@pytest.mark.parametrize(
    ("now", "step", "expected"),
    [
        (datetime(2026, 11, 19, 10, 29, 59, 999999, tzinfo=UTC), 30, (10, 0)),
        (datetime(2026, 11, 19, 10, 30, tzinfo=UTC), 30, (10, 30)),
        (datetime(2026, 11, 19, 10, 59, 59, tzinfo=UTC), 30, (10, 30)),
        (datetime(2026, 11, 19, 0, 6, tzinfo=UTC), 7, (0, 0)),
        (datetime(2026, 11, 19, 0, 7, tzinfo=UTC), 7, (0, 7)),
    ],
)
def test_stable_signing_time_floors_to_step(
    now: datetime, step: int, expected: tuple[int, int]
) -> None:
    floored = stable_signing_time(now, step)
    assert (floored.hour, floored.minute, floored.second, floored.microsecond) == (*expected, 0, 0)
    assert floored.tzinfo == UTC


def test_stable_signing_time_converts_to_utc() -> None:
    ist = timezone(timedelta(hours=5, minutes=30))
    # 16:15 IST = 10:45 UTC -> 10:30 UTC
    assert stable_signing_time(datetime(2026, 11, 19, 16, 15, tzinfo=ist), 30) == FROZEN


def test_stable_signing_time_rejects_bad_step() -> None:
    with pytest.raises(ValueError):
        stable_signing_time(FROZEN, 0)


# --- presign_get matches botocore ---


@pytest.mark.parametrize(
    ("endpoint", "region", "style"),
    [
        ("http://localhost:8333", "us-east-1", "path"),
        ("https://storage.googleapis.com", "auto", "path"),
        ("https://storage.googleapis.com", "auto", "virtual"),
    ],
)
@pytest.mark.parametrize(
    "key",
    ["photos/0b9d/display-1.webp", "held/a b/thumb-2.webp", "staging/ünï+code~x=y&z"],
)
def test_presign_get_matches_botocore_for_the_same_time(
    frozen_botocore: None, endpoint: str, region: str, style: AddressingStyle, key: str
) -> None:
    reference = boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=region,
        aws_access_key_id=ACCESS_KEY,
        aws_secret_access_key=SECRET_KEY,
        config=Config(signature_version="s3v4", s3={"addressing_style": style}),
    )
    expected = reference.generate_presigned_url(
        "get_object", Params={"Bucket": BUCKET, "Key": key}, ExpiresIn=3600
    )
    storage = _storage(public_endpoint_url=endpoint, region=region, addressing_style=style)

    assert storage.presign_get(key, signed_at=FROZEN, expires_s=3600) == expected


def test_presign_get_is_stable_within_a_step_and_changes_after() -> None:
    storage = _storage()
    key = "photos/x/display-1.webp"

    def url_at(now: datetime) -> str:
        return storage.presign_get(key, signed_at=stable_signing_time(now, 30), expires_s=3600)

    first = url_at(datetime(2026, 11, 19, 10, 30, 1, tzinfo=UTC))
    assert first == url_at(datetime(2026, 11, 19, 10, 59, 59, tzinfo=UTC))
    assert first != url_at(datetime(2026, 11, 19, 11, 0, tzinfo=UTC))
    query = _query(first)
    assert query["X-Amz-Date"] == "20261119T103000Z"
    assert query["X-Amz-Expires"] == "3600"
    assert query["X-Amz-SignedHeaders"] == "host"


def test_presign_get_uses_the_public_endpoint() -> None:
    url = _storage().presign_get("photos/x", signed_at=FROZEN, expires_s=60)
    assert url.startswith(f"http://localhost:8333/{BUCKET}/photos/x?")


@pytest.mark.parametrize("expires", [0, -1, 7 * 24 * 3600 + 1])
def test_presign_rejects_bad_expiry(expires: int) -> None:
    storage = _storage()
    with pytest.raises(ValueError):
        storage.presign_get("k", signed_at=FROZEN, expires_s=expires)
    with pytest.raises(ValueError):
        storage.presign_put("k", "image/jpeg", 10, expires)


# --- presign_put ---


def test_presign_put_signs_content_type_and_length() -> None:
    req = _storage().presign_put("staging/abc", "image/jpeg", 12345, 600)
    assert req.method == "PUT"
    assert req.headers == {"Content-Type": "image/jpeg", "Content-Length": "12345"}
    query = _query(req.url)
    assert query["X-Amz-SignedHeaders"].split(";") == ["content-length", "content-type", "host"]
    assert query["X-Amz-Expires"] == "600"
    assert req.url.startswith(f"http://localhost:8333/{BUCKET}/staging/abc?")


# --- S3 error mapping (GCS answers 404 where S3 answers 204) ---


def _stubbed() -> tuple[S3Storage, Stubber]:
    storage = _storage()
    stubber = Stubber(storage._client)
    stubber.activate()
    return storage, stubber


async def test_delete_of_missing_key_is_not_an_error() -> None:
    storage, stubber = _stubbed()
    stubber.add_client_error("delete_object", "NoSuchKey", http_status_code=404)
    await storage.delete("photos/gone")
    stubber.assert_no_pending_responses()


async def test_delete_raises_other_errors() -> None:
    storage, stubber = _stubbed()
    stubber.add_client_error("delete_object", "AccessDenied", http_status_code=403)
    with pytest.raises(Exception, match="AccessDenied"):
        await storage.delete("photos/x")


async def test_head_and_copy_of_missing_key() -> None:
    storage, stubber = _stubbed()
    stubber.add_client_error("head_object", "404", http_status_code=404)
    stubber.add_client_error("copy_object", "NoSuchKey", http_status_code=404)
    assert await storage.head("photos/gone") is None
    assert await storage.copy("photos/gone", "held/gone") is False


# --- MemoryStorage (the test double other modules use) ---


async def test_memory_storage_round_trip() -> None:
    storage = MemoryStorage()
    await storage.put_bytes("staging/a", b"\xff\xd8\xffdata", "image/jpeg", "private")
    info = await storage.head("staging/a")
    assert info is not None
    assert (info.size, info.content_type) == (7, "image/jpeg")
    assert await storage.get_range("staging/a", 0, 2) == b"\xff\xd8\xff"
    assert await storage.get_bytes("staging/a", 7) == b"\xff\xd8\xffdata"
    with pytest.raises(ObjectTooLarge):
        await storage.get_bytes("staging/a", 6)
    with pytest.raises(ObjectNotFound):
        await storage.get_bytes("staging/missing", 10)
    assert await storage.head("staging/missing") is None
    await storage.delete("staging/a")
    await storage.delete("staging/a")
    assert storage.objects == {}


async def test_memory_storage_presign_urls() -> None:
    storage = MemoryStorage()
    put = storage.presign_put("staging/a", "image/png", 5, 600)
    assert put.url.startswith("memory://")
    assert put.headers == {"Content-Type": "image/png", "Content-Length": "5"}
    url = storage.presign_get("photos/a", signed_at=FROZEN, expires_s=3600)
    assert url == storage.presign_get("photos/a", signed_at=FROZEN, expires_s=3600)
    assert "20261119T103000Z" in url


async def test_move_is_idempotent() -> None:
    storage = MemoryStorage()
    await storage.put_bytes("photos/p/display-1.webp", b"img", "image/webp", "private")
    await move(storage, "photos/p/display-1.webp", "held/p/display-1.webp")
    assert set(storage.objects) == {"held/p/display-1.webp"}
    # A retry after the move finished is a no-op.
    await move(storage, "photos/p/display-1.webp", "held/p/display-1.webp")
    assert set(storage.objects) == {"held/p/display-1.webp"}
    with pytest.raises(ObjectNotFound):
        await move(storage, "photos/none", "held/none")


async def test_memory_storage_list_older_than() -> None:
    storage = MemoryStorage()
    await storage.put_bytes("staging/old", b"1", "image/jpeg", "private")
    await storage.put_bytes("photos/old", b"1", "image/webp", "private")
    storage.objects["staging/old"].last_modified = datetime.now(UTC) - timedelta(days=3)
    await storage.put_bytes("staging/new", b"1", "image/jpeg", "private")
    cutoff = datetime.now(UTC) - timedelta(hours=48)
    assert [k async for k in storage.list_older_than("staging/", cutoff)] == ["staging/old"]
