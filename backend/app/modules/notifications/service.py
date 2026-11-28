"""Public interface of the notifications module (docs/M6-DESIGN.md D103, D104).

Nobody imports ``notifications`` (enforced by ``tests/test_architecture.py``): other modules
reach it only through the domain events its handlers subscribe to. This file gathers the
module's own operations for its router, handlers and jobs.
"""

from app.modules.notifications._limits import NotificationsConfig, load_notifications
from app.modules.notifications._prefs import mute, preferences, set_preference, unmute
from app.modules.notifications._purge import Purged, purge
from app.modules.notifications._reads import (
    list_page,
    mark_all_read,
    mark_read,
    mark_seen,
    mark_unread,
    unread_count,
)
from app.modules.notifications._write import Notice, Outcome, notify

__all__ = [
    "Notice",
    "NotificationsConfig",
    "Outcome",
    "Purged",
    "list_page",
    "load_notifications",
    "mark_all_read",
    "mark_read",
    "mark_seen",
    "mark_unread",
    "mute",
    "notify",
    "preferences",
    "purge",
    "set_preference",
    "unmute",
    "unread_count",
]
