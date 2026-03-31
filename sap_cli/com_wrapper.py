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
    ScreenOntology,
    SemanticContext,
    StatusMessageType,
)

# win32com은 Windows 전용 - 임포트 실패 시 스텁 제공
_WIN32 = platform.system() == "Windows"

if _WIN32:
    import win32com.client  # type: ignore[import-untyped]


class SapGuiError(Exception):
    """SAP GUI 조작 중 발생하는 에러."""


class SapGuiWrapper:
    """SAP GUI Scripting API COM 래퍼.

    Running Object Table(ROT)을 통해 실행 중인 SAP GUI에 연결하고,
    세션을 통해 모든 GUI 조작을 수행한다.
    """

    def __init__(self) -> None:
        self._application: Any = None
        self._connection: Any = None
        self._session: Any = None

    # -------------------------------------------------------------------
    # Connection Management
    # -------------------------------------------------------------------

    def connect(self, connection_index: int = 0, session_index: int = 0) -> SapSession:
        """실행 중인 SAP GUI에 연결.

        SAP GUI가 이미 실행 중이고 로그인된 상태여야 한다.
        ROT(Running Object Table)을 통해 SAP GUI 프로세스에 접근한다.
        """
        if not _WIN32:
            raise SapGuiError("SAP GUI COM is only available on Windows")

        try:
            rot_entry = win32com.client.GetObject("SAPGUI")
            self._application = rot_entry.GetScriptingEngine
        except Exception as e:
            raise SapGuiError(
                "SAP GUI에 연결할 수 없습니다. SAP GUI가 실행 중인지, "
                "스크립팅이 활성화되어 있는지 확인하세요.\n"
                "서버: sapgui/user_scripting = TRUE\n"
                "클라이언트: SAP GUI Options > Accessibility > Enable scripting"
            ) from e

        if self._application.Connections.Count == 0:
            raise SapGuiError("활성 SAP 연결이 없습니다. SAP에 로그인하세요.")

        if connection_index >= self._application.Connections.Count:
            raise SapGuiError(
                f"연결 인덱스 {connection_index} 없음. "
                f"활성 연결 수: {self._application.Connections.Count}"
            )

        self._connection = self._application.Connections(connection_index)

        if session_index >= self._connection.Sessions.Count:
            raise SapGuiError(
                f"세션 인덱스 {session_index} 없음. "
                f"활성 세션 수: {self._connection.Sessions.Count}"
            )

        self._session = self._connection.Sessions(session_index)
        return self.get_session_info()

    @property
    def session(self) -> Any:
        if not self.is_connected:
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
        """필드에 값 설정."""
        try:
            element = self.session.FindById(element_id)
            element.Text = value
        except Exception as e:
            raise SapGuiError(f"필드 설정 실패 [{element_id}]: {e}") from e

    def get_field(self, element_id: str) -> str:
        """필드 값 읽기."""
        try:
            element = self.session.FindById(element_id)
            return str(element.Text)
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
        """버튼 클릭."""
        try:
            self.session.FindById(element_id).Press()
        except Exception as e:
            raise SapGuiError(f"버튼 클릭 실패 [{element_id}]: {e}") from e

    def send_vkey(self, vkey: int) -> None:
        """VKey 전송 (Enter=0, Back=3, Execute=8, Save=11, Cancel=12)."""
        try:
            self.session.FindById("wnd[0]").SendVKey(vkey)
        except Exception as e:
            raise SapGuiError(f"VKey 전송 실패 [{vkey}]: {e}") from e

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
            self.session.FindById(element_id).Value = value
        except Exception as e:
            raise SapGuiError(f"콤보박스 선택 실패 [{element_id}]: {e}") from e

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

    def get_screen(self, window_id: str = "wnd[0]") -> ScreenOntology:
        """현재 화면의 전체 UI 구조를 ScreenOntology로 반환.

        수동 탐색(Children 재귀) 방식 사용. GetObjectTree는 id만 반환하여
        각 요소마다 FindById COM 호출이 필요해 느림. 수동 탐색은 Children
        한 번 호출로 모든 자식을 가져오므로 COM 왕복이 적다.
        """
        info = self.session.Info
        screen = ScreenOntology(
            transaction=info.Transaction,
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
            ))
        elif comp_type == GuiComponentType.COMBO_BOX:
            screen.fields.append(SapField(
                id=obj_id,
                name=self._extract_name(obj_id),
                type=comp_type,
                label=self._get_label_for(obj),
                value=self._safe_attr(obj, "Value", ""),
                changeable=bool(self._safe_attr(obj, "Changeable", False)),
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
        """COM 객체 속성을 안전하게 읽기."""
        try:
            val = getattr(obj, attr, default)
            return str(val) if val is not None else default
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

        # 2. 툴바 (버튼)
        for tbar_idx in range(3):
            try:
                tbar = self.session.FindById(f"{window_id}/tbar[{tbar_idx}]")
                self._scan_children(tbar, screen)
            except Exception:
                break  # tbar가 더 이상 없으면 중단

        # 3. 전체 윈도우 fallback (usr/tbar에서 못 찾은 경우)
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
            for i in range(count):
                child = children(i)
                child_type = str(child.Type)

                # 불필요한 타입은 즉시 건너뛰기 (COM 호출 최소화)
                if child_type in self._SKIP_TYPES:
                    continue

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
                        label=str(getattr(child, "Text", "")),
                        value=str(getattr(child, "Text", "")),
                        changeable=bool(getattr(child, "Changeable", False)),
                    ))
                elif comp_type == GuiComponentType.BUTTON:
                    screen.buttons.append(SapButton(
                        id=child_id,
                        name=self._extract_name(child_id),
                        text=str(getattr(child, "Text", "")),
                        tooltip=str(getattr(child, "Tooltip", "")),
                        icon_name=str(getattr(child, "IconName", "")),
                    ))
                elif comp_type == GuiComponentType.COMBO_BOX:
                    screen.fields.append(SapField(
                        id=child_id,
                        name=self._extract_name(child_id),
                        type=comp_type,
                        label=str(getattr(child, "Text", "")),
                        value=str(getattr(child, "Value", "")),
                        changeable=bool(getattr(child, "Changeable", False)),
                    ))
                elif comp_type in (GuiComponentType.CHECK_BOX, GuiComponentType.RADIO_BUTTON):
                    screen.fields.append(SapField(
                        id=child_id,
                        name=self._extract_name(child_id),
                        type=comp_type,
                        label=str(getattr(child, "Text", "")),
                        changeable=bool(getattr(child, "Changeable", False)),
                    ))
                elif comp_type == GuiComponentType.TAB:
                    screen.tabs.append(SapButton(
                        id=child_id,
                        name=self._extract_name(child_id),
                        text=str(getattr(child, "Text", "")),
                    ))
                    continue
                elif comp_type in (GuiComponentType.TABLE_CONTROL, GuiComponentType.GRID_VIEW):
                    row_count = int(getattr(child, "RowCount", 0))
                    col_count = int(getattr(child, "ColumnCount", 0))
                    columns = []
                    if comp_type == GuiComponentType.GRID_VIEW:
                        for c in range(col_count):
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
            logger.warning("화면 요소 탐색 중 오류: %s", e)

    # -------------------------------------------------------------------
    # Table Operations
    # -------------------------------------------------------------------

    def read_table(self, element_id: str) -> SapTable:
        """테이블 데이터 읽기."""
        try:
            table = self.session.FindById(element_id)
            table_type = str(table.Type)

            if table_type == "GuiGridView":
                return self._read_grid_view(element_id, table)
            else:
                return self._read_table_control(element_id, table)
        except Exception as e:
            raise SapGuiError(f"테이블 읽기 실패 [{element_id}]: {e}") from e

    def _read_grid_view(self, element_id: str, grid: Any) -> SapTable:
        row_count = grid.RowCount
        col_count = grid.ColumnCount
        columns = []
        for c in range(col_count):
            col_id = grid.ColumnOrder(c)
            columns.append(SapTableColumn(
                name=col_id,
                title=grid.GetColumnTitles(col_id)[0] if grid.GetColumnTitles(col_id) else col_id,
                position=c,
            ))

        rows = []
        for r in range(min(row_count, 100)):
            row_data = {}
            for col in columns:
                try:
                    row_data[col.name] = grid.GetCellValue(r, col.name)
                except Exception:
                    row_data[col.name] = ""
            rows.append(row_data)

        return SapTable(
            id=element_id,
            type=GuiComponentType.GRID_VIEW,
            row_count=row_count,
            column_count=col_count,
            visible_rows=grid.VisibleRowCount,
            columns=columns,
            rows=rows,
        )

    def _read_table_control(self, element_id: str, table: Any) -> SapTable:
        row_count = table.RowCount
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
        for r in range(min(row_count, visible_rows)):
            row_data = {}
            for col in columns:
                try:
                    cell = table.GetCell(r, col.position)
                    row_data[col.name] = str(cell.Text)
                except Exception:
                    row_data[col.name] = ""
            rows.append(row_data)

        return SapTable(
            id=element_id,
            type=GuiComponentType.TABLE_CONTROL,
            row_count=row_count,
            column_count=len(columns),
            visible_rows=visible_rows,
            columns=columns,
            rows=rows,
        )

    def set_table_cell(self, element_id: str, row: int, col: str, value: str) -> None:
        """테이블 셀에 값 설정."""
        try:
            table = self.session.FindById(element_id)
            if str(table.Type) == "GuiGridView":
                table.ModifyCell(row, col, value)
            else:
                cell = table.GetCell(row, col)
                cell.Text = value
        except Exception as e:
            raise SapGuiError(f"테이블 셀 설정 실패: {e}") from e

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

    @staticmethod
    def _map_component_type(type_str: str) -> GuiComponentType:
        """타입 문자열을 GuiComponentType enum으로 매핑."""
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
