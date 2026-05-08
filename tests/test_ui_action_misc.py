"""Generic UI action scroll/text/window execution tests."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from sap_cli.com_wrapper import SapGuiWrapper
from sap_cli.scripting_ontology import component_capability


class FakeScrollableGrid:
    Type = "GuiGridView"
    Name = "GRID"
    FirstVisibleRow = 0
    VisibleRowCount = 10


class FakeScrollbar:
    Position = 0


class FakeHorizontalScrollable:
    Type = "GuiUserArea"
    Name = "USR"

    def __init__(self) -> None:
        self.HorizontalScrollbar = FakeScrollbar()


class FakeTextEdit:
    Type = "GuiTextedit"
    Name = "EDITOR"

    def __init__(self) -> None:
        self.Text = "hello"


class FakeWindow:
    Type = "GuiMainWindow"
    Name = "wnd[0]"

    def __init__(self) -> None:
        self.called = []

    def Maximize(self) -> None:
        self.called.append("Maximize")

    def Restore(self) -> None:
        self.called.append("Restore")

    def Iconify(self) -> None:
        self.called.append("Iconify")


class FakeGenericComponent:
    Type = "GuiShell"
    SubType = "Custom"
    Name = "CUSTOM"

    def __init__(self) -> None:
        self.SomeProperty = "old"

    def Echo(self, prefix: str, count: int) -> dict:
        return {"prefix": prefix, "count": count}


def _wrapper_with(element):
    with patch.object(SapGuiWrapper, "__init__", lambda self: None):
        wrapper = SapGuiWrapper()
        wrapper._session = MagicMock()
        wrapper._session.FindById.return_value = element
        return wrapper


def test_component_capability_includes_text_and_window_actions():
    text_capability = component_capability("GuiTextedit")
    window_capability = component_capability("GuiMainWindow")

    assert "append_text" in text_capability["actions"]
    assert "clear_text" in text_capability["actions"]
    assert "maximize" in window_capability["actions"]
    assert "minimize" in window_capability["actions"]


def test_ui_action_scrolls_first_visible_row_and_scrollbar():
    grid = FakeScrollableGrid()
    wrapper = _wrapper_with(grid)

    page = wrapper.perform_ui_action("wnd[0]/usr/cntlGRID/shellcont/shell", "page_down")
    absolute = wrapper.perform_ui_action("wnd[0]/usr/cntlGRID/shellcont/shell", "scroll", "3")

    horizontal = FakeHorizontalScrollable()
    wrapper_h = _wrapper_with(horizontal)
    h_result = wrapper_h.perform_ui_action("wnd[0]/usr", "scroll", "+5", column="horizontal")

    assert page["position"] == 10
    assert grid.FirstVisibleRow == 3
    assert absolute["position"] == 3
    assert h_result["position"] == 5
    assert horizontal.HorizontalScrollbar.Position == 5


def test_ui_action_appends_and_clears_text():
    text = FakeTextEdit()
    wrapper = _wrapper_with(text)

    appended = wrapper.perform_ui_action("wnd[0]/usr/cntlEDITOR/shell", "append_text", " world")
    cleared = wrapper.perform_ui_action("wnd[0]/usr/cntlEDITOR/shell", "clear_text")

    assert appended["text"] == "hello world"
    assert cleared["text_cleared"] is True
    assert text.Text == ""


def test_ui_action_performs_window_actions():
    window = FakeWindow()
    wrapper = _wrapper_with(window)

    maxed = wrapper.perform_ui_action("wnd[0]", "maximize")
    restored = wrapper.perform_ui_action("wnd[0]", "restore")
    minimized = wrapper.perform_ui_action("wnd[0]", "minimize")

    assert maxed["method"] == "Maximize"
    assert restored["method"] == "Restore"
    assert minimized["method"] == "Iconify"
    assert window.called == ["Maximize", "Restore", "Iconify"]


def test_ui_action_calls_explicit_method_and_get_sets_property():
    component = FakeGenericComponent()
    wrapper = _wrapper_with(component)

    called = wrapper.perform_ui_action(
        "wnd[0]/usr/custom",
        "call_method",
        "Echo",
        method_args=["A", 3],
    )
    read_old = wrapper.perform_ui_action(
        "wnd[0]/usr/custom",
        "get_property",
        property_name="SomeProperty",
    )
    written = wrapper.perform_ui_action(
        "wnd[0]/usr/custom",
        "set_property",
        "new",
        property_name="SomeProperty",
    )
    read_new = wrapper.perform_ui_action(
        "wnd[0]/usr/custom",
        "get_property",
        property_name="SomeProperty",
    )

    assert called["method_called"] == "Echo"
    assert called["result"] == {"prefix": "A", "count": 3}
    assert read_old["value"] == "old"
    assert written["property_set"] == "SomeProperty"
    assert read_new["value"] == "new"
