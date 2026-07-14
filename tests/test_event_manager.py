"""Platform-independent tests for the SAP GUI event subscription core."""

from __future__ import annotations

import json

import pytest

from sap_cli.events import (
    EVENT_CATALOG,
    EventBackend,
    EventSubscriptionManager,
    events_for_scope,
    get_event_spec,
)


class FakeClock:
    def __init__(self) -> None:
        self.value = 100.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class FakeBackend:
    def __init__(self) -> None:
        self.attachments: dict[int, dict] = {}
        self.detached: list[int] = []
        self.pump_count = 0
        self.next_handle = 1
        self.fail_attach = False

    def attach(self, target, event_names, emit):
        if self.fail_attach:
            raise RuntimeError("attach failed")
        handle = self.next_handle
        self.next_handle += 1
        self.attachments[handle] = {
            "target": target,
            "events": event_names,
            "emit": emit,
        }
        return handle

    def detach(self, handle):
        self.detached.append(handle)
        self.attachments.pop(handle, None)

    def pump(self):
        self.pump_count += 1

    def emit(self, handle, event_name, *args, **kwargs):
        self.attachments[handle]["emit"](event_name, args, kwargs)


@pytest.fixture
def runtime():
    clock = FakeClock()
    backend = FakeBackend()
    ids = iter((f"sub-{index}" for index in range(1, 50)))
    manager = EventSubscriptionManager(
        backend, clock=clock, id_factory=lambda: next(ids)
    )
    return manager, backend, clock


def _subscribe(manager, **updates):
    values = {
        "slot_id": "ses0",
        "scope": "session",
        "target": object(),
        "event_names": ["StartRequest"],
        "owner": "agent-a",
        "ttl_seconds": 30,
        "queue_limit": 3,
    }
    values.update(updates)
    return manager.subscribe(**values)


def test_catalog_contains_exactly_the_17_verified_declarations():
    assert len(EVENT_CATALOG) == 17
    assert len({(item.scope, item.name) for item in EVENT_CATALOG}) == 17
    assert len(events_for_scope("application")) == 4
    assert len(events_for_scope("session")) == 13
    assert get_event_spec("SESSION", "startrequest").name == "StartRequest"
    assert get_event_spec("session", "Remarks") is None
    assert get_event_spec("session", "True") is None


def test_event_backend_protocol_is_runtime_checkable():
    assert isinstance(FakeBackend(), EventBackend)


def test_subscribe_emit_poll_preserves_order_and_cursor(runtime):
    manager, backend, _clock = runtime
    created = _subscribe(
        manager,
        event_names=["StartRequest", "EndRequest"],
    )
    handle = next(iter(backend.attachments))

    backend.emit(handle, "StartRequest", FakeCom("GuiSession", "ses[0]"))
    backend.emit(handle, "EndRequest", FakeCom("GuiSession", "ses[0]"))

    first = manager.poll(created["subscription_id"], limit=1)
    second = manager.poll(created["subscription_id"], cursor=first["next_cursor"])
    assert [item["event_name"] for item in first["events"]] == ["StartRequest"]
    assert [item["event_name"] for item in second["events"]] == ["EndRequest"]
    assert first["has_more"] is True
    assert second["next_cursor"] == 2
    assert json.loads(json.dumps(second))["events"][0]["payload"]["Session"]["id"] == "ses[0]"


def test_bounded_queue_reports_drops_and_cursor_gap(runtime):
    manager, backend, _clock = runtime
    created = _subscribe(manager, queue_limit=2)
    handle = next(iter(backend.attachments))

    for _ in range(5):
        backend.emit(handle, "StartRequest", object())

    result = manager.poll(created["subscription_id"], cursor=0)
    assert [item["sequence"] for item in result["events"]] == [4, 5]
    assert result["lost_count"] == 3
    assert result["dropped_count"] == 3
    assert result["next_cursor"] == 5


def test_poll_cursor_at_dropped_boundary_does_not_double_count_loss(runtime):
    manager, backend, _clock = runtime
    created = _subscribe(manager, queue_limit=2)
    handle = next(iter(backend.attachments))
    for _ in range(4):
        backend.emit(handle, "StartRequest", object())

    result = manager.poll(created["subscription_id"], cursor=2)
    assert result["lost_count"] == 0
    assert [item["sequence"] for item in result["events"]] == [3, 4]


def test_sensitive_official_parameters_and_nested_secret_keys_are_redacted(runtime):
    manager, backend, _clock = runtime
    created = _subscribe(manager, event_names=["Change"])
    handle = next(iter(backend.attachments))
    backend.emit(
        handle,
        "Change",
        FakeCom("GuiSession", "ses[0]"),
        FakeCom("GuiTextField", "wnd[0]/usr/txtSECRET"),
        {"value": "ordinary", "password": "do-not-store"},
    )

    payload = manager.poll(created["subscription_id"])["events"][0]["payload"]
    assert payload["Session"]["id"] == "ses[0]"
    assert payload["Component"]["type"] == "GuiTextField"
    assert payload["CommandArray"] == "[REDACTED]"


def test_redaction_can_be_disabled_explicitly(runtime):
    manager, backend, _clock = runtime
    created = _subscribe(
        manager,
        event_names=["AbapScriptingEvent"],
        redact_sensitive=False,
    )
    handle = next(iter(backend.attachments))
    backend.emit(handle, "AbapScriptingEvent", {"token": "visible", "value": 7})

    payload = manager.poll(created["subscription_id"])["events"][0]["payload"]
    assert payload["param"] == {"token": "visible", "value": 7}


