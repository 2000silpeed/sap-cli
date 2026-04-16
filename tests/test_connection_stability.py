"""Phase 16: 연결 안정성 테스트.

멀티 시스템 연결(SID 탐색) + 세션 자동 복구 검증.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

from sap_cli.com_wrapper import SapGuiError, SapGuiWrapper


def _make_session_info(system_name="PR1", client="100", user="TESTUSER"):
    info = SimpleNamespace(
        User=user,
        Language="KO",
        Transaction="SESSION_MANAGER",
        ScreenNumber=100,
        Program="SAPMSYST",
        SystemName=system_name,
        Client=client,
        ApplicationServer="sapserver01",
    )
    session = MagicMock()
    session.Info = info
    session.Id = "/app/con[0]/ses[0]"
    return session


def _make_connection(system_name="PR1", client="100", user="TESTUSER", session_count=1):
    conn = MagicMock()
    conn.Description = f"{system_name} - SAP System"
    sessions = []
    for i in range(session_count):
        sessions.append(_make_session_info(system_name, client, user))
    conn.Sessions.Count = session_count
    conn.Sessions.side_effect = lambda idx: sessions[idx]
    return conn


def _make_application(*connections):
    app = MagicMock()
    app.Connections.Count = len(connections)
    app.Connections.side_effect = lambda idx: connections[idx]
    return app


def _connected_wrapper(system_name="PR1", system=None):
    """connect()가 완료된 wrapper를 반환한다."""
    conn = _make_connection(system_name)
    app = _make_application(conn)
    wrapper = SapGuiWrapper()
    wrapper._application = app
    with patch.object(wrapper, "_get_application", return_value=app):
        wrapper.connect(system=system)
    return wrapper, app


# ---------------------------------------------------------------------------
# list_connections
# ---------------------------------------------------------------------------

class TestListConnections:
    def test_single_connection(self):
        wrapper = SapGuiWrapper()
        conn = _make_connection("PR1", "100", "USER1")
        wrapper._application = _make_application(conn)

        result = wrapper.list_connections()
        assert result["connection_count"] == 1
        assert len(result["connections"]) == 1
        assert result["connections"][0]["system_name"] == "PR1"
        assert result["connections"][0]["client"] == "100"
        assert result["connections"][0]["index"] == 0

    def test_multiple_connections(self):
        wrapper = SapGuiWrapper()
        conn1 = _make_connection("PR1", "100", "USER1")
        conn2 = _make_connection("ERP", "200", "USER2")
        wrapper._application = _make_application(conn1, conn2)

        result = wrapper.list_connections()
        assert result["connection_count"] == 2
        systems = [c["system_name"] for c in result["connections"]]
        assert systems == ["PR1", "ERP"]

    def test_sessions_detail(self):
        wrapper = SapGuiWrapper()
        conn = _make_connection("PR1", "100", "USER1", session_count=2)
        wrapper._application = _make_application(conn)

        result = wrapper.list_connections()
        assert result["connections"][0]["session_count"] == 2
        assert len(result["connections"][0]["sessions"]) == 2

    def test_connection_with_zero_sessions(self):
        wrapper = SapGuiWrapper()
        conn = _make_connection("PR1", session_count=0)
        wrapper._application = _make_application(conn)

        result = wrapper.list_connections()
        assert result["connections"][0]["session_count"] == 0


# ---------------------------------------------------------------------------
# find_connection_by_system
# ---------------------------------------------------------------------------

class TestFindConnectionBySystem:
    def test_find_by_system_name(self):
        wrapper = SapGuiWrapper()
        conn1 = _make_connection("PR1")
        conn2 = _make_connection("ERP")
        wrapper._application = _make_application(conn1, conn2)

        assert wrapper._find_connection_by_system("ERP") == 1
        assert wrapper._find_connection_by_system("PR1") == 0

    def test_find_case_insensitive(self):
        wrapper = SapGuiWrapper()
        conn = _make_connection("PR1")
        wrapper._application = _make_application(conn)

        assert wrapper._find_connection_by_system("pr1") == 0

    def test_find_by_description(self):
        wrapper = SapGuiWrapper()
        conn = _make_connection("PR1")
        conn.Description = "Production System PR1"
        conn.Sessions(0).Info.SystemName = "XYZ"
        wrapper._application = _make_application(conn)

        assert wrapper._find_connection_by_system("PR1") == 0

    def test_not_found(self):
        wrapper = SapGuiWrapper()
        conn = _make_connection("PR1")
        wrapper._application = _make_application(conn)

        assert wrapper._find_connection_by_system("QAS") is None

    def test_system_name_takes_priority_over_description(self):
        """SystemName 정확 매칭이 Description 부분 매칭보다 우선한다."""
        wrapper = SapGuiWrapper()
        conn1 = _make_connection("AAA")
        conn1.Description = "System with ERP in description"
        conn2 = _make_connection("ERP")
        conn2.Description = "Other System"
        wrapper._application = _make_application(conn1, conn2)

        assert wrapper._find_connection_by_system("ERP") == 1


# ---------------------------------------------------------------------------
# connect with system parameter
# ---------------------------------------------------------------------------

class TestConnectWithSystem:
    def test_connect_by_system_id(self):
        conn1 = _make_connection("PR1")
        conn2 = _make_connection("ERP")
        app = _make_application(conn1, conn2)

        wrapper = SapGuiWrapper()
        wrapper._application = app

        with patch.object(wrapper, "_get_application", return_value=app):
            info = wrapper.connect(system="ERP")

        assert info.system_name == "ERP"
        assert wrapper._last_system_id == "ERP"
        assert wrapper._last_connection_index == 1

    def test_connect_system_not_found(self):
        conn = _make_connection("PR1")
        app = _make_application(conn)

        wrapper = SapGuiWrapper()
        wrapper._application = app

        with patch.object(wrapper, "_get_application", return_value=app):
            with pytest.raises(SapGuiError, match="찾을 수 없습니다"):
                wrapper.connect(system="QAS")

    def test_connect_saves_identity(self):
        wrapper, app = _connected_wrapper("PR1")

        assert wrapper._connected_identity is not None
        assert wrapper._connected_identity["system_name"] == "PR1"
        assert wrapper._connected_identity["client"] == "100"
        assert wrapper._connected_identity["user"] == "TESTUSER"

    def test_connect_without_system_clears_last_system_id(self):
        conn1 = _make_connection("PR1")
        conn2 = _make_connection("ERP")
        app = _make_application(conn1, conn2)

        wrapper = SapGuiWrapper()
        wrapper._application = app
        with patch.object(wrapper, "_get_application", return_value=app):
            wrapper.connect(system="ERP")
        assert wrapper._last_system_id == "ERP"

        with patch.object(wrapper, "_get_application", return_value=app):
            wrapper.connect(connection_index=0)
        assert wrapper._last_system_id is None


# ---------------------------------------------------------------------------
# auto reconnect
# ---------------------------------------------------------------------------

class TestAutoReconnect:
    def test_session_property_triggers_reconnect(self):
        wrapper, app = _connected_wrapper("PR1")

        wrapper._session = None
        wrapper._connection = None
        wrapper._application = None

        with patch.object(wrapper, "_get_application", return_value=app):
            sess = wrapper.session
        assert sess is not None

    def test_reconnect_disabled(self):
        wrapper = SapGuiWrapper()
        wrapper._auto_reconnect = False
        wrapper._session = None

        with pytest.raises(SapGuiError, match="연결되지 않았습니다"):
            _ = wrapper.session

    def test_fresh_wrapper_no_reconnect(self):
        """connect()를 한 번도 호출하지 않은 wrapper는 reconnect 시도하지 않는다."""
        wrapper = SapGuiWrapper()
        assert wrapper._connected_identity is None

        with pytest.raises(SapGuiError, match="연결되지 않았습니다"):
            _ = wrapper.session

    def test_reconnect_preserves_system_id(self):
        conn1 = _make_connection("PR1")
        conn2 = _make_connection("ERP")
        app = _make_application(conn1, conn2)

        wrapper = SapGuiWrapper()
        wrapper._application = app
        with patch.object(wrapper, "_get_application", return_value=app):
            wrapper.connect(system="ERP")
        assert wrapper._last_system_id == "ERP"

        wrapper._session = None
        wrapper._connection = None
        wrapper._application = None

        with patch.object(wrapper, "_get_application", return_value=app):
            sess = wrapper.session
        assert sess.Info.SystemName == "ERP"

    def test_reconnect_rejects_wrong_system(self):
        """복구된 세션이 다른 시스템이면 거부한다."""
        wrapper, _ = _connected_wrapper("PR1")

        wrong_conn = _make_connection("QAS")
        wrong_app = _make_application(wrong_conn)

        wrapper._session = None
        wrapper._connection = None
        wrapper._application = None

        with patch.object(wrapper, "_get_application", return_value=wrong_app):
            with patch("time.sleep"):
                result = wrapper._try_reconnect()
        assert result is False

    def test_reconnect_retries_after_connect(self):
        wrapper, _ = _connected_wrapper("PR1")
        conn = _make_connection("PR1")
        app = _make_application(conn)
        call_count = 0

        def fake_get_app():
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise SapGuiError("COM error")
            return app

        wrapper._session = None
        wrapper._connection = None
        wrapper._application = None

        with patch.object(wrapper, "_get_application", side_effect=fake_get_app):
            with patch("time.sleep"):
                result = wrapper._try_reconnect()
        assert result is True
        assert call_count == 3

    def test_reconnect_gives_up_after_connect(self):
        wrapper, _ = _connected_wrapper("PR1")

        wrapper._session = None
        wrapper._connection = None
        wrapper._application = None

        with patch.object(wrapper, "_get_application", side_effect=SapGuiError("COM error")):
            with patch("time.sleep"):
                result = wrapper._try_reconnect()
        assert result is False

    def test_reconnect_catches_non_sapgui_errors(self):
        """SapGuiError뿐 아니라 다른 예외도 처리한다."""
        wrapper, _ = _connected_wrapper("PR1")

        wrapper._session = None
        wrapper._connection = None
        wrapper._application = None

        with patch.object(wrapper, "_get_application", side_effect=RuntimeError("unexpected")):
            with patch("time.sleep"):
                result = wrapper._try_reconnect()
        assert result is False


# ---------------------------------------------------------------------------
# dispatcher integration
# ---------------------------------------------------------------------------

class TestDispatcherConnections:
    @patch("sap_cli.dispatcher.SapGuiWrapper")
    def test_dispatch_connections_no_connect_required(self, MockWrapper):
        """connections 커맨드는 connect 없이 연결 목록을 조회한다."""
        from sap_cli.dispatcher import DispatchContext, dispatch_command

        mock_sap = MagicMock()
        mock_sap.list_connections.return_value = {
            "connection_count": 1,
            "connections": [{"index": 0, "system_name": "PR1"}],
        }
        MockWrapper.return_value = mock_sap

        result = dispatch_command("connections", {}, DispatchContext(source="test"))
        assert result["connection_count"] == 1
        mock_sap.connect.assert_not_called()

    def test_dispatch_connect_with_system(self):
        from sap_cli.dispatcher import DispatchContext, dispatch_command
        from sap_cli.session_pool import SessionPool

        mock_sap = MagicMock()
        mock_sap.connect.return_value = MagicMock(
            model_dump=lambda mode="json": {
                "system_name": "ERP",
                "user": "TEST",
            }
        )

        mock_pool = SessionPool()
        with patch("sap_cli.service.get_pool", return_value=mock_pool), \
             patch.object(mock_pool, "get_or_create", return_value=mock_sap):
            result = dispatch_command(
                "connect",
                {"system": "ERP"},
                DispatchContext(source="test"),
            )
        mock_sap.connect.assert_called_once_with(
            connection_index=0, session_index=0, system="ERP"
        )
        assert result["connected"] is True
