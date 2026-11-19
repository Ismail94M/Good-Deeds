"""Object keys and object headers for photos (docs/M3-DESIGN.md §2.2, §4.2, §4.7).

Every key is chosen by the server from the photo's random ID; clients never choose a path.
Variant keys carry a version suffix, so an object never changes under the same key.
"""

import uuid
from collections.abc import Mapping
from typing import Any

STAGING_PREFIX = "staging/"
PHOTOS_PREFIX = "photos/"
HELD_PREFIX = "held/"
# Variants are private and immutable under their key (§4.2).
CACHE_CONTROL = "private, max-age=3600"
VARIANT_CONTENT_TYPE = "image/webp"
FIRST_VERSION = 1


def staging_key(photo_id: uuid.UUID) -> str:
    """The raw upload (deleted after processing; no unblurred copy is kept, D33)."""
    return f"{STAGING_PREFIX}{photo_id}"


def variant_key(photo_id: uuid.UUID, name: str, version: int = FIRST_VERSION) -> str:
    return f"{PHOTOS_PREFIX}{photo_id}/{name}-{version}.webp"


def held_key(key: str) -> str:
    """Where an admin removal moves a variant (``photos/…`` -> ``held/…``, §4.7)."""
    if not key.startswith(PHOTOS_PREFIX):
        raise ValueError("not a photos/ key")
    return HELD_PREFIX + key.removeprefix(PHOTOS_PREFIX)


def variant_keys_of(value: Mapping[str, Any] | None) -> dict[str, str]:
    """The ``variant_keys`` JSON as ``{name: key}`` (empty when there are none)."""
    if not value:
        return {}
    return {str(name): str(key) for name, key in value.items()}


def all_object_keys(staging: str, variants: Mapping[str, Any] | None) -> list[str]:
    """Every object a photo may have: its staging object, its variants and their held copies.
    Deleting a missing key is not an error, so deleting all of them is always safe."""
    keys = [staging]
    for key in variant_keys_of(variants).values():
        keys.append(key)
        if key.startswith(PHOTOS_PREFIX):
            keys.append(held_key(key))
    return keys
