"""Command registry and derived command/tool mappings.

This module is the single source of truth for dispatcher command metadata.
The top-level :mod:`sap_cli.dispatcher` module re-exports these names for
backward compatibility with existing CLI, MCP, daemon, and test imports.
"""

from __future__ import annotations

from dataclasses import dataclass

from sap_cli.auth.policy import Permission
from sap_cli.ui_action_catalog import get_ui_action_defs


@dataclass(slots=True)
class CommandDef:
    tool_name: str
    permission: Permission
    mcp_arg_map: dict[str, str] | None = None  # MCP인자명 -> dispatcher인자명
    expose_cli: bool = True
    expose_mcp: bool = True
    description: str = ""
    category: str = "general"
    risk_level: str = "medium"


COMMAND_REGISTRY: dict[str, CommandDef] = {
    "connect": CommandDef("sap_connect", Permission.ADMIN),
    "connections": CommandDef("sap_connections", Permission.READ),
    "session": CommandDef("sap_session", Permission.READ),
    "screen": CommandDef("sap_screen", Permission.READ),
    "map_scan": CommandDef(
        "sap_map_scan", Permission.READ, category="ui_map", risk_level="low"
    ),
    "map_list": CommandDef(
        "sap_map_list", Permission.READ, category="ui_map", risk_level="low"
    ),
    "map_search": CommandDef(
        "sap_map_search", Permission.READ, category="ui_map", risk_level="low"
    ),
    "map_path": CommandDef(
        "sap_map_path", Permission.READ, category="ui_map", risk_level="low"
    ),
    "map_report": CommandDef(
        "sap_map_report", Permission.READ, category="ui_map", risk_level="low"
    ),
    "map_job_status": CommandDef(
        "sap_map_job_status", Permission.READ, category="ui_map", risk_level="low"
    ),
    "map_job_cancel": CommandDef(
        "sap_map_job_cancel", Permission.WRITE, category="ui_map", risk_level="medium"
    ),
    "map_augment_start": CommandDef(
        "sap_map_augment_start", Permission.WRITE, category="ui_map", risk_level="medium"
    ),
    "map_traverse_start": CommandDef(
        "sap_map_traverse_start", Permission.EXECUTE, category="ui_map", risk_level="high"
    ),
    "map_traverse_resume": CommandDef(
        "sap_map_traverse_resume", Permission.EXECUTE, category="ui_map", risk_level="high"
    ),
    "plan_preview": CommandDef(
        "sap_plan_preview", Permission.READ, category="planning", risk_level="low"
    ),
    "plan_approve": CommandDef(
        "sap_plan_approve", Permission.ADMIN, category="planning", risk_level="high"
    ),
    "plan_execute": CommandDef(
        "sap_plan_execute", Permission.WRITE, category="planning", risk_level="high"
    ),
    "job_status": CommandDef(
        "sap_job_status", Permission.READ, category="jobs", risk_level="low"
    ),
    "job_reconcile": CommandDef(
        "sap_job_reconcile", Permission.ADMIN, category="jobs", risk_level="high"
    ),
    "inspect": CommandDef("sap_inspect_element", Permission.READ),
    "scripting_query": CommandDef("sap_scripting_query", Permission.READ),
    "api_object_action": CommandDef(
        "sap_api_object_action",
        Permission.ADMIN,
        category="ui_action",
        risk_level="high",
    ),
    "api_live_probe": CommandDef(
        "sap_api_live_probe",
        Permission.ADMIN,
        expose_cli=False,
        expose_mcp=False,
        category="canary",
        risk_level="high",
    ),
    "event_subscribe": CommandDef(
        "sap_event_subscribe", Permission.ADMIN, category="events", risk_level="high"
    ),
    "event_poll": CommandDef(
        "sap_event_poll", Permission.ADMIN, category="events", risk_level="high"
    ),
    "event_unsubscribe": CommandDef(
        "sap_event_unsubscribe", Permission.ADMIN, category="events", risk_level="high"
    ),
    "event_list": CommandDef(
        "sap_event_list", Permission.ADMIN, category="events", risk_level="high"
    ),
    "ui_action": CommandDef("sap_ui_action", Permission.WRITE),
    "combo_entries": CommandDef("sap_combo_entries", Permission.READ),
    "run": CommandDef("sap_run", Permission.EXECUTE),
    "set": CommandDef("sap_set_field", Permission.WRITE),
    "get": CommandDef("sap_get_field", Permission.READ),
    "press": CommandDef("sap_press", Permission.EXECUTE),
    "vkey": CommandDef("sap_send_vkey", Permission.EXECUTE, {"vkey": "key"}),
    "status": CommandDef("sap_get_status", Permission.READ),
    "table": CommandDef("sap_table_read", Permission.READ),
    "command": CommandDef("sap_command", Permission.EXECUTE),
    "modal": CommandDef("sap_modal", Permission.READ),
    "ontology_query": CommandDef("sap_ontology_query", Permission.READ),
    "transcript_query": CommandDef("sap_transcript_query", Permission.READ),
    "transcript_plan": CommandDef("sap_transcript_plan", Permission.READ),
    "learn": CommandDef("sap_learn", Permission.ADMIN),
    "record_step": CommandDef("sap_macro_record_step", Permission.WRITE),
    "macro_control": CommandDef("sap_macro_control", Permission.ADMIN),
    "macro_play": CommandDef("sap_macro_play", Permission.EXECUTE),
    "macro_list": CommandDef("sap_macro_list", Permission.READ),
    "macro_show": CommandDef("sap_macro_show", Permission.READ),
    "macro_delete": CommandDef("sap_macro_delete", Permission.ADMIN),
    "table_select": CommandDef("sap_table_select", Permission.EXECUTE),
    "table_doubleclick": CommandDef("sap_table_doubleclick", Permission.EXECUTE),
    "process_list": CommandDef("sap_process_list", Permission.READ),
    "process_show": CommandDef("sap_process_show", Permission.READ),
    "process_create": CommandDef("sap_process_create", Permission.ADMIN),
    "process_delete": CommandDef("sap_process_delete", Permission.ADMIN),
    "process_start": CommandDef("sap_process_start", Permission.EXECUTE),
    "process_step": CommandDef("sap_process_step", Permission.EXECUTE),
    "process_status": CommandDef("sap_process_status", Permission.READ),
    "annotate": CommandDef("sap_annotate_field", Permission.WRITE),
    "annotate_dependency": CommandDef("sap_annotate_dependency", Permission.WRITE),
    "fill": CommandDef("sap_fill_fields", Permission.WRITE),
    "tree": CommandDef("sap_tree_read", Permission.READ),
    "tree_select": CommandDef("sap_tree_select", Permission.EXECUTE),
    "tree_doubleclick": CommandDef("sap_tree_doubleclick", Permission.EXECUTE),
    "tree_expand": CommandDef("sap_tree_expand", Permission.EXECUTE),
    "tree_collapse": CommandDef("sap_tree_collapse", Permission.EXECUTE),
    "tree_press": CommandDef("sap_tree_press", Permission.EXECUTE),
    "ontology_ask": CommandDef(
        "sap_ontology_ask",
        Permission.READ,
        category="ontology",
        risk_level="low",
    ),
    "ontology_relationships": CommandDef("sap_ontology_relationships", Permission.READ),
    "field_dependencies": CommandDef("sap_field_dependencies", Permission.READ),
    "document_flow": CommandDef("sap_document_flow", Permission.READ),
    "process_path": CommandDef("sap_process_path", Permission.READ),
    "ontology_rebuild": CommandDef(
        "sap_ontology_rebuild",
        Permission.ADMIN,
        category="ontology",
        risk_level="high",
    ),
    "capture": CommandDef(
        "sap_capture",
        Permission.READ,
        category="capture",
        risk_level="low",
    ),
    "capture_list": CommandDef(
        "sap_capture_list",
        Permission.READ,
        category="capture",
        risk_level="low",
    ),
    "recipe_save": CommandDef("sap_recipe_save", Permission.WRITE),
    "recipe_search": CommandDef("sap_recipe_search", Permission.READ),
    "recipe_show": CommandDef(
        "sap_recipe_show", Permission.READ, category="recipe", risk_level="low"
    ),
    "recipe_list": CommandDef("sap_recipe_list", Permission.READ),
    "recipe_delete": CommandDef("sap_recipe_delete", Permission.ADMIN),
    "recipe_success": CommandDef(
        "sap_recipe_success", Permission.WRITE, category="recipe", risk_level="medium"
    ),
    "recipe_query": CommandDef(
        "sap_recipe_query", Permission.READ, category="recipe", risk_level="low"
    ),
    "recipe_pending": CommandDef(
        "sap_recipe_pending", Permission.READ, category="recipe", risk_level="low"
    ),
    "recipe_failure": CommandDef(
        "sap_recipe_failure", Permission.WRITE, category="recipe", risk_level="medium"
    ),
    "recipe_enable": CommandDef(
        "sap_recipe_enable", Permission.ADMIN, category="recipe", risk_level="high"
    ),
    "recipe_version_up": CommandDef(
        "sap_recipe_version_up",
        Permission.WRITE,
        category="recipe",
        risk_level="medium",
    ),
    "recipe_history": CommandDef(
        "sap_recipe_history", Permission.READ, category="recipe", risk_level="low"
    ),
    "recipe_chain": CommandDef(
        "sap_recipe_chain_search", Permission.READ, category="recipe", risk_level="low"
    ),
    "recipe_replay_plan": CommandDef(
        "sap_recipe_replay_plan",
        Permission.READ,
        category="recipe",
        risk_level="low",
    ),
    "recipe_replay_execute": CommandDef(
        "sap_recipe_replay_execute",
        Permission.WRITE,
        category="recipe",
        risk_level="high",
    ),
    "session_start": CommandDef(
        "sap_session_start",
        Permission.WRITE,
        expose_cli=True,
        category="session",
        risk_level="medium",
    ),
    "session_end": CommandDef(
        "sap_session_end",
        Permission.WRITE,
        expose_cli=True,
        category="session",
        risk_level="medium",
    ),
    "session_list": CommandDef(
        "sap_session_list",
        Permission.READ,
        expose_cli=True,
        category="session",
        risk_level="low",
    ),
    "session_show": CommandDef(
        "sap_session_show",
        Permission.READ,
        expose_cli=True,
        category="session",
        risk_level="low",
    ),
    "session_candidates": CommandDef(
        "sap_session_candidates",
        Permission.READ,
        expose_cli=True,
        category="session",
        risk_level="low",
    ),
    "session_approve": CommandDef(
        "sap_session_approve",
        Permission.ADMIN,
        expose_cli=True,
        category="session",
        risk_level="high",
    ),
    "session_mark": CommandDef(
        "sap_session_mark",
        Permission.WRITE,
        expose_cli=True,
        category="session",
        risk_level="low",
    ),
    "session_excel": CommandDef(
        "sap_session_excel",
        Permission.WRITE,
        expose_cli=True,
        category="session",
        risk_level="low",
    ),
    "slots": CommandDef("sap_session_pool", Permission.READ),
    "slot_allocate": CommandDef(
        "sap_session_allocate",
        Permission.ADMIN,
        expose_cli=False,
        category="session",
        risk_level="high",
    ),
    "slot_release": CommandDef(
        "sap_session_release",
        Permission.ADMIN,
        expose_cli=False,
        category="session",
        risk_level="high",
    ),
}

COMMAND_REGISTRY.update(
    {
        definition.command: CommandDef(
            definition.tool_name,
            Permission(definition.permission),
            expose_cli=True,
            expose_mcp=True,
            description=definition.description,
            category="ui_action",
            risk_level=definition.risk_level,
        )
        for definition in get_ui_action_defs()
    }
)

COMMAND_TOOL_MAP: dict[str, str] = {
    command: definition.tool_name for command, definition in COMMAND_REGISTRY.items()
}
MCP_TOOL_TO_COMMAND: dict[str, str] = {
    definition.tool_name: command
    for command, definition in COMMAND_REGISTRY.items()
    if definition.expose_mcp
}
