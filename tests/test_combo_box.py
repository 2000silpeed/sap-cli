"""ComboBox direct set/get support tests."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from sap_cli.com_wrapper import SapGuiWrapper


class FakeCombo:
    Type = "GuiComboBox"

    def __init__(self) -> None:
        self.Key = ""
        self.Value = ""
        self.Text = ""
        self.Name = "LFART"
        self.Entries = FakeEntries([
            FakeEntry("LF", "Standard Delivery", 1),
            FakeEntry("NL", "Normal Delivery", 2),
        ])


class FakeEntry:
    def __init__(self, key: str, value: str, pos: int) -> None:
        self.Key = key
        self.Value = value
        self.Pos = pos


class FakeEntries:
    def __init__(self, entries: list[FakeEntry]) -> None:
        self._entries = entries
        self.Count = len(entries)

    def __call__(self, index: int) -> FakeEntry:
        return self._entries[index]


class FakeOneBasedEntries:
    def __init__(self, entries: list[FakeEntry]) -> None:
        self._entries = entries

    def Count(self) -> int:
        return len(self._entries)

    def Item(self, index: int) -> FakeEntry:
        if index < 1 or index > len(self._entries):
            raise IndexError(index)
        return self._entries[index - 1]


class FakeComboWithMethodEntries:
    Type = "GuiComboBox"
    Key = ""
    Value = ""
    Text = ""
    Name = "LFART"

    def Entries(self) -> FakeOneBasedEntries:
        return FakeOneBasedEntries([
            FakeEntry("A", "Alpha", 1),
            FakeEntry("B", "Beta", 2),
        ])


class FakeTextField:
    Type = "GuiTextField"

    def __init__(self) -> None:
        self.Text = ""
        self.Name = "MATNR"


def _wrapper_with(element):
    with patch.object(SapGuiWrapper, "__init__", lambda self: None):
        wrapper = SapGuiWrapper()
        wrapper._session = MagicMock()
        wrapper._session.FindById.return_value = element
        return wrapper


def test_set_field_handles_gui_combo_box():
    combo = FakeCombo()
    wrapper = _wrapper_with(combo)

    wrapper.set_field("wnd[0]/usr/cmbLFART", "LF")

    assert combo.Key == "LF"
    assert wrapper.get_field("wnd[0]/usr/cmbLFART") == "LF"
    assert wrapper.get_field_info("wnd[0]/usr/cmbLFART") == {
        "name": "LFART",
        "field": "wnd[0]/usr/cmbLFART",
        "value": "LF",
        "key": "LF",
        "text": "",
        "entries": [
            {"key": "LF", "value": "Standard Delivery", "pos": "1"},
            {"key": "NL", "value": "Normal Delivery", "pos": "2"},
        ],
    }


def test_set_field_maps_display_value_to_combo_key():
    combo = FakeCombo()
    wrapper = _wrapper_with(combo)

    wrapper.set_field("wnd[0]/usr/cmbLFART", "Normal Delivery")

    assert combo.Key == "NL"


def test_select_combo_uses_same_combo_setter():
    combo = FakeCombo()
    wrapper = _wrapper_with(combo)

    wrapper.select_combo("wnd[0]/usr/cmbLFART", "NL")

    assert combo.Key == "NL"


def test_get_combo_entries():
    combo = FakeCombo()
    wrapper = _wrapper_with(combo)

    assert wrapper.get_combo_entries("wnd[0]/usr/cmbLFART") == [
        {"key": "LF", "value": "Standard Delivery", "pos": "1"},
        {"key": "NL", "value": "Normal Delivery", "pos": "2"},
    ]


def test_get_combo_entries_handles_method_and_one_based_collection():
    combo = FakeComboWithMethodEntries()
    wrapper = _wrapper_with(combo)

    assert wrapper.get_combo_entries("wnd[0]/usr/cmbLFART") == [
        {"key": "A", "value": "Alpha", "pos": "1"},
        {"key": "B", "value": "Beta", "pos": "2"},
    ]


def test_inspect_element_returns_combo_capability():
    combo = FakeCombo()
    wrapper = _wrapper_with(combo)

    inspected = wrapper.inspect_element("wnd[0]/usr/cmbLFART")

    assert inspected["type"] == "GuiComboBox"
    assert "set" in inspected["capability"]["actions"]
    assert inspected["ontology_preflight"]["type"] == "GuiComboBox"
    assert inspected["ontology_preflight"]["api_graph"]["runtime"]["nodes_total"] >= 1
    assert inspected["entries"][1] == {"key": "NL", "value": "Normal Delivery", "pos": "2"}


def test_perform_ui_action_sets_combo_by_display_value():
    combo = FakeCombo()
    wrapper = _wrapper_with(combo)

    result = wrapper.perform_ui_action(
        "wnd[0]/usr/cmbLFART",
        "set",
        "Normal Delivery",
    )

    assert result["action"] == "set"
    assert result["type"] == "GuiComboBox"
    assert result["ontology_preflight"]["normalized_action"] == "set"
    assert "set" in result["ontology_preflight"]["capability"]["actions"]
    assert combo.Key == "NL"


def test_build_action_context_includes_scripting_graph_summary():
    combo = FakeCombo()
    wrapper = _wrapper_with(combo)

    context = wrapper.build_action_context("wnd[0]/usr/cmbLFART", "set", "LF")

    assert context["type"] == "GuiComboBox"
    assert context["normalized_action"] == "set"
    assert context["api_graph"]["query"]["component_type"] == "GuiComboBox"
    assert context["api_graph"]["node_count"] >= 1


def test_set_field_keeps_text_field_behavior():
    field = FakeTextField()
    wrapper = _wrapper_with(field)

    wrapper.set_field("wnd[0]/usr/txtMATNR", "100-100")

    assert field.Text == "100-100"
