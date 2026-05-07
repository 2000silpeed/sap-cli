"""SAP GUI Scripting component capability ontology.

This module is a compact, local map of common SAP GUI Scripting component
types to the operations an AI agent can attempt at runtime. It is intentionally
small and conservative: live COM inspection in :mod:`sap_cli.com_wrapper`
adds dynamic properties such as entries, current value, and subtype.
"""

from __future__ import annotations

from typing import Any

from .scripting_api_graph import get_default_graph


COMPONENT_CAPABILITIES: dict[str, dict[str, Any]] = {
    "GuiTextField": {
        "read": ["Text"],
        "write": ["Text"],
        "actions": ["read", "set", "focus"],
    },
    "GuiCTextField": {
        "read": ["Text"],
        "write": ["Text"],
        "actions": ["read", "set", "focus", "search_help"],
    },
    "GuiPasswordField": {
        "read": [],
        "write": ["Text"],
        "actions": ["set", "focus"],
    },
    "GuiOkCodeField": {
        "read": ["Text"],
        "write": ["Text"],
        "actions": ["set", "focus"],
    },
    "GuiComboBox": {
        "read": ["Key", "Value", "Text", "Entries"],
        "write": ["Key", "Value"],
        "actions": ["read", "set", "select", "choose", "focus"],
        "notes": [
            "Prefer Key selection. If only display text is known, resolve it via Entries.",
        ],
    },
    "GuiCheckBox": {
        "read": ["Selected", "Text"],
        "write": ["Selected"],
        "actions": ["read", "set", "toggle", "focus"],
    },
    "GuiRadioButton": {
        "read": ["Selected", "Text"],
        "write": ["Select"],
        "actions": ["read", "select", "focus"],
    },
    "GuiButton": {
        "read": ["Text", "Tooltip", "IconName"],
        "write": [],
        "actions": ["press", "focus"],
    },
    "GuiTab": {
        "read": ["Text"],
        "write": ["Select"],
        "actions": ["select", "press", "focus"],
    },
    "GuiMenu": {
        "read": ["Text"],
        "write": ["Select"],
        "actions": ["select", "press"],
    },
    "GuiTree": {
        "read": ["GetAllNodeKeys", "GetNodeTextByKey"],
        "write": ["SelectNode", "ExpandNode", "CollapseNode", "DoubleClickNode"],
        "actions": ["read_tree", "select_node", "expand_node", "collapse_node", "doubleclick_node"],
    },
    "GuiShell": {
        "read": ["SubType"],
        "write": [],
        "actions": ["inspect"],
        "subtypes": {
            "GridView": {
                "read": ["RowCount", "ColumnCount", "GetCellValue"],
                "write": ["SelectedRows", "DoubleClickCurrentCell"],
                "actions": ["read_table", "select_row", "doubleclick_cell"],
            },
            "Tree": {
                "read": ["GetAllNodeKeys", "GetNodeTextByKey"],
                "write": ["SelectNode", "ExpandNode", "CollapseNode", "DoubleClickNode"],
                "actions": ["read_tree", "select_node", "expand_node", "collapse_node", "doubleclick_node", "press_node"],
            },
        },
    },
}


ACTION_ALIASES: dict[str, str] = {
    "input": "set",
    "write": "set",
    "choose": "select",
    "click": "press",
    "button": "press",
    "tab": "select",
    "radio": "select",
    "checkbox": "set",
    "tree_select": "select_node",
    "tree-select": "select_node",
    "select-tree": "select_node",
    "tree_expand": "expand_node",
    "tree-expand": "expand_node",
    "tree_collapse": "collapse_node",
    "tree-collapse": "collapse_node",
    "tree_doubleclick": "doubleclick_node",
    "tree-doubleclick": "doubleclick_node",
}


def normalize_action(action: str) -> str:
    """Normalize user/AI action names into ontology action names."""
    normalized = action.strip().lower().replace(" ", "_")
    return ACTION_ALIASES.get(normalized, normalized)


def component_capability(component_type: str, subtype: str = "") -> dict[str, Any]:
    """Return static capability metadata for a component type/subtype."""
    graph_capability = get_default_graph().capability(component_type, subtype)
    if graph_capability:
        return graph_capability

    base = dict(COMPONENT_CAPABILITIES.get(component_type, {}))
    if subtype and isinstance(base.get("subtypes"), dict):
        subtype_meta = base["subtypes"].get(subtype)
        if subtype_meta:
            merged = dict(base)
            merged.pop("subtypes", None)
            merged.update(subtype_meta)
            return merged
    return base or {
        "read": [],
        "write": [],
        "actions": ["inspect", "focus"],
        "notes": ["Unknown SAP GUI component type; inspect live properties before acting."],
    }
