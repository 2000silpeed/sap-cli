"""ReBAC 권한관리 단위 테스트 - COM 불필요."""

import json
import pytest
from pathlib import Path

from sap_cli.auth.policy import (
    AgentPolicy,
    AuditConfig,
    DangerousTransaction,
    DangerousReport,
    Permission,
    Policy,
    Relation,
    RelationBinding,
    TOOL_PERMISSION_MAP,
    load_policy,
    match_resource,
)
from sap_cli.auth.checker import PermissionChecker
from sap_cli.auth.audit import AuditLogger


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------

class TestMatchResource:
    def test_exact_match(self):
        assert match_resource("tx:MM03", "tx:MM03") is True
        assert match_resource("tx:MM03", "tx:VA01") is False

    def test_wildcard(self):
        assert match_resource("tx:MM*", "tx:MM03") is True
        assert match_resource("tx:MM*", "tx:MM01") is True
        assert match_resource("tx:MM*", "tx:VA01") is False

    def test_all_wildcard(self):
        assert match_resource("tx:*", "tx:MM03") is True
        assert match_resource("tx:*", "tx:VA01") is True

    def test_full_wildcard(self):
        assert match_resource("*", "tx:MM03") is True


class TestLoadPolicy:
    def test_load_json(self, tmp_path):
        policy_data = {
            "version": 1,
            "agents": {
                "test_agent": {
                    "description": "테스트",
                    "relations": [
                        {"resource": "tx:*", "relation": "viewer"}
                    ],
                }
            },
            "active_agent": "test_agent",
        }
        path = tmp_path / "policy.json"
        path.write_text(json.dumps(policy_data), encoding="utf-8")

        policy = load_policy(path)
        assert policy is not None
        assert policy.version == 1
        assert "test_agent" in policy.agents

    def test_load_nonexistent(self, tmp_path):
        assert load_policy(tmp_path / "nonexistent.yaml") is None

    def test_no_policy_returns_none(self, tmp_path):
        assert load_policy(tmp_path / "nope.yaml") is None


class TestPolicy:
    def test_get_agent(self):
        policy = Policy(agents={
            "a1": AgentPolicy(description="에이전트1"),
        })
        assert policy.get_agent("a1") is not None
        assert policy.get_agent("a2") is None

    def test_dangerous_tcodes(self):
        policy = Policy(dangerous_transactions=[
            DangerousTransaction(
                tcode="SM30",
                reason="테이블 수정",
                category="table_maintenance",
                risk_level="critical",
                preflight_code="dangerous_table_maintenance",
            ),
        ])
        d = policy.get_dangerous_tcodes()
        assert "SM30" in d
        assert d["SM30"] == "테이블 수정"
        details = policy.get_dangerous_transaction_details()
        assert details["SM30"].category == "table_maintenance"
        assert details["SM30"].preflight_code == "dangerous_table_maintenance"

    def test_dangerous_reports(self):
        policy = Policy(dangerous_reports=[
            DangerousReport(report="RSPARAM", reason="시스템 파라미터 조회"),
        ])
        details = policy.get_dangerous_report_details()
        assert "RSPARAM" in details
        assert details["RSPARAM"].category == "sensitive_report"


# ---------------------------------------------------------------------------
# Checker
# ---------------------------------------------------------------------------

def _make_policy(agent_name: str, relations: list[dict], dangerous: list[dict] | None = None) -> Policy:
    return Policy(
        agents={
            agent_name: AgentPolicy(
                relations=[RelationBinding(**r) for r in relations],
            )
        },
        dangerous_transactions=[DangerousTransaction(**d) for d in (dangerous or [])],
        active_agent=agent_name,
    )


class TestPermissionCheckerNoPolicy:
    def test_no_policy_allows_all(self):
        checker = PermissionChecker(None)
        assert checker.is_enforcing is False
        result = checker.check_tool("sap_run", {"tcode": "SM30"})
        assert result.allowed is True

    def test_no_approval_needed(self):
        checker = PermissionChecker(None)
        assert checker.requires_approval("sap_run", {"tcode": "SM30"}) is None


