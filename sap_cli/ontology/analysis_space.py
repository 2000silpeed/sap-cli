"""6개 분석공간 프레임워크.

SOS Tier 2: 지능 시스템이 문제를 처리하는 6개의 독립적이면서 상호연결된 위상적 차원.
SAP 자동화에서 AI가 "지금 상태에서 무엇을 해야 하는가"를 구조적으로 판단하는 프레임워크.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from .schema import (
    ActionRecord,
    ActionType,
    AvailableAction,
    ScreenOntology,
    TransactionOntology,
)


# ---------------------------------------------------------------------------
# 계층공간 (Hierarchy Space)
# ---------------------------------------------------------------------------

class HierarchyNode(BaseModel):
    """계층 트리의 노드."""
    id: str
    label: str
    type: str
    level: int
    children: list[HierarchyNode] = Field(default_factory=list)


class HierarchySpace(BaseModel):
    """계층공간: SAP 모듈 → 트랜잭션 → 화면 → 영역 → 필드.

    AI가 현재 위치를 계층적으로 파악하여 컨텍스트를 이해한다.
    SOS: "Leiden 커뮤니티 계층 C0~C3"과 동일한 원리.
    """
    current_path: list[str] = Field(default_factory=list)

    def locate(self, session_info: dict[str, Any]) -> list[str]:
        """현재 SAP 세션의 계층 위치를 반환."""
        path = []
        if system := session_info.get("system_name"):
            path.append(f"System:{system}")
        if tcode := session_info.get("transaction"):
            module = self._infer_module(tcode)
            if module:
                path.append(f"Module:{module}")
            path.append(f"Transaction:{tcode}")
        if screen := session_info.get("screen_number"):
            path.append(f"Screen:{screen}")
        if program := session_info.get("program"):
            path.append(f"Program:{program}")
        self.current_path = path
        return path

    def _infer_module(self, tcode: str) -> str:
        """트랜잭션 코드에서 모듈 추론."""
        prefix_map = {
            "MM": "MM (Material Management)",
            "ME": "MM (Purchasing)",
            "MB": "MM (Inventory)",
            "VA": "SD (Sales)",
            "VL": "SD (Shipping)",
            "VF": "SD (Billing)",
            "FI": "FI (Financial Accounting)",
            "FB": "FI (Financial Accounting)",
            "CO": "CO (Controlling)",
            "PP": "PP (Production Planning)",
            "PA": "HR (Personnel Admin)",
            "QA": "QM (Quality Management)",
            "WM": "WM (Warehouse Management)",
            "SE": "BC (Basis/Development)",
            "SM": "BC (System Management)",
            "SU": "BC (User Management)",
        }
        for prefix, module in prefix_map.items():
            if tcode.upper().startswith(prefix):
                return module
        return ""


# ---------------------------------------------------------------------------
# 시간공간 (Temporal Space)
# ---------------------------------------------------------------------------

class ScreenTransition(BaseModel):
    """화면 전환 기록."""
    from_screen: int
    to_screen: int
    trigger_action: ActionType
    trigger_id: str = ""
    count: int = 1


class TemporalSpace(BaseModel):
    """시간공간: 트랜잭션 실행 순서, 화면 전환 시퀀스.

    AI가 '다음 단계'를 예측. 예: MM03 → 기본뷰 → MRP뷰 → 회계뷰 순서.
    SOS: "고객 생명주기: 잠재→가입→활성→이탈→복귀"와 동일한 시간적 패턴.
    """
    action_history: list[ActionRecord] = Field(default_factory=list)
    screen_transitions: list[ScreenTransition] = Field(default_factory=list)

    def record_action(self, action: ActionRecord) -> None:
        self.action_history.append(action)
        if action.before_screen != action.after_screen:
            self._update_transition(action)

    def _update_transition(self, action: ActionRecord) -> None:
        for t in self.screen_transitions:
            if t.from_screen == action.before_screen and t.to_screen == action.after_screen:
                t.count += 1
                return
        self.screen_transitions.append(ScreenTransition(
            from_screen=action.before_screen,
            to_screen=action.after_screen,
            trigger_action=action.action_type,
            trigger_id=action.target_id,
        ))

    def predict_next_screen(self, current_screen: int) -> int | None:
        """현재 화면에서 가장 빈번한 다음 화면 예측."""
        candidates = [t for t in self.screen_transitions if t.from_screen == current_screen]
        if not candidates:
            return None
        return max(candidates, key=lambda t: t.count).to_screen

    def get_common_sequence(self, tcode: str) -> list[int]:
        """가장 흔한 화면 전환 경로."""
        if not self.screen_transitions:
            return []
        screens = set()
        for t in self.screen_transitions:
            screens.add(t.from_screen)
            screens.add(t.to_screen)
        return sorted(screens)


# ---------------------------------------------------------------------------
# 재귀공간 (Recursive Space)
# ---------------------------------------------------------------------------

class SubTask(BaseModel):
    """프랙탈 분해된 서브태스크."""
    id: str
    name: str
    description: str = ""
    action_sequence: list[ActionRecord] = Field(default_factory=list)
    sub_tasks: list[SubTask] = Field(default_factory=list)
    completed: bool = False


class RecursiveSpace(BaseModel):
    """재귀공간: 복잡한 SAP 작업을 서브태스크로 프랙탈 분해.

    예: '구매오더 생성' → '헤더입력→품목추가→수량입력→납품일→저장'
    SOS: "프랙탈 분해(10→50개 쪼개기), 자기유사성"
    """
    task_tree: SubTask | None = None

    def decompose_transaction(self, tcode: str, ontology: TransactionOntology) -> SubTask:
        """트랜잭션을 서브태스크로 분해."""
        root = SubTask(
            id=f"task_{tcode}",
            name=f"Execute {tcode}: {ontology.title}",
            description=ontology.description,
        )

        for screen_num, screen in sorted(ontology.screens.items()):
            screen_task = SubTask(
                id=f"task_{tcode}_screen_{screen_num}",
                name=f"Screen {screen_num}: {screen.title}",
            )

            for field in screen.get_changeable_fields():
                field_task = SubTask(
                    id=f"task_{tcode}_{screen_num}_{field.name}",
                    name=f"Set {field.label or field.name}",
                    description=f"Enter value for {field.label} (field: {field.name})",
                )
                screen_task.sub_tasks.append(field_task)

            root.sub_tasks.append(screen_task)

        self.task_tree = root
        return root


# ---------------------------------------------------------------------------
# 구조공간 (Structural Space)
# ---------------------------------------------------------------------------

class FieldDependency(BaseModel):
    """필드 간 종속성."""
    source_field: str
    target_field: str
    dependency_type: str  # value_change, visibility, options, validation
    description: str = ""


class StructuralSpace(BaseModel):
    """구조공간: 화면 내 필드 간 관계 그래프.

    필드 종속성 파악: 플랜트 선택 → 저장위치 목록 변경.
    SOS: "NOMIK 모듈 간 관계 발견"과 동일.
    """
    dependencies: list[FieldDependency] = Field(default_factory=list)

    def add_dependency(self, dep: FieldDependency) -> None:
        self.dependencies.append(dep)

    def get_affected_fields(self, source_field: str) -> list[str]:
        """특정 필드 변경 시 영향받는 필드 목록 (인과 전파)."""
        affected = set()
        queue = [source_field]
        while queue:
            current = queue.pop(0)
            for dep in self.dependencies:
                if dep.source_field == current and dep.target_field not in affected:
                    affected.add(dep.target_field)
                    queue.append(dep.target_field)
        return list(affected)


# ---------------------------------------------------------------------------
# 인과공간 (Causal Space)
# ---------------------------------------------------------------------------

class CausalLink(BaseModel):
    """액션 → 결과 인과관계."""
    action_type: ActionType
    action_target: str
    result_type: str  # screen_change, status_message, field_update, error, document_created
    result_detail: str = ""
    probability: float = 1.0  # 관측된 빈도 기반 확률
    observation_count: int = 0


class CausalSpace(BaseModel):
    """인과공간: 액션 → 결과 인과관계 추적.

    '저장 버튼 클릭 → 어떤 에러가 발생할 수 있는가'
    SOS: "Impact Analysis: 변경 영향도 분석"
    """
    causal_links: list[CausalLink] = Field(default_factory=list)

    def record_causality(self, action: ActionRecord, result_type: str, result_detail: str) -> None:
        """관측된 인과관계 기록."""
        for link in self.causal_links:
            if (link.action_type == action.action_type
                    and link.action_target == action.target_id
                    and link.result_type == result_type):
                link.observation_count += 1
                return

        self.causal_links.append(CausalLink(
            action_type=action.action_type,
            action_target=action.target_id,
            result_type=result_type,
            result_detail=result_detail,
            observation_count=1,
        ))

    def predict_result(self, action_type: ActionType, target_id: str) -> list[CausalLink]:
        """액션 실행 시 예상되는 결과 예측."""
        return [
            link for link in self.causal_links
            if link.action_type == action_type and link.action_target == target_id
        ]


# ---------------------------------------------------------------------------
# 다중공간 (Cross Space) - 모든 공간의 통합 분석
# ---------------------------------------------------------------------------

class CrossSpaceAnalysis(BaseModel):
    """다중공간 분석 결과 - AI 의사결정을 위한 통합 컨텍스트."""
    hierarchy_path: list[str] = Field(default_factory=list)
    predicted_next_screen: int | None = None
    available_actions: list[AvailableAction] = Field(default_factory=list)
    affected_fields: list[str] = Field(default_factory=list)
    predicted_results: list[CausalLink] = Field(default_factory=list)
    recommended_action: AvailableAction | None = None
    reasoning: str = ""


class CrossSpace(BaseModel):
    """다중공간: 모든 공간 동시 고려한 의사결정.

    AI가 '지금 상태에서 가장 효과적인 다음 액션'을 결정.
    SOS: "팔란티어 AIP = 창발적 구현체"
    """
    hierarchy: HierarchySpace = Field(default_factory=HierarchySpace)
    temporal: TemporalSpace = Field(default_factory=TemporalSpace)
    recursive: RecursiveSpace = Field(default_factory=RecursiveSpace)
    structural: StructuralSpace = Field(default_factory=StructuralSpace)
    causal: CausalSpace = Field(default_factory=CausalSpace)

    def analyze(
        self,
        session_info: dict[str, Any],
        screen: ScreenOntology,
    ) -> CrossSpaceAnalysis:
        """6공간 통합 분석 수행."""
        analysis = CrossSpaceAnalysis()

        # 계층공간: 현재 위치 파악
        analysis.hierarchy_path = self.hierarchy.locate(session_info)

        # 시간공간: 다음 화면 예측
        analysis.predicted_next_screen = self.temporal.predict_next_screen(
            screen.screen_number
        )

        # 구조공간 + 인과공간: 가용 액션 + 결과 예측
        analysis.available_actions = screen.get_available_actions()

        return analysis
