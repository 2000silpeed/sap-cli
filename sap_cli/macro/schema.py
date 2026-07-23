"""매크로 데이터 모델.

SAP GUI 작업 시퀀스를 녹화/재생 가능한 매크로로 정의한다.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

from sap_cli.ontology.schema import ActionType

_ACTION_ALIASES: dict[str, ActionType] = {
    "set": ActionType.SET_FIELD_VALUE,
    "set-field": ActionType.SET_FIELD_VALUE,
    "set-field-value": ActionType.SET_FIELD_VALUE,
    "press": ActionType.PRESS_BUTTON,
    "button": ActionType.PRESS_BUTTON,
    "click": ActionType.PRESS_BUTTON,
    "vkey": ActionType.SEND_VKEY,
    "send-vkey": ActionType.SEND_VKEY,
    "enter": ActionType.SEND_VKEY,
    "send-enter": ActionType.SEND_VKEY,
    "run": ActionType.RUN_TRANSACTION,
    "transaction": ActionType.RUN_TRANSACTION,
    "checkbox": ActionType.TOGGLE_CHECKBOX,
    "toggle-checkbox": ActionType.TOGGLE_CHECKBOX,
    "radio": ActionType.SELECT_RADIO,
    "combo": ActionType.SELECT_COMBO_ITEM,
    "select-combo": ActionType.SELECT_COMBO_ITEM,
    "tab": ActionType.SELECT_TAB,
    "menu": ActionType.SELECT_MENU,
    "screen": ActionType.READ_SCREEN,
    "read": ActionType.READ_SCREEN,
    "read-screen": ActionType.READ_SCREEN,
    "table-select": ActionType.SELECT_TABLE_ROW,
    "select-row": ActionType.SELECT_TABLE_ROW,
    "table-row": ActionType.SELECT_TABLE_ROW,
    "table-cell": ActionType.SELECT_TABLE_CELL,
    "select-cell": ActionType.SELECT_TABLE_CELL,
    "current-cell": ActionType.SELECT_TABLE_CELL,
    "set-current-cell": ActionType.SELECT_TABLE_CELL,
    "click-cell": ActionType.CLICK_TABLE_CELL,
    "cell-click": ActionType.CLICK_TABLE_CELL,
    "table-click": ActionType.CLICK_TABLE_CELL,
    "table-doubleclick": ActionType.DOUBLE_CLICK_TABLE_CELL,
    "cell-doubleclick": ActionType.DOUBLE_CLICK_TABLE_CELL,
    "doubleclick-cell": ActionType.DOUBLE_CLICK_TABLE_CELL,
    "double-click-cell": ActionType.DOUBLE_CLICK_TABLE_CELL,
    "tree-select": ActionType.SELECT_TREE_NODE,
    "select-node": ActionType.SELECT_TREE_NODE,
    "tree-expand": ActionType.EXPAND_TREE_NODE,
    "expand-node": ActionType.EXPAND_TREE_NODE,
    "tree-doubleclick": ActionType.DOUBLE_CLICK_TREE_NODE,
    "doubleclick-node": ActionType.DOUBLE_CLICK_TREE_NODE,
    "tree-collapse": ActionType.COLLAPSE_TREE_NODE,
    "collapse-node": ActionType.COLLAPSE_TREE_NODE,
    "ui-action": ActionType.PERFORM_UI_ACTION,
    "perform-ui-action": ActionType.PERFORM_UI_ACTION,
    "act": ActionType.PERFORM_UI_ACTION,
}


def coerce_action_type(value: Any) -> ActionType:
    """Coerce stored macro action strings and aliases to ``ActionType``."""
    if isinstance(value, ActionType):
        return value
    raw = str(value)
    try:
        return ActionType(raw)
    except ValueError:
        pass
    normalized = raw.strip().replace("_", "-").lower()
    if normalized in _ACTION_ALIASES:
        return _ACTION_ALIASES[normalized]
    raise ValueError(f"알 수 없는 매크로 액션: {value}")


class MacroVariable(BaseModel):
    """매크로 변수 정의. 재생 시 사용자가 값을 주입할 수 있다."""

    name: str
    description: str = ""
    default: str = ""
    required: bool = True


class MacroStep(BaseModel):
    """매크로의 한 단계."""

    step_number: int
    action: ActionType
    target_id: str
    value: str | None = None
    description: str = ""
    expected_screen: int | None = None
    wait_after: float = 0
    params: dict[str, Any] = Field(default_factory=dict)

    @field_validator("action", mode="before")
    @classmethod
    def _coerce_action(cls, value: Any) -> ActionType:
        return coerce_action_type(value)


class MacroStepResult(BaseModel):
    """단계 실행 결과."""

    step_number: int
    success: bool
    actual_screen: int = 0
    status_message: str = ""
    status_type: str = "S"
    error: str | None = None


class Macro(BaseModel):
    """완성된 매크로. 저장/로드/재생의 단위."""

    id: str
    name: str
    description: str = ""
    tcode: str = ""
    variables: list[MacroVariable] = Field(default_factory=list)
    steps: list[MacroStep] = Field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    tags: list[str] = Field(default_factory=list)


class MacroRunResult(BaseModel):
    """매크로 실행 결과."""

    macro_id: str
    macro_name: str
    success: bool
    variables_used: dict[str, str] = Field(default_factory=dict)
    step_results: list[MacroStepResult] = Field(default_factory=list)
    total_steps: int = 0
    completed_steps: int = 0
    error_step: int | None = None
    started_at: str = ""
    finished_at: str = ""
