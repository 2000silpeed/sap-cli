from __future__ import annotations

from sap_cli.auth.policy import (
    AgentPolicy,
    DangerousTransaction,
    Policy,
    Relation,
    RelationBinding,
)
from sap_cli.auth.preflight import preflight_tool_call


def policy(*, dangerous_action="approval_required") -> Policy:
    return Policy(
        active_agent="planner",
        agents={
            "planner": AgentPolicy(
                relations=[RelationBinding(resource="tx:*", relation=Relation.OPERATOR)]
            )
        },
        dangerous_transactions=[
            DangerousTransaction(
                tcode="SE16N",
                reason="민감 조회",
                auth_action=dangerous_action,
            )
        ],
    )


def test_preflight_is_non_mutating_and_returns_resolved_resource():
    result = preflight_tool_call(
        "sap_run", {"tcode": "MM03"}, policy=policy(), agent_name="planner"
    )

    assert result.allowed is True
    assert result.approval_required is False
    assert result.resource == "tx:MM03"
    assert result.permission == "execute"


def test_dangerous_resolved_tcode_requires_approval():
    result = preflight_tool_call(
        "sap_run", {"tcode": "SE16N"}, policy=policy(), agent_name="planner"
    )

    assert result.allowed is True
    assert result.approval_required is True
    assert result.risk_metadata["preflight_code"] == "dangerous_transaction"


def test_denied_dangerous_tcode_is_rejected():
    result = preflight_tool_call(
        "sap_run",
        {"tcode": "SE16N"},
        policy=policy(dangerous_action="deny"),
        agent_name="planner",
    )

    assert result.allowed is False
    assert result.approval_required is False
    assert result.risk_metadata["auth_action"] == "deny"


def test_unknown_tool_is_rejected_under_policy():
    result = preflight_tool_call(
        "sap_not_registered", {}, policy=policy(), agent_name="planner"
    )

    assert result.allowed is False
    assert "알 수 없는 도구" in result.reason
