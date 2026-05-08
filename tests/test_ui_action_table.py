"""Generic UI action table/grid execution tests."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from sap_cli.com_wrapper import SapGuiWrapper
from sap_cli.scripting_ontology import component_capability


class FakeGridView:
    Type = "GuiGridView"
    Name = "GRID"
    RowCount = 2
    ColumnCount = 2
    VisibleRowCount = 2

    def __init__(self) -> None:
        self.cells = {
            (0, "MATNR"): "100-100",
            (0, "MAKTX"): "Bolt",
            (1, "MATNR"): "200-200",
            (1, "MAKTX"): "Nut",
        }
        self.SelectedRows = ""
        self.current_cell = None
        self.clicked = False
        self.double_clicked = False
        self.context_opened = False
        self.context_item = None
        self.selected_columns = set()
        self.selection_cleared = False
        self.toolbar_button = None
        self.toolbar_context_button = None
        self.toolbar_menu_item = None

    def ColumnOrder(self, index: int) -> str:
        return ["MATNR", "MAKTX"][index]

    def GetColumnTitles(self, column: str) -> list[str]:
        return [column]

    def GetCellValue(self, row: int, column: str) -> str:
        return self.cells[(row, column)]

    def ModifyCell(self, row: int, column: str, value: str) -> None:
        self.cells[(row, column)] = value

    def SetCurrentCell(self, row: int, column: str) -> None:
        self.current_cell = (row, column)

    def DoubleClickCurrentCell(self) -> None:
        self.double_clicked = True

    def ClickCurrentCell(self) -> None:
        self.clicked = True

    def ContextMenu(self) -> None:
        self.context_opened = True

    def SelectContextMenuItem(self, item: str) -> None:
        self.context_item = ("id", item)

    def SelectColumn(self, column: str) -> None:
        self.selected_columns.add(column)

    def DeselectColumn(self, column: str) -> None:
        self.selected_columns.discard(column)

    def ClearSelection(self) -> None:
        self.selection_cleared = True

    def PressToolbarButton(self, button_id: str) -> None:
        self.toolbar_button = button_id

    def PressToolbarContextButton(self, button_id: str) -> None:
        self.toolbar_context_button = button_id

    def SelectToolbarMenuItem(self, item: str) -> None:
        self.toolbar_menu_item = ("id", item)


def _wrapper_with(element):
    with patch.object(SapGuiWrapper, "__init__", lambda self: None):
        wrapper = SapGuiWrapper()
        wrapper._session = MagicMock()
        wrapper._session.FindById.return_value = element
        return wrapper


def test_component_capability_merges_graph_and_static_grid_actions():
    capability = component_capability("GuiGridView")

    assert "select_row" in capability["actions"]
    assert "read_table" in capability["actions"]
    assert "read_cell" in capability["actions"]
    assert "set_cell" in capability["actions"]
    assert "doubleclick_cell" in capability["actions"]


def test_ui_action_reads_table_and_cell():
    grid = FakeGridView()
    wrapper = _wrapper_with(grid)

    table = wrapper.perform_ui_action(
        "wnd[0]/usr/cntlGRID/shellcont/shell",
        "read_table",
        max_rows=1,
        columns=["MATNR"],
    )
    cell = wrapper.perform_ui_action(
        "wnd[0]/usr/cntlGRID/shellcont/shell",
        "read_cell",
        row=1,
        column="MAKTX",
    )

    assert table["read_table"] is True
    assert table["table"]["rows"] == [{"MATNR": "100-100"}]
    assert cell["value"] == "Nut"


def test_ui_action_sets_selects_and_doubleclicks_grid_cell():
    grid = FakeGridView()
    wrapper = _wrapper_with(grid)

    set_result = wrapper.perform_ui_action(
        "wnd[0]/usr/cntlGRID/shellcont/shell",
        "set_cell",
        "300-300",
        row=1,
        column="MATNR",
    )
    select_result = wrapper.perform_ui_action(
        "wnd[0]/usr/cntlGRID/shellcont/shell",
        "select_row",
        row=1,
    )
    doubleclick_result = wrapper.perform_ui_action(
        "wnd[0]/usr/cntlGRID/shellcont/shell",
        "doubleclick_cell",
        row=1,
        column="MATNR",
    )

    assert set_result["cell_set"] is True
    assert grid.cells[(1, "MATNR")] == "300-300"
    assert select_result["selected"] is True
    assert grid.SelectedRows == "1"
    assert doubleclick_result["double_clicked"] is True
    assert grid.current_cell == (1, "MATNR")
    assert grid.double_clicked is True


def test_ui_action_handles_grid_context_menu_and_column_selection():
    grid = FakeGridView()
    wrapper = _wrapper_with(grid)

    current = wrapper.perform_ui_action(
        "wnd[0]/usr/cntlGRID/shellcont/shell",
        "set_current_cell",
        row=1,
        column="MAKTX",
    )
    click = wrapper.perform_ui_action(
        "wnd[0]/usr/cntlGRID/shellcont/shell",
        "click_cell",
        row=1,
        column="MAKTX",
    )
    context = wrapper.perform_ui_action(
        "wnd[0]/usr/cntlGRID/shellcont/shell",
        "context_menu_item",
        "SORT_ASC",
        row=1,
        column="MAKTX",
    )
    select_col = wrapper.perform_ui_action(
        "wnd[0]/usr/cntlGRID/shellcont/shell",
        "select_column",
        column="MATNR",
    )
    deselect_col = wrapper.perform_ui_action(
        "wnd[0]/usr/cntlGRID/shellcont/shell",
        "deselect_column",
        column="MATNR",
    )
    clear = wrapper.perform_ui_action(
        "wnd[0]/usr/cntlGRID/shellcont/shell",
        "clear_selection",
    )

    assert current["current_cell_set"] is True
    assert click["clicked"] is True
    assert grid.clicked is True
    assert context["context_menu_item_selected"] == "SORT_ASC"
    assert grid.context_opened is True
    assert grid.context_item == ("id", "SORT_ASC")
    assert select_col["column_selected"] == "MATNR"
    assert deselect_col["column_deselected"] == "MATNR"
    assert "MATNR" not in grid.selected_columns
    assert clear["selection_cleared"] is True
    assert grid.selection_cleared is True


def test_ui_action_handles_grid_toolbar_buttons_and_menu_items():
    grid = FakeGridView()
    wrapper = _wrapper_with(grid)

    press = wrapper.perform_ui_action(
        "wnd[0]/usr/cntlGRID/shellcont/shell",
        "press_toolbar_button",
        "&SORT_ASC",
    )
    context_press = wrapper.perform_ui_action(
        "wnd[0]/usr/cntlGRID/shellcont/shell",
        "press_toolbar_context_button",
        "&MB_EXPORT",
    )
    menu = wrapper.perform_ui_action(
        "wnd[0]/usr/cntlGRID/shellcont/shell",
        "select_toolbar_menu_item",
        "&XXL",
    )

    assert press["toolbar_button_pressed"] == "&SORT_ASC"
    assert context_press["toolbar_context_button_pressed"] == "&MB_EXPORT"
    assert menu["toolbar_menu_item_selected"] == "&XXL"
    assert menu["selected_by"] == "SelectToolbarMenuItem"
    assert grid.toolbar_button == "&SORT_ASC"
    assert grid.toolbar_context_button == "&MB_EXPORT"
    assert grid.toolbar_menu_item == ("id", "&XXL")
