"""Dispatcher / MCP / ReBAC policy command surface consistency tests."""

from __future__ import annotations

import ast
from pathlib import Path

from sap_cli.auth.checker import PermissionChecker
from sap_cli.auth.policy import (
    AgentPolicy,
    Permission,
    Policy,
    RelationBinding,
    TOOL_PERMISSION_MAP,
)
from sap_cli.dispatcher import COMMAND_REGISTRY, MCP_TOOL_TO_COMMAND, resolve_mcp_call
from sap_cli.mcp_server import TOOL_DEFINITIONS, _visible_tool_names_for_checker


# Dispatcher 내부 기능이지만 MCP tool로 직접 노출하지 않는 명령.
# 현재는 CLI/daemon/session에서 가능한 주요 기능을 MCP에도 최대한 노출한다.
DISPATCHER_ONLY_TOOLS: set[str] = set()

# MCP에는 노출하지만 아직 별도 CLI subcommand를 만들지 않은 명령.
# recipe replay/version-up은 CLI에도 노출되었고, slot allocate/release만 MCP 중심으로 유지한다.
MCP_ONLY_COMMANDS = {
    "slot_allocate",
    "slot_release",
}

VALID_RISK_LEVELS = {"low", "medium", "high"}


def _make_policy(agent_name: str, relation: str) -> Policy:
    return Policy(
        agents={
            agent_name: AgentPolicy(
                relations=[
                    RelationBinding(resource="tx:*", relation=relation),
                    RelationBinding(resource="*", relation=relation),
                ],
            )
        },
        active_agent=agent_name,
    )


def _cli_dispatched_commands() -> set[str]:
    """CLI가 실제 dispatcher로 넘기는 command key를 정적으로 수집한다."""
    source = Path("sap_cli/cli.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    commands: set[str] = set()

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Name):
            continue
        if node.func.id not in {"_run_command", "_dispatch_local"}:
            continue
        if not node.args:
            continue
        first_arg = node.args[0]
        if isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str):
            commands.add(first_arg.value)

    return commands


def test_command_metadata_is_valid():
    """CommandDef 노출/위험도 메타데이터는 registry에서 관리되는 단일 기준이다."""
    for command, definition in COMMAND_REGISTRY.items():
        assert definition.tool_name.startswith("sap_"), command
        assert isinstance(definition.expose_cli, bool), command
        assert isinstance(definition.expose_mcp, bool), command
        assert isinstance(definition.description, str), command
        assert definition.category, command
        assert definition.risk_level in VALID_RISK_LEVELS, command


def test_all_registered_tools_have_policy_permission():
    """COMMAND_REGISTRY에 등록된 tool_name은 ReBAC policy map에 반드시 있어야 한다."""
    registry_tools = {definition.tool_name for definition in COMMAND_REGISTRY.values()}

    assert registry_tools <= set(TOOL_PERMISSION_MAP)


def test_registry_permission_matches_policy_permission():
    """CommandDef.permission과 TOOL_PERMISSION_MAP 분류가 drift 없이 같아야 한다."""
    mismatches = {
        command: (definition.permission, TOOL_PERMISSION_MAP.get(definition.tool_name))
        for command, definition in COMMAND_REGISTRY.items()
        if TOOL_PERMISSION_MAP.get(definition.tool_name) != definition.permission
    }

    assert mismatches == {}


def test_mcp_tool_definitions_match_dispatcher_mapping_except_allowlist():
    """MCP schema와 dispatcher MCP mapping은 의도적 비노출 allowlist를 제외하고 일치해야 한다."""
    mcp_definition_names = {definition["name"] for definition in TOOL_DEFINITIONS}
    dispatcher_mcp_names = set(MCP_TOOL_TO_COMMAND)
    registry_hidden_names = {
        definition.tool_name
        for definition in COMMAND_REGISTRY.values()
        if not definition.expose_mcp
    }

    assert registry_hidden_names == DISPATCHER_ONLY_TOOLS
    assert dispatcher_mcp_names - mcp_definition_names == set()
    assert mcp_definition_names - dispatcher_mcp_names == set()


