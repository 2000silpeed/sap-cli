"""SAP GUI COM Wrapper - win32com을 통한 SAP GUI Scripting API 직접 호출.

Windows에서 실행 중인 SAP GUI에 COM을 통해 연결하고,
트랜잭션 실행, 필드 조작, 화면 탐색 등 모든 GUI 자동화를 수행한다.

Usage:
    sap = SapGuiWrapper()
    sap.connect()
    sap.run_transaction("MM03")
    sap.set_field("wnd[0]/usr/ctxtRMMG1-MATNR", "100-100")
    sap.send_vkey(0)  # Enter
    screen = sap.get_screen()
    print(screen.model_dump_json())
"""

from __future__ import annotations

import json
import logging
import platform
from typing import Any

logger = logging.getLogger(__name__)

from .ontology.schema import (
    GuiComponentType,
    SapButton,
    SapField,
    SapMenuItem,
    SapSession,
    SapStatusMessage,
    SapTable,
    SapTableColumn,
    SapTreeNode,
    ScreenOntology,
    SemanticContext,
    StatusMessageType,
)
from .scripting_api_graph import query_default_graph
from .scripting_ontology import component_capability, normalize_action

# win32com은 Windows 전용 - 임포트 실패 시 스텁 제공
_WIN32 = platform.system() == "Windows"

if _WIN32:
    import pythoncom  # type: ignore[import-untyped]
    import win32com.client  # type: ignore[import-untyped]


class SapGuiError(Exception):
    """SAP GUI 조작 중 발생하는 에러."""


