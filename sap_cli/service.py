"""SAP CLI 서비스 레이어 - CLI와 MCP 서버의 공통 로직."""

from __future__ import annotations

from typing import Any

from .com_wrapper import SapGuiWrapper
from .learner import OntologyLearner
from .macro.recorder import MacroRecorder
from .macro.schema import coerce_action_type
from .macro.store import MacroStore
from .ontology.schema import ActionType
from .ontology.store import OntologyStore
import threading

from .session_pool import DEFAULT_SLOT, SessionPool
from .session_store import SessionStore


# ---------------------------------------------------------------------------
# 세션 풀 (싱글턴 → 풀 전환)
# ---------------------------------------------------------------------------

_pool: SessionPool | None = None
_pool_lock = threading.Lock()


def get_pool() -> SessionPool:
    """글로벌 SessionPool 인스턴스 반환 (스레드 안전)."""
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = SessionPool()
    return _pool


def get_sap(slot_id: str = DEFAULT_SLOT) -> SapGuiWrapper:
    """slot_id에 해당하는 SapGuiWrapper 반환.

    슬롯이 없으면 자동 생성. 하위 호환: slot_id 미지정 시 "ses0".
    재연결 시 슬롯에 저장된 connection/session 인덱스를 사용.
    """
    pool = get_pool()
    wrapper = pool.get_or_create(slot_id)
    if not wrapper.is_connected:
        slot = pool._slots.get(slot_id)
        if slot and (slot.connection_index or slot.session_index or slot.system_id):
            wrapper.connect(
                connection_index=slot.connection_index,
                session_index=slot.session_index,
                system=slot.system_id,
            )
        else:
            wrapper.connect()
    return wrapper


# ---------------------------------------------------------------------------
# 온톨로지/매크로/세션 스토어 (슬롯과 무관 — 전역 공유)
# ---------------------------------------------------------------------------

_store: OntologyStore | None = None
_learner: OntologyLearner | None = None
_recorder: MacroRecorder | None = None
_macro_store: MacroStore | None = None
_session_store: SessionStore | None = None


def get_store() -> OntologyStore:
    global _store
    if _store is None:
        _store = OntologyStore()
    return _store


def get_learner(slot_id: str = DEFAULT_SLOT) -> OntologyLearner:
    """slot_id의 SAP wrapper를 사용하는 learner 반환."""
    global _learner
    sap = get_sap(slot_id)
    if _learner is None or _learner._sap is not sap:
        _learner = OntologyLearner(sap, get_store())
    return _learner


def get_recorder() -> MacroRecorder:
    global _recorder
    if _recorder is None:
        _recorder = MacroRecorder()
    return _recorder


def get_macro_store() -> MacroStore:
    global _macro_store
    if _macro_store is None:
        _macro_store = MacroStore()
    return _macro_store


_session_config = None


def get_session_store(config=None) -> SessionStore:
    global _session_store, _session_config
    if _session_store is None or (config is not None and config != _session_config):
        _session_config = config
        _session_store = SessionStore(config)
    return _session_store


# ---------------------------------------------------------------------------
# action 문자열 → ActionType 매핑
# ---------------------------------------------------------------------------

ACTION_MAP: dict[str, ActionType] = {
    "set": ActionType.SET_FIELD_VALUE,
    "press": ActionType.PRESS_BUTTON,
    "vkey": ActionType.SEND_VKEY,
    "enter": ActionType.SEND_VKEY,
    "send-enter": ActionType.SEND_VKEY,
    "run": ActionType.RUN_TRANSACTION,
    "checkbox": ActionType.TOGGLE_CHECKBOX,
    "radio": ActionType.SELECT_RADIO,
    "combo": ActionType.SELECT_COMBO_ITEM,
    "tab": ActionType.SELECT_TAB,
    "menu": ActionType.SELECT_MENU,
    "screen": ActionType.READ_SCREEN,
    "read": ActionType.READ_SCREEN,
    "read-screen": ActionType.READ_SCREEN,
    "table-select": ActionType.SELECT_TABLE_ROW,
    "select-row": ActionType.SELECT_TABLE_ROW,
    "table-row": ActionType.SELECT_TABLE_ROW,
    "table_cell": ActionType.SELECT_TABLE_CELL,
    "table-cell": ActionType.SELECT_TABLE_CELL,
    "select_cell": ActionType.SELECT_TABLE_CELL,
    "select-cell": ActionType.SELECT_TABLE_CELL,
    "current_cell": ActionType.SELECT_TABLE_CELL,
    "current-cell": ActionType.SELECT_TABLE_CELL,
    "set_current_cell": ActionType.SELECT_TABLE_CELL,
    "set-current-cell": ActionType.SELECT_TABLE_CELL,
    "table_doubleclick": ActionType.DOUBLE_CLICK_TABLE_CELL,
    "table-doubleclick": ActionType.DOUBLE_CLICK_TABLE_CELL,
    "cell_doubleclick": ActionType.DOUBLE_CLICK_TABLE_CELL,
    "cell-doubleclick": ActionType.DOUBLE_CLICK_TABLE_CELL,
    "doubleclick_cell": ActionType.DOUBLE_CLICK_TABLE_CELL,
    "doubleclick-cell": ActionType.DOUBLE_CLICK_TABLE_CELL,
    "click_cell": ActionType.CLICK_TABLE_CELL,
    "click-cell": ActionType.CLICK_TABLE_CELL,
    "cell_click": ActionType.CLICK_TABLE_CELL,
    "cell-click": ActionType.CLICK_TABLE_CELL,
    "ui_action": ActionType.PERFORM_UI_ACTION,
    "ui-action": ActionType.PERFORM_UI_ACTION,
    "act": ActionType.PERFORM_UI_ACTION,
    "tree-select": ActionType.SELECT_TREE_NODE,
    "tree-expand": ActionType.EXPAND_TREE_NODE,
    "tree-doubleclick": ActionType.DOUBLE_CLICK_TREE_NODE,
    "tree-collapse": ActionType.COLLAPSE_TREE_NODE,
}


