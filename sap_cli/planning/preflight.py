"""Pure, side-effect-free validation for compiled execution plans."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from sap_cli.dispatchers.registry import COMMAND_REGISTRY
from sap_cli.planning.schema import ExecutionPlan, PlanStep
from sap_cli.ui_map.schema import SideEffectClass

_TOKEN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_.-]*)\}")
_DANGEROUS = {
    SideEffectClass.COMMIT,
    SideEffectClass.DESTRUCTIVE,
    SideEffectClass.EXTERNAL,
}
_REDACTED = "***REDACTED***"


class PreflightStep(BaseModel):
    """Registry-resolved preview step with safe-to-serialize parameters."""

    model_config = ConfigDict(frozen=True)
    step_id: str
    order: int
    command: str
    tool: str
    params: dict[str, Any] = Field(default_factory=dict)
    expected_from_fingerprint: str = ""
    expected_to_fingerprint: str = ""
    map_transition_id: str = ""
    side_effect: SideEffectClass = SideEffectClass.NAVIGATION
    risk: str = "medium"
    approval_required: bool = False
    authorization_allowed: bool | None = None
    authorization_requires_approval: bool = False
    authorization_resource: str = ""
    authorization_risk: dict[str, str] = Field(default_factory=dict)


class PreflightResult(BaseModel):
    """Serializable outcome which never contains sensitive raw values."""

    model_config = ConfigDict(frozen=True)
    valid: bool
    plan_hash: str
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    unresolved_inputs: list[str] = Field(default_factory=list)
    requires_approval: bool = False
    required_approvals: list[str] = Field(default_factory=list)
    resolved_steps: list[PreflightStep] = Field(default_factory=list)


AuthorizationHook = Callable[[str, str, dict[str, Any], PlanStep], Any]


def preflight_plan(
    plan: ExecutionPlan,
    *,
    command_registry: Mapping[str, Any] | None = None,
    authorization_hook: AuthorizationHook | None = None,
) -> PreflightResult:
    """Resolve and validate a plan without executing SAP or mutating it.

    The optional hook sees raw resolved values so a later central authorization
    integration can inspect the real TCode and arguments.  Hook messages and
    exceptions are never copied to the result because they may echo secrets.
    """

    registry = command_registry if command_registry is not None else COMMAND_REGISTRY
    errors: list[str] = []
    previews: list[PreflightStep] = []
    approvals: list[str] = []
    if not plan.verify_hash():
        errors.append("plan hash verification failed")

    unresolved = sorted(set(plan.unresolved_inputs))
    errors.extend(f"unresolved plan input: {name}" for name in unresolved)
    values: dict[str, Any] = {}
    sensitive: set[str] = set()
    for name, variable in plan.variables.items():
        if variable.sensitive:
            sensitive.add(name)
        if variable.required and (not variable.resolved or variable.value is None):
            errors.append(f"required plan variable is unresolved: {name}")
        elif variable.resolved:
            values[name] = variable.value

    for step in plan.steps:
        command, tool, registry_error = _resolve_registry_pair(
            step.command, step.tool, registry
        )
        if registry_error:
            errors.append(f"step {step.step_id}: {registry_error}")
        step_errors: list[str] = []
        raw_params = _substitute(
            step.params, values, sensitive, redact=False, errors=step_errors
        )
        safe_params = _substitute(
            step.params, values, sensitive, redact=True, errors=[]
        )
        errors.extend(f"step {step.step_id}: {error}" for error in step_errors)

        if step.side_effect in _DANGEROUS:
            if not step.approval_required:
                errors.append(f"step {step.step_id}: dangerous action requires approval")
            else:
                approvals.append(step.step_id)

        allowed: bool | None = None
        hook_approval = False
        authorization_resource = ""
        authorization_risk: dict[str, str] = {}
        if authorization_hook is not None and not registry_error and not step_errors:
            try:
                (
                    allowed,
                    hook_approval,
                    authorization_resource,
                    authorization_risk,
                ) = _authorization_summary(
                    authorization_hook(command, tool, raw_params, step)
                )
            except Exception:
                allowed = False
                errors.append(f"step {step.step_id}: authorization hook failed")
            if allowed is False:
                errors.append(f"step {step.step_id}: authorization denied")
            if hook_approval:
                approvals.append(step.step_id)

        previews.append(
            PreflightStep(
                step_id=step.step_id,
                order=step.order,
                command=command,
                tool=tool,
                params=safe_params,
                expected_from_fingerprint=step.expected_from_fingerprint,
                expected_to_fingerprint=step.expected_to_fingerprint,
                map_transition_id=step.map_transition_id,
                side_effect=step.side_effect,
                risk=step.risk,
                approval_required=step.approval_required or hook_approval,
                authorization_allowed=allowed,
                authorization_requires_approval=hook_approval,
                authorization_resource=authorization_resource,
                authorization_risk=authorization_risk,
            )
        )

    approvals = list(dict.fromkeys(approvals))
    return PreflightResult(
        valid=not errors,
        plan_hash=plan.plan_hash,
        errors=errors,
        unresolved_inputs=unresolved,
        requires_approval=bool(approvals),
        required_approvals=approvals,
        resolved_steps=previews,
    )


def strict_substitute(
    value: Any,
    variables: Mapping[str, Any],
    *,
    sensitive_names: set[str] | None = None,
    redact_sensitive: bool = True,
) -> Any:
    """Resolve every nested ``${name}`` or raise a value-free error."""

    errors: list[str] = []
    result = _substitute(
        value,
        variables,
        sensitive_names or set(),
        redact=redact_sensitive,
        errors=errors,
    )
    if errors:
        raise ValueError("; ".join(errors))
    return result


def _resolve_registry_pair(
    command: str, tool: str, registry: Mapping[str, Any]
) -> tuple[str, str, str]:
    if command:
        definition = registry.get(command)
        if definition is None:
            return command, tool, f"unknown command: {command}"
        expected = _tool_name(definition)
        if tool and tool != expected:
            return command, tool, "command/tool registry mismatch"
        return command, expected, ""
    matches = [name for name, item in registry.items() if _tool_name(item) == tool]
    if len(matches) != 1:
        return command, tool, f"unknown or ambiguous tool: {tool}"
    return matches[0], tool, ""


def _tool_name(definition: Any) -> str:
    if isinstance(definition, str):
        return definition
    if isinstance(definition, Mapping):
        return str(definition.get("tool_name", ""))
    return str(getattr(definition, "tool_name", ""))


def _substitute(
    value: Any,
    variables: Mapping[str, Any],
    sensitive_names: set[str],
    *,
    redact: bool,
    errors: list[str],
) -> Any:
    if isinstance(value, dict):
        return {
            key: _substitute(item, variables, sensitive_names, redact=redact, errors=errors)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            _substitute(item, variables, sensitive_names, redact=redact, errors=errors)
            for item in value
        ]
    if isinstance(value, tuple):
        return tuple(
            _substitute(item, variables, sensitive_names, redact=redact, errors=errors)
            for item in value
        )
    if not isinstance(value, str):
        return value
    matches = list(_TOKEN.finditer(value))
    if not matches:
        return value
    if len(matches) == 1 and matches[0].span() == (0, len(value)):
        name = matches[0].group(1)
        if name not in variables:
            errors.append(f"unresolved variable: {name}")
            return value
        resolved = variables[name]
        if _contains_token(resolved):
            errors.append(f"resolved variable contains an unresolved token: {name}")
            return value
        return _REDACTED if redact and name in sensitive_names else resolved
    rendered = value
    for match in matches:
        name = match.group(1)
        if name not in variables:
            errors.append(f"unresolved variable: {name}")
            continue
        resolved = variables[name]
        if _contains_token(resolved):
            errors.append(f"resolved variable contains an unresolved token: {name}")
            continue
        replacement = _REDACTED if redact and name in sensitive_names else str(resolved)
        rendered = rendered.replace(match.group(0), replacement)
    return rendered


def _contains_token(value: Any) -> bool:
    if isinstance(value, str):
        return bool(_TOKEN.search(value))
    if isinstance(value, Mapping):
        return any(_contains_token(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_contains_token(item) for item in value)
    return False


def _authorization_summary(
    decision: Any,
) -> tuple[bool, bool, str, dict[str, str]]:
    if isinstance(decision, bool):
        return decision, False, "", {}
    if isinstance(decision, Mapping):
        return (
            bool(decision.get("allowed", decision.get("authorized", False))),
            bool(
                decision.get(
                    "approval_required", decision.get("requires_approval", False)
                )
            ),
            str(decision.get("resource", "")),
            {
                str(key): str(value)
                for key, value in dict(decision.get("risk_metadata") or {}).items()
            },
        )
    return (
        bool(getattr(decision, "allowed", getattr(decision, "authorized", False))),
        bool(
            getattr(
                decision,
                "approval_required",
                getattr(decision, "requires_approval", False),
            )
        ),
        str(getattr(decision, "resource", "")),
        {
            str(key): str(value)
            for key, value in dict(getattr(decision, "risk_metadata", {}) or {}).items()
        },
    )
