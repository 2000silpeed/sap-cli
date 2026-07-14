"""MCP tool surface visibility tests."""

from __future__ import annotations

from sap_cli.auth.policy import AgentPolicy, Policy, Relation, RelationBinding
from sap_cli.dispatchers.registry import COMMAND_REGISTRY
from sap_cli.mcp_server import TOOL_DEFINITIONS, get_visible_tool_names
from sap_cli.scripting_ontology import COMPONENT_CAPABILITIES
from sap_cli.ui_action_catalog import get_ui_action_defs



def _make_policy(agent_name: str, relations: list[tuple[str, Relation]]) -> Policy:
    return Policy(
        agents={
            agent_name: AgentPolicy(
                relations=[
                    RelationBinding(resource=resource, relation=relation)
                    for resource, relation in relations
                ]
            )
        },
        active_agent=agent_name,
    )



def test_verifier_profile_gets_readonly_surface(monkeypatch):
    policy = _make_policy(
        "verifier",
        [("tx:*", Relation.VIEWER)],
    )
    monkeypatch.setattr("sap_cli.mcp_server.load_policy", lambda: policy)

    names = set(get_visible_tool_names())

    assert "sap_session" in names
    assert "sap_screen" in names
    assert "sap_modal" in names
    assert "sap_combo_entries" in names
    assert "sap_get_status" in names
    assert "sap_transcript_query" in names
    assert "sap_transcript_plan" in names
    assert "sap_run" not in names
    assert "sap_command" not in names
    assert "sap_set_field" not in names
    assert "sap_connect" not in names


def test_mcp_tool_definitions_cover_exposed_registry():
    defined = {definition["name"] for definition in TOOL_DEFINITIONS}
    expected = {
        command.tool_name
        for command in COMMAND_REGISTRY.values()
        if command.expose_mcp
    }

    assert expected - defined == set()


def test_generated_ui_action_tools_are_exposed():
    defined = {definition["name"] for definition in TOOL_DEFINITIONS}
    generated = {definition.tool_name for definition in get_ui_action_defs()}

    assert "sap_ui_append_text" in defined
    assert "sap_ui_get_property" in defined
    assert "sap_ui_select_toolbar_menu_item" in defined
    assert generated <= defined


def test_generated_ui_action_catalog_covers_capability_actions():
    catalog_actions = {definition.action for definition in get_ui_action_defs()}
    capability_actions = set()
    for capability in COMPONENT_CAPABILITIES.values():
        capability_actions.update(capability.get("actions", []))
        for subtype in capability.get("subtypes", {}).values():
            capability_actions.update(subtype.get("actions", []))

    assert capability_actions <= catalog_actions



def test_operator_profile_gets_execute_surface_without_admin(monkeypatch):
    policy = _make_policy(
        "operator",
        [("tx:*", Relation.OPERATOR)],
    )
    monkeypatch.setattr("sap_cli.mcp_server.load_policy", lambda: policy)

    names = set(get_visible_tool_names())

    assert "sap_run" in names
    assert "sap_command" in names
    assert "sap_set_field" in names
    assert "sap_press" in names
    assert "sap_send_vkey" in names
    assert "sap_macro_play" in names
    assert "sap_transcript_query" in names
    assert "sap_transcript_plan" in names
    assert "sap_connect" not in names
    assert "sap_learn" not in names
