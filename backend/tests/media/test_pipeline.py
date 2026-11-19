"""The pure photo pipeline with generated fixture images (docs/M3-DESIGN.md §4.4 to §4.6, §8
"Pipeline privacy"). No database, no storage."""

import io

import pytest
from PIL import Image, ImageFile, ImageFilter

from app.modules.media import _pipeline as p
from app.modules.media.models import PhotoRejectReason
from tests.media import images

CFG = p.PipelineConfig(
    max_image_pixels=40_000_000,
    min_image_side_px=200,
    variants=(p.VariantSpec("thumb", 320), p.VariantSpec("display", 1280)),
    webp_quality=80,
)
# One variant at full size, so pixels can be compared with the source.
FULL = p.PipelineConfig(
    max_image_pixels=40_000_000,
    min_image_side_px=200,
    variants=(p.VariantSpec("thumb", 320), p.VariantSpec("display", 4096)),
    webp_quality=100,
)
CENTRE = p.BlurRegion(x=0.25, y=0.25, w=0.5, h=0.5)


def _job(
    data: bytes,
    content_type: str = "image/jpeg",
    regions: tuple[p.BlurRegion, ...] = (),
    cfg: p.PipelineConfig = CFG,
) -> p.PhotoJob:
    return p.PhotoJob(
        data=data, declared_type=content_type, owner_kind="deed", regions=regions, config=cfg
    )


def _run(*args: object, **kwargs: object) -> dict[str, p.EncodedVariant]:
    return p.run(_job(*args, **kwargs))  # type: ignore[arg-type]


def _open(variant: p.EncodedVariant) -> Image.Image:
    img = Image.open(io.BytesIO(variant.data))
    img.load()
    return img


def _reject_reason(job: p.PhotoJob) -> PhotoRejectReason:
    with pytest.raises(p.PhotoRejected) as info:
        p.run(job)
    return info.value.reason


# --------------------------------------------------- metadata stripping


def test_every_variant_has_no_metadata_at_all() -> None:
    source = images.jpeg_with_metadata()
    # The fixture really carries everything we strip.
    src = Image.open(io.BytesIO(source))
    assert {"exif", "xmp", "icc_profile", "comment", "photoshop"} <= set(src.info)
    assert src.getexif().get_ifd(0x8825)  # GPS

    variants = _run(source)
    assert set(variants) == {"thumb", "display"}
    for variant in variants.values():
        img = _open(variant)
        assert img.format == "WEBP"
        assert not {"exif", "xmp", "icc_profile", "comment", "photoshop"} & set(img.info)
        assert len(img.getexif()) == 0
        for marker in images.FORBIDDEN_MARKERS:
            assert marker not in variant.data, marker
        # Only image-data chunks in the container (no EXIF, ICCP or XMP chunks).
        assert set(images.webp_chunks(variant.data)) <= {b"VP8 ", b"VP8L"}


@pytest.mark.parametrize("fmt", ["PNG", "WEBP"])
def test_png_and_webp_metadata_is_stripped_too(fmt: str) -> None:
    exif = Image.Exif()
    exif[0x010F] = images.SECRET_MAKE
    source = images.to_bytes(
        images.noise((400, 300)), fmt, exif=exif.tobytes(), icc_profile=images.srgb_icc()
    )
    for variant in _run(source, f"image/{fmt.lower()}").values():
        assert images.SECRET_MAKE.encode() not in variant.data
        assert set(images.webp_chunks(variant.data)) <= {b"VP8 ", b"VP8L"}


# --------------------------------------------------- orientation


def _marked_upright() -> Image.Image:
    """Landscape 600x400, grey, with a red block in the top-left corner."""
    img = images.solid((600, 400), (128, 128, 128))
    img.paste((255, 0, 0), (0, 0, 150, 100))
    return img


