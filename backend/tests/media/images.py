"""Generated fixture images for the pipeline privacy tests (QA6-style). Nothing is checked in:
every image is built here with Pillow, so its contents are known exactly."""

import io
import random
import struct
import zlib

from PIL import Image, ImageChops, ImageCms, ImageFilter, ImageStat

# Distinctive values that must never survive processing.
SECRET_MAKE = "SecretCamMakerXYZ"
SECRET_MODEL = "SecretModel-42"
SECRET_COMMENT = b"secret-comment-do-not-keep"
SECRET_XMP = (
    b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF '
    b'xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
    b'<rdf:Description xmlns:xmp="http://ns.adobe.com/xap/1.0/" '
    b'xmp:CreatorTool="SecretXmpTool"/></rdf:RDF></x:xmpmeta>'
)
SECRET_IPTC = b"SecretIptcByline"
# Raw-byte markers that must not appear in any variant.
FORBIDDEN_MARKERS = (
    b"Exif",
    b"EXIF",
    b"GPS",
    b"ICCP",
    b"XMP ",
    b"http://ns.adobe.com/xap",
    b"xmpmeta",
    SECRET_MAKE.encode(),
    SECRET_MODEL.encode(),
    SECRET_COMMENT,
    SECRET_IPTC,
    b"SecretXmpTool",
)

_GPS_IFD = 0x8825
_ORIENTATION = 0x0112


def noise(size: tuple[int, int], seed: int = 1) -> Image.Image:
    """High-frequency RGB noise (every pixel random): blur must destroy it."""
    rng = random.Random(seed)
    return Image.frombytes("RGB", size, rng.randbytes(size[0] * size[1] * 3))


def solid(size: tuple[int, int], colour: tuple[int, int, int] = (40, 120, 200)) -> Image.Image:
    return Image.new("RGB", size, colour)


def to_bytes(img: Image.Image, fmt: str, **params: object) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format=fmt, **params)
    return buf.getvalue()


def _exif(orientation: int | None = None) -> Image.Exif:
    exif = Image.Exif()
    exif[0x010F] = SECRET_MAKE  # Make
    exif[0x0110] = SECRET_MODEL  # Model
    exif[0x0132] = "2026:11:19 10:30:00"  # DateTime
    if orientation is not None:
        exif[_ORIENTATION] = orientation
    gps = exif.get_ifd(_GPS_IFD)
    gps[1] = "N"
    gps[2] = (12.0, 58.0, 17.76)  # Bangalore
    gps[3] = "E"
    gps[4] = (77.0, 35.0, 40.56)
    return exif


def _insert_app13(jpeg: bytes) -> bytes:
    """Splice an IPTC (Photoshop APP13) segment in after SOI."""
    payload = b"Photoshop 3.0\x00" + b"8BIM\x04\x04\x00\x00" + struct.pack(">I", 0)
    payload += b"\x1c\x02\x50" + struct.pack(">H", len(SECRET_IPTC)) + SECRET_IPTC
    segment = b"\xff\xed" + struct.pack(">H", len(payload) + 2) + payload
    return jpeg[:2] + segment + jpeg[2:]


def srgb_icc() -> bytes:
    return ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()


def jpeg_with_metadata(img: Image.Image | None = None, *, orientation: int | None = None) -> bytes:
    """A JPEG carrying GPS, camera and time EXIF, XMP, an ICC profile, a comment and IPTC."""
    img = img if img is not None else noise((640, 480))
    data = to_bytes(
        img,
        "JPEG",
        quality=95,
        exif=_exif(orientation).tobytes(),
        xmp=SECRET_XMP,
        icc_profile=srgb_icc(),
        comment=SECRET_COMMENT,
    )
    return _insert_app13(data)


def png_with_alpha(size: tuple[int, int] = (400, 300)) -> bytes:
    """Red, fully transparent on the left half and opaque on the right."""
    img = Image.new("RGBA", size, (255, 0, 0, 255))
    img.paste((255, 0, 0, 0), (0, 0, size[0] // 2, size[1]))
    return to_bytes(img, "PNG")


def animated_webp(size: tuple[int, int] = (300, 300)) -> bytes:
    frames = [solid(size, (255, 0, 0)), solid(size, (0, 255, 0))]
    return to_bytes(frames[0], "WEBP", save_all=True, append_images=frames[1:], duration=100)


def animated_png(size: tuple[int, int] = (300, 300)) -> bytes:
    frames = [solid(size, (255, 0, 0)), solid(size, (0, 255, 0))]
    return to_bytes(frames[0], "PNG", save_all=True, append_images=frames[1:], duration=100)


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def png_header_only(width: int, height: int) -> bytes:
    """A PNG whose header claims ``width`` x ``height`` pixels but holds no pixel data: the
    pipeline must refuse it from the header, without decoding (allocating) anything."""
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IEND", b"")


def webp_chunks(data: bytes) -> list[bytes]:
    """The RIFF chunk types in a WebP file (``VP8 ``, ``VP8L``, ``VP8X``, ``EXIF``, ...)."""
    assert data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    kinds: list[bytes] = []
    pos = 12
    while pos + 8 <= len(data):
        kind = data[pos : pos + 4]
        (length,) = struct.unpack("<I", data[pos + 4 : pos + 8])
        kinds.append(kind)
        pos += 8 + length + (length & 1)
    return kinds


def laplacian_variance(img: Image.Image) -> float:
    """Variance of a 3x3 Laplacian over the greyscale image: high for sharp detail."""
    lap = img.convert("L").filter(
        ImageFilter.Kernel((3, 3), [0, 1, 0, 1, -4, 1, 0, 1, 0], scale=1, offset=128)
    )
    return float(ImageStat.Stat(lap).var[0])


def mean_abs_diff(a: Image.Image, b: Image.Image) -> float:
    """Mean absolute per-channel difference of two same-size RGB images (0..255)."""
    assert a.size == b.size
    diff = ImageChops.difference(a.convert("RGB"), b.convert("RGB"))
    return sum(ImageStat.Stat(diff).mean) / 3
