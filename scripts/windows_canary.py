#!/usr/bin/env python3
"""Safe, config-driven Windows canary for daemon UI-map and plan execution.

The runner deliberately talks only to the persistent daemon.  It never starts
SAP GUI, dismisses security prompts, or falls back to direct COM access.  Tests
inject a transport and use ``--allow-fake`` so no live SAP session is required.
"""

from __future__ import annotations

import argparse
import io
import json
import math
import os
import platform
import re
import sys
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO


Transport = Callable[..., dict[str, Any] | None]
_TCODE = re.compile(r"^[A-Z0-9_/.-]{2,20}$")
_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_UNSAFE_ENGLISH = re.compile(
    r"(?<![A-Za-z0-9_])(save|post(?:ing)?|delete|release|send|print)(?![A-Za-z0-9_])",
    re.IGNORECASE,
)
_UNSAFE_KOREAN = ("저장", "전기", "삭제", "릴리스", "승인", "전송", "인쇄")
_COMMIT_EFFECTS = {"commit", "destructive", "external"}
_TERMINAL_MAP_STATUSES = {"completed", "failed", "cancelled", "needs_user_input", "interrupted"}


class CanaryError(RuntimeError):
    """A value-safe canary failure suitable for JSONL reporting."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class CanaryConfig:
    tcode: str
    map_id: str
    mode: str
    budget: dict[str, Any]
    allowed_actions: tuple[str, ...]
    navigate: bool
    read_only_acknowledged: bool
    non_production_acknowledged: bool
    reversible_actions_acknowledged: bool
    poll_interval_seconds: float
    timeout_seconds: float
    slot_id: str
    plan: dict[str, Any] | None
    existing_job_id: str
    api_live_probes: tuple[dict[str, str], ...]

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "CanaryConfig":
        if not isinstance(raw, Mapping):
            raise CanaryError("config_not_object")
        tcode = str(raw.get("tcode", "")).strip().upper()
        if not _TCODE.fullmatch(tcode):
            raise CanaryError("invalid_tcode")

        mapping = raw.get("mapping", {})
        if not isinstance(mapping, Mapping):
            raise CanaryError("mapping_not_object")
        mode = str(mapping.get("mode", "passive")).strip().lower()
        if mode not in {"passive", "reversible"}:
            raise CanaryError("unsafe_mapping_mode")
        map_id = str(mapping.get("map_id", f"canary-{tcode.lower()}")).strip()
        if not _SAFE_TOKEN.fullmatch(map_id):
            raise CanaryError("invalid_map_id")
        existing_job_id = str(mapping.get("existing_job_id", "")).strip()
        if existing_job_id and not _SAFE_TOKEN.fullmatch(existing_job_id):
            raise CanaryError("invalid_existing_job_id")
        budget = mapping.get("budget", {})
        if not isinstance(budget, Mapping):
            raise CanaryError("budget_not_object")
        allowed_actions = mapping.get("allowed_actions", [])
        if not isinstance(allowed_actions, Sequence) or isinstance(
            allowed_actions, (str, bytes)
        ):
            raise CanaryError("allowed_actions_not_array")
        action_values = tuple(str(item).strip() for item in allowed_actions)
        if any(not item for item in action_values):
            raise CanaryError("empty_allowed_action")

        acknowledgements = raw.get("acknowledgements", {})
        if not isinstance(acknowledgements, Mapping):
            raise CanaryError("acknowledgements_not_object")
        read_only = acknowledgements.get("read_only_tcode") is True
        non_production = acknowledgements.get("non_production_client") is True
        reversible_only = (
            acknowledgements.get("reversible_actions_only") is True
        )
        if not read_only:
            raise CanaryError("read_only_acknowledgement_required")
        if mode == "reversible" and not (non_production and reversible_only):
            raise CanaryError("reversible_acknowledgements_required")

        polling = raw.get("polling", {})
        if not isinstance(polling, Mapping):
            raise CanaryError("polling_not_object")
        interval = _positive_number(
            polling.get("interval_seconds", 0.5), "invalid_poll_interval"
        )
        timeout = _positive_number(
            polling.get("timeout_seconds", 300.0), "invalid_timeout"
        )
        if interval > timeout:
            raise CanaryError("poll_interval_exceeds_timeout")

        slot_id = str(raw.get("slot_id", "ses0")).strip()
        if not _SAFE_TOKEN.fullmatch(slot_id):
            raise CanaryError("invalid_slot_id")

        plan_raw = raw.get("plan")
        plan: dict[str, Any] | None = None
        if plan_raw is not None:
            if not isinstance(plan_raw, Mapping):
                raise CanaryError("plan_not_object")
            if plan_raw.get("enabled", True) is True:
                request = str(plan_raw.get("request", "")).strip()
                if not request:
                    raise CanaryError("plan_request_required")
                values = plan_raw.get("values", {})
                if not isinstance(values, Mapping):
                    raise CanaryError("plan_values_not_object")
                if values:
                    raise CanaryError("plan_values_forbidden")
                if plan_raw.get("approval_grants") not in (None, [], ()):
                    raise CanaryError("plan_approval_grants_forbidden")
                plan = {
                    "request": request,
                    "values": dict(values),
                    "current_state_id": str(
                        plan_raw.get("current_state_id", "")
                    ),
                    "goal_query": str(plan_raw.get("goal_query", "")),
                    "allow_ui_select_node": plan_raw.get("allow_ui_select_node")
                    is True,
                }

        probes_raw = raw.get("api_live_probes", [])
        if not isinstance(probes_raw, Sequence) or isinstance(probes_raw, (str, bytes)):
            raise CanaryError("api_live_probes_not_array")
        probes: list[dict[str, str]] = []
        for item in probes_raw:
            if not isinstance(item, Mapping):
                raise CanaryError("api_live_probe_not_object")
            action = str(item.get("action", "get_property")).strip().lower()
            member_name = str(item.get("member_name", "")).strip()
            element_id = str(item.get("element_id", "")).strip()
            scope = str(item.get("scope", "")).strip()
            if action not in {"get_property", "call_method"}:
                raise CanaryError("api_live_probe_action_invalid")
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", member_name):
                raise CanaryError("api_live_probe_member_invalid")
            if bool(element_id) == bool(scope):
                raise CanaryError("api_live_probe_target_invalid")
            probes.append({
                "action": action, "member_name": member_name,
                **({"element_id": element_id} if element_id else {}),
                **({"scope": scope} if scope else {}),
            })

        candidate = {
            "allowed_actions": action_values,
            "plan_request": plan["request"] if plan else "",
        }
        if _contains_unsafe_action(candidate):
            raise CanaryError("unsafe_action_forbidden")

        return cls(
            tcode=tcode,
            map_id=map_id,
            mode=mode,
            budget=dict(budget),
            allowed_actions=action_values,
            navigate=bool(mapping.get("navigate", True)),
            read_only_acknowledged=read_only,
            non_production_acknowledged=non_production,
            reversible_actions_acknowledged=reversible_only,
            poll_interval_seconds=interval,
            timeout_seconds=timeout,
            slot_id=slot_id,
            plan=plan,
            existing_job_id=existing_job_id,
            api_live_probes=tuple(probes),
        )


class JsonlEmitter:
    """Emit a fixed, value-safe event vocabulary as JSON lines."""

    def __init__(self, stream: TextIO, now: Callable[[], datetime]) -> None:
        self._stream = stream
        self._now = now

    def emit(self, event: str, **fields: Any) -> None:
        record = {
            "timestamp": self._now().astimezone(timezone.utc).isoformat(),
            "event": event,
            **fields,
        }
        self._stream.write(
            json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n"
        )
        self._stream.flush()


class WindowsCanaryRunner:
    """Run one mapping job and, optionally, twenty immutable cheap plans."""

    def __init__(
        self,
        config: CanaryConfig,
        *,
        transport: Transport,
        emitter: JsonlEmitter,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        id_factory: Callable[[], str] = lambda: uuid.uuid4().hex,
    ) -> None:
        self.config = config
        self.transport = transport
        self.emitter = emitter
        self.monotonic = monotonic
        self.sleep = sleep
        self.run_id = _safe_id(id_factory())
        self.context = {
            "source": "windows_canary",
            "agent_name": "windows-canary",
            "slot_id": config.slot_id,
            "include_post_action_context": False,
            "include_screen_summary": False,
        }

    def run(self) -> dict[str, Any]:
        self.emitter.emit(
            "canary_started", mode=self.config.mode
        )
        self._require_ok("ping", {}, timeout=min(5.0, self.config.timeout_seconds))
        self.emitter.emit("daemon_ready", status="ok")

        if self.config.existing_job_id:
            job_id = self.config.existing_job_id
            self.emitter.emit("map_job_resumed", status="polling")
        else:
            map_started = self._require_ok(
                "map_traverse_start",
                {
                    "tcode": self.config.tcode,
                    "map_id": self.config.map_id,
                    "mode": self.config.mode,
                    "budget": self.config.budget,
                    "allowed_actions": list(self.config.allowed_actions),
                    "navigate": self.config.navigate,
                    "test_client_confirmed": self.config.mode == "reversible",
                },
            )
            job = _required_mapping(map_started, "job", "map_start_shape_invalid")
            job_id = _safe_id(job.get("job_id"))
            self.emitter.emit("map_job_started", status="queued")
        final_job = self._poll_map_job(job_id)
        map_status = str(final_job.get("status", ""))
        stop_reason = _safe_optional_token(final_job.get("stop_reason"))
        self.emitter.emit(
            "map_job_finished", status=map_status, stop_reason=stop_reason
        )
        if map_status != "completed":
            raise CanaryError(f"map_{map_status or 'unknown'}")

        coverage = self._map_report()
        self.emitter.emit("map_coverage", **coverage)

        result: dict[str, Any] = {"map_status": "completed", "coverage": coverage}
        if self.config.api_live_probes:
            result["api_live_probes"] = self._run_api_live_probes()
        if self.config.plan is not None:
            result["benchmark"] = self._preview_and_execute_plan()
        self.emitter.emit("canary_finished", status="success")
        return result

    def _run_api_live_probes(self) -> dict[str, int]:
        completed = 0
        for probe in self.config.api_live_probes:
            response = self._require_ok("api_live_probe", dict(probe))
            if response.get("verified") is not True:
                raise CanaryError("api_live_probe_failed")
            completed += 1
        self.emitter.emit("api_live_probes", requested=len(self.config.api_live_probes), succeeded=completed)
        return {"requested": len(self.config.api_live_probes), "succeeded": completed}

    def _poll_map_job(self, job_id: str) -> Mapping[str, Any]:
        deadline = self.monotonic() + self.config.timeout_seconds
        while True:
            response = self._require_ok("map_job_status", {"job_id": job_id})
            job = _required_mapping(response, "job", "map_status_shape_invalid")
            status = str(job.get("status", ""))
            if status in _TERMINAL_MAP_STATUSES:
                return job
            if self.monotonic() >= deadline:
                raise CanaryError("map_poll_timeout")
            self.sleep(self.config.poll_interval_seconds)

    def _map_report(self) -> dict[str, Any]:
        response = self._require_ok(
            "map_report",
            {"tcode": self.config.tcode, "map_id": self.config.map_id},
        )
        counts = response.get("counts")
        scan_metrics = response.get("scan_metrics")
        fingerprint = str(response.get("map_fingerprint", ""))
        if not isinstance(counts, Mapping) or not isinstance(scan_metrics, Mapping):
            raise CanaryError("map_report_shape_invalid")
        if not re.fullmatch(r"[0-9a-fA-F]{64}", fingerprint):
            raise CanaryError("map_fingerprint_invalid")
        return {
            "map_fingerprint": fingerprint.lower(),
            "counts": _safe_numeric_tree(counts, "invalid_map_count"),
            "scan_metrics": _safe_numeric_tree(
                scan_metrics, "invalid_scan_metric"
            ),
        }

    def _preview_and_execute_plan(self) -> dict[str, Any]:
        assert self.config.plan is not None
        preview = self._require_ok(
            "plan_preview",
            {
                "request": self.config.plan["request"],
                "tcode": self.config.tcode,
                "map_id": self.config.map_id,
                "values": self.config.plan["values"],
                "current_state_id": self.config.plan["current_state_id"],
                "goal_query": self.config.plan["goal_query"],
            },
        )
        validation = _required_mapping(
            preview, "validation", "plan_validation_shape_invalid"
        )
        plan = _required_mapping(preview, "plan", "plan_shape_invalid")
        plan_ref = _required_mapping(preview, "plan_ref", "plan_ref_missing")
        if validation.get("valid") is not True:
            raise CanaryError("plan_invalid")
        if plan.get("unresolved_inputs"):
            raise CanaryError("plan_unresolved_inputs_forbidden")
        _reject_resolved_plan_variables(plan.get("variables"))
        if validation.get("requires_approval") is True or validation.get(
            "required_approvals"
        ):
            raise CanaryError("plan_approval_forbidden")
        _assert_safe_plan(
            plan,
            allow_ui_select_node=bool(
                self.config.plan["allow_ui_select_node"]
            ),
        )
        plan_id = _safe_id(plan_ref.get("plan_id"))
        plan_hash = str(plan_ref.get("plan_hash", ""))
        if not re.fullmatch(r"[0-9a-fA-F]{64}", plan_hash):
            raise CanaryError("invalid_plan_hash")
        self.emitter.emit(
            "plan_previewed", status="safe", step_count=len(plan.get("steps", []))
        )

        warmup = self._execute_plan_once(
            plan_id,
            plan_hash,
            execution_id=f"canary-{self.run_id}-warmup",
        )
        if not warmup["success"]:
            raise CanaryError("plan_warmup_failed")
        if warmup["full_screen_scans"]:
            raise CanaryError("plan_rescan_detected")
        self.emitter.emit(
            "plan_warmup",
            status="success",
            drift_recovered=warmup["drift"],
        )

        durations: list[float] = []
        drifted = 0
        succeeded = 0
        full_screen_scans = 0
        for index in range(1, 21):
            started = self.monotonic()
            outcome = self._execute_plan_once(
                plan_id,
                plan_hash,
                execution_id=f"canary-{self.run_id}-measured-{index:02d}",
            )
            elapsed_ms = max(0.0, (self.monotonic() - started) * 1000.0)
            durations.append(elapsed_ms)
            success = bool(outcome["success"])
            if success:
                succeeded += 1
            drift = bool(outcome["drift"])
            drifted += int(drift)
            scan_count = int(outcome["full_screen_scans"])
            full_screen_scans += scan_count
            self.emitter.emit(
                "plan_execution",
                sequence=index,
                status="success" if success else "failed",
                duration_ms=round(elapsed_ms, 3),
                drift_recovered=drift,
                full_screen_scans=scan_count,
            )
            if not success:
                raise CanaryError("plan_execution_failed")
            if scan_count:
                # The target goal is the daemon's cheap path: every command is
                # precompiled from the map and no whole-screen rescan is
                # allowed during a benchmarked execution.
                raise CanaryError("plan_rescan_detected")

        summary = {
            "samples": len(durations),
            "successes": succeeded,
            "success_rate": succeeded / len(durations),
            "mean_ms": round(sum(durations) / len(durations), 3),
            "p50_ms": round(_percentile(durations, 0.50), 3),
            "p95_ms": round(_percentile(durations, 0.95), 3),
            "drift_count": drifted,
            "drift_rate": drifted / len(durations),
            "full_screen_scans": full_screen_scans,
        }
        self.emitter.emit("plan_benchmark", **summary)
        return summary

    def _execute_plan_once(
        self, plan_id: str, plan_hash: str, *, execution_id: str
    ) -> dict[str, bool | int]:
        response = self._require_ok(
            "plan_execute",
            {
                "plan_id": plan_id,
                "plan_hash": plan_hash,
                "execution_id": execution_id,
                "verify_mode": "cheap",
            },
        )
        execution = response.get("result")
        inner_status = (
            str(execution.get("status", ""))
            if isinstance(execution, Mapping)
            else ""
        )
        if not isinstance(execution, Mapping):
            raise CanaryError("plan_execution_shape_invalid")
        full_screen_scans = execution.get("get_screen_count", 0)
        if (
            isinstance(full_screen_scans, bool)
            or not isinstance(full_screen_scans, int)
            or full_screen_scans < 0
        ):
            raise CanaryError("plan_scan_count_invalid")
        return {
            "success": str(response.get("status", "")) == "completed"
            and inner_status == "success",
            "drift": _has_recovery(execution),
            "full_screen_scans": full_screen_scans,
        }

    def _require_ok(
        self, command: str, args: dict[str, Any], *, timeout: float | None = None
    ) -> dict[str, Any]:
        try:
            envelope = self.transport(
                command,
                args,
                timeout=timeout or self.config.timeout_seconds,
                context=self.context,
            )
        except Exception as exc:
            raise CanaryError("daemon_transport_error") from exc
        if envelope is None:
            raise CanaryError("daemon_unavailable")
        if not isinstance(envelope, Mapping):
            raise CanaryError("daemon_response_invalid")
        if envelope.get("ok") is not True:
            if envelope.get("approval_required") is True:
                raise CanaryError("daemon_approval_forbidden")
            raise CanaryError("daemon_command_failed")
        data = envelope.get("data")
        if not isinstance(data, Mapping):
            raise CanaryError("daemon_data_invalid")
        return dict(data)


def _assert_safe_plan(
    plan: Mapping[str, Any], *, allow_ui_select_node: bool = False
) -> None:
    if _contains_unsafe_action(plan):
        raise CanaryError("unsafe_plan_action")
    steps = plan.get("steps")
    if not isinstance(steps, list) or not steps:
        raise CanaryError("plan_steps_missing")
    for step in steps:
        if not isinstance(step, Mapping):
            raise CanaryError("plan_step_shape_invalid")
        effect = str(step.get("side_effect", "")).strip().lower()
        if (
            effect in _COMMIT_EFFECTS
            or step.get("commit_boundary") is True
            or step.get("approval_required") is True
            or step.get("approval")
        ):
            raise CanaryError("plan_commit_or_approval_forbidden")
        command = str(step.get("command", "")).strip().lower()
        command_allowed = (
            command == "run"
            or command.startswith("ui_read")
            or command in {"ui_inspect", "ui_get_property"}
            or (command == "ui_select_node" and allow_ui_select_node)
        )
        if not command_allowed:
            raise CanaryError("plan_command_forbidden")


def _reject_resolved_plan_variables(raw: Any) -> None:
    if raw in (None, {}):
        return
    if not isinstance(raw, Mapping):
        raise CanaryError("plan_variables_shape_invalid")
    for variable in raw.values():
        if not isinstance(variable, Mapping):
            raise CanaryError("plan_variables_shape_invalid")
        value = variable.get("value")
        if variable.get("resolved") is True or value not in (None, "", "***REDACTED***"):
            raise CanaryError("resolved_plan_variables_forbidden")


def _contains_unsafe_action(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(
            _contains_unsafe_action(key) or _contains_unsafe_action(item)
            for key, item in value.items()
        )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return any(_contains_unsafe_action(item) for item in value)
    if not isinstance(value, str):
        return False
    normalized = value.strip()
    return bool(
        _UNSAFE_ENGLISH.search(normalized)
        or any(word in normalized for word in _UNSAFE_KOREAN)
        or re.search(r"(?:send)?vkey\s*\(?\s*(?:11|27)\b", normalized, re.I)
    )


def _has_recovery(execution: Any) -> bool:
    if not isinstance(execution, Mapping):
        return False
    traces = execution.get("traces", [])
    return isinstance(traces, list) and any(
        isinstance(trace, Mapping) and bool(trace.get("recovery")) for trace in traces
    )


def _required_mapping(
    value: Mapping[str, Any], key: str, error_code: str
) -> Mapping[str, Any]:
    item = value.get(key)
    if not isinstance(item, Mapping):
        raise CanaryError(error_code)
    return item


def _safe_id(value: Any) -> str:
    text = str(value or "").strip()
    if not _SAFE_TOKEN.fullmatch(text):
        raise CanaryError("unsafe_identifier")
    return text


def _safe_optional_token(value: Any) -> str:
    text = str(value or "").strip()
    return text if not text or _SAFE_TOKEN.fullmatch(text) else "unknown"


def _non_negative_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CanaryError("invalid_coverage_counter")
    return value


def _safe_numeric_tree(value: Mapping[str, Any], error_code: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for raw_key, raw_value in value.items():
        key = str(raw_key)
        if not _SAFE_TOKEN.fullmatch(key):
            raise CanaryError(error_code)
        if isinstance(raw_value, Mapping):
            result[key] = _safe_numeric_tree(raw_value, error_code)
        elif (
            isinstance(raw_value, bool)
            or not isinstance(raw_value, (int, float))
            or not math.isfinite(float(raw_value))
            or raw_value < 0
        ):
            raise CanaryError(error_code)
        else:
            result[key] = raw_value
    return result


def _positive_number(value: Any, error_code: str) -> float:
    if isinstance(value, bool):
        raise CanaryError(error_code)
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise CanaryError(error_code) from exc
    if not math.isfinite(result) or result <= 0:
        raise CanaryError(error_code)
    return result


def _percentile(values: Sequence[float], percentile: float) -> float:
    ordered = sorted(float(item) for item in values)
    if not ordered:
        raise CanaryError("benchmark_samples_missing")
    return ordered[math.ceil(percentile * len(ordered)) - 1]


def _default_transport(
    command: str,
    args: dict[str, Any],
    *,
    timeout: float,
    context: dict[str, Any],
) -> dict[str, Any] | None:
    from sap_cli.daemon import send_command

    return send_command(command, args, timeout=timeout, context=context)


def load_config(path: str | os.PathLike[str]) -> CanaryConfig:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CanaryError("config_read_failed") from exc
    return CanaryConfig.from_mapping(raw)


def _open_output(path: str | None) -> tuple[TextIO, bool]:
    if not path or path == "-":
        return sys.stdout, False
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(
        target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600
    )
    try:
        os.chmod(target, 0o600)
        return io.TextIOWrapper(
            os.fdopen(descriptor, "wb", closefd=True), encoding="utf-8"
        ), True
    except Exception:
        os.close(descriptor)
        raise


def _write_sidecar_reports(output: str, report: Mapping[str, Any]) -> None:
    if not output or output == "-":
        return
    target = Path(output).expanduser()
    stem = target.with_suffix("") if target.suffix else target
    json_path = stem.with_name(stem.name + ".report.json")
    markdown_path = stem.with_name(stem.name + ".report.md")
    payload = json.dumps(
        {"schema_version": "sap-canary.report.v1", **dict(report)},
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
    ) + "\n"
    benchmark = report.get("benchmark")
    lines = [
        "# SAP GUI Windows Canary Report",
        "",
        f"- Status: {report.get('status', 'UNKNOWN')}",
        f"- Map status: {report.get('map_status', '')}",
    ]
    if isinstance(benchmark, Mapping):
        lines.extend(
            [
                f"- Samples: {benchmark.get('samples', 0)}",
                f"- Success rate: {benchmark.get('success_rate', 0)}",
                f"- p50 ms: {benchmark.get('p50_ms', 0)}",
                f"- p95 ms: {benchmark.get('p95_ms', 0)}",
                f"- Drift rate: {benchmark.get('drift_rate', 0)}",
                f"- Full screen scans: {benchmark.get('full_screen_scans', 0)}",
            ]
        )
    if report.get("error_code"):
        lines.append(f"- Error code: {report['error_code']}")
    _write_private_text(json_path, payload)
    _write_private_text(markdown_path, "\n".join(lines) + "\n")


def _write_private_text(path: Path, value: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", closefd=True) as stream:
            stream.write(value)
        os.chmod(path, 0o600)
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the safe Windows SAP canary")
    parser.add_argument("--config", required=True, help="canary JSON configuration")
    parser.add_argument("--output", default="-", help="sanitized JSONL path or -")
    parser.add_argument(
        "--allow-fake",
        action="store_true",
        help="permit a non-Windows injected/fake transport (tests only)",
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    transport: Transport | None = None,
    platform_name: str | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    id_factory: Callable[[], str] = lambda: uuid.uuid4().hex,
) -> int:
    args = build_parser().parse_args(argv)
    stream, should_close = _open_output(args.output)
    emitter = JsonlEmitter(stream, now)
    try:
        config = load_config(args.config)
        current_platform = platform_name or platform.system()
        if current_platform != "Windows" and not args.allow_fake:
            raise CanaryError("windows_live_required")
        if current_platform != "Windows" and transport is None:
            raise CanaryError("fake_transport_required")
        runner = WindowsCanaryRunner(
            config,
            transport=transport or _default_transport,
            emitter=emitter,
            monotonic=monotonic,
            sleep=sleep,
            id_factory=id_factory,
        )
        result = runner.run()
        _write_sidecar_reports(
            args.output,
            {
                "status": "PASS",
                "map_status": result.get("map_status", ""),
                "coverage": result.get("coverage", {}),
                "api_live_probes": result.get("api_live_probes", {}),
                "benchmark": result.get("benchmark", {}),
            },
        )
        return 0
    except CanaryError as exc:
        emitter.emit("canary_failed", status="failed", error_code=exc.code)
        _write_sidecar_reports(
            args.output,
            {"status": "FAIL", "map_status": "", "error_code": exc.code},
        )
        return 2
    finally:
        if should_close:
            stream.close()


if __name__ == "__main__":
    raise SystemExit(main())
