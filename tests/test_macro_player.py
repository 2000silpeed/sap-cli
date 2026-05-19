"""MacroPlayer 단위 테스트 - COM 없이 트리 액션 매핑 검증."""

from types import SimpleNamespace

from sap_cli.macro.player import MacroPlayer
from sap_cli.macro.schema import Macro, MacroStep, MacroVariable
from sap_cli.ontology.schema import ActionType


class FakeSap:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []
        self.fields: list[tuple[str, str]] = []

    def run_transaction(self, tcode: str) -> None:
        self.calls.append(("run_transaction", tcode, ""))

    def tree_select_node(self, element_id: str, node_key: str) -> None:
        self.calls.append(("tree_select_node", element_id, node_key))

    def tree_expand_node(self, element_id: str, node_key: str) -> None:
        self.calls.append(("tree_expand_node", element_id, node_key))

    def tree_double_click_node(self, element_id: str, node_key: str) -> None:
        self.calls.append(("tree_double_click_node", element_id, node_key))

    def tree_collapse_node(self, element_id: str, node_key: str) -> None:
        self.calls.append(("tree_collapse_node", element_id, node_key))

    def click_table_cell(self, element_id: str, row: int, col: str) -> None:
        self.calls.append(("click_table_cell", element_id, str(row), col))

    def double_click_table_cell(self, element_id: str, row: int, col: str) -> None:
        self.calls.append(("double_click_table_cell", element_id, str(row), col))

    def set_current_table_cell(self, element_id: str, row: int, col: str) -> None:
        self.calls.append(("set_current_table_cell", element_id, str(row), col))

    def select_table_row(self, element_id: str, row: int) -> None:
        self.calls.append(("select_table_row", element_id, str(row)))

    def set_field(self, element_id: str, value: str) -> None:
        self.fields.append((element_id, value))

    def press(self, element_id: str) -> None:
        self.calls.append(("press", element_id, ""))

    def send_vkey(self, key: int) -> None:
        self.calls.append(("send_vkey", str(key), ""))

    def set_checkbox(self, element_id: str, checked: bool) -> None:
        self.calls.append(("set_checkbox", element_id, str(checked)))

    def select_radio(self, element_id: str) -> None:
        self.calls.append(("select_radio", element_id, ""))

    def select_combo(self, element_id: str, value: str) -> None:
        self.calls.append(("select_combo", element_id, value))

    def select_menu(self, element_id: str) -> None:
        self.calls.append(("select_menu", element_id, ""))

    def perform_ui_action(self, element_id: str, action: str, value: str | None = None, **kwargs) -> None:
        self.calls.append(("perform_ui_action", element_id, action, value or ""))

    def get_screen(self):
        self.calls.append(("get_screen", "", ""))
        return SimpleNamespace()

    def get_status(self):
        return SimpleNamespace(type=SimpleNamespace(value="S"), message="")

    def get_session_info(self):
        return SimpleNamespace(screen_number=100)


def _macro(*steps: MacroStep) -> Macro:
    return Macro(
        id="m1",
        name="tree macro",
        steps=list(steps),
    )


def _macro_with_vars(*steps: MacroStep) -> Macro:
    return Macro(
        id="m2",
        name="variable macro",
        variables=[
            MacroVariable(
                name="matnr",
                description="material",
                default="100-100",
            )
        ],
        steps=list(steps),
    )


def test_macro_player_executes_tree_actions():
    sap = FakeSap()
    player = MacroPlayer(sap)  # type: ignore[arg-type]
    tree_id = "wnd[0]/usr/cntlTREE/shellcont/shell"

    result = player.play(
        _macro(
            MacroStep(
                step_number=1,
                action=ActionType.SELECT_TREE_NODE,
                target_id=tree_id,
                value="0001",
            ),
            MacroStep(
                step_number=2,
                action=ActionType.EXPAND_TREE_NODE,
                target_id=tree_id,
                value="0002",
            ),
            MacroStep(
                step_number=3,
                action=ActionType.DOUBLE_CLICK_TREE_NODE,
                target_id=tree_id,
                value="0003",
            ),
            MacroStep(
                step_number=4,
                action=ActionType.COLLAPSE_TREE_NODE,
                target_id=tree_id,
                value="0004",
            ),
        )
    )

    assert result.success is True
    assert sap.calls == [
        ("tree_select_node", tree_id, "0001"),
        ("tree_expand_node", tree_id, "0002"),
        ("tree_double_click_node", tree_id, "0003"),
        ("tree_collapse_node", tree_id, "0004"),
    ]


def test_macro_player_tree_action_requires_node_key():
    sap = FakeSap()
    player = MacroPlayer(sap)  # type: ignore[arg-type]

    result = player.play(
        _macro(
            MacroStep(
                step_number=1,
                action=ActionType.SELECT_TREE_NODE,
                target_id="wnd[0]/usr/tree",
                value="",
            )
        )
    )

    assert result.success is False
    assert result.error_step == 1
    assert "node_key" in (result.step_results[0].error or "")


def test_macro_player_executes_table_doubleclick_action():
    sap = FakeSap()
    player = MacroPlayer(sap)  # type: ignore[arg-type]
    table_id = "wnd[0]/usr/cntlGRID/shellcont/shell"

    result = player.play(
        _macro(
            MacroStep(
                step_number=1,
                action=ActionType.DOUBLE_CLICK_TABLE_CELL,
                target_id=table_id,
                params={"row": 2, "column": "MATNR"},
            )
        )
    )

    assert result.success is True
    assert sap.calls == [("double_click_table_cell", table_id, "2", "MATNR")]


