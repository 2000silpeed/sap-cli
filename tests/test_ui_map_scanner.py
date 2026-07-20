from __future__ import annotations

from types import SimpleNamespace

from sap_cli.com_wrapper import SapGuiWrapper
from sap_cli.ui_map.scanner import UiMapScanner
from sap_cli.ui_map.schema import structural_fingerprint


class Collection:
    def __init__(self, items=(), *, bad=()):
        self.items = list(items)
        self.bad = set(bad)
        self.Count = len(self.items) + len(self.bad)

    def __call__(self, index):
        if index in self.bad:
            raise IndexError(f"bad index {index}")
        real = index - sum(value < index for value in self.bad)
        return self.items[real]


class ElementAtCollection:
    def __init__(self, items=()):
        self.items = list(items)
        self.Count = len(self.items)

    def ElementAt(self, index):
        return self.items[index]


class Element(SimpleNamespace):
    action_calls = 0

    def Press(self):
        type(self).action_calls += 1
        raise AssertionError("scanner called an action")

    def Select(self):
        type(self).action_calls += 1
        raise AssertionError("scanner called an action")


def element(sap_id, component_type, *, children=(), **values):
    defaults = {
        "Id": sap_id,
        "Type": component_type,
        "Name": sap_id.rsplit("/", 1)[-1],
        "Text": "",
        "Tooltip": "",
        "IconName": "",
        "Visible": True,
        "Enabled": True,
        "Changeable": False,
        "Required": False,
        "Selected": False,
        "Children": Collection(children),
    }
    defaults.update(values)
    return Element(**defaults)


class Grid(Element):
    def ColumnOrder(self, index):
        return self._columns[index]

    def GetColumnTitles(self, column):
        return [f"Title {column}"]

    def GetColumnDataType(self, column):
        return "String"

    def GetColumnPosition(self, column):
        return self._columns.index(column)

    def GetToolbarButtonId(self, index):
        return self._toolbar[index][0]

    def GetToolbarButtonText(self, index):
        return self._toolbar[index][1]

    def GetToolbarButtonTooltip(self, index):
        return f"tip-{index}"

    def GetToolbarButtonIcon(self, index):
        return f"icon-{index}"

    def GetToolbarButtonType(self, index):
        return "Menu" if index == 1 else "Button"

    def GetToolbarButtonEnabled(self, index):
        return True

    def GetToolbarButtonChecked(self, index):
        return False

    def IsColumnFiltered(self, column):
        return column == "C02"

    def GetColumnSortType(self, column):
        return "Ascending" if column == "C03" else "None"


def grid(sap_id, columns=25, toolbar=()):
    obj = Grid(
        Id=sap_id,
        Type="GuiGridView",
        SubType="GridView",
        Name="ALV",
        Text="",
        Tooltip="ALV",
        IconName="",
        Visible=True,
        Enabled=True,
        Changeable=False,
        Required=False,
        Selected=False,
        Children=Collection(),
        ColumnCount=columns,
        RowCount=99,
        FirstVisibleRow=10,
        VisibleRowCount=15,
        SelectedRows="1,3-5",
        SelectionMode="Multiple",
        ToolbarButtonCount=len(toolbar),
        HorizontalScrollbar=SimpleNamespace(Position=2),
        VerticalScrollbar=SimpleNamespace(Position=10),
        CurrentCellRow=12,
        CurrentCellColumn="C12",
    )
    obj._columns = [f"C{i:02d}" for i in range(columns)]
    obj._toolbar = list(toolbar)
    return obj


def session(*windows, active=None):
    return SimpleNamespace(
        Children=Collection(windows),
        ActiveWindow=active or windows[-1],
        Info=SimpleNamespace(
            User="TEST", Transaction="SE16", Program="SAPLSETB", ScreenNumber=1000
        ),
    )


def test_live_session_transaction_overrides_caller_tcode_hint():
    main = element("wnd[0]", "GuiMainWindow", Text="Main")
    state = UiMapScanner().scan(session(main), tcode="MM03")
    assert state.tcode == "SE16"


