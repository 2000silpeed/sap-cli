import pytest

from sap_cli.planning.action_adapter import (
    UnsupportedTransitionError,
    compile_transition,
)
from sap_cli.ui_map.schema import SideEffectClass, Transition, UiComponent, UiState


def state(component_type="GuiTab"):
    return UiState(
        state_id="a",
        components=[
            UiComponent(
                logical_id="target",
                sap_id="wnd[0]/usr/target",
                type=component_type,
            )
        ],
    )


def transition(action, **updates):
    values = {
        "transition_id": "t1",
        "from_state_id": "a",
        "to_state_id": "b",
        "action": action,
        "target_logical_id": "target",
        "side_effect": SideEffectClass.REVERSIBLE,
        "reversible": True,
    }
    values.update(updates)
    return Transition(**values)


def test_typed_tab_scroll_and_tree_transitions_compile_to_registered_commands():
    tab = compile_transition(transition("select_tab"), state())
    assert (tab.command, tab.tool, tab.params) == (
        "ui_select",
        "sap_ui_select",
        {"element_id": "wnd[0]/usr/target"},
    )
    scroll = compile_transition(
        transition("scroll_to", observed_params={"position": 20}),
        state("GuiGridView"),
    )
    assert scroll.params["value"] == 20
    assert scroll.params["column"] == "vertical"
    horizontal_scroll = compile_transition(
        transition("scroll_to", observed_params={"position": 4, "axis": "horizontal"}),
        state("GuiGridView"),
    )
    assert horizontal_scroll.params["column"] == "horizontal"
    tree = compile_transition(
        transition("expand_node", observed_params={"node_key": "N1"}),
        state("GuiTree"),
    )
    assert tree.params["node_key"] == "N1"


def test_unknown_or_user_guided_transition_is_never_made_executable():
    with pytest.raises(UnsupportedTransitionError, match="unmapped"):
        compile_transition(transition("arbitrary_com_call"), state())
    with pytest.raises(UnsupportedTransitionError, match="user-guided"):
        compile_transition(transition("user_guided_navigation"), state())


def test_transition_must_be_supported_by_its_saved_component_capability():
    unsupported_state = UiState(
        state_id="a",
        components=[
            UiComponent(
                logical_id="target",
                sap_id="wnd[0]/usr/field",
                type="GuiTextField",
                supported_actions=["set", "read"],
            )
        ],
    )
    with pytest.raises(UnsupportedTransitionError, match="not supported"):
        compile_transition(transition("close"), unsupported_state)


def test_generic_press_requires_typed_pressable_component_and_keeps_approval():
    dangerous = transition(
        "press",
        side_effect=SideEffectClass.COMMIT,
        approval_required=True,
    )
    action = compile_transition(dangerous, state("GuiButton"))
    assert action.command == "ui_press"
    assert action.approval_required
    assert action.risk == "high"
    with pytest.raises(UnsupportedTransitionError, match="typed pressable"):
        compile_transition(dangerous, state("GuiTextField"))


def test_saved_toolbar_and_vkey_actions_compile_inside_closed_ui_command_set():
    toolbar = compile_transition(
        transition(
            "press_toolbar_button",
            observed_params={"value": "&SAVE"},
        ),
        state("GuiGridView"),
    )
    assert (toolbar.command, toolbar.tool, toolbar.params) == (
        "ui_press_toolbar_button",
        "sap_ui_press_toolbar_button",
        {"element_id": "wnd[0]/usr/target", "value": "&SAVE"},
    )

    menu = compile_transition(transition("select_menu"), state("GuiMenu"))
    assert (menu.command, menu.params) == (
        "ui_select", {"element_id": "wnd[0]/usr/target"}
    )

    context = compile_transition(
        transition(
            "select_context_menu_item",
            observed_params={"item_id": "&DETAIL"},
        ),
        state("GuiGridView"),
    )
    assert context.params == {
        "element_id": "wnd[0]/usr/target", "value": "&DETAIL"
    }

    window = UiState(
        state_id="a",
        components=[
            UiComponent(
                logical_id="target",
                sap_id="wnd[0]",
                type="GuiMainWindow",
            )
        ],
    )
    save = compile_transition(
        transition(
            "vkey",
            observed_params={"key": 11},
            target_locator={"sap_id": "wnd[0]", "component_type": "GuiMainWindow"},
            side_effect=SideEffectClass.COMMIT,
            approval_required=True,
        ),
        window,
    )
    assert save.command == "ui_call_method"
    assert save.params == {
        "element_id": "wnd[0]",
        "value": "SendVKey",
        "method_args": [11],
    }
    assert save.approval_required


def test_save_vkey_is_commit_and_requires_approval_even_from_legacy_navigation_map():
    window = UiState(
        state_id="a",
        components=[UiComponent(logical_id="target", sap_id="wnd[0]", type="GuiMainWindow")],
    )
    action = compile_transition(
        transition(
            "vkey",
            observed_params={"key": 11},
            target_locator={"sap_id": "wnd[0]", "component_type": "GuiMainWindow"},
            side_effect=SideEffectClass.NAVIGATION,
            approval_required=False,
        ),
        window,
    )
    assert action.side_effect is SideEffectClass.COMMIT
    assert action.risk == "high"
    assert action.approval_required
