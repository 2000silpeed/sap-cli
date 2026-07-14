from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from sap_cli.canary import (
    CanaryCheckResult,
    CanaryCounters,
    CanaryEnvironment,
    CanaryExecutionSample,
    CheckStatus,
    FailureClass,
    build_jsonl,
    parse_jsonl,
    redact_identifier,
    summarize_samples,
)


def environment() -> CanaryEnvironment:
    return CanaryEnvironment.from_raw_identifiers(
        environment_id="windows-canary",
        system_id="PRD",
        client_id="100",
        user_id="CANARY_USER",
        os_name="Windows",
        sap_gui_version="8.00",
    )


def test_environment_retains_only_stable_redacted_identifiers() -> None:
    first = environment()
    second = environment()

    assert first == second
    assert first.system_id_redacted.startswith("redacted:")
    assert "PRD" not in first.model_dump_json()
    assert "CANARY_USER" not in first.model_dump_json()
    assert redact_identifier("PRD") != redact_identifier("QAS")

    with pytest.raises(ValidationError, match="redacted tokens"):
        CanaryEnvironment(environment_id="bad", system_id_redacted="PRD")
    with pytest.raises(ValidationError, match="business_values"):
        CanaryEnvironment.model_validate(
            {"environment_id": "bad", "business_values": {"matnr": "123"}}
        )


def test_check_status_and_failure_class_are_low_cardinality_contracts() -> None:
    passed = CanaryCheckResult(check_id="map.load", status=CheckStatus.PASS)
    failed = CanaryCheckResult(
        check_id="plan.execute",
        status=CheckStatus.FAIL,
        failure_class=FailureClass.SAP,
    )

    assert passed.status is CheckStatus.PASS
    assert failed.failure_class is FailureClass.SAP
    assert {item.value for item in FailureClass} == {
        "map",
        "plan",
        "auth",
        "environment",
        "sap",
        "unknown",
    }
    with pytest.raises(ValidationError, match="require failure_class"):
        CanaryCheckResult(check_id="map.load", status=CheckStatus.PARTIAL)
    with pytest.raises(ValidationError, match="cannot have failure_class"):
        CanaryCheckResult(
            check_id="map.load",
            status=CheckStatus.PASS,
            failure_class=FailureClass.MAP,
        )


def test_summary_uses_nearest_rank_and_stable_failure_order() -> None:
    samples = [
        CanaryExecutionSample(
            sample_id=f"sample-{index:02d}",
            duration_ms=index,
            success=index % 3 != 0,
            drift=index in {4, 8},
            failure_class=FailureClass.SAP if index % 3 == 0 else None,
            counters=CanaryCounters(com_calls=2, sap_actions=1),
        )
        for index in range(1, 21)
    ]

    summary = summarize_samples(samples)

    assert summary.sample_count == 20
    assert summary.success_count == 14
    assert summary.success_rate == 0.7
    assert summary.p50_duration_ms == 10
    assert summary.p95_duration_ms == 19
    assert summary.drift_count == 2
    assert summary.drift_rate == 0.1
    assert summary.failure_counts == {FailureClass.SAP: 6}
    assert summary.counters_total.com_calls == 40


def test_jsonl_is_deterministic_safe_and_ordered() -> None:
    samples = (
        CanaryExecutionSample(
            sample_id="sample-ok",
            duration_ms=12.5,
            success=True,
            checks=(
                CanaryCheckResult(check_id="map.load", status=CheckStatus.PASS),
            ),
        ),
        CanaryExecutionSample(
            sample_id="sample-fail",
            duration_ms=20,
            success=False,
            drift=True,
            failure_class=FailureClass.MAP,
        ),
    )

    first = build_jsonl(environment(), samples)
    second = build_jsonl(environment(), samples)
    parsed = parse_jsonl(first)

    assert first == second
    assert first.endswith("\n")
    assert [record["record_type"] for record in parsed] == [
        "environment",
        "sample",
        "sample",
        "summary",
    ]
    assert json.loads(first.splitlines()[-1])["summary"]["sample_count"] == 2
    assert "PRD" not in first
    assert "CANARY_USER" not in first


def test_models_reject_business_payload_fields_and_inconsistent_outcomes() -> None:
    with pytest.raises(ValidationError, match="business_values"):
        CanaryExecutionSample.model_validate(
            {
                "sample_id": "sample-1",
                "duration_ms": 1,
                "success": True,
                "business_values": {"material": "secret"},
            }
        )
    with pytest.raises(ValidationError, match="failed samples require"):
        CanaryExecutionSample(sample_id="sample-1", duration_ms=1, success=False)
    with pytest.raises(ValidationError, match="successful samples cannot"):
        CanaryExecutionSample(
            sample_id="sample-1",
            duration_ms=1,
            success=True,
            failure_class=FailureClass.UNKNOWN,
        )


def test_empty_report_has_defined_zero_rates_and_percentiles() -> None:
    summary = summarize_samples(())

    assert summary.sample_count == 0
    assert summary.success_rate == 0
    assert summary.p50_duration_ms == 0
    assert summary.p95_duration_ms == 0
    assert summary.drift_rate == 0
    assert parse_jsonl(build_jsonl(environment(), ()))[-1]["summary"] == summary.model_dump(
        mode="json"
    )


def test_jsonl_parser_revalidates_redaction_and_summary() -> None:
    rendered = build_jsonl(
        environment(),
        (CanaryExecutionSample(sample_id="sample-ok", duration_ms=1, success=True),),
    )
    lines = [json.loads(line) for line in rendered.splitlines()]
    lines[0]["environment"]["system_id_redacted"] = "PRD"
    unsafe = "\n".join(json.dumps(line) for line in lines)

    with pytest.raises(ValidationError, match="redacted tokens"):
        parse_jsonl(unsafe)

    lines = [json.loads(line) for line in rendered.splitlines()]
    lines[-1]["summary"]["success_count"] = 0
    inconsistent = "\n".join(json.dumps(line) for line in lines)
    with pytest.raises((ValidationError, ValueError)):
        parse_jsonl(inconsistent)
