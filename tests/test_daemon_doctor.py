"""데몬 기동 하드닝 + doctor 헬스체크 회귀 테스트.

세 가지 실사용 증상을 고정한다.
1) 자동 기동 실패가 조용히 삼켜지지 않고, 원인(로그 tail)과 함께 빠르게 실패한다.
2) MCP에 데몬 상태를 확인하는 도구(sap_daemon_status)가 존재한다.
3) 그 도구는 데몬으로 위임되지 않고 호출자 프로세스에서 로컬로 진단한다
   (데몬이 죽어 있어도 실행 환경/venv를 진단할 수 있어야 하므로).
"""

from __future__ import annotations

import json

from sap_cli import daemon
from sap_cli import mcp_server


def test_doctor_reports_execution_environment():
    report = daemon.doctor()
    for key in (
        "python_executable",
        "prefix",
        "in_venv",
        "cwd",
        "platform",
        "port",
        "log_path",
        "sap_cli_path",
        "pywin32",
        "mcp",
        "daemon_running",
    ):
        assert key in report, key


def test_start_daemon_detailed_fails_fast_with_reason_when_child_dies(monkeypatch, tmp_path):
    """자식이 준비 전에 죽으면 예산을 소진하지 않고 로그 tail을 원인으로 돌려준다."""
    log = tmp_path / "daemon.log"
    log.write_text("ModuleNotFoundError: No module named 'sap_cli'\n", encoding="utf-8")
    monkeypatch.setenv("SAP_CLI_DAEMON_LOG", str(log))
    monkeypatch.setattr(daemon, "is_running", lambda timeout=2.0: False)

    class _DeadProc:
        def poll(self):
            return 1  # 이미 종료됨

    monkeypatch.setattr(daemon.subprocess, "Popen", lambda *a, **k: _DeadProc())

    ok, err = daemon.start_daemon_detailed(timeout=5)

    assert ok is False
    assert err is not None
    assert "exit=1" in err
    assert "No module named" in err  # daemon.log tail 이 원인에 포함


def test_start_daemon_wrapper_returns_bool(monkeypatch):
    monkeypatch.setattr(daemon, "start_daemon_detailed", lambda timeout=None: (False, "nope"))
    assert daemon.start_daemon() is False


def test_daemon_status_is_exposed_as_mcp_tool():
    names = {definition["name"] for definition in mcp_server.TOOL_DEFINITIONS}
    assert "sap_daemon_status" in names
    assert "sap_daemon_status" in mcp_server.COMPACT_TOOL_NAMES


def test_restart_waits_for_full_stop_before_starting(monkeypatch):
    """restart는 stop → 포트 완전 종료 대기 → start 순서를 지켜야 한다.

    shutdown 응답 후에도 데몬은 잠시 살아 있으므로, 대기 없이 start 하면
    죽어가는 데몬의 ping 응답을 already-running으로 오판한다 (실측 재현된 레이스).
    """
    from sap_cli.dispatcher import _dispatch_daemon_status, DispatchContext

    order: list[str] = []
    monkeypatch.setattr(daemon, "stop_daemon", lambda: order.append("stop") or True)
    monkeypatch.setattr(
        daemon, "wait_until_stopped", lambda timeout=5.0: order.append("wait") or True
    )
    monkeypatch.setattr(
        daemon,
        "start_daemon_detailed",
        lambda timeout=None: order.append("start") or (True, None),
    )
    monkeypatch.setattr(daemon, "doctor", lambda: {"daemon_running": True})

    result = _dispatch_daemon_status({"action": "restart"}, DispatchContext())

    assert order == ["stop", "wait", "start"]
    assert result["restart"] == {"ok": True, "error": None}


def test_mcp_daemon_status_runs_locally_and_is_never_delegated(monkeypatch):
    """sap_daemon_status는 daemon 위임 경로를 타지 않고 로컬 doctor를 반환한다."""
    spy = {"delegated": False}

    def _fake_delegate(*args, **kwargs):
        spy["delegated"] = True
        return {"ok": True, "data": {"delegated": True}}

    monkeypatch.setenv("SAP_CLI_USE_DAEMON", "1")
    monkeypatch.setattr(mcp_server, "_dispatch_via_daemon", _fake_delegate)
    monkeypatch.setattr(
        daemon, "doctor", lambda: {"daemon_running": False, "sentinel": "local"}
    )

    result = mcp_server.handle_tool("sap_daemon_status", {"action": "status"})
    data = json.loads(result[0].text)

    assert spy["delegated"] is False
    assert data.get("sentinel") == "local"
