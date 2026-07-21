from __future__ import annotations

from types import SimpleNamespace

from sap_cli.com_wrapper import SapGuiWrapper


class Element:
    def __init__(self, sap_id, ctype):
        self.Id = sap_id
        self.Type = ctype
        self.pressed = 0
        self.selected = 0

    def Press(self):
        if self.Type in ("GuiTab", "GuiMenu", "GuiRadioButton"):
            raise AssertionError(f"{self.Type} has no Press()")
        self.pressed += 1

    def Select(self):
        self.selected += 1


class Session:
    def __init__(self, elements):
        self._elements = {e.Id: e for e in elements}
        # is_connected probes session.Info.User.
        self.Info = SimpleNamespace(User="TESTER")

    def FindById(self, sap_id):
        return self._elements[sap_id]


def _wrapper(*elements):
    w = SapGuiWrapper()
    w._session = Session(list(elements))
    return w


def test_press_uses_select_for_menu_tab_radio_and_press_for_button():
    button = Element("wnd[0]/tbar[1]/btn[0]", "GuiButton")
    tab = Element("wnd[0]/usr/tabs/tabpX", "GuiTab")
    # A "More" menu item is a GuiMenu — it must be Select()ed, not Pressed.
    menu = Element("wnd[0]/mbar/menu[3]/menu[1]", "GuiMenu")
    # GuiRadioButton also has no Press(); it selects via Select().
    radio = Element("wnd[0]/usr/radRB", "GuiRadioButton")
    w = _wrapper(button, tab, menu, radio)

    w.press(button.Id)
    assert (button.pressed, button.selected) == (1, 0)

    w.press(tab.Id)
    assert (tab.pressed, tab.selected) == (0, 1)

    w.press(menu.Id)
    assert (menu.pressed, menu.selected) == (0, 1)

    w.press(radio.Id)
    assert (radio.pressed, radio.selected) == (0, 1)


def test_press_on_menu_no_longer_raises():
    menu = Element("wnd[0]/mbar/menu[0]", "GuiMenu")
    w = _wrapper(menu)
    # Before the fix this called Press() and failed with a SapGuiError.
    w.press(menu.Id)
    assert menu.selected == 1


class Field:
    """A settable field fake for set_field routing tests."""

    def __init__(self, sap_id, ctype):
        self.Id = sap_id
        self.Type = ctype
        self.Selected = False
        self.Text = ""
        self.selects = 0

    def Select(self):
        self.selects += 1


def test_set_field_routes_by_type_even_for_nonstandard_ids():
    # ids intentionally lack the /chk, /rad, /cmb conventions.
    chk = Field("wnd[0]/usr/ctrlOPT", "GuiCheckBox")
    rad = Field("wnd[0]/usr/ctrlMODE", "GuiRadioButton")
    txt = Field("wnd[0]/usr/ctrlNAME", "GuiTextField")
    w = _wrapper(chk, rad, txt)

    w.set_field(chk.Id, "true")
    assert chk.Selected is True
    w.set_field(chk.Id, "false")
    assert chk.Selected is False

    w.set_field(rad.Id, "x")
    assert rad.selects == 1

    w.set_field(txt.Id, "hello")
    assert txt.Text == "hello"