def test_input_values_and_status_text_are_not_persisted_but_status_metadata_is_kept():
    material = element(
        "wnd[0]/usr/ctxtMATNR", "GuiCTextField", Text="PRIVATE-MATNR", Value="PRIVATE-MATNR"
    )
    status = element(
        "wnd[0]/sbar", "GuiStatusbar", Text="PRIVATE STATUS", MessageType="E",
        MessageId="M3", MessageNumber="123"
    )
    main = element("wnd[0]", "GuiMainWindow", children=(material, status))
    state = UiMapScanner().scan(session(main))

    field = next(item for item in state.components if item.type == "GuiCTextField")
    status_component = next(item for item in state.components if item.type == "GuiStatusbar")
    assert field.text == "<redacted>"
    assert field.volatile_properties["text"] == "<redacted>"
    assert status_component.text == "<redacted>"
    assert state.normalized_status["statusbar"] == {
        "message_type": "E", "message_id": "M3", "message_number": "123"
    }
    serialized = state.model_dump_json()
    assert "PRIVATE-MATNR" not in serialized
    assert "PRIVATE STATUS" not in serialized


def test_grid_visible_cells_capture_only_structural_affordances_not_values():
    alv = grid("wnd[0]/usr/cntlALV/shell", columns=2)
    alv.FirstVisibleRow = 5
    alv.VisibleRowCount = 2
    alv.GetCellType = lambda row, column: "GuiButton" if column == "C00" else "GuiTextField"
    alv.GetCellChangeable = lambda row, column: column == "C01"
    alv.IsCellSelected = lambda row, column: row == 5 and column == "C00"
    alv.IsCellHotspot = lambda row, column: column == "C00"
    alv.HasCellF4Help = lambda row, column: column == "C01"
    # A value method intentionally exists, but the scanner must never call it.
    alv.GetCellValue = lambda *_args: (_ for _ in ()).throw(AssertionError("value read"))
    main = element("wnd[0]", "GuiMainWindow", children=(alv,))

    state = UiMapScanner().scan(session(main))
    descriptor = next(item for item in state.collections if item.kind in {"grid", "alv"})
    assert len(descriptor.visible_cells) == 4
    button = next(item for item in descriptor.visible_cells if item.column_id == "C00")
    editable = next(item for item in descriptor.visible_cells if item.column_id == "C01")
    assert button.absolute_row == 5
    assert button.affordances == ["open_cell", "press_cell"]
    assert editable.affordances == ["set_cell", "value_help"]
    assert "GetCellValue" not in state.model_dump_json()


def test_apo_grid_and_calendar_are_saved_as_structural_collections_without_dates_or_values():
    apo = element(
        "wnd[0]/usr/cntlAPO", "GuiApoGrid", ColumnCount=2, RowCount=40,
        FirstVisibleRow=3, VisibleRowCount=2,
    )
    apo.GetCellType = lambda row, column: "GuiCheckBox"
    apo.GetCellChangeable = lambda row, column: True
    apo.IsCellSelected = lambda row, column: False
    apo.IsCellHotspot = lambda row, column: False
    apo.HasCellF4Help = lambda row, column: False
    calendar = element(
        "wnd[0]/usr/cal", "GuiCalendar", FirstVisibleDate="20260701",
        LastVisibleDate="20260731", FocusDate="20260715", SelectionInterval="20260715-20260715",
        FocusedElement=2, horizontal=True,
    )
    main = element("wnd[0]", "GuiMainWindow", children=(apo, calendar))

    state = UiMapScanner().scan(session(main))
    apo_descriptor = next(item for item in state.collections if item.kind == "apo_grid")
    calendar_descriptor = next(item for item in state.collections if item.kind == "calendar")
    assert [column.id for column in apo_descriptor.columns] == ["0", "1"]
    assert len(apo_descriptor.visible_cells) == 4
    assert apo_descriptor.visible_cells[0].affordances == ["set_cell"]
    assert calendar_descriptor.sample_policy == "calendar_structure_only"
    assert calendar_descriptor.volatile_properties["FocusDate"] == "<redacted>"
    serialized = state.model_dump_json()
    assert "20260715" not in serialized


