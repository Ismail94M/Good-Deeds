"""The photo processing pipeline: pure functions, no database and no storage (§4.4 to §4.6).

``run(job)`` runs an ordered list of steps over a ``PhotoJob``:

    decode -> validate -> orient -> colour -> [privacy steps] -> derive -> encode

M3's only privacy step is ``apply_blur_regions`` (D33). A later privacy feature (face
*suggestions*, a guardian-consent hold, a review hold) is one function added to
``PRIVACY_STEPS`` plus a config flag; upload, attach, storage and serving don't change.

Privacy rules this file keeps:
- Every variant is derived from the image *after* the privacy steps; nothing is encoded before
  them, and the pipeline returns bytes only (the worker uploads them afterwards).
- Output is WebP written with no ``exif``, ``icc_profile`` or ``xmp`` arguments and with the
  image's ``info`` cleared, so it carries no metadata (GPS, camera, time, device, comments).
- Only the JPEG, PNG and WebP decoders are reachable (``formats=``), the header is checked
  (format, pixel count, animation, minimum side) before any pixel is decoded, and pixel
  bombs are refused without allocating them.

A bad file raises ``PhotoRejected`` with a ``PhotoRejectReason``; the worker records it.
"""

import io
import math
import warnings
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from PIL import Image, ImageChops, ImageCms, ImageDraw, ImageFilter, ImageOps

from app.modules.media.models import PhotoRejectReason

# Declared type -> the Pillow format it must decode as.
FORMAT_FOR_TYPE: dict[str, str] = {
    "image/jpeg": "JPEG",
    "image/png": "PNG",
    "image/webp": "WEBP",
}
_DECODERS = tuple(FORMAT_FOR_TYPE.values())
_WHITE = (255, 255, 255)

# Blur geometry (§4.5). The box around each ellipse grows by this fraction of its size on
# every side; the soft edge lives in that margin, so the ellipse itself is fully covered.
BLUR_MARGIN = 0.10
BLUR_MIN_BLOCK_PX = 8
BLUR_BLOCKS_PER_SIDE = 8
BLUR_RADIUS_DIVISOR = 6


class PhotoRejected(Exception):
    """The file can't be used; ``reason`` is stored on the photo."""

    def __init__(self, reason: PhotoRejectReason) -> None:
        super().__init__(reason.value)
        self.reason = reason


@dataclass(frozen=True)
class BlurRegion:
    """A normalised ellipse: fractions of the upright (orientation-corrected) image, top-left
    origin. ``x``, ``y`` is the top-left corner of its bounding box."""

    x: float
    y: float
    w: float
    h: float


@dataclass(frozen=True)
class VariantSpec:
    name: str
    max_side_px: int


@dataclass(frozen=True)
class PipelineConfig:
    """The ``media`` config values the pipeline needs (taken from ``MediaLimits``)."""

    max_image_pixels: int
    min_image_side_px: int
    variants: tuple[VariantSpec, ...]
    webp_quality: int


@dataclass(frozen=True)
class EncodedVariant:
    name: str
    data: bytes
    width: int
    height: int
    content_type: str = "image/webp"


@dataclass
class PhotoJob:
    data: bytes
    declared_type: str
    # "deed" or "self_log"; steps may use it (self-logs never carry regions).
    owner_kind: str
    regions: tuple[BlurRegion, ...]
    config: PipelineConfig
    image: Image.Image | None = None
    derived: dict[str, Image.Image] = field(default_factory=dict)
    variants: dict[str, EncodedVariant] = field(default_factory=dict)
    # Names of the steps that ran, in order (tests check the order).
    trace: list[str] = field(default_factory=list)

    def current(self) -> Image.Image:
        if self.image is None:
            raise RuntimeError("no image decoded yet")
        return self.image


type Step = Callable[[PhotoJob], None]


# --------------------------------------------------- decode and validate


def magic_matches(data: bytes, declared_type: str) -> bool:
    """Whether the first bytes are the declared type's magic number (JPEG ``FF D8 FF``, PNG
    ``89 50 4E 47 0D 0A 1A 0A``, WebP ``RIFF????WEBP``)."""
    if declared_type == "image/jpeg":
        return data[:3] == b"\xff\xd8\xff"
    if declared_type == "image/png":
        return data[:8] == b"\x89PNG\r\n\x1a\n"
    if declared_type == "image/webp":
        return data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    return False


def _open(data: bytes) -> Image.Image:
    """Open the header only. Any Pillow decompression-bomb signal counts as too many pixels."""
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        return Image.open(io.BytesIO(data), formats=_DECODERS)