class SapGuiWrapper:
    """SAP GUI Scripting API COM 래퍼.

    Running Object Table(ROT)을 통해 실행 중인 SAP GUI에 연결하고,
    세션을 통해 모든 GUI 조작을 수행한다.
    """

    # 사용자가 sap run으로 실행한 트랜잭션으로 간주할 접두사 목록.
    # SAP가 내부적으로 호출하는 프로그램(SE38 등)은 제외.
    _INTERNAL_TCODES = frozenset({"SE38", "SE80", "SE37", "SAPLSMTR_NAVIGATION"})

    def __init__(self) -> None:
        self._application: Any = None
        self._connection: Any = None
        self._session: Any = None
        self._user_tcode: str | None = None  # sap run으로 실행한 원래 tcode
        self._last_connection_index: int = 0
        self._last_session_index: int = 0
        self._last_system_id: str | None = None
        self._connected_identity: dict[str, str] | None = None
        self._auto_reconnect: bool = True
        self._max_reconnect_attempts: int = 3

    # -------------------------------------------------------------------
    # Connection Management
    # -------------------------------------------------------------------

    def _ensure_com_initialized(self) -> None:
        """현재 스레드에서 COM STA가 초기화되었는지 확인."""
        if not _WIN32:
            return
        try:
            pythoncom.CoInitialize()
        except Exception:
            pass

    def _get_application(self) -> Any:
        """ROT에서 SAP GUI Application 객체를 가져온다."""
        if not _WIN32:
            raise SapGuiError("SAP GUI COM is only available on Windows")
        self._ensure_com_initialized()
        try:
            rot_entry = win32com.client.GetObject("SAPGUI")
            self._application = rot_entry.GetScriptingEngine
            return self._application
        except Exception as e:
            raise SapGuiError(
                "SAP GUI에 연결할 수 없습니다. SAP GUI가 실행 중인지, "
                "스크립팅이 활성화되어 있는지 확인하세요.\n"
                "서버: sapgui/user_scripting = TRUE\n"
                "클라이언트: SAP GUI Options > Accessibility > Enable scripting"
            ) from e

    def connect(
        self,
        connection_index: int = 0,
        session_index: int = 0,
        system: str | None = None,
    ) -> SapSession:
        """실행 중인 SAP GUI에 연결.

        SAP GUI가 이미 실행 중이고 로그인된 상태여야 한다.
        ROT(Running Object Table)을 통해 SAP GUI 프로세스에 접근한다.

        system을 지정하면 시스템 ID(SID)로 올바른 연결을 자동 탐색한다.
        """
        app = self._get_application()

        if app.Connections.Count == 0:
            raise SapGuiError("활성 SAP 연결이 없습니다. SAP에 로그인하세요.")

        if system:
            idx = self._find_connection_by_system(system)
            if idx is None:
                available = [
                    c["system_name"] for c in self.list_connections()["connections"]
                ]
                raise SapGuiError(
                    f"시스템 '{system}'에 해당하는 연결을 찾을 수 없습니다. "
                    f"활성 시스템: {available}"
                )
            connection_index = idx
            self._last_system_id = system
        else:
            self._last_system_id = None

        if connection_index >= app.Connections.Count:
            raise SapGuiError(
                f"연결 인덱스 {connection_index} 없음. "
                f"활성 연결 수: {app.Connections.Count}"
            )

        self._connection = app.Connections(connection_index)

        if session_index >= self._connection.Sessions.Count:
            raise SapGuiError(
                f"세션 인덱스 {session_index} 없음. "
                f"활성 세션 수: {self._connection.Sessions.Count}"
            )

        self._session = self._connection.Sessions(session_index)
        self._last_connection_index = connection_index
        self._last_session_index = session_index
        info = self.get_session_info()
        self._connected_identity = {
            "system_name": info.system_name,
            "client": info.client,
            "user": info.user,
        }
        return info

    def list_connections(self) -> dict[str, Any]:
        """현재 SAP GUI의 모든 활성 연결 목록을 반환한다."""
        app = self._application
        if app is None:
            app = self._get_application()

        connections = []
        for i in range(app.Connections.Count):
            conn = app.Connections(i)
            conn_info: dict[str, Any] = {
                "index": i,
                "description": conn.Description,
                "session_count": conn.Sessions.Count,
                "sessions": [],
            }
            try:
                conn_info["system_name"] = ""
                conn_info["client"] = ""
                if conn.Sessions.Count > 0:
                    s = conn.Sessions(0)
                    conn_info["system_name"] = s.Info.SystemName
                    conn_info["client"] = s.Info.Client
            except Exception:
                pass
            for j in range(conn.Sessions.Count):
                try:
                    sess = conn.Sessions(j)
                    conn_info["sessions"].append({
                        "index": j,
                        "user": sess.Info.User,
                        "transaction": sess.Info.Transaction,
                    })
                except Exception:
                    pass
            connections.append(conn_info)

        return {
            "connection_count": app.Connections.Count,
            "connections": connections,
        }

    def _find_connection_by_system(self, system: str) -> int | None:
        """시스템 ID(SID) 또는 Description으로 연결 인덱스를 찾는다.

        SystemName 정확 매칭을 우선하고, 없으면 Description 부분 매칭으로 폴백한다.
        """
        app = self._application
        if app is None:
            app = self._get_application()

        system_upper = system.upper()

        for i in range(app.Connections.Count):
            conn = app.Connections(i)
            try:
                if conn.Sessions.Count > 0:
                    s = conn.Sessions(0)
                    if s.Info.SystemName.upper() == system_upper:
                        return i
            except Exception:
                continue

        matches = []
        for i in range(app.Connections.Count):
            conn = app.Connections(i)
            if system_upper in conn.Description.upper():
                matches.append(i)

        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            logger.warning(
                "시스템 '%s'에 대해 복수의 Description 매칭 발견 (인덱스: %s) — 첫 번째 사용",
                system, matches,
            )
            return matches[0]

        return None

    @property
    def session(self) -> Any:
        if not self.is_connected:
            if self._auto_reconnect and self._connected_identity and self._try_reconnect():
                return self._session
            raise SapGuiError("SAP GUI에 연결되지 않았습니다. connect()를 먼저 호출하세요.")
        return self._session

    @property
    def is_connected(self) -> bool:
        if self._session is None:
            return False
        try:
            _ = self._session.Info.User
            return True
        except Exception:
            self._session = None
            self._connection = None
            self._application = None
            return False

    def _try_reconnect(self) -> bool:
        """이전 연결 파라미터로 자동 재연결을 시도한다."""
        expected = self._connected_identity
        for attempt in range(1, self._max_reconnect_attempts + 1):
            try:
                logger.info(
                    "세션 복구 시도 %d/%d (system=%s, conn=%d, sess=%d)",
                    attempt, self._max_reconnect_attempts,
                    self._last_system_id or "-",
                    self._last_connection_index, self._last_session_index,
                )
                info = self.connect(
                    connection_index=self._last_connection_index,
                    session_index=self._last_session_index,
                    system=self._last_system_id,
                )
                if expected and info.system_name != expected["system_name"]:
                    logger.warning(
                        "복구된 세션이 다른 시스템입니다: %s (기대: %s)",
                        info.system_name, expected["system_name"],
                    )
                    self._session = None
                    self._connection = None
                    self._application = None
                    return False
                logger.info("세션 복구 성공")
                return True
            except Exception:
                if attempt < self._max_reconnect_attempts:
                    import time
                    time.sleep(1)
                continue
        logger.warning("세션 복구 실패 — %d회 시도 후 포기", self._max_reconnect_attempts)
        return False

    def create_session(self) -> int:
        """현재 연결에 새 SAP 세션을 생성하고 세션 인덱스를 반환한다.

        SAP GUI에서 /o 명령과 동일. 연결당 최대 6세션(기본값).
        """
        if self._connection is None:
            raise SapGuiError("연결되지 않았습니다. connect()를 먼저 호출하세요.")
        before = self._connection.Sessions.Count
        try:
            self._connection.Sessions(0).CreateSession()
        except Exception as e:
            raise SapGuiError(f"새 세션 생성 실패: {e}") from e
        import time
        for _ in range(20):
            time.sleep(0.3)
            try:
                if self._connection.Sessions.Count > before:
                    return self._connection.Sessions.Count - 1
            except Exception:
                break
        raise SapGuiError(
            "새 세션 생성 시간 초과 (최대 세션 수 초과 가능). "
            f"현재 세션 수: {before}"
        )

    def disconnect(self) -> None:
        self._session = None
        self._connection = None
        self._application = None

    # -------------------------------------------------------------------
    # Session Info
    # -------------------------------------------------------------------

    def get_session_info(self) -> SapSession:
        """현재 세션의 상세 정보."""
        info = self.session.Info
        return SapSession(
            session_id=self.session.Id,
            user=info.User,
            language=info.Language,
            transaction=info.Transaction,
            screen_number=info.ScreenNumber,
            program=info.Program,
            system_name=info.SystemName,
            client=info.Client,
            application_server=info.ApplicationServer,
        )

    # -------------------------------------------------------------------
    # Transaction Execution
    # -------------------------------------------------------------------

    def run_transaction(self, tcode: str) -> None:
        """트랜잭션 실행. /n 접두사가 자동 추가된다."""
        cmd = tcode if tcode.startswith("/") else f"/n{tcode}"
        # 사용자가 명시적으로 실행한 tcode를 기억 (learn 시 fallback용)
        raw = tcode.lstrip("/").lstrip("n").lstrip("/").upper()
        if raw and raw not in self._INTERNAL_TCODES:
            self._user_tcode = raw
        self.session.SendCommand(cmd)

    def send_command(self, command: str) -> None:
        """커맨드 필드에 명령 입력 후 실행."""
        self.session.SendCommand(command)

    def end_transaction(self) -> None:
        """현재 트랜잭션 종료 (/n)."""
        self.session.EndTransaction()

    # -------------------------------------------------------------------
    # Field Operations
    # -------------------------------------------------------------------

    def set_field(self, element_id: str, value: str) -> None:
        """필드에 값 설정.

        GuiComboBox는 Text 대신 Key/Value 계열 속성을 써야 하는 경우가
        있어 콤보박스 타입이면 select_combo와 같은 경로로 처리한다.
        """
        try:
            element = self.session.FindById(element_id)
            if self._is_combo_box(element, element_id):
                self._set_combo_value(element, value)
                return
            element.Text = value
        except Exception as e:
            raise SapGuiError(f"필드 설정 실패 [{element_id}]: {e}") from e

    def get_field(self, element_id: str) -> str:
        """필드 값 읽기."""
        try:
            element = self.session.FindById(element_id)
            if self._is_combo_box(element, element_id):
                return self._get_combo_value(element)
            return str(element.Text)
        except Exception as e:
            raise SapGuiError(f"필드 읽기 실패 [{element_id}]: {e}") from e

    def get_field_info(self, element_id: str) -> dict[str, Any]:
        """필드 값과 이름 정보를 함께 읽기."""
        try:
            element = self.session.FindById(element_id)
            name = self._safe_attr(element, "Name", "")
            payload: dict[str, Any] = {
                "name": name or self._extract_name(element_id),
                "field": element_id,
                "value": self.get_field(element_id),
            }
            if self._is_combo_box(element, element_id):
                payload["key"] = self._safe_attr(element, "Key", "")
                payload["text"] = self._safe_attr(element, "Text", "")
                entries = self._get_combo_entries(element)
                if entries:
                    payload["entries"] = entries
            return payload
        except Exception as e:
            raise SapGuiError(f"필드 읽기 실패 [{element_id}]: {e}") from e

    def set_focus(self, element_id: str) -> None:
        """요소에 포커스 설정."""
        try:
            self.session.FindById(element_id).SetFocus()
        except Exception as e:
            raise SapGuiError(f"포커스 설정 실패 [{element_id}]: {e}") from e

    # -------------------------------------------------------------------
    # Button / Key Operations
    # -------------------------------------------------------------------

    def press(self, element_id: str) -> None:
        """버튼 클릭 또는 탭 선택.

        GuiTab은 Press()가 아닌 Select()로 선택해야 한다.
        요소 타입을 확인하여 자동으로 적절한 메서드를 호출.
        """
        try:
            obj = self.session.FindById(element_id)
            obj_type = str(obj.Type)
            if obj_type == "GuiTab":
                obj.Select()
            else:
                obj.Press()
        except Exception as e:
            raise SapGuiError(f"버튼/탭 클릭 실패 [{element_id}]: {e}") from e

    def send_vkey(self, vkey: int) -> None:
        """VKey 전송 (Enter=0, Back=3, Execute=8, Save=11, Cancel=12).

        모달(wnd[1])이 떠 있으면 모달에 전송, 없으면 메인 윈도우(wnd[0])에 전송.
        """
        window_id = "wnd[1]" if self.has_modal() else "wnd[0]"
        try:
            self.session.FindById(window_id).SendVKey(vkey)
        except Exception as e:
            raise SapGuiError(f"VKey 전송 실패 [{vkey}] on {window_id}: {e}") from e

    def select_menu(self, menu_id: str) -> None:
        """메뉴 항목 선택."""
        try:
            self.session.FindById(menu_id).Select()
        except Exception as e:
            raise SapGuiError(f"메뉴 선택 실패 [{menu_id}]: {e}") from e

    # -------------------------------------------------------------------
    # Checkbox / Radio / ComboBox
    # -------------------------------------------------------------------

    def set_checkbox(self, element_id: str, checked: bool) -> None:
        """체크박스 설정."""
        try:
            self.session.FindById(element_id).Selected = checked
        except Exception as e:
            raise SapGuiError(f"체크박스 설정 실패 [{element_id}]: {e}") from e

    def select_radio(self, element_id: str) -> None:
        """라디오 버튼 선택."""
        try:
            self.session.FindById(element_id).Select()
        except Exception as e:
            raise SapGuiError(f"라디오 선택 실패 [{element_id}]: {e}") from e

    def select_combo(self, element_id: str, value: str) -> None:
        """콤보박스(드롭다운) 값 선택."""
        try:
            self._set_combo_value(self.session.FindById(element_id), value)
        except Exception as e:
            raise SapGuiError(f"콤보박스 선택 실패 [{element_id}]: {e}") from e

    def get_combo_entries(self, element_id: str) -> list[dict[str, Any]]:
        """콤보박스 선택 가능 항목 목록 조회."""
        try:
            element = self.session.FindById(element_id)
            if not self._is_combo_box(element, element_id):
                raise SapGuiError(f"콤보박스가 아닙니다 [{element_id}]")
            return self._get_combo_entries(element)
        except SapGuiError:
            raise
        except Exception as e:
            raise SapGuiError(f"콤보박스 항목 조회 실패 [{element_id}]: {e}") from e

    # -------------------------------------------------------------------
    # Scripting Ontology / Generic UI Action
    # -------------------------------------------------------------------

    def build_action_context(
        self,
        element_id: str,
        action: str = "",
        value: str | None = None,
    ) -> dict[str, Any]:
        """Build live element + scripting API graph context before acting."""
        element = self.session.FindById(element_id)
        component_type = str(self._safe_attr(element, "Type", "Unknown"))
        subtype = str(self._safe_attr(element, "SubType", ""))
        normalized = normalize_action(action) if action else ""
        capability = component_capability(component_type, subtype)
        graph = query_default_graph(
            component_type="" if component_type == "Unknown" else component_type,
            intent=normalized,
            depth=1,
        )
        graph_nodes = graph.get("nodes", [])
        graph_edges = graph.get("edges", [])
        actions = set(capability.get("actions", []))
        warnings = list(graph.get("warnings", []))
        if normalized and normalized != "inspect" and actions and normalized not in actions:
            warnings.append(
                f"Action '{normalized}' is not listed in capability actions for {component_type}; "
                "inspect live component and prefer sap_ui_action fallback."
            )

        return {
            "element_id": element_id,
            "requested_action": action,
            "normalized_action": normalized,
            **({"requested_value": value} if value is not None else {}),
            "type": component_type,
            **({"sub_type": subtype} if subtype else {}),
            "capability": capability,
            "api_graph": {
                "query": graph.get("query", {}),
                "runtime": graph.get("runtime", {}),
                "node_count": len(graph_nodes),
                "edge_count": len(graph_edges),
                "nodes": graph_nodes[:40],
                "edges": graph_edges[:80],
                "truncated": len(graph_nodes) > 40 or len(graph_edges) > 80,
            },
            "warnings": warnings,
        }

    def inspect_element(self, element_id: str) -> dict[str, Any]:
        """Inspect one SAP GUI component and return live capability metadata."""
        try:
            element = self.session.FindById(element_id)
            component_type = str(self._safe_attr(element, "Type", "Unknown"))
            subtype = str(self._safe_attr(element, "SubType", ""))
            payload: dict[str, Any] = {
                "id": element_id,
                "type": component_type,
                **({"sub_type": subtype} if subtype else {}),
                "name": self._safe_attr(element, "Name", self._extract_name(element_id)),
                "text": self._safe_attr(element, "Text", ""),
                "tooltip": self._safe_attr(element, "Tooltip", ""),
                "changeable": bool(self._safe_attr(element, "Changeable", False)),
                "selected": self._safe_attr(element, "Selected", ""),
                "value": self._element_value(element, element_id),
                "capability": component_capability(component_type, subtype),
                "ontology_preflight": self.build_action_context(element_id, "inspect"),
            }
            if self._is_combo_box(element, element_id):
                payload["key"] = self._safe_attr(element, "Key", "")
                payload["entries"] = self._get_combo_entries(element)
            return payload
        except Exception as e:
            raise SapGuiError(f"요소 검사 실패 [{element_id}]: {e}") from e

    def perform_ui_action(
        self,
        element_id: str,
        action: str,
        value: str | None = None,
        *,
        node_key: str = "",
        column: str = "",
        row: int | None = None,
    ) -> dict[str, Any]:
        """Perform a generic UI action using live component capabilities.

        This is the adaptive path for AI agents: inspect the element type at
        runtime and route a high-level action to the correct SAP GUI Scripting
        API call without needing a bespoke command for every component.
        """
        normalized = normalize_action(action)
        element = self.session.FindById(element_id)
        component_type = str(self._safe_attr(element, "Type", "Unknown"))
        subtype = str(self._safe_attr(element, "SubType", ""))
        used: dict[str, Any] = {
            "element_id": element_id,
            "requested_action": action,
            "action": normalized,
            "type": component_type,
            **({"sub_type": subtype} if subtype else {}),
            "ontology_preflight": self.build_action_context(element_id, normalized, value),
        }

        if normalized == "inspect":
            return {"inspected": True, **self.inspect_element(element_id)}

        if normalized == "focus":
            self.set_focus(element_id)
            return {"focused": True, **used}

        if normalized == "read":
            return {"read": True, **used, "field_info": self.get_field_info(element_id)}

        if normalized in {"set", "select"}:
            if component_type == "GuiComboBox" or "/cmb" in element_id:
                if value is None:
                    raise SapGuiError("콤보박스 선택에는 value가 필요합니다.")
                self.select_combo(element_id, value)
                return {"selected": True, "value": value, **used}
            if component_type == "GuiCheckBox" or "/chk" in element_id:
                checked = str(value or "").lower() not in ("", "0", "false", "off", "no")
                self.set_checkbox(element_id, checked)
                return {"checked": checked, **used}
            if component_type == "GuiRadioButton" or "/rad" in element_id:
                self.select_radio(element_id)
                return {"selected": True, **used}
            if component_type in {"GuiTab", "GuiMenu"}:
                self.session.FindById(element_id).Select()
                return {"selected": True, **used}
            if component_type == "GuiButton" and normalized == "select":
                self.press(element_id)
                return {"pressed": True, **used}
            if value is None:
                raise SapGuiError("필드 설정에는 value가 필요합니다.")
            self.set_field(element_id, value)
            return {"value_set": value, **used}

        if normalized == "toggle":
            if component_type != "GuiCheckBox" and "/chk" not in element_id:
                raise SapGuiError(f"toggle은 체크박스에만 사용할 수 있습니다: {component_type}")
            current = bool(self._safe_attr(element, "Selected", False))
            self.set_checkbox(element_id, not current)
            return {"checked": not current, **used}

        if normalized == "press":
            self.press(element_id)
            return {"pressed": True, **used}

        if normalized == "search_help":
            self.set_focus(element_id)
            self.send_vkey(16)
            return {"search_help": True, **used}

        if normalized == "select_node":
            key = node_key or value or ""
            if not key:
                raise SapGuiError("트리 노드 선택에는 node_key 또는 value가 필요합니다.")
            self.tree_select_node(element_id, key)
            return {"selected": True, "node_key": key, **used}

        if normalized == "expand_node":
            key = node_key or value or ""
            if not key:
                raise SapGuiError("트리 노드 확장에는 node_key 또는 value가 필요합니다.")
            self.tree_expand_node(element_id, key)
            return {"expanded": True, "node_key": key, **used}

        if normalized == "collapse_node":
            key = node_key or value or ""
            if not key:
                raise SapGuiError("트리 노드 접기에는 node_key 또는 value가 필요합니다.")
            self.tree_collapse_node(element_id, key)
            return {"collapsed": True, "node_key": key, **used}

        if normalized == "doubleclick_node":
            key = node_key or value or ""
            if not key:
                raise SapGuiError("트리 노드 더블클릭에는 node_key 또는 value가 필요합니다.")
            self.tree_double_click_node(element_id, key)
            return {"double_clicked": True, "node_key": key, **used}

        if normalized == "press_node":
            key = node_key or value or ""
            if not key:
                raise SapGuiError("트리 노드 버튼 클릭에는 node_key 또는 value가 필요합니다.")
            used_col = self.tree_press_button(element_id, key, column)
            return {"pressed": True, "node_key": key, "used_column": used_col, **used}

        if normalized == "select_row":
            if row is None:
                if value is None:
                    raise SapGuiError("테이블 행 선택에는 row 또는 value가 필요합니다.")
                row = int(value)
            self.select_table_row(element_id, row)
            return {"selected": True, "row": row, **used}

        raise SapGuiError(
            f"지원하지 않는 UI action: {action} ({normalized}) for {component_type}"
        )

    def _element_value(self, element: Any, element_id: str) -> str:
        if self._is_combo_box(element, element_id):
            return self._get_combo_value(element)
        for attr in ("Text", "Value", "Key"):
            val = self._safe_attr(element, attr, "")
            if val not in ("", None):
                return str(val)
        return ""

    @staticmethod
    def _is_combo_box(element: Any, element_id: str = "") -> bool:
        """Best-effort GuiComboBox detection for normal set/get paths."""
        try:
            if str(element.Type) == "GuiComboBox":
                return True
        except Exception:
            pass
        return "/cmb" in element_id

    @staticmethod
    def _set_combo_value(element: Any, value: str) -> None:
        """Set a SAP GUI combo box by key or display value.

        SAP GUI's normal Windows scripting model selects combo boxes by Key.
        Users and AI often know only the displayed value, so resolve display
        value -> key from Entries before falling back to Value/Text.
        """
        combo_key = SapGuiWrapper._resolve_combo_key(element, value)
        errors: list[str] = []
        for attr, candidate in (
            ("Key", combo_key),
            ("key", combo_key),
            ("Value", value),
            ("value", value),
            ("Text", value),
            ("text", value),
        ):
            try:
                setattr(element, attr, candidate)
                return
            except Exception as e:
                errors.append(f"{attr}: {e}")
        raise SapGuiError("; ".join(errors) or "no writable combo property")

    @staticmethod
    def _get_combo_value(element: Any) -> str:
        """Read a combo box value using the most informative available property."""
        for attr in ("Key", "Value", "Text"):
            try:
                val = getattr(element, attr)
                if val is not None:
                    return str(val)
            except Exception:
                continue
        return ""

    @staticmethod
    def _resolve_combo_key(element: Any, requested: str) -> str:
        """Return the combo key matching requested key/display value if known."""
        entries = SapGuiWrapper._get_combo_entries(element)
        if not entries:
            return requested

        requested_norm = requested.strip()
        requested_lower = requested_norm.lower()

        # Exact key match first.
        for entry in entries:
            key = str(entry.get("key", ""))
            if requested_norm == key:
                return key

        # Then exact display/value match.
        for entry in entries:
            key = str(entry.get("key", ""))
            value = str(entry.get("value", ""))
            if requested_norm == value:
                return key

        # Finally case-insensitive convenience matching.
        for entry in entries:
            key = str(entry.get("key", ""))
            value = str(entry.get("value", ""))
            if requested_lower in {key.lower(), value.lower()}:
                return key

        return requested

    @staticmethod
    def _get_combo_entries(element: Any) -> list[dict[str, Any]]:
        """Extract combo entries from SAP GUI's Entries collection if available."""
        try:
            entries = getattr(element, "Entries")
        except Exception:
            try:
                entries = getattr(element, "entries")
            except Exception:
                return []

        count = SapGuiWrapper._collection_count(entries)
        result: list[dict[str, Any]] = []
        for i in range(count):
            entry = SapGuiWrapper._collection_item(entries, i)
            if entry is None:
                continue
            key = SapGuiWrapper._entry_attr(entry, "Key", "key")
            value = SapGuiWrapper._entry_attr(entry, "Value", "value")
            pos = SapGuiWrapper._entry_attr(entry, "Pos", "pos")
            result.append(
                {
                    "key": key,
                    "value": value,
                    **({"pos": pos} if pos != "" else {}),
                }
            )
        return result

    @staticmethod
    def _collection_count(collection: Any) -> int:
        for attr in ("Count", "count", "Length", "length"):
            try:
                return int(getattr(collection, attr))
            except Exception:
                continue
        return 0

    @staticmethod
    def _collection_item(collection: Any, index: int) -> Any | None:
        for getter in (
            lambda: collection(index),
            lambda: collection.ElementAt(index),
            lambda: collection.elementAt(index),
            lambda: collection.Item(index),
            lambda: collection.item(index),
        ):
            try:
                return getter()
            except Exception:
                continue
        return None

    @staticmethod
    def _entry_attr(entry: Any, *names: str) -> str:
        for name in names:
            try:
                return str(getattr(entry, name))
            except Exception:
                continue
        return ""

    # -------------------------------------------------------------------
    # Status Bar
    # -------------------------------------------------------------------

    def get_status(self) -> SapStatusMessage:
        """상태바 메시지 조회."""
        try:
            statusbar = self.session.FindById("wnd[0]/sbar")
            msg_type = statusbar.MessageType or "S"
            return SapStatusMessage(
                type=StatusMessageType(msg_type),
                message=statusbar.Text or "",
            )
        except Exception as e:
            raise SapGuiError(f"상태바 조회 실패: {e}") from e

    # -------------------------------------------------------------------
    # Screen Reading (GetObjectTree 기반)
    # -------------------------------------------------------------------

    def get_screen(self, window_id: str | None = None) -> ScreenOntology:
        """현재 화면의 전체 UI 구조를 ScreenOntology로 반환.

        window_id를 지정하지 않으면 모달(wnd[1])이 있을 때 모달을,
        없으면 메인 윈도우(wnd[0])를 스캔한다.

        수동 탐색(Children 재귀) 방식 사용. GetObjectTree는 id만 반환하여
        각 요소마다 FindById COM 호출이 필요해 느림. 수동 탐색은 Children
        한 번 호출로 모든 자식을 가져오므로 COM 왕복이 적다.
        """
        if window_id is None:
            window_id = "wnd[1]" if self.has_modal() else "wnd[0]"

        info = self.session.Info
        reported_tcode = info.Transaction
        # SAP가 내부 프로그램(SE38 등)을 반환하면 사용자가 실행한 원래 tcode 사용
        tcode = reported_tcode
        if self._user_tcode and reported_tcode.upper() in self._INTERNAL_TCODES:
            logger.debug("SAP 보고 tcode=%s → 원래 tcode=%s 사용", reported_tcode, self._user_tcode)
            tcode = self._user_tcode
        screen = ScreenOntology(
            transaction=tcode,
            screen_number=info.ScreenNumber,
            program=info.Program,
            title=self.session.FindById(window_id).Text,
        )

        self._manual_scan(window_id, screen)
        try:
            screen.status = self.get_status()
        except Exception as e:
            logger.warning("상태바 조회 실패 (화면 스캔에는 영향 없음): %s", e)
        return screen

    def _get_object_tree(self, window_id: str) -> dict[str, Any] | None:
        """GetObjectTree()로 JSON UI 트리 획득 (SAP GUI 7.70 PL3+)."""
        try:
            json_str = self.session.GetObjectTree(window_id, None)
            return json.loads(json_str)
        except Exception:
            return None

    def _parse_tree_to_ontology(self, tree: dict[str, Any], screen: ScreenOntology) -> None:
        """GetObjectTree JSON을 ScreenOntology로 파싱.

        GetObjectTree는 트리 구조(id + children)만 반환한다.
        실제 속성(Type, Text, Changeable 등)은 FindById로 별도 조회.

        최적화: 리프 노드(자식 없는 요소)와 테이블/탭 후보만 FindById 호출.
        컨테이너(usr, sub, tbar 등)는 건너뛰어 COM 호출 횟수를 최소화.
        """
        ids = self._collect_actionable_ids(tree)
        for element_id in ids:
            try:
                obj = self.session.FindById(element_id)
                self._add_element_to_screen(obj, screen)
            except Exception as e:
                logger.debug("요소 조회 건너뜀 [%s]: %s", element_id, e)

    def _collect_actionable_ids(self, node: dict[str, Any]) -> list[str]:
        """리프 노드 + 테이블/탭 후보 ID만 수집 (컨테이너 제외)."""
        ids: list[str] = []
        self._walk_tree_for_ids(node, ids)
        return ids

    def _walk_tree_for_ids(self, node: dict[str, Any], ids: list[str]) -> None:
        props = node.get("properties", {})
        node_id = props.get("id", props.get("Id", ""))
        children = node.get("children", [])

        if not children and node_id:
            # 리프 노드: 필드, 버튼, 체크박스 등
            ids.append(node_id)
        elif node_id and self._looks_like_widget(node_id):
            # 자식이 있지만 위젯으로 보이는 것: 테이블, 탭스트립, 그리드
            ids.append(node_id)

        for child in children:
            self._walk_tree_for_ids(child, ids)

    @staticmethod
    def _looks_like_widget(element_id: str) -> bool:
        """ID 패턴으로 테이블/탭/쉘 등 위젯 여부 판단."""
        last_part = element_id.rsplit("/", 1)[-1].lower()
        return any(last_part.startswith(p) for p in (
            "tbl", "shell", "tabs", "tabp",
        ))

    def _add_element_to_screen(self, obj: Any, screen: ScreenOntology) -> None:
        """COM 객체를 ScreenOntology에 추가."""
        obj_type = str(obj.Type)
        obj_id = str(obj.Id)
        comp_type = self._map_component_type(obj_type)

        if comp_type in (
            GuiComponentType.TEXT_FIELD,
            GuiComponentType.CTEXT_FIELD,
            GuiComponentType.PASSWORD_FIELD,
            GuiComponentType.OK_CODE_FIELD,
        ):
            screen.fields.append(SapField(
                id=obj_id,
                name=self._extract_name(obj_id),
                type=comp_type,
                label=self._safe_attr(obj, "Text", ""),
                value=self._safe_attr(obj, "Text", ""),
                changeable=bool(self._safe_attr(obj, "Changeable", False)),
                max_length=self._get_max_length(obj),
                semantic=self._build_semantic(obj, obj_id),
            ))
        elif comp_type == GuiComponentType.COMBO_BOX:
            screen.fields.append(SapField(
                id=obj_id,
                name=self._extract_name(obj_id),
                type=comp_type,
                label=self._get_label_for(obj),
                value=self._safe_attr(obj, "Value", ""),
                changeable=bool(self._safe_attr(obj, "Changeable", False)),
                semantic=self._build_semantic(obj, obj_id),
            ))
        elif comp_type == GuiComponentType.BUTTON:
            screen.buttons.append(SapButton(
                id=obj_id,
                name=self._extract_name(obj_id),
                text=self._safe_attr(obj, "Text", ""),
                tooltip=self._safe_attr(obj, "Tooltip", ""),
                icon_name=self._safe_attr(obj, "IconName", ""),
            ))
        elif comp_type in (GuiComponentType.CHECK_BOX, GuiComponentType.RADIO_BUTTON):
            screen.fields.append(SapField(
                id=obj_id,
                name=self._extract_name(obj_id),
                type=comp_type,
                label=self._safe_attr(obj, "Text", ""),
                changeable=bool(self._safe_attr(obj, "Changeable", False)),
                semantic=self._build_semantic(obj, obj_id),
            ))
        elif comp_type == GuiComponentType.TAB:
            screen.tabs.append(SapButton(
                id=obj_id,
                name=self._extract_name(obj_id),
                text=self._safe_attr(obj, "Text", ""),
            ))
        elif comp_type in (GuiComponentType.TABLE_CONTROL, GuiComponentType.GRID_VIEW):
            row_count = int(self._safe_attr(obj, "RowCount", 0))
            col_count = int(self._safe_attr(obj, "ColumnCount", 0))
            columns = []
            if comp_type == GuiComponentType.GRID_VIEW:
                for c in range(col_count):
                    try:
                        col_id = obj.ColumnOrder(c)
                        columns.append(SapTableColumn(
                            name=col_id,
                            title=str(obj.GetColumnTitles(col_id) or col_id),
                            position=c,
                        ))
                    except Exception:
                        columns.append(SapTableColumn(name=f"col_{c}", position=c))
            screen.tables.append(SapTable(
                id=obj_id,
                name=self._extract_name(obj_id),
                type=comp_type,
                row_count=row_count,
                column_count=col_count,
                visible_rows=int(self._safe_attr(obj, "VisibleRowCount", 0)),
                columns=columns,
            ))

    @staticmethod
    def _safe_attr(obj: Any, attr: str, default: Any = "") -> Any:
        """COM 객체 속성을 안전하게 읽기.

        default가 str이면 str로 변환, bool이면 원본 유지.
        """
        try:
            val = getattr(obj, attr, default)
            if val is None:
                return default
            # default 타입에 맞춰 변환 (bool은 str 변환하면 안 됨)
            if isinstance(default, bool):
                return bool(val)
            return str(val)
        except Exception:
            return default

    def _manual_scan(self, window_id: str, screen: ScreenOntology) -> None:
        """GetObjectTree 미지원 시 수동 탐색으로 화면 요소 수집.

        SAP GUI 윈도우 구조:
          wnd[0]
          ├── titl (titlebar)
          ├── mbar (menubar)
          ├── tbar[0] (system toolbar - Enter, Back, Save 등)
          ├── tbar[1] (application toolbar)
          ├── usr (user area - 필드, 테이블 등)
          └── sbar (statusbar)

        usr 영역과 각 tbar를 명시적으로 스캔하여 안정성을 높인다.
        """
        # 1. 사용자 영역 (필드, 테이블, 탭 등)
        try:
            usr = self.session.FindById(f"{window_id}/usr")
            self._scan_children(usr, screen)
        except Exception as e:
            logger.warning("사용자 영역 탐색 실패 [%s/usr]: %s", window_id, e)

        # 2. 메뉴바 (menus)
        try:
            mbar = self.session.FindById(f"{window_id}/mbar")
            children = mbar.Children
            for i in range(children.Count):
                menu = children(i)
                screen.menus.append(SapMenuItem(
                    id=str(menu.Id),
                    text=str(getattr(menu, "Text", "")),
                ))
        except Exception:
            pass  # 메뉴바 없는 화면 (모달 등)

        # 3. OKCode 필드 (tbar[0]/okcd - 항상 존재)
        try:
            okcd = self.session.FindById(f"{window_id}/tbar[0]/okcd")
            okcd_type = str(okcd.Type)
            comp = self._map_component_type(okcd_type)
            if comp == GuiComponentType.OK_CODE_FIELD:
                screen.fields.append(SapField(
                    id=str(okcd.Id),
                    name=self._extract_name(str(okcd.Id)),
                    type=comp,
                    label=str(getattr(okcd, "Text", "")),
                    value=str(getattr(okcd, "Text", "")),
                    changeable=bool(getattr(okcd, "Changeable", True)),
                    semantic=self._build_semantic(okcd, str(okcd.Id)),
                ))
        except Exception:
            pass

        # 4. 툴바 (버튼)
        for tbar_idx in range(3):
            try:
                tbar = self.session.FindById(f"{window_id}/tbar[{tbar_idx}]")
                self._scan_children(tbar, screen)
            except Exception:
                break  # tbar가 더 이상 없으면 중단

        # 4. 전체 윈도우 fallback (usr/tbar에서 못 찾은 경우)
        if not screen.fields and not screen.buttons:
            try:
                window = self.session.FindById(window_id)
                self._scan_children(window, screen)
            except Exception as e:
                logger.warning("윈도우 전체 탐색 실패 [%s]: %s", window_id, e)

    # 재귀 탐색 불필요한 컨테이너 타입
    _SKIP_TYPES = frozenset({
        "GuiLabel", "GuiTitlebar", "GuiStatusbar", "GuiStatusPane",
        "GuiPicture", "GuiHTMLViewer", "GuiTextedit",
    })

    def _scan_children(self, container: Any, screen: ScreenOntology, depth: int = 0) -> None:
        """컨테이너의 자식 요소를 재귀 탐색. 깊이 제한으로 속도 확보."""
        if depth > 8:
            return
        try:
            children = container.Children
            count = children.Count
        except Exception as e:
            logger.warning("화면 요소 탐색 중 오류: %s", e)
            return

        for i in range(count):
            try:
                child = children(i)
                child_type = str(child.Type)
            except Exception as e:
                logger.debug("자식 요소 접근 건너뜀 [index=%d]: %s", i, e)
                continue

            # 불필요한 타입은 즉시 건너뛰기 (COM 호출 최소화)
            if child_type in self._SKIP_TYPES:
                continue

            # OKCode는 _manual_scan에서 직접 추가하므로 중복 방지
            mapped = self._map_component_type(child_type)
            if mapped == GuiComponentType.OK_CODE_FIELD:
                continue

            try:
                child_id = str(child.Id)
                comp_type = self._map_component_type(child_type)

                if comp_type in (
                    GuiComponentType.TEXT_FIELD,
                    GuiComponentType.CTEXT_FIELD,
                    GuiComponentType.PASSWORD_FIELD,
                    GuiComponentType.OK_CODE_FIELD,
                ):
                    screen.fields.append(SapField(
                        id=child_id,
                        name=self._extract_name(child_id),
                        type=comp_type,
                        label=self._safe_attr(child, "Text", ""),
                        value=self._safe_attr(child, "Text", ""),
                        changeable=bool(self._safe_attr(child, "Changeable", False)),
                        max_length=self._get_max_length(child),
                        semantic=self._build_semantic(child, child_id),
                    ))
                elif comp_type == GuiComponentType.BUTTON:
                    screen.buttons.append(SapButton(
                        id=child_id,
                        name=self._extract_name(child_id),
                        text=self._safe_attr(child, "Text", ""),
                        tooltip=self._safe_attr(child, "Tooltip", ""),
                        icon_name=self._safe_attr(child, "IconName", ""),
                    ))
                elif comp_type == GuiComponentType.COMBO_BOX:
                    screen.fields.append(SapField(
                        id=child_id,
                        name=self._extract_name(child_id),
                        type=comp_type,
                        label=self._safe_attr(child, "Text", ""),
                        value=self._safe_attr(child, "Value", ""),
                        changeable=bool(self._safe_attr(child, "Changeable", False)),
                        semantic=self._build_semantic(child, child_id),
                    ))
                elif comp_type in (GuiComponentType.CHECK_BOX, GuiComponentType.RADIO_BUTTON):
                    screen.fields.append(SapField(
                        id=child_id,
                        name=self._extract_name(child_id),
                        type=comp_type,
                        label=self._safe_attr(child, "Text", ""),
                        changeable=bool(self._safe_attr(child, "Changeable", False)),
                        semantic=self._build_semantic(child, child_id),
                    ))
                elif comp_type == GuiComponentType.TAB:
                    screen.tabs.append(SapButton(
                        id=child_id,
                        name=self._extract_name(child_id),
                        text=str(getattr(child, "Text", "")),
                    ))
                    # 탭 안의 필드도 재귀 탐색
                    try:
                        if hasattr(child, "Children") and child.Children.Count > 0:
                            self._scan_children(child, screen, depth + 1)
                    except Exception:
                        pass
                    continue
                elif comp_type in (GuiComponentType.TREE, GuiComponentType.SHELL):
                    # GuiShell SubType이 GridView면 ALV Grid → tables로 분류
                    shell_sub_type = ""
                    if comp_type == GuiComponentType.SHELL:
                        shell_sub_type = self._safe_attr(child, "SubType", "")
                    if shell_sub_type == "GridView":
                        row_count = int(getattr(child, "RowCount", 0))
                        col_count = int(getattr(child, "ColumnCount", 0))
                        columns = []
                        scan_cols = min(col_count, 20)
                        for c in range(scan_cols):
                            try:
                                col_id = child.ColumnOrder(c)
                                titles = child.GetColumnTitles(col_id)
                                title = titles[0] if titles else col_id
                                columns.append(SapTableColumn(
                                    name=col_id, title=str(title), position=c,
                                ))
                            except Exception:
                                columns.append(SapTableColumn(name=f"col_{c}", position=c))
                        screen.tables.append(SapTable(
                            id=child_id,
                            name=self._extract_name(child_id),
                            type=GuiComponentType.SHELL,
                            sub_type="GridView",
                            row_count=row_count,
                            column_count=col_count,
                            visible_rows=int(getattr(child, "VisibleRowCount", 0)),
                            columns=columns,
                        ))
                    else:
                        # 트리/기타 쉘 — 위치만 기록, 데이터는 sap tree로 읽기
                        node_count = 0
                        try:
                            node_count = int(child.TopNodeKeyCount)
                        except Exception:
                            try:
                                node_keys = child.GetAllNodeKeys()
                                node_count = node_keys.Count if node_keys else 0
                            except Exception:
                                pass
                        screen.trees.append(SapTable(
                            id=child_id,
                            name=self._extract_name(child_id),
                            type=comp_type,
                            sub_type=shell_sub_type,
                            row_count=node_count,
                        ))
                    continue
                elif comp_type in (GuiComponentType.TABLE_CONTROL, GuiComponentType.GRID_VIEW):
                    row_count = int(getattr(child, "RowCount", 0))
                    col_count = int(getattr(child, "ColumnCount", 0))
                    columns = []
                    if comp_type == GuiComponentType.GRID_VIEW:
                        for c in range(min(col_count, 20)):
                            try:
                                col_id = child.ColumnOrder(c)
                                columns.append(SapTableColumn(
                                    name=col_id,
                                    title=str(child.GetColumnTitles(col_id) or col_id),
                                    position=c,
                                ))
                            except Exception:
                                columns.append(SapTableColumn(name=f"col_{c}", position=c))
                    screen.tables.append(SapTable(
                        id=child_id,
                        name=self._extract_name(child_id),
                        type=comp_type,
                        row_count=row_count,
                        column_count=col_count,
                        visible_rows=int(getattr(child, "VisibleRowCount", 0)),
                        columns=columns,
                    ))
                    # 테이블 안의 체크박스/라디오만 스캔 (일반 셀은 제외하여 노이즈 방지)
                    try:
                        if hasattr(child, "Children") and child.Children.Count > 0:
                            self._scan_table_controls(child, screen)
                    except Exception:
                        pass
                    continue
                else:
                    # 컨테이너: 재귀 탐색
                    try:
                        if hasattr(child, "Children") and child.Children.Count > 0:
                            self._scan_children(child, screen, depth + 1)
                    except Exception:
                        pass
                    continue

                # 리프 요소는 재귀 불필요
            except Exception as e:
                logger.debug("요소 처리 건너뜀 [index=%d]: %s", i, e)

    def _scan_table_controls(self, table: Any, screen: ScreenOntology, depth: int = 0) -> None:
        """테이블 내부에서 체크박스/라디오버튼만 수집 (일반 텍스트 셀은 무시)."""
        if depth > 3:
            return
        try:
            children = table.Children
            for i in range(children.Count):
                child = children(i)
                child_type = str(child.Type)
                comp_type = self._map_component_type(child_type)
                if comp_type in (GuiComponentType.CHECK_BOX, GuiComponentType.RADIO_BUTTON):
                    child_id = str(child.Id)
                    screen.fields.append(SapField(
                        id=child_id,
                        name=self._extract_name(child_id),
                        type=comp_type,
                        label=str(getattr(child, "Text", "")),
                        changeable=bool(getattr(child, "Changeable", False)),
                    ))
                elif hasattr(child, "Children"):
                    try:
                        if child.Children.Count > 0:
                            self._scan_table_controls(child, screen, depth + 1)
                    except Exception:
                        pass
        except Exception:
            pass

    # -------------------------------------------------------------------
    # Table Operations
    # -------------------------------------------------------------------

    def read_table(
        self,
        element_id: str,
        max_rows: int = 50,
        columns: list[str] | None = None,
    ) -> SapTable:
        """테이블 데이터 읽기. GuiGridView, GuiTableControl, GuiShell(ALV) 지원.

        max_rows: 최대 읽을 행 수 (기본 50, 최대 500)
        columns: 읽을 컬럼 이름 목록 (None이면 전체)
        """
        max_rows = min(max(max_rows, 1), 500)
        try:
            table = self.session.FindById(element_id)
            table_type = str(table.Type)

            if table_type == "GuiGridView":
                return self._read_grid_view(element_id, table, max_rows, columns)
            elif table_type == "GuiShell":
                sub_type = self._safe_attr(table, "SubType", "")
                if sub_type == "GridView":
                    return self._read_alv_grid(element_id, table, max_rows, columns)
                raise SapGuiError(
                    f"지원하지 않는 Shell SubType [{element_id}]: {sub_type}")
            else:
                return self._read_table_control(element_id, table)
        except SapGuiError:
            raise
        except Exception as e:
            raise SapGuiError(f"테이블 읽기 실패 [{element_id}]: {e}") from e

    def _read_grid_view(
        self, element_id: str, grid: Any,
        max_rows: int = 50, filter_columns: list[str] | None = None,
    ) -> SapTable:
        raw_row_count = grid.RowCount
        col_count = grid.ColumnCount
        columns = []
        for c in range(col_count):
            col_id = grid.ColumnOrder(c)
            columns.append(SapTableColumn(
                name=col_id,
                title=grid.GetColumnTitles(col_id)[0] if grid.GetColumnTitles(col_id) else col_id,
                position=c,
            ))

        read_cols = columns
        if filter_columns:
            fc_set = set(filter_columns)
            read_cols = [c for c in columns if c.name in fc_set]

        rows = []
        for r in range(min(raw_row_count, max_rows)):
            row_data = {}
            all_empty = True
            for col in read_cols:
                try:
                    val = grid.GetCellValue(r, col.name)
                    row_data[col.name] = val
                    if val and str(val).strip():
                        all_empty = False
                except Exception:
                    row_data[col.name] = ""
            if all_empty:
                break
            rows.append(row_data)

        return SapTable(
            id=element_id,
            type=GuiComponentType.GRID_VIEW,
            row_count=len(rows),
            column_count=col_count,
            visible_rows=grid.VisibleRowCount,
            columns=columns,
            rows=rows,
        )

    def _read_alv_grid(
        self, element_id: str, grid: Any,
        max_rows: int = 50, filter_columns: list[str] | None = None,
    ) -> SapTable:
        """GuiShell(SubType=GridView) ALV Grid 읽기. 스크롤 처리 포함."""
        raw_row_count = int(grid.RowCount)
        col_count = int(grid.ColumnCount)
        visible_rows = int(grid.VisibleRowCount)

        # 컬럼 수집
        columns = []
        for c in range(col_count):
            try:
                col_id = grid.ColumnOrder(c)
                titles = grid.GetColumnTitles(col_id)
                title = titles[0] if titles else col_id
                columns.append(SapTableColumn(
                    name=str(col_id), title=str(title), position=c,
                ))
            except Exception:
                columns.append(SapTableColumn(name=f"col_{c}", position=c))

        # 필터/정렬 상태
        filtered: list[str] = []
        sorted_cols: list[str] = []
        try:
            fk = grid.GetFilteredColumnKeys()
            if fk:
                filtered = [str(fk(i)) for i in range(fk.Count)]
        except Exception:
            pass
        try:
            sk = grid.GetSortedColumnKeys()
            if sk:
                sorted_cols = [str(sk(i)) for i in range(sk.Count)]
        except Exception:
            pass

        # 컬럼 필터링
        read_cols = columns
        if filter_columns:
            fc_set = set(filter_columns)
            read_cols = [c for c in columns if c.name in fc_set]

        # 행 데이터 수집 (스크롤 처리)
        max_rows = min(raw_row_count, max_rows)
        rows: list[dict[str, str]] = []
        if visible_rows == 0:
            return SapTable(
                id=element_id,
                type=GuiComponentType.SHELL,
                sub_type="GridView",
                row_count=0,
                column_count=col_count,
                visible_rows=0,
                columns=columns,
                rows=[],
                filtered_columns=filtered,
                sorted_columns=sorted_cols,
            )
        hit_empty = False
        read_count = 0
        while read_count < max_rows and not hit_empty:
            grid.FirstVisibleRow = read_count
            chunk_size = min(visible_rows, max_rows - read_count)
            for r in range(read_count, read_count + chunk_size):
                row_data: dict[str, str] = {}
                all_empty = True
                for col in read_cols:
                    try:
                        val = str(grid.GetCellValue(r, col.name))
                        row_data[col.name] = val
                        if val.strip():
                            all_empty = False
                    except Exception:
                        row_data[col.name] = ""
                if all_empty:
                    hit_empty = True
                    break
                rows.append(row_data)
            read_count += chunk_size

        return SapTable(
            id=element_id,
            type=GuiComponentType.SHELL,
            sub_type="GridView",
            row_count=len(rows),
            column_count=col_count,
            visible_rows=visible_rows,
            columns=columns,
            rows=rows,
            filtered_columns=filtered,
            sorted_columns=sorted_cols,
        )

    def _read_table_control(self, element_id: str, table: Any) -> SapTable:
        raw_row_count = table.RowCount
        visible_rows = table.VisibleRowCount
        columns = []
        for c in range(table.Columns.Count):
            col = table.Columns(c)
            columns.append(SapTableColumn(
                name=col.Name,
                title=col.Title,
                position=c,
            ))

        rows = []
        for r in range(min(raw_row_count, visible_rows)):
            row_data = {}
            all_empty = True
            for col in columns:
                try:
                    cell = table.GetCell(r, col.position)
                    val = str(cell.Text)
                    row_data[col.name] = val
                    if val.strip():
                        all_empty = False
                except Exception:
                    row_data[col.name] = ""
            if all_empty:
                break
            rows.append(row_data)

        return SapTable(
            id=element_id,
            type=GuiComponentType.TABLE_CONTROL,
            row_count=len(rows),
            column_count=len(columns),
            visible_rows=visible_rows,
            columns=columns,
            rows=rows,
        )

    # -------------------------------------------------------------------
    # Tree Operations (Document Flow 등)
    # -------------------------------------------------------------------

    def read_tree(self, element_id: str) -> list[SapTreeNode]:
        """트리 컨트롤 데이터 읽기 (Document Flow, BOM 등).

        SAP GUI의 GuiTree(Column Tree, List Tree, Simple Tree)를 지원.
        각 노드의 텍스트와 컬럼 아이템을 읽어 계층 구조로 반환.
        """
        try:
            tree = self.session.FindById(element_id)
            tree_type = str(tree.Type)

            if tree_type not in ("GuiTree", "GuiShell"):
                raise SapGuiError(
                    f"트리가 아닙니다 [{element_id}]: type={tree_type}")

            node_keys = tree.GetAllNodeKeys()
            if not node_keys:
                return []

            # 컬럼 이름 수집 (Column Tree인 경우) — 각 방법을 독립적으로 시도
            col_names: list[str] = []

            # 방법 1: GetColumnNames
            try:
                names = tree.GetColumnNames()
                if names and names.Count > 0:
                    for c in range(names.Count):
                        try:
                            col_names.append(str(names.ElementAt(c)))
                        except Exception:
                            col_names.append(str(names(c)))
            except Exception:
                pass

            # 방법 2: GetColumnHeaders
            if not col_names:
                try:
                    headers = tree.GetColumnHeaders()
                    if headers and headers.Count > 0:
                        for c in range(headers.Count):
                            try:
                                col_names.append(str(headers.ElementAt(c)))
                            except Exception:
                                col_names.append(str(headers(c)))
                except Exception:
                    pass

            # 방법 3: ColumnCount + GetColumnTitlesByIndex
            if not col_names:
                try:
                    col_count = tree.ColumnCount
                    for c in range(col_count):
                        try:
                            col_names.append(str(tree.GetColumnCol(c)))
                        except Exception:
                            col_names.append(f"col_{c}")
                except Exception:
                    pass

            # 트리 SubType 확인
            sub_type = ""
            try:
                sub_type = str(self._safe_attr(tree, "SubType", ""))
            except Exception:
                pass

            # 컬럼 이름 저장 (dispatcher에서 참조)
            self._tree_columns = col_names

            # 노드 데이터 수집
            nodes_by_key: dict[str, SapTreeNode] = {}
            root_keys: list[str] = []

            for i in range(node_keys.Count):
                try:
                    key = str(node_keys.ElementAt(i))
                except Exception:
                    key = str(node_keys(i))

                # 텍스트 수집 — 여러 방법 시도
                text = ""
                try:
                    text = str(tree.GetNodeTextByKey(key))
                except Exception:
                    pass
                if not text:
                    try:
                        text = str(tree.GetNodeTextByPath(key))
                    except Exception:
                        pass

                # 컬럼별 아이템 값
                items: dict[str, str] = {}
                for col_name in col_names:
                    try:
                        val = str(tree.GetItemText(key, col_name))
                        if val:
                            items[col_name] = val
                    except Exception:
                        pass

                # 컬럼 이름을 못 구했어도 아이템 직접 탐색 시도
                if not items and not col_names:
                    try:
                        ic = tree.GetItemCount(key)
                        for ci in range(ic):
                            try:
                                cn = str(tree.GetItemName(key, ci))
                                cv = str(tree.GetItemText(key, cn))
                                if cv:
                                    items[cn] = cv
                            except Exception:
                                pass
                    except Exception:
                        pass

                level = 0
                try:
                    level = int(tree.GetNodeLevel(key))
                except Exception:
                    pass

                # text가 비어있으면 아이템 첫 번째 값을 텍스트로 사용
                if not text and items:
                    text = next(iter(items.values()), "")

                node = SapTreeNode(
                    key=key,
                    text=text,
                    level=level,
                    items=items,
                )
                nodes_by_key[key] = node

            # --- 부모-자식 관계 구성 ---
            # 전략 1: GetSubNodesCol (자식 직접 조회)
            children_built = False
            try:
                has_children = set()
                for key in nodes_by_key:
                    try:
                        sub = tree.GetSubNodesCol(key)
                        if sub and sub.Count > 0:
                            for ci in range(sub.Count):
                                try:
                                    ck = str(sub.ElementAt(ci))
                                except Exception:
                                    ck = str(sub(ci))
                                if ck in nodes_by_key:
                                    nodes_by_key[key].children.append(nodes_by_key[ck])
                                    has_children.add(ck)
                    except Exception:
                        pass
                # root = 누구의 자식도 아닌 노드
                root_keys = [k for k in nodes_by_key if k not in has_children]
                children_built = len(has_children) > 0
            except Exception:
                pass

            # 전략 2: GetParentNodeKey
            if not children_built:
                root_keys.clear()
                parent_map: dict[str, str] = {}
                for key in nodes_by_key:
                    parent_key = None
                    try:
                        pk = tree.GetParentNodeKey(key)
                        if pk is not None:
                            pk_str = str(pk).strip()
                            if pk_str and pk_str in nodes_by_key:
                                parent_key = pk_str
                    except Exception:
                        pass
                    if parent_key:
                        parent_map[key] = parent_key
                    else:
                        root_keys.append(key)
                if parent_map:
                    for child_key, par_key in parent_map.items():
                        nodes_by_key[par_key].children.append(nodes_by_key[child_key])
                    children_built = True

            # 전략 3: level 기반 계층 재구성
            if not children_built and len(nodes_by_key) > 1:
                has_levels = any(n.level > 0 for n in nodes_by_key.values())
                if has_levels:
                    root_keys = []
                    ordered = list(nodes_by_key.values())
                    stack: list[SapTreeNode] = []
                    for nd in ordered:
                        nd.children = []
                    for nd in ordered:
                        while stack and stack[-1].level >= nd.level:
                            stack.pop()
                        if stack:
                            stack[-1].children.append(nd)
                        else:
                            root_keys.append(nd.key)
                        stack.append(nd)

            return [nodes_by_key[k] for k in root_keys if k in nodes_by_key]
        except SapGuiError:
            raise
        except Exception as e:
            raise SapGuiError(f"트리 읽기 실패 [{element_id}]: {e}") from e

    # -------------------------------------------------------------------
    # Tree Node Actions
    # -------------------------------------------------------------------

    def _get_tree(self, element_id: str) -> Any:
        """트리 컨트롤 객체를 가져오고 타입을 검증."""
        tree = self.session.FindById(element_id)
        ttype = str(tree.Type)
        if ttype not in ("GuiTree", "GuiShell"):
            raise SapGuiError(f"트리가 아닙니다 [{element_id}]: type={ttype}")
        return tree

    def tree_select_node(self, element_id: str, node_key: str) -> None:
        """트리 노드 선택."""
        try:
            tree = self._get_tree(element_id)
            tree.SelectNode(node_key)
        except SapGuiError:
            raise
        except Exception as e:
            raise SapGuiError(f"트리 노드 선택 실패 [{element_id}, key={node_key}]: {e}") from e

    def tree_double_click_node(self, element_id: str, node_key: str) -> None:
        """트리 노드 더블클릭 (SPRO IMG Activity 진입 등)."""
        try:
            tree = self._get_tree(element_id)
            tree.DoubleClickNode(node_key)
        except SapGuiError:
            raise
        except Exception as e:
            raise SapGuiError(f"트리 노드 더블클릭 실패 [{element_id}, key={node_key}]: {e}") from e

    def tree_expand_node(self, element_id: str, node_key: str) -> None:
        """트리 노드 확장 (하위 노드 펼치기)."""
        try:
            tree = self._get_tree(element_id)
            tree.ExpandNode(node_key)
        except SapGuiError:
            raise
        except Exception as e:
            raise SapGuiError(f"트리 노드 확장 실패 [{element_id}, key={node_key}]: {e}") from e

    def tree_collapse_node(self, element_id: str, node_key: str) -> None:
        """트리 노드 접기."""
        try:
            tree = self._get_tree(element_id)
            tree.CollapseNode(node_key)
        except SapGuiError:
            raise
        except Exception as e:
            raise SapGuiError(f"트리 노드 접기 실패 [{element_id}, key={node_key}]: {e}") from e

    def _try_tree_action(self, tree: Any, node_key: str, col: str) -> bool:
        """단일 컬럼에 대해 ClickLink/PressButton/ItemDoubleClick 시도. 성공 시 True."""
        for method in ("ClickLink", "PressButton", "ItemDoubleClick"):
            try:
                getattr(tree, method)(node_key, col)
                return True
            except Exception:
                pass
        return False

    # 트리 액션 컬럼 캐시: element_id → {"execute": col, "doc": col}
    _tree_action_cache: dict[str, dict[str, str]] = {}

    def tree_press_button(self, element_id: str, node_key: str, col_name: str) -> str:
        """트리 내 버튼/아이콘/링크 클릭 (SPRO 실행 아이콘 등).

        col_name이 비어있으면 캐시 또는 자동 탐색으로 실행 컬럼을 찾음.
        Returns: 실행에 사용된 컬럼 이름.
        """
        tree = self._get_tree(element_id)

        # 컬럼 지정된 경우: 해당 컬럼만 시도
        if col_name:
            if self._try_tree_action(tree, node_key, col_name):
                return col_name
            raise SapGuiError(
                f"트리 액션 실패 [{element_id}, key={node_key}, col={col_name}]")

        # SPRO 트리: 컬럼 고정 (1=doc, 2=execute)
        try:
            tcode = self.session.Info.Transaction.upper()
            if tcode == "SPRO":
                self._tree_action_cache[element_id] = {"execute": "2", "doc": "1"}
                if self._try_tree_action(tree, node_key, "2"):
                    return "2"
        except Exception:
            pass

        # 캐시에 execute 컬럼이 있으면 우선 시도
        cached = self._tree_action_cache.get(element_id, {})
        if cached.get("execute"):
            if self._try_tree_action(tree, node_key, cached["execute"]):
                return cached["execute"]

        # 컬럼 미지정: 모든 가능한 컬럼을 탐색
        cols_to_try: list[str] = []
        try:
            names = tree.GetColumnNames()
            if names:
                for i in range(names.Count):
                    try:
                        cols_to_try.append(str(names.ElementAt(i)))
                    except Exception:
                        cols_to_try.append(str(names(i)))
        except Exception:
            pass
        # ColumnCount 기반 보완
        try:
            cc = int(tree.ColumnCount)
            known = set(cols_to_try)
            for i in range(cc):
                try:
                    cn = str(tree.GetColumnCol(i))
                    if cn not in known:
                        cols_to_try.append(cn)
                        known.add(cn)
                except Exception:
                    cn = str(i + 1)
                    if cn not in known:
                        cols_to_try.append(cn)
                        known.add(cn)
        except Exception:
            pass
        # 숫자 컬럼의 빈 번호 채우기
        known = set(cols_to_try)
        nums = [int(c) for c in known if c.isdigit()]
        if nums:
            for n in range(min(nums), max(nums) + 1):
                s = str(n)
                if s not in known:
                    cols_to_try.append(s)
                    known.add(s)

        before_screen = None
        try:
            before_screen = self.session.Info.ScreenNumber
        except Exception:
            pass

        _DOC_KEYWORDS = ("document", "dokumentation", "hilfe", "help", "info", "hypertext")
        doc_col = None

        for col in cols_to_try:
            if self._try_tree_action(tree, node_key, col):
                changed = False
                try:
                    if before_screen is not None and self.session.Info.ScreenNumber != before_screen:
                        changed = True
                except Exception:
                    pass
                if not changed:
                    try:
                        if self.has_modal():
                            changed = True
                    except Exception:
                        pass
                if not changed:
                    continue
                # documentation 화면인지 확인
                try:
                    wnd_id = "wnd[1]" if self.has_modal() else "wnd[0]"
                    title = self.session.FindById(wnd_id).Text.lower()
                    if any(kw in title for kw in _DOC_KEYWORDS):
                        doc_col = col
                        try:
                            self.session.FindById(wnd_id).SendVKey(3)
                        except Exception:
                            pass
                        continue
                except Exception:
                    pass
                # execute 성공 — 캐시 저장
                self._tree_action_cache[element_id] = {
                    "execute": col, **({"doc": doc_col} if doc_col else {})}
                return col

        # documentation만 열린 경우
        if doc_col is not None:
            self._tree_action_cache[element_id] = {"doc": doc_col}
            self._try_tree_action(tree, node_key, doc_col)
            return doc_col

        raise SapGuiError(
            f"트리 액션 실패 [{element_id}, key={node_key}]: "
            f"시도한 컬럼={cols_to_try}, 화면 변경 없음")

    def set_table_cell(self, element_id: str, row: int, col: str, value: str) -> None:
        """테이블 셀에 값 설정."""
        try:
            table = self.session.FindById(element_id)
            ttype = str(table.Type)
            if ttype == "GuiGridView" or (
                ttype == "GuiShell" and self._safe_attr(table, "SubType", "") == "GridView"
            ):
                table.ModifyCell(row, col, value)
            else:
                cell = table.GetCell(row, col)
                cell.Text = value
        except Exception as e:
            raise SapGuiError(f"테이블 셀 설정 실패: {e}") from e

    def select_table_row(self, element_id: str, row: int) -> None:
        """테이블에서 행 선택."""
        try:
            table = self.session.FindById(element_id)
            ttype = str(table.Type)
            if ttype == "GuiGridView" or (
                ttype == "GuiShell" and self._safe_attr(table, "SubType", "") == "GridView"
            ):
                table.SelectedRows = str(row)
            else:
                table.GetAbsoluteRow(row).Selected = True
        except Exception as e:
            raise SapGuiError(f"테이블 행 선택 실패 [{element_id}, row={row}]: {e}") from e

    def double_click_table_cell(self, element_id: str, row: int, col: str) -> None:
        """테이블 셀 더블클릭 (ALV 행 진입 등)."""
        try:
            table = self.session.FindById(element_id)
            ttype = str(table.Type)
            if ttype == "GuiGridView" or (
                ttype == "GuiShell" and self._safe_attr(table, "SubType", "") == "GridView"
            ):
                if not col:
                    col = str(table.ColumnOrder(0))
                table.SetCurrentCell(row, col)
                table.DoubleClickCurrentCell()
            else:
                table.GetAbsoluteRow(row).Selected = True
                table.DoubleClick(row, col)
        except Exception as e:
            raise SapGuiError(
                f"테이블 더블클릭 실패 [{element_id}, row={row}, col={col}]: {e}"
            ) from e

    # -------------------------------------------------------------------
    # Modal Window Handling
    # -------------------------------------------------------------------

    def has_modal(self) -> bool:
        """모달 윈도우(팝업) 존재 여부."""
        try:
            self.session.FindById("wnd[1]")
            return True
        except Exception:
            return False

    def get_modal_text(self) -> str:
        """모달 윈도우의 텍스트 내용."""
        try:
            modal = self.session.FindById("wnd[1]")
            return str(modal.Text)
        except Exception:
            return ""

    def close_modal(self, vkey: int = 0) -> None:
        """모달 윈도우 닫기 (기본: Enter)."""
        try:
            self.session.FindById("wnd[1]").SendVKey(vkey)
        except Exception as e:
            raise SapGuiError(f"모달 닫기 실패: {e}") from e

    # -------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------

    def _build_semantic(self, element: Any, element_id: str) -> SemanticContext:
        """SAP COM 요소에서 시맨틱 정보 자동 추출."""
        name = self._extract_name(element_id)

        # COM 프록시는 getattr도 예외를 던질 수 있으므로 모두 안전 처리
        tooltip = self._safe_attr(element, "Tooltip", "")

        raw_label = ""
        try:
            left_label = element.LeftLabel
            if left_label:
                raw_label = str(left_label.Text)
        except Exception:
            pass
        if not raw_label:
            raw_label = tooltip

        sap_field_name = name.split("-")[-1] if "-" in name else name

        return SemanticContext(
            raw_label=raw_label,
            sap_field_name=sap_field_name,
            description=tooltip,
        )

    @staticmethod
    def _get_max_length(element: Any) -> int:
        """COM 요소의 MaxLength 안전 추출."""
        try:
            val = getattr(element, "MaxLength", 0)
            return int(val) if val else 0
        except Exception:
            return 0

    @staticmethod
    def _extract_name(element_id: str) -> str:
        """요소 ID에서 이름 부분 추출. 예: 'wnd[0]/usr/txtFIELD' → 'FIELD'"""
        if "/" in element_id:
            last = element_id.rsplit("/", 1)[-1]
            for prefix in ("txt", "ctxt", "cmb", "chk", "rad", "btn", "tab", "tabp", "lbl",
                           "shell", "sub", "ssub", "tbl"):
                if last.startswith(prefix):
                    return last[len(prefix):]
            return last
        return element_id

    # SAP GUI가 반환하는 타입명 오타 보정 맵
    _TYPE_ALIASES: dict[str, str] = {
        "GuiOkCodeFiled": "GuiOkCodeField",  # SAP GUI 일부 버전 오타
    }

    @classmethod
    def _map_component_type(cls, type_str: str) -> GuiComponentType:
        """타입 문자열을 GuiComponentType enum으로 매핑."""
        type_str = cls._TYPE_ALIASES.get(type_str, type_str)
        try:
            return GuiComponentType(type_str)
        except ValueError:
            return GuiComponentType.UNKNOWN

    @staticmethod
    def _get_label_for(element: Any) -> str:
        """요소의 라벨 텍스트 추출 시도."""
        try:
            left_label = element.LeftLabel
            if left_label:
                return str(left_label.Text)
        except Exception:
            pass
        try:
            return str(element.Tooltip) if element.Tooltip else ""
        except Exception:
            return ""