def test_table_visible_cells_keep_types_and_editability_without_cell_values():
    columns = Collection(
        (
            SimpleNamespace(Name="MATNR", Title="Material", Position=0, Changeable=True, Visible=True),
            SimpleNamespace(Name="SELECT", Title="Select", Position=1, Changeable=True, Visible=True),
        )
    )
    table = element(
        "wnd[0]/usr/tblITEMS", "GuiTableControl", Columns=columns, RowCount=20,
        FirstVisibleRow=7, VisibleRowCount=2,
    )
    cells = {
        (0, 0): element("ignored", "GuiTextField", Text="PRIVATE-ROW-7", Changeable=True),
        (0, 1): element("ignored", "GuiCheckBox", Text="PRIVATE-CHECK", Changeable=True),
        (1, 0): element("ignored", "GuiTextField", Text="PRIVATE-ROW-8", Changeable=False),
        (1, 1): element("ignored", "GuiButton", Text="PRIVATE-BUTTON", Changeable=False),
    }
    table.GetCell = lambda row, column: cells[(row, column)]
    main = element("wnd[0]", "GuiMainWindow", children=(table,))

    state = UiMapScanner().scan(session(main))
    descriptor = next(item for item in state.collections if item.kind == "table")
    assert [(cell.absolute_row, cell.column_id) for cell in descriptor.visible_cells] == [
        (7, "MATNR"), (7, "SELECT"), (8, "MATNR"), (8, "SELECT")
    ]
    assert descriptor.visible_cells[0].affordances == ["set_cell"]
    assert descriptor.visible_cells[1].affordances == ["set_cell"]
    assert descriptor.visible_cells[-1].affordances == ["press_cell"]
    serialized = state.model_dump_json()
    assert "PRIVATE-ROW-7" not in serialized
    assert "PRIVATE-BUTTON" not in serialized


def test_scans_lossless_hierarchy_all_columns_toolbars_and_menus():
    Element.action_calls = 0
    nested_menu = element("wnd[0]/mbar/menu[0]/menu[0]", "GuiMenu", Text="Log off")
    menu_a = element(
        "wnd[0]/mbar/menu[0]", "GuiMenu", Text="System", children=(nested_menu,)
    )
    menu_b = element("wnd[0]/mbar/menu[1]", "GuiMenu", Text="Help")
    mbar = element("wnd[0]/mbar", "GuiMenubar", children=(menu_a, menu_b))
    button_a = element("wnd[0]/tbar[0]/btn[0]", "GuiButton", Text="Back")
    button_b = element("wnd[0]/tbar[1]/btn[0]", "GuiButton", Text="Find")
    toolbar_a = element("wnd[0]/tbar[0]", "GuiToolbar", children=(button_a,))
    toolbar_b = element("wnd[0]/tbar[1]", "GuiToolbar", children=(button_b,))
    alv = grid("wnd[0]/usr/cntlALV/shell", 27, (("EXPORT", "Export"), ("LAYOUT", "Layout")))
    tab = element("wnd[0]/usr/tabs/tabpMAIN", "GuiTab", Text="Main")
    tabs = element(
        "wnd[0]/usr/tabs", "GuiTabStrip", children=(tab, alv), SelectedTab=tab
    )
    window = element(
        "wnd[0]", "GuiMainWindow", Text="Data Browser", children=(mbar, toolbar_a, toolbar_b, tabs)
    )

    scanner = UiMapScanner()
    state = scanner.scan(session(window))

    assert len(state.components) == 12
    assert {item.sap_id for item in state.components} >= {
        "wnd[0]/mbar/menu[0]",
        "wnd[0]/mbar/menu[1]",
        "wnd[0]/mbar/menu[0]/menu[0]",
        "wnd[0]/tbar[0]/btn[0]",
        "wnd[0]/tbar[1]/btn[0]",
    }
    descriptor = next(item for item in state.collections if item.kind == "alv")
    assert len(descriptor.columns) == 27
    assert descriptor.columns[-1].id == "C26"
    assert descriptor.visible_range == (10, 24)
    assert descriptor.selected_rows == [1, 3, 4, 5]
    assert [item["id"] for item in descriptor.toolbar_actions] == ["EXPORT", "LAYOUT"]
    assert descriptor.filtered_columns == ["C02"]
    assert descriptor.sorted_columns == ["C03"]
    assert state.active_tabs["wnd[0]/usr/tabs"] == "wnd[0]/usr/tabs/tabpMAIN"
    assert Element.action_calls == 0
    assert scanner.metrics.query_calls > 27
    assert scanner.metrics.elapsed_ms >= 0


