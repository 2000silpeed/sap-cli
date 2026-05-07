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
    }


def test_select_combo_uses_same_combo_setter():
    combo = FakeCombo()
    wrapper = _wrapper_with(combo)

    wrapper.select_combo("wnd[0]/usr/cmbLFART", "NL")

    assert combo.Key == "NL"


def test_set_field_keeps_text_field_behavior():
    field = FakeTextField()
    wrapper = _wrapper_with(field)

    wrapper.set_field("wnd[0]/usr/txtMATNR", "100-100")

    assert field.Text == "100-100"
