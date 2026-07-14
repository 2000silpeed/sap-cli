"""Pure contracts and reporting helpers for Windows SAP GUI canaries."""

from .report import (
    build_jsonl,
    iter_jsonl_records,
    parse_jsonl,
    summarize_samples,
)
from .schema import (
    CanaryCheckResult,
    CanaryCounters,
    CanaryEnvironment,
    CanaryExecutionSample,
    CanarySummary,
    CheckStatus,
    FailureClass,
    redact_identifier,
)

__all__ = [
    "CanaryCheckResult",
    "CanaryCounters",
    "CanaryEnvironment",
    "CanaryExecutionSample",
    "CanarySummary",
    "CheckStatus",
    "FailureClass",
    "build_jsonl",
    "iter_jsonl_records",
    "parse_jsonl",
    "redact_identifier",
    "summarize_samples",
]
