"""Generic UI action scroll/text/window execution tests."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from sap_cli.api_handles import ApiHandleRegistry
from sap_cli.com_wrapper import SapGuiError, SapGuiWrapper
from sap_cli.dispatchers.sap_actions import dispatch_sap_action_command
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
    Type = "GuiGridView"
    SubType = "GridView"
    Name = "CUSTOM"

    def __init__(self) -> None:
        self.RowCount = 1
        self.SelectedRows = ""

    def GetCellValue(self, row: int, column: str) -> dict:
        return {"row": row, "column": column}


class FakeGuiCollection:
    Count = 1


class FakeVComponent:
    Type = "GuiVComponent"
    Name = "CUSTOM"

    def __init__(self) -> None:
        self.AccLabelCollection = FakeGuiCollection()

    def DumpState(self, inner_object: str) -> FakeGuiCollection:
        return FakeGuiCollection()


def _wrapper_with(element):
    with patch.object(SapGuiWrapper, "__init__", lambda self: None):
        wrapper = SapGuiWrapper()
        wrapper._session = MagicMock()
        wrapper._session.FindById.return_value = element
        wrapper._api_handles = ApiHandleRegistry()
        return wrapper


class FakeDispatchContext:
    slot_id = "ses0"


class FakeDispatchSap:
    def __init__(self) -> None:
        self.calls = []

    def perform_ui_action(self, element_id, action, value=None, **kwargs):
        self.calls.append((element_id, action, value, kwargs))
        return {"action": action, "element_id": element_id, "value": value}


def test_generated_ui_action_dispatch_injects_catalog_action():
    sap = FakeDispatchSap()

    result = dispatch_sap_action_command(
        "ui_append_text",
        {"element_id": "wnd[0]/usr/cntlEDITOR/shell", "value": " world"},
        FakeDispatchContext(),
        get_sap=lambda slot_id: sap,
        get_recorder=lambda: None,
        map_action=lambda action: action,
        execute_sap_action=lambda *args, **kwargs: None,
        collect_recipe_hints=lambda tcode: [],
        with_post_action=lambda payload, *_: payload,
    )

    assert result["action"] == "append_text"
    assert sap.calls[0][1] == "append_text"


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
        "GetCellValue",
        method_args=[0, "MATNR"],
    )
    read_old = wrapper.perform_ui_action(
        "wnd[0]/usr/custom",
        "get_property",
        property_name="RowCount",
    )
    written = wrapper.perform_ui_action(
        "wnd[0]/usr/custom",
        "set_property",
        "new",
        property_name="SelectedRows",
        trusted_plan=True,
    )
    read_new = wrapper.perform_ui_action(
        "wnd[0]/usr/custom",
        "get_property",
        property_name="SelectedRows",
    )

    assert called["method_called"] == "GetCellValue"
    assert called["result"] == {"row": 0, "column": "MATNR"}
    assert read_old["value"] == 1
    assert written["property_set"] == "SelectedRows"
    assert read_new["value"] == "new"


def test_high_risk_visual_member_cannot_bypass_immutable_plan_approval():
    component = FakeGenericComponent()
    component.DeleteRows = lambda *_args: None
    wrapper = _wrapper_with(component)
    try:
        wrapper.perform_ui_action(
            "wnd[0]/usr/custom", "call_method", "DeleteRows", method_args=[0, 1]
        )
    except SapGuiError as exc:
        assert "approved immutable plan" in str(exc)
    else:  # pragma: no cover - the policy gate is the assertion
        raise AssertionError("high-risk generic member was executed without a plan")


def test_visual_generic_actions_keep_documented_collection_return_types():
    wrapper = _wrapper_with(FakeVComponent())

    method_result = wrapper.perform_ui_action(
        "wnd[0]/usr/custom",
        "call_method",
        "DumpState",
        method_args=[""],
    )["result"]
    property_result = wrapper.perform_ui_action(
        "wnd[0]/usr/custom",
        "get_property",
        property_name="AccLabelCollection",
    )["value"]

    assert method_result["kind"] == "collection_handle"
    assert method_result["object_type"] == "GuiCollection"
    assert property_result["kind"] == "collection_handle"
    assert property_result["object_type"] == "GuiComponentCollection"
