"""Single-call, pre-authorized execution of immutable SAP GUI plans."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
import time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from sap_cli.planning.preflight import preflight_plan, strict_substitute
from sap_cli.planning.recovery import (
    BackoffPolicy,
    RecoveryBudget,
    RecoveryErrorKind,
    RetryPolicy,
    classify_execution_error,
)
from sap_cli.planning.safety import runtime_safety_errors
from sap_cli.planning.schema import ExecutionPlan, PlanStep
from sap_cli.ui_map.fingerprint import compute_map_fingerprint
from sap_cli.ui_map.schema import SideEffectClass, UiMap


VerificationMode = Literal["cheap", "full"]
_COMMIT_EFFECTS = {
    SideEffectClass.COMMIT,
    SideEffectClass.DESTRUCTIVE,
    SideEffectClass.EXTERNAL,
}
_NO_ROUNDTRIP = {
    "ui_set",
    "ui_read",
    "ui_read_cell",
    "ui_read_table",
    "ui_read_tree",
    "ui_inspect",
    "ui_get_property",
}


class StepTrace(BaseModel):
    """A secret-safe trace for one step; raw parameters are never retained."""

    model_config = ConfigDict(frozen=True)
    step_id: str
    order: int
    command: str
    status: Literal["success", "failed"]
    attempts: int = 1
    params: dict[str, Any] = Field(default_factory=dict)
    result: Any = None
    probe: dict[str, Any] = Field(default_factory=dict)
    failure_code: str = ""
    recovery: dict[str, Any] = Field(default_factory=dict)


class PlanExecutionResult(BaseModel):
    """Serializable result of a blocked, failed, or completed batch."""

    model_config = ConfigDict(frozen=True)
    status: Literal["blocked", "failed", "success"]
    plan_id: str
    plan_hash: str
    map_id: str
    map_version: int
    errors: list[str] = Field(default_factory=list)
    traces: list[StepTrace] = Field(default_factory=list)
    final_probe: dict[str, Any] = Field(default_factory=dict)
    commit_attempted: bool = False
    get_screen_count: int = 0


class PlanExecutor:
    """Execute a fully compiled plan after every non-SAP gate has passed."""

    def __init__(
        self,
        *,
        get_sap: Callable[[], Any],
        map_store: Any,
        auth_hook: Callable[[str, str, dict[str, Any], PlanStep], Any],
        probe: Callable[[Any, bool], dict[str, Any]] | None = None,
        execute_action: Callable[[str, dict[str, Any], Any, PlanStep], Any] | None = None,
        execute_set_group: Callable[
            [list[tuple[PlanStep, dict[str, Any]]], Any], list[Any]
        ]
        | None = None,
        retry_policy: RetryPolicy | None = None,
        backoff_policy: BackoffPolicy | None = None,
        sleep: Callable[[float], None] = time.sleep,
        targeted_locator_recovery: Callable[
            [PlanStep, dict[str, Any], Any], dict[str, Any] | None
        ]
        | None = None,
        full_rescan_recovery: Callable[
            [PlanStep, dict[str, Any], Any], dict[str, Any] | None
        ]
        | None = None,
    ) -> None:
        self._get_sap = get_sap
        self._map_store = map_store
        self._auth_hook = auth_hook
        self._probe = probe or _default_probe
        self._execute_action = execute_action or _default_execute_action
        self._execute_set_group = execute_set_group or _default_execute_set_group
        self._retry_policy = retry_policy or RetryPolicy()
        self._backoff_policy = backoff_policy or BackoffPolicy()
        self._sleep = sleep
        self._targeted_locator_recovery = (
            targeted_locator_recovery or _default_targeted_locator_recovery
        )
        self._full_rescan_recovery = (
            full_rescan_recovery or _default_full_rescan_recovery
        )

    def execute(
        self,
        plan: ExecutionPlan,
        *,
        approval_grants: Iterable[str] = (),
        verification: VerificationMode = "cheap",
    ) -> PlanExecutionResult:
        """Run the plan once; blocked preflight paths obtain no SAP wrapper."""

        if verification not in {"cheap", "full"}:
            return self._blocked(plan, ["invalid verification mode"])
        if not plan.verify_hash():
            return self._blocked(plan, ["plan hash verification failed"])

        freshness_error = self._latest_map_error(plan)
        if freshness_error:
            return self._blocked(plan, [freshness_error])

        try:
            ui_map = self._load_exact_map(plan)
        except Exception:
            return self._blocked(plan, ["exact UI map version could not be loaded"])
        map_errors = self._map_errors(plan, ui_map)
        if map_errors:
            return self._blocked(plan, map_errors)

        closed_errors = [
            f"step {step.step_id}: command is outside the closed execution set"
            for step in plan.steps
            if step.command != "run" and not step.command.startswith("ui_")
        ]
        if closed_errors:
            return self._blocked(plan, closed_errors)

        safety_errors = [
            error for step in plan.steps for error in runtime_safety_errors(step, ui_map)
        ]
        if safety_errors:
            return self._blocked(plan, safety_errors)

        preflight = preflight_plan(plan, authorization_hook=self._auth_hook)
        if not preflight.valid:
            return self._blocked(plan, list(preflight.errors))
        required_approvals = set(preflight.required_approvals)
        if not any(step.command == "run" for step in plan.steps):
            reference_step = plan.steps[0] if plan.steps else PlanStep(
                step_id="authorize-tcode",
                order=1,
                command="run",
                tool="sap_run",
                params={"tcode": plan.tcode},
            )
            try:
                tcode_decision = self._auth_hook(
                    "run", "sap_run", {"tcode": plan.tcode}, reference_step
                )
            except Exception:
                return self._blocked(plan, ["transaction authorization preflight failed"])
            allowed, approval = _decision_flags(tcode_decision)
            if not allowed:
                return self._blocked(plan, ["transaction authorization denied"])
            if approval:
                required_approvals.add("authorize-tcode")
        missing_grants = sorted(required_approvals - set(approval_grants))
        if missing_grants:
            return self._blocked(
                plan,
                [f"approval grant missing for step: {step_id}" for step_id in missing_grants],
            )

        values = {
            name: variable.value
            for name, variable in plan.variables.items()
            if variable.resolved
        }
        sensitive_names = {
            name for name, variable in plan.variables.items() if variable.sensitive
        }
        secrets = [
            variable.value
            for variable in plan.variables.values()
            if variable.sensitive and variable.resolved and variable.value is not None
        ]
        try:
            raw_params = {
                step.step_id: strict_substitute(
                    step.params,
                    values,
                    sensitive_names=sensitive_names,
                    redact_sensitive=False,
                )
                for step in plan.steps
            }
        except ValueError:
            return self._blocked(plan, ["strict variable substitution failed"])

        # This is deliberately the first SAP access in the function.
        sap = self._get_sap()
        traces: list[StepTrace] = []
        commit_attempted = False
        recovery_budget = RecoveryBudget()
        screen_count = 0
        scope_error = self._live_scope_error(sap, plan)
        if scope_error:
            return self._failed(
                plan,
                [scope_error],
                traces,
                {},
                commit_attempted,
                screen_count,
            )
        initial = self._safe_probe(sap, full=False)
        initial_error = _initial_probe_error(initial, plan, ui_map)
        if not initial_error:
            first = plan.steps[0] if plan.steps else None
            if first is None or first.command != "run":
                initial_error = self._targeted_state_error(
                    sap, ui_map.states.get(plan.start_state_id)
                )
        if initial_error:
            return self._failed(
                plan,
                [initial_error],
                traces,
                _redact(initial, secrets),
                commit_attempted,
                screen_count,
            )

        batches = _execution_batches(plan.steps)
        last_probed_step_id = ""
        final_probe: dict[str, Any] = {}
        for batch in batches:
            if len(batch) > 1 and all(step.command == "ui_set" for step in batch):
                grouped = [(step, raw_params[step.step_id]) for step in batch]
                try:
                    group_results = self._execute_set_group(grouped, sap)
                    if len(group_results) != len(grouped):
                        raise RuntimeError("set group result length mismatch")
                except Exception:
                    for step, params in grouped:
                        traces.append(
                            StepTrace(
                                step_id=step.step_id,
                                order=step.order,
                                command=step.command,
                                status="failed",
                                attempts=1,
                                params=_redact(params, secrets),
                            )
                        )
                    return self._failed(
                        plan,
                        ["grouped ui_set action failed"],
                        traces,
                        {},
                        commit_attempted,
                        screen_count,
                    )
                for (step, params), action_result in zip(grouped, group_results):
                    traces.append(
                        StepTrace(
                            step_id=step.step_id,
                            order=step.order,
                            command=step.command,
                            status="success",
                            attempts=1,
                            params=_redact(params, secrets),
                            result=_redact(action_result, secrets),
                        )
                    )
                continue
            for step in batch:
                params = raw_params[step.step_id]
                if step.commit_boundary or step.side_effect in _COMMIT_EFFECTS:
                    # Once invoked, the commit outcome may be unknowable.  No retry.
                    commit_attempted = True
                attempt_limit = self._retry_policy.attempt_limit(
                    side_effect=step.side_effect,
                    idempotent=step.idempotent,
                    requested_attempts=step.retry,
                    commit_boundary=step.commit_boundary,
                    commit_attempted=commit_attempted,
                )
                attempts = 0
                failure_kind = None
                recovery_trace: dict[str, Any] = {}
                while attempts < attempt_limit:
                    attempts += 1
                    try:
                        action_result = self._execute_action(
                            step.command, params, sap, step
                        )
                        failure_kind = None
                        break
                    except Exception as error:
                        failure_kind = classify_execution_error(error)
                        if (
                            failure_kind is RecoveryErrorKind.LOCATOR_DRIFT
                            and attempts < attempt_limit
                        ):
                            recovered = self._recover_locator(
                                step,
                                params,
                                sap,
                                recovery_budget,
                                allow_full_rescan=plan.scan_policy != "fast",
                            )
                            if recovered is not None:
                                try:
                                    decision = self._auth_hook(
                                        step.command, step.tool, recovered, step
                                    )
                                except Exception:
                                    failure_kind = RecoveryErrorKind.PERMANENT
                                    break
                                allowed, approval = _decision_flags(decision)
                                if not allowed or (
                                    approval and step.step_id not in set(approval_grants)
                                ):
                                    failure_kind = RecoveryErrorKind.PERMANENT
                                    break
                                params = recovered
                                raw_params[step.step_id] = recovered
                                recovery_trace = recovery_budget.snapshot()
                                continue
                        if (
                            attempts >= attempt_limit
                            or not self._retry_policy.retryable(failure_kind)
                        ):
                            break
                        self._backoff_policy.wait(attempts, sleep=self._sleep)
                if failure_kind is not None:
                    traces.append(
                        StepTrace(
                            step_id=step.step_id,
                            order=step.order,
                            command=step.command,
                            status="failed",
                            attempts=attempts,
                            params=_redact(params, secrets),
                            failure_code=failure_kind.value,
                            recovery=recovery_trace,
                        )
                    )
                    return self._failed(
                        plan,
                        [f"step {step.step_id}: action failed"],
                        traces,
                        {},
                        commit_attempted,
                        screen_count,
                    )
                traces.append(
                    StepTrace(
                        step_id=step.step_id,
                        order=step.order,
                        command=step.command,
                        status="success",
                        attempts=attempts,
                        params=_redact(params, secrets),
                        result=_redact(action_result, secrets),
                        recovery=recovery_trace,
                    )
                )

            # A set group has no server roundtrip and intentionally has no
            # intermediate probe.  Every other action batch is one step.
            final_step = batch[-1]
            if _requires_probe(final_step.command):
                observed = self._safe_probe(sap, full=False)
                error = _probe_error(observed, final_step, ui_map)
                expected_state = (
                    ui_map.states.get(plan.start_state_id)
                    if final_step.command == "run"
                    else _target_state(ui_map, final_step)
                    if final_step.map_transition_id
                    else None
                )
                if not error and final_step.command == "run":
                    error = _observed_state_error(
                        observed, expected_state, "TCode start"
                    )
                if not error and expected_state is not None:
                    error = self._targeted_state_error(sap, expected_state)
                if (
                    error == "expected SAP screen transition did not occur"
                    and plan.scan_policy != "fast"
                    and recovery_budget.consume_full_rescan()
                ):
                    # Re-observe once after the action; never execute the action again.
                    observed = self._safe_probe(sap, full=True)
                    screen_count += 1
                    error = _probe_error(observed, final_step, ui_map)
                traces[-1] = traces[-1].model_copy(update={"probe": _redact(observed, secrets)})
                final_probe = observed
                last_probed_step_id = final_step.step_id
                if error:
                    return self._failed(
                        plan,
                        [error],
                        traces,
                        _redact(observed, secrets),
                        commit_attempted,
                        screen_count,
                    )

        if verification == "full":
            if screen_count == 0:
                final_probe = self._safe_probe(sap, full=True)
                screen_count = 1
        elif not plan.steps or last_probed_step_id != plan.steps[-1].step_id:
            final_probe = self._safe_probe(sap, full=False)
        final_error = _final_probe_error(final_probe, plan, ui_map)
        if not final_error:
            final_error = self._targeted_state_error(
                sap, ui_map.states.get(plan.goal_state_id)
            )
        if not final_error:
            final_error = self._goal_component_error(
                sap, plan, ui_map.states.get(plan.goal_state_id)
            )
        if final_error:
            return self._failed(
                plan,
                [final_error],
                traces,
                _redact(final_probe, secrets),
                commit_attempted,
                screen_count,
            )
        return PlanExecutionResult(
            status="success",
            plan_id=plan.plan_id,
            plan_hash=plan.plan_hash,
            map_id=plan.map_id,
            map_version=plan.map_version,
            traces=traces,
            final_probe=_redact(final_probe, secrets),
            commit_attempted=commit_attempted,
            get_screen_count=screen_count,
        )

    def _load_exact_map(self, plan: ExecutionPlan) -> UiMap:
        return self._map_store.load_version(
            plan.map_id,
            plan.map_version,
            tcode=plan.tcode,
            system_id=str(plan.map_scope.get("system_id", "")),
            client=str(plan.map_scope.get("client", "")),
            language=str(plan.map_scope.get("language", "")),
        )

    def _latest_map_error(self, plan: ExecutionPlan) -> str:
        """Fail closed when a newer snapshot superseded the compiled plan."""

        latest_head = getattr(self._map_store, "latest_head", None)
        if not callable(latest_head):
            return ""
        try:
            head = latest_head(
                plan.map_id,
                tcode=plan.tcode,
                system_id=str(plan.map_scope.get("system_id", "")),
                client=str(plan.map_scope.get("client", "")),
                language=str(plan.map_scope.get("language", "")),
            )
            latest_version = int(head["version"])
        except Exception:
            return "latest UI map head could not be verified"
        if latest_version != plan.map_version:
            return "stale plan: a newer UI map version is available"
        return ""

    def _recover_locator(
        self,
        step: PlanStep,
        params: dict[str, Any],
        sap: Any,
        budget: RecoveryBudget,
        *,
        allow_full_rescan: bool,
    ) -> dict[str, Any] | None:
        if (
            self._targeted_locator_recovery is not None
            and budget.consume_targeted_inspect()
        ):
            try:
                recovered = self._targeted_locator_recovery(
                    step, dict(params), sap
                )
            except Exception:
                recovered = None
            if recovered:
                return dict(recovered)
        if (
            allow_full_rescan
            and self._full_rescan_recovery is not None
            and budget.consume_full_rescan()
        ):
            try:
                recovered = self._full_rescan_recovery(step, dict(params), sap)
            except Exception:
                recovered = None
            if recovered:
                return dict(recovered)
        return None

    @staticmethod
    def _map_errors(plan: ExecutionPlan, ui_map: UiMap) -> list[str]:
        errors: list[str] = []
        manifest = ui_map.manifest
        if manifest.map_id != plan.map_id or manifest.map_version != plan.map_version:
            errors.append("UI map identity/version mismatch")
        expected_scope = {
            "system_id": manifest.system_id,
            "client": manifest.client,
            "language": manifest.language,
        }
        if manifest.tcode.upper() != plan.tcode.upper() or any(
            str(plan.map_scope.get(key, "")) != str(value)
            for key, value in expected_scope.items()
        ):
            errors.append("UI map scope mismatch")
        if compute_map_fingerprint(ui_map) != plan.map_fingerprint:
            errors.append("UI map fingerprint mismatch")
        for step in plan.steps:
            if step.map_id and step.map_id != plan.map_id:
                errors.append(f"step {step.step_id}: map id mismatch")
            if step.map_version is not None and step.map_version != plan.map_version:
                errors.append(f"step {step.step_id}: map version mismatch")
            if step.map_fingerprint and step.map_fingerprint != plan.map_fingerprint:
                errors.append(f"step {step.step_id}: map fingerprint mismatch")
        return errors

    def _safe_probe(self, sap: Any, *, full: bool) -> dict[str, Any]:
        try:
            value = self._probe(sap, full)
            return value if isinstance(value, dict) else {"probe_error": True}
        except Exception:
            return {"probe_error": True}

    @staticmethod
    def _targeted_state_error(sap: Any, state: Any) -> str:
        """Verify saved tabs/subscreens with targeted reads, never a full scan."""

        if state is None:
            return "expected UI map state is missing"
        components = {item.logical_id: item for item in state.components}
        getter = getattr(sap, "get_component_property", None)
        for logical_id in state.active_tabs.values():
            component = components.get(logical_id)
            if component is None or not component.sap_id:
                return "expected active SAP tab locator is missing"
            if not callable(getter):
                return "active SAP tab could not be verified"
            try:
                selected = getter(component.sap_id, "Selected")
                if isinstance(selected, Mapping):
                    selected = selected.get("value", selected.get("result"))
            except Exception:
                return "active SAP tab could not be verified"
            if selected is not True:
                return "active SAP tab does not match plan UI map"

        inspect = getattr(sap, "inspect_element", None)
        for logical_id in state.active_subscreens:
            component = components.get(logical_id)
            if component is None or not component.sap_id:
                return "expected active SAP subscreen locator is missing"
            if not callable(inspect):
                return "active SAP subscreen could not be verified"
            try:
                observed = inspect(component.sap_id)
            except Exception:
                return "active SAP subscreen does not match plan UI map"
            if not isinstance(observed, Mapping) or observed.get("visible") is not True:
                return "active SAP subscreen does not match plan UI map"
        return ""

    @staticmethod
    def _goal_component_error(sap: Any, plan: ExecutionPlan, state: Any) -> str:
        """Verify the exact requested goal control without a recursive scan."""

        if not plan.goal_component_id:
            return ""
        if state is None:
            return "goal SAP state is missing"
        component = next(
            (item for item in state.components if item.logical_id == plan.goal_component_id),
            None,
        )
        if component is None or not component.sap_id:
            return "goal SAP component is missing from UI map"
        inspect = getattr(sap, "inspect_element", None)
        if not callable(inspect):
            return "goal SAP component could not be verified"
        try:
            observed = inspect(component.sap_id)
        except Exception:
            return "goal SAP component does not match plan UI map"
        if not isinstance(observed, Mapping) or observed.get("visible") is not True:
            return "goal SAP component does not match plan UI map"
        return ""

    @staticmethod
    def _live_scope_error(sap: Any, plan: ExecutionPlan) -> str:
        """Fail closed when the connected SAP environment differs from the map."""

        expected = {
            "system_id": str(plan.map_scope.get("system_id", "")).strip().upper(),
            "client": str(plan.map_scope.get("client", "")).strip(),
            "language": str(plan.map_scope.get("language", "")).strip().upper(),
        }
        if not any(expected.values()):
            return ""
        try:
            info = _dump(sap.get_session_info())
        except Exception:
            return "live SAP session scope could not be verified"
        if not isinstance(info, Mapping):
            return "live SAP session scope could not be verified"
        actual = {
            "system_id": str(
                info.get("system_name")
                or info.get("system_id")
                or info.get("SystemName")
                or ""
            ).strip().upper(),
            "client": str(info.get("client") or info.get("Client") or "").strip(),
            "language": str(
                info.get("language") or info.get("Language") or ""
            ).strip().upper(),
        }
        for key, wanted in expected.items():
            if wanted and actual[key] != wanted:
                return "live SAP session scope does not match plan UI map"
        return ""

    @staticmethod
    def _blocked(plan: ExecutionPlan, errors: list[str]) -> PlanExecutionResult:
        return PlanExecutionResult(
            status="blocked",
            plan_id=plan.plan_id,
            plan_hash=plan.plan_hash,
            map_id=plan.map_id,
            map_version=plan.map_version,
            errors=errors,
        )

    @staticmethod
    def _failed(
        plan: ExecutionPlan,
        errors: list[str],
        traces: list[StepTrace],
        final_probe: dict[str, Any],
        commit_attempted: bool,
        screen_count: int,
    ) -> PlanExecutionResult:
        return PlanExecutionResult(
            status="failed",
            plan_id=plan.plan_id,
            plan_hash=plan.plan_hash,
            map_id=plan.map_id,
            map_version=plan.map_version,
            errors=errors,
            traces=traces,
            final_probe=final_probe,
            commit_attempted=commit_attempted,
            get_screen_count=screen_count,
        )


def _execution_batches(steps: list[PlanStep]) -> list[list[PlanStep]]:
    batches: list[list[PlanStep]] = []
    for step in steps:
        if (
            step.command == "ui_set"
            and step.side_effect not in _COMMIT_EFFECTS
            and not step.approval_required
            and not step.modal
            and step.expected_from_fingerprint
            and batches
            and all(item.command == "ui_set" for item in batches[-1])
            and all(item.side_effect not in _COMMIT_EFFECTS for item in batches[-1])
            and all(not item.approval_required and not item.modal for item in batches[-1])
            and batches[-1][0].expected_from_fingerprint == step.expected_from_fingerprint
        ):
            batches[-1].append(step)
        else:
            batches.append([step])
    return batches


def _requires_probe(command: str) -> bool:
    return command == "run" or (command.startswith("ui_") and command not in _NO_ROUNDTRIP)


def _default_execute_action(
    command: str, params: dict[str, Any], sap: Any, _step: PlanStep
) -> Any:
    if command == "run":
        sap.run_transaction(str(params["tcode"]))
        return {"executed": str(params["tcode"])}
    if not command.startswith("ui_"):
        raise ValueError("execution plan contains a non-closed command")
    action = command[3:]
    kwargs = {
        key: params[key]
        for key in (
            "node_key",
            "column",
            "row",
            "max_rows",
            "columns",
            "method_args",
            "property_name",
            "property_value",
        )
        if key in params
    }
    return sap.perform_ui_action(
        str(params["element_id"]),
        action,
        None if params.get("value") is None else str(params.get("value")),
        trusted_plan=bool(_step.approval_required),
        **kwargs,
    )


def _default_execute_set_group(
    grouped: list[tuple[PlanStep, dict[str, Any]]], sap: Any
) -> list[Any]:
    """Apply exact saved element IDs without scans, readbacks, or probes."""

    results: list[Any] = []
    for step, params in grouped:
        element_id = str(params["element_id"])
        value = "" if params.get("value") is None else str(params.get("value"))
        component_type = str(
            step.target_locator.get("type")
            or step.target_locator.get("component_type")
            or ""
        )
        if component_type == "GuiCheckBox":
            sap.set_checkbox(
                element_id, value.casefold() not in {"", "0", "false", "off", "no"}
            )
        elif component_type == "GuiRadioButton":
            sap.select_radio(element_id)
        else:
            sap.set_field(element_id, value)
        results.append({"field": element_id, "value_set": value})
    return results


def _default_targeted_locator_recovery(
    step: PlanStep, params: dict[str, Any], sap: Any
) -> dict[str, Any] | None:
    """Try only locators embedded in the immutable plan, without a screen scan."""

    inspect = getattr(sap, "inspect_element", None)
    if not callable(inspect):
        return None
    candidates = step.target_locator.get("alternate_locators", [])
    if not isinstance(candidates, list):
        return None
    current = str(params.get("element_id", ""))
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            continue
        element_id = str(candidate.get("sap_id") or candidate.get("element_id") or "")
        if not element_id or element_id == current:
            continue
        try:
            inspect(element_id)
        except Exception:
            continue
        recovered = dict(params)
        recovered["element_id"] = element_id
        return recovered
    return None


def _default_full_rescan_recovery(
    step: PlanStep, params: dict[str, Any], sap: Any
) -> dict[str, Any] | None:
    """Resolve one logical target from one ephemeral lossless scan."""

    scan = getattr(sap, "scan_ui_state", None)
    if not callable(scan) or not step.target_logical_id:
        return None
    payload = scan()
    if not isinstance(payload, Mapping):
        return None
    state = payload.get("state", payload)
    if not isinstance(state, Mapping):
        return None
    components = state.get("components", [])
    if isinstance(components, Mapping):
        components = list(components.values())
    if not isinstance(components, list):
        return None
    for component in components:
        if not isinstance(component, Mapping):
            continue
        if str(component.get("logical_id", "")) != step.target_logical_id:
            continue
        element_id = str(component.get("sap_id", ""))
        if not element_id:
            continue
        recovered = dict(params)
        recovered["element_id"] = element_id
        return recovered
    return None


def _default_probe(sap: Any, full: bool) -> dict[str, Any]:
    wait = getattr(sap, "wait_until_idle", None)
    if callable(wait) and wait() is False:
        return {"probe_error": True, "busy": True}
    modal_state = getattr(sap, "get_modal_state", None)
    if callable(modal_state):
        modal = _dump(modal_state())
    else:
        modal = {"has_modal": bool(sap.has_modal())}
        if modal["has_modal"]:
            modal["text"] = str(sap.get_modal_text())
    payload = {
        "session": _dump(sap.get_session_info()),
        "status": _dump(sap.get_status()),
        "modal": modal,
    }
    if full:
        payload["screen"] = _dump(sap.get_screen())
    return payload


def _probe_error(
    observed: Mapping[str, Any], step: PlanStep | None, ui_map: UiMap | None = None
) -> str:
    if observed.get("probe_error"):
        return "SAP state probe failed"
    status = observed.get("status") or observed.get("sap_status") or {}
    if str(status.get("type", "")).upper() in {"E", "A"}:
        return "SAP status indicates an error or abort"
    modal = observed.get("modal") or observed.get("sap_modal") or {}
    expected_modal_signature = str(
        (step.modal if step else {}).get("signature", "")
    )
    if expected_modal_signature and not modal.get("has_modal"):
        return "expected SAP modal did not appear"
    if modal.get("has_modal"):
        expected = bool(step and step.modal)
        signature = expected_modal_signature
        actual = str(modal.get("signature", ""))
        if not expected or (signature and signature != actual):
            return "unexpected SAP modal"
    if step is not None and ui_map is not None and step.map_transition_id:
        transition = next(
            (
                item
                for item in ui_map.transitions
                if item.transition_id == step.map_transition_id
            ),
            None,
        )
        target = ui_map.states.get(transition.to_state_id) if transition else None
        session = observed.get("session") or {}
        actual_program = str(
            session.get("program") or session.get("Program") or ""
        ).strip().upper()
        expected_program = str(target.program if target else "").strip().upper()
        if session and expected_program and actual_program != expected_program:
            return "expected SAP program transition did not occur"
        actual_screen = str(session.get("screen_number", "")).lstrip("0") or "0"
        expected_screen = str(target.screen_number).lstrip("0") if target else ""
        if expected_screen and actual_screen != expected_screen:
            return "expected SAP screen transition did not occur"
    return ""


def _target_state(ui_map: UiMap, step: PlanStep) -> Any:
    if not step.map_transition_id:
        return None
    transition = next(
        (
            item
            for item in ui_map.transitions
            if item.transition_id == step.map_transition_id
        ),
        None,
    )
    return ui_map.states.get(transition.to_state_id) if transition else None


def _observed_state_error(
    observed: Mapping[str, Any], state: Any, label: str
) -> str:
    if state is None:
        return f"expected {label} UI map state is missing"
    session = observed.get("session") or {}
    actual_transaction = str(
        session.get("transaction") or session.get("Transaction") or ""
    ).strip().upper()
    if state.tcode and actual_transaction != str(state.tcode).strip().upper():
        return f"{label} SAP transaction does not match UI map"
    actual_program = str(
        session.get("program") or session.get("Program") or ""
    ).strip().upper()
    if state.program and actual_program != str(state.program).strip().upper():
        return f"{label} SAP program does not match UI map"
    raw_screen = str(
        session.get("screen_number") or session.get("ScreenNumber") or ""
    )
    actual_screen = raw_screen.lstrip("0") or ("0" if raw_screen else "")
    expected_screen = str(state.screen_number).lstrip("0")
    if expected_screen and actual_screen != expected_screen:
        return f"{label} SAP screen does not match UI map"
    return ""


def _initial_probe_error(
    observed: Mapping[str, Any], plan: ExecutionPlan, ui_map: UiMap
) -> str:
    error = _probe_error(observed, None, ui_map)
    if error:
        return error
    first = plan.steps[0] if plan.steps else None
    if first is not None and first.command == "run":
        return ""
    session = observed.get("session") or {}
    transaction = str(session.get("transaction", "")).upper()
    if transaction and transaction != plan.tcode.upper():
        return "initial SAP transaction does not match plan"
    start = ui_map.states.get(plan.start_state_id)
    actual_program = str(
        session.get("program") or session.get("Program") or ""
    ).strip().upper()
    expected_program = str(start.program if start else "").strip().upper()
    if session and expected_program and actual_program != expected_program:
        return "initial SAP program does not match plan"
    raw_screen = str(session.get("screen_number", ""))
    actual_screen = raw_screen.lstrip("0") or ("0" if raw_screen else "")
    expected_screen = str(start.screen_number).lstrip("0") if start else ""
    if expected_screen and actual_screen and actual_screen != expected_screen:
        return "initial SAP screen does not match plan"
    return ""


def _final_probe_error(
    observed: Mapping[str, Any], plan: ExecutionPlan, ui_map: UiMap
) -> str:
    last_step = plan.steps[-1] if plan.steps else None
    error = _probe_error(observed, last_step, ui_map)
    if error:
        return error
    session = observed.get("session") or {}
    transaction = str(session.get("transaction", "")).upper()
    if transaction and transaction != plan.tcode.upper():
        return "final SAP transaction does not match plan"
    goal = ui_map.states.get(plan.goal_state_id)
    actual_program = str(
        session.get("program") or session.get("Program") or ""
    ).strip().upper()
    expected_program = str(goal.program if goal else "").strip().upper()
    if session and expected_program and actual_program != expected_program:
        return "final SAP program does not match plan goal"
    raw_screen = str(session.get("screen_number", ""))
    actual_screen = raw_screen.lstrip("0") or ("0" if raw_screen else "")
    expected_screen = str(goal.screen_number).lstrip("0") if goal else ""
    if expected_screen and actual_screen and actual_screen != expected_screen:
        return "final SAP screen does not match plan goal"
    return ""


def _dump(value: Any) -> Any:
    dumper = getattr(value, "model_dump", None)
    return dumper(mode="json") if callable(dumper) else value


def _redact(value: Any, secrets: list[Any]) -> Any:
    if isinstance(value, str):
        redacted = value
        for secret in secrets:
            if secret not in (None, ""):
                redacted = redacted.replace(str(secret), "***REDACTED***")
        return redacted
    if isinstance(value, Mapping):
        return {key: _redact(item, secrets) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item, secrets) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact(item, secrets) for item in value)
    if any(value == secret for secret in secrets):
        return "***REDACTED***"
    return value


def _decision_flags(decision: Any) -> tuple[bool, bool]:
    if isinstance(decision, bool):
        return decision, False
    if isinstance(decision, Mapping):
        return (
            bool(decision.get("allowed", decision.get("authorized", False))),
            bool(
                decision.get(
                    "approval_required", decision.get("requires_approval", False)
                )
            ),
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
    )


__all__ = ["PlanExecutionResult", "PlanExecutor", "StepTrace", "VerificationMode"]
