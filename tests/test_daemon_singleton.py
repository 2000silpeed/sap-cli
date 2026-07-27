"""데몬 싱글턴 가드 회귀 테스트.

실사용 증상: 명령을 보낼 때마다 데몬 프로세스가 하나씩 늘어나 무한히 쌓인다.
원인은 세 가지가 겹친 것이다.
  1) 데몬은 무거운 초기화가 끝난 뒤에야 포트를 bind 하므로, 그 창(window)
     동안 클라이언트에게는 계속 "refused"로 보인다.
  2) refused 면 확인 없이 새 프로세스를 띄운다.
  3) Windows 의 SO_REUSEADDR 는 중복 bind 를 허용하므로, 늦게 뜬 데몬도
     "포트 사용 중"으로 죽지 않고 살아남는다.

여기서는 (2)를 막는 싱글턴 락 동작을 고정한다.
"""

from __future__ import annotations

import pytest

from sap_cli import daemon


@pytest.fixture(autouse=True)
def isolated_lock(monkeypatch, tmp_path):
    monkeypatch.setenv("SAP_CLI_DAEMON_LOCK", str(tmp_path / "daemon.lock"))
    monkeypatch.setenv("SAP_CLI_DAEMON_LOG", str(tmp_path / "daemon.log"))


def test_lock_is_exclusive_and_released_on_close():
    first = daemon.acquire_singleton_lock()
    assert first is not None
    try:
        assert daemon.acquire_singleton_lock() is None
        assert daemon.daemon_start_in_progress() is True
    finally:
        daemon.release_singleton_lock(first)

    assert daemon.daemon_start_in_progress() is False


def test_does_not_spawn_second_daemon_while_one_is_starting(monkeypatch):
    """이미 기동 중인 데몬이 있으면 새 프로세스를 띄우지 않는다."""
    monkeypatch.setattr(daemon, "probe_daemon", lambda timeout=None: "refused")

    spawned: list = []
    monkeypatch.setattr(
        daemon.subprocess, "Popen", lambda *a, **k: spawned.append(a) or _DeadProc()
    )

    held = daemon.acquire_singleton_lock()
    assert held is not None
    try:
        ok, err = daemon.start_daemon_detailed(timeout=0.5)
    finally:
        daemon.release_singleton_lock(held)

    assert ok is False
    assert spawned == [], "중복 데몬을 띄웠다"
    assert err is not None and "중복 기동" in err


def test_waits_for_the_starting_daemon_instead_of_spawning(monkeypatch):
    """기동 중이던 데몬이 준비되면 새로 띄우지 않고 그대로 성공 처리한다."""
    states = ["refused", "refused", "alive"]
    monkeypatch.setattr(
        daemon, "probe_daemon", lambda timeout=None: states.pop(0) if states else "alive"
    )

    spawned: list = []
    monkeypatch.setattr(
        daemon.subprocess, "Popen", lambda *a, **k: spawned.append(a) or _DeadProc()
    )

    held = daemon.acquire_singleton_lock()
    assert held is not None
    try:
        ok, err = daemon.start_daemon_detailed(timeout=5)
    finally:
        daemon.release_singleton_lock(held)

    assert ok is True
    assert err is None
    assert spawned == []


def test_spawns_when_no_daemon_holds_the_lock(monkeypatch):
    """락이 비어 있으면 종전대로 새 데몬을 띄운다 (가드가 정상 기동을 막지 않음)."""
    monkeypatch.setattr(daemon, "probe_daemon", lambda timeout=None: "refused")

    spawned: list = []
    monkeypatch.setattr(
        daemon.subprocess, "Popen", lambda *a, **k: spawned.append(a) or _DeadProc()
    )

    ok, _ = daemon.start_daemon_detailed(timeout=0.5)

    assert ok is False  # _DeadProc 이므로 기동 실패는 정상
    assert len(spawned) == 1


def test_server_entrypoint_exits_before_heavy_init_when_lock_is_held(monkeypatch):
    """중복 데몬은 COM 초기화를 시작하기 전에 즉시 물러난다."""

    def _boom():
        raise AssertionError("중복 데몬이 runtime 초기화를 시작했다")

    monkeypatch.setattr(daemon, "_DaemonServer", lambda *a, **k: _boom())

    held = daemon.acquire_singleton_lock()
    assert held is not None
    try:
        daemon._run_server()  # 예외 없이 즉시 반환해야 한다
    finally:
        daemon.release_singleton_lock(held)


def test_doctor_reports_lock_state():
    report = daemon.doctor()
    assert "singleton_lock_held" in report


class _DeadProc:
    def poll(self):
        return 1
