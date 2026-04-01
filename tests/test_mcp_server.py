"""MCP tool surface visibility tests."""

from __future__ import annotations

from sap_cli.auth.policy import AgentPolicy, Policy, Relation, RelationBinding
from sap_cli.mcp_server import get_visible_tool_names



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
    assert "sap_get_status" in names
    assert "sap_transcript_query" in names
    assert "sap_transcript_plan" in names
    assert "sap_run" not in names
    assert "sap_command" not in names
    assert "sap_set_field" not in names
    assert "sap_connect" not in names



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