def test_modal_stack_unknown_shell_sensitive_fields_and_bounds_are_preserved():
    secret = element(
        "wnd[1]/usr/pwdPASSWORD",
        "GuiPasswordField",
        Text="never-store-me",
        Value="also-secret",
        ScreenLeft=10,
        ScreenTop=20,
        Width=100,
        Height=22,
        Changeable=True,
        Required=True,
    )
    unknown = element(
        "wnd[1]/usr/shellSPECIAL", "GuiShell", SubType="PictureControl", Tooltip="special"
    )
    main = element("wnd[0]", "GuiMainWindow", Text="Main")
    modal = element(
        "wnd[1]", "GuiModalWindow", Text="Credentials", IsModal=True, children=(secret, unknown)
    )

    scanner = UiMapScanner()
    state = scanner.scan(session(main, modal, active=modal))

    assert [window.sap_id for window in state.window_stack] == ["wnd[0]", "wnd[1]"]
    assert state.window_stack[-1].modal is True
    assert state.window_stack[-1].active is True
    assert state.window_stack[0].active is False
    assert "GuiModalWindow:wnd[1]" in state.modal_signature
    assert state.window_stack[-1].signature != "GuiModalWindow:wnd[1]"
    password = next(item for item in state.components if item.sap_id.endswith("pwdPASSWORD"))
    assert password.sensitive is True
    assert password.text == "<redacted>"
    assert password.volatile_properties["value"] == "<redacted>"
    assert password.bounds == {"left": 10, "top": 20, "width": 100, "height": 22}
    raw = next(item for item in state.components if item.sap_id.endswith("shellSPECIAL"))
    assert raw.role == "shell:PictureControl"
    assert next(item for item in state.collections if item.component_logical_id == raw.logical_id).volatile_properties["preserved_raw"]


def test_combo_entries_are_collected_read_only_and_sensitive_values_redacted():
    Element.action_calls = 0
    combo = element(
        "wnd[0]/usr/cmbLANG",
        "GuiComboBox",
        Key="KO",
        Value="Korean",
        Entries=Collection(
            [
                SimpleNamespace(Key="EN", Value="English", Pos=0),
                SimpleNamespace(Key="KO", Value="Korean", Pos=1),
            ]
        ),
    )
    sensitive = element(
        "wnd[0]/usr/cmbTOKEN",
        "GuiComboBox",
        Name="API_TOKEN",
        Entries=Collection([SimpleNamespace(Key="secret", Value="TOP-SECRET", Pos=0)]),
    )
    window = element("wnd[0]", "GuiMainWindow", children=(combo, sensitive))

    state = UiMapScanner().scan(session(window))

    language = next(item for item in state.components if item.sap_id.endswith("cmbLANG"))
    assert language.static_properties["entries"] == [
        {"key": "EN", "value": "English", "position": 0},
        {"key": "KO", "value": "Korean", "position": 1},
    ]
    assert language.static_properties["entries_count"] == 2
    token = next(item for item in state.components if item.sap_id.endswith("cmbTOKEN"))
    assert token.static_properties["entries"] == [
        {"key": "<redacted>", "value": "<redacted>", "position": 0}
    ]
    assert Element.action_calls == 0


