"""QA6 (M3) against the real S3-compatible store (SeaweedFS locally): a rotated JPEG carrying GPS,
camera and time EXIF, XMP, ICC, a comment and IPTC goes through presigned PUT, complete, attach
with a blur region and the worker. What the store then serves, and what the bucket holds, must
be clean, upright and blurred (docs/M3-DESIGN.md §8 "Pipeline privacy", Q31).

Runs only when GD_TEST_STORAGE_URL is set."""

import io
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from httpx import AsyncClient
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.storage import S3Storage
from app.modules.media import _worker, service
from tests.deeds.helpers import make_member, scan_and_report
from tests.media import images
from tests.media.helpers import fetch
from tests.media.test_storage_flow_integration import (
    STORAGE_URL,
    _cleanup,
    _upload,
    s3,  # noqa: F401  (fixture)
)

pytestmark = pytest.mark.skipif(not STORAGE_URL, reason="GD_TEST_STORAGE_URL is not set")


async def test_served_variants_are_clean_upright_and_blurred(
    client: AsyncClient,
    session: AsyncSession,
    s3: S3Storage,  # noqa: F811
) -> None:
    reporter, doer = await make_member(session), await make_member(session)
    deed = await scan_and_report(session, reporter, doer)
    await session.commit()
    # A smooth gradient, with sharp noise ("a face") only where the blur region goes.
    upright = Image.linear_gradient("L").resize((1200, 800)).convert("RGB")
    upright.paste(images.noise((600, 400), seed=5), (300, 200))
    stored = upright.transpose(Image.Transpose.ROTATE_90)  # EXIF orientation 6 undoes this
    data = images.jpeg_with_metadata(stored, orientation=6)
    photo_id = await _upload(client, s3, reporter, data)
    try:
        region = {"x": 0.25, "y": 0.25, "w": 0.5, "h": 0.5}  # in the upright frame
        resp = await client.post(
            f"/api/media/deeds/{deed.id}/photos",
            json={"photos": [{"photo_id": str(photo_id), "blur_regions": [region]}]},
            headers=reporter.headers,
        )
        assert resp.status_code == 200, resp.text
        while await _worker.process_batch(session, s3) == _worker.CLAIM_BATCH:
            pass
        row = await fetch(session, photo_id)
        assert row.variant_keys is not None, row.reject_reason
        assert (row.width, row.height) == (1200, 800)

        # The bucket: only the blurred variants, no staging, original or held copy (Q31).
        future = datetime.now(UTC) + timedelta(days=1)
        keys = []
        for prefix in (f"staging/{photo_id}", f"photos/{photo_id}/", f"held/{photo_id}/"):
            keys += [k async for k in s3.list_older_than(prefix, future)]
        assert sorted(keys) == sorted(row.variant_keys.values())

        [photo] = (await service.public_photos(session, [deed.id], storage=s3))[deed.id]
        async with httpx.AsyncClient() as http:
            for url in (photo.thumb_url, photo.display_url):
                got = await http.get(url)
                assert got.status_code == 200
                for marker in images.FORBIDDEN_MARKERS:
                    assert marker not in got.content, marker
                img = Image.open(io.BytesIO(got.content))
                img.load()
                assert img.width > img.height  # upright landscape
                assert not {"exif", "xmp", "icc_profile", "comment"} & set(img.info)
                assert len(img.getexif()) == 0
            display = Image.open(io.BytesIO((await http.get(photo.display_url)).content))
        expected = upright.resize(display.size, Image.Resampling.LANCZOS)
        w, h = display.size
        inside = (int(w * 0.35), int(h * 0.35), int(w * 0.65), int(h * 0.65))
        outside = (0, 0, int(w * 0.15), int(h * 0.15))
        assert images.mean_abs_diff(display.crop(inside), expected.crop(inside)) > 30
        assert (
            images.laplacian_variance(display.crop(inside))
            < images.laplacian_variance(expected.crop(inside)) / 10
        )
        # Outside the region the (lossy) variant still matches the source closely.
        assert images.mean_abs_diff(display.crop(outside), expected.crop(outside)) < 30
    finally:
        await _cleanup(s3, [photo_id])
