"""Process-local access to the daemon-owned cooperative map scheduler."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .cache import CachedUiMapStore
    from .scheduler import UiMapScheduler


_scheduler: UiMapScheduler | None = None
_map_store: CachedUiMapStore | None = None


def set_ui_map_scheduler(scheduler: UiMapScheduler | None) -> None:
    global _scheduler
    _scheduler = scheduler


def get_ui_map_scheduler() -> UiMapScheduler:
    if _scheduler is None:
        raise RuntimeError(
            "UI map traversal requires the persistent daemon; start the daemon first"
        )
    return _scheduler


def set_ui_map_store(store: CachedUiMapStore | None) -> None:
    global _map_store
    _map_store = store


def try_get_ui_map_store() -> CachedUiMapStore | None:
    return _map_store


__all__ = [
    "get_ui_map_scheduler",
    "set_ui_map_scheduler",
    "set_ui_map_store",
    "try_get_ui_map_store",
]
