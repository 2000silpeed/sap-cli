"""ALV Grid (GuiShell SubType=GridView) 테스트.

Phase 12: _scan_children ALV 감지, read_table ALV 지원,
스크롤 처리, 행 선택, 더블클릭, 필터/정렬 상태 읽기.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch, call

import pytest

from sap_cli.com_wrapper import SapGuiWrapper, SapGuiError
from sap_cli.ontology.schema import (
    GuiComponentType,
    ScreenOntology,
    SapTable,
)


# ---------------------------------------------------------------------------
# Fake COM helpers
# ---------------------------------------------------------------------------

class FakeChildren:
    def __init__(self, items: list):
        self._items = items
        self.Count = len(items)

    def __call__(self, index: int):
        return self._items[index]


class FakeCollection:
    """COM 컬렉션 모의 — Count 속성 + __call__(index)."""
    def __init__(self, items: list):
        self._items = items
        self.Count = len(items)

    def __call__(self, index: int):
        return self._items[index]


def _make_alv_shell(
    element_id: str,
    *,
    row_count: int = 10,
    col_count: int = 3,
    visible_rows: int = 5,
    columns: list[str] | None = None,
    cell_data: dict[tuple[int, str], str] | None = None,
    filtered_keys: list[str] | None = None,
    sorted_keys: list[str] | None = None,
    sub_type: str = "GridView",
):
    """ALV Grid GuiShell 모의 객체."""
    cols = columns or [f"COL{i}" for i in range(col_count)]
    data = cell_data or {}

    shell = MagicMock()
    shell.Id = element_id
    shell.Type = "GuiShell"
    shell.SubType = sub_type
    shell.RowCount = row_count
    shell.ColumnCount = len(cols)
    shell.VisibleRowCount = visible_rows
    shell.FirstVisibleRow = 0

    def column_order(c):
        return cols[c]
    shell.ColumnOrder = column_order

    def get_column_titles(col_id):
        return [f"Title_{col_id}"]
    shell.GetColumnTitles = get_column_titles

    def get_cell_value(r, col_id):
        return data.get((r, col_id), f"val_{r}_{col_id}")
    shell.GetCellValue = get_cell_value

    # 필터/정렬
    if filtered_keys is not None:
        shell.GetFilteredColumnKeys.return_value = FakeCollection(filtered_keys)
    else:
        shell.GetFilteredColumnKeys.return_value = None

    if sorted_keys is not None:
        shell.GetSortedColumnKeys.return_value = FakeCollection(sorted_keys)
    else:
        shell.GetSortedColumnKeys.return_value = None

    return shell


def _make_tree_shell(element_id: str):
    """트리 유형 GuiShell 모의 객체."""
    shell = MagicMock()
    shell.Id = element_id
    shell.Type = "GuiShell"
    shell.SubType = "Tree"
    node_keys = FakeCollection(["key1", "key2"])
    shell.GetAllNodeKeys.return_value = node_keys
    return shell


def _make_element(element_id: str, type_str: str, **kwargs):
    el = SimpleNamespace(Id=element_id, Type=type_str, **kwargs)
    return el


@pytest.fixture
def wrapper():
    with patch.object(SapGuiWrapper, "__init__", lambda self: None):
        w = SapGuiWrapper()
        w._session = MagicMock()
        return w


def _empty_screen() -> ScreenOntology:
    return ScreenOntology(
        transaction="ME2M",
        screen_number=500,
        program="RM06BM00",
        title="Purchase Orders by Material",
    )


# ---------------------------------------------------------------------------
# _scan_children: ALV 감지
# ---------------------------------------------------------------------------

class TestScanChildrenALV:
    """_scan_children에서 GuiShell(SubType=GridView)을 tables로 분류."""

    def test_alv_shell_detected_as_table(self, wrapper):
        alv = _make_alv_shell(
            "wnd[0]/usr/cntlGRID/shellcont/shell",
            row_count=50, col_count=3, visible_rows=20,
            columns=["MATNR", "MAKTX", "MENGE"],
        )
        alv.Children = FakeChildren([])

        container = SimpleNamespace(
            Id="wnd[0]/usr",
            Type="GuiUserArea",
            Children=FakeChildren([alv]),
        )

        screen = _empty_screen()
        wrapper._scan_children(container, screen)

        assert len(screen.tables) == 1
        assert len(screen.trees) == 0
        t = screen.tables[0]
        assert t.type == GuiComponentType.SHELL
        assert t.sub_type == "GridView"
        assert t.row_count == 50
        assert t.column_count == 3
        assert t.visible_rows == 20
        assert len(t.columns) == 3
        assert t.columns[0].name == "MATNR"
        assert t.columns[1].title == "Title_MAKTX"

    def test_tree_shell_still_goes_to_trees(self, wrapper):
        tree = _make_tree_shell("wnd[0]/usr/shell")
        tree.Children = FakeChildren([])

        container = SimpleNamespace(
            Id="wnd[0]/usr",
            Type="GuiUserArea",
            Children=FakeChildren([tree]),
        )

        screen = _empty_screen()
        wrapper._scan_children(container, screen)

        assert len(screen.trees) == 1
        assert len(screen.tables) == 0
        assert screen.trees[0].sub_type == "Tree"

    def test_alv_and_regular_table_coexist(self, wrapper):
        """ALV Shell + GuiTableControl이 같은 화면에 있을 때."""
        alv = _make_alv_shell(
            "wnd[0]/usr/cntlALV/shell",
            row_count=10, col_count=2, visible_rows=5,
            columns=["COL_A", "COL_B"],
        )
        alv.Children = FakeChildren([])

        table_ctrl = SimpleNamespace(
            Id="wnd[0]/usr/tblCONTROL",
            Type="GuiTableControl",
            RowCount=5,
            ColumnCount=2,
            VisibleRowCount=5,
        )
        table_ctrl.Children = FakeChildren([])

        container = SimpleNamespace(
            Id="wnd[0]/usr",
            Type="GuiUserArea",
            Children=FakeChildren([alv, table_ctrl]),
        )

        screen = _empty_screen()
        wrapper._scan_children(container, screen)

        assert len(screen.tables) == 2
        types = {t.type for t in screen.tables}
        assert GuiComponentType.SHELL in types
        assert GuiComponentType.TABLE_CONTROL in types


# ---------------------------------------------------------------------------
# _read_alv_grid: 데이터 읽기
# ---------------------------------------------------------------------------

class TestReadAlvGrid:
    """_read_alv_grid 데이터 읽기 + 스크롤."""

    def test_basic_read(self, wrapper):
        alv = _make_alv_shell(
            "wnd[0]/usr/shell",
            row_count=3, col_count=2, visible_rows=10,
            columns=["EBELN", "EBELP"],
        )
        wrapper._session.FindById.return_value = alv

        result = wrapper.read_table("wnd[0]/usr/shell")
        assert result.type == GuiComponentType.SHELL
        assert result.sub_type == "GridView"
        assert result.row_count == 3
        assert len(result.rows) == 3
        assert result.rows[0]["EBELN"] == "val_0_EBELN"

    def test_scroll_reads_all_rows(self, wrapper):
        """VisibleRowCount=3, RowCount=7 → FirstVisibleRow 조정하며 전체 읽기."""
        alv = _make_alv_shell(
            "wnd[0]/usr/shell",
            row_count=7, col_count=1, visible_rows=3,
            columns=["MATNR"],
        )
        wrapper._session.FindById.return_value = alv

        result = wrapper.read_table("wnd[0]/usr/shell")
        assert len(result.rows) == 7

    def test_max_500_rows(self, wrapper):
        """1000행 ALV + 명시적 max_rows=500 → 최대 500행만 읽기."""
        alv = _make_alv_shell(
            "wnd[0]/usr/shell",
            row_count=1000, col_count=1, visible_rows=50,
            columns=["COL"],
        )
        wrapper._session.FindById.return_value = alv

        result = wrapper.read_table("wnd[0]/usr/shell", max_rows=500)
        assert len(result.rows) == 500

    def test_default_max_rows_is_50(self, wrapper):
        """기본 호출은 응답 크기 보호를 위해 50행만 읽는다."""
        alv = _make_alv_shell(
            "wnd[0]/usr/shell",
            row_count=1000, col_count=1, visible_rows=50,
            columns=["COL"],
        )
        wrapper._session.FindById.return_value = alv

        result = wrapper.read_table("wnd[0]/usr/shell")
        assert len(result.rows) == 50

    def test_filtered_sorted_columns(self, wrapper):
        alv = _make_alv_shell(
            "wnd[0]/usr/shell",
            row_count=1, col_count=2, visible_rows=10,
            columns=["MATNR", "WERKS"],
            filtered_keys=["WERKS"],
            sorted_keys=["MATNR"],
        )
        wrapper._session.FindById.return_value = alv

        result = wrapper.read_table("wnd[0]/usr/shell")
        assert result.filtered_columns == ["WERKS"]
        assert result.sorted_columns == ["MATNR"]

    def test_unsupported_shell_subtype_raises(self, wrapper):
        """SubType이 GridView가 아닌 GuiShell → 에러."""
        shell = MagicMock()
        shell.Type = "GuiShell"
        shell.SubType = "Toolbar"
        wrapper._session.FindById.return_value = shell

        with pytest.raises(SapGuiError, match="지원하지 않는 Shell SubType"):
            wrapper.read_table("wnd[0]/usr/shell")


# ---------------------------------------------------------------------------
# select_table_row: GuiShell ALV 지원
# ---------------------------------------------------------------------------

class TestSelectTableRowALV:

    def test_select_row_on_alv_shell(self, wrapper):
        alv = _make_alv_shell("wnd[0]/usr/shell", row_count=10)
        wrapper._session.FindById.return_value = alv

        wrapper.select_table_row("wnd[0]/usr/shell", 3)
        assert alv.SelectedRows == "3"

    def test_select_row_on_grid_view(self, wrapper):
        grid = MagicMock()
        grid.Type = "GuiGridView"
        wrapper._session.FindById.return_value = grid

        wrapper.select_table_row("wnd[0]/usr/grid", 5)
        assert grid.SelectedRows == "5"


# ---------------------------------------------------------------------------
# double_click_table_cell
# ---------------------------------------------------------------------------

class TestDoubleClickTableCell:

    def test_doubleclick_alv_shell(self, wrapper):
        alv = _make_alv_shell("wnd[0]/usr/shell", row_count=10, columns=["VBELN"])
        wrapper._session.FindById.return_value = alv

        wrapper.double_click_table_cell("wnd[0]/usr/shell", 2, "VBELN")
        alv.SetCurrentCell.assert_called_once_with(2, "VBELN")
        alv.DoubleClickCurrentCell.assert_called_once()

    def test_doubleclick_grid_view(self, wrapper):
        grid = MagicMock()
        grid.Type = "GuiGridView"
        grid.SubType = None  # no SubType on pure GridView
        wrapper._session.FindById.return_value = grid

        wrapper.double_click_table_cell("wnd[0]/usr/grid", 0, "EBELN")
        grid.SetCurrentCell.assert_called_once_with(0, "EBELN")
        grid.DoubleClickCurrentCell.assert_called_once()

    def test_doubleclick_table_control_fallback(self, wrapper):
        table = MagicMock()
        table.Type = "GuiTableControl"
        wrapper._session.FindById.return_value = table

        wrapper.double_click_table_cell("wnd[0]/usr/tbl", 1, "COL")
        table.GetAbsoluteRow.assert_called_once_with(1)
        table.DoubleClick.assert_called_once_with(1, "COL")

    def test_doubleclick_error_raises(self, wrapper):
        wrapper._session.FindById.side_effect = Exception("COM error")

        with pytest.raises(SapGuiError, match="테이블 더블클릭 실패"):
            wrapper.double_click_table_cell("wnd[0]/usr/shell", 0, "COL")


# ---------------------------------------------------------------------------
# set_table_cell: GuiShell ALV 지원
# ---------------------------------------------------------------------------

class TestSetTableCellALV:

    def test_modify_cell_on_alv_shell(self, wrapper):
        alv = _make_alv_shell("wnd[0]/usr/shell")
        wrapper._session.FindById.return_value = alv

        wrapper.set_table_cell("wnd[0]/usr/shell", 1, "MENGE", "100")
        alv.ModifyCell.assert_called_once_with(1, "MENGE", "100")