class TestPermissionCheckerViewer:
    @pytest.fixture
    def checker(self):
        policy = _make_policy("readonly", [
            {"resource": "tx:*", "relation": "viewer"},
        ])
        return PermissionChecker(policy)

    def test_read_allowed(self, checker):
        assert checker.check_tool("sap_session").allowed is True
        assert checker.check_tool("sap_screen").allowed is True
        assert checker.check_tool("sap_get_field", {"element_id": "x"}).allowed is True
        assert checker.check_tool("sap_table_read", {"element_id": "x"}).allowed is True
        assert checker.check_tool("sap_transcript_query").allowed is True
        assert checker.check_tool("sap_transcript_plan").allowed is True

    def test_write_denied(self, checker):
        assert checker.check_tool("sap_set_field", {"element_id": "x", "value": "v"}).allowed is False

    def test_execute_denied(self, checker):
        assert checker.check_tool("sap_run", {"tcode": "MM03"}).allowed is False
        assert checker.check_tool("sap_command", {"cmd": "/nMM03"}).allowed is False

    def test_admin_denied(self, checker):
        assert checker.check_tool("sap_connect").allowed is False


class TestPermissionCheckerOperator:
    @pytest.fixture
    def checker(self):
        policy = _make_policy("standard", [
            {"resource": "tx:*", "relation": "operator"},
        ])
        return PermissionChecker(policy)

    def test_read_allowed(self, checker):
        assert checker.check_tool("sap_session").allowed is True

    def test_write_allowed(self, checker):
        assert checker.check_tool("sap_set_field", {"element_id": "x", "value": "v"}).allowed is True

    def test_execute_allowed(self, checker):
        assert checker.check_tool("sap_run", {"tcode": "MM03"}).allowed is True
        assert checker.check_tool("sap_command", {"cmd": "/nMM03"}).allowed is True

    def test_admin_denied(self, checker):
        assert checker.check_tool("sap_connect").allowed is False


class TestPermissionCheckerModuleScope:
    @pytest.fixture
    def checker(self):
        policy = _make_policy("mm_only", [
            {"resource": "tx:MM*", "relation": "operator"},
            {"resource": "tx:*", "relation": "viewer"},
        ])
        return PermissionChecker(policy)

    def test_mm_execute_allowed(self, checker):
        assert checker.check_tool("sap_run", {"tcode": "MM03"}).allowed is True

    def test_va_execute_denied(self, checker):
        assert checker.check_tool("sap_run", {"tcode": "VA01"}).allowed is False

    def test_va_read_allowed(self, checker):
        assert checker.check_tool("sap_session").allowed is True


class TestDangerousTransactions:
    @pytest.fixture
    def checker(self):
        policy = _make_policy(
            "standard",
            [{"resource": "tx:*", "relation": "operator"}],
            dangerous=[{"tcode": "SM30", "reason": "테이블 직접 수정"}],
        )
        return PermissionChecker(policy)

    def test_dangerous_tcode_blocked(self, checker):
        result = checker.check_transaction("SM30")
        assert result.allowed is False
        assert "위험" in result.reason

    def test_safe_tcode_allowed(self, checker):
        result = checker.check_transaction("MM03")
        assert result.allowed is True

    def test_requires_approval(self, checker):
        reason = checker.requires_approval("sap_run", {"tcode": "SM30"})
        assert reason is not None
        assert "테이블" in reason
        assert "sensitive_transaction" in reason

    def test_command_requires_approval(self, checker):
        reason = checker.requires_approval("sap_command", {"cmd": "/nSM30"})
        assert reason is not None
        assert "테이블" in reason

    def test_risk_metadata_for_dangerous_tcode(self, checker):
        metadata = checker.risk_metadata("sap_run", {"tcode": "SM30"})
        assert metadata["type"] == "transaction"
        assert metadata["id"] == "SM30"
        assert metadata["category"] == "sensitive_transaction"
        assert metadata["auth_action"] == "approval_required"
        assert metadata["preflight_code"] == "dangerous_transaction"

    def test_deny_auth_action_blocks_tool(self):
        policy = _make_policy(
            "standard",
            [{"resource": "tx:*", "relation": "operator"}],
            dangerous=[{"tcode": "SM30", "reason": "테이블 직접 수정", "auth_action": "deny"}],
        )
        checker = PermissionChecker(policy)

        result = checker.check_tool("sap_run", {"tcode": "SM30"})

        assert result.allowed is False
        assert result.risk_metadata["auth_action"] == "deny"

    def test_requires_approval_for_dangerous_report(self):
        policy = Policy(
            agents={
                "standard": AgentPolicy(
                    relations=[RelationBinding(resource="tx:*", relation="operator")],
                )
            },
            dangerous_reports=[
                DangerousReport(
                    report="RSPARAM",
                    reason="시스템 파라미터 조회",
                    category="system_configuration_report",
                    risk_level="medium",
                    preflight_code="dangerous_system_report",
                )
            ],
            active_agent="standard",
        )
        checker = PermissionChecker(policy)

        reason = checker.requires_approval("sap_run", {"tcode": "SA38", "report": "RSPARAM"})
        metadata = checker.risk_metadata("sap_run", {"tcode": "SA38", "report": "RSPARAM"})

        assert reason is not None
        assert "시스템 파라미터" in reason
        assert metadata["type"] == "report"
        assert metadata["id"] == "RSPARAM"
        assert metadata["preflight_code"] == "dangerous_system_report"


