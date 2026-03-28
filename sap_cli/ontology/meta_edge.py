"""메타엣지 엔진 - 관계를 정의하는 규칙.

SOS Tier 1: "메타엣지 하나를 변경하면 하위 모든 일반 엣지가 영향받는 레버리지 효과"

SAP에서의 메타엣지:
- FieldActivation: 자재유형에 따라 어떤 탭/필드가 활성화되는지
- TransactionFlow: 트랜잭션 실행 후 자동 생성되는 후속 문서/프로세스
- AuthorizationScope: 사용자 권한에 따른 접근 범위 (SOS ReBAC)
- ValidationRule: 필드 입력 검증 규칙
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class MetaEdgeType(str, Enum):
    """메타엣지 유형."""
    FIELD_ACTIVATION = "FieldActivation"
    TRANSACTION_FLOW = "TransactionFlow"
    AUTHORIZATION_SCOPE = "AuthorizationScope"
    VALIDATION_RULE = "ValidationRule"
    FIELD_DEFAULT = "FieldDefault"
    SCREEN_VARIANT = "ScreenVariant"


class Condition(BaseModel):
    """메타엣지 조건식."""
    field: str
    operator: str  # eq, ne, in, not_in, contains, gt, lt, between
    value: Any


class Effect(BaseModel):
    """메타엣지 효과."""
    target: str
    action: str  # activate, deactivate, set_value, set_required, set_options, navigate
    value: Any = None


class MetaEdge(BaseModel):
    """메타엣지 - 하위 엣지들을 일괄 생성/변경하는 상위 규칙.

    SOS: "DB의 외래키 제약, API의 인증 규칙, 코드의 인터페이스 정의"와
    동일한 추상화 수준. SAP에서는 필드 활성화, 트랜잭션 흐름, 권한 규칙.
    """
    id: str
    meta_edge_type: MetaEdgeType
    name: str
    description: str = ""
    transaction: str = ""
    conditions: list[Condition] = Field(default_factory=list)
    effects: list[Effect] = Field(default_factory=list)
    priority: int = 0

    def evaluate(self, context: dict[str, Any]) -> list[Effect]:
        """컨텍스트(현재 필드 값들)에 대해 조건을 평가하고 해당 효과를 반환."""
        if all(self._check_condition(cond, context) for cond in self.conditions):
            return self.effects
        return []

    def _check_condition(self, cond: Condition, context: dict[str, Any]) -> bool:
        ctx_value = context.get(cond.field)
        if ctx_value is None:
            return False

        if cond.operator == "eq":
            return ctx_value == cond.value
        elif cond.operator == "ne":
            return ctx_value != cond.value
        elif cond.operator == "in":
            return ctx_value in cond.value
        elif cond.operator == "not_in":
            return ctx_value not in cond.value
        elif cond.operator == "contains":
            return cond.value in str(ctx_value)
        elif cond.operator == "gt":
            return ctx_value > cond.value
        elif cond.operator == "lt":
            return ctx_value < cond.value
        return False


class MetaEdgeRegistry(BaseModel):
    """메타엣지 레지스트리 - 트랜잭션별 메타엣지 관리."""
    edges: list[MetaEdge] = Field(default_factory=list)

    def add(self, edge: MetaEdge) -> None:
        self.edges.append(edge)

    def get_by_transaction(self, tcode: str) -> list[MetaEdge]:
        return [e for e in self.edges if e.transaction == tcode or e.transaction == ""]

    def get_by_type(self, edge_type: MetaEdgeType) -> list[MetaEdge]:
        return [e for e in self.edges if e.meta_edge_type == edge_type]

    def evaluate_all(self, tcode: str, context: dict[str, Any]) -> list[Effect]:
        """특정 트랜잭션의 모든 메타엣지를 평가하여 적용할 효과 목록 반환."""
        effects = []
        edges = sorted(self.get_by_transaction(tcode), key=lambda e: e.priority)
        for edge in edges:
            effects.extend(edge.evaluate(context))
        return effects


# ---------------------------------------------------------------------------
# 사전 정의된 SAP 메타엣지 (도메인 지식)
# ---------------------------------------------------------------------------

def create_mm_meta_edges() -> list[MetaEdge]:
    """MM(Material Management) 모듈의 메타엣지."""
    return [
        MetaEdge(
            id="mm_field_activation_fert",
            meta_edge_type=MetaEdgeType.FIELD_ACTIVATION,
            name="완제품(FERT) 필드 활성화",
            description="자재유형이 FERT일 때 활성화되는 탭/필드",
            transaction="MM01",
            conditions=[Condition(field="MTART", operator="eq", value="FERT")],
            effects=[
                Effect(target="TAB_BASIC", action="activate", value=True),
                Effect(target="TAB_MRP", action="activate", value=True),
                Effect(target="TAB_ACCOUNTING", action="activate", value=True),
                Effect(target="TAB_COSTING", action="activate", value=True),
                Effect(target="TAB_PURCHASING", action="deactivate", value=True),
            ],
        ),
        MetaEdge(
            id="mm_field_activation_roh",
            meta_edge_type=MetaEdgeType.FIELD_ACTIVATION,
            name="원자재(ROH) 필드 활성화",
            description="자재유형이 ROH일 때 활성화되는 탭/필드",
            transaction="MM01",
            conditions=[Condition(field="MTART", operator="eq", value="ROH")],
            effects=[
                Effect(target="TAB_BASIC", action="activate", value=True),
                Effect(target="TAB_PURCHASING", action="activate", value=True),
                Effect(target="TAB_MRP", action="activate", value=True),
                Effect(target="TAB_ACCOUNTING", action="activate", value=True),
                Effect(target="TAB_COSTING", action="deactivate", value=True),
            ],
        ),
        MetaEdge(
            id="mm_plant_storage_dependency",
            meta_edge_type=MetaEdgeType.FIELD_ACTIVATION,
            name="플랜트-저장위치 종속성",
            description="플랜트 선택 시 저장위치 옵션이 변경됨",
            transaction="MM01",
            conditions=[Condition(field="WERKS", operator="ne", value="")],
            effects=[
                Effect(target="LGORT", action="set_options", value="DEPENDS_ON_WERKS"),
            ],
        ),
    ]


def create_sd_meta_edges() -> list[MetaEdge]:
    """SD(Sales & Distribution) 모듈의 메타엣지."""
    return [
        MetaEdge(
            id="sd_va01_flow",
            meta_edge_type=MetaEdgeType.TRANSACTION_FLOW,
            name="수주(VA01) 후속 프로세스",
            description="수주 저장 시 발생하는 후속 문서/프로세스",
            transaction="VA01",
            conditions=[Condition(field="ACTION", operator="eq", value="SAVE")],
            effects=[
                Effect(target="VBELN", action="set_value", value="GENERATED"),
                Effect(target="NEXT_TRANSACTIONS", action="navigate", value=["VA03", "VL01N", "VF01"]),
            ],
        ),
        MetaEdge(
            id="sd_order_type_fields",
            meta_edge_type=MetaEdgeType.FIELD_ACTIVATION,
            name="오더유형별 필드 활성화",
            description="오더유형(OR/SO/RE 등)에 따라 화면 필드 구성 변경",
            transaction="VA01",
            conditions=[Condition(field="AUART", operator="eq", value="OR")],
            effects=[
                Effect(target="PRICING_FIELDS", action="activate", value=True),
                Effect(target="SHIPPING_FIELDS", action="activate", value=True),
            ],
        ),
    ]


def create_default_registry() -> MetaEdgeRegistry:
    """기본 메타엣지 레지스트리 생성."""
    registry = MetaEdgeRegistry()
    for edge in create_mm_meta_edges():
        registry.add(edge)
    for edge in create_sd_meta_edges():
        registry.add(edge)
    return registry
