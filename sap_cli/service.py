"""SAP CLI 서비스 레이어 - CLI와 MCP 서버의 공통 로직."""

from __future__ import annotations

from .com_wrapper import SapGuiWrapper
from .learner import OntologyLearner
from .macro.recorder import MacroRecorder
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
    "run": ActionType.RUN_TRANSACTION,
    "checkbox": ActionType.TOGGLE_CHECKBOX,
    "radio": ActionType.SELECT_RADIO,
    "combo": ActionType.SELECT_COMBO_ITEM,
    "tab": ActionType.SELECT_TAB,
    "menu": ActionType.SELECT_MENU,
    "table-select": ActionType.SELECT_TABLE_ROW,
    "tree-select": ActionType.SELECT_TREE_NODE,
    "tree-expand": ActionType.EXPAND_TREE_NODE,
    "tree-doubleclick": ActionType.DOUBLE_CLICK_TREE_NODE,
    "tree-collapse": ActionType.COLLAPSE_TREE_NODE,
}


def map_action(action: str) -> ActionType:
    at = ACTION_MAP.get(action.lower())
    if at is None:
        raise ValueError(f"알 수 없는 액션: {action}. 가능한 값: {list(ACTION_MAP.keys())}")
    return at


def execute_sap_action(sap: SapGuiWrapper, action: str, target: str, value: str | None) -> None:
    """action에 따라 실제 SAP 명령 실행."""
    if action == "set":
        sap.set_field(target, value or "")
    elif action == "press":
        sap.press(target)
    elif action == "vkey":
        sap.send_vkey(int(target))
    elif action == "run":
        sap.run_transaction(target)
    elif action == "checkbox":
        sap.set_checkbox(target, (value or "").lower() == "true")
    elif action == "radio":
        sap.select_radio(target)
    elif action == "combo":
        sap.select_combo(target, value or "")
    elif action == "tab":
        sap.press(target)
    elif action == "menu":
        sap.select_menu(target)
    elif action == "table-select":
        sap.select_table_row(target, int(value or "0"))
    elif action == "tree-select":
        if not value:
            raise ValueError("tree-select 액션은 value에 node_key가 필요합니다.")
        sap.tree_select_node(target, value)
    elif action == "tree-expand":
        if not value:
            raise ValueError("tree-expand 액션은 value에 node_key가 필요합니다.")
        sap.tree_expand_node(target, value)
    elif action == "tree-doubleclick":
        if not value:
            raise ValueError("tree-doubleclick 액션은 value에 node_key가 필요합니다.")
        sap.tree_double_click_node(target, value)
    elif action == "tree-collapse":
        if not value:
            raise ValueError("tree-collapse 액션은 value에 node_key가 필요합니다.")
        sap.tree_collapse_node(target, value)
