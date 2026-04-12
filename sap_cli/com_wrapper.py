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

    # 사용자가 sap run으로 실행한 트랜잭션으로 간주할 접두사 목록.
    # SAP가 내부적으로 호출하는 프로그램(SE38 등)은 제외.
    _INTERNAL_TCODES = frozenset({"SE38", "SE80", "SE37", "SAPLSMTR_NAVIGATION"})

    def __init__(self) -> None:
        self._application: Any = None
        self._connection: Any = None
        self._session: Any = None
        self._user_tcode: str | None = None  # sap run으로 실행한 원래 tcode

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

    def get_field_info(self, element_id: str) -> dict[str, str]:
        """필드 값과 이름 정보를 함께 읽기."""
        try:
            element = self.session.FindById(element_id)
            name = self._safe_attr(element, "Name", "")
            return {
                "name": name or self._extract_name(element_id),
                "field": element_id,
                "value": str(element.Text),
            }
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
                        for c in range(col_count):
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

    def read_table(self, element_id: str) -> SapTable:
        """테이블 데이터 읽기. GuiGridView, GuiTableControl, GuiShell(ALV) 지원."""
        try:
            table = self.session.FindById(element_id)
            table_type = str(table.Type)

            if table_type == "GuiGridView":
                return self._read_grid_view(element_id, table)
            elif table_type == "GuiShell":
                sub_type = self._safe_attr(table, "SubType", "")
                if sub_type == "GridView":
                    return self._read_alv_grid(element_id, table)
                raise SapGuiError(
                    f"지원하지 않는 Shell SubType [{element_id}]: {sub_type}")
            else:
                return self._read_table_control(element_id, table)
        except SapGuiError:
            raise
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

    def _read_alv_grid(self, element_id: str, grid: Any) -> SapTable:
        """GuiShell(SubType=GridView) ALV Grid 읽기. 스크롤 처리 포함."""
        row_count = int(grid.RowCount)
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

        # 행 데이터 수집 (스크롤 처리 — 최대 500행)
        max_rows = min(row_count, 500)
        rows: list[dict[str, str]] = []
        if visible_rows == 0:
            return SapTable(
                id=element_id,
                type=GuiComponentType.SHELL,
                sub_type="GridView",
                row_count=row_count,
                column_count=col_count,
                visible_rows=0,
                columns=columns,
                rows=[],
                filtered_columns=filtered,
                sorted_columns=sorted_cols,
            )
        read_count = 0
        while read_count < max_rows:
            grid.FirstVisibleRow = read_count
            chunk_size = min(visible_rows, max_rows - read_count)
            for r in range(read_count, read_count + chunk_size):
                row_data: dict[str, str] = {}
                for col in columns:
                    try:
                        row_data[col.name] = str(grid.GetCellValue(r, col.name))
                    except Exception:
                        row_data[col.name] = ""
                rows.append(row_data)
            read_count += chunk_size

        return SapTable(
            id=element_id,
            type=GuiComponentType.SHELL,
            sub_type="GridView",
            row_count=row_count,
            column_count=col_count,
            visible_rows=visible_rows,
            columns=columns,
            rows=rows,
            filtered_columns=filtered,
            sorted_columns=sorted_cols,
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

    def tree_press_button(self, element_id: str, node_key: str, col_name: str) -> None:
        """트리 내 버튼/아이콘/링크 클릭 (SPRO 실행 아이콘 등).

        col_name이 비어있으면 모든 컬럼을 자동 탐색하여
        화면이 바뀌는 액션을 찾아 실행.
        """
        tree = self._get_tree(element_id)

        # 컬럼 지정된 경우: 해당 컬럼만 시도
        if col_name:
            if self._try_tree_action(tree, node_key, col_name):
                return
            raise SapGuiError(
                f"트리 액션 실패 [{element_id}, key={node_key}, col={col_name}]")

        # 컬럼 미지정: 모든 가능한 컬럼을 탐색
        # 1) GetColumnNames에서 가져온 컬럼
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
        # 2) ColumnCount 기반으로 빠진 컬럼 보완 (0~ColumnCount 범위의 숫자)
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
                    # 인덱스로 직접 이름 생성해서 시도
                    cn = str(i + 1)
                    if cn not in known:
                        cols_to_try.append(cn)
                        known.add(cn)
        except Exception:
            pass

        before_screen = None
        try:
            before_screen = self.session.Info.ScreenNumber
        except Exception:
            pass

        for col in cols_to_try:
            if self._try_tree_action(tree, node_key, col):
                # 화면 변경 확인
                try:
                    after_screen = self.session.Info.ScreenNumber
                    if before_screen is not None and after_screen != before_screen:
                        return  # 화면이 바뀜 = 성공
                except Exception:
                    pass
                # 모달 팝업이 떴는지 확인
                try:
                    if self.has_modal():
                        return
                except Exception:
                    pass

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
