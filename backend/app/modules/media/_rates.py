"""Rate limits added by the M3 security review (SEC2 L9) for routes the design gave none.

Code constants for now: the ``media`` config namespace has no keys for them, and adding keys
means a new config version (a migration). Move them into config with the next ``media`` version.
"""

import uuid

from app.core.ratelimit import RateLimiter

# "<count>/<seconds>", per account.
RATE_UPLOAD_COMPLETE_USER = "120/3600"
RATE_UPLOAD_DISCARD_USER = "120/3600"
RATE_OWNER_READ_USER = "600/900"
RATE_ADMIN_READ_USER = "600/900"


async def complete(limiter: RateLimiter, user_id: uuid.UUID) -> None:
    await limiter.hit(f"uploadcomplete:u:{user_id}", RATE_UPLOAD_COMPLETE_USER)


async def discard(limiter: RateLimiter, user_id: uuid.UUID) -> None:
    await limiter.hit(f"uploaddiscard:u:{user_id}", RATE_UPLOAD_DISCARD_USER)


async def owner_read(limiter: RateLimiter, user_id: uuid.UUID) -> None:
    await limiter.hit(f"mediaread:u:{user_id}", RATE_OWNER_READ_USER)


async def admin_read(limiter: RateLimiter, user_id: uuid.UUID) -> None:
    await limiter.hit(f"adminread:u:{user_id}", RATE_ADMIN_READ_USER)
