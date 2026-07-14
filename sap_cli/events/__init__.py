"""SAP GUI event catalog and platform-independent subscription core."""

from .catalog import (
    EVENT_CATALOG,
    EventParameter,
    EventSpec,
    events_for_scope,
    get_event_spec,
)
from .manager import EventBackend, EventEmitter, EventSubscriptionManager

__all__ = [
    "EVENT_CATALOG",
    "EventBackend",
    "EventEmitter",
    "EventParameter",
    "EventSpec",
    "EventSubscriptionManager",
    "events_for_scope",
    "get_event_spec",
]
