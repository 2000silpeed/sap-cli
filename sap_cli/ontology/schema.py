"""SAP Domain Ontology Schema.

팔란티어 OAG 모델의 4요소를 SAP GUI에 적용:
- Object Types: SAP 시스템 객체 (세션, 트랜잭션, 화면, 필드 등)
- Link Types: 객체 간 관계 (계층, 네비게이션, 종속성)
- Property Types: 의미 풍부화된 속성 (SOS 벡터 풍부화)
- Action Types: 실행 가능한 동작 (팔란티어 Kinetic Elements)
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class GuiComponentType(str, Enum):
    """SAP GUI Scripting API 컴포넌트 타입."""
    TEXT_FIELD = "GuiTextField"
    CTEXT_FIELD = "GuiCTextField"
    PASSWORD_FIELD = "GuiPasswordField"
    COMBO_BOX = "GuiComboBox"
    CHECK_BOX = "GuiCheckBox"
    RADIO_BUTTON = "GuiRadioButton"
    BUTTON = "GuiButton"
    LABEL = "GuiLabel"
    TABLE_CONTROL = "GuiTableControl"
    GRID_VIEW = "GuiGridView"
    TREE = "GuiTree"
    TAB_STRIP = "GuiTabStrip"
    TAB = "GuiTab"
    MENU_BAR = "GuiMenubar"
    MENU = "GuiMenu"
    TOOLBAR = "GuiToolbar"
    STATUSBAR = "GuiStatusbar"
    STATUS_PANE = "GuiStatusPane"
    MAIN_WINDOW = "GuiMainWindow"
    MODAL_WINDOW = "GuiModalWindow"
    USER_AREA = "GuiUserArea"
    SHELL = "GuiShell"
    CONTAINER_SHELL = "GuiContainerShell"
    SCROLL_CONTAINER = "GuiScrollContainer"
    SIMPLE_CONTAINER = "GuiSimpleContainer"
    BOX = "GuiBox"
    CUSTOM_CONTROL = "GuiCustomControl"
    OK_CODE_FIELD = "GuiOkCodeField"
    TITLEBAR = "GuiTitlebar"
    PICTURE = "GuiPicture"
    TEXTEDIT = "GuiTextedit"
    HTML_VIEWER = "GuiHTMLViewer"
    INPUT_FIELD_CONTROL = "GuiInputFieldControl"
    SPLITTER_CONTAINER = "GuiSplitterContainer"
    UNKNOWN = "Unknown"


class StatusMessageType(str, Enum):
    """SAP 상태 메시지 유형."""
    SUCCESS = "S"
    WARNING = "W"
    ERROR = "E"
    INFO = "I"
    ABORT = "A"


class LinkType(str, Enum):
    """온톨로지 관계 유형."""
    # 계층 관계 (구조공간)
    SYSTEM_HAS_SESSION = "SYSTEM_HAS_SESSION"
    SESSION_RUNS = "SESSION_RUNS"
    TRANSACTION_HAS_SCREEN = "TRANSACTION_HAS_SCREEN"
    SCREEN_CONTAINS = "SCREEN_CONTAINS"

    # 네비게이션 관계 (시간공간)
    NAVIGATES_TO = "NAVIGATES_TO"
    TRANSACTION_CALLS = "TRANSACTION_CALLS"

    # 종속성 관계 (인과공간)
    FIELD_DEPENDS_ON = "FIELD_DEPENDS_ON"
    FIELD_VALIDATES = "FIELD_VALIDATES"
    BUTTON_TRIGGERS = "BUTTON_TRIGGERS"

    # 크로스 트랜잭션 관계 (Phase 11 온톨로지)
    SAME_FIELD = "SAME_FIELD"               # 동일 필드가 다른 트랜잭션에 존재
    DOCUMENT_FLOW = "DOCUMENT_FLOW"         # 문서 흐름 (VA01 → VL01N)
    FIELD_DETERMINES = "FIELD_DETERMINES"   # 필드가 다른 필드를 결정


class ActionType(str, Enum):
    """팔란티어 Kinetic Elements - 실행 가능한 동작 유형."""
    SET_FIELD_VALUE = "SetFieldValue"
    PRESS_BUTTON = "PressButton"
    SEND_VKEY = "SendVKey"
    RUN_TRANSACTION = "RunTransaction"
    SELECT_TABLE_ROW = "SelectTableRow"
    SELECT_TABLE_CELL = "SelectTableCell"
    SELECT_COMBO_ITEM = "SelectComboItem"
    TOGGLE_CHECKBOX = "ToggleCheckbox"
    SELECT_RADIO = "SelectRadio"
    SELECT_TAB = "SelectTab"
    SELECT_MENU = "SelectMenu"
    READ_SCREEN = "ReadScreen"
    SELECT_TREE_NODE = "SelectTreeNode"
    EXPAND_TREE_NODE = "ExpandTreeNode"
    DOUBLE_CLICK_TREE_NODE = "DoubleClickTreeNode"
    COLLAPSE_TREE_NODE = "CollapseTreeNode"


# ---------------------------------------------------------------------------
# VKey 매핑 (SAP GUI 키보드 키)
# ---------------------------------------------------------------------------

VKEY_MAP: dict[int, str] = {
    0: "Enter",
    3: "Back (F3)",
    8: "Execute (F8)",
    11: "Save (Ctrl+S)",
    12: "Cancel (F12)",
    15: "F1 Help",
    16: "F4 Search Help",
    27: "Print (Ctrl+P)",
    33: "Ctrl+Shift+F6",
    70: "Ctrl+E",
    71: "Ctrl+F",
    73: "Ctrl+G",
    83: "Ctrl+F5",
}


# ---------------------------------------------------------------------------
# Object Types (클래스 - "무엇이 존재하는가")
# ---------------------------------------------------------------------------

class SemanticContext(BaseModel):
    """SOS 벡터 풍부화 - 필드의 의미적 컨텍스트.

    이중 언어 지원: description/synonyms는 영어, _ko 접미사는 한국어.
    learn 시 SAP COM 속성에서 자동 추출되며, annotate로 수동 보강 가능.
    """
    raw_label: str = ""
    sap_field_name: str = ""
    description: str = ""
    description_ko: str = ""
    synonyms: list[str] = Field(default_factory=list)
    synonyms_ko: list[str] = Field(default_factory=list)
    validation: str = ""
    domain_context: str = ""


class SapSystem(BaseModel):
    """SAP 시스템 연결 정보."""
    sid: str = ""
    host: str = ""
    port: int = 3200
    client: str = ""
    description: str = ""


class SapSession(BaseModel):
    """SAP GUI 세션 컨텍스트."""
    session_id: str = ""
    user: str = ""
    language: str = ""
    transaction: str = ""
    screen_number: int = 0
    program: str = ""
    system_name: str = ""
    client: str = ""
    application_server: str = ""


class SapTransaction(BaseModel):
    """SAP 트랜잭션 코드 정보."""
    tcode: str
    title: str = ""
    module: str = ""
    description: str = ""
    screens: list[SapScreenMeta] = Field(default_factory=list)


class SapScreenMeta(BaseModel):
    """화면 메타데이터 (온톨로지 참조용)."""
    screen_number: int
    program: str = ""
    title: str = ""
    description: str = ""


class SapField(BaseModel):
    """SAP GUI 필드 (입력/출력)."""
    id: str
    name: str = ""
    type: GuiComponentType = GuiComponentType.TEXT_FIELD
    label: str = ""
    value: str = ""
    changeable: bool = False
    required: bool = False
    max_length: int = 0
    semantic: SemanticContext = Field(default_factory=SemanticContext)


class SapTableColumn(BaseModel):
    """테이블 컬럼 정의."""
    name: str
    title: str = ""
    position: int = 0


class SapTable(BaseModel):
    """SAP GUI 테이블/그리드."""
    id: str
    name: str = ""
    type: GuiComponentType = GuiComponentType.TABLE_CONTROL
    sub_type: str = ""  # GuiShell의 SubType (예: "GridView")
    row_count: int = 0
    column_count: int = 0
    visible_rows: int = 0
    columns: list[SapTableColumn] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    filtered_columns: list[str] = Field(default_factory=list)
    sorted_columns: list[str] = Field(default_factory=list)


class SapTreeNode(BaseModel):
    """SAP GUI 트리 노드 (Document Flow 등)."""
    key: str
    text: str = ""
    level: int = 0
    items: dict[str, str] = Field(default_factory=dict)
    children: list["SapTreeNode"] = Field(default_factory=list)
    child_count: int | None = Field(default=None, description="depth 제한 시 실제 자식 수")


class SapButton(BaseModel):
    """SAP GUI 버튼."""
    id: str
    name: str = ""
    text: str = ""
    tooltip: str = ""
    icon_name: str = ""
    vkey: int | None = None
    emphasized: bool = False


class SapStatusMessage(BaseModel):
    """SAP 상태바 메시지."""
    type: StatusMessageType = StatusMessageType.SUCCESS
    message: str = ""


class SapMenuItem(BaseModel):
    """메뉴 아이템."""
    id: str
    text: str = ""
    children: list[SapMenuItem] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Screen Ontology (화면 단위 온톨로지)
# ---------------------------------------------------------------------------

class ScreenOntology(BaseModel):
    """특정 화면의 전체 온톨로지 - GetObjectTree() 결과의 구조화된 표현.

    NOMIK 패턴: "Scan once, query everything"
    이 모델 하나로 화면의 모든 Object, Link, Action을 조회할 수 있다.
    """
    transaction: str = ""
    screen_number: int = 0
    program: str = ""
    title: str = ""

    # Object Types
    fields: list[SapField] = Field(default_factory=list)
    tables: list[SapTable] = Field(default_factory=list)
    trees: list[SapTable] = Field(default_factory=list)  # 트리 컨트롤 (Document Flow 등) - id/type만 스캔 시 기록
    buttons: list[SapButton] = Field(default_factory=list)
    menus: list[SapMenuItem] = Field(default_factory=list)
    tabs: list[SapButton] = Field(default_factory=list)
    status: SapStatusMessage | None = None

    # 원본 UI 트리 (디버깅/학습용)
    raw_tree: dict[str, Any] | None = Field(default=None, exclude=True)

    def get_field_by_name(self, name: str) -> SapField | None:
        """SAP 필드명으로 필드 검색."""
        for f in self.fields:
            if f.name == name or f.semantic.sap_field_name == name:
                return f
        return None

    def get_field_by_id(self, element_id: str) -> SapField | None:
        """요소 ID로 필드 검색."""
        for f in self.fields:
            if f.id == element_id:
                return f
        return None

    def get_field_by_label(self, label: str) -> SapField | None:
        """라벨 텍스트로 필드 검색 (부분 일치, 한국어 동의어 포함)."""
        label_lower = label.lower()
        for f in self.fields:
            if label_lower in f.label.lower() or label_lower in f.semantic.raw_label.lower():
                return f
            if any(label_lower in syn.lower() for syn in f.semantic.synonyms):
                return f
            if any(label_lower in syn.lower() for syn in f.semantic.synonyms_ko):
                return f
        return None

    def get_changeable_fields(self) -> list[SapField]:
        """입력 가능한 필드만 반환."""
        return [f for f in self.fields if f.changeable]

    def get_available_actions(self) -> list[AvailableAction]:
        """현재 화면에서 가능한 모든 Action 목록."""
        actions: list[AvailableAction] = []

        for field in self.get_changeable_fields():
            actions.append(AvailableAction(
                action_type=ActionType.SET_FIELD_VALUE,
                target_id=field.id,
                target_label=field.label or field.name,
                description=f"Set '{field.label or field.name}' value",
            ))

        for btn in self.buttons:
            actions.append(AvailableAction(
                action_type=ActionType.PRESS_BUTTON,
                target_id=btn.id,
                target_label=btn.text or btn.tooltip,
                description=f"Press '{btn.text or btn.tooltip}'",
            ))

        for tab in self.tabs:
            actions.append(AvailableAction(
                action_type=ActionType.SELECT_TAB,
                target_id=tab.id,
                target_label=tab.text,
                description=f"Select tab '{tab.text}'",
            ))

        for table in self.tables:
            actions.append(AvailableAction(
                action_type=ActionType.SELECT_TABLE_ROW,
                target_id=table.id,
                target_label=table.name,
                description=f"Select row in table '{table.name}' ({table.row_count} rows, {table.column_count} cols)",
            ))

        for menu in self.menus:
            actions.append(AvailableAction(
                action_type=ActionType.SELECT_MENU,
                target_id=menu.id,
                target_label=menu.text,
                description=f"Select menu '{menu.text}'",
            ))

        return actions


# ---------------------------------------------------------------------------
# Action Types (Kinetic Elements - "무엇을 할 수 있는가")
# ---------------------------------------------------------------------------

class ActionPrecondition(BaseModel):
    """액션 실행 전 조건."""
    field_id: str = ""
    condition: str = ""
    description: str = ""


class ActionEffect(BaseModel):
    """액션 실행 후 효과."""
    effect_type: str = ""
    description: str = ""


class AvailableAction(BaseModel):
    """현재 컨텍스트에서 실행 가능한 액션."""
    action_type: ActionType
    target_id: str
    target_label: str = ""
    description: str = ""
    params: dict[str, Any] = Field(default_factory=dict)
    preconditions: list[ActionPrecondition] = Field(default_factory=list)
    effects: list[ActionEffect] = Field(default_factory=list)


class ActionRecord(BaseModel):
    """실행된 액션 기록 (시간공간 - 액션 시퀀스 추적)."""
    action_type: ActionType
    target_id: str
    params: dict[str, Any] = Field(default_factory=dict)
    result_status: SapStatusMessage | None = None
    before_screen: int = 0
    after_screen: int = 0
    timestamp: str = ""


# ---------------------------------------------------------------------------
# Link (관계 정의)
# ---------------------------------------------------------------------------

class OntologyLink(BaseModel):
    """두 온톨로지 객체 간의 관계."""
    link_type: LinkType
    source_id: str
    target_id: str
    properties: dict[str, Any] = Field(default_factory=dict)
    description: str = ""


class ActionStep(BaseModel):
    """액션 경로의 단일 실행 단계."""
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    hint: str = ""


class ActionPath(BaseModel):
    """트리 네비게이션 경로 — SPRO 등에서 특정 설정까지의 경로.

    path 예시: "Enterprise Structure > Definition > Logistics > Shipping Point"
    """
    context: str = ""
    keywords: list[str] = Field(default_factory=list)
    steps: list[ActionStep] = Field(default_factory=list)
    success_count: int = 0
    updated_at: float = 0


# ---------------------------------------------------------------------------
# Transaction Ontology (트랜잭션 단위 온톨로지)
# ---------------------------------------------------------------------------

class TransactionOntology(BaseModel):
    """트랜잭션의 전체 온톨로지 - 여러 화면의 통합 지식.

    SOS 6공간을 통합한 트랜잭션 레벨 지식 표현:
    - 계층공간: 화면 → 영역 → 필드 계층
    - 시간공간: 화면 전환 시퀀스
    - 구조공간: 필드 간 종속성
    - 인과공간: 액션 → 결과 매핑
    """
    tcode: str
    title: str = ""
    module: str = ""
    description: str = ""

    # 화면별 온톨로지
    screens: dict[int, ScreenOntology] = Field(default_factory=dict)

    # 화면 전환 그래프 (시간공간)
    screen_flow: list[OntologyLink] = Field(default_factory=list)

    # 필드 종속성 (구조공간/인과공간)
    field_dependencies: list[OntologyLink] = Field(default_factory=list)

    # 액션 시퀀스 기록 (시간공간)
    recorded_sequences: list[list[ActionRecord]] = Field(default_factory=list)

    # 관련 트랜잭션 (네비게이션 관계)
    related_transactions: list[str] = Field(default_factory=list)

    # AI 네비게이션 레시피 참조 (recipe_id 목록) — 외부 recipe.py 연동용
    recipes: list[str] = Field(default_factory=list)

    # 경로 기반 액션 (SPRO 등 트리 네비게이션 경로를 온톨로지에 직접 저장)
    action_paths: dict[str, "ActionPath"] = Field(default_factory=dict)

    def get_screen(self, screen_number: int) -> ScreenOntology | None:
        return self.screens.get(screen_number)

    def add_screen(self, screen: ScreenOntology) -> None:
        self.screens[screen.screen_number] = screen

    def get_all_fields(self) -> list[SapField]:
        """모든 화면의 필드를 통합 반환."""
        fields = []
        for screen in self.screens.values():
            fields.extend(screen.fields)
        return fields

    def find_action_path(self, query: str) -> list[tuple[str, "ActionPath"]]:
        """키워드로 action_paths 검색. (path, ActionPath) 쌍 반환."""
        query_lower = query.lower()
        results = []
        for path, ap in self.action_paths.items():
            score = 0
            if query_lower in path.lower():
                score += 3
            if query_lower in ap.context.lower():
                score += 2
            for kw in ap.keywords:
                if query_lower in kw.lower():
                    score += 3
            if score > 0:
                results.append((score, path, ap))
        results.sort(key=lambda x: (-x[0], -x[2].success_count))
        return [(p, a) for _, p, a in results]


# ---------------------------------------------------------------------------
# Business Process (업무 프로세스 - 트랜잭션 간 연결)
# ---------------------------------------------------------------------------

class DataFlow(BaseModel):
    """스텝 간 데이터 전달 — 앞 스텝 출력 → 뒤 스텝 입력."""
    source_step: int
    source_field: str
    target_step: int
    target_field: str
    description: str = ""


class ProcessStep(BaseModel):
    """업무 프로세스의 단일 스텝 = 트랜잭션 1회 실행."""
    step_number: int
    tcode: str
    description: str = ""
    input_fields: dict[str, str] = Field(default_factory=dict)
    expected_output: list[str] = Field(default_factory=list)
    optional: bool = False


class BusinessProcess(BaseModel):
    """여러 트랜잭션을 연결한 업무 프로세스."""
    id: str
    name: str
    module: str = ""
    description: str = ""
    steps: list[ProcessStep] = Field(default_factory=list)
    data_flows: list[DataFlow] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)

    def validate_integrity(self) -> list[str]:
        """프로세스 무결성 검증. 문제가 있으면 에러 메시지 리스트 반환."""
        errors: list[str] = []
        step_numbers = [s.step_number for s in self.steps]
        if len(step_numbers) != len(set(step_numbers)):
            errors.append("중복된 step_number가 존재합니다")
        for df in self.data_flows:
            if df.source_step not in step_numbers:
                errors.append(f"data_flow의 source_step {df.source_step}이 steps에 없습니다")
            if df.target_step not in step_numbers:
                errors.append(f"data_flow의 target_step {df.target_step}이 steps에 없습니다")
            if df.source_step >= df.target_step:
                errors.append(f"data_flow의 source_step({df.source_step})이 target_step({df.target_step})보다 크거나 같습니다")
        return errors


# ---------------------------------------------------------------------------
# Process Execution (업무 프로세스 실행 기록)
# ---------------------------------------------------------------------------

class StepResult(BaseModel):
    """단일 스텝의 실행 결과."""
    step_number: int
    tcode: str
    status: str = ""  # "success", "error", "skipped"
    outputs: dict[str, str] = Field(default_factory=dict)
    error: str = ""
    timestamp: str = ""


class ProcessExecution(BaseModel):
    """업무 프로세스 1회 실행 기록."""
    process_id: str
    process_name: str = ""
    started_at: str = ""
    finished_at: str = ""
    status: str = ""  # "running", "completed", "failed", "aborted"
    context: dict[str, str] = Field(default_factory=dict)
    step_results: list[StepResult] = Field(default_factory=list)
    current_step: int = 0


# ---------------------------------------------------------------------------
# Field Dependency Graph (필드 종속성 그래프) — Phase 11-2
# ---------------------------------------------------------------------------

class FieldDependencyType(str, Enum):
    """필드 종속성 유형."""
    VALUE_DETERMINES = "value_determines"     # 값이 다른 필드 값을 결정 (KUNNR → ZTERM)
    INPUT_ORDER = "input_order"               # 입력 순서 종속 (WERKS 먼저 → LGORT)
    LOOKUP = "lookup"                         # 조회 종속 (MATNR → MAKTX)
    FIELD_ACTIVATION = "field_activation"     # 필드/탭 활성화 (MTART → 뷰 목록)


class FieldRelationship(BaseModel):
    """두 필드 간의 종속/결정 관계."""
    source_field: str               # 결정하는 필드 (sap_field_name)
    target_field: str               # 종속되는 필드 (sap_field_name)
    dependency_type: FieldDependencyType
    description: str = ""
    observed_in: list[str] = Field(default_factory=list)  # 관찰된 트랜잭션 목록


class FieldDependencyGraph(BaseModel):
    """글로벌 필드 종속성 그래프."""
    relationships: list[FieldRelationship] = Field(default_factory=list)
    last_rebuilt: str = ""


# ---------------------------------------------------------------------------
# Document Flow (문서 흐름 온톨로지) — Phase 11-3
# ---------------------------------------------------------------------------

class DocumentFlowType(str, Enum):
    """문서 흐름 유형."""
    CREATES = "creates"         # 생성 (VA01 → VL01N: 수주가 출하를 생성)
    REFERENCES = "references"   # 참조 (MIGO → ME21N: 입고가 PO를 참조)
    SUCCEEDS = "succeeds"       # 후속 (VL01N → VF01: 출하 후 대금청구)
    GENERATES = "generates"     # 자동 생성 (MIGO → 회계전표 자동 생성)


class DocumentFlowEdge(BaseModel):
    """트랜잭션 간 문서 흐름 엣지."""
    source_tcode: str
    target_tcode: str
    flow_type: DocumentFlowType = DocumentFlowType.REFERENCES
    linking_field: str = ""     # 연결 필드 (VBELN, EBELN 등)
    description: str = ""
    confidence: str = "high"    # high: DataFlow 기반, low: related_transactions 기반


class DocumentFlow(BaseModel):
    """글로벌 문서 흐름 온톨로지."""
    edges: list[DocumentFlowEdge] = Field(default_factory=list)
    last_rebuilt: str = ""


# ---------------------------------------------------------------------------
# Field Registry (크로스 트랜잭션 필드 인덱스)
# ---------------------------------------------------------------------------

class FieldOccurrence(BaseModel):
    """필드가 출현하는 위치 — 트랜잭션/화면/element_id."""
    tcode: str
    screen_number: int
    element_id: str
    label: str = ""
    changeable: bool = False


class FieldRegistryEntry(BaseModel):
    """하나의 SAP 필드명에 대한 글로벌 레지스트리 항목."""
    sap_field_name: str
    description: str = ""
    occurrences: list[FieldOccurrence] = Field(default_factory=list)


class FieldRegistry(BaseModel):
    """글로벌 필드 레지스트리 — 모든 트랜잭션의 필드를 sap_field_name으로 통합 인덱스."""
    fields: dict[str, FieldRegistryEntry] = Field(default_factory=dict)
    last_rebuilt: str = ""


# ---------------------------------------------------------------------------
# MCP Session (AI 실행 세션) — Phase 14-1
# ---------------------------------------------------------------------------

class SessionStep(BaseModel):
    """세션 내 개별 단계."""
    step_number: int
    timestamp: str = ""

    command: str = ""
    tool_name: str = ""
    arguments: dict[str, Any] = Field(default_factory=dict)
    source: str = ""
    cli_invocation: str = ""
    raw_argv: list[str] = Field(default_factory=list)

    intent: str = ""

    outcome: str = ""
    error: str = ""

    before_transaction: str = ""
    before_screen: int = 0
    after_transaction: str = ""
    after_screen: int = 0
    status_message: str = ""

    is_exploration: bool = False
    is_recovery: bool = False


class McpSession(BaseModel):
    """AI 실행 세션 — 사용자 요청부터 최종 결과까지."""
    id: str
    started_at: str = ""
    finished_at: str | None = None
    status: str = "running"

    agent: str = ""
    sap_user: str = ""
    sap_system: str = ""
    source: str = "mcp"

    user_request: str = ""
    ai_reasoning: str = ""

    steps: list[SessionStep] = Field(default_factory=list)

    outcome: str = ""
    outcome_summary: str = ""

    candidate: bool = False
    approved: bool = False
    recipe_id: str | None = None


# TransactionOntology에서 참조하는 forward ref 해결
SapTransaction.model_rebuild()
