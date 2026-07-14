"""Deterministic contracts for compiled SAP GUI execution plans."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Literal

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    SerializationInfo,
    field_serializer,
    field_validator,
    model_validator,
)

from sap_cli.ui_map.schema import SideEffectClass


RiskLevel = Literal["low", "medium", "high", "critical"]
ScanPolicy = Literal["fast", "adaptive", "full"]
_VARIABLE_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_.-]*)\}")
_COMMIT_EFFECTS = {
    SideEffectClass.COMMIT,
    SideEffectClass.DESTRUCTIVE,
    SideEffectClass.EXTERNAL,
}


class NaturalLanguageRequest(BaseModel):
    """Original user request plus explicit execution scope and supplied values."""

    request_id: str = ""
    text: str = Field(validation_alias=AliasChoices("text", "query", "request"))
    language: str = "ko"
    system_id: str = ""
    client: str = ""
    slot: int | None = Field(default=None, ge=0)
    tcode_hint: str = ""
    supplied_values: dict[str, Any] = Field(default_factory=dict)
    success_criteria: list[str] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)


class ResolvedIntent(BaseModel):
    """Structured interpretation produced before graph path compilation."""

    request_id: str = ""
    tcode: str
    operation: str
    entities: list[str] = Field(default_factory=list)
    values: dict[str, Any] = Field(default_factory=dict)
    success_criteria: list[str] = Field(default_factory=list)
    ambiguities: list[dict[str, Any]] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    provenance: dict[str, Any] = Field(default_factory=dict)

    @field_validator("tcode")
    @classmethod
    def _normalize_tcode(cls, value: str) -> str:
        return value.strip().upper()


class PlanVariable(BaseModel):
    """Typed plan value whose sensitive payload is redacted by default."""

    model_config = ConfigDict(frozen=True)

    value: Any = None
    source: str = ""
    required: bool = True
    resolved: bool = True
    sensitive: bool = False
    description: str = ""

    @field_serializer("value")
    def _redact_value(self, value: Any, info: SerializationInfo) -> Any:
        context = info.context or {}
        if self.sensitive and not context.get("reveal_sensitive", False):
            return "***REDACTED***"
        return value


class PlanInput(BaseModel):
    """All resolved inputs and map evidence consumed by the compiler."""

    request: NaturalLanguageRequest
    intent: ResolvedIntent
    map_id: str
    map_version: int = Field(ge=1)
    map_fingerprint: str = ""
    map_scope: dict[str, str] = Field(default_factory=dict)
    recipe_ids: list[str] = Field(default_factory=list)
    variables: dict[str, PlanVariable] = Field(default_factory=dict)

    @field_validator("variables", mode="before")
    @classmethod
    def _coerce_variables(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        return {
            name: item if isinstance(item, (dict, PlanVariable)) else {"value": item}
            for name, item in value.items()
        }


class PlanStep(BaseModel):
    """One pre-resolved daemon command and its transition contract."""

    model_config = ConfigDict(frozen=True)

    step_id: str
    order: int = Field(ge=1)
    command: str = ""
    tool: str = ""
    target_logical_id: str = ""
    target_locator: dict[str, Any] = Field(default_factory=dict)
    params: dict[str, Any] = Field(default_factory=dict)
    expected_from_fingerprint: str = ""
    expected_to_fingerprint: str = ""
    guards: list[dict[str, Any]] = Field(default_factory=list)
    effects: list[dict[str, Any]] = Field(default_factory=list)
    status: dict[str, Any] = Field(default_factory=dict)
    modal: dict[str, Any] = Field(default_factory=dict)
    side_effect: SideEffectClass = SideEffectClass.NAVIGATION
    risk: RiskLevel = "medium"
    approval_required: bool = False
    approval: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: float = Field(default=30.0, gt=0)
    idempotent: bool = False
    retry: dict[str, Any] = Field(default_factory=dict)
    recovery: dict[str, Any] = Field(default_factory=dict)
    commit_boundary: bool = False
    map_transition_id: str = ""
    map_id: str = ""
    map_version: int | None = Field(default=None, ge=1)
    map_fingerprint: str = ""

    @model_validator(mode="after")
    def _validate_safety(self) -> "PlanStep":
        if not self.command and not self.tool:
            raise ValueError("plan step requires command or tool")
        if self.side_effect in _COMMIT_EFFECTS and not self.approval_required:
            raise ValueError("commit/destructive/external plan step requires approval")
        if self.side_effect in _COMMIT_EFFECTS and _retry_attempts(self.retry) > 1:
            raise ValueError("commit/destructive/external plan step cannot be retried")
        return self


class ExecutionPlan(BaseModel):
    """Immutable, content-addressed plan ready for preview and authorization."""

    model_config = ConfigDict(frozen=True)

    plan_id: str
    original_request: str
    tcode: str
    map_id: str
    map_scope: dict[str, str] = Field(default_factory=dict)
    map_version: int = Field(ge=1)
    map_fingerprint: str = ""
    recipe_ids: list[str] = Field(default_factory=list)
    start_state_id: str
    goal_state_id: str = ""
    goal_component_id: str = ""
    variables: dict[str, PlanVariable] = Field(default_factory=dict)
    unresolved_inputs: list[str] = Field(default_factory=list)
    questions: list[str | dict[str, Any]] = Field(default_factory=list)
    preconditions: list[dict[str, Any]] = Field(default_factory=list)
    scan_policy: ScanPolicy = "fast"
    steps: list[PlanStep]
    success_criteria: list[str] = Field(default_factory=list)
    recovery: dict[str, Any] = Field(default_factory=dict)
    created_at: str = ""
    plan_hash: str = Field(default="", frozen=True)

    @field_validator("tcode")
    @classmethod
    def _normalize_tcode(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("variables", mode="before")
    @classmethod
    def _coerce_variables(cls, value: Any) -> Any:
        return PlanInput._coerce_variables(value)

    @model_validator(mode="after")
    def _validate_plan(self) -> "ExecutionPlan":
        if not self.goal_state_id and not self.goal_component_id:
            raise ValueError("plan requires a goal state or goal component")
        orders = [step.order for step in self.steps]
        if orders != list(range(1, len(self.steps) + 1)):
            raise ValueError("plan step order must be contiguous and start at 1")
        if len({step.step_id for step in self.steps}) != len(self.steps):
            raise ValueError("plan step_id values must be unique")

        declared = set(self.variables) | set(self.unresolved_inputs)
        referenced = set().union(*(_variable_refs(step.params) for step in self.steps))
        missing = sorted(referenced - declared)
        if missing:
            raise ValueError(f"undeclared plan variables: {', '.join(missing)}")

        commit_seen = False
        for step in self.steps:
            if commit_seen and _retry_attempts(step.retry) > 1:
                raise ValueError("retry is forbidden after a commit boundary")
            if step.commit_boundary or step.side_effect in _COMMIT_EFFECTS:
                commit_seen = True

        expected = self.compute_plan_hash()
        if self.plan_hash and self.plan_hash != expected:
            raise ValueError("plan_hash does not match immutable plan content")
        if not self.plan_hash:
            object.__setattr__(self, "plan_hash", expected)
        return self

    def compute_plan_hash(self) -> str:
        payload = self.model_dump(
            mode="json",
            exclude={"plan_hash", "created_at"},
            context={"reveal_sensitive": True},
        )
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def verify_hash(self) -> bool:
        return self.plan_hash == self.compute_plan_hash()


class PlanValidation(BaseModel):
    """Serializable preflight result shown with the plan preview."""

    valid: bool
    plan_hash: str = ""
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    unresolved_inputs: list[str] = Field(default_factory=list)
    questions: list[str | dict[str, Any]] = Field(default_factory=list)
    requires_approval: bool = False
    required_approvals: list[str] = Field(default_factory=list)
    checked_preconditions: list[dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode="after")
    def _valid_means_executable(self) -> "PlanValidation":
        if self.valid and (self.errors or self.unresolved_inputs):
            raise ValueError("valid plan cannot contain errors or unresolved inputs")
        return self


def _retry_attempts(retry: dict[str, Any]) -> int:
    for key in ("max_attempts", "attempts", "count"):
        if key in retry:
            try:
                return max(1, int(retry[key]))
            except (TypeError, ValueError):
                return 1
    return 1


def _variable_refs(value: Any) -> set[str]:
    if isinstance(value, str):
        return set(_VARIABLE_REF.findall(value))
    if isinstance(value, dict):
        return set().union(*(_variable_refs(item) for item in value.values()), set())
    if isinstance(value, (list, tuple, set)):
        return set().union(*(_variable_refs(item) for item in value), set())
    return set()