def test_mcp_exposure_metadata_matches_tool_definitions():
    """CommandDef.expose_mcp와 실제 MCP tool definition이 drift 없이 일치해야 한다."""
    mcp_definition_names = {definition["name"] for definition in TOOL_DEFINITIONS}
    exposed_by_registry = {
        definition.tool_name
        for definition in COMMAND_REGISTRY.values()
        if definition.expose_mcp
    }
    hidden_by_registry = {
        definition.tool_name
        for definition in COMMAND_REGISTRY.values()
        if not definition.expose_mcp
    }

    assert exposed_by_registry == mcp_definition_names
    assert hidden_by_registry == DISPATCHER_ONLY_TOOLS
    assert hidden_by_registry.isdisjoint(MCP_TOOL_TO_COMMAND)


def test_hidden_mcp_commands_do_not_resolve_as_mcp_calls():
    """expose_mcp=False인 registry command는 MCP 역해석 경로에서도 숨겨져야 한다."""
    for tool_name in DISPATCHER_ONLY_TOOLS:
        try:
            resolve_mcp_call(tool_name, {})
        except ValueError as exc:
            assert str(exc) == f"Unknown tool: {tool_name}"
        else:
            raise AssertionError(f"{tool_name} resolved despite expose_mcp=False")


def test_cli_exposure_metadata_matches_actual_cli_dispatch_except_allowlist():
    """의도적으로 CLI에 없는 MCP 전용 command는 allowlist와 expose_cli=False로 문서화한다."""
    cli_commands = _cli_dispatched_commands()
    registry_commands = set(COMMAND_REGISTRY)
    cli_exposed_by_registry = {
        command
        for command, definition in COMMAND_REGISTRY.items()
        if definition.expose_cli
    }
    cli_hidden_by_registry = {
        command
        for command, definition in COMMAND_REGISTRY.items()
        if not definition.expose_cli
    }

    assert cli_commands <= registry_commands
    assert registry_commands - cli_commands == MCP_ONLY_COMMANDS
    assert cli_exposed_by_registry == cli_commands
    assert cli_hidden_by_registry == MCP_ONLY_COMMANDS


def test_mcp_tool_definitions_resolve_to_registered_commands():
    """모든 MCP tool definition은 dispatcher command로 역해석 가능해야 한다."""
    for definition in TOOL_DEFINITIONS:
        tool_name = definition["name"]
        command = MCP_TOOL_TO_COMMAND[tool_name]
        assert command in COMMAND_REGISTRY
        assert COMMAND_REGISTRY[command].tool_name == tool_name


def test_rebac_enforcing_exposes_recent_mcp_tools_by_permission_level():
    """정책 enforcing 모드에서 최근 추가 MCP 도구가 permission 분류대로 노출된다."""
    viewer = PermissionChecker(_make_policy("viewer", "viewer"))
    operator = PermissionChecker(_make_policy("operator", "operator"))
    supervisor = PermissionChecker(_make_policy("supervisor", "supervisor"))

    viewer_tools = set(_visible_tool_names_for_checker(viewer))
    operator_tools = set(_visible_tool_names_for_checker(operator))
    supervisor_tools = set(_visible_tool_names_for_checker(supervisor))

    assert "sap_tree_read" in viewer_tools
    assert "sap_recipe_query" in viewer_tools
    assert "sap_recipe_pending" in viewer_tools
    assert "sap_recipe_replay_plan" in viewer_tools
    assert "sap_session_candidates" in viewer_tools

    assert "sap_fill_fields" not in viewer_tools
    assert "sap_fill_fields" in operator_tools
    assert "sap_annotate_field" in operator_tools
    assert "sap_recipe_failure" in operator_tools
    assert "sap_recipe_replay_execute" in operator_tools
    assert "sap_recipe_version_up" in operator_tools

    assert "sap_recipe_enable" not in operator_tools
    assert "sap_session_approve" not in operator_tools
    assert "sap_recipe_enable" in supervisor_tools
    assert "sap_session_approve" in supervisor_tools

    assert TOOL_PERMISSION_MAP["sap_fill_fields"] == Permission.WRITE
    assert TOOL_PERMISSION_MAP["sap_recipe_replay_execute"] == Permission.WRITE
    assert TOOL_PERMISSION_MAP["sap_recipe_enable"] == Permission.ADMIN
