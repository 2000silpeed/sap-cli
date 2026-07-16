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
    "GuiTextedit": {
        "read": ["LineCount", "GetLineText", "NumberOfUnprotectedTextParts", "GetUnprotectedTextPart", "Text"],
        "write": ["Text"],
        "actions": ["read", "set", "append_text", "clear_text", "focus"],
    },
    "GuiAbapEditor": {
        "read": ["GetLineCount", "GetLineText", "Text"],
        "write": ["InsertText", "ReplaceSelection"],
        "actions": ["read", "focus"],
    },
    "GuiFrameWindow": {
        "read": ["Text"],
        "write": ["Maximize", "Restore", "Iconify", "Close"],
        "actions": ["maximize", "restore", "minimize", "close", "focus"],
    },
    "GuiMainWindow": {
        "read": ["Text"],
        "write": ["Maximize", "Restore", "Iconify", "Close"],
        "actions": ["maximize", "restore", "minimize", "close", "focus"],
    },
    "GuiModalWindow": {
        "read": ["Text"],
        "write": ["Close"],
        "actions": ["close", "focus"],
    },
    "GuiGridView": {
        "read": ["RowCount", "ColumnCount", "GetCellValue"],
        "write": [
            "ModifyCell", "SelectedRows", "SetCurrentCell", "ClickCurrentCell",
            "DoubleClickCurrentCell", "ContextMenu", "SelectContextMenuItem",
            "SelectColumn", "DeselectColumn", "ClearSelection", "PressToolbarButton",
            "PressToolbarContextButton", "SelectToolbarMenuItem",
        ],
        "actions": [
            "read_table", "read_cell", "set_cell", "set_current_cell", "click_cell",
            "select_row", "doubleclick_cell", "context_menu", "context_menu_item",
            "select_context_menu_item", "select_column", "deselect_column",
            "clear_selection", "press_toolbar_button", "press_toolbar_context_button",
            "select_toolbar_menu_item", "scroll", "scroll_to", "scroll_to_row", "scroll_down",
            "scroll_up", "page_down", "page_up", "call_method", "get_property",
            "set_property", "focus",
        ],
    },
    "GuiTableControl": {
        "read": ["Rows", "Columns", "GetCell"],
        "write": ["GetCell.Text", "GetAbsoluteRow.Selected", "DoubleClick"],
        "actions": [
            "read_table", "read_cell", "set_cell", "set_current_cell", "click_cell",
            "select_row", "doubleclick_cell", "context_menu", "context_menu_item",
            "select_context_menu_item", "clear_selection", "focus",
        ],
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
                "write": [
                    "ModifyCell", "SelectedRows", "SetCurrentCell", "ClickCurrentCell",
                    "DoubleClickCurrentCell", "ContextMenu", "SelectContextMenuItem",
                    "SelectColumn", "DeselectColumn", "ClearSelection", "PressToolbarButton",
                    "PressToolbarContextButton", "SelectToolbarMenuItem",
                ],
                "actions": [
                    "read_table", "read_cell", "set_cell", "set_current_cell", "click_cell",
                    "select_row", "doubleclick_cell", "context_menu", "context_menu_item",
                    "select_context_menu_item", "select_column", "deselect_column",
                    "clear_selection", "press_toolbar_button", "press_toolbar_context_button",
                    "select_toolbar_menu_item", "scroll", "scroll_to", "scroll_to_row", "scroll_down",
                    "scroll_up", "page_down", "page_up", "call_method", "get_property",
                    "set_property",
                ],
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
    "table_read": "read_table",
    "table-read": "read_table",
    "tree_read": "read_tree",
    "tree-read": "read_tree",
    "cell_read": "read_cell",
    "cell-read": "read_cell",
    "read-cell": "read_cell",
    "cell_set": "set_cell",
    "cell-set": "set_cell",
    "set-cell": "set_cell",
    "table_set": "set_cell",
    "table-set": "set_cell",
    "cell_doubleclick": "doubleclick_cell",
    "cell-doubleclick": "doubleclick_cell",
    "doubleclick-cell": "doubleclick_cell",
    "table_doubleclick": "doubleclick_cell",
    "table-doubleclick": "doubleclick_cell",
    "current_cell": "set_current_cell",
    "current-cell": "set_current_cell",
    "set-current-cell": "set_current_cell",
    "cell_click": "click_cell",
    "cell-click": "click_cell",
    "click-cell": "click_cell",
    "context": "context_menu",
    "context-menu": "context_menu",
    "context_menu_select": "context_menu_item",
    "context-menu-select": "context_menu_item",
    "context-menu-item": "context_menu_item",
    "select_context": "select_context_menu_item",
    "select-context": "select_context_menu_item",
    "select-context-menu-item": "select_context_menu_item",
    "column_select": "select_column",
    "column-select": "select_column",
    "select-column": "select_column",
    "column_deselect": "deselect_column",
    "column-deselect": "deselect_column",
    "deselect-column": "deselect_column",
    "clear-selection": "clear_selection",
    "clear_table_selection": "clear_selection",
    "clear-table-selection": "clear_selection",
    "toolbar_press": "press_toolbar_button",
    "toolbar-press": "press_toolbar_button",
    "press-toolbar": "press_toolbar_button",
    "press-toolbar-button": "press_toolbar_button",
    "toolbar_context": "press_toolbar_context_button",
    "toolbar-context": "press_toolbar_context_button",
    "press-toolbar-context": "press_toolbar_context_button",
    "press-toolbar-context-button": "press_toolbar_context_button",
    "toolbar_menu": "select_toolbar_menu_item",
    "toolbar-menu": "select_toolbar_menu_item",
    "toolbar-menu-item": "select_toolbar_menu_item",
    "select-toolbar-menu-item": "select_toolbar_menu_item",
    "scroll-to": "scroll_to",
    "scroll_to_position": "scroll_to",
    "scroll-to-position": "scroll_to",
    "scroll_to_row": "scroll_to_row",
    "scroll-to-row": "scroll_to_row",
    "scroll_down": "scroll_down",
    "scroll-down": "scroll_down",
    "scroll_up": "scroll_up",
    "scroll-up": "scroll_up",
    "page_down": "page_down",
    "page-down": "page_down",
    "pagedown": "page_down",
    "page_up": "page_up",
    "page-up": "page_up",
    "pageup": "page_up",
    "append-text": "append_text",
    "clear-text": "clear_text",
    "clear": "clear_text",
    "max": "maximize",
    "min": "minimize",
    "invoke": "call_method",
    "call-method": "call_method",
    "method": "call_method",
    "get-prop": "get_property",
    "get_property": "get_property",
    "set-prop": "set_property",
    "set_property": "set_property",
}


