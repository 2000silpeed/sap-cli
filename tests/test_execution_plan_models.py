import pytest
from pydantic import ValidationError

from sap_cli.planning import ExecutionPlan, PlanStep, PlanVariable
from sap_cli.ui_map import SideEffectClass


def _step(**updates):
    values = {
        "step_id": "set-material",
        "order": 1,
        "tool": "sap_set",
        "params": {"element_id": "wnd[0]/usr/ctxtMATNR", "value": "${material}"},
        "map_transition_id": "tr-1",
        "map_version": 3,
    }
    values.update(updates)
    return PlanStep(**values)


def _plan(**updates):
    values = {
        "plan_id": "plan-1",
        "original_request": "자재 100을 조회해줘",
        "tcode": "mm03",
        "map_id": "map-mm03",
        "map_scope": {"system_id": "DEV", "client": "100", "language": "KO"},
        "map_version": 3,
        "map_fingerprint": "map-fp",
        "start_state_id": "start-fp",
        "goal_component_id": "material-description",
        "variables": {"material": {"value": "100", "source": "user"}},
        "steps": [_step()],
    }
    values.update(updates)
    return ExecutionPlan(**values)


def test_plan_round_trip_and_normalizes_tcode():
    plan = _plan()
    restored = ExecutionPlan.model_validate_json(plan.model_dump_json())
    assert restored == plan
    assert restored.tcode == "MM03"
    assert restored.steps[0].params["value"] == "${material}"


def test_plan_hash_is_deterministic_and_tamper_evident():
    first = _plan(created_at="one")
    second = _plan(created_at="two")
    assert first.plan_hash == second.plan_hash
    assert first.verify_hash()
    payload = first.model_dump()
    payload["goal_component_id"] = "different"
    with pytest.raises(ValidationError, match="plan_hash"):
        ExecutionPlan.model_validate(payload)


@pytest.mark.parametrize(
    "effect", [SideEffectClass.COMMIT, SideEffectClass.DESTRUCTIVE, SideEffectClass.EXTERNAL]
)
def test_dangerous_step_requires_approval(effect):
    with pytest.raises(ValidationError, match="requires approval"):
        _step(side_effect=effect)


def test_retry_is_forbidden_after_commit_boundary():
    commit = _step(
        step_id="save",
        order=1,
        tool="sap_press",
        side_effect=SideEffectClass.COMMIT,
        approval_required=True,
        commit_boundary=True,
        params={},
    )
    after = _step(step_id="next", order=2, retry={"max_attempts": 2})
    with pytest.raises(ValidationError, match="after a commit boundary"):
        _plan(steps=[commit, after])


def test_sensitive_variables_are_redacted_by_default():
    plan = _plan(
        variables={
            "material": PlanVariable(value="secret", source="user", sensitive=True)
        }
    )
    assert plan.model_dump()["variables"]["material"]["value"] == "***REDACTED***"
    revealed = plan.model_dump(context={"reveal_sensitive": True})
    assert revealed["variables"]["material"]["value"] == "secret"
    other = _plan(
        variables={
            "material": PlanVariable(value="different", source="user", sensitive=True)
        }
    )
    assert plan.plan_hash != other.plan_hash


def test_undeclared_variable_reference_is_rejected():
    with pytest.raises(ValidationError, match="undeclared plan variables"):
        _plan(variables={})
