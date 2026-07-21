"""Process-local access to the daemon-owned cooperative map scheduler."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from .cache import CachedUiMapStore
    from .recorder import MapRecorder
    from .scheduler import UiMapScheduler


_scheduler: UiMapScheduler | None = None
_map_store: CachedUiMapStore | None = None
_recorder_factory: Callable[[str], "MapRecorder"] | None = None
_recorders: dict[str, "MapRecorder"] = {}


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


def set_map_recorder_factory(factory: Callable[[str], "MapRecorder"] | None) -> None:
    """Install the per-slot recorder factory (daemon owns the live sessions)."""

    global _recorder_factory
    _recorder_factory = factory
    _recorders.clear()


def get_map_recorder(slot_id: str = "ses0") -> "MapRecorder | None":
    """Return the slot's recorder, creating it once, or None without a daemon."""

    if _recorder_factory is None:
        return None
    recorder = _recorders.get(slot_id)
    if recorder is None:
        recorder = _recorder_factory(slot_id)
        _recorders[slot_id] = recorder
    return recorder


def try_get_active_recorder(slot_id: str = "ses0") -> "MapRecorder | None":
    """Return the slot's recorder only when it is actively recording."""

    recorder = _recorders.get(slot_id)
    return recorder if recorder is not None and recorder.active else None


__all__ = [
    "get_ui_map_scheduler",
    "set_ui_map_scheduler",
    "set_ui_map_store",
    "try_get_ui_map_store",
    "set_map_recorder_factory",
    "get_map_recorder",
    "try_get_active_recorder",
]
