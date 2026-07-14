"""Slot-local opaque handles for SAP GUI COM return values."""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from typing import Any


class ApiHandleError(ValueError):
    """Raised when an opaque handle is unknown or expired."""


@dataclass(slots=True)
class ApiHandle:
    handle_id: str
    value: Any
    object_type: str
    created_at: float
    expires_at: float
    parent_handle: str = ""


class ApiHandleRegistry:
    """Keep COM objects inside their owning wrapper/slot process."""

    def __init__(self, ttl_seconds: float = 900.0) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be greater than zero")
        self._ttl_seconds = ttl_seconds
        self._handles: dict[str, ApiHandle] = {}

    def register(
        self, value: Any, object_type: str, *, parent_handle: str = ""
    ) -> ApiHandle:
        self.sweep()
        now = time.monotonic()
        handle = ApiHandle(
            handle_id=secrets.token_urlsafe(18),
            value=value,
            object_type=object_type,
            created_at=now,
            expires_at=now + self._ttl_seconds,
            parent_handle=parent_handle,
        )
        self._handles[handle.handle_id] = handle
        return handle

    def resolve(self, handle_id: str) -> ApiHandle:
        self.sweep()
        handle = self._handles.get(handle_id)
        if handle is None:
            raise ApiHandleError("unknown or expired SAP API handle")
        return handle

    def clear(self) -> None:
        self._handles.clear()

    def sweep(self) -> int:
        now = time.monotonic()
        expired = [
            handle_id
            for handle_id, handle in self._handles.items()
            if handle.expires_at <= now
        ]
        for handle_id in expired:
            del self._handles[handle_id]
        return len(expired)

    def __len__(self) -> int:
        return len(self._handles)
