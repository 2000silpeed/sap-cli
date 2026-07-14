"""Runtime semantic safety checks for frozen plan steps."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from sap_cli.ui_map.schema import SideEffectClass, UiMap

from .schema import PlanStep


_DESTRUCTIVE = re.compile(r"(?:delete|remove|삭제|폐기)", re.I)
_COMMIT = re.compile(
    r"(?:save|post(?:ing)?|release|approve|commit|저장|전기|승인|릴리즈|확정)",
    re.I,
)
_EXTERNAL = re.compile(r"(?:send|print|export|mail|전송|인쇄|출력|내보내기)", re.I)
_ORDER = {
    SideEffectClass.READ_ONLY: 0,
    SideEffectClass.REVERSIBLE: 1,
    SideEffectClass.NAVIGATION: 2,
    SideEffectClass.EXTERNAL: 3,
    SideEffectClass.COMMIT: 4,
    SideEffectClass.DESTRUCTIVE: 5,
}


@dataclass(frozen=True, slots=True)
class RuntimeSafety:
    side_effect: SideEffectClass
    approval_required: bool
    reason: str = ""
    evidence: tuple[str, ...] = ()


def classify_runtime_step(step: PlanStep, ui_map: UiMap) -> RuntimeSafety:
    """Combine frozen plan metadata with saved target semantics."""

    evidence = _step_evidence(step, ui_map)
    joined = " ".join(evidence)
    inferred = step.side_effect
    reason = ""
    if _DESTRUCTIVE.search(joined):
        inferred = _max_effect(inferred, SideEffectClass.DESTRUCTIVE)
        reason = "destructive_target_semantics"
    elif _COMMIT.search(joined):
        inferred = _max_effect(inferred, SideEffectClass.COMMIT)
        reason = "commit_target_semantics"
    elif _EXTERNAL.search(joined):
        inferred = _max_effect(inferred, SideEffectClass.EXTERNAL)
        reason = "external_target_semantics"

    function_key = str(
        step.target_locator.get("function_code")
        or step.params.get("function_code")
        or ""
    ).upper()
    if function_key in {"SAVE", "POST", "RELEASE", "APPROVE"}:
        inferred = _max_effect(inferred, SideEffectClass.COMMIT)
        reason = "commit_function_code"
    elif function_key in {"DELETE", "REMOVE"}:
        inferred = _max_effect(inferred, SideEffectClass.DESTRUCTIVE)
        reason = "destructive_function_code"

    is_save_vkey = (
        (step.command == "vkey" and str(step.params.get("key", "")) == "11")
        or (
            step.command == "ui_call_method"
            and str(step.params.get("value", "")).casefold() == "sendvkey"
            and list(step.params.get("method_args", [])) == [11]
        )
    )
    if is_save_vkey:
        inferred = _max_effect(inferred, SideEffectClass.COMMIT)
        reason = "save_vkey"
    official_risk = str(
        step.target_locator.get("official_member_risk")
        or step.params.get("official_member_risk")
        or ""
    ).casefold()
    approval = step.approval_required or inferred in {
        SideEffectClass.COMMIT,
        SideEffectClass.DESTRUCTIVE,
        SideEffectClass.EXTERNAL,
    } or official_risk in {"high", "critical"}
    if official_risk in {"high", "critical"} and not reason:
        reason = "official_member_risk"
    return RuntimeSafety(
        side_effect=inferred,
        approval_required=approval,
        reason=reason,
        evidence=evidence,
    )


def runtime_safety_errors(step: PlanStep, ui_map: UiMap) -> list[str]:
    safety = classify_runtime_step(step, ui_map)
    errors: list[str] = []
    if _ORDER[safety.side_effect] > _ORDER[step.side_effect]:
        errors.append(f"step {step.step_id}: runtime side-effect escalation")
    if safety.approval_required and not step.approval_required:
        errors.append(f"step {step.step_id}: runtime approval required")
    return errors


def _step_evidence(step: PlanStep, ui_map: UiMap) -> tuple[str, ...]:
    values: list[Any] = [
        step.command,
        step.tool,
        step.target_locator.get("text"),
        step.target_locator.get("name"),
        step.target_locator.get("tooltip"),
        step.target_locator.get("icon_name"),
        step.target_locator.get("function_code"),
    ]
    transition = next(
        (
            item
            for item in ui_map.transitions
            if item.transition_id == step.map_transition_id
        ),
        None,
    )
    if transition is not None:
        state = ui_map.states.get(transition.from_state_id)
        if state is not None:
            component = next(
                (
                    item
                    for item in state.components
                    if item.logical_id == step.target_logical_id
                ),
                None,
            )
            if component is not None:
                values.extend(
                    (
                        component.text,
                        component.name,
                        component.tooltip,
                        component.icon_name,
                        component.static_properties.get("function_code"),
                    )
                )
    return tuple(dict.fromkeys(str(value).strip() for value in values if value))


def _max_effect(
    left: SideEffectClass, right: SideEffectClass
) -> SideEffectClass:
    return left if _ORDER[left] >= _ORDER[right] else right


__all__ = [
    "RuntimeSafety",
    "classify_runtime_step",
    "runtime_safety_errors",
]
