from __future__ import annotations

import socket
import threading

import pytest

import sap_cli.daemon as daemon


def _port_free() -> bool:
    probe = socket.socket()
    probe.settimeout(0.3)
    try:
        probe.connect(("127.0.0.1", daemon.PORT))
        probe.close()
        return False
    except OSError:
        return True


@pytest.fixture
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("SAP_CLI_PLAN_JOB_DIR", str(tmp_path / "plan_jobs"))
    monkeypatch.setenv("SAP_CLI_UI_MAP_DIR", str(tmp_path / "ui_maps"))
    monkeypatch.setenv("SAP_CLI_UI_MAP_JOB_DIR", str(tmp_path / "ui_map_jobs"))
    if not _port_free():
        pytest.skip("a daemon is already bound to the port")
    yield


def _make_server():
    server = daemon._DaemonServer()
    return server


def test_worker_disabled_by_default_keeps_inline_owner_thread(_isolated):
    server = _make_server()
    try:
        assert server._com_worker is None
        # Owner thread is the constructing (main) thread — unchanged behaviour.
        assert server._plan_runtime.owner_thread_id == threading.get_ident()
        # _run_com executes inline when there is no worker.
        assert server._run_com(lambda: threading.get_ident()) == threading.get_ident()
    finally:
        server.close_events()
        server.server_close()


def test_worker_enabled_pins_com_state_to_the_worker_thread(monkeypatch, _isolated):
    monkeypatch.setenv("SAP_CLI_COM_WORKER", "1")
    server = _make_server()
    try:
        worker = server._com_worker
        assert worker is not None and worker.ident is not None
        assert worker.ident != threading.get_ident()
        # Owner-thread-bound state is pinned to the worker so plan/approval
        # operations run on the same STA thread as SAP COM.
        assert server._plan_runtime.owner_thread_id == worker.ident
        assert server._plan_approval_store._owner_thread_id == worker.ident
        # Command execution is marshalled onto the worker thread.
        assert server._run_com(lambda: threading.get_ident()) == worker.ident
    finally:
        server.close_events()
        server.server_close()
        assert server._com_worker is None  # stopped on close
