"""Platform-independent SAP GUI event subscription manager."""

from __future__ import annotations

import math
import time
import uuid
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from .catalog import EventSpec, get_event_spec


EventEmitter = Callable[[str, tuple[Any, ...], dict[str, Any]], None]


@runtime_checkable
class EventBackend(Protocol):
    """OS-specific connection-point adapter used by the core manager."""

    def attach(
        self,
        target: Any,
        event_names: tuple[str, ...],
        emit: EventEmitter,
    ) -> Any:
        """Attach to ``target`` and return an opaque backend handle."""

    def detach(self, handle: Any) -> None:
        """Disconnect an attachment and release backend references."""

    def pump(self) -> None:
        """Deliver pending events without blocking."""


@dataclass(slots=True)
class _Subscription:
    subscription_id: str
    token: str
    slot_id: str
    scope: str
    owner: str
    event_names: tuple[str, ...]
    created_at: float
    expires_at: float
    queue_limit: int
    redact_sensitive: bool
    handle: Any = None
    events: deque[dict[str, Any]] = field(default_factory=deque)
    next_sequence: int = 1
    dropped_count: int = 0

    def public(self) -> dict[str, Any]:
        return {
            "subscription_id": self.subscription_id,
            "slot_id": self.slot_id,
            "scope": self.scope,
            "owner": self.owner,
            "event_names": list(self.event_names),
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "queue_limit": self.queue_limit,
            "queued_count": len(self.events),
            "next_sequence": self.next_sequence,
            "dropped_count": self.dropped_count,
            "active": True,
        }


