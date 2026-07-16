"""Read-only execution-plan preview dispatcher."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping
from typing import Any, Callable

from sap_cli.auth.policy import Policy
from sap_cli.auth.preflight import preflight_tool_call
from sap_cli.planning.compiler import PlanCompiler
from sap_cli.planning.executor import PlanExecutor
from sap_cli.planning.approvals import get_plan_approval_store
from sap_cli.planning.preflight import preflight_plan
from sap_cli.planning.runtime import (
    ExecutionConflictError,
    get_plan_runtime,
    try_get_plan_runtime,
)
from sap_cli.planning.schema import NaturalLanguageRequest
from sap_cli.jobs import try_get_job_coordinator, try_get_plan_job_ledger
from sap_cli.recipe import RecipeStore
from sap_cli.ui_map.store import UiMapStore

from .ui_maps import default_map_store


PLANNING_COMMANDS = frozenset({"plan_approve", "plan_execute", "plan_preview"})


def is_planning_command(command: str) -> bool:
    return command in PLANNING_COMMANDS


def dispatch_planning_command(
    command: str,
    args: dict[str, Any],
    *,
    get_map_store: Callable[[], UiMapStore] = default_map_store,
    get_recipe_store: Callable[[], Any] = RecipeStore,
    policy: Policy | None = None,
    agent_name: str | None = None,
    enforce_auth: bool = True,
    ctx: Any = None,
    get_sap: Callable[[str], Any] | None = None,
    authorize_slot: Callable[[str, str], None] | None = None,
) -> dict[str, Any]:
    if command == "plan_approve":
        return _dispatch_plan_approve(args, ctx=ctx)
    if command == "plan_execute":
        return _dispatch_plan_execute(
            args,
            policy=policy,
            agent_name=agent_name,
            enforce_auth=enforce_auth,
            ctx=ctx,
            get_map_store=get_map_store,
            get_sap=get_sap,
            authorize_slot=authorize_slot,
        )
    if command != "plan_preview":
        raise ValueError(f"unsupported planning command: {command}")
    text = str(args.get("request") or args.get("text") or "").strip()
    if not text:
        raise ValueError("request is required")
    supplied = args.get("values") or args.get("supplied_values") or {}
    if not isinstance(supplied, dict):
        raise ValueError("values must be an object")
    request = NaturalLanguageRequest(
        text=text,
        request_id=str(args.get("request_id", "")),
        system_id=str(args.get("system_id", "")),
        client=str(args.get("client", "")),
        tcode_hint=str(args.get("tcode", "")),
        supplied_values=supplied,
        success_criteria=list(args.get("success_criteria") or []),
        context={"map_id": str(args.get("map_id", ""))},
    )
    runtime_versions = {
        key: str(args[key])
        for key in ("api_version", "gui_version", "application_release", "schema_version")
        if args.get(key)
    }
    result = PlanCompiler(get_map_store(), get_recipe_store()).compile(
        request,
        current_state_id=str(args.get("current_state_id", "")),
        goal_query=str(args.get("goal_query", "")),
        map_id=str(args.get("map_id", "")),
        runtime_versions=runtime_versions,
    )
    validation = result.validation.model_dump(mode="json")
    plan_preflight = None
    transaction_authorization = None
    if result.plan is not None:
        plan_preflight = preflight_plan(
            result.plan,
            authorization_hook=lambda _command, tool, params, _step: preflight_tool_call(
                tool,
                params,
                policy=policy,
                agent_name=agent_name,
                enforce_auth=enforce_auth,
            ),
        )
        validation["valid"] = bool(validation["valid"] and plan_preflight.valid)
        validation["errors"] = list(
            dict.fromkeys([*validation["errors"], *plan_preflight.errors])
        )
        validation["requires_approval"] = bool(
            validation["requires_approval"] or plan_preflight.requires_approval
        )
        validation["required_approvals"] = list(
            dict.fromkeys(
                [
                    *validation["required_approvals"],
                    *plan_preflight.required_approvals,
                ]
            )
        )
        if not any(step.command == "run" for step in result.plan.steps):
            transaction_authorization = preflight_tool_call(
                "sap_run",
                {"tcode": result.plan.tcode},
                policy=policy,
                agent_name=agent_name,
                enforce_auth=enforce_auth,
            )
            if not transaction_authorization.allowed:
                validation["valid"] = False
                validation["errors"] = list(
                    dict.fromkeys(
                        [*validation["errors"], "transaction authorization denied"]
                    )
                )
            if transaction_authorization.approval_required:
                validation["requires_approval"] = True
                validation["required_approvals"] = list(
                    dict.fromkeys(
                        [*validation["required_approvals"], "authorize-tcode"]
                    )
                )
    plan_ref = None
    activation = None
    if result.plan is not None and validation["valid"]:
        runtime = try_get_plan_runtime()
        reference = runtime.register_plan(result.plan) if runtime is not None else None
        if reference is not None:
            plan_ref = {
                "plan_id": reference.plan_id,
                "plan_hash": reference.plan_hash,
                "expires_in_seconds": reference.expires_in_seconds,
                "daemon_memory_only": True,
            }
            # Keep the hand-off deliberately small.  An agent can forward this
            # object directly to ``sap_plan_execute`` after the user confirms
            # the preview; it does not need to copy identifiers from the full
            # plan payload or invent an execution ID.
            activation = {
                "tool": "sap_plan_execute",
                "arguments": {"plan_ref": plan_ref, "verify_mode": "cheap"},
                "requires_user_confirmation": True,
            }
    payload: dict[str, Any] = {
        "validation": validation,
        "plan": result.plan.model_dump(mode="json") if result.plan else None,
        "plan_ref": plan_ref,
        "activation": activation,
        "preflight": (
            plan_preflight.model_dump(mode="json") if plan_preflight else None
        ),
        "transaction_authorization": (
            transaction_authorization.as_dict()
            if transaction_authorization is not None
            else None
        ),
    }
    if result.intent is not None:
        sensitive = {
            name
            for name, variable in (result.plan.variables.items() if result.plan else [])
            if variable.sensitive
        } | {
            name
            for name in result.intent.values
            if any(
                marker in name.casefold()
                for marker in ("password", "passwd", "pwd", "secret", "token", "pin", "비밀번호")
            )
        }
        payload["intent"] = {
            "tcode": result.intent.tcode,
            "operation": result.intent.operation,
            "entities": result.intent.entities,
            "values": {
                name: "<redacted>" if name in sensitive else value
                for name, value in result.intent.values.items()
            },
            "ambiguities": result.intent.ambiguities,
            "confidence": result.intent.confidence,
        }
    return payload


def _dispatch_plan_approve(args: dict[str, Any], *, ctx: Any) -> dict[str, Any]:
    """Issue one opaque, daemon-memory approval token for a subject agent."""

    if ctx is None:
        raise RuntimeError("plan approval requires the persistent daemon runtime")
    plan_id = str(args.get("plan_id", "")).strip()
    plan_hash = str(args.get("plan_hash", "")).strip()
    subject_agent = str(args.get("subject_agent", "")).strip()
    raw_steps = args.get("step_ids")
    if not plan_id or not plan_hash or not subject_agent:
        raise ValueError("plan_id, plan_hash, and subject_agent are required")
    if not isinstance(raw_steps, list):
        raise ValueError("step_ids must be an array")
    step_ids = list(dict.fromkeys(str(item).strip() for item in raw_steps))
    if not step_ids or any(not item for item in step_ids):
        raise ValueError("at least one non-empty step_id is required")
    plan = get_plan_runtime().get_plan(plan_id, plan_hash)
    allowed_step_ids = {step.step_id for step in plan.steps} | {"authorize-tcode"}
    unknown = sorted(set(step_ids) - allowed_step_ids)
    if unknown:
        raise ValueError("approval contains a step outside the immutable plan")
    slot_id = str(getattr(ctx, "slot_id", "ses0") or "ses0")
    ttl_raw = args.get("expires_in_seconds")
    ttl = None if ttl_raw in (None, "") else float(ttl_raw)
    receipt = get_plan_approval_store().issue(
        plan_id=plan_id,
        plan_hash=plan_hash,
        step_ids=step_ids,
        subject_agent=subject_agent,
        slot_id=slot_id,
        ttl_seconds=ttl,
    )
    return {
        "approval_token": receipt.token,
        "expires_in_seconds": receipt.expires_in_seconds,
        "daemon_memory_only": True,
        "single_use": True,
    }


def _dispatch_plan_execute(
    args: dict[str, Any],
    *,
    policy: Policy | None,
    agent_name: str | None,
    enforce_auth: bool,
    ctx: Any,
    get_map_store: Callable[[], UiMapStore],
    get_sap: Callable[[str], Any] | None,
    authorize_slot: Callable[[str, str], None] | None,
) -> dict[str, Any]:
    """Execute one daemon-cached immutable plan with idempotent ownership."""

    if ctx is None or get_sap is None:
        raise RuntimeError("plan execution requires the persistent daemon runtime")
    runtime = get_plan_runtime()
    plan_id, plan_hash = _execution_plan_reference(args)
    supplied_execution_id = str(args.get("execution_id", "")).strip()
    # The ID remains caller-controllable for idempotent retries.  For the
    # normal preview -> confirm -> execute flow, generate it here so an agent
    # can activate the supplied plan_ref without manufacturing one itself.
    execution_id = supplied_execution_id or f"plan-{uuid.uuid4().hex}"
    verification = str(args.get("verify_mode", "cheap") or "cheap").lower()
    if verification not in {"cheap", "full"}:
        raise ValueError("verify_mode must be cheap or full")
    if "approval_grants" in args:
        raise ValueError(
            "caller-supplied approval_grants are forbidden; use approval_token"
        )
    approval_token = str(args.get("approval_token", "") or "").strip()
    slot_id = str(getattr(ctx, "slot_id", "ses0") or "ses0")
    effective_agent = str(
        agent_name
        or getattr(ctx, "agent_name", "")
        or (policy.active_agent if policy else "")
        or "anonymous"
    )
    if authorize_slot is not None:
        authorize_slot(slot_id, effective_agent)
    digest = hashlib.sha256(
        json.dumps(
            {
                "plan_id": plan_id,
                "plan_hash": plan_hash,
                "verify_mode": verification,
                "slot_id": slot_id,
                "agent_name": effective_agent,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    try:
        previous = runtime.get_execution(execution_id)
    except KeyError:
        previous = None
    if previous is not None:
        if (
            previous.request_digest != digest
            or previous.slot_id != slot_id
            or previous.agent_name != effective_agent
        ):
            raise ExecutionConflictError(
                "execution_id is already bound to a different request"
            )
        return {
            "execution_id": execution_id,
            "status": previous.status,
            "replayed": True,
            "result": previous.result,
            "error_code": previous.error_code,
        }
    plan = runtime.get_plan(plan_id, plan_hash)
    map_store = get_map_store()
    head = map_store.latest_head(
        plan.map_id,
        tcode=plan.tcode,
        system_id=str(plan.map_scope.get("system_id", "")),
        client=str(plan.map_scope.get("client", "")),
        language=str(plan.map_scope.get("language", "")),
    )
    plan = runtime.validate_plan_map_head(
        plan_id,
        plan_hash,
        head,
        fingerprint=str(head.get("content_fingerprint", "")),
    )
    grants: set[str] = set()
    approval_store = None
    if approval_token:
        approval_store = get_plan_approval_store()
        grants = set(
            approval_store.validate(
                approval_token,
                plan_id=plan_id,
                plan_hash=plan_hash,
                subject_agent=effective_agent,
                slot_id=slot_id,
            )
        )
    coordinator = try_get_job_coordinator()
    ledger = try_get_plan_job_ledger()
    coordinator_id = f"plan:{execution_id}"
    if coordinator is not None:
        lease = coordinator.lease_for_slot(slot_id)
        if lease is not None and lease.job_id != coordinator_id:
            raise RuntimeError("SAP slot is reserved by another daemon job")
    ledger_record = None
    try:
        if coordinator is not None:
            coordinated = coordinator.submit(
                coordinator_id,
                "plan_execute",
                digest,
                slot_id=slot_id,
                agent_name=effective_agent,
            )
            if not coordinated.replayed:
                claimed = coordinator.claim(coordinator_id)
                if claimed is None:
                    coordinator.cancel(coordinator_id)
                    raise RuntimeError("SAP slot is reserved by an earlier daemon job")
        if ledger is not None:
            ledger_record = ledger.create(
                job_id=coordinator_id,
                execution_id=execution_id,
                plan_id=plan_id,
                plan_hash=plan_hash,
                request_digest=digest,
                owner=effective_agent,
                slot=slot_id,
                status="running",
            )
    except Exception:
        if coordinator is not None:
            try:
                current = coordinator.get(coordinator_id)
                if current.status in {"queued", "running"}:
                    coordinator.fail(
                        coordinator_id, error_code="job_coordination_failed"
                    )
            except Exception:
                pass
        if ledger is not None:
            try:
                if ledger_record is None:
                    ledger.create(
                        job_id=coordinator_id,
                        execution_id=execution_id,
                        plan_id=plan_id,
                        plan_hash=plan_hash,
                        request_digest=digest,
                        owner=effective_agent,
                        slot=slot_id,
                        status="failed",
                    )
                elif ledger_record.status in {"queued", "running"}:
                    ledger.update(
                        coordinator_id,
                        status="failed",
                        error_code="job_coordination_failed",
                    )
            except Exception:
                pass
        raise
    try:
        snapshot = runtime.begin_execution(
            execution_id,
            digest,
            slot_id=slot_id,
            agent_name=effective_agent,
        )
    except Exception:
        if coordinator is not None:
            current = coordinator.get(coordinator_id)
            if current.status == "running":
                coordinator.fail(coordinator_id, error_code="plan_runtime_rejected")
        if ledger is not None:
            ledger.update(
                coordinator_id, status="failed", error_code="plan_runtime_rejected"
            )
        raise
    if snapshot.replayed:
        return {
            "execution_id": execution_id,
            "status": snapshot.status,
            "replayed": True,
            "result": snapshot.result,
            "error_code": snapshot.error_code,
        }
    if approval_token:
        assert approval_store is not None
        try:
            grants = set(
                approval_store.consume(
                    approval_token,
                    plan_id=plan_id,
                    plan_hash=plan_hash,
                    subject_agent=effective_agent,
                    slot_id=slot_id,
                )
            )
        except Exception:
            failed = runtime.fail_execution(
                execution_id,
                error_code="approval_token_rejected",
                slot_id=slot_id,
                agent_name=effective_agent,
            )
            if coordinator is not None:
                current = coordinator.get(coordinator_id)
                if current.status == "running":
                    coordinator.fail(
                        coordinator_id, error_code="approval_token_rejected"
                    )
            if ledger is not None:
                ledger.update(
                    coordinator_id,
                    status="failed",
                    error_code="approval_token_rejected",
                )
            raise ValueError(failed.error_code) from None

    def owned_sap() -> Any:
        sap = get_sap(slot_id)
        if authorize_slot is not None:
            authorize_slot(slot_id, effective_agent)
        return sap

    executor = PlanExecutor(
        get_sap=owned_sap,
        map_store=map_store,
        auth_hook=lambda _command, tool, params, _step: preflight_tool_call(
            tool,
            params,
            policy=policy,
            agent_name=effective_agent,
            enforce_auth=enforce_auth,
        ),
    )
    try:
        execution_result = executor.execute(
            plan,
            approval_grants=grants,
            verification=verification,
        )
        result = execution_result.model_dump(mode="json")
    except Exception:
        unknown = runtime.mark_outcome_unknown(
            execution_id,
            error_code="executor_outcome_unknown",
            slot_id=slot_id,
            agent_name=effective_agent,
        )
        if coordinator is not None:
            current = coordinator.get(coordinator_id)
            if current.status == "running":
                coordinator.mark_outcome_unknown(
                    coordinator_id, error_code="executor_outcome_unknown"
                )
        if ledger is not None:
            ledger.update(
                coordinator_id,
                status="outcome_unknown",
                error_code="executor_outcome_unknown",
            )
        return {
            "execution_id": execution_id,
            "status": unknown.status,
            "error_code": unknown.error_code,
            "replayed": False,
        }
    if execution_result.status == "failed" and execution_result.commit_attempted:
        unknown = runtime.mark_outcome_unknown(
            execution_id,
            error_code="unknown_commit_outcome",
            result=result,
            slot_id=slot_id,
            agent_name=effective_agent,
        )
        if coordinator is not None:
            current = coordinator.get(coordinator_id)
            if current.status == "running":
                coordinator.mark_outcome_unknown(
                    coordinator_id, error_code="unknown_commit_outcome"
                )
        if ledger is not None:
            ledger.update(
                coordinator_id,
                status="outcome_unknown",
                error_code="unknown_commit_outcome",
            )
        return {
            "execution_id": execution_id,
            "status": unknown.status,
            "error_code": unknown.error_code,
            "replayed": False,
            "result": unknown.result,
        }
    if execution_result.status in {"blocked", "failed"}:
        error_code = (
            "plan_blocked"
            if execution_result.status == "blocked"
            else "plan_failed"
        )
        failed = runtime.fail_execution(
            execution_id,
            result,
            error_code=error_code,
            slot_id=slot_id,
            agent_name=effective_agent,
        )
        if coordinator is not None:
            current = coordinator.get(coordinator_id)
            if current.status == "running":
                coordinator.fail(coordinator_id, error_code=error_code)
        if ledger is not None:
            ledger.update(
                coordinator_id, status="failed", error_code=error_code
            )
        return {
            "execution_id": execution_id,
            "status": failed.status,
            "error_code": failed.error_code,
            "replayed": False,
            "result": failed.result,
        }
    completed = runtime.complete_execution(
        execution_id,
        result,
        slot_id=slot_id,
        agent_name=effective_agent,
    )
    if coordinator is not None:
        current = coordinator.get(coordinator_id)
        if current.status == "running":
            coordinator.complete(coordinator_id)
    if ledger is not None:
        ledger.update(coordinator_id, status="completed")
    return {
        "execution_id": execution_id,
        "status": completed.status,
        "replayed": False,
        "result": completed.result,
    }


def _execution_plan_reference(args: Mapping[str, Any]) -> tuple[str, str]:
    """Resolve a preview ``plan_ref`` without weakening immutable-plan checks.

    Preview responses intentionally nest their opaque reference under
    ``plan_ref``.  Historically execute only accepted flattened ``plan_id`` /
    ``plan_hash`` fields, which made the natural hand-off fail unless a caller
    manually reshaped the response.  Accept both representations and reject a
    conflicting mixture rather than silently choosing one.
    """

    direct_id = str(args.get("plan_id", "") or "").strip()
    direct_hash = str(args.get("plan_hash", "") or "").strip()
    raw_ref = args.get("plan_ref")
    if raw_ref in (None, ""):
        if not direct_id or not direct_hash:
            raise ValueError("plan_ref or both plan_id and plan_hash are required")
        return direct_id, direct_hash
    if not isinstance(raw_ref, Mapping):
        raise ValueError("plan_ref must be an object returned by plan_preview")
    ref_id = str(raw_ref.get("plan_id", "") or "").strip()
    ref_hash = str(raw_ref.get("plan_hash", "") or "").strip()
    if not ref_id or not ref_hash:
        raise ValueError("plan_ref requires plan_id and plan_hash")
    if direct_id and direct_id != ref_id:
        raise ValueError("plan_id conflicts with plan_ref")
    if direct_hash and direct_hash != ref_hash:
        raise ValueError("plan_hash conflicts with plan_ref")
    return ref_id, ref_hash

__all__ = [
    "PLANNING_COMMANDS",
    "dispatch_planning_command",
    "is_planning_command",
]