def normalize_action(action: str) -> str:
    """Normalize user/AI action names into ontology action names."""
    normalized = action.strip().lower().replace(" ", "_")
    return ACTION_ALIASES.get(normalized, normalized)


def component_capability(component_type: str, subtype: str = "") -> dict[str, Any]:
    """Return static capability metadata for a component type/subtype."""
    graph_capability = get_default_graph().capability(component_type, subtype)
    base = dict(COMPONENT_CAPABILITIES.get(component_type, {}))
    if subtype and isinstance(base.get("subtypes"), dict):
        subtype_meta = base["subtypes"].get(subtype)
        if subtype_meta:
            merged = dict(base)
            merged.pop("subtypes", None)
            merged.update(subtype_meta)
            base = merged

    if graph_capability:
        merged_capability = dict(graph_capability)
        for key in ("read", "write", "methods", "collections", "actions"):
            merged_capability[key] = sorted(set(graph_capability.get(key, [])) | set(base.get(key, [])))
        notes = [*graph_capability.get("notes", []), *base.get("notes", [])]
        if notes:
            merged_capability["notes"] = notes
        return merged_capability

    return base or {
        "read": [],
        "write": [],
        "actions": ["inspect", "focus", "call_method", "get_property", "set_property"],
        "notes": ["Unknown SAP GUI component type; inspect live properties before acting."],
    }