def test_macro_player_accepts_list_variable_payload_and_case_insensitive_names():
    sap = FakeSap()
    player = MacroPlayer(sap)  # type: ignore[arg-type]

    result = player.play(
        _macro_with_vars(
            MacroStep(
                step_number=1,
                action=ActionType.SET_FIELD_VALUE,
                target_id="wnd[0]/usr/ctxtRMMG1-MATNR",
                value="${matnr}",
            )
        ),
        variables=[{"name": "MATNR", "value": "200-200"}],
    )

    assert result.success is True
    assert sap.fields == [("wnd[0]/usr/ctxtRMMG1-MATNR", "200-200")]
    assert result.variables_used["matnr"] == "200-200"


def test_macro_player_executes_every_action_type():
    steps_by_action = {
        ActionType.SET_FIELD_VALUE: MacroStep(
            step_number=1,
            action=ActionType.SET_FIELD_VALUE,
            target_id="wnd[0]/usr/txtFIELD",
            value="value",
        ),
        ActionType.PRESS_BUTTON: MacroStep(
            step_number=1,
            action=ActionType.PRESS_BUTTON,
            target_id="wnd[0]/tbar[0]/btn[0]",
        ),
        ActionType.SEND_VKEY: MacroStep(
            step_number=1,
            action=ActionType.SEND_VKEY,
            target_id="0",
        ),
        ActionType.RUN_TRANSACTION: MacroStep(
            step_number=1,
            action=ActionType.RUN_TRANSACTION,
            target_id="MM03",
        ),
        ActionType.SELECT_TABLE_ROW: MacroStep(
            step_number=1,
            action=ActionType.SELECT_TABLE_ROW,
            target_id="wnd[0]/usr/tbl",
            value="0",
        ),
        ActionType.SELECT_TABLE_CELL: MacroStep(
            step_number=1,
            action=ActionType.SELECT_TABLE_CELL,
            target_id="wnd[0]/usr/tbl",
            params={"row": 0, "column": "MATNR"},
        ),
        ActionType.SELECT_COMBO_ITEM: MacroStep(
            step_number=1,
            action=ActionType.SELECT_COMBO_ITEM,
            target_id="wnd[0]/usr/cmbFIELD",
            value="A",
        ),
        ActionType.TOGGLE_CHECKBOX: MacroStep(
            step_number=1,
            action=ActionType.TOGGLE_CHECKBOX,
            target_id="wnd[0]/usr/chkFIELD",
            value="true",
        ),
        ActionType.SELECT_RADIO: MacroStep(
            step_number=1,
            action=ActionType.SELECT_RADIO,
            target_id="wnd[0]/usr/radFIELD",
        ),
        ActionType.SELECT_TAB: MacroStep(
            step_number=1,
            action=ActionType.SELECT_TAB,
            target_id="wnd[0]/usr/tabs/tabpA",
        ),
        ActionType.SELECT_MENU: MacroStep(
            step_number=1,
            action=ActionType.SELECT_MENU,
            target_id="wnd[0]/mbar/menu[0]",
        ),
        ActionType.READ_SCREEN: MacroStep(
            step_number=1,
            action=ActionType.READ_SCREEN,
            target_id="screen",
        ),
        ActionType.SELECT_TREE_NODE: MacroStep(
            step_number=1,
            action=ActionType.SELECT_TREE_NODE,
            target_id="wnd[0]/usr/tree",
            value="0001",
        ),
        ActionType.EXPAND_TREE_NODE: MacroStep(
            step_number=1,
            action=ActionType.EXPAND_TREE_NODE,
            target_id="wnd[0]/usr/tree",
            value="0001",
        ),
        ActionType.DOUBLE_CLICK_TREE_NODE: MacroStep(
            step_number=1,
            action=ActionType.DOUBLE_CLICK_TREE_NODE,
            target_id="wnd[0]/usr/tree",
            value="0001",
        ),
        ActionType.COLLAPSE_TREE_NODE: MacroStep(
            step_number=1,
            action=ActionType.COLLAPSE_TREE_NODE,
            target_id="wnd[0]/usr/tree",
            value="0001",
        ),
        ActionType.CLICK_TABLE_CELL: MacroStep(
            step_number=1,
            action=ActionType.CLICK_TABLE_CELL,
            target_id="wnd[0]/usr/tbl",
            params={"row": 0, "column": "MATNR"},
        ),
        ActionType.DOUBLE_CLICK_TABLE_CELL: MacroStep(
            step_number=1,
            action=ActionType.DOUBLE_CLICK_TABLE_CELL,
            target_id="wnd[0]/usr/tbl",
            params={"row": 0, "column": "MATNR"},
        ),
        ActionType.PERFORM_UI_ACTION: MacroStep(
            step_number=1,
            action=ActionType.PERFORM_UI_ACTION,
            target_id="wnd[0]/usr/tbl",
            value="doubleclick_cell",
            params={"row": 0, "column": "MATNR"},
        ),
    }

    assert set(steps_by_action) == set(ActionType)

    for action, step in steps_by_action.items():
        result = MacroPlayer(FakeSap()).play(_macro(step))
        assert result.success is True, action


def test_macro_step_accepts_stored_action_aliases():
    assert MacroStep(
        step_number=1,
        action="doubleclick_cell",
        target_id="wnd[0]/usr/tbl",
    ).action == ActionType.DOUBLE_CLICK_TABLE_CELL
    assert MacroStep(
        step_number=1,
        action="set_current_cell",
        target_id="wnd[0]/usr/tbl",
    ).action == ActionType.SELECT_TABLE_CELL
