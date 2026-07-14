"""Deterministic canary aggregation and JSONL serialization."""

from __future__ import annotations

import json
import math
from collections import Counter
from collections.abc import Iterable, Iterator, Sequence
from typing import Any

from .schema import (
    CanaryCounters,
    CanaryEnvironment,
    CanaryExecutionSample,
    CanarySummary,
    FailureClass,
)


_RECORD_TYPES = ("environment", "sample", "summary")
_FORBIDDEN_KEYS = {
    "business_value",
    "business_values",
    "field_value",
    "field_values",
    "input_values",
    "params",
    "request",
    "screenshot",
    "variables",
}


def _nearest_rank(values: Sequence[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    return ordered[math.ceil(percentile * len(ordered)) - 1]


def summarize_samples(samples: Iterable[CanaryExecutionSample]) -> CanarySummary:
    """Aggregate injected observations without reading clocks or external state."""

    materialized = tuple(samples)
    if any(not isinstance(sample, CanaryExecutionSample) for sample in materialized):
        raise TypeError("samples must contain CanaryExecutionSample objects")

    sample_count = len(materialized)
    success_count = sum(sample.success for sample in materialized)
    drift_count = sum(sample.drift for sample in materialized)
    failures = Counter(
        sample.failure_class for sample in materialized if sample.failure_class is not None
    )
    counters = CanaryCounters()
    for sample in materialized:
        counters = counters + sample.counters

    return CanarySummary(
        sample_count=sample_count,
        success_count=success_count,
        success_rate=success_count / sample_count if sample_count else 0,
        p50_duration_ms=_nearest_rank(
            [sample.duration_ms for sample in materialized], 0.50
        ),
        p95_duration_ms=_nearest_rank(
            [sample.duration_ms for sample in materialized], 0.95
        ),
        drift_count=drift_count,
        drift_rate=drift_count / sample_count if sample_count else 0,
        failure_counts={kind: failures[kind] for kind in FailureClass if failures[kind]},
        counters_total=counters,
    )


def iter_jsonl_records(
    environment: CanaryEnvironment,
    samples: Iterable[CanaryExecutionSample],
    *,
    summary: CanarySummary | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield environment, samples, and summary in canonical record order."""

    if not isinstance(environment, CanaryEnvironment):
        raise TypeError("environment must be a CanaryEnvironment")
    materialized = tuple(samples)
    computed = summarize_samples(materialized)
    if summary is not None and summary != computed:
        raise ValueError("provided summary does not match samples")

    records = [
        {
            "record_type": "environment",
            "environment": environment.model_dump(mode="json"),
        },
        *(
            {
                "record_type": "sample",
                "sample": sample.model_dump(mode="json"),
            }
            for sample in materialized
        ),
        {
            "record_type": "summary",
            "summary": computed.model_dump(mode="json"),
        },
    ]
    for record in records:
        _assert_no_business_values(record)
        yield record


def build_jsonl(
    environment: CanaryEnvironment,
    samples: Iterable[CanaryExecutionSample],
    *,
    summary: CanarySummary | None = None,
) -> str:
    """Render canonical compact JSONL with a trailing newline."""

    return "".join(
        json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
        for record in iter_jsonl_records(environment, samples, summary=summary)
    )


def parse_jsonl(value: str) -> tuple[dict[str, Any], ...]:
    """Validate safe payload schemas, ordering, and aggregate consistency."""

    if not isinstance(value, str):
        raise TypeError("JSONL input must be text")
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(value.splitlines(), start=1):
        if not line.strip():
            continue
        decoded = json.loads(line)
        if not isinstance(decoded, dict):
            raise ValueError(f"JSONL record {line_number} must be an object")
        _assert_no_business_values(decoded)
        records.append(decoded)

    kinds = [record.get("record_type") for record in records]
    if not kinds or kinds[0] != "environment" or kinds[-1] != "summary":
        raise ValueError("JSONL must start with environment and end with summary")
    if any(kind not in _RECORD_TYPES for kind in kinds):
        raise ValueError("unknown canary JSONL record type")
    if any(kind != "sample" for kind in kinds[1:-1]):
        raise ValueError("only sample records may appear between envelope records")
    expected_keys = {
        "environment": {"record_type", "environment"},
        "sample": {"record_type", "sample"},
        "summary": {"record_type", "summary"},
    }
    for record, kind in zip(records, kinds, strict=True):
        if set(record) != expected_keys[kind]:
            raise ValueError(f"invalid {kind} record envelope")

    environment = CanaryEnvironment.model_validate(records[0]["environment"])
    samples = tuple(
        CanaryExecutionSample.model_validate(record["sample"])
        for record in records[1:-1]
    )
    summary = CanarySummary.model_validate(records[-1]["summary"])
    computed = summarize_samples(samples)
    if summary != computed:
        raise ValueError("JSONL summary does not match sample records")

    # Return normalized JSON-compatible records so enum and tuple handling is
    # identical regardless of how the source JSON was produced.
    return tuple(iter_jsonl_records(environment, samples, summary=summary))


def _assert_no_business_values(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in _FORBIDDEN_KEYS:
                raise ValueError("canary records cannot contain business values")
            _assert_no_business_values(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _assert_no_business_values(item)
