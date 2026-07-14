from __future__ import annotations

from types import SimpleNamespace

from sap_cli.daemon import _DaemonServer


class FakeManager:
    def __init__(self) -> None:
        self.pumps = 0
        self.cleaned = []

    def pump(self) -> None:
        self.pumps += 1

    def list_subscriptions(self):
        return [{"slot_id": "live"}, {"slot_id": "expired"}]

    def cleanup_slot(self, slot_id):
        self.cleaned.append(slot_id)


def test_daemon_idle_pump_cleans_subscriptions_for_released_slots(monkeypatch):
    manager = FakeManager()
    server = _DaemonServer.__new__(_DaemonServer)
    server._event_manager = manager
    pool = SimpleNamespace(has_slot=lambda slot_id: slot_id == "live")
    monkeypatch.setattr("sap_cli.service.get_pool", lambda: pool)

    server.service_actions()

    assert manager.pumps == 1
    assert manager.cleaned == ["expired"]


def test_daemon_idle_service_advances_one_ui_map_scheduler_tick():
    server = _DaemonServer.__new__(_DaemonServer)
    server._event_manager = None
    calls = []
    server._ui_map_scheduler = SimpleNamespace(tick=lambda: calls.append("tick"))

    server.service_actions()

    assert calls == ["tick"]