@pytest.mark.parametrize(
    ("orientation", "stored_transpose"),
    [
        # exif_transpose applies ROTATE_270 for 6 and ROTATE_90 for 8; store the inverse.
        (6, Image.Transpose.ROTATE_90),
        (8, Image.Transpose.ROTATE_270),
    ],
)
def test_exif_orientation_gives_upright_variants(
    orientation: int, stored_transpose: Image.Transpose
) -> None:
    upright = _marked_upright()
    stored = upright.transpose(stored_transpose)
    assert stored.size == (400, 600)
    source = images.jpeg_with_metadata(stored, orientation=orientation)

    display = _open(_run(source, cfg=FULL)["display"])
    assert display.size == (600, 400)
    r, g, b = display.convert("RGB").getpixel((40, 30))  # type: ignore[misc]
    assert r > 200 and g < 60 and b < 60  # the red block is top-left again
    r, g, b = display.convert("RGB").getpixel((500, 300))  # type: ignore[misc]
    assert abs(r - 128) < 20 and abs(g - 128) < 20


@pytest.mark.parametrize("orientation", [6, 8])
def test_blur_regions_use_the_upright_frame(orientation: int) -> None:
    """A region in the upright frame lands on the same upright pixels, whatever the stored
    orientation."""
    upright = images.noise((600, 400), seed=7)
    transpose = Image.Transpose.ROTATE_90 if orientation == 6 else Image.Transpose.ROTATE_270
    source = images.jpeg_with_metadata(upright.transpose(transpose), orientation=orientation)
    # Blur the top-left quarter only.
    region = p.BlurRegion(x=0.05, y=0.05, w=0.4, h=0.4)
    display = _open(_run(source, regions=(region,), cfg=FULL)["display"])
    inside = display.crop((120, 80, 210, 140))  # well inside the ellipse
    outside = display.crop((420, 260, 560, 380))  # far from it
    assert images.laplacian_variance(inside) < 50
    assert images.laplacian_variance(outside) > 1000


# --------------------------------------------------- blur


def _upright_rgb(data: bytes, regions: tuple[p.BlurRegion, ...]) -> tuple[Image.Image, Image.Image]:
    """The image right before and right after the privacy steps (lossless comparison)."""
    job = _job(data, "image/png", regions)
    for step in (p.decode, p.validate, p.orient, p.colour):
        step(job)
    before = job.current().copy()
    p.apply_blur_regions(job)
    return before, job.current()


def test_blur_changes_the_region_and_nothing_outside_its_box() -> None:
    source = images.to_bytes(images.noise((800, 600), seed=3), "PNG")
    before, after = _upright_rgb(source, (CENTRE,))
    left, top, right, bottom = p.blur_box(CENTRE, 800, 600)

    region_before = before.crop((300, 225, 500, 375))  # inside the ellipse
    region_after = after.crop((300, 225, 500, 375))
    assert images.mean_abs_diff(region_before, region_after) > 40

    # Every pixel outside the grown box is byte-for-byte unchanged.
    for box in (
        (0, 0, 800, top),
        (0, bottom, 800, 600),
        (0, top, left, bottom),
        (right, top, 800, bottom),
    ):
        assert before.crop(box).tobytes() == after.crop(box).tobytes()


def test_blur_is_irreversible_detail_is_gone() -> None:
    source = images.to_bytes(images.noise((800, 600), seed=4), "PNG")
    before, after = _upright_rgb(source, (CENTRE,))
    inside = (300, 225, 500, 375)
    assert images.laplacian_variance(before.crop(inside)) > 2000
    assert images.laplacian_variance(after.crop(inside)) < 5
    # Sharpening the blurred pixels doesn't bring the original back.
    sharpened = after.crop(inside).filter(ImageFilter.UnsharpMask(radius=4, percent=500))
    assert images.mean_abs_diff(sharpened, before.crop(inside)) > 40


