"""Execution planning contracts."""

from sap_cli.planning.action_adapter import (
    CompiledAction,
    UnsupportedTransitionError,
    compile_transition,
)
from sap_cli.planning.compiler import CompilationResult, PlanCompiler
from sap_cli.planning.approvals import (
    ApprovalReceipt,
    ApprovalTokenConsumedError,
    ApprovalTokenError,
    ApprovalTokenExpiredError,
    ApprovalTokenInvalidError,
    PlanApprovalStore,
    get_plan_approval_store,
    set_plan_approval_store,
    try_get_plan_approval_store,
)
from sap_cli.planning.executor import (
    PlanExecutionResult,
    PlanExecutor,
    StepTrace,
    VerificationMode,
)
from sap_cli.planning.intent import ParsedIntent, parse_intent
from sap_cli.planning.preflight import (
    AuthorizationHook,
    PreflightResult,
    PreflightStep,
    preflight_plan,
    strict_substitute,
)
from sap_cli.planning.schema import (
    ExecutionPlan,
    NaturalLanguageRequest,
    PlanInput,
    PlanStep,
    PlanValidation,
    PlanVariable,
    ResolvedIntent,
    RiskLevel,
    ScanPolicy,
)
from sap_cli.planning.variables import (
    BindingResult,
    bind_variables,
    contains_variable_tokens,
    redact_variables,
)
from sap_cli.planning.runtime import (
    PlanReference,
    PlanRegistry,
    PlanRuntime,
    get_plan_runtime,
    set_plan_runtime,
    try_get_plan_runtime,
)
from sap_cli.planning.safety import (
    RuntimeSafety,
    classify_runtime_step,
    runtime_safety_errors,
)

__all__ = [
    "AuthorizationHook",
    "ApprovalReceipt",
    "ApprovalTokenConsumedError",
    "ApprovalTokenError",
    "ApprovalTokenExpiredError",
    "ApprovalTokenInvalidError",
    "ExecutionPlan",
    "BindingResult",
    "CompilationResult",
    "CompiledAction",
    "NaturalLanguageRequest",
    "PlanInput",
    "PlanApprovalStore",
    "PlanExecutionResult",
    "PlanExecutor",
    "PlanReference",
    "PlanRegistry",
    "PlanRuntime",
    "PlanStep",
    "PlanValidation",
    "PlanVariable",
    "PlanCompiler",
    "PreflightResult",
    "PreflightStep",
    "ParsedIntent",
    "ResolvedIntent",
    "RiskLevel",
    "RuntimeSafety",
    "ScanPolicy",
    "UnsupportedTransitionError",
    "VerificationMode",
    "bind_variables",
    "compile_transition",
    "contains_variable_tokens",
    "classify_runtime_step",
    "get_plan_runtime",
    "get_plan_approval_store",
    "parse_intent",
    "preflight_plan",
    "redact_variables",
    "runtime_safety_errors",
    "set_plan_runtime",
    "set_plan_approval_store",
    "StepTrace",
    "strict_substitute",
    "try_get_plan_runtime",
    "try_get_plan_approval_store",
]
