import pytest

from sap_cli.planning import ExecutionPlan, PlanStep, preflight_plan, strict_substitute
from sap_cli.ui_map import SideEffectClass

REGISTRY = {
    "set": {"tool_name": "sap_set_field"},
    "press": {"tool_name": "sap_press"},
}


def _plan(*, step=None, variables=None, unresolved_inputs=None):
    return ExecutionPlan(
        plan_id="p1",
        original_request="비밀 값 입력",
        tcode="VA01",
        map_id="m1",
        map_version=1,
        start_state_id="s1",
        goal_state_id="s2",
        variables=variables or {"value": {"value": "100"}},
        unresolved_inputs=unresolved_inputs or [],
        steps=[step or PlanStep(
            step_id="one", order=1, command="set", tool="sap_set_field",
            params={"value": "${value}", "nested": ["x-${value}"]},
        )],
    )


def test_hash_and_nested_substitution():
    result = preflight_plan(_plan(), command_registry=REGISTRY)
    assert result.valid
    assert result.resolved_steps[0].params == {"value": "100", "nested": ["x-100"]}


def test_mutated_hash_is_rejected():
    plan = _plan()
    plan.steps[0].params["value"] = "changed"
    result = preflight_plan(plan, command_registry=REGISTRY)
    assert not result.valid
    assert "plan hash verification failed" in result.errors


def test_strict_substitution_rejects_missing_and_smuggled_tokens():
    with pytest.raises(ValueError, match="missing"):
        strict_substitute({"a": "${missing}"}, {})
    with pytest.raises(ValueError) as error:
        strict_substitute("${known}", {"known": "${other}"})
    assert "known" in str(error.value)
    assert "${other}" not in str(error.value)


def test_registry_mismatch_and_unresolved_input_are_rejected():
    step = PlanStep(step_id="one", order=1, command="set", tool="sap_press", params={})
    result = preflight_plan(
        _plan(step=step, unresolved_inputs=["company_code"]), command_registry=REGISTRY
    )
    assert not result.valid
    assert any("registry mismatch" in error for error in result.errors)
    assert result.unresolved_inputs == ["company_code"]


def test_dangerous_step_requires_approval_in_result():
    step = PlanStep(
        step_id="save", order=1, command="press", tool="sap_press", params={},
        side_effect=SideEffectClass.COMMIT, approval_required=True,
    )
    result = preflight_plan(_plan(step=step), command_registry=REGISTRY)
    assert result.valid
    assert result.requires_approval
    assert result.required_approvals == ["save"]


def test_sensitive_value_is_absent_from_result_and_hook_error():
    secret = "TOP-SECRET-4711"
    observed = []

    def hook(command, tool, params, step):
        observed.append(params["value"])
        raise RuntimeError(f"denied value={params['value']}")

    result = preflight_plan(
        _plan(variables={"value": {"value": secret, "sensitive": True}}),
        command_registry=REGISTRY,
        authorization_hook=hook,
    )
    assert observed == [secret]
    assert not result.valid
    assert secret not in result.model_dump_json()
    assert result.resolved_steps[0].params["value"] == "***REDACTED***"


def test_authorization_approval_required_shape_is_recognized():
    result = preflight_plan(
        _plan(),
        command_registry=REGISTRY,
        authorization_hook=lambda *args: {
            "allowed": True,
            "approval_required": True,
            "resource": "tx:SE16N",
            "risk_metadata": {"risk_level": "high"},
        },
    )

    assert result.valid
    assert result.requires_approval
    assert result.required_approvals == ["one"]
    assert result.resolved_steps[0].approval_required
    assert result.resolved_steps[0].authorization_resource == "tx:SE16N"
    assert result.resolved_steps[0].authorization_risk == {"risk_level": "high"}
