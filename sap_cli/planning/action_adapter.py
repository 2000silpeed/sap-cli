"""Closed translation from observed map transitions to dispatcher commands."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sap_cli.dispatchers.registry import COMMAND_REGISTRY
from sap_cli.ui_action_catalog import get_ui_action_def
from sap_cli.ui_map.schema import SideEffectClass, Transition, UiState


class UnsupportedTransitionError(ValueError):
    pass


@dataclass(frozen=True)
class CompiledAction:
    command: str
    tool: str
    params: dict[str, Any]
    side_effect: SideEffectClass
    risk: str
    approval_required: bool


_MAPPING = {
    "select_tab": ("ui_select", "select"),
    "select": ("ui_select", "select"),
    "scroll_to": ("ui_scroll_to", "scroll_to"),
    "expand_node": ("ui_expand_node", "expand_node"),
    "collapse_node": ("ui_collapse_node", "collapse_node"),
    "read": ("ui_read", "read"),
    "inspect": ("ui_inspect", "inspect"),
    "press": ("ui_press", "press"),
    # Map traversal records menu selection as a distinct structural branch;
    # SAP GUI exposes the actual operation through the standard select action.
    "select_menu": ("ui_select", "select"),
}

_PASSTHROUGH_PARAMS = {
    "value",
    "node_key",
    "column",
    "row",
    "max_rows",
    "columns",
    "method_args",
    "property_name",
    "property_value",
}


def compile_transition(transition: Transition, source_state: UiState) -> CompiledAction:
    action = transition.action.strip().lower().replace("-", "_").replace(" ", "_")
    if action == "user_guided_navigation":
        raise UnsupportedTransitionError("user-guided transition requires live user input")
    mapped = _MAPPING.get(action)
    if mapped is None:
        definition = get_ui_action_def(f"ui_{action}")
        if definition is not None:
            mapped = (definition.command, action)
    if mapped is None and action in {"vkey", "send_vkey"}:
        # Keep execution inside the immutable ui_* command set.  SendVKey is a
        # documented GuiWindow method and is therefore represented by the
        # graph-aware generic method adapter rather than a special live path.
        mapped = ("ui_call_method", "call_method")
    if mapped is None:
        raise UnsupportedTransitionError(f"unmapped UI-map transition action: {transition.action}")
    command, ui_action = mapped
    definition = COMMAND_REGISTRY.get(command)
    if definition is None:
        raise UnsupportedTransitionError(f"dispatcher command is not registered: {command}")
    element_id = str(transition.target_locator.get("sap_id", ""))
    component = next(
        (
            item
            for item in source_state.components
            if item.logical_id == transition.target_logical_id
        ),
        None,
    )
    if not element_id and component is not None:
        element_id = component.sap_id
    if not element_id:
        raise UnsupportedTransitionError("transition target has no stable SAP element id")
    if component is not None and action not in {"vkey", "send_vkey"}:
        supported = {
            value.strip().lower().replace("-", "_")
            for value in component.supported_actions
        }
        expected_capability = (
            "select" if action in {"select_menu", "select_tab"} else action
        )
        if supported and expected_capability not in supported:
            raise UnsupportedTransitionError(
                f"{action} is not supported by saved component type {component.type}"
            )
    if action == "press":
        component_type = str(
            transition.target_locator.get("component_type", "")
            or (component.type if component is not None else "")
        )
        if component_type not in {"GuiButton", "GuiMenu", "GuiToolbar"}:
            raise UnsupportedTransitionError("press target is not a typed pressable component")

    params: dict[str, Any] = {"element_id": element_id}
    for key in _PASSTHROUGH_PARAMS:
        if key in transition.observed_params:
            params[key] = transition.observed_params[key]
    if ui_action in {"scroll_to"}:
        if "position" not in transition.observed_params:
            raise UnsupportedTransitionError("scroll transition has no observed position")
        params["value"] = transition.observed_params["position"]
        axis = str(transition.observed_params.get("axis", "vertical")).casefold()
        if axis not in {"horizontal", "vertical"}:
            raise UnsupportedTransitionError("scroll transition axis is invalid")
        # ``perform_ui_action`` uses ``column`` as the generic scroll
        # orientation argument.  Preserve the observed axis so horizontal ALV
        # navigation is replayed horizontally instead of silently vertically.
        params["column"] = axis
    if ui_action in {"expand_node", "collapse_node"}:
        node_key = transition.observed_params.get("node_key")
        if node_key in (None, ""):
            raise UnsupportedTransitionError("tree transition has no node_key")
        params["node_key"] = node_key
    if action in {"vkey", "send_vkey"}:
        component_type = str(
            transition.target_locator.get("component_type", "")
            or (component.type if component is not None else "")
        )
        if component_type not in {"GuiMainWindow", "GuiModalWindow"}:
            raise UnsupportedTransitionError("vkey target is not a typed SAP window")
        raw_key = transition.observed_params.get(
            "key", transition.observed_params.get("vkey")
        )
        if raw_key in (None, ""):
            raise UnsupportedTransitionError("vkey transition has no observed key")
        try:
            key = int(raw_key)
        except (TypeError, ValueError) as exc:
            raise UnsupportedTransitionError("vkey transition key is not an integer") from exc
        params.update({"value": "SendVKey", "method_args": [key]})
    if action in {"press_toolbar_button", "select_context_menu_item"}:
        item_id = transition.observed_params.get(
            "item_id", transition.observed_params.get("value")
        )
        if item_id in (None, ""):
            item_id = transition.target_locator.get("item_id")
        if item_id in (None, ""):
            raise UnsupportedTransitionError(
                f"{action} transition has no observed item_id"
            )
        params["value"] = str(item_id)

    effective_side_effect = transition.side_effect
    if action in {"vkey", "send_vkey"} and key == 11:
        # SAP standard VKey 11 is Save.  It must retain commit semantics even
        # if a legacy/discovered map recorded the transition as navigation.
        effective_side_effect = SideEffectClass.COMMIT
    risk = {
        SideEffectClass.READ_ONLY: "low",
        SideEffectClass.REVERSIBLE: "low",
        SideEffectClass.NAVIGATION: "medium",
        SideEffectClass.COMMIT: "high",
        SideEffectClass.DESTRUCTIVE: "critical",
        SideEffectClass.EXTERNAL: "high",
    }[effective_side_effect]
    approval = transition.approval_required or effective_side_effect in {
        SideEffectClass.COMMIT,
        SideEffectClass.DESTRUCTIVE,
        SideEffectClass.EXTERNAL,
    }
    return CompiledAction(
        command=command,
        tool=definition.tool_name,
        params=params,
        side_effect=effective_side_effect,
        risk=risk,
        approval_required=approval,
    )


__all__ = ["CompiledAction", "UnsupportedTransitionError", "compile_transition"]
