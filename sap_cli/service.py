"""SAP CLI 서비스 레이어 - CLI와 MCP 서버의 공통 로직."""

from __future__ import annotations

from .com_wrapper import SapGuiWrapper
from .learner import OntologyLearner
from .macro.recorder import MacroRecorder
from .macro.store import MacroStore
from .ontology.schema import ActionType
from .ontology.store import OntologyStore
from .session_store import SessionStore


# ---------------------------------------------------------------------------
# 싱글턴 인스턴스 관리
# ---------------------------------------------------------------------------

_sap: SapGuiWrapper | None = None
_store: OntologyStore | None = None
_learner: OntologyLearner | None = None
_recorder: MacroRecorder | None = None
_macro_store: MacroStore | None = None
_session_store: SessionStore | None = None


def get_sap() -> SapGuiWrapper:
    global _sap
    if _sap is None:
        _sap = SapGuiWrapper()
    if not _sap.is_connected:
        _sap.connect()
    return _sap


def get_store() -> OntologyStore:
    global _store
    if _store is None:
        _store = OntologyStore()
    return _store


def get_learner() -> OntologyLearner:
    global _learner
    if _learner is None:
        _learner = OntologyLearner(get_sap(), get_store())
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
