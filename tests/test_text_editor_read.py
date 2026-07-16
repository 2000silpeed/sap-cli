"""Regression coverage for SAP rich-text and ABAP editor readers."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from sap_cli.com_wrapper import SapGuiWrapper
from sap_cli.ui_map.scanner import UiMapScanner


class _ZeroBasedTextEdit:
    Type = "GuiTextedit"
    Name = "EDITOR"
    LineCount = 3

    def __init__(self) -> None:
        self.calls: list[int] = []
        self._lines = ["first", "second", "third"]

    def GetLineText(self, index: int) -> str:
        self.calls.append(index)
        return self._lines[index]


class _OneBasedAbapEditor:
    Type = "GuiAbapEditor"
    Name = "ABAP_SOURCE"

    def __init__(self) -> None:
        self.calls: list[int] = []
        self._lines = ["REPORT z_demo.", "WRITE 'ok'."]

    def GetLineCount(self) -> int:
        return len(self._lines)

    def GetLineText(self, index: int) -> str:
        self.calls.append(index)
        if index < 1:
            raise IndexError("one based")
        return self._lines[index - 1]


class _UnprotectedTextEdit:
    Type = "GuiTextedit"
    Name = "LONG_TEXT"
    NumberOfUnprotectedTextParts = 2

    def GetUnprotectedTextPart(self, index: int) -> str:
        return ("header", "body")[index]


class _SensitiveTextEdit(_ZeroBasedTextEdit):
    Name = "SECRET_EDITOR"


def _wrapper(element):
    with patch.object(SapGuiWrapper, "__init__", lambda self: None):
        wrapper = SapGuiWrapper()
        wrapper._session = MagicMock()
        wrapper._session.FindById.return_value = element
        return wrapper


def test_gui_textedit_read_uses_zero_based_line_api_and_bounds_output():
    editor = _ZeroBasedTextEdit()
    wrapper = _wrapper(editor)

    payload = wrapper.perform_ui_action(
        "wnd[0]/usr/cntlEDITOR/shell",
        "read",
        max_lines=2,
        max_chars=8,
    )

    assert payload["value"] == "first\nse"
    assert payload["text_editor"] == {
        "element_id": "wnd[0]/usr/cntlEDITOR/shell",
        "type": "GuiTextedit",
        "max_lines": 2,
        "max_chars": 8,
        "line_count": 3,
        "text": "first\nse",
        "lines_read": 2,
        "char_count": 8,
        "truncated": True,
        "read_method": "line_api",
    }
    assert editor.calls == [0, 1]


def test_gui_abap_editor_retries_one_based_documented_line_api():
    editor = _OneBasedAbapEditor()
    wrapper = _wrapper(editor)

    payload = wrapper.get_field_info("wnd[0]/usr/cntlEDITOR/shell")

    assert payload["value"] == "REPORT z_demo.\nWRITE 'ok'."
    assert payload["text_editor"]["read_method"] == "line_api"
    assert payload["text_editor"]["line_count"] == 2
    assert editor.calls == [0, 1, 2]


def test_inspect_element_exposes_bounded_text_editor_reading():
    editor = _ZeroBasedTextEdit()
    wrapper = _wrapper(editor)

    payload = wrapper.inspect_element("wnd[0]/usr/cntlEDITOR/shell")

    assert payload["text"] == "first\nsecond\nthird"
    assert payload["value"] == payload["text"]
    assert payload["text_editor"]["read_method"] == "line_api"


def test_textedit_falls_back_to_unprotected_parts_when_line_api_is_unavailable():
    wrapper = _wrapper(_UnprotectedTextEdit())

    payload = wrapper.read_text_editor("wnd[0]/usr/cntlEDITOR/shell")

    assert payload["text"] == "header\nbody"
    assert payload["read_method"] == "unprotected_parts"
    assert payload["parts_read"] == 2


def test_sensitive_text_editor_is_redacted_without_reading_body():
    editor = _SensitiveTextEdit()
    wrapper = _wrapper(editor)

    payload = wrapper.read_text_editor("wnd[0]/usr/cntlSECRET_EDITOR/shell")

    assert payload["text"] == "<redacted>"
    assert payload["redacted"] is True
    assert editor.calls == []


class _Collection:
    def __init__(self, values):
        self._values = list(values)
        self.Count = len(self._values)

    def __call__(self, index):
        return self._values[index]


def test_map_scanner_records_editor_size_but_never_editor_body():
    editor = SimpleNamespace(
        Id="wnd[0]/usr/cntlEDITOR/shell",
        Type="GuiAbapEditor",
        Name="ABAP_SOURCE",
        Tooltip="",
        IconName="",
        Visible=True,
        Enabled=True,
        Changeable=True,
        Required=False,
        Selected=False,
        Children=_Collection(()),
        GetLineCount=lambda: 345,
    )
    window = SimpleNamespace(
        Id="wnd[0]",
        Type="GuiMainWindow",
        Name="wnd[0]",
        Text="Editor",
        Tooltip="",
        IconName="",
        Visible=True,
        Enabled=True,
        Changeable=False,
        Required=False,
        Selected=False,
        Children=_Collection((editor,)),
    )
    session = SimpleNamespace(
        Children=_Collection((window,)),
        ActiveWindow=window,
        Info=SimpleNamespace(Transaction="SE38", Program="SAPLWBABAP", ScreenNumber=100),
    )

    state = UiMapScanner().scan(session)
    component = next(item for item in state.components if item.sap_id == editor.Id)

    assert component.text == ""
    assert component.volatile_properties["text_editor"] == {"line_count": 345}
