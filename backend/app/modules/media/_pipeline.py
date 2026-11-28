"""The photo processing pipeline: pure functions, no database and no storage (§4.4 to §4.6).

``run(job)`` runs an ordered list of steps over a ``PhotoJob``:

    decode -> validate -> orient -> colour -> [privacy steps] -> derive -> encode

M3's only privacy step is ``apply_blur_regions`` (D33). Profile pictures (M6A, D116) run
``avatar_steps()`` instead: no blur, and a server-side square ``crop_square`` before the
variants are derived, so pixels outside the crop never reach any stored variant. A later
privacy feature (face *suggestions*, a guardian-consent hold, a review hold) is one function
added to ``PRIVACY_STEPS`` plus a config flag; upload, attach, storage and serving don't change.

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
class AvatarCrop:
    """A square on the upright (orientation-corrected) image (D116): ``x`` is a fraction of the
    width, ``y`` a fraction of the height (top-left corner), and ``size`` the side as a fraction
    of the image's shorter side, so ``size = 1`` is the largest square that fits."""

    x: float
    y: float
    size: float


class CropTooSmall(Exception):
    """The square (or the whole image) is smaller than ``avatar_min_side_px``."""


class CropInvalid(Exception):
    """The square doesn't fit inside the image."""


def crop_tolerance_px(width: int, height: int) -> int:
    """Rounding slack for a client's normalised numbers: 2 px, or 0.2% of the longer side."""
    return max(2, math.ceil(0.002 * max(width, height)))


def crop_box(
    crop: AvatarCrop, width: int, height: int, min_side_px: int
) -> tuple[int, int, int, int]:
    """The pixel square ``(left, top, right, bottom)`` of ``crop`` on a ``width`` x ``height``
    upright image. A square that overshoots an edge by no more than the rounding slack is moved
    back inside; anything more is ``CropInvalid``. A side under ``min_side_px`` is
    ``CropTooSmall``."""
    short = min(width, height)
    side = min(round(crop.size * short), short)
    if short < min_side_px or side < min_side_px:
        raise CropTooSmall
    left, top = round(crop.x * width), round(crop.y * height)
    slack = crop_tolerance_px(width, height)
    if left < 0 or top < 0 or left + side > width + slack or top + side > height + slack:
        raise CropInvalid
    left, top = min(left, width - side), min(top, height - side)
    return left, top, left + side, top + side


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
    # Profile pictures only (``owner_kind == "avatar"``).
    crop: AvatarCrop | None = None
    # The crop's smallest side in pixels (``avatar_min_side_px``).
    crop_min_side_px: int = 1
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
    if job.owner_kind == "avatar" and (job.regions or job.crop is None):
        # PUT /me/avatar always sets the crop (ck_photos_avatar_crop_required) and no regions.
        raise ValueError("a profile picture needs a crop and has no blur regions")


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


def crop_square(job: PhotoJob) -> None:
    """Cut the profile picture's square out of the upright image (D116). Everything after this
    step sees only the square. ``PUT /me/avatar`` checked the crop against the same header, so
    a failure here means the file changed or lied about its size: it is rejected."""
    img = job.current()
    assert job.crop is not None  # validate() checked
    try:
        box = crop_box(job.crop, img.width, img.height, job.crop_min_side_px)
    except (CropTooSmall, CropInvalid):
        raise PhotoRejected(PhotoRejectReason.CORRUPT) from None
    square = img.crop(box)
    square.load()
    square.info.clear()
    job.image = square


# Privacy steps for profile pictures, after the crop (none yet; a later review hold goes here).
AVATAR_PRIVACY_STEPS: tuple[Step, ...] = ()


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


def avatar_steps() -> tuple[Step, ...]:
    """The profile-picture pipeline (D116): no blur; the square crop before any variant."""
    return (decode, validate, orient, colour, crop_square, *AVATAR_PRIVACY_STEPS, derive, encode)


def steps_for(owner_kind: str) -> tuple[Step, ...]:
    return avatar_steps() if owner_kind == "avatar" else steps()


# EXIF orientations that swap width and height (a quarter turn, with or without a mirror).
_QUARTER_TURNS = frozenset({5, 6, 7, 8})
_EXIF_ORIENTATION = 0x0112


def upright_size(data: bytes, declared_type: str) -> tuple[int, int] | None:
    """The image's ``(width, height)`` once its EXIF orientation is applied, read from the header
    only (no pixels decoded). None when the header can't be read safely: the worker then
    rejects the file with the right reason."""
    expected = FORMAT_FOR_TYPE.get(declared_type)
    if expected is None or not magic_matches(data, declared_type):
        return None
    try:
        img = _open(data)
        if img.format != expected:
            return None
        width, height = img.size
        orientation = img.getexif().get(_EXIF_ORIENTATION)
    except Exception:
        return None
    if orientation in _QUARTER_TURNS:
        return height, width
    return width, height


def run(job: PhotoJob, pipeline: Sequence[Step] | None = None) -> dict[str, EncodedVariant]:
    """Run every step over ``job``; returns the encoded variants by name. CPU-bound: the worker
    calls it in a thread. Raises ``PhotoRejected`` for a file that can't be used."""
    for step in pipeline if pipeline is not None else steps_for(job.owner_kind):
        step(job)
        job.trace.append(step.__name__)
    job.image = None  # drop the full-resolution pixels as soon as possible
    return job.variants