# ---------------------------------------------------------------------------
# Audit Logger
# ---------------------------------------------------------------------------

class TestAuditLogger:
    def test_log_creates_file(self, tmp_path):
        config = AuditConfig(enabled=True, log_dir=str(tmp_path / "audit"))
        logger = AuditLogger(config)
        logger.log(
            agent="test",
            tool_name="sap_run",
            arguments={"tcode": "MM03"},
            allowed=True,
            permission="execute",
            resource="tx:MM03",
        )
        files = list((tmp_path / "audit").glob("*.jsonl"))
        assert len(files) == 1
        lines = files[0].read_text().strip().split("\n")
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["agent"] == "test"
        assert entry["tool"] == "sap_run"
        assert entry["allowed"] is True

    def test_disabled_logger_no_file(self, tmp_path):
        config = AuditConfig(enabled=False, log_dir=str(tmp_path / "audit"))
        logger = AuditLogger(config)
        logger.log(agent="test", tool_name="sap_run", arguments={}, allowed=True)
        assert not (tmp_path / "audit").exists()

    def test_sensitive_args_filtered(self, tmp_path):
        config = AuditConfig(enabled=True, log_dir=str(tmp_path / "audit"))
        logger = AuditLogger(config)
        logger.log(
            agent="test",
            tool_name="sap_connect",
            arguments={"password": "secret123", "host": "sap.company.com"},
            allowed=True,
        )
        files = list((tmp_path / "audit").glob("*.jsonl"))
        entry = json.loads(files[0].read_text().strip())
        assert "password" not in entry["arguments"]
        assert entry["arguments"]["host"] == "sap.company.com"


class TestToolSurfaceVisibility:
    def test_readonly_surface_hides_mutating_tools(self):
        checker = PermissionChecker(_make_policy("readonly", [
            {"resource": "tx:*", "relation": "viewer"},
        ]))

        assert checker.can_access_tool_surface("sap_session") is True
        assert checker.can_access_tool_surface("sap_modal") is True
        assert checker.can_access_tool_surface("sap_run") is False
        assert checker.can_access_tool_surface("sap_command") is False
        assert checker.can_access_tool_surface("sap_set_field") is False

    def test_operator_surface_hides_admin_tools(self):
        checker = PermissionChecker(_make_policy("operator", [
            {"resource": "tx:*", "relation": "operator"},
        ]))

        assert checker.can_access_tool_surface("sap_run") is True
        assert checker.can_access_tool_surface("sap_command") is True
        assert checker.can_access_tool_surface("sap_set_field") is True
        assert checker.can_access_tool_surface("sap_connect") is False
        assert checker.can_access_tool_surface("sap_learn") is False

    def test_module_operator_still_sees_transaction_surface(self):
        checker = PermissionChecker(_make_policy("mm_only", [
            {"resource": "tx:MM*", "relation": "operator"},
            {"resource": "tx:*", "relation": "viewer"},
        ]))

        assert checker.can_access_tool_surface("sap_run") is True
        assert checker.can_access_tool_surface("sap_command") is True