def map_action(action: str) -> ActionType:
    try:
        return coerce_action_type(action)
    except ValueError as exc:
        raise ValueError(f"알 수 없는 액션: {action}. 가능한 값: {list(ACTION_MAP.keys())}") from exc


def execute_sap_action(
    sap: SapGuiWrapper,
    action: str,
    target: str,
    value: str | None,
    params: dict[str, Any] | None = None,
) -> None:
    """action에 따라 실제 SAP 명령 실행."""
    params = params or {}
    action_type = coerce_action_type(action)
    normalized_action = str(action).lower().replace("_", "-")

    if action_type == ActionType.SET_FIELD_VALUE:
        sap.set_field(target, value or "")
    elif action_type == ActionType.PRESS_BUTTON:
        sap.press(target)
    elif action_type == ActionType.SEND_VKEY:
        key = 0 if normalized_action in {"enter", "send-enter"} else int(target)
        sap.send_vkey(key)
    elif action_type == ActionType.RUN_TRANSACTION:
        sap.run_transaction(target)
    elif action_type == ActionType.TOGGLE_CHECKBOX:
        sap.set_checkbox(target, (value or "").lower() == "true")
    elif action_type == ActionType.SELECT_RADIO:
        sap.select_radio(target)
    elif action_type == ActionType.SELECT_COMBO_ITEM:
        sap.select_combo(target, value or "")
    elif action_type == ActionType.SELECT_TAB:
        sap.press(target)
    elif action_type == ActionType.SELECT_MENU:
        sap.select_menu(target)
    elif action_type == ActionType.READ_SCREEN:
        sap.get_screen()
    elif action_type == ActionType.SELECT_TABLE_ROW:
        sap.select_table_row(target, int(value or "0"))
    elif action_type == ActionType.SELECT_TABLE_CELL:
        row = _row_param(params, value, normalized_action)
        sap.set_current_table_cell(target, row, str(params.get("column", "") or ""))
    elif action_type == ActionType.DOUBLE_CLICK_TABLE_CELL:
        row = _row_param(params, value, normalized_action)
        sap.double_click_table_cell(target, row, str(params.get("column", "") or ""))
    elif action_type == ActionType.CLICK_TABLE_CELL:
        row = _row_param(params, value, normalized_action)
        sap.click_table_cell(target, row, str(params.get("column", "") or ""))
    elif action_type == ActionType.PERFORM_UI_ACTION:
        ui_action = str(params.get("ui_action") or params.get("action") or value or "")
        if not ui_action:
            raise ValueError("ui-action 액션은 value 또는 params.ui_action에 실제 UI action이 필요합니다.")
        sap.perform_ui_action(
            target,
            ui_action,
            _optional_str(params.get("action_value", params.get("value"))),
            node_key=str(params.get("node_key", "") or ""),
            column=str(params.get("column", "") or ""),
            row=_optional_int(params.get("row")),
            max_rows=int(params.get("max_rows", 50) or 50),
            columns=_optional_columns(params.get("columns")),
            method_args=params.get("method_args"),
            property_name=str(params.get("property_name", "") or ""),
            property_value=params.get("property_value"),
        )
    elif action_type == ActionType.SELECT_TREE_NODE:
        if not value:
            raise ValueError("tree-select 액션은 value에 node_key가 필요합니다.")
        sap.tree_select_node(target, value)
    elif action_type == ActionType.EXPAND_TREE_NODE:
        if not value:
            raise ValueError("tree-expand 액션은 value에 node_key가 필요합니다.")
        sap.tree_expand_node(target, value)
    elif action_type == ActionType.DOUBLE_CLICK_TREE_NODE:
        if not value:
            raise ValueError("tree-doubleclick 액션은 value에 node_key가 필요합니다.")
        sap.tree_double_click_node(target, value)
    elif action_type == ActionType.COLLAPSE_TREE_NODE:
        if not value:
            raise ValueError("tree-collapse 액션은 value에 node_key가 필요합니다.")
        sap.tree_collapse_node(target, value)
    else:
        raise ValueError(f"미지원 매크로 액션: {action_type.value}")


def _row_param(params: dict[str, Any], value: str | None, action: str) -> int:
    row = _optional_int(params.get("row"))
    if row is not None:
        return row
    if value not in (None, ""):
        return int(value)
    raise ValueError(f"{action} 액션은 row 또는 value(row)가 필요합니다.")


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    return int(value)


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _optional_columns(value: Any) -> list[str] | None:
    if value in (None, ""):
        return None
    if isinstance(value, list):
        return [str(item) for item in value]
    return [item.strip() for item in str(value).split(",") if item.strip()]
