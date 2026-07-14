from __future__ import annotations

from types import SimpleNamespace

import pytest

from sap_cli.dispatchers.events import dispatch_event_command
from sap_cli.events.manager import EventSubscriptionManager
from sap_cli.events.runtime import set_event_manager


class FakeBackend:
    def __init__(self) -> None:
        self.emitters = {}
        self.next_handle = 1

    def attach(self, target, event_names, emit):
        handle = self.next_handle
        self.next_handle += 1
        self.emitters[handle] = emit
        return handle

    def detach(self, handle):
        self.emitters.pop(handle, None)

    def pump(self):
        return None


class FakeWrapper:
    def __init__(self) -> None:
        self._application = object()
        self._session = object()

    @property
    def session(self):
        return self._session


@pytest.fixture
def event_runtime():
    backend = FakeBackend()
    manager = EventSubscriptionManager(backend, id_factory=lambda: "sub-1")
    set_event_manager(manager)
    yield manager, backend
    manager.close_all()
    set_event_manager(None)


def _ctx(owner="agent-a"):
    return SimpleNamespace(slot_id="ses0", agent_name=owner)


def test_event_dispatcher_subscribe_poll_list_and_unsubscribe(event_runtime):
    manager, backend = event_runtime
    wrapper = FakeWrapper()
    created = dispatch_event_command(
        "event_subscribe",
        {"scope": "session", "events": ["StartRequest"]},
        _ctx(),
        get_sap=lambda slot_id: wrapper,
    )
    backend.emitters[1]("StartRequest", (object(),), {})

    polled = dispatch_event_command(
        "event_poll",
        {"subscription_id": created["subscription_id"]},
        _ctx(),
        get_sap=lambda slot_id: wrapper,
    )
    listed = dispatch_event_command(
        "event_list", {}, _ctx(), get_sap=lambda slot_id: wrapper
    )
    removed = dispatch_event_command(
        "event_unsubscribe",
        {"subscription_id": created["subscription_id"]},
        _ctx(),
        get_sap=lambda slot_id: wrapper,
    )

    assert polled["events"][0]["event_name"] == "StartRequest"
    assert listed["subscriptions"][0]["owner"] == "agent-a"
    assert removed["unsubscribed"] is True
    assert manager.list_subscriptions() == []


def test_event_dispatcher_prevents_cross_owner_access(event_runtime):
    wrapper = FakeWrapper()
    created = dispatch_event_command(
        "event_subscribe",
        {"scope": "session", "events": ["EndRequest"]},
        _ctx("owner-a"),
        get_sap=lambda slot_id: wrapper,
    )
    with pytest.raises(ValueError, match="owner mismatch"):
        dispatch_event_command(
            "event_poll",
            {"subscription_id": created["subscription_id"]},
            _ctx("owner-b"),
            get_sap=lambda slot_id: wrapper,
        )


def test_event_dispatcher_requires_daemon_runtime():
    set_event_manager(None)
    with pytest.raises(RuntimeError, match="persistent daemon"):
        dispatch_event_command(
            "event_list", {}, _ctx(), get_sap=lambda slot_id: FakeWrapper()
        )
