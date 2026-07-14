"""Value-safe status and explicit reconciliation for daemon SAP jobs."""

from __future__ import annotations

from typing import Any, Callable

from sap_cli.jobs import get_job_coordinator, try_get_plan_job_ledger
from sap_cli.planning.runtime import get_plan_runtime
from sap_cli.ui_map.jobs import MapJobStore

from .ui_maps import default_job_store


JOB_COMMANDS = frozenset({"job_status", "job_reconcile"})


def is_job_command(command: str) -> bool:
    return command in JOB_COMMANDS


def dispatch_job_command(
    command: str,
    args: dict[str, Any],
    ctx: Any,
    *,
    get_map_jobs: Callable[[], MapJobStore] = default_job_store,
) -> dict[str, Any]:
    coordinator = get_job_coordinator()
    ledger = try_get_plan_job_ledger()
    owner = str(ctx.agent_name or ctx.source or "unknown")
    allowed_owners = {owner}
    if not ctx.agent_name:
        allowed_owners.add("anonymous")
    slot_id = str(ctx.slot_id or "ses0")
    if command == "job_status":
        job_id = str(args.get("job_id", "")).strip()
        if job_id:
            try:
                snapshots = [coordinator.get(job_id)]
            except KeyError:
                if ledger is None:
                    raise
                record = ledger.authorize(job_id, owner=owner, slot=slot_id)
                return {"count": 1, "jobs": [_ledger_summary(record)]}
        else:
            snapshots = list(coordinator.snapshots())
        visible = [
            item
            for item in snapshots
            if item.agent_name in allowed_owners and item.slot_id == slot_id
        ]
        if job_id and not visible:
            raise PermissionError("job belongs to another agent or slot")
        return {"count": len(visible), "jobs": [_summary(item, get_map_jobs) for item in visible]}
    if command != "job_reconcile":
        raise ValueError(f"unsupported job command: {command}")
    job_id = str(args.get("job_id", "")).strip()
    resolution = str(args.get("resolution", "")).strip().lower()
    if not job_id or not resolution:
        raise ValueError("job_id and resolution are required")
    snapshot = coordinator.get(job_id)
    if snapshot.agent_name not in allowed_owners or snapshot.slot_id != slot_id:
        raise PermissionError("job belongs to another agent or slot")
    if snapshot.status != "outcome_unknown":
        raise ValueError("only outcome_unknown jobs can be reconciled")
    if snapshot.kind == "plan_execute":
        resolution_aliases = {
            "release_after_manual_verification": "verified_failed",
        }
        resolution = resolution_aliases.get(resolution, resolution)
        outcomes = {
            "verified_applied": ("completed", ""),
            "verified_not_applied": ("failed", "manually_verified_not_applied"),
            "verified_failed": ("failed", "manually_verified_failed"),
        }
        if resolution not in outcomes:
            raise ValueError(
                "plan resolution must be verified_applied, "
                "verified_not_applied, or verified_failed"
            )
        final_status, error_code = outcomes[resolution]
        execution_id = job_id.removeprefix("plan:")
        runtime = get_plan_runtime()
        if ledger is not None:
            ledger.authorize(job_id, owner=snapshot.agent_name, slot=slot_id)
        try:
            execution = runtime.get_execution(execution_id)
        except KeyError:
            execution = None
        if execution is not None and execution.status != "outcome_unknown":
            raise ValueError("plan runtime is not outcome_unknown")
        if execution is not None:
            # Validate both coordinated records before either lease is released.
            # After this point both mutations are deterministic owner-thread
            # operations over the same already-validated state.
            runtime.validate_reconciliation(
                execution_id,
                slot_id=slot_id,
                agent_name=snapshot.agent_name,
            )
            coordinator.validate_reconciliation(job_id)
            runtime.reconcile_execution(
                execution_id,
                final_status,
                error_code=error_code,
                slot_id=slot_id,
                agent_name=snapshot.agent_name,
            )
        else:
            coordinator.validate_reconciliation(job_id)
        coordinator.reconcile(job_id, final_status, error_code=error_code)
        if ledger is not None:
            ledger.update(job_id, status=final_status, error_code=error_code)
    else:
        map_job_id = job_id.removeprefix("map:")
        record = get_map_jobs().authorize(
            map_job_id, owner_agent=snapshot.agent_name, slot_id=slot_id
        )
        del record
        reconciled = get_map_jobs().reconcile_interrupted(map_job_id, resolution)
        if resolution == "verified_not_applied":
            coordinator.requeue_reconciled_map(job_id)
        else:
            coordinator.reconcile(job_id, "cancelled")
        return {"job": _summary(coordinator.get(job_id), get_map_jobs), "map_status": reconciled.status}
    return {"job": _summary(coordinator.get(job_id), get_map_jobs)}


def _summary(snapshot: Any, get_map_jobs: Callable[[], MapJobStore]) -> dict[str, Any]:
    payload = {
        "job_id": snapshot.job_id,
        "kind": snapshot.kind,
        "status": snapshot.status,
        "slot_id": snapshot.slot_id,
        "created_at": snapshot.created_at,
        "started_at": snapshot.started_at,
        "finished_at": snapshot.finished_at,
        "error_code": snapshot.error_code,
    }
    if snapshot.kind == "map_traverse":
        try:
            record = get_map_jobs().get(snapshot.job_id.removeprefix("map:"))
            payload["pending_questions"] = record.pending_questions
            payload["stop_reason"] = record.stop_reason
            payload["budget"] = dict(record.traversal_config.get("budget") or {})
            checkpoint = record.checkpoint
            payload["usage"] = (
                {
                    "elapsed_seconds": checkpoint.elapsed_seconds,
                    "states": len(checkpoint.ui_map.states),
                    "transitions": len(checkpoint.ui_map.transitions),
                    "pages_visited": checkpoint.pages_visited,
                    "nodes_visited": checkpoint.nodes_visited,
                }
                if checkpoint is not None
                else {}
            )
        except Exception:
            payload["pending_questions"] = []
    return payload


def _ledger_summary(record: Any) -> dict[str, Any]:
    return {
        "job_id": record.job_id,
        "kind": "plan_execute",
        "status": record.status,
        "slot_id": record.slot,
        "created_at": record.created_at,
        "started_at": record.started_at,
        "finished_at": record.finished_at,
        "error_code": record.error_code,
    }


__all__ = ["JOB_COMMANDS", "dispatch_job_command", "is_job_command"]
