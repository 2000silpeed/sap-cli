from __future__ import annotations

import socket
import threading

import pytest

import sap_cli.daemon as daemon
from sap_cli.ui_map.scheduler import UiMapScheduler


def _connect_refused() -> bool:
    """True if the daemon port refuses a connection right now."""
    probe = socket.socket()
    probe.settimeout(0.3)
    try:
        probe.connect(("127.0.0.1", daemon.PORT))
        probe.close()
        return False
    except (ConnectionRefusedError, OSError):
        return True


def test_port_stays_closed_until_init_finishes(monkeypatch, tmp_path):
    """The listener must not accept connections while __init__ is still setting
    up runtime state. Binding early leaves clients blocked on recv until their
    command timeout — the "daemon hangs until timeout on first launch" bug."""
    monkeypatch.setenv("SAP_CLI_PLAN_JOB_DIR", str(tmp_path / "plan_jobs"))
    monkeypatch.setenv("SAP_CLI_UI_MAP_DIR", str(tmp_path / "ui_maps"))
    monkeypatch.setenv("SAP_CLI_UI_MAP_JOB_DIR", str(tmp_path / "ui_map_jobs"))

    if not _connect_refused():
        pytest.skip("a daemon is already bound to the port")

    entered = threading.Event()
    release = threading.Event()

    original = UiMapScheduler.hydrate_persisted_jobs

    def gated(self, *args, **kwargs):
        # Signal that we are mid-__init__, then hold here until the probe ran.
        entered.set()
        assert release.wait(timeout=5)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(UiMapScheduler, "hydrate_persisted_jobs", gated)

    built: dict[str, object] = {}

    def build() -> None:
        built["server"] = daemon._DaemonServer()

    thread = threading.Thread(target=build)
    thread.start()
    try:
        assert entered.wait(timeout=5), "daemon __init__ never reached setup"
        # Port must be refused while runtime state is still being built.
        assert _connect_refused(), "port accepted a connection mid-init"
        release.set()
        thread.join(timeout=5)
        assert not thread.is_alive()
        # Once __init__ returns the listener is live.
        assert not _connect_refused(), "port never opened after init finished"
    finally:
        release.set()
        thread.join(timeout=5)
        server = built.get("server")
        if server is not None:
            # close_events() resets the process-global runtime singletons the
            # daemon registered (plan runtime, job coordinator, ui-map store,
            # …); server_close() alone would leak them into later tests.
            server.close_events()
            server.server_close()
