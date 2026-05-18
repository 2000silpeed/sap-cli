"""MCP → Daemon 위임 모드 테스트.

Phase 13: SAP_CLI_USE_DAEMON=1 일 때 MCP handle_tool이
daemon TCP 소켓으로 명령을 위임하는 경로 검증.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import pytest

# MCP 의존성이 없는 환경에서도 테스트 가능하도록 TextContent 모킹
try:
    from mcp.types import TextContent
    HAS_MCP = True
except ImportError:
    HAS_MCP = False

    @dataclass
    class TextContent:
        type: str
        text: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _patch_mcp_deps():
    """mcp 패키지가 없을 때 handle_tool을 import할 수 있도록 모킹."""
    if HAS_MCP:
        return {}
    import types
    mock_mcp = types.ModuleType("mcp")
    mock_types = types.ModuleType("mcp.types")
    mock_types.TextContent = type("TextContent", (), {
        "__init__": lambda self, **kw: setattr(self, "__dict__", kw) or None,
    })
    mock_mcp.types = mock_types
    return {"mcp": mock_mcp, "mcp.types": mock_types}


# ---------------------------------------------------------------------------
# daemon.send_command context 전달 테스트
# ---------------------------------------------------------------------------

class TestDaemonSendCommandContext:
    """send_command가 context를 TCP 페이로드에 포함하는지 검증."""

    def test_send_command_includes_context(self):
        import json
        from unittest.mock import patch, MagicMock

        mock_sock = MagicMock()
        received_data = []

        def capture_sendall(data):
            received_data.append(data)

        mock_sock.sendall = capture_sendall
        mock_sock.recv = MagicMock(side_effect=[
            json.dumps({"ok": True, "data": {"test": 1}}).encode(),
            b"",
        ])

        with patch("socket.socket", return_value=mock_sock):
            from sap_cli.daemon import send_command
            result = send_command(
                "screen",
                {"tcode": "MM03"},
                context={"source": "mcp", "tool_name": "sap_screen"},
            )

        assert result is not None
        assert result["ok"] is True
        sent = json.loads(received_data[0].decode().strip())
        assert sent["cmd"] == "screen"
        assert sent["context"]["source"] == "mcp"
        assert sent["context"]["tool_name"] == "sap_screen"

    def test_send_command_without_context(self):
        import json
        from unittest.mock import patch, MagicMock

        mock_sock = MagicMock()
        received_data = []
        mock_sock.sendall = lambda d: received_data.append(d)
        mock_sock.recv = MagicMock(side_effect=[
            json.dumps({"ok": True, "data": {}}).encode(),
            b"",
        ])

        with patch("socket.socket", return_value=mock_sock):
            from sap_cli.daemon import send_command
            send_command("ping")

        sent = json.loads(received_data[0].decode().strip())
        assert "context" not in sent


# ---------------------------------------------------------------------------
# DaemonServer.dispatch context 수신 테스트
# ---------------------------------------------------------------------------

class TestDaemonServerDispatchContext:
    """daemon 서버가 context dict를 DispatchContext로 변환하는지 검증."""

    def test_dispatch_with_context(self):
        from sap_cli.daemon import _DaemonServer
        from sap_cli.dispatcher import DispatchContext

        captured_ctx = []

        def mock_dispatch_command(cmd, args, ctx):
            captured_ctx.append(ctx)
            return {"status": "ok"}

        with patch("sap_cli.daemon.dispatch_command", mock_dispatch_command):
            with patch.object(_DaemonServer, "__init__", lambda self: None):
                server = _DaemonServer()
                server._sap = None
                result = server.dispatch(
                    "screen",
                    {"tcode": "MM03"},
                    {
                        "source": "mcp",
                        "tool_name": "sap_screen",
                        "agent_name": "op",
                        "include_post_action_context": False,
                        "include_screen_summary": True,
                    },
                )

        assert result["ok"] is True
        ctx = captured_ctx[0]
        assert ctx.source == "mcp"
        assert ctx.tool_name == "sap_screen"
        assert ctx.agent_name == "op"
        assert ctx.include_post_action_context is False
        assert ctx.include_screen_summary is True

    def test_dispatch_without_context_defaults_to_daemon(self):
        from sap_cli.daemon import _DaemonServer

        captured_ctx = []

        def mock_dispatch_command(cmd, args, ctx):
            captured_ctx.append(ctx)
            return {"status": "ok"}

        with patch("sap_cli.daemon.dispatch_command", mock_dispatch_command):
            with patch.object(_DaemonServer, "__init__", lambda self: None):
                server = _DaemonServer()
                server._sap = None
                server.dispatch("status", {}, None)

        assert captured_ctx[0].source == "daemon"


# ---------------------------------------------------------------------------
# MCP handle_tool daemon 위임 테스트
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _ensure_textcontent(monkeypatch):
    """mcp 패키지 없어도 TextContent가 동작하도록 보장."""
    if not HAS_MCP:
        import sap_cli.mcp_server as _mod
        monkeypatch.setattr(_mod, "TextContent", TextContent, raising=False)
        monkeypatch.setattr(_mod, "HAS_MCP", True, raising=False)


class TestMcpHandleToolDaemon:
    """handle_tool이 SAP_CLI_USE_DAEMON=1일 때 daemon으로 위임."""

    def test_daemon_mode_delegates_to_daemon_by_default(self, monkeypatch):
        monkeypatch.delenv("SAP_CLI_USE_DAEMON", raising=False)
        monkeypatch.setattr("sap_cli.mcp_server.load_policy", lambda: None)

        daemon_response = {"ok": True, "data": {"transaction": "MM03", "screen_number": 100}}

        with patch("sap_cli.mcp_server._dispatch_via_daemon", return_value=daemon_response) as mock_daemon:
            from sap_cli.mcp_server import handle_tool
            result = handle_tool("sap_screen", {"tcode": "MM03"})

        mock_daemon.assert_called_once()
        # 결과가 TextContent 리스트
        assert len(result) == 1
        import json
        data = json.loads(result[0].text)
        assert data["transaction"] == "MM03"

    def test_daemon_mode_fallback_on_failure(self, monkeypatch):
        """daemon 위임 실패 시 직접 dispatch로 fallback."""
        monkeypatch.setenv("SAP_CLI_USE_DAEMON", "1")
        monkeypatch.setattr("sap_cli.mcp_server.load_policy", lambda: None)

        # daemon 실패 (None 반환)
        with patch("sap_cli.mcp_server._dispatch_via_daemon", return_value=None):
            with patch("sap_cli.mcp_server.dispatch_command", return_value={"fallback": True}) as mock_direct:
                from sap_cli.mcp_server import handle_tool
                result = handle_tool("sap_session", {})

        mock_direct.assert_called_once()
        import json
        data = json.loads(result[0].text)
        assert data["fallback"] is True

    def test_daemon_mode_off_uses_direct(self, monkeypatch):
        """SAP_CLI_USE_DAEMON=0이면 daemon 위임 안 함."""
        monkeypatch.setenv("SAP_CLI_USE_DAEMON", "0")
        monkeypatch.setattr("sap_cli.mcp_server.load_policy", lambda: None)

        with patch("sap_cli.mcp_server._dispatch_via_daemon") as mock_daemon:
            with patch("sap_cli.mcp_server.dispatch_command", return_value={"direct": True}):
                from sap_cli.mcp_server import handle_tool
                handle_tool("sap_session", {})

        mock_daemon.assert_not_called()

    def test_daemon_mode_approval_required(self, monkeypatch):
        """daemon이 approval_required 반환 시 올바르게 전달."""
        monkeypatch.setenv("SAP_CLI_USE_DAEMON", "1")
        monkeypatch.setattr("sap_cli.mcp_server.load_policy", lambda: None)

        daemon_response = {
            "ok": False,
            "approval_required": True,
            "tool": "sap_command",
            "reason": "dangerous transaction",
        }
        with patch("sap_cli.mcp_server._dispatch_via_daemon", return_value=daemon_response):
            from sap_cli.mcp_server import handle_tool
            result = handle_tool("sap_command", {"cmd": "/nSE16"})

        import json
        data = json.loads(result[0].text)
        assert data["approval_required"] is True
        assert data["tool"] == "sap_command"

    def test_daemon_mode_error(self, monkeypatch):
        """daemon이 에러 반환 시 올바르게 전달."""
        monkeypatch.setenv("SAP_CLI_USE_DAEMON", "1")
        monkeypatch.setattr("sap_cli.mcp_server.load_policy", lambda: None)

        daemon_response = {"ok": False, "error": "COM disconnected"}
        with patch("sap_cli.mcp_server._dispatch_via_daemon", return_value=daemon_response):
            from sap_cli.mcp_server import handle_tool
            result = handle_tool("sap_get_status", {})

        import json
        data = json.loads(result[0].text)
        assert data["error"] is True
        assert "COM disconnected" in data["message"]

    def test_daemon_mode_fallback_on_unknown_command(self, monkeypatch):
        """오래 떠 있던 daemon이 새 명령을 모르면 직접 dispatch로 fallback."""
        monkeypatch.setenv("SAP_CLI_USE_DAEMON", "1")
        monkeypatch.setattr("sap_cli.mcp_server.load_policy", lambda: None)

        daemon_response = {"ok": False, "error": "알수 없는 명령: inspect"}
        with patch("sap_cli.mcp_server._dispatch_via_daemon", return_value=daemon_response):
            with patch("sap_cli.mcp_server.dispatch_command", return_value={"fallback": True}) as mock_direct:
                from sap_cli.mcp_server import handle_tool
                result = handle_tool("sap_inspect_element", {"element_id": "wnd[0]/usr/cmbTEST"})

        mock_direct.assert_called_once()
        import json
        data = json.loads(result[0].text)
        assert data["fallback"] is True


# ---------------------------------------------------------------------------
# _dispatch_via_daemon 단위 테스트
# ---------------------------------------------------------------------------

class TestDispatchViaDaemon:
    """_dispatch_via_daemon 함수의 daemon 자동 시작 + fallback."""

    def test_auto_starts_daemon(self, monkeypatch):
        monkeypatch.setenv("SAP_CLI_MCP_SCREEN_SUMMARY", "1")
        started = []

        with patch("sap_cli.daemon.is_running", return_value=False):
            with patch("sap_cli.daemon.start_daemon", side_effect=lambda: started.append(1) or True):
                with patch("sap_cli.daemon.send_command", return_value={"ok": True, "data": {}}):
                    from sap_cli.mcp_server import _dispatch_via_daemon
                    result = _dispatch_via_daemon("status", {}, "sap_status", None)

        assert len(started) == 1
        assert result is not None

    def test_returns_none_when_daemon_start_fails(self, monkeypatch):
        monkeypatch.setenv("SAP_CLI_MCP_SCREEN_SUMMARY", "0")

        with patch("sap_cli.daemon.is_running", return_value=False):
            with patch("sap_cli.daemon.start_daemon", return_value=False):
                from sap_cli.mcp_server import _dispatch_via_daemon
                result = _dispatch_via_daemon("status", {}, "sap_status", None)

        assert result is None

    def test_passes_context_to_daemon(self, monkeypatch):
        monkeypatch.setenv("SAP_CLI_MCP_SCREEN_SUMMARY", "1")
        captured = []

        def mock_send(cmd, args, timeout=60, context=None):
            captured.append(context)
            return {"ok": True, "data": {}}

        with patch("sap_cli.daemon.is_running", return_value=True):
            with patch("sap_cli.daemon.send_command", mock_send):
                from sap_cli.mcp_server import _dispatch_via_daemon
                _dispatch_via_daemon("screen", {"tcode": "VA01"}, "sap_screen", "operator")

        ctx = captured[0]
        assert ctx["source"] == "mcp"
        assert ctx["tool_name"] == "sap_screen"
        assert ctx["agent_name"] == "operator"
        assert ctx["include_post_action_context"] is True
        assert ctx["include_screen_summary"] is True

    def test_connect_uses_fast_context(self, monkeypatch):
        monkeypatch.setenv("SAP_CLI_MCP_SCREEN_SUMMARY", "1")
        captured = []

        def mock_send(cmd, args, timeout=60, context=None):
            captured.append(context)
            return {"ok": True, "data": {"connected": True}}

        with patch("sap_cli.daemon.is_running", return_value=True):
            with patch("sap_cli.daemon.send_command", mock_send):
                from sap_cli.mcp_server import _dispatch_via_daemon
                result = _dispatch_via_daemon("connect", {}, "sap_connect", "operator")

        assert result == {"ok": True, "data": {"connected": True}}
        ctx = captured[0]
        assert ctx["source"] == "mcp"
        assert ctx["tool_name"] == "sap_connect"
        assert ctx["agent_name"] == "operator"
        assert ctx["include_post_action_context"] is False
        assert ctx["include_screen_summary"] is False