def decode(job: PhotoJob) -> None:
    """Header checks, then ``verify()``, then reopen and decode the pixels."""
    expected = FORMAT_FOR_TYPE.get(job.declared_type)
    if expected is None or not magic_matches(job.data, job.declared_type):
        raise PhotoRejected(PhotoRejectReason.UNSUPPORTED_TYPE)
    cfg = job.config
    try:
        img = _open(job.data)
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise PhotoRejected(PhotoRejectReason.TOO_MANY_PIXELS) from None
    except Exception:
        # The magic bytes matched the declared type but the header doesn't parse.
        raise PhotoRejected(PhotoRejectReason.CORRUPT) from None

    # Cheap checks on the header, before any pixel is decoded.
    if img.format != expected:
        raise PhotoRejected(PhotoRejectReason.UNSUPPORTED_TYPE)
    width, height = img.size
    if width * height > cfg.max_image_pixels:
        raise PhotoRejected(PhotoRejectReason.TOO_MANY_PIXELS)
    if getattr(img, "n_frames", 1) > 1 or getattr(img, "is_animated", False):
        raise PhotoRejected(PhotoRejectReason.ANIMATED)
    if min(width, height) < cfg.min_image_side_px:
        raise PhotoRejected(PhotoRejectReason.CORRUPT)

    try:
        img.verify()
        img = _open(job.data)  # verify() leaves the image unusable: reopen
        img.load()
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise PhotoRejected(PhotoRejectReason.TOO_MANY_PIXELS) from None
    except Exception:
        raise PhotoRejected(PhotoRejectReason.CORRUPT) from None
    job.image = img


def validate(job: PhotoJob) -> None:
    """Rules on the decoded image (the header checks already ran in ``decode``)."""
    img = job.current()
    if img.size[0] <= 0 or img.size[1] <= 0:
        raise PhotoRejected(PhotoRejectReason.CORRUPT)
    if job.owner_kind == "self_log" and job.regions:
        # Attach refuses this (422); never blur-and-keep a private photo by mistake.
        raise ValueError("self-log photos have no blur regions")


# --------------------------------------------------- orient and colour


def orient(job: PhotoJob) -> None:
    """Apply the EXIF orientation, so the image is upright (the frame blur regions use)."""
    img = job.current()
    try:
        job.image = ImageOps.exif_transpose(img)
    except Exception:
        raise PhotoRejected(PhotoRejectReason.CORRUPT) from None


def _has_alpha(img: Image.Image) -> bool:
    return img.mode in ("RGBA", "LA", "PA", "La", "RGBa") or (
        img.mode == "P" and "transparency" in img.info
    )


def colour(job: PhotoJob) -> None:
    """Embedded ICC profile -> sRGB (then dropped); alpha flattened onto white; mode RGB."""
    img = job.current()
    alpha = _has_alpha(img)
    if img.mode.startswith("I;16") or img.mode == "I":
        # 16-bit greyscale PNG: scale to 8 bits (a plain convert would clip to white).
        icc_info = img.info.get("icc_profile")
        img = img.convert("I").point(lambda v: v * (1 / 256)).convert("L")
        if icc_info:
            img.info["icc_profile"] = icc_info
    if img.mode in ("P", "PA", "LA", "La", "RGBa", "F", "1"):
        img = img.convert("RGBA" if alpha else "RGB")
    icc = img.info.get("icc_profile")
    converted: Image.Image | None = None
    if icc:
        try:
            src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
            dst = ImageCms.createProfile("sRGB")
            converted = ImageCms.profileToProfile(
                img, src, dst, outputMode="RGBA" if alpha else "RGB"
            )
        except (ImageCms.PyCMSError, OSError, ValueError):
            converted = None  # a broken profile: fall back to a plain conversion
    if converted is None:
        converted = img.convert("RGBA" if alpha else "RGB")
    if converted.mode == "RGBA":
        flat = Image.new("RGB", converted.size, _WHITE)
        flat.paste(converted, mask=converted.getchannel("A"))
        converted = flat
    elif converted.mode != "RGB":
        converted = converted.convert("RGB")
    converted.info.clear()
    job.image = converted


# --------------------------------------------------- privacy steps


