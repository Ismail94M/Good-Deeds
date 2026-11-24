"""Object storage (photos) behind a provider-neutral interface, S3-compatible API only.

Production: Google Cloud Storage through its S3-interoperable XML API (HMAC keys, SigV4).
Development: SeaweedFS in docker-compose. Tests: ``MemoryStorage``. Switching provider is a
settings change (``GD_STORAGE_*``). Design: docs/M3-DESIGN.md §4.2, D34, D35.

GCS notes, kept in ``S3Storage``:
- no multi-object delete (``DeleteObjects``): ``delete`` is one request per key;
- recent boto3 adds CRC checksum headers by default, which GCS rejects: checksums are only
  sent "when_required";
- bucket CORS and lifecycle rules are infrastructure (``gcloud``), never set from the app.

Signed GET URLs are *stable*: the caller passes a signing time floored to a step
(``stable_signing_time``), so the same object keeps the same URL for the whole step and
browsers can cache it. Removing access at once means deleting or moving the object (the
URL then returns 404); an issued URL can't be revoked any other way.
"""

import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import TYPE_CHECKING, Any, Literal, Protocol
from urllib.parse import urlsplit, urlunsplit

import boto3
from botocore.auth import SIGV4_TIMESTAMP, S3SigV4QueryAuth
from botocore.awsrequest import AWSRequest
from botocore.config import Config
from botocore.credentials import Credentials
from botocore.exceptions import ClientError

from app.core.config import Settings, get_settings

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

# Error codes that mean "no such object" (S3, SeaweedFS and GCS spell it differently).
_NOT_FOUND_CODES = frozenset({"404", "NoSuchKey", "NotFound"})
# SigV4 presigned URLs can't be valid for longer than 7 days.
_MAX_EXPIRES_S = 7 * 24 * 3600
_LIST_PAGE_SIZE = 1000

type AddressingStyle = Literal["path", "virtual"]


class StorageError(Exception):
    """Base class for storage failures the caller may want to handle."""


class ObjectNotFound(StorageError):
    pass


class ObjectTooLarge(StorageError):
    pass


@dataclass(frozen=True)
class PresignedRequest:
    """What the browser needs for a direct upload: send exactly these headers."""

    url: str
    method: str
    headers: dict[str, str]


@dataclass(frozen=True)
class ObjectInfo:
    size: int
    content_type: str | None


class ObjectStorage(Protocol):
    def presign_put(
        self, key: str, content_type: str, content_length: int, expires_s: int
    ) -> PresignedRequest:
        """Signed PUT; Content-Type and Content-Length are signed headers."""
        ...

    def presign_get(self, key: str, *, signed_at: datetime, expires_s: int) -> str:
        """Signed GET dated ``signed_at`` (see ``stable_signing_time``)."""
        ...

    async def head(self, key: str) -> ObjectInfo | None: ...

    async def get_range(self, key: str, start: int, end: int) -> bytes:
        """Bytes ``start``..``end`` inclusive (HTTP Range semantics)."""
        ...

    async def get_bytes(self, key: str, max_bytes: int) -> bytes:
        """The whole object; raises ``ObjectTooLarge`` above ``max_bytes``."""
        ...

    async def put_bytes(
        self, key: str, data: bytes, content_type: str, cache_control: str
    ) -> None: ...

    async def copy(self, src_key: str, dst_key: str) -> bool:
        """Server-side copy. False when the source doesn't exist (lets a move be retried)."""
        ...

    async def delete(self, key: str) -> None:
        """Delete one key; deleting a missing key is not an error."""
        ...

    def list_older_than(self, prefix: str, before: datetime) -> AsyncIterator[str]:
        """Keys under ``prefix`` last modified before ``before``."""
        ...


