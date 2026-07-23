from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest


SCRIPT = Path(__file__).parents[1] / "scripts" / "windows_canary.py"
SPEC = importlib.util.spec_from_file_location("windows_canary", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
windows_canary = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = windows_canary
SPEC.loader.exec_module(windows_canary)


def config(*, mode: str = "passive", plan: bool = False) -> dict[str, Any]:
    active_mapping = mode in {"reversible", "allowlisted"}
    allowed_branches = (
        [
            {
                "action": "press",
                "source_state_id": "state-mm03-main",
                "target_logical_id": "open-detail",
                "target_sap_id": "wnd[0]/usr/btnDETAIL",
                "component_type": "GuiButton",
                "restore_vkey": 3,
            }
        ]
        if mode == "allowlisted"
        else []
    )
    value: dict[str, Any] = {
        "tcode": "MM03",
        "mapping": {
            "mode": mode,
            "map_id": "canary-mm03",
            "budget": {"max_states": 10},
            "allowed_actions": ["tab-next"] if mode == "reversible" else [],
            "allowed_branches": allowed_branches,
        },
        "acknowledgements": {
            "read_only_tcode": True,
            "non_production_client": active_mapping,
            "reversible_actions_only": active_mapping,
        },
        "polling": {"interval_seconds": 0.01, "timeout_seconds": 5},
    }
    if plan:
        value["plan"] = {
            "request": "MM03에서 현재 자재 화면을 조회",
            "values": {},
        }
    return value


def write_config(tmp_path: Path, value: dict[str, Any]) -> Path:
    path = tmp_path / "canary.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


class FakeTransport:
    def __init__(self, *, with_plan: bool = False) -> None:
        self.with_plan = with_plan
        self.calls: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
        self.polls = 0

    def __call__(
        self,
        command: str,
        args: dict[str, Any],
        *,
        timeout: float,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        self.calls.append((command, args, context))
        if command == "ping":
            data: dict[str, Any] = {"daemon": "running"}
        elif command == "map_traverse_start":
            data = {
                "job": {
                    "job_id": "map-job-1",
                    "status": "queued",
                }
            }
        elif command == "map_job_status":
            self.polls += 1
            data = {
                "job": {
                    "job_id": "map-job-1",
                    "status": "completed" if self.polls > 1 else "running",
                    "stop_reason": "passive_complete",
                }
            }
        elif command == "map_report":
            data = {
                "map_id": "canary-mm03",
                "map_fingerprint": "b" * 64,
                "scope": {"tcode": "MM03", "client": "SECRET-CLIENT"},
                "counts": {
                    "states": 4,
                    "transitions": 3,
                    "components": 12,
                    "collections_by_kind": {"alv": 1},
                    "unreachable_by_reason": {"max_states": 1},
                },
                "scan_metrics": {"bulk_tree_calls": 4},
            }
        elif command == "api_live_probe":
            data = {"verified": True, "member_id": "property:GuiSessionInfo.Client"}
        elif command == "plan_preview" and self.with_plan:
            data = {
                "validation": {
                    "valid": True,
                    "requires_approval": False,
                    "required_approvals": [],
                },
                "plan": {
                    "steps": [
                        {
                            "step_id": "read-1",
                            "command": "ui_get_property",
                            "side_effect": "read_only",
                            "approval_required": False,
                            "commit_boundary": False,
                            "params": {"element_id": "wnd[0]/usr/txtSECRET"},
                        }
                    ]
                },
                "plan_ref": {
                    "plan_id": "plan-1",
                    "plan_hash": "a" * 64,
                },
            }
        elif command == "plan_execute" and self.with_plan:
            data = {
                "status": "completed",
                "result": {
                    "status": "success",
                    "traces": [],
                    "get_screen_count": 0,
                    "final_probe": {"status_text": "SENSITIVE BUSINESS VALUE"},
                },
            }
        else:
            raise AssertionError(f"unexpected command: {command}")
        return {"ok": True, "data": data}


def fixed_now() -> datetime:
    return datetime(2026, 7, 15, tzinfo=timezone.utc)


def test_non_windows_live_run_is_refused_before_transport(tmp_path: Path) -> None:
    cfg = write_config(tmp_path, config())
    output = tmp_path / "result.jsonl"
    transport = FakeTransport()

    code = windows_canary.main(
        ["--config", str(cfg), "--output", str(output)],
        platform_name="Darwin",
        transport=transport,
        now=fixed_now,
    )

    assert code == 2
    assert transport.calls == []
    assert read_jsonl(output)[-1]["error_code"] == "windows_live_required"


def test_non_windows_allow_fake_requires_injected_transport(tmp_path: Path) -> None:
    cfg = write_config(tmp_path, config())
    output = tmp_path / "result.jsonl"

    code = windows_canary.main(
        ["--config", str(cfg), "--output", str(output), "--allow-fake"],
        platform_name="Darwin",
        now=fixed_now,
    )

    assert code == 2
    assert read_jsonl(output)[-1]["error_code"] == "fake_transport_required"


@pytest.mark.parametrize(
    ("mutate", "error"),
    [
        (
            lambda value: value["acknowledgements"].update(
                {"read_only_tcode": False}
            ),
            "read_only_acknowledgement_required",
        ),
        (
            lambda value: value["acknowledgements"].update(
                {"non_production_client": False}
            ),
            "reversible_acknowledgements_required",
        ),
        (
            lambda value: value["mapping"].update(
                {"allowed_actions": ["save"]}
            ),
            "unsafe_action_forbidden",
        ),
    ],
)
def test_reversible_safety_fails_before_daemon(
    tmp_path: Path, mutate: Any, error: str
) -> None:
    value = config(mode="reversible")
    mutate(value)
    cfg = write_config(tmp_path, value)
    output = tmp_path / "result.jsonl"
    transport = FakeTransport()

    code = windows_canary.main(
        ["--config", str(cfg), "--output", str(output), "--allow-fake"],
        platform_name="Darwin",
        transport=transport,
        now=fixed_now,
    )

    assert code == 2
    assert transport.calls == []
    assert read_jsonl(output)[-1]["error_code"] == error


@pytest.mark.parametrize("word", ["save", "post", "delete", "release", "send", "print"])
def test_all_named_unsafe_plan_request_words_are_denied(
    tmp_path: Path, word: str
) -> None:
    value = config(plan=True)
    value["plan"]["request"] = f"MM03 then {word}"
    cfg = write_config(tmp_path, value)
    output = tmp_path / "result.jsonl"
    transport = FakeTransport(with_plan=True)

    code = windows_canary.main(
        ["--config", str(cfg), "--output", str(output), "--allow-fake"],
        platform_name="Darwin",
        transport=transport,
        now=fixed_now,
    )

    assert code == 2
    assert transport.calls == []
    assert read_jsonl(output)[-1]["error_code"] == "unsafe_action_forbidden"


def test_one_mapping_job_is_polled_and_report_is_sanitized(tmp_path: Path) -> None:
    cfg = write_config(tmp_path, config(mode="reversible"))
    output = tmp_path / "result.jsonl"
    transport = FakeTransport()

    code = windows_canary.main(
        ["--config", str(cfg), "--output", str(output), "--allow-fake"],
        platform_name="Darwin",
        transport=transport,
        sleep=lambda _seconds: None,
        now=fixed_now,
        id_factory=lambda: "run-1",
    )

    assert code == 0
    commands = [call[0] for call in transport.calls]
    assert commands.count("map_traverse_start") == 1
    start_args = next(call[1] for call in transport.calls if call[0] == "map_traverse_start")
    assert start_args["test_client_confirmed"] is True
    assert commands.count("map_job_status") == 2
    events = read_jsonl(output)
    assert next(item for item in events if item["event"] == "map_coverage") == {
        "timestamp": "2026-07-15T00:00:00+00:00",
        "event": "map_coverage",
        "map_fingerprint": "b" * 64,
        "counts": {
            "states": 4,
            "transitions": 3,
            "components": 12,
            "collections_by_kind": {"alv": 1},
            "unreachable_by_reason": {"max_states": 1},
        },
        "scan_metrics": {"bulk_tree_calls": 4},
    }
    text = output.read_text(encoding="utf-8")
    assert "SECRET-CLIENT" not in text
    assert "map-job-1" not in text
    assert "MM03" not in text


def test_allowlisted_canary_forwards_only_validated_exact_branches(tmp_path: Path) -> None:
    cfg = write_config(tmp_path, config(mode="allowlisted"))
    output = tmp_path / "result.jsonl"
    transport = FakeTransport()

    code = windows_canary.main(
        ["--config", str(cfg), "--output", str(output), "--allow-fake"],
        platform_name="Darwin",
        transport=transport,
        sleep=lambda _seconds: None,
        now=fixed_now,
    )

    assert code == 0
    start_args = next(
        call[1] for call in transport.calls if call[0] == "map_traverse_start"
    )
    assert start_args["mode"] == "allowlisted"
    assert start_args["test_client_confirmed"] is True
    assert start_args["allowed_branches"] == [
        {
            "action": "press",
            "source_state_id": "state-mm03-main",
            "source_fingerprint": "",
            "target_logical_id": "open-detail",
            "target_sap_id": "wnd[0]/usr/btnDETAIL",
            "component_type": "GuiButton",
            "item_id": "",
            "restore_vkey": 3,
            "expected_program": "",
            "expected_screen_number": None,
            "expected_modal_signature": "",
            "rule_id": start_args["allowed_branches"][0]["rule_id"],
        }
    ]


def test_allowlisted_canary_requires_an_exact_branch_before_daemon(tmp_path: Path) -> None:
    value = config(mode="allowlisted")
    value["mapping"]["allowed_branches"] = []
    cfg = write_config(tmp_path, value)
    output = tmp_path / "result.jsonl"
    transport = FakeTransport()

    code = windows_canary.main(
        ["--config", str(cfg), "--output", str(output), "--allow-fake"],
        platform_name="Darwin", transport=transport, now=fixed_now,
    )

    assert code == 2
    assert transport.calls == []
    assert read_jsonl(output)[-1]["error_code"] == "allowlisted_branches_required"


def test_existing_user_guided_job_is_polled_without_starting_another_job(tmp_path: Path) -> None:
    value = config()
    value["mapping"]["existing_job_id"] = "map-job-1"
    cfg = write_config(tmp_path, value)
    output = tmp_path / "result.jsonl"
    transport = FakeTransport()

    code = windows_canary.main(
        ["--config", str(cfg), "--output", str(output), "--allow-fake"],
        platform_name="Darwin", transport=transport,
        sleep=lambda _seconds: None, now=fixed_now,
    )

    assert code == 0
    commands = [call[0] for call in transport.calls]
    assert "map_traverse_start" not in commands
    assert commands.count("map_job_status") == 2
    text = output.read_text()
    assert "map-job-1" not in text
    assert "MM03" not in text


def test_safe_plan_runs_twenty_cheap_daemon_batches(tmp_path: Path) -> None:
    cfg = write_config(tmp_path, config(plan=True))
    output = tmp_path / "result.jsonl"
    transport = FakeTransport(with_plan=True)
    ticks = iter(range(1000))

    code = windows_canary.main(
        ["--config", str(cfg), "--output", str(output), "--allow-fake"],
        platform_name="Darwin",
        transport=transport,
        monotonic=lambda: next(ticks) / 1000,
        sleep=lambda _seconds: None,
        now=fixed_now,
        id_factory=lambda: "run-1",
    )

    assert code == 0
    executions = [call for call in transport.calls if call[0] == "plan_execute"]
    assert len(executions) == 21
    assert {call[1]["verify_mode"] for call in executions} == {"cheap"}
    assert all("approval_grants" not in call[1] for call in executions)
    assert all("approval_token" not in call[1] for call in executions)
    assert len({call[1]["execution_id"] for call in executions}) == 21
    assert sum("warmup" in call[1]["execution_id"] for call in executions) == 1
    assert all(call[2]["include_screen_summary"] is False for call in executions)
    events = read_jsonl(output)
    summary = next(item for item in events if item["event"] == "plan_benchmark")
    assert summary["samples"] == 20
    assert summary["success_rate"] == 1.0
    assert summary["p50_ms"] == 1.0
    assert summary["p95_ms"] == 1.0
    assert summary["full_screen_scans"] == 0
    text = output.read_text(encoding="utf-8")
    assert "SENSITIVE BUSINESS VALUE" not in text
    assert "wnd[0]/usr/txtSECRET" not in text
    report = json.loads((tmp_path / "result.report.json").read_text())
    assert report["status"] == "PASS"
    assert report["benchmark"]["samples"] == 20
    markdown = (tmp_path / "result.report.md").read_text()
    assert "p95 ms" in markdown
    assert "SENSITIVE" not in markdown
    assert "MM03" not in text
    assert "현재 자재 화면" not in text


def test_canary_runs_only_declared_value_free_api_live_probes(tmp_path: Path) -> None:
    value = config()
    value["api_live_probes"] = [
        {"scope": "session_info", "action": "get_property", "member_name": "Client"}
    ]
    cfg = write_config(tmp_path, value)
    output = tmp_path / "result.jsonl"
    transport = FakeTransport()
    code = windows_canary.main(
        ["--config", str(cfg), "--output", str(output), "--allow-fake"],
        platform_name="Darwin", transport=transport, sleep=lambda _seconds: None, now=fixed_now,
    )
    assert code == 0
    probes = [call for call in transport.calls if call[0] == "api_live_probe"]
    assert probes == [("api_live_probe", {"scope": "session_info", "action": "get_property", "member_name": "Client"}, transport.calls[-1][2])]
    event = next(item for item in read_jsonl(output) if item["event"] == "api_live_probes")
    assert event["requested"] == event["succeeded"] == 1


def test_api_probe_example_is_a_valid_read_only_canary_config() -> None:
    example = Path(__file__).parents[1] / "examples" / "windows_canary.api-probes.example.json"
    parsed = windows_canary.CanaryConfig.from_mapping(json.loads(example.read_text()))
    assert parsed.api_live_probes == (
        {"scope": "session_info", "action": "get_property", "member_name": "Client"},
    )


def test_allowlisted_example_is_a_valid_non_production_canary_config() -> None:
    example = Path(__file__).parents[1] / "examples" / "windows_canary.allowlisted.example.json"
    parsed = windows_canary.CanaryConfig.from_mapping(json.loads(example.read_text()))
    assert parsed.mode == "allowlisted"
    assert len(parsed.allowed_branches) == 1


def test_plan_rescan_is_rejected_even_when_cheap_execution_reports_success(
    tmp_path: Path,
) -> None:
    cfg = write_config(tmp_path, config(plan=True))
    output = tmp_path / "result.jsonl"
    transport = FakeTransport(with_plan=True)
    original = transport.__call__

    class RescanningTransport:
        def __call__(self, command: str, args: dict[str, Any], **kwargs: Any):
            response = original(command, args, **kwargs)
            if command == "plan_execute":
                response["data"]["result"]["get_screen_count"] = 1
            return response

    code = windows_canary.main(
        ["--config", str(cfg), "--output", str(output), "--allow-fake"],
        platform_name="Darwin",
        transport=RescanningTransport(),
        sleep=lambda _seconds: None,
        now=fixed_now,
    )

    assert code == 2
    assert read_jsonl(output)[-1]["error_code"] == "plan_rescan_detected"


def test_plan_values_are_refused_before_transport(tmp_path: Path) -> None:
    value = config(plan=True)
    value["plan"]["values"] = {"material": "SENSITIVE"}
    cfg = write_config(tmp_path, value)
    output = tmp_path / "result.jsonl"
    transport = FakeTransport(with_plan=True)

    code = windows_canary.main(
        ["--config", str(cfg), "--output", str(output), "--allow-fake"],
        platform_name="Darwin",
        transport=transport,
        now=fixed_now,
    )

    assert code == 2
    assert transport.calls == []
    assert read_jsonl(output)[-1]["error_code"] == "plan_values_forbidden"


def test_resolved_preview_variables_are_refused_before_execution(tmp_path: Path) -> None:
    cfg = write_config(tmp_path, config(plan=True))
    output = tmp_path / "result.jsonl"
    transport = FakeTransport(with_plan=True)
    original = transport.__call__

    class ResolvedPreview:
        def __call__(self, command: str, args: dict[str, Any], **kwargs: Any):
            response = original(command, args, **kwargs)
            if command == "plan_preview":
                response["data"]["plan"]["variables"] = {
                    "material": {"resolved": True, "value": "SENSITIVE"}
                }
            return response

    code = windows_canary.main(
        ["--config", str(cfg), "--output", str(output), "--allow-fake"],
        platform_name="Darwin",
        transport=ResolvedPreview(),
        sleep=lambda _seconds: None,
        now=fixed_now,
    )

    assert code == 2
    assert not any(call[0] == "plan_execute" for call in transport.calls)
    assert read_jsonl(output)[-1]["error_code"] == "resolved_plan_variables_forbidden"


def test_ui_select_node_requires_explicit_plan_opt_in(tmp_path: Path) -> None:
    cfg = write_config(tmp_path, config(plan=True))
    output = tmp_path / "result.jsonl"
    transport = FakeTransport(with_plan=True)
    original = transport.__call__

    class TreePreview:
        def __call__(self, command: str, args: dict[str, Any], **kwargs: Any):
            response = original(command, args, **kwargs)
            if command == "plan_preview":
                response["data"]["plan"]["steps"][0]["command"] = "ui_select_node"
            return response

    code = windows_canary.main(
        ["--config", str(cfg), "--output", str(output), "--allow-fake"],
        platform_name="Darwin",
        transport=TreePreview(),
        sleep=lambda _seconds: None,
        now=fixed_now,
    )

    assert code == 2
    assert not any(call[0] == "plan_execute" for call in transport.calls)
    assert read_jsonl(output)[-1]["error_code"] == "plan_command_forbidden"


def test_ui_select_node_closed_set_allows_explicit_opt_in() -> None:
    windows_canary._assert_safe_plan(
        {
            "steps": [
                {
                    "command": "ui_select_node",
                    "side_effect": "navigation",
                    "approval_required": False,
                    "commit_boundary": False,
                }
            ]
        },
        allow_ui_select_node=True,
    )


@pytest.mark.parametrize(
    "unsafe_step",
    [
        {"command": "ui_press", "side_effect": "commit", "approval_required": True},
        {"command": "ui_press", "side_effect": "navigation", "commit_boundary": True},
        {"command": "ui_press", "side_effect": "navigation", "approval": {"kind": "commit"}},
        {"command": "save", "side_effect": "navigation"},
    ],
)
def test_preview_commit_or_approval_never_reaches_execution(
    tmp_path: Path, unsafe_step: dict[str, Any]
) -> None:
    cfg = write_config(tmp_path, config(plan=True))
    output = tmp_path / "result.jsonl"
    transport = FakeTransport(with_plan=True)
    original = transport.__call__

    class UnsafePreview:
        def __call__(self, command: str, args: dict[str, Any], **kwargs: Any):
            response = original(command, args, **kwargs)
            if command == "plan_preview":
                response["data"]["plan"]["steps"] = [
                    {"step_id": "unsafe", **unsafe_step}
                ]
            return response

    code = windows_canary.main(
        ["--config", str(cfg), "--output", str(output), "--allow-fake"],
        platform_name="Darwin",
        transport=UnsafePreview(),
        sleep=lambda _seconds: None,
        now=fixed_now,
    )

    assert code == 2
    assert not any(call[0] == "plan_execute" for call in transport.calls)
    assert read_jsonl(output)[-1]["error_code"] in {
        "unsafe_plan_action",
        "plan_commit_or_approval_forbidden",
    }