def test_combo_option_values_do_not_create_new_structural_state():
    first = element(
        "wnd[0]/usr/cmbSTATUS", "GuiComboBox",
        Entries=Collection([SimpleNamespace(Key="A100", Value="Customer A", Pos=0)]),
    )
    second = element(
        "wnd[0]/usr/cmbSTATUS", "GuiComboBox",
        Entries=Collection([SimpleNamespace(Key="B200", Value="Customer B", Pos=0)]),
    )
    first_state = UiMapScanner().scan(session(element("wnd[0]", "GuiMainWindow", children=(first,))))
    second_state = UiMapScanner().scan(session(element("wnd[0]", "GuiMainWindow", children=(second,))))

    assert structural_fingerprint(first_state) == structural_fingerprint(second_state)


def test_index_failures_do_not_abort_later_siblings_and_are_measured():
    first = element("wnd[0]/usr/txtA", "GuiTextField", Text="A")
    second = element("wnd[0]/usr/btnB", "GuiButton", Text="B")
    window = element("wnd[0]", "GuiMainWindow", Text="Main")
    window.Children = Collection((first, second), bad=(1, 3))

    scanner = UiMapScanner()
    state = scanner.scan(session(window))

    ids = {component.sap_id for component in state.components}
    assert {"wnd[0]/usr/txtA", "wnd[0]/usr/btnB"} <= ids
    assert scanner.metrics.collection_calls >= 5
    assert len(scanner.metrics.errors) == 2
    assert {error["operation"] for error in scanner.metrics.errors} == {"index[1]", "index[3]"}
    assert state.normalized_status["scan_metrics"]["error_count"] == 2


def test_table_and_tree_collection_descriptors_are_separate():
    columns = Collection(
        [
            SimpleNamespace(Name="MATNR", Title="Material", Position=0, Changeable=True, Visible=True),
            SimpleNamespace(Name="MAKTX", Title="Description", Position=1, Changeable=False, Visible=True),
        ]
    )
    table = element(
        "wnd[0]/usr/tblDATA",
        "GuiTableControl",
        Columns=columns,
        RowCount=40,
        FirstVisibleRow=5,
        VisibleRowCount=10,
        VerticalScrollbar=SimpleNamespace(Position=5),
        SelectedRows="2;7",
    )

    class Tree(Element):
        def GetColumnNames(self):
            return ["NODE", "VALUE"]

        def GetColumnTitles(self):
            return ["Node", "Value"]

        def GetSelectedNodes(self):
            return ["N2"]

        def GetAllNodeKeys(self):
            return ["ROOT", "N2"]

        def GetNodeTextByKey(self, key):
            return {"ROOT": "Root", "N2": "Second"}[key]

        def GetParent(self, key):
            return "" if key == "ROOT" else "ROOT"

        def GetNodePathByKey(self, key):
            return key if key == "ROOT" else f"ROOT/{key}"

        def GetNodeChildrenCount(self, key):
            return 1 if key == "ROOT" else 0

        def IsFolderExpandable(self, key):
            return key == "ROOT"

        def IsFolderExpanded(self, key):
            return key == "ROOT"

    tree = Tree(**element("wnd[0]/usr/tree", "GuiTree").__dict__)
    window = element("wnd[0]", "GuiMainWindow", children=(table, tree))

    state = UiMapScanner().scan(session(window))
    by_kind = {item.kind: item for item in state.collections}
    assert [column.id for column in by_kind["table"].columns] == ["MATNR", "MAKTX"]
    assert by_kind["table"].visible_range == (5, 14)
    assert by_kind["tree"].selected_node_keys == ["N2"]
    assert by_kind["tree"].total_node_count == 2
    assert by_kind["tree"].nodes[0].expandable is True
    assert by_kind["tree"].nodes[1].parent_key == "ROOT"
    assert by_kind["tree"].nodes[1].selected is True
    assert by_kind["tree"].expanded_node_keys == ["ROOT"]
    assert by_kind["tree"].coverage["unexpanded_descendants"] == "not_observed_until_expanded"