def stable_signing_time(now: datetime, step_minutes: int) -> datetime:
    """``now`` floored to a multiple of ``step_minutes`` since midnight UTC (D34)."""
    if step_minutes <= 0:
        raise ValueError("step_minutes must be positive")
    now = now.astimezone(UTC)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    step = timedelta(minutes=step_minutes)
    return midnight + ((now - midnight) // step) * step


async def move(storage: ObjectStorage, src_key: str, dst_key: str) -> None:
    """Copy then delete, safe to retry: when the source is already gone, the destination must
    exist (an earlier attempt finished the copy). Used for admin holds (``held/``, §4.7)."""
    if not await storage.copy(src_key, dst_key):
        if await storage.head(dst_key) is None:
            raise ObjectNotFound(src_key)
        return
    await storage.delete(src_key)


class _StableS3SigV4QueryAuth(S3SigV4QueryAuth):
    """botocore's presigned-URL signer with the signing time given instead of "now"."""

    def __init__(
        self,
        credentials: Credentials,
        service_name: str,
        region_name: str,
        expires: int,
        signed_at: datetime,
    ) -> None:
        super().__init__(credentials, service_name, region_name, expires=expires)
        self._signed_at = signed_at.astimezone(UTC)

    def add_auth(self, request: AWSRequest) -> None:
        # Same steps as SigV4Auth.add_auth, with the timestamp injected.
        request.context["timestamp"] = self._signed_at.strftime(SIGV4_TIMESTAMP)
        # (These two botocore methods are private, so they are missing from the type stubs.)
        self._modify_request_before_signing(request)  # type: ignore[attr-defined]
        canonical_request = self.canonical_request(request)
        string_to_sign = self.string_to_sign(request, canonical_request)
        signature = self.signature(string_to_sign, request)
        self._inject_signature_to_request(request, signature)  # type: ignore[attr-defined]


def _client_config(addressing_style: AddressingStyle) -> Config:
    return Config(
        signature_version="s3v4",
        s3={"addressing_style": addressing_style},
        # Required for GCS: don't add the CRC checksum headers recent boto3 sends by default.
        request_checksum_calculation="when_required",
        response_checksum_validation="when_required",
        retries={"max_attempts": 3, "mode": "standard"},
    )


def _is_not_found(exc: ClientError) -> bool:
    error = exc.response.get("Error", {})
    status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    return error.get("Code") in _NOT_FOUND_CODES or status == 404


class S3Storage:
    """boto3 implementation for GCS (XML API) and SeaweedFS. Network calls run in threads.

    Two clients: ``_client`` talks to the internal endpoint; ``_signer`` is only used offline
    to sign URLs for the public endpoint (in docker the API reaches ``storage:8333`` while
    browsers use ``127.0.0.1:8333``).
    """

    def __init__(
        self,
        *,
        endpoint_url: str,
        public_endpoint_url: str,
        region: str,
        bucket: str,
        access_key_id: str,
        secret_access_key: str,
        addressing_style: AddressingStyle = "path",
    ) -> None:
        self.bucket = bucket
        self._region = region
        self._credentials = Credentials(access_key_id, secret_access_key)
        session = boto3.session.Session(
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name=region,
        )
        config = _client_config(addressing_style)
        self._client: S3Client = session.client("s3", endpoint_url=endpoint_url, config=config)
        self._signer: S3Client = session.client(
            "s3", endpoint_url=public_endpoint_url, config=config
        )

    @classmethod
    def from_settings(cls, settings: Settings) -> "S3Storage":
        return cls(
            endpoint_url=settings.storage_endpoint_url,
            public_endpoint_url=settings.storage_signing_endpoint_url,
            region=settings.storage_region,
            bucket=settings.storage_bucket,
            access_key_id=settings.storage_access_key_id,
            secret_access_key=settings.storage_secret_access_key,
            addressing_style=settings.storage_addressing_style,
        )

    # --- signing (offline) ---

    def presign_put(
        self, key: str, content_type: str, content_length: int, expires_s: int
    ) -> PresignedRequest:
        url = self._signer.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": self.bucket,
                "Key": key,
                "ContentType": content_type,
                "ContentLength": content_length,
            },
            ExpiresIn=_check_expires(expires_s),
        )
        headers = {"Content-Type": content_type, "Content-Length": str(content_length)}
        return PresignedRequest(url=url, method="PUT", headers=headers)

    def presign_get(self, key: str, *, signed_at: datetime, expires_s: int) -> str:
        # botocore builds the object URL (addressing style, key encoding); we drop its
        # signature and sign that URL again with the given time.
        unsigned = urlsplit(
            self._signer.generate_presigned_url(
                "get_object", Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=1
            )
        )
        query = "&".join(
            part for part in unsigned.query.split("&") if part and not part.startswith("X-Amz-")
        )
        request = AWSRequest(
            method="GET", url=urlunsplit((*unsigned[:3], query, "")), headers={}, data=b""
        )
        signer = _StableS3SigV4QueryAuth(
            self._credentials, "s3", self._region, _check_expires(expires_s), signed_at
        )
        signer.add_auth(request)
        return str(request.url)

    # --- network calls ---

    async def head(self, key: str) -> ObjectInfo | None:
        try:
            resp = await asyncio.to_thread(self._client.head_object, Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if _is_not_found(exc):
                return None
            raise
        return ObjectInfo(size=resp["ContentLength"], content_type=resp.get("ContentType"))

    async def get_range(self, key: str, start: int, end: int) -> bytes:
        if start < 0 or end < start:
            raise ValueError("invalid range")

        def _read() -> bytes:
            resp = self._client.get_object(
                Bucket=self.bucket, Key=key, Range=f"bytes={start}-{end}"
            )
            with resp["Body"] as body:
                return body.read(end - start + 1)

        return await self._call(key, _read)

    async def get_bytes(self, key: str, max_bytes: int) -> bytes:
        def _read() -> bytes:
            resp = self._client.get_object(Bucket=self.bucket, Key=key)
            with resp["Body"] as body:
                if resp["ContentLength"] > max_bytes:
                    raise ObjectTooLarge(key)
                data = body.read(max_bytes + 1)
            if len(data) > max_bytes:
                raise ObjectTooLarge(key)
            return data

        return await self._call(key, _read)

    async def put_bytes(self, key: str, data: bytes, content_type: str, cache_control: str) -> None:
        await asyncio.to_thread(
            self._client.put_object,
            Bucket=self.bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
            CacheControl=cache_control,
        )

    async def copy(self, src_key: str, dst_key: str) -> bool:
        try:
            await asyncio.to_thread(
                self._client.copy_object,
                Bucket=self.bucket,
                Key=dst_key,
                CopySource={"Bucket": self.bucket, "Key": src_key},
                MetadataDirective="COPY",
            )
        except ClientError as exc:
            if _is_not_found(exc):
                return False
            raise
        return True

    async def delete(self, key: str) -> None:
        try:
            await asyncio.to_thread(self._client.delete_object, Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if not _is_not_found(exc):  # GCS answers 404 for a missing key; S3 answers 204
                raise

    async def list_older_than(self, prefix: str, before: datetime) -> AsyncIterator[str]:
        token: str | None = None
        while True:
            kwargs: dict[str, Any] = {
                "Bucket": self.bucket,
                "Prefix": prefix,
                "MaxKeys": _LIST_PAGE_SIZE,
            }
            if token:
                kwargs["ContinuationToken"] = token
            page = await asyncio.to_thread(self._client.list_objects_v2, **kwargs)
            for obj in page.get("Contents", []):
                if obj["LastModified"] < before:
                    yield obj["Key"]
            if not page.get("IsTruncated"):
                return
            token = page.get("NextContinuationToken")

    async def _call[T](self, key: str, fn: Callable[[], T]) -> T:
        try:
            return await asyncio.to_thread(fn)
        except ClientError as exc:
            if _is_not_found(exc):
                raise ObjectNotFound(key) from exc
            raise


def _check_expires(expires_s: int) -> int:
    if not 0 < expires_s <= _MAX_EXPIRES_S:
        raise ValueError("expires_s must be between 1 second and 7 days")
    return expires_s


@dataclass
class _MemoryObject:
    data: bytes
    content_type: str
    cache_control: str
    last_modified: datetime


@dataclass
class MemoryStorage:
    """In-memory storage for tests. Presigned URLs are ``memory://`` strings that encode the
    key, method and signing time, so tests can assert on them; tests "upload" with
    ``put_bytes``."""

    bucket: str = "test-media"
    objects: dict[str, _MemoryObject] = field(default_factory=dict)

    def presign_put(
        self, key: str, content_type: str, content_length: int, expires_s: int
    ) -> PresignedRequest:
        _check_expires(expires_s)
        return PresignedRequest(
            url=f"memory://{self.bucket}/{key}?method=PUT&expires={expires_s}",
            method="PUT",
            headers={"Content-Type": content_type, "Content-Length": str(content_length)},
        )

    def presign_get(self, key: str, *, signed_at: datetime, expires_s: int) -> str:
        _check_expires(expires_s)
        stamp = signed_at.astimezone(UTC).strftime(SIGV4_TIMESTAMP)
        return f"memory://{self.bucket}/{key}?method=GET&date={stamp}&expires={expires_s}"

    async def head(self, key: str) -> ObjectInfo | None:
        obj = self.objects.get(key)
        return None if obj is None else ObjectInfo(len(obj.data), obj.content_type)

    async def get_range(self, key: str, start: int, end: int) -> bytes:
        if start < 0 or end < start:
            raise ValueError("invalid range")
        return self._get(key).data[start : end + 1]

    async def get_bytes(self, key: str, max_bytes: int) -> bytes:
        data = self._get(key).data
        if len(data) > max_bytes:
            raise ObjectTooLarge(key)
        return data

    async def put_bytes(self, key: str, data: bytes, content_type: str, cache_control: str) -> None:
        self.objects[key] = _MemoryObject(data, content_type, cache_control, datetime.now(UTC))

    async def copy(self, src_key: str, dst_key: str) -> bool:
        obj = self.objects.get(src_key)
        if obj is None:
            return False
        self.objects[dst_key] = _MemoryObject(
            obj.data, obj.content_type, obj.cache_control, datetime.now(UTC)
        )
        return True

    async def delete(self, key: str) -> None:
        self.objects.pop(key, None)

    async def list_older_than(self, prefix: str, before: datetime) -> AsyncIterator[str]:
        for key, obj in sorted(self.objects.items()):
            if key.startswith(prefix) and obj.last_modified < before:
                yield key

    def _get(self, key: str) -> _MemoryObject:
        obj = self.objects.get(key)
        if obj is None:
            raise ObjectNotFound(key)
        return obj


@lru_cache
def _default_storage() -> S3Storage:
    return S3Storage.from_settings(get_settings())


def get_storage() -> ObjectStorage:
    """FastAPI dependency (and the worker's accessor). Tests override it with MemoryStorage."""
    return _default_storage()