def blur_box(region: BlurRegion, width: int, height: int) -> tuple[int, int, int, int]:
    """The pixel box ``(left, top, right, bottom)`` a region may change: its bounding box grown
    by ``BLUR_MARGIN`` on every side, clamped to the image. Pixels outside it never change."""
    w_px, h_px = region.w * width, region.h * height
    left = math.floor(region.x * width - BLUR_MARGIN * w_px)
    top = math.floor(region.y * height - BLUR_MARGIN * h_px)
    right = math.ceil((region.x + region.w) * width + BLUR_MARGIN * w_px)
    bottom = math.ceil((region.y + region.h) * height + BLUR_MARGIN * h_px)
    return max(left, 0), max(top, 0), min(right, width), min(bottom, height)


def _blur_one(img: Image.Image, region: BlurRegion) -> None:
    width, height = img.size
    left, top, right, bottom = blur_box(region, width, height)
    box_w, box_h = right - left, bottom - top
    if box_w <= 0 or box_h <= 0:
        return
    w_px, h_px = region.w * width, region.h * height
    crop = img.crop((left, top, right, bottom))

    # Pixelate first (irreversible), then a Gaussian blur over the blocks.
    block = max(BLUR_MIN_BLOCK_PX, max(box_w, box_h) / BLUR_BLOCKS_PER_SIDE)
    small = (max(1, math.ceil(box_w / block)), max(1, math.ceil(box_h / block)))
    crop = crop.resize(small, Image.Resampling.BOX).resize((box_w, box_h), Image.Resampling.NEAREST)
    crop = crop.filter(ImageFilter.GaussianBlur(max(w_px, h_px) / BLUR_RADIUS_DIVISOR))

    # Mask: a feathered ellipse over the grown box, plus the region's own ellipse at full
    # strength, so every pixel of the ellipse is replaced and only the margin is blended.
    # Coordinates are relative to the clamped box; drawing clips what falls outside.
    ox, oy = region.x * width - left, region.y * height - top
    grown = (
        ox - BLUR_MARGIN * w_px,
        oy - BLUR_MARGIN * h_px,
        ox + w_px + BLUR_MARGIN * w_px,
        oy + h_px + BLUR_MARGIN * h_px,
    )
    feather = Image.new("L", (box_w, box_h), 0)
    ImageDraw.Draw(feather).ellipse(grown, fill=255)
    feather = feather.filter(ImageFilter.GaussianBlur(BLUR_MARGIN * min(w_px, h_px) / 3))
    core = Image.new("L", (box_w, box_h), 0)
    # One pixel larger than the ellipse, so anti-aliasing never leaves its rim half-blended.
    ImageDraw.Draw(core).ellipse((ox - 1, oy - 1, ox + w_px + 1, oy + h_px + 1), fill=255)
    mask = ImageChops.lighter(feather, core)

    img.paste(crop, (left, top), mask)


def apply_blur_regions(job: PhotoJob) -> None:
    """Blur every region on the full-resolution upright image (D33, §4.5)."""
    if not job.regions:
        return
    img = job.current()
    for region in job.regions:
        _blur_one(img, region)


PRIVACY_STEPS: tuple[Step, ...] = (apply_blur_regions,)


# --------------------------------------------------- derive and encode


def derive(job: PhotoJob) -> None:
    """One resized copy per configured variant, each from the (blurred) full image."""
    img = job.current()
    for spec in job.config.variants:
        copy = img.copy()
        copy.thumbnail((spec.max_side_px, spec.max_side_px), Image.Resampling.LANCZOS)
        copy.info.clear()
        job.derived[spec.name] = copy


def encode(job: PhotoJob) -> None:
    """WebP with no metadata arguments: no EXIF, ICC profile or XMP is written."""
    for name, img in job.derived.items():
        buf = io.BytesIO()
        img.save(buf, format="WEBP", quality=job.config.webp_quality, method=4)
        job.variants[name] = EncodedVariant(
            name=name, data=buf.getvalue(), width=img.width, height=img.height
        )
    job.derived.clear()


def steps() -> tuple[Step, ...]:
    """The pipeline, in order. Read at call time, so a test can patch ``PRIVACY_STEPS``."""
    return (decode, validate, orient, colour, *PRIVACY_STEPS, derive, encode)


def run(job: PhotoJob, pipeline: Sequence[Step] | None = None) -> dict[str, EncodedVariant]:
    """Run every step over ``job``; returns the encoded variants by name. CPU-bound: the worker
    calls it in a thread. Raises ``PhotoRejected`` for a file that can't be used."""
    for step in pipeline if pipeline is not None else steps():
        step(job)
        job.trace.append(step.__name__)
    job.image = None  # drop the full-resolution pixels as soon as possible
    return job.variants
