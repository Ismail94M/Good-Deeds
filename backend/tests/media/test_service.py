"""``media.service``: public photos and stable signed URLs (D34, §4.7, Q33)."""

from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.storage import MemoryStorage
from app.modules.media import service
from app.modules.media.models import ObjectMove, PhotoStatus, PhotoTakedown
from tests.deeds.helpers import make_member, scan_and_report
from tests.media.helpers import fetch, publish_media, queued_photo, ready_photo, self_log

T0 = datetime(2026, 11, 19, 10, 7, tzinfo=UTC)


def _date(url: str) -> str:
    return parse_qs(urlsplit(url).query)["date"][0]


async def test_public_photos_only_ready_live_deed_photos_in_order(
    session: AsyncSession, storage: MemoryStorage
) -> None:
    reporter, doer = await make_member(session), await make_member(session)
    deed = await scan_and_report(session, reporter, doer)
    other = await scan_and_report(session, await make_member(session), doer)
    await session.commit()

    second = await ready_photo(session, storage, reporter, deed_id=deed.id, position=2)
    first = await ready_photo(session, storage, reporter, deed_id=deed.id, position=1)
    queued = await queued_photo(session, storage, reporter, deed_id=deed.id, position=3)
    removed = await ready_photo(session, storage, reporter, deed_id=deed.id, position=4)
    moving = await ready_photo(session, storage, reporter, deed_id=deed.id, position=5)
    deleted = await ready_photo(session, storage, reporter, deed_id=deed.id, position=6)
    row = await fetch(session, removed.id)
    row.takedown, row.removed_at = PhotoTakedown.ADMIN_REMOVED, T0
    (await fetch(session, moving.id)).object_move = ObjectMove.RELEASE
    row = await fetch(session, deleted.id)
    row.status, row.deleted_at = PhotoStatus.DELETED, T0
    owner = await make_member(session)
    await ready_photo(session, storage, owner, self_log_id=await self_log(session, owner))
    await session.commit()
    assert (await fetch(session, queued.id)).status == PhotoStatus.READY  # processed too
    row = await fetch(session, queued.id)
    row.status = PhotoStatus.PROCESSING
    await session.commit()

    result = await service.public_photos(
        session, [deed.id, other.id, deed.id], storage=storage, now=T0
    )
    assert list(result) == [deed.id, other.id]
    assert result[other.id] == []
    photos = result[deed.id]
    assert [p.id for p in photos] == [first.id, second.id]
    out = photos[0]
    assert (out.width, out.height) == (400, 300)
    assert f"photos/{first.id}/thumb-1.webp" in out.thumb_url
    assert f"photos/{first.id}/display-1.webp" in out.display_url
    assert await service.public_photos(session, [], storage=storage) == {}


async def test_urls_are_stable_within_a_step_and_change_after(
    session: AsyncSession, storage: MemoryStorage
) -> None:
    reporter, doer = await make_member(session), await make_member(session)
    deed = await scan_and_report(session, reporter, doer)
    await session.commit()
    await ready_photo(session, storage, reporter, deed_id=deed.id)

    a = (await service.public_photos(session, [deed.id], storage=storage, now=T0))[deed.id][0]
    b = (
        await service.public_photos(
            session, [deed.id], storage=storage, now=T0 + timedelta(minutes=20)
        )
    )[deed.id][0]
    c = (
        await service.public_photos(
            session, [deed.id], storage=storage, now=T0 + timedelta(minutes=24)
        )
    )[deed.id][0]
    assert a.display_url == b.display_url and a.thumb_url == b.thumb_url
    assert c.display_url != a.display_url
    assert _date(a.display_url) == "20261119T100000Z"
    assert _date(c.display_url) == "20261119T103000Z"
    assert "expires=3600" in a.display_url
    assert a.urls_expire_at == datetime(2026, 11, 19, 11, 0, tzinfo=UTC)

    # Lifetime and step come from config.
    await publish_media(session, url_ttl_minutes=20, url_step_minutes=10)
    d = (await service.public_photos(session, [deed.id], storage=storage, now=T0))[deed.id][0]
    assert _date(d.display_url) == "20261119T100000Z"
    assert "expires=1200" in d.display_url
    assert d.urls_expire_at == datetime(2026, 11, 19, 10, 20, tzinfo=UTC)


async def test_admin_urls_sign_held_keys_after_a_removal(
    session: AsyncSession, storage: MemoryStorage
) -> None:
    reporter, doer = await make_member(session), await make_member(session)
    deed = await scan_and_report(session, reporter, doer)
    await session.commit()
    photo = await ready_photo(session, storage, reporter, deed_id=deed.id)

    live = await service.admin_photo_urls(session, photo.id, storage=storage, now=T0)
    assert live is not None and f"/photos/{photo.id}/display-1.webp" in live.display_url

    row = await fetch(session, photo.id)
    row.takedown, row.removed_at, row.object_move = PhotoTakedown.ADMIN_REMOVED, T0, None
    await session.commit()
    held = await service.admin_photo_urls(session, photo.id, storage=storage, now=T0)
    assert held is not None and f"/held/{photo.id}/display-1.webp" in held.display_url

    row.objects_purged_at = T0
    await session.commit()
    assert await service.admin_photo_urls(session, photo.id, storage=storage) is None
