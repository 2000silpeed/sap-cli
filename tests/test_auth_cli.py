"""Auth diagnostic CLI tests."""

from __future__ import annotations

import json

from typer.testing import CliRunner

from sap_cli.cli import app


runner = CliRunner()


def _json_output(result) -> dict:
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def test_auth_status_without_policy_reports_allow_all(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("SAP_CLI_AGENT", raising=False)

    data = _json_output(runner.invoke(app, ["auth", "status"]))

    assert data["policy_loaded"] is False
    assert data["mode"] == "allow_all"
    assert data["active_agent"] is None
    assert data["effective_agent"] is None
    assert data["visible_mcp_tool_count"] > 0


def test_auth_tools_with_viewer_policy_hides_write_tools(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("SAP_CLI_MCP_SURFACE", "full")
    policy_dir = tmp_path / ".sap_cli"
    policy_dir.mkdir()
    (policy_dir / "policy.json").write_text(
        json.dumps(
            {
                "version": 1,
                "agents": {
                    "viewer": {
                        "relations": [
                            {"resource": "*", "relation": "viewer"},
                            {"resource": "tx:*", "relation": "viewer"},
                        ]
                    }
                },
                "active_agent": "viewer",
            }
        ),
        encoding="utf-8",
    )

    data = _json_output(runner.invoke(app, ["auth", "tools"]))

    assert data["policy_loaded"] is True
    assert data["mode"] == "enforcing"
    assert data["effective_agent"] == "viewer"
    assert "sap_recipe_query" in data["visible_mcp_tools"]
    assert "sap_recipe_failure" not in data["visible_mcp_tools"]


def test_auth_check_reports_permission_and_visibility(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    policy_dir = tmp_path / ".sap_cli"
    policy_dir.mkdir()
    (policy_dir / "policy.json").write_text(
        json.dumps(
            {
                "version": 1,
                "agents": {
                    "viewer": {
                        "relations": [
                            {"resource": "*", "relation": "viewer"},
                        ]
                    }
                },
                "active_agent": "viewer",
            }
        ),
        encoding="utf-8",
    )

    data = _json_output(runner.invoke(app, ["auth", "check", "sap_recipe_failure"]))

    assert data["tool"] == "sap_recipe_failure"
    assert data["known_tool"] is True
    assert data["permission"] == "write"
    assert data["visible_on_mcp"] is False
    assert data["allowed"] is False
    assert "권한 없음" in data["reason"]


def test_auth_top_level_aliases_work_without_policy(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("SAP_CLI_AGENT", raising=False)
    monkeypatch.setenv("SAP_CLI_MCP_SURFACE", "full")

    status = _json_output(runner.invoke(app, ["auth-status"]))
    tools = _json_output(runner.invoke(app, ["auth-tools"]))
    check = _json_output(runner.invoke(app, ["auth-check", "sap_recipe_query"]))

    assert status["mode"] == "allow_all"
    assert "sap_recipe_query" in tools["visible_mcp_tools"]
    assert check["allowed"] is True