def test_unknown_or_wrong_scope_events_are_rejected_before_backend_attach(runtime):
    manager, backend, _clock = runtime
    with pytest.raises(ValueError, match="verified session catalog"):
        _subscribe(manager, event_names=["CreateSession"])
    with pytest.raises(ValueError, match="verified session catalog"):
        _subscribe(manager, event_names=["Remarks"])
    assert backend.attachments == {}


@pytest.mark.parametrize("events", [[], "StartRequest", b"StartRequest"])
def test_event_names_must_be_a_nonempty_sequence(runtime, events):
    manager, _backend, _clock = runtime
    with pytest.raises(ValueError, match="non-empty sequence"):
        _subscribe(manager, event_names=events)


def test_duplicate_event_names_are_canonicalized(runtime):
    manager, backend, _clock = runtime
    created = _subscribe(
        manager,
        event_names=["startrequest", "StartRequest", "ENDREQUEST"],
    )
    assert created["event_names"] == ["StartRequest", "EndRequest"]
    assert next(iter(backend.attachments.values()))["events"] == (
        "StartRequest", "EndRequest"
    )


def test_ttl_expiry_detaches_and_late_emit_is_ignored(runtime):
    manager, backend, clock = runtime
    created = _subscribe(manager, ttl_seconds=5)
    handle = next(iter(backend.attachments))
    late_emit = backend.attachments[handle]["emit"]

    clock.advance(5)
    assert manager.sweep_expired() == 1
    assert backend.detached == [handle]
    late_emit("StartRequest", (object(),), {})
    with pytest.raises(KeyError, match="unknown or expired"):
        manager.poll(created["subscription_id"])


def test_unsubscribe_is_idempotent_and_late_emit_is_ignored(runtime):
    manager, backend, _clock = runtime
    created = _subscribe(manager)
    handle = next(iter(backend.attachments))
    late_emit = backend.attachments[handle]["emit"]

    assert manager.unsubscribe(created["subscription_id"]) is True
    assert manager.unsubscribe(created["subscription_id"]) is False
    late_emit("StartRequest", (object(),), {})
    assert backend.detached == [handle]


def test_attach_failure_rolls_back_subscription(runtime):
    manager, backend, _clock = runtime
    backend.fail_attach = True
    with pytest.raises(RuntimeError, match="attach failed"):
        _subscribe(manager)
    assert manager.list_subscriptions() == []


def test_slot_cleanup_only_detaches_matching_slot(runtime):
    manager, backend, _clock = runtime
    first = _subscribe(manager, slot_id="ses0")
    second = _subscribe(manager, slot_id="ses1")

    assert manager.cleanup_slot("ses0") == 1
    assert [item["subscription_id"] for item in manager.list_subscriptions()] == [
        second["subscription_id"]
    ]
    assert manager.cleanup_slot("missing") == 0
    assert first["subscription_id"] != second["subscription_id"]
    assert len(backend.detached) == 1


def test_list_can_filter_by_slot_and_owner(runtime):
    manager, _backend, _clock = runtime
    one = _subscribe(manager, slot_id="ses0", owner="a")
    _subscribe(manager, slot_id="ses0", owner="b")
    _subscribe(manager, slot_id="ses1", owner="a")
    result = manager.list_subscriptions(slot_id="ses0", owner="a")
    assert [item["subscription_id"] for item in result] == [one["subscription_id"]]


def test_pump_delegates_and_expires(runtime):
    manager, backend, clock = runtime
    _subscribe(manager, ttl_seconds=2)
    clock.advance(2)
    manager.pump()
    assert backend.pump_count == 1
    assert manager.list_subscriptions() == []


def test_close_all_detaches_every_sink_and_prevents_reuse(runtime):
    manager, backend, _clock = runtime
    _subscribe(manager, slot_id="ses0")
    _subscribe(manager, slot_id="ses1")
    assert manager.close_all() == 2
    assert manager.close_all() == 0
    assert len(backend.detached) == 2
    manager.pump()
    assert backend.pump_count == 0
    with pytest.raises(RuntimeError, match="closed"):
        _subscribe(manager)


@pytest.mark.parametrize(
    "updates,match",
    [
        ({"slot_id": ""}, "slot_id"),
        ({"scope": "connection"}, "scope"),
        ({"ttl_seconds": 0}, "ttl_seconds"),
        ({"ttl_seconds": float("inf")}, "ttl_seconds"),
        ({"queue_limit": 0}, "queue_limit"),
        ({"queue_limit": True}, "queue_limit"),
    ],
)
def test_subscription_limits_are_validated(runtime, updates, match):
    manager, _backend, _clock = runtime
    with pytest.raises(ValueError, match=match):
        _subscribe(manager, **updates)


def test_poll_arguments_are_validated(runtime):
    manager, _backend, _clock = runtime
    created = _subscribe(manager)
    with pytest.raises(ValueError, match="cursor"):
        manager.poll(created["subscription_id"], cursor=-1)
    with pytest.raises(ValueError, match="limit"):
        manager.poll(created["subscription_id"], limit=0)


class FakeCom:
    def __init__(self, type_name, object_id):
        self.Type = type_name
        self.Id = object_id
        self.Name = object_id.rsplit("/", 1)[-1]