def test_the_whole_ellipse_is_replaced_not_just_its_centre() -> None:
    """Pixels just inside the ellipse's edge are blurred as strongly as the centre."""
    source = images.to_bytes(images.noise((800, 600), seed=5), "PNG")
    before, after = _upright_rgb(source, (CENTRE,))
    # Near the left end of the horizontal axis (ellipse spans x 200..600 at y=300).
    edge = (205, 290, 215, 310)
    assert images.mean_abs_diff(before.crop(edge), after.crop(edge)) > 40


def test_several_regions_and_regions_at_the_border() -> None:
    source = images.to_bytes(images.noise((800, 600), seed=6), "PNG")
    regions = (
        p.BlurRegion(0.0, 0.0, 0.2, 0.2),
        p.BlurRegion(0.8, 0.8, 0.2, 0.2),
    )
    before, after = _upright_rgb(source, regions)
    for box in ((20, 20, 140, 100), (660, 500, 780, 580)):
        assert images.mean_abs_diff(before.crop(box), after.crop(box)) > 40
    middle = (350, 250, 450, 350)
    assert before.crop(middle).tobytes() == after.crop(middle).tobytes()


def test_stored_variants_are_blurred_too() -> None:
    upright = images.noise((800, 600), seed=8)
    source = images.to_bytes(upright, "PNG")
    variants = _run(source, "image/png", (CENTRE,), FULL)
    display = _open(variants["display"]).convert("RGB")
    inside = (300, 225, 500, 375)
    assert images.mean_abs_diff(display.crop(inside), upright.crop(inside)) > 40
    assert images.laplacian_variance(display.crop(inside)) < 20
    thumb = _open(variants["thumb"]).convert("RGB")  # 320x240
    small = upright.resize(thumb.size, Image.Resampling.LANCZOS)
    t_inside = (120, 90, 200, 150)
    assert images.mean_abs_diff(thumb.crop(t_inside), small.crop(t_inside)) > 20


def test_no_regions_leaves_pixels_alone() -> None:
    source = images.to_bytes(images.noise((400, 300)), "PNG")
    before, after = _upright_rgb(source, ())
    assert before.tobytes() == after.tobytes()


def test_privacy_steps_run_before_any_variant_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[int, int]] = []

    def spy(job: p.PhotoJob) -> None:
        # Nothing derived or encoded yet, and the image is the full-resolution one.
        assert job.derived == {} and job.variants == {}
        seen.append(job.current().size)
        p.apply_blur_regions(job)

    spy.__name__ = "apply_blur_regions"
    monkeypatch.setattr(p, "PRIVACY_STEPS", (spy,))
    source = images.to_bytes(images.noise((2000, 1500)), "PNG")
    job = _job(source, "image/png", (CENTRE,))
    p.run(job)
    assert seen == [(2000, 1500)]
    assert job.trace == [
        "decode",
        "validate",
        "orient",
        "colour",
        "apply_blur_regions",
        "derive",
        "encode",
    ]
    assert job.image is None  # full-resolution pixels dropped after the run


# --------------------------------------------------- colour


def test_png_with_alpha_is_flattened_onto_white() -> None:
    display = _open(_run(images.png_with_alpha(), "image/png", cfg=FULL)["display"])
    assert display.mode == "RGB"
    r, g, b = display.getpixel((50, 150))  # type: ignore[misc]
    assert min(r, g, b) > 240  # transparent half -> white
    r, g, b = display.getpixel((350, 150))  # type: ignore[misc]
    assert r > 200 and g < 60 and b < 60


@pytest.mark.parametrize("mode", ["L", "LA", "P", "1", "I;16", "CMYK"])
def test_other_modes_become_rgb(mode: str) -> None:
    img = Image.new(mode, (400, 300))
    fmt, ctype = ("JPEG", "image/jpeg") if mode == "CMYK" else ("PNG", "image/png")
    for variant in _run(images.to_bytes(img, fmt), ctype).values():
        assert _open(variant).mode == "RGB"


