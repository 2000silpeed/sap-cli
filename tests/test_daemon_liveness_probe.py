from __future__ import annotations

import socket

import pytest

import sap_cli.daemon as daemon


@pytest.fixture(autouse=True)
def _fast_probe(monkeypatch):
    # Keep the port probe short so the wedged-port cases stay fast in tests.
    monkeypatch.setattr(daemon, "DAEMON_PROBE_TIMEOUT", 0.3)


@pytest.fixture
def _no_daemon():
    if daemon.probe_daemon(timeout=0.3) != "refused":
        pytest.skip("something is already bound to the daemon port")
    yield


def test_probe_refused_when_nothing_listening(_no_daemon):
    assert daemon.probe_daemon(timeout=0.3) == "refused"
    assert daemon.is_running() is False


def test_probe_unresponsive_when_port_held_without_reply(_no_daemon):
    # A bare listener accepts the connection into its backlog but never replies,
    # exactly like a daemon still initialising or wedged on a blocking call.
    held = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    held.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    held.bind(("127.0.0.1", daemon.PORT))
    held.listen(128)
    try:
        assert daemon.probe_daemon(timeout=0.3) == "unresponsive"
        assert daemon.is_running() is False
    finally:
        held.close()


def test_ensure_daemon_bails_fast_on_unresponsive_port(_no_daemon):
    held = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    held.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    held.bind(("127.0.0.1", daemon.PORT))
    held.listen(128)
    spawned = []
    try:
        # Must not try to spawn a second daemon (it could never bind the port),
        # and must not block on the command timeout.
        import sap_cli.daemon as d

        original = d._spawn_and_wait
        d._spawn_and_wait = lambda: spawned.append(1) or True
        try:
            assert d.ensure_daemon() is False
            assert d.start_daemon() is False
        finally:
            d._spawn_and_wait = original
        assert spawned == []
    finally:
        held.close()


def test_ensure_daemon_refused_respects_autostart_flag(_no_daemon, monkeypatch):
    spawned = []
    monkeypatch.setattr(daemon, "_spawn_and_wait", lambda: spawned.append(1) or True)

    # autostart off → do not launch anything.
    assert daemon.ensure_daemon(autostart=False) is False
    assert spawned == []

    # autostart on → launch (stubbed).
    assert daemon.ensure_daemon(autostart=True) is True
    assert spawned == [1]
