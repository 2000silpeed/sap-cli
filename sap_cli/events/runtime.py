"""Process-local event manager binding used by the daemon dispatcher."""

from __future__ import annotations

from .manager import EventSubscriptionManager

_manager: EventSubscriptionManager | None = None


def set_event_manager(manager: EventSubscriptionManager | None) -> None:
    global _manager
    _manager = manager


def get_event_manager() -> EventSubscriptionManager:
    if _manager is None:
        raise RuntimeError(
            "SAP event subscriptions require the persistent daemon runtime"
        )
    return _manager
