from __future__ import annotations

import pytest

from sap_cli.api_handles import ApiHandleError, ApiHandleRegistry


def test_handle_registry_resolves_and_clears_slot_local_object() -> None:
    registry = ApiHandleRegistry()
    value = object()
    handle = registry.register(value, "GuiCollection")

    resolved = registry.resolve(handle.handle_id)
    assert resolved.value is value
    assert resolved.object_type == "GuiCollection"

    registry.clear()
    with pytest.raises(ApiHandleError, match="unknown or expired"):
        registry.resolve(handle.handle_id)


def test_handle_registry_expires(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = iter((10.0, 10.0, 12.0))
    monkeypatch.setattr("sap_cli.api_handles.time.monotonic", lambda: next(clock))
    registry = ApiHandleRegistry(ttl_seconds=1.0)
    handle = registry.register(object(), "GuiCollection")

    with pytest.raises(ApiHandleError, match="unknown or expired"):
        registry.resolve(handle.handle_id)
