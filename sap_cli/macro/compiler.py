"""Compile saved macros into closed, fully resolved dispatcher steps.

This module is intentionally side-effect free.  It never imports or calls the
central dispatcher; runtime code can preflight the returned canonical commands
and then execute each one through ``dispatch_command``.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, SerializationInfo, field_serializer

from sap_cli.dispatchers.registry import COMMAND_REGISTRY
from sap_cli.macro.schema import Macro, MacroStep, MacroVariable
from sap_cli.macro.variables import normalize_runtime_variables, normalize_variable_name
from sap_cli.ontology.schema import ActionType
from sap_cli.planning.schema import PlanStep
from sap_cli.planning.variables import bind_variables, contains_variable_tokens
from sap_cli.ui_map.schema import SideEffectClass


class MacroCompileError(ValueError):
    """Raised before execution when a macro cannot be compiled safely."""


_SENSITIVE_NAME = re.compile(
    r"(?:password|passwd|passcode|secret|token|credential|api[_-]?key|pin|pwd)",
    re.IGNORECASE,
)
_SEMANTIC_RISK = re.compile(
    r"(?:\bsave\b|\bpost\b|\bdelete\b|\brelease\b|\bsend\b|\bprint\b|"
    r"저장|전기|삭제|릴리스|승인|전송|인쇄)",
    re.IGNORECASE,
)
_DESTRUCTIVE = re.compile(r"(?:\bdelete\b|삭제)", re.IGNORECASE)
_EXTERNAL = re.compile(r"(?:\bprint\b|인쇄)", re.IGNORECASE)
_HIGH_RISK_UI_ACTIONS = {
    "call_method": SideEffectClass.EXTERNAL,
    "set_property": SideEffectClass.COMMIT,
    "close": SideEffectClass.DESTRUCTIVE,
}


def _redact(value: Any, secrets: Sequence[str]) -> Any:
    if isinstance(value, str):
        result = value
        for secret in secrets:
            if secret:
                result = result.replace(secret, "<redacted>")
        return result
    if isinstance(value, dict):
        return {key: _redact(item, secrets) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item, secrets) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact(item, secrets) for item in value)
    return value


class CompiledMacroVariable(BaseModel):
    """Resolved variable whose value is redacted unless explicitly revealed."""

    model_config = ConfigDict(frozen=True)

    value: str
    source: str = "runtime"
    sensitive: bool = False

    @field_serializer("value")
    def _serialize_value(self, value: str, info: SerializationInfo) -> str:
        if self.sensitive and not (info.context or {}).get("reveal_sensitive", False):
            return "<redacted>"
        return value


class CompiledMacroStep(PlanStep):
    """A canonical dispatcher step with macro-specific post-step metadata."""

    macro_step_number: int = Field(default=0, ge=0)
    synthetic: bool = False
    expected_screen: int | None = None
    wait_after: float = Field(default=0.0, ge=0)
    sensitive_values: tuple[str, ...] = Field(default=(), exclude=True, repr=False)

    @field_serializer("params")
    def _serialize_params(self, value: dict[str, Any], info: SerializationInfo) -> dict[str, Any]:
        if (info.context or {}).get("reveal_sensitive", False):
            return value
        return _redact(value, self.sensitive_values)

    def reveal_params(self) -> dict[str, Any]:
        """Return actual resolved arguments for trusted runtime dispatch only."""

        return self.model_dump(
            mode="python", context={"reveal_sensitive": True}
        )["params"]


class CompiledMacro(BaseModel):
    """Side-effect-free compiler output ready for preflight and dispatch."""

    model_config = ConfigDict(frozen=True)

    macro_id: str
    macro_name: str
    variables: dict[str, CompiledMacroVariable] = Field(default_factory=dict)
    used_variables: tuple[str, ...] = ()
    steps: tuple[CompiledMacroStep, ...] = ()


def _explicit_sensitive_names(raw: Any) -> set[str]:
    names: set[str] = set()
    if isinstance(raw, Mapping):
        if raw.get("sensitive") is True:
            name = raw.get("name") or raw.get("key") or raw.get("id") or raw.get("variable")
            if name:
                names.add(normalize_variable_name(str(name)).casefold())
        for key, value in raw.items():
            if isinstance(value, Mapping) and value.get("sensitive") is True:
                names.add(normalize_variable_name(str(key)).casefold())
            names.update(_explicit_sensitive_names(value))
    elif isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, bytearray)):
        for item in raw:
            names.update(_explicit_sensitive_names(item))
    return names


def _effective_variables(
    macro: Macro,
    runtime_variables: Any,
    sensitive_variables: Sequence[str],
) -> tuple[dict[str, str], dict[str, CompiledMacroVariable], tuple[str, ...]]:
    runtime = normalize_runtime_variables(runtime_variables, macro)
    values = {
        variable.name: variable.default
        for variable in macro.variables
        if variable.default != ""
    }
    values.update(runtime)

    required_missing = sorted(
        variable.name
        for variable in macro.variables
        if variable.required and variable.name not in values
    )
    if required_missing:
        raise MacroCompileError(
            "missing required macro variables: " + ", ".join(required_missing)
        )

    explicit_sensitive = _explicit_sensitive_names(runtime_variables)
    explicit_sensitive.update(
        normalize_variable_name(name).casefold() for name in sensitive_variables
    )
    compiled: dict[str, CompiledMacroVariable] = {}
    secrets: list[str] = []
    for name, value in values.items():
        sensitive = bool(_SENSITIVE_NAME.search(name)) or name.casefold() in explicit_sensitive
        source = "runtime" if name in runtime else "default"
        compiled[name] = CompiledMacroVariable(
            value=str(value), source=source, sensitive=sensitive
        )
        if sensitive and str(value):
            secrets.append(str(value))

    return values, compiled, tuple(sorted(set(secrets), key=lambda item: (-len(item), item)))


def _registered(command: str) -> tuple[str, str]:
    definition = COMMAND_REGISTRY.get(command)
    if definition is None:
        raise MacroCompileError(f"canonical dispatcher command is not registered: {command}")
    return command, definition.tool_name


def _row(step: MacroStep) -> int:
    value = step.params.get("row", step.value)
    if value in (None, ""):
        raise MacroCompileError(f"macro step {step.step_number} requires row")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise MacroCompileError(f"macro step {step.step_number} row must be an integer") from exc


def _column(step: MacroStep) -> str:
    value = step.params.get("column", "")
    return "" if value is None else str(value)


def _required_value(step: MacroStep, label: str) -> str:
    if step.value in (None, ""):
        raise MacroCompileError(f"macro step {step.step_number} requires {label}")
    return str(step.value)


def _semantic_blob(step: MacroStep) -> str:
    return " ".join(
        [
            step.description,
            step.target_id,
            "" if step.value is None else str(step.value),
            json.dumps(step.params, ensure_ascii=False, sort_keys=True, default=str),
        ]
    )


def _risk_contract(
    step: MacroStep,
    *,
    ui_action: str = "",
) -> tuple[SideEffectClass, str, bool, bool, list[dict[str, Any]]]:
    if ui_action in _HIGH_RISK_UI_ACTIONS:
        effect = _HIGH_RISK_UI_ACTIONS[ui_action]
        risk = "critical" if ui_action in {"call_method", "close"} else "high"
        return effect, risk, True, True, [{"semantic_risk": ui_action}]

    semantic = _semantic_blob(step)
    if step.action == ActionType.SEND_VKEY:
        try:
            key = int(step.value or step.target_id)
        except (TypeError, ValueError):
            key = -1
        if key == 11:
            return SideEffectClass.COMMIT, "high", True, True, [{"semantic_risk": "save_vkey"}]
        if key == 27:
            return SideEffectClass.EXTERNAL, "high", True, True, [{"semantic_risk": "print_vkey"}]
    if re.search(r"(?:^|/)btn\[11\](?:$|/)", step.target_id, re.IGNORECASE):
        return SideEffectClass.COMMIT, "high", True, True, [{"semantic_risk": "save_button"}]
    if _SEMANTIC_RISK.search(semantic):
        if _DESTRUCTIVE.search(semantic):
            effect, risk = SideEffectClass.DESTRUCTIVE, "critical"
        elif _EXTERNAL.search(semantic):
            effect, risk = SideEffectClass.EXTERNAL, "high"
        else:
            effect, risk = SideEffectClass.COMMIT, "high"
        return effect, risk, True, True, [{"semantic_risk": "business_commit"}]

    if step.action == ActionType.READ_SCREEN:
        return SideEffectClass.READ_ONLY, "low", False, False, []
    if step.action in {
        ActionType.SET_FIELD_VALUE,
        ActionType.SELECT_COMBO_ITEM,
        ActionType.TOGGLE_CHECKBOX,
        ActionType.SELECT_RADIO,
        ActionType.SELECT_TABLE_CELL,
    }:
        return SideEffectClass.REVERSIBLE, "low", False, False, []
    return SideEffectClass.NAVIGATION, "medium", False, False, []


def _canonical_action(step: MacroStep) -> tuple[str, dict[str, Any], str]:
    target = step.target_id
    action = step.action
    ui_action = ""

    if action == ActionType.SET_FIELD_VALUE:
        command, args = "set", {"element_id": target, "value": step.value or ""}
    elif action == ActionType.PRESS_BUTTON:
        command, args = "press", {"element_id": target}
    elif action == ActionType.SEND_VKEY:
        raw = step.value or target
        try:
            key = int(raw)
        except (TypeError, ValueError) as exc:
            raise MacroCompileError(f"macro step {step.step_number} vkey must be an integer") from exc
        command, args = "vkey", {"key": key}
    elif action == ActionType.RUN_TRANSACTION:
        command, args = "run", {"tcode": str(step.value or target).strip().upper()}
    elif action == ActionType.SELECT_TABLE_ROW:
        command, args = "table_select", {"element_id": target, "row": _row(step)}
    elif action == ActionType.SELECT_TABLE_CELL:
        command, args = "ui_set_current_cell", {
            "element_id": target,
            "row": _row(step),
            "column": _column(step),
        }
    elif action == ActionType.SELECT_COMBO_ITEM:
        command, args = "ui_select", {"element_id": target, "value": step.value or ""}
    elif action == ActionType.TOGGLE_CHECKBOX:
        command, args = "ui_set", {"element_id": target, "value": step.value or "false"}
    elif action == ActionType.SELECT_RADIO:
        command, args = "ui_select", {"element_id": target}
    elif action == ActionType.SELECT_TAB:
        command, args = "ui_select", {"element_id": target}
    elif action == ActionType.SELECT_MENU:
        command, args = "ui_select", {"element_id": target}
    elif action == ActionType.READ_SCREEN:
        command, args = "screen", {}
    elif action == ActionType.SELECT_TREE_NODE:
        command, args = "tree_select", {"element_id": target, "node_key": _required_value(step, "node_key")}
    elif action == ActionType.EXPAND_TREE_NODE:
        command, args = "tree_expand", {"element_id": target, "node_key": _required_value(step, "node_key")}
    elif action == ActionType.DOUBLE_CLICK_TREE_NODE:
        command, args = "tree_doubleclick", {"element_id": target, "node_key": _required_value(step, "node_key")}
    elif action == ActionType.COLLAPSE_TREE_NODE:
        command, args = "tree_collapse", {"element_id": target, "node_key": _required_value(step, "node_key")}
    elif action == ActionType.CLICK_TABLE_CELL:
        command, args = "ui_click_cell", {
            "element_id": target,
            "row": _row(step),
            "column": _column(step),
        }
    elif action == ActionType.DOUBLE_CLICK_TABLE_CELL:
        command, args = "table_doubleclick", {
            "element_id": target,
            "row": _row(step),
            "column": _column(step),
        }
    elif action == ActionType.PERFORM_UI_ACTION:
        ui_action = str(
            step.params.get("ui_action")
            or step.params.get("action")
            or step.value
            or ""
        ).strip().lower().replace("-", "_")
        if not ui_action:
            raise MacroCompileError(f"macro step {step.step_number} requires ui_action")
        command = f"ui_{ui_action}"
        args = {"element_id": target}
        for key, value in step.params.items():
            if key not in {"ui_action", "action"}:
                args[key] = value
        if step.value and step.value != ui_action and "value" not in args:
            args["value"] = step.value
    else:  # Defensive even if a future ActionType is accepted by MacroStep.
        raise MacroCompileError(f"unmapped macro ActionType: {action}")

    _registered(command)
    return command, args, ui_action


def _compile_step(
    step: MacroStep,
    *,
    order: int,
    secrets: tuple[str, ...],
    synthetic: bool = False,
) -> CompiledMacroStep:
    command, args, ui_action = _canonical_action(step)
    _, tool = _registered(command)
    effect, risk, approval, commit, effects = _risk_contract(step, ui_action=ui_action)
    return CompiledMacroStep(
        step_id=f"macro:{'tcode' if synthetic else step.step_number}",
        order=order,
        command=command,
        tool=tool,
        target_logical_id=step.target_id,
        target_locator={"sap_id": step.target_id} if step.target_id else {},
        params=args,
        effects=effects,
        side_effect=effect,
        risk=risk,
        approval_required=approval,
        retry={"max_attempts": 1},
        commit_boundary=commit,
        macro_step_number=step.step_number,
        synthetic=synthetic,
        expected_screen=step.expected_screen,
        wait_after=step.wait_after,
        sensitive_values=secrets,
    )


def compile_macro(
    macro: Macro,
    runtime_variables: Any = None,
    *,
    sensitive_variables: Sequence[str] = (),
) -> CompiledMacro:
    """Strictly resolve and compile a macro without executing SAP actions."""

    values, compiled_variables, secrets = _effective_variables(
        macro, runtime_variables, sensitive_variables
    )
    payload = {
        "tcode": macro.tcode,
        "steps": [step.model_dump(mode="python") for step in macro.steps],
    }
    binding = bind_variables(payload, values)
    if binding.missing:
        raise MacroCompileError(
            "missing macro variables: " + ", ".join(binding.missing)
        )
    if contains_variable_tokens(binding.value):
        raise MacroCompileError("unresolved macro variable token remains after binding")

    resolved_tcode = str(binding.value["tcode"] or "").strip().upper()
    resolved_steps = [MacroStep.model_validate(item) for item in binding.value["steps"]]
    steps: list[CompiledMacroStep] = []
    order = 1
    if resolved_tcode:
        synthetic = MacroStep(
            step_number=0,
            action=ActionType.RUN_TRANSACTION,
            target_id=resolved_tcode,
            value=resolved_tcode,
            description="macro tcode",
        )
        steps.append(_compile_step(synthetic, order=order, secrets=secrets, synthetic=True))
        order += 1
    for step in resolved_steps:
        steps.append(_compile_step(step, order=order, secrets=secrets))
        order += 1

    # Runtime values not used by the macro are rejected after binding so old
    # macros with undeclared-but-referenced placeholders remain supported.
    unused = sorted(set(values) - set(binding.used))
    declared_defaults = {
        variable.name
        for variable in macro.variables
        if variable.default != "" and variable.name not in normalize_runtime_variables(runtime_variables, macro)
    }
    unused_runtime = [name for name in unused if name not in declared_defaults]
    if unused_runtime:
        raise MacroCompileError("unused macro variables: " + ", ".join(unused_runtime))

    return CompiledMacro(
        macro_id=macro.id,
        macro_name=macro.name,
        variables=compiled_variables,
        used_variables=binding.used,
        steps=tuple(steps),
    )


__all__ = [
    "CompiledMacro",
    "CompiledMacroStep",
    "CompiledMacroVariable",
    "MacroCompileError",
    "compile_macro",
]
