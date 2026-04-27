"""ReBAC 권한 판단 엔진."""

from __future__ import annotations

from typing import Any

from ..command_parser import extract_tcode_from_command
from .policy import (
    AgentPolicy,
    Permission,
    Policy,
    Relation,
    TOOL_PERMISSION_MAP,
    match_resource,
)


class CheckResult:
    __slots__ = ("allowed", "reason", "risk_metadata")

    def __init__(
        self,
        allowed: bool,
        reason: str = "",
        risk_metadata: dict[str, str] | None = None,
    ):
        self.allowed = allowed
        self.reason = reason
        self.risk_metadata = risk_metadata or {}

    def __bool__(self) -> bool:
        return self.allowed


class PermissionChecker:
    """ReBAC 권한 판단기.

    정책이 None이면 전체 허용 (하위 호환).
    """

    def __init__(self, policy: Policy | None = None) -> None:
        self._policy = policy
        self._agent: AgentPolicy | None = None
        self._agent_name: str = ""
        if policy and policy.active_agent:
            self._agent_name = policy.active_agent
            self._agent = policy.get_agent(policy.active_agent)

    @property
    def is_enforcing(self) -> bool:
        return self._policy is not None and self._agent is not None

    def set_agent(self, agent_name: str) -> None:
        if self._policy:
            self._agent_name = agent_name
            self._agent = self._policy.get_agent(agent_name)

    @property
    def agent_name(self) -> str:
        return self._agent_name

    def resolve_resource(self, tool_name: str, arguments: dict[str, Any] | None = None) -> str:
        return self._resolve_resource(tool_name, arguments or {})

    def check_tool(self, tool_name: str, arguments: dict[str, Any] | None = None) -> CheckResult:
        """MCP 도구 호출 권한 체크."""
        if not self.is_enforcing:
            return CheckResult(True, "no_policy")

        permission = TOOL_PERMISSION_MAP.get(tool_name)
        if permission is None:
            return CheckResult(False, f"알 수 없는 도구: {tool_name}")

        deny_risk = next(
            (
                risk
                for risk in self._risk_metadata_entries(tool_name, arguments or {})
                if risk.get("auth_action") == "deny"
            ),
            None,
        )
        if deny_risk:
            return CheckResult(
                False,
                self._format_risk_reason(deny_risk),
                risk_metadata=deny_risk,
            )

        resource = self._resolve_resource(tool_name, arguments or {})
        return self._check_permission(resource, permission)

    def check_transaction(self, tcode: str) -> CheckResult:
        """트랜잭션 실행 권한 체크."""
        if not self.is_enforcing:
            return CheckResult(True, "no_policy")

        resource = f"tx:{tcode.upper()}"

        # 위험 트랜잭션 체크
        dangerous = self._policy.get_dangerous_transaction_details()
        dangerous_entry = dangerous.get(tcode.upper())
        if dangerous_entry:
            metadata = dangerous_entry.to_risk_metadata()
            return CheckResult(
                False,
                self._format_risk_reason(metadata),
                risk_metadata=metadata,
            )

        return self._check_permission(resource, Permission.EXECUTE)

    def requires_approval(self, tool_name: str, arguments: dict[str, Any] | None = None) -> str | None:
        """승인이 필요하면 사유 반환, 아니면 None."""
        if not self.is_enforcing:
            return None

        args = arguments or {}
        approval_risks = [
            risk
            for risk in self._risk_metadata_entries(tool_name, args)
            if risk.get("auth_action") == "approval_required"
        ]
        if approval_risks:
            report_risk = next(
                (risk for risk in approval_risks if risk.get("type") == "report"),
                None,
            )
            return self._format_risk_reason(report_risk or approval_risks[0])

        # 에이전트별 승인 규칙 체크
        if self._agent and self._agent.requires_approval:
            permission = TOOL_PERMISSION_MAP.get(tool_name)
            tcode = self._extract_tcode(tool_name, args)
            if permission and tcode:
                for rule in self._agent.requires_approval:
                    if rule.permission == permission and tcode.upper() in [t.upper() for t in rule.transactions]:
                        return f"에이전트 정책: {self._agent_name}은 {tcode} {permission.value}에 승인 필요"

        return None

    def risk_metadata(self, tool_name: str, arguments: dict[str, Any] | None = None) -> dict[str, str]:
        """권한/preflight 응답에 포함할 구조화 위험 분류를 반환."""
        risk_entries = self._risk_metadata_entries(tool_name, arguments or {})
        if not risk_entries:
            return {}

        deny_risk = next(
            (risk for risk in risk_entries if risk.get("auth_action") == "deny"),
            None,
        )
        if deny_risk:
            return deny_risk

        report_risk = next(
            (risk for risk in risk_entries if risk.get("type") == "report"),
            None,
        )
        if report_risk:
            return report_risk
        return risk_entries[0]

    def _risk_metadata_entries(
        self,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
    ) -> list[dict[str, str]]:
        """Return all dangerous transaction/report risks that match a call.

        Report risks are kept as a first-class match instead of being hidden
        behind a generic SE38/SA38 transaction risk.  Deny risks are still
        handled first by callers so a report argument cannot bypass a denied
        transaction.
        """
        if not self.is_enforcing or not self._policy:
            return []

        args = arguments or {}
        risks: list[dict[str, str]] = []
        tcode = self._extract_tcode(tool_name, args)
        if tcode:
            dangerous = self._policy.get_dangerous_transaction_details().get(tcode.upper())
            if dangerous:
                risks.append(dangerous.to_risk_metadata())

        report = self._extract_report(tool_name, args)
        if report:
            dangerous_report = self._policy.get_dangerous_report_details().get(report.upper())
            if dangerous_report:
                risks.append(dangerous_report.to_risk_metadata())

        return risks

    def can_access_tool_surface(self, tool_name: str) -> bool:
        """현재 에이전트 프로필에 이 도구를 MCP surface에 노출할 수 있는지 판단."""
        if not self.is_enforcing:
            return True

        permission = TOOL_PERMISSION_MAP.get(tool_name)
        if permission is None:
            return False

        if tool_name in {"sap_run", "sap_command"}:
            return self._has_transaction_capability(permission)
        return self._has_general_capability(permission)

    def _check_permission(self, resource: str, permission: Permission) -> CheckResult:
        if not self._agent:
            return CheckResult(False, "에이전트 미설정")

        # 1. 직접 리소스 매칭
        for binding in self._agent.relations:
            if match_resource(binding.resource, resource):
                allowed_perms = self._policy.resolve_relation_permissions(binding.relation)
                if permission in allowed_perms:
                    return CheckResult(True, f"{binding.relation.value} on {binding.resource}")

        # 2. 리소스가 tx: 형태면 와일드카드 탐색
        parts = resource.split(":")
        if len(parts) >= 2 and parts[0] == "tx":
            for binding in self._agent.relations:
                if match_resource(binding.resource, resource):
                    allowed_perms = self._policy.resolve_relation_permissions(binding.relation)
                    if permission in allowed_perms:
                        return CheckResult(True, f"{binding.relation.value} on {binding.resource} (wildcard)")

        # 3. 비-트랜잭션 리소스 (tool:, element:, vkey 등):
        #    에이전트의 가장 높은 권한 수준으로 판단
        if parts[0] != "tx":
            for binding in self._agent.relations:
                allowed_perms = self._policy.resolve_relation_permissions(binding.relation)
                if permission in allowed_perms:
                    return CheckResult(True, f"{binding.relation.value} (general permission)")

        return CheckResult(False, f"권한 없음: {permission.value} on {resource}")

    def _has_general_capability(self, permission: Permission) -> bool:
        if not self._agent or not self._policy:
            return False

        for binding in self._agent.relations:
            allowed_perms = self._policy.resolve_relation_permissions(binding.relation)
            if permission in allowed_perms:
                return True
        return False

    def _has_transaction_capability(self, permission: Permission) -> bool:
        if not self._agent or not self._policy:
            return False

        for binding in self._agent.relations:
            if binding.resource == "*" or binding.resource.startswith("tx:"):
                allowed_perms = self._policy.resolve_relation_permissions(binding.relation)
                if permission in allowed_perms:
                    return True
        return False

    def _resolve_resource(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """도구 + 인자에서 리소스 식별."""
        if tool_name == "sap_run":
            tcode = arguments.get("tcode", "UNKNOWN")
            return f"tx:{tcode.upper()}"
        elif tool_name == "sap_command":
            command = str(arguments.get("cmd", ""))
            tcode = extract_tcode_from_command(command)
            if tcode:
                return f"tx:{tcode.upper()}"
            return f"command:__unparsed__"
        elif tool_name in ("sap_set_field", "sap_get_field", "sap_press"):
            element_id = arguments.get("element_id", "")
            return f"element:{element_id}"
        elif tool_name == "sap_send_vkey":
            return "vkey"
        elif tool_name == "sap_table_read":
            element_id = arguments.get("element_id", "")
            return f"table:{element_id}"
        elif tool_name == "sap_macro_play":
            macro_id = arguments.get("macro_id", "")
            return f"macro:{macro_id}"
        elif tool_name == "sap_ontology_query":
            tcode = arguments.get("tcode", "*")
            return f"ontology:{tcode}"
        return f"tool:{tool_name}"

    def _extract_tcode(self, tool_name: str, arguments: dict[str, Any]) -> str | None:
        if tool_name == "sap_run":
            return arguments.get("tcode")
        if tool_name == "sap_command":
            return extract_tcode_from_command(str(arguments.get("cmd", "")))
        return None

    def _extract_report(self, tool_name: str, arguments: dict[str, Any]) -> str | None:
        if tool_name != "sap_run":
            return None
        report = arguments.get("report") or arguments.get("program")
        return str(report).strip() if report else None

    def _format_risk_reason(self, metadata: dict[str, str]) -> str:
        reason = metadata.get("reason") or metadata.get("category") or "위험 항목"
        target_type = "트랜잭션" if metadata.get("type") == "transaction" else "리포트"
        category = metadata.get("category", "")
        risk_level = metadata.get("risk_level", "")
        suffix = f" [{category}/{risk_level}]" if category or risk_level else ""
        return f"위험 {target_type}: {reason}{suffix}"