def test_icc_profile_is_converted_then_dropped() -> None:
    source = images.to_bytes(
        images.solid((400, 300), (10, 200, 30)), "PNG", icc_profile=images.srgb_icc()
    )
    display = _open(_run(source, "image/png", cfg=FULL)["display"])
    assert "icc_profile" not in display.info
    r, g, b = display.getpixel((200, 150))  # type: ignore[misc]
    assert abs(r - 10) < 8 and abs(g - 200) < 8 and abs(b - 30) < 8


# --------------------------------------------------- rejections


def test_animated_webp_and_png_are_refused() -> None:
    assert _reject_reason(_job(images.animated_webp(), "image/webp")) == "animated"
    assert _reject_reason(_job(images.animated_png(), "image/png")) == "animated"


def test_pixel_bomb_is_refused_from_the_header_without_decoding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def no_decoding(self: ImageFile.ImageFile) -> None:
        raise AssertionError("pixels were decoded")

    monkeypatch.setattr(ImageFile.ImageFile, "load", no_decoding)
    # 50 MP (over the 40 MP limit) and 400 MP (over Pillow's own bomb limit).
    for side in (7072, 20000):
        job = _job(images.png_header_only(side, side), "image/png")
        assert _reject_reason(job) == "too_many_pixels"


def test_pixel_limit_comes_from_config() -> None:
    source = images.to_bytes(images.noise((400, 300)), "PNG")
    small = p.PipelineConfig(100_000, 200, CFG.variants, 80)
    assert _reject_reason(_job(source, "image/png", cfg=small)) == "too_many_pixels"


def test_truncated_file_is_corrupt() -> None:
    full = images.to_bytes(images.noise((800, 600)), "JPEG")
    assert _reject_reason(_job(full[: len(full) // 2])) == "corrupt"
    png = images.to_bytes(images.noise((400, 300)), "PNG")
    assert _reject_reason(_job(png[: len(png) // 2], "image/png")) == "corrupt"


def test_too_small_image_is_corrupt() -> None:
    assert _reject_reason(_job(images.to_bytes(images.noise((100, 100)), "JPEG"))) == "corrupt"
    assert _reject_reason(_job(images.to_bytes(images.noise((1000, 150)), "JPEG"))) == "corrupt"


def test_declared_type_must_match_the_file() -> None:
    png = images.to_bytes(images.noise((400, 300)), "PNG")
    assert _reject_reason(_job(png, "image/jpeg")) == "unsupported_type"
    gif = images.to_bytes(images.noise((400, 300)).convert("P"), "GIF")
    assert _reject_reason(_job(gif, "image/png")) == "unsupported_type"
    tiff = images.to_bytes(images.noise((400, 300)), "TIFF")
    assert _reject_reason(_job(tiff, "image/jpeg")) == "unsupported_type"
    assert _reject_reason(_job(b"\xff\xd8\xff" + b"garbage" * 50)) == "corrupt"


def test_only_the_three_decoders_are_reachable() -> None:
    """A GIF dressed with a JPEG magic prefix still isn't decoded as GIF."""
    gif = images.to_bytes(images.noise((400, 300)).convert("P"), "GIF")
    assert _reject_reason(_job(b"\xff\xd8\xff" + gif)) == "corrupt"


# --------------------------------------------------- variants


def test_variants_respect_max_side_and_aspect_ratio() -> None:
    variants = _run(images.to_bytes(images.noise((3000, 1500)), "JPEG"))
    assert (variants["thumb"].width, variants["thumb"].height) == (320, 160)
    assert (variants["display"].width, variants["display"].height) == (1280, 640)
    for v in variants.values():
        assert _open(v).size == (v.width, v.height)

    portrait = _run(images.to_bytes(images.noise((900, 1800)), "JPEG"))
    assert (portrait["display"].width, portrait["display"].height) == (640, 1280)


def test_small_images_are_never_upscaled() -> None:
    variants = _run(images.to_bytes(images.noise((300, 250)), "JPEG"))
    assert (variants["display"].width, variants["display"].height) == (300, 250)
    assert (variants["thumb"].width, variants["thumb"].height) == (300, 250)
