"""Generated command metadata for adaptive SAP GUI UI actions.

The executable action list is derived from the local component capability
ontology, which is itself overlaid with the SAP GUI Scripting API graph at
runtime.  Dispatcher, CLI, MCP, and ReBAC use this module so newly modelled
actions can be surfaced without hand wiring each command in four places.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from .scripting_ontology import COMPONENT_CAPABILITIES


@dataclass(frozen=True, slots=True)
class UiActionDef:
    action: str
    command: str
    tool_name: str
    cli_name: str
    permission: str
    risk_level: str
    description: str


READ_ACTIONS = {
    "get_property",
    "inspect",
    "read",
    "read_cell",
    "read_table",
    "read_tree",
}
EXECUTE_ACTIONS = {
    "click_cell",
    "close",
    "collapse_node",
    "context_menu",
    "context_menu_item",
    "deselect_column",
    "doubleclick_cell",
    "doubleclick_node",
    "expand_node",
    "focus",
    "maximize",
    "minimize",
    "page_down",
    "page_up",
    "press",
    "press_node",
    "press_toolbar_button",
    "press_toolbar_context_button",
    "restore",
    "scroll",
    "scroll_down",
    "scroll_to",
    "scroll_to_row",
    "scroll_up",
    "search_help",
    "select_column",
    "select_context_menu_item",
    "select_node",
    "select_row",
    "select_toolbar_menu_item",
}
HIGH_RISK_ACTIONS = {"call_method", "set_property", "close"}


def _collect_actions_from_capabilities() -> set[str]:
    actions: set[str] = set()
    for capability in COMPONENT_CAPABILITIES.values():
        actions.update(str(action) for action in capability.get("actions", []))
        for subtype in capability.get("subtypes", {}).values():
            actions.update(str(action) for action in subtype.get("actions", []))
    return actions


def _permission_for_action(action: str) -> str:
    if action in READ_ACTIONS:
        return "read"
    if action in EXECUTE_ACTIONS:
        return "execute"
    return "write"


def _risk_for_action(action: str) -> str:
    if action in HIGH_RISK_ACTIONS:
        return "high"
    if _permission_for_action(action) == "read":
        return "low"
    return "medium"


def _description_for_action(action: str) -> str:
    label = action.replace("_", " ")
    if action in READ_ACTIONS:
        return (
            f"SAP GUI element에 adaptive UI action '{action}'을 수행합니다. "
            f"이 read 계열 명령은 live Type/SubType과 capability preflight를 거쳐 {label} 결과를 반환합니다."
        )
    if action in {"call_method", "get_property", "set_property"}:
        return (
            f"SAP GUI Scripting API graph/PDF 기준 capability를 확인한 뒤 '{action}'을 수행합니다. "
            "전용 도구가 없는 메서드나 속성 접근에만 사용하세요."
        )
    return (
        f"SAP GUI element에 adaptive UI action '{action}'을 수행합니다. "
        "대상 element의 live Type/SubType과 capability preflight를 먼저 확인한 뒤 기존 ui_action 실행 경로로 위임합니다."
    )


@lru_cache(maxsize=1)
def get_ui_action_defs() -> tuple[UiActionDef, ...]:
    defs = []
    for action in sorted(_collect_actions_from_capabilities()):
        suffix = action.lower()
        defs.append(
            UiActionDef(
                action=action,
                command=f"ui_{suffix}",
                tool_name=f"sap_ui_{suffix}",
                cli_name=f"ui-{suffix.replace('_', '-')}",
                permission=_permission_for_action(action),
                risk_level=_risk_for_action(action),
                description=_description_for_action(action),
            )
        )
    return tuple(defs)


UI_ACTION_COMMANDS: frozenset[str] = frozenset(
    definition.command for definition in get_ui_action_defs()
)
UI_ACTION_TOOL_NAMES: frozenset[str] = frozenset(
    definition.tool_name for definition in get_ui_action_defs()
)


@lru_cache(maxsize=1)
def _defs_by_command() -> dict[str, UiActionDef]:
    return {definition.command: definition for definition in get_ui_action_defs()}


def get_ui_action_def(command: str) -> UiActionDef | None:
    return _defs_by_command().get(command)


def mcp_tool_definitions() -> list[dict[str, Any]]:
    return [_mcp_tool_definition(definition) for definition in get_ui_action_defs()]


def _mcp_tool_definition(definition: UiActionDef) -> dict[str, Any]:
    return {
        "name": definition.tool_name,
        "description": definition.description,
        "inputSchema": {
            "type": "object",
            "properties": {
                "element_id": {"type": "string", "description": "대상 SAP GUI element ID"},
                "value": {"type": "string", "description": "액션 값. 필드값, node_key, row 번호, method/property 이름 등"},
                "node_key": {"type": "string", "description": "트리 node_key"},
                "column": {"type": "string", "description": "테이블 컬럼, 트리 컬럼, toolbar/menu item id, method 이름 등"},
                "row": {"type": "integer", "description": "테이블 행 번호 (0-based)"},
                "max_rows": {"type": "integer", "description": "read_table 최대 행 수", "default": 50},
                "columns": {
                    "oneOf": [
                        {"type": "string"},
                        {"type": "array", "items": {"type": "string"}},
                    ],
                    "description": "read_table 컬럼 목록. 쉼표 구분 문자열 또는 문자열 배열",
                },
                "method_args": {
                    "type": "array",
                    "items": {},
                    "description": (
                        "call_method의 ByVal 입력 배열. ByRef/out은 graph signature로 "
                        "자동 생성되며 객체 인자는 handle:<id> 또는 handle_id 객체 사용"
                    ),
                },
                "args_json": {"type": "string", "description": "call_method 인자 JSON 배열 문자열"},
                "property_name": {"type": "string", "description": "get_property/set_property 대상 속성명"},
                "property_value": {"description": "set_property 값. 생략 시 value 사용"},
                "slot_id": {"type": "string", "description": "대상 세션 슬롯 ID (기본: ses0)"},
            },
            "required": ["element_id"],
        },
    }