def test_element_at_collections_are_supported_without_callable_indexer():
    child = element("wnd[0]/usr/txtA", "GuiTextField", Text="A")
    window = element("wnd[0]", "GuiMainWindow", Text="Main")
    window.Children = ElementAtCollection([child])
    sap_session = session(window)
    sap_session.Children = ElementAtCollection([window])

    state = UiMapScanner().scan(sap_session)

    assert {component.sap_id for component in state.components} == {
        "wnd[0]",
        "wnd[0]/usr/txtA",
    }


def test_wrapper_exposes_lossless_scan_for_dispatcher_runtime():
    window = element("wnd[0]", "GuiMainWindow", Text="Main")
    wrapper = SapGuiWrapper()
    wrapper._session = session(window)

    result = wrapper.scan_ui_state()

    assert result["state"]["tcode"] == "SE16"
    assert result["state"]["components"][0]["sap_id"] == "wnd[0]"
    assert result["metrics"]["components"] == 1


def test_active_tab_comes_from_tabstrip_selectedtab_not_gui_tab_selected():
    # GuiTab has no Selected property in the SAP GUI Scripting API; the active
    # tab is the parent GuiTabStrip.SelectedTab. If the scanner can't tell which
    # tab is active, guided traversal finds no reversible sibling to restore to
    # and offers no tab candidate — the strip's tabs never get mapped and the
    # job ends early. Regression for that.
    tab1 = element("wnd[0]/usr/tabsSTRIP/tabpTAB1", "GuiTab")
    tab2 = element("wnd[0]/usr/tabsSTRIP/tabpTAB2", "GuiTab")
    strip = element(
        "wnd[0]/usr/tabsSTRIP",
        "GuiTabStrip",
        children=(tab1, tab2),
        SelectedTab=tab1,
    )
    main = element("wnd[0]", "GuiMainWindow", children=(strip,))

    state = UiMapScanner().scan(session(main))

    by_id = {component.sap_id: component for component in state.components}
    assert by_id["wnd[0]/usr/tabsSTRIP/tabpTAB1"].selected is True
    assert by_id["wnd[0]/usr/tabsSTRIP/tabpTAB2"].selected is False

    strip_lid = by_id["wnd[0]/usr/tabsSTRIP"].logical_id
    active_lid = by_id["wnd[0]/usr/tabsSTRIP/tabpTAB1"].logical_id
    assert state.active_tabs.get(strip_lid) == active_lid


def test_scanned_tab_strip_yields_a_select_tab_candidate():
    # End-to-end: the scanned state must let the traversal adapter offer a
    # select_tab candidate for the inactive sibling — the exact thing guided
    # traversal needs and that was missing before the SelectedTab fix.
    from sap_cli.ui_map.sap_traversal import SapTraversalAdapter

    tab1 = element("wnd[0]/usr/tabsSTRIP/tabpTAB1", "GuiTab")
    tab2 = element("wnd[0]/usr/tabsSTRIP/tabpTAB2", "GuiTab")
    strip = element(
        "wnd[0]/usr/tabsSTRIP",
        "GuiTabStrip",
        children=(tab1, tab2),
        SelectedTab=tab1,
    )
    main = element("wnd[0]", "GuiMainWindow", children=(strip,))
    state = UiMapScanner().scan(session(main))

    candidates = list(SapTraversalAdapter(SimpleNamespace()).discover(state))
    tab_targets = {
        c.target_locator.get("sap_id")
        for c in candidates
        if c.action == "select_tab"
    }
    # The inactive tab is offered; the active one is not.
    assert "wnd[0]/usr/tabsSTRIP/tabpTAB2" in tab_targets
    assert "wnd[0]/usr/tabsSTRIP/tabpTAB1" not in tab_targets