class EventSubscriptionManager:
    """Own subscriptions and bounded event queues without importing pywin32."""

    def __init__(
        self,
        backend: EventBackend,
        *,
        clock: Callable[[], float] = time.monotonic,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._backend = backend
        self._clock = clock
        self._id_factory = id_factory or (lambda: uuid.uuid4().hex)
        self._subscriptions: dict[str, _Subscription] = {}
        self._closed = False

    def subscribe(
        self,
        *,
        slot_id: str,
        scope: str,
        target: Any,
        event_names: Sequence[str],
        owner: str = "",
        ttl_seconds: float = 300.0,
        queue_limit: int = 256,
        redact_sensitive: bool = True,
    ) -> dict[str, Any]:
        """Validate, attach, and return a serialisable subscription summary."""

        if self._closed:
            raise RuntimeError("event subscription manager is closed")
        if not slot_id.strip():
            raise ValueError("slot_id is required")
        normalized_scope = scope.strip().lower()
        if normalized_scope not in {"application", "session"}:
            raise ValueError("scope must be application or session")
        if not math.isfinite(ttl_seconds) or ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be a finite positive number")
        if isinstance(queue_limit, bool) or queue_limit < 1:
            raise ValueError("queue_limit must be at least 1")

        specs = _resolve_specs(normalized_scope, event_names)
        canonical_names = tuple(spec.name for spec in specs)
        subscription_id = self._id_factory()
        if not subscription_id or subscription_id in self._subscriptions:
            raise ValueError("id_factory returned an empty or duplicate subscription id")
        token = uuid.uuid4().hex
        now = self._clock()
        subscription = _Subscription(
            subscription_id=subscription_id,
            token=token,
            slot_id=slot_id.strip(),
            scope=normalized_scope,
            owner=owner,
            event_names=canonical_names,
            created_at=now,
            expires_at=now + ttl_seconds,
            queue_limit=queue_limit,
            redact_sensitive=redact_sensitive,
        )
        self._subscriptions[subscription_id] = subscription

        def emit(
            event_name: str,
            args: tuple[Any, ...] = (),
            kwargs: dict[str, Any] | None = None,
        ) -> None:
            self._emit(subscription_id, token, event_name, args, kwargs or {})

        try:
            subscription.handle = self._backend.attach(target, canonical_names, emit)
        except Exception:
            self._subscriptions.pop(subscription_id, None)
            raise
        return subscription.public()

    def poll(
        self,
        subscription_id: str,
        *,
        cursor: int = 0,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Return records after ``cursor`` and report records lost to overflow."""

        if isinstance(cursor, bool) or cursor < 0:
            raise ValueError("cursor must be zero or greater")
        if isinstance(limit, bool) or limit < 1:
            raise ValueError("limit must be at least 1")
        self.sweep_expired()
        subscription = self._subscriptions.get(subscription_id)
        if subscription is None:
            raise KeyError(f"unknown or expired subscription: {subscription_id}")

        available = [item for item in subscription.events if item["sequence"] > cursor]
        selected = available[:limit]
        earliest = subscription.events[0]["sequence"] if subscription.events else None
        lost_count = max(0, earliest - cursor - 1) if earliest is not None else 0
        next_cursor = selected[-1]["sequence"] if selected else cursor + lost_count
        return {
            "subscription_id": subscription_id,
            "events": selected,
            "next_cursor": next_cursor,
            "lost_count": lost_count,
            "dropped_count": subscription.dropped_count,
            "has_more": len(available) > len(selected),
            "active": True,
        }

    def unsubscribe(self, subscription_id: str) -> bool:
        """Detach a subscription. Repeated calls are harmless."""

        subscription = self._subscriptions.pop(subscription_id, None)
        if subscription is None:
            return False
        self._detach(subscription)
        return True

    def list_subscriptions(
        self, *, slot_id: str | None = None, owner: str | None = None
    ) -> list[dict[str, Any]]:
        """Return active subscriptions, optionally restricted by slot/owner."""

        self.sweep_expired()
        values = sorted(self._subscriptions.values(), key=lambda item: item.created_at)
        return [
            item.public()
            for item in values
            if (slot_id is None or item.slot_id == slot_id)
            and (owner is None or item.owner == owner)
        ]

    def cleanup_slot(self, slot_id: str) -> int:
        """Detach all subscriptions owned by a released session slot."""

        ids = [
            item.subscription_id
            for item in self._subscriptions.values()
            if item.slot_id == slot_id
        ]
        for subscription_id in ids:
            self.unsubscribe(subscription_id)
        return len(ids)

    def sweep_expired(self) -> int:
        """Detach subscriptions whose monotonic TTL has elapsed."""

        now = self._clock()
        ids = [
            item.subscription_id
            for item in self._subscriptions.values()
            if item.expires_at <= now
        ]
        for subscription_id in ids:
            self.unsubscribe(subscription_id)
        return len(ids)

    def pump(self) -> None:
        """Ask the backend to deliver pending callbacks, then expire old sinks."""

        if self._closed:
            return
        self._backend.pump()
        self.sweep_expired()

    def close_all(self) -> int:
        """Detach every sink and permanently close this manager."""

        if self._closed:
            return 0
        ids = list(self._subscriptions)
        for subscription_id in ids:
            self.unsubscribe(subscription_id)
        self._closed = True
        return len(ids)

    def _detach(self, subscription: _Subscription) -> None:
        try:
            if subscription.handle is not None:
                self._backend.detach(subscription.handle)
        finally:
            subscription.handle = None

    def _emit(
        self,
        subscription_id: str,
        token: str,
        event_name: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> None:
        subscription = self._subscriptions.get(subscription_id)
        if subscription is None or subscription.token != token or self._closed:
            return
        if self._clock() >= subscription.expires_at:
            self.unsubscribe(subscription_id)
            return
        spec = get_event_spec(subscription.scope, event_name)
        if spec is None or spec.name not in subscription.event_names:
            return

        payload = _event_payload(
            spec,
            args,
            kwargs,
            redact_sensitive=subscription.redact_sensitive,
        )
        record = {
            "sequence": subscription.next_sequence,
            "event_name": spec.name,
            "received_at": self._clock(),
            "payload": payload,
        }
        subscription.next_sequence += 1
        if len(subscription.events) >= subscription.queue_limit:
            subscription.events.popleft()
            subscription.dropped_count += 1
        subscription.events.append(record)


def _resolve_specs(scope: str, event_names: Sequence[str]) -> tuple[EventSpec, ...]:
    if isinstance(event_names, (str, bytes)) or not event_names:
        raise ValueError("event_names must be a non-empty sequence")
    specs: list[EventSpec] = []
    seen: set[str] = set()
    for event_name in event_names:
        spec = get_event_spec(scope, str(event_name))
        if spec is None:
            raise ValueError(f"event is not in the verified {scope} catalog: {event_name}")
        if spec.name not in seen:
            specs.append(spec)
            seen.add(spec.name)
    return tuple(specs)


def _event_payload(
    spec: EventSpec,
    args: tuple[Any, ...],
    kwargs: Mapping[str, Any],
    *,
    redact_sensitive: bool,
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for index, parameter in enumerate(spec.parameters):
        if index < len(args):
            value = args[index]
        elif parameter.name in kwargs:
            value = kwargs[parameter.name]
        else:
            continue
        payload[parameter.name] = (
            "[REDACTED]"
            if redact_sensitive and parameter.sensitive
            else _json_safe(value, redact_sensitive=redact_sensitive)
        )
    if len(args) > len(spec.parameters):
        payload["extra_args"] = [
            _json_safe(value, redact_sensitive=redact_sensitive)
            for value in args[len(spec.parameters):]
        ]
    declared = {item.name for item in spec.parameters}
    extras = {
        str(key): _json_safe(value, redact_sensitive=redact_sensitive)
        for key, value in kwargs.items()
        if key not in declared
    }
    if extras:
        payload["extra_kwargs"] = extras
    return payload


_SENSITIVE_KEYS = {
    "password", "passwd", "secret", "token", "credential", "credentials",
}


def _json_safe(value: Any, *, redact_sensitive: bool, depth: int = 0) -> Any:
    if depth >= 8:
        return "[MAX_DEPTH]"
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            name = str(key)
            result[name] = (
                "[REDACTED]"
                if redact_sensitive and name.strip().lower() in _SENSITIVE_KEYS
                else _json_safe(item, redact_sensitive=redact_sensitive, depth=depth + 1)
            )
        return result
    if isinstance(value, (list, tuple, set, frozenset)):
        return [
            _json_safe(item, redact_sensitive=redact_sensitive, depth=depth + 1)
            for item in value
        ]

    reference: dict[str, Any] = {"python_type": type(value).__name__}
    for source_name, output_name in (("Type", "type"), ("Id", "id"), ("Name", "name")):
        try:
            item = getattr(value, source_name)
        except Exception:
            continue
        if item is not None and isinstance(item, (str, int, float, bool)):
            reference[output_name] = item
    return reference
