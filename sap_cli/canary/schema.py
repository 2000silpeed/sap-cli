"""Value-only schemas for production canary observations.

These contracts deliberately have no field for SAP business inputs, component
values, screenshots, free-form exception messages, or natural-language
requests.  Environment identifiers are accepted only after deterministic
redaction.
"""

from __future__ import annotations

import hashlib
import re
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


_REDACTED_ID = re.compile(r"^redacted:[0-9a-f]{16,64}$")
_CHECK_ID = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")


class CheckStatus(StrEnum):
    """Portable result of one canary prerequisite or verification check."""

    PASS = "PASS"
    FAIL = "FAIL"
    PARTIAL = "PARTIAL"
    SKIP = "SKIP"


class FailureClass(StrEnum):
    """Stable, low-cardinality failure classes; never exception text."""

    MAP = "map"
    PLAN = "plan"
    AUTH = "auth"
    ENVIRONMENT = "environment"
    SAP = "sap"
    UNKNOWN = "unknown"


def redact_identifier(value: str, *, salt: str = "sap-cli-canary-v1") -> str:
    """Return a stable token without retaining the source identifier."""

    if not isinstance(value, str):
        raise TypeError("identifier must be a string")
    normalized = value.strip()
    if not normalized:
        return ""
    digest = hashlib.sha256(f"{salt}\0{normalized}".encode("utf-8")).hexdigest()
    return f"redacted:{digest}"


class _CanaryModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_enum_values=False)


class CanaryEnvironment(_CanaryModel):
    """Non-business environment facts attached to a canary run."""

    environment_id: str = Field(min_length=1, max_length=64, pattern=_CHECK_ID.pattern)
    system_id_redacted: str = ""
    client_id_redacted: str = ""
    user_id_redacted: str = ""
    os_name: str = Field(default="", max_length=64)
    os_version: str = Field(default="", max_length=64)
    architecture: str = Field(default="", max_length=32)
    python_version: str = Field(default="", max_length=32)
    sap_gui_version: str = Field(default="", max_length=64)
    sap_cli_version: str = Field(default="", max_length=32)

    @field_validator(
        "system_id_redacted", "client_id_redacted", "user_id_redacted"
    )
    @classmethod
    def _require_redacted_identifier(cls, value: str) -> str:
        if value and not _REDACTED_ID.fullmatch(value):
            raise ValueError("environment identifiers must be redacted tokens")
        return value

    @classmethod
    def from_raw_identifiers(
        cls,
        *,
        environment_id: str,
        system_id: str = "",
        client_id: str = "",
        user_id: str = "",
        **facts: Any,
    ) -> "CanaryEnvironment":
        """Construct an environment while discarding raw identifiers."""

        return cls(
            environment_id=environment_id,
            system_id_redacted=redact_identifier(system_id),
            client_id_redacted=redact_identifier(client_id),
            user_id_redacted=redact_identifier(user_id),
            **facts,
        )


class CanaryCheckResult(_CanaryModel):
    """One named check without free-form diagnostic or business payloads."""

    check_id: str = Field(min_length=1, max_length=64, pattern=_CHECK_ID.pattern)
    status: CheckStatus
    failure_class: FailureClass | None = None
    duration_ms: float = Field(default=0.0, ge=0)

    @model_validator(mode="after")
    def _validate_failure_class(self) -> "CanaryCheckResult":
        if self.status in {CheckStatus.FAIL, CheckStatus.PARTIAL}:
            if self.failure_class is None:
                raise ValueError("failed or partial checks require failure_class")
        elif self.status is CheckStatus.PASS and self.failure_class is not None:
            raise ValueError("passing checks cannot have failure_class")
        return self


class CanaryCounters(_CanaryModel):
    """Structural work counters safe to retain outside the SAP session."""

    ipc_calls: int = Field(default=0, ge=0)
    com_calls: int = Field(default=0, ge=0)
    sap_actions: int = Field(default=0, ge=0)
    com_state_reads: int = Field(default=0, ge=0)
    full_screen_scans: int = Field(default=0, ge=0)
    map_cache_hits: int = Field(default=0, ge=0)
    plan_cache_hits: int = Field(default=0, ge=0)
    retries: int = Field(default=0, ge=0)
    drift_recoveries: int = Field(default=0, ge=0)

    def __add__(self, other: object) -> "CanaryCounters":
        if not isinstance(other, CanaryCounters):
            return NotImplemented
        return CanaryCounters(
            **{
                name: getattr(self, name) + getattr(other, name)
                for name in type(self).model_fields
            }
        )


class CanaryExecutionSample(_CanaryModel):
    """One end-to-end execution observation with no SAP business values."""

    sample_id: str = Field(min_length=1, max_length=64, pattern=_CHECK_ID.pattern)
    duration_ms: float = Field(ge=0)
    success: bool
    drift: bool = False
    failure_class: FailureClass | None = None
    counters: CanaryCounters = Field(default_factory=CanaryCounters)
    checks: tuple[CanaryCheckResult, ...] = ()

    @model_validator(mode="after")
    def _validate_outcome(self) -> "CanaryExecutionSample":
        if self.success and self.failure_class is not None:
            raise ValueError("successful samples cannot have failure_class")
        if not self.success and self.failure_class is None:
            raise ValueError("failed samples require failure_class")
        check_ids = [check.check_id for check in self.checks]
        if len(check_ids) != len(set(check_ids)):
            raise ValueError("check_id values must be unique within a sample")
        if self.success and any(
            check.status in {CheckStatus.FAIL, CheckStatus.PARTIAL}
            for check in self.checks
        ):
            raise ValueError("successful samples cannot contain failed checks")
        return self


class CanarySummary(_CanaryModel):
    """Deterministic aggregate over an ordered set of execution samples."""

    sample_count: int = Field(ge=0)
    success_count: int = Field(ge=0)
    success_rate: float = Field(ge=0, le=1)
    p50_duration_ms: float = Field(ge=0)
    p95_duration_ms: float = Field(ge=0)
    drift_count: int = Field(ge=0)
    drift_rate: float = Field(ge=0, le=1)
    failure_counts: dict[FailureClass, int] = Field(default_factory=dict)
    counters_total: CanaryCounters = Field(default_factory=CanaryCounters)

    @field_validator("failure_counts")
    @classmethod
    def _validate_failure_counts(
        cls, value: dict[FailureClass, int]
    ) -> dict[FailureClass, int]:
        normalized = {FailureClass(key): count for key, count in value.items()}
        if any(isinstance(count, bool) or count < 0 for count in normalized.values()):
            raise ValueError("failure counts must be non-negative integers")
        return {key: normalized[key] for key in FailureClass if key in normalized}

    @model_validator(mode="after")
    def _validate_totals(self) -> "CanarySummary":
        if self.success_count > self.sample_count:
            raise ValueError("success_count cannot exceed sample_count")
        if self.drift_count > self.sample_count:
            raise ValueError("drift_count cannot exceed sample_count")
        if sum(self.failure_counts.values()) != self.sample_count - self.success_count:
            raise ValueError("failure_counts must account for every failed sample")
        expected_success = self.success_count / self.sample_count if self.sample_count else 0
        expected_drift = self.drift_count / self.sample_count if self.sample_count else 0
        if self.success_rate != expected_success:
            raise ValueError("success_rate does not match counts")
        if self.drift_rate != expected_drift:
            raise ValueError("drift_rate does not match counts")
        return self
