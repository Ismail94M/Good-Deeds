"""Security review 2 (SEC2) fixes on the media side: conflict of interest in the admin queue
(M1, L11), removal routes on ``CurrentUser`` (L2), pending moves on deleted photos and job
starvation (L6), staging re-created by a late PUT (L7), rate limits (L9), NUL characters in free
text (L8) and the role CLI's ``--operator`` (info)."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditLog
from app.core.storage import MemoryStorage
from app.modules.feed._projection import sync_post
from app.modules.identity import cli
from app.modules.identity.models import Role, User
from app.modules.identity.service import set_role
from app.modules.media import _cleanup, _keys, _moves, _rates
from app.modules.media.models import (
    ObjectMove,
    Photo,
    PhotoPurpose,
    PhotoReportCase,
    PhotoStatus,
    PhotoTakedown,
    PhotoType,
)
from tests.deeds.helpers import Member, make_member, scan_and_report
from tests.media.helpers import fetch, limits, ready_photo, uploaded_photo


@pytest.fixture
async def world(session: AsyncSession, storage: MemoryStorage) -> dict[str, Any]:
    reporter, doer = await make_member(session), await make_member(session)
    deed = await scan_and_report(session, reporter, doer)
    await sync_post(session, deed.id)
    await session.commit()
    photo = await ready_photo(session, storage, reporter, deed_id=deed.id)
    return {
        "reporter": reporter,
        "doer": doer,
        "deed_id": deed.id,
        "photo": photo,
        "admin": await _with_role(session, await make_member(session), Role.ADMIN),
        "stranger": await make_member(session),
    }


async def _with_role(session: AsyncSession, m: Member, role: Role) -> Member:
    user = await session.get(User, m.user_id)
    assert user is not None
    user.role = role
    await session.commit()
    return m


async def _report(client: AsyncClient, photo_id: uuid.UUID, m: Member, **kw: Any) -> Any:
    return await client.post(
        f"/api/media/photos/{photo_id}/reports",
        json={"reason": "not_consented", **kw},
        headers=m.headers,
    )


async def _case_id(session: AsyncSession, photo_id: uuid.UUID) -> uuid.UUID:
    case_id = await session.scalar(
        select(PhotoReportCase.id)
        .where(PhotoReportCase.photo_id == photo_id)
        .order_by(PhotoReportCase.opened_at.desc())
    )
    assert case_id is not None
    return case_id


async def _queue_ids(client: AsyncClient, m: Member, status: str = "open") -> tuple[set[str], int]:
    ids: set[str] = set()
    cursor = ""
    while True:
        resp = await client.get(
            f"/api/admin/photo-reports?status={status}&limit=50{cursor}", headers=m.headers
        )
        assert resp.status_code == 200, resp.text
        page = resp.json()
        ids |= {item["case_id"] for item in page["items"]}
        if page["next_cursor"] is None:
            return ids, int(page[f"{status}_count"])
        cursor = f"&cursor={page['next_cursor']}"


# --------------------------------------------------- M1 + L11: conflict of interest


@pytest.mark.parametrize("who", ["doer", "reporter", "filer"])
async def test_involved_reviewer_never_sees_the_case(
    client: AsyncClient, session: AsyncSession, world: dict[str, Any], who: str
) -> None:
    """The deed's doer, its reporter (who is also the photo's uploader) and anyone who filed a
    report in the case: the case is missing from their queue and counts, and is 404 for them."""
    photo = world["photo"]
    filer = world["stranger"]
    assert (await _report(client, photo.id, filer)).status_code == 202
    case_id = await _case_id(session, photo.id)
    involved = await _with_role(session, filer if who == "filer" else world[who], Role.MODERATOR)

    admin_ids, admin_open = await _queue_ids(client, world["admin"])
    assert str(case_id) in admin_ids
    ids, open_count = await _queue_ids(client, involved)
    assert str(case_id) not in ids
    assert open_count == admin_open - 1
    resp = await client.get(f"/api/admin/photo-reports/{case_id}", headers=involved.headers)
    assert (resp.status_code, resp.json()["code"]) == (404, "case_not_found")
    # The uninvolved admin still sees it.
    resp = await client.get(f"/api/admin/photo-reports/{case_id}", headers=world["admin"].headers)
    assert resp.status_code == 200

    # L11: and can't decide it.
    resp = await client.post(
        f"/api/admin/photo-reports/{case_id}/decision",
        json={"action": "remove", "note": "Not for me to decide"},
        headers=involved.headers,
    )
    assert (resp.status_code, resp.json()["code"]) == (403, "conflict_of_interest")


async def test_report_filer_cant_decide_restore_or_see_resolved(
    client: AsyncClient, session: AsyncSession, world: dict[str, Any]
) -> None:
    photo, admin = world["photo"], world["admin"]
    filer = await _with_role(session, world["stranger"], Role.MODERATOR)
    assert (await _report(client, photo.id, filer)).status_code == 202
    case_id = await _case_id(session, photo.id)
    resp = await client.post(
        f"/api/admin/photo-reports/{case_id}/decision",
        json={"action": "remove", "note": "I reported this"},
        headers=filer.headers,
    )
    assert (resp.status_code, resp.json()["code"]) == (403, "conflict_of_interest")
    resp = await client.post(
        f"/api/admin/photo-reports/{case_id}/decision",
        json={"action": "remove", "note": "Person asked"},
        headers=admin.headers,
    )
    assert resp.status_code == 200, resp.text
    # Resolved queue: still hidden from the filer.
    ids, _ = await _queue_ids(client, filer, "resolved")
    assert str(case_id) not in ids
    assert str(case_id) in (await _queue_ids(client, admin, "resolved"))[0]
    # A restore undoes the removal of a photo they reported: refused.
    resp = await client.post(
        f"/api/admin/photos/{photo.id}/restore",
        json={"note": "Bring it back"},
        headers=filer.headers,
    )
    assert (resp.status_code, resp.json()["code"]) == (403, "conflict_of_interest")
    assert (await fetch(session, photo.id)).takedown == PhotoTakedown.ADMIN_REMOVED


# --------------------------------------------------- L2: removal on CurrentUser


async def _not_onboarded(session: AsyncSession, m: Member) -> None:
    user = await session.get(User, m.user_id)
    assert user is not None
    user.adult_declared_at = None  # not onboarded any more
    await session.commit()


async def test_removal_routes_dont_need_onboarding(
    client: AsyncClient, session: AsyncSession, storage: MemoryStorage, world: dict[str, Any]
) -> None:
    reporter, photo = world["reporter"], world["photo"]
    upload = await uploaded_photo(session, storage, reporter)
    await _not_onboarded(session, reporter)
    # An onboarded-only route refuses them ...
    resp = await client.get(f"/api/media/deeds/{world['deed_id']}/photos", headers=reporter.headers)
    assert resp.status_code == 403
    # ... but they can still drop their upload and take their photo down.
    resp = await client.delete(f"/api/media/uploads/{upload.id}", headers=reporter.headers)
    assert resp.status_code == 204
    assert (await fetch(session, upload.id)).status == PhotoStatus.EXPIRED
    resp = await client.delete(f"/api/media/photos/{photo.id}", headers=reporter.headers)
    assert resp.status_code == 204
    assert (await fetch(session, photo.id)).status == PhotoStatus.DELETED


async def test_removal_routes_keep_party_checks(
    client: AsyncClient, session: AsyncSession, storage: MemoryStorage, world: dict[str, Any]
) -> None:
    stranger = world["stranger"]
    upload = await uploaded_photo(session, storage, world["reporter"])
    await _not_onboarded(session, stranger)
    resp = await client.delete(f"/api/media/uploads/{upload.id}", headers=stranger.headers)
    assert resp.status_code == 404
    resp = await client.delete(f"/api/media/photos/{world['photo'].id}", headers=stranger.headers)
    assert resp.status_code == 404
    assert (await fetch(session, world["photo"].id)).status == PhotoStatus.READY


# --------------------------------------------------- L6: moves


async def test_removing_a_photo_cancels_its_pending_restore(
    client: AsyncClient, session: AsyncSession, storage: MemoryStorage, world: dict[str, Any]
) -> None:
    """A restore whose move failed leaves ``release`` pending on a visible photo; when the
    uploader then deletes it, the move must never copy the variants back under ``photos/``."""
    photo = world["photo"]
    row = await fetch(session, photo.id)
    keys = _keys.variant_keys_of(row.variant_keys)
    for key in keys.values():
        await storage.copy(key, _keys.held_key(key))
        await storage.delete(key)
    row.object_move = ObjectMove.RELEASE
    await session.commit()

    resp = await client.delete(f"/api/media/photos/{photo.id}", headers=world["reporter"].headers)
    assert resp.status_code == 204
    row = await fetch(session, photo.id)
    assert (row.status, row.object_move) == (PhotoStatus.DELETED, None)
    await _moves.move_pending(session, storage)
    assert not any(k.startswith(f"photos/{photo.id}/") for k in storage.objects)


def _stuck_row(owner: Member, photo_id: uuid.UUID, status: PhotoStatus) -> Photo:
    now = datetime.now(UTC)
    return Photo(
        id=photo_id,
        uploader_user_id=owner.user_id,
        purpose=PhotoPurpose.DEED,
        status=status,
        declared_type=PhotoType.JPEG,
        declared_bytes=100,
        staging_key=_keys.staging_key(photo_id),
        variant_keys={
            "thumb": f"photos/{photo_id}/thumb-1.webp",
            "display": f"photos/{photo_id}/display-1.webp",
        },
        width=10,
        height=10,
        takedown=PhotoTakedown.ADMIN_REMOVED,
        removed_at=now,
        deleted_at=now if status == PhotoStatus.DELETED else None,
        object_move=ObjectMove.HOLD,
    )


async def test_failing_moves_dont_starve_the_rest(
    session: AsyncSession, storage: MemoryStorage
) -> None:
    """More stuck rows than one batch (sorting first by id), a healthy row after them and a
    deleted row: one run tries every live row once and skips the deleted one."""
    owner = await make_member(session)
    stuck = [uuid.UUID(int=i) for i in range(1, _moves.MOVE_BATCH + 3)]  # 22 rows
    healthy = uuid.UUID(int=10_000)
    deleted = uuid.UUID(int=10_001)
    for pid in stuck:
        session.add(_stuck_row(owner, pid, PhotoStatus.READY))
    session.add(_stuck_row(owner, healthy, PhotoStatus.READY))
    session.add(_stuck_row(owner, deleted, PhotoStatus.DELETED))
    await session.commit()
    for pid in [*stuck, healthy, deleted]:
        for name in ("thumb", "display"):
            await storage.put_bytes(f"photos/{pid}/{name}-1.webp", b"x", "image/webp", "public")

    tried: list[uuid.UUID] = []
    stuck_ids = {str(p) for p in stuck}

    class _Flaky(MemoryStorage):
        async def copy(self, src_key: str, dst_key: str) -> bool:
            pid = src_key.split("/")[1]
            if src_key.endswith("thumb-1.webp"):
                tried.append(uuid.UUID(pid))
            if pid in stuck_ids:
                raise ConnectionError("storage down")
            return await super().copy(src_key, dst_key)

    flaky = _Flaky(objects=storage.objects)
    await _moves.move_pending(session, flaky)
    ours = [p for p in tried if p in {*stuck, healthy, deleted}]
    assert sorted(ours) == sorted([*stuck, healthy])  # each live row exactly once
    assert (await fetch(session, healthy)).object_move is None
    assert f"held/{healthy}/thumb-1.webp" in storage.objects
    for p in stuck:
        assert (await fetch(session, p)).object_move == ObjectMove.HOLD
    # The deleted row is never moved (its objects are purged instead).
    assert f"held/{deleted}/thumb-1.webp" not in storage.objects
    assert await _moves.run_move(session, storage, deleted) is True
    assert f"held/{deleted}/thumb-1.webp" not in storage.objects


# --------------------------------------------------- L7: late PUTs


async def test_sweep_late_puts_deletes_staging_only_once_the_url_expired(
    session: AsyncSession, storage: MemoryStorage, world: dict[str, Any]
) -> None:
    lim = await limits(session)
    ttl = timedelta(seconds=lim.upload_url_ttl_seconds)
    reporter = world["reporter"]
    ready = await fetch(session, world["photo"].id)
    queued = await uploaded_photo(session, storage, reporter, status=PhotoStatus.QUEUED)
    # A late PUT re-created staging for the processed photo.
    await storage.put_bytes(ready.staging_key, b"raw", "image/jpeg", "private")
    created = ready.created_at

    # The URL is still valid: nothing is deleted (a later PUT could re-create it anyway).
    await _cleanup.sweep_late_puts(session, storage, lim, now=created + ttl - timedelta(minutes=1))
    assert ready.staging_key in storage.objects
    # Expired: the processed photo's staging copy goes; a queued one still needs its staging.
    await _cleanup.sweep_late_puts(session, storage, lim, now=created + ttl + timedelta(minutes=1))
    assert ready.staging_key not in storage.objects
    queued_row = await fetch(session, queued.id)
    assert queued_row.created_at <= created + timedelta(minutes=1)
    assert queued.staging_key in storage.objects


# --------------------------------------------------- L9: rate limits (code constants)


@pytest.mark.parametrize(
    ("constant", "method", "path"),
    [
        ("RATE_UPLOAD_COMPLETE_USER", "POST", "/api/media/uploads/{upload}/complete"),
        ("RATE_UPLOAD_DISCARD_USER", "DELETE", "/api/media/uploads/{upload}"),
        ("RATE_OWNER_READ_USER", "GET", "/api/media/deeds/{deed}/photos"),
        ("RATE_ADMIN_READ_USER", "GET", "/api/admin/photo-reports"),
    ],
)
async def test_media_routes_are_rate_limited(
    client: AsyncClient,
    session: AsyncSession,
    storage: MemoryStorage,
    world: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    constant: str,
    method: str,
    path: str,
) -> None:
    monkeypatch.setattr(_rates, constant, "1/3600")
    upload = await uploaded_photo(session, storage, world["reporter"])
    url = path.format(upload=upload.id, deed=world["deed_id"])
    who = world["admin"] if "admin" in path else world["reporter"]
    first = await client.request(method, url, headers=who.headers)
    assert first.status_code != 429
    second = await client.request(method, url, headers=who.headers)
    assert (second.status_code, second.json()["code"]) == (429, "rate_limited")


async def test_admin_detail_is_rate_limited(
    client: AsyncClient,
    session: AsyncSession,
    world: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_rates, "RATE_ADMIN_READ_USER", "1/3600")
    url = f"/api/admin/photo-reports/{uuid.uuid4()}"
    assert (await client.get(url, headers=world["admin"].headers)).status_code == 404
    resp = await client.get(url, headers=world["admin"].headers)
    assert (resp.status_code, resp.json()["code"]) == (429, "rate_limited")


# --------------------------------------------------- L8: NUL in free text


async def test_nul_in_report_details_and_notes_is_422(
    client: AsyncClient, session: AsyncSession, world: dict[str, Any]
) -> None:
    photo, admin = world["photo"], world["admin"]
    resp = await _report(client, photo.id, world["stranger"], details="bad\x00text")
    assert (resp.status_code, resp.json()["code"]) == (422, "invalid_text")
    assert (await _report(client, photo.id, world["stranger"])).status_code == 202
    case_id = await _case_id(session, photo.id)
    resp = await client.post(
        f"/api/admin/photo-reports/{case_id}/decision",
        json={"action": "remove", "note": "Person\x00asked"},
        headers=admin.headers,
    )
    assert (resp.status_code, resp.json()["code"]) == (422, "invalid_text")
    resp = await client.post(
        f"/api/admin/photo-reports/{case_id}/decision",
        json={"action": "remove", "note": "Person asked"},
        headers=admin.headers,
    )
    assert resp.status_code == 200
    resp = await client.post(
        f"/api/admin/photos/{photo.id}/restore",
        json={"note": "Consent\x00confirmed"},
        headers=admin.headers,
    )
    assert (resp.status_code, resp.json()["code"]) == (422, "invalid_text")


# --------------------------------------------------- role CLI --operator


async def test_operator_lands_in_the_audit_row(session: AsyncSession) -> None:
    m = await make_member(session)
    user = await session.get(User, m.user_id)
    assert user is not None
    assert await set_role(
        session, user, Role.MODERATOR, reason="Trusted reviewer", operator="  Asha Ops  "
    )
    await session.commit()
    row = await session.scalar(
        select(AuditLog).where(
            AuditLog.entity_id == str(m.user_id), AuditLog.action == "role.changed"
        )
    )
    assert row is not None
    assert row.actor_id is None
    assert row.new_value == {"role": "moderator", "operator": "Asha Ops"}
    assert row.reason == "Trusted reviewer"


def test_cli_requires_an_operator(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, Role, str, str]] = []

    async def fake(handle: str, role: Role, reason: str, operator: str) -> int:
        calls.append((handle, role, reason, operator))
        return 0

    monkeypatch.setattr(cli, "_set_role", fake)
    base = ["set-role", "@someone", "moderator", "--reason", "Trusted"]
    with pytest.raises(SystemExit):
        cli.main(base)  # --operator is required
    with pytest.raises(SystemExit):
        cli.main([*base, "--operator", "   "])
    assert calls == []
    assert cli.main([*base, "--operator", " Asha Ops "]) == 0
    assert calls == [("someone", Role.MODERATOR, "Trusted", "Asha Ops")]
