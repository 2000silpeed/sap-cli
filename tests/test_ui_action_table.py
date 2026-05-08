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
        self.double_clicked = False

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
