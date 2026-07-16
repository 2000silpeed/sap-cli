"""Dispatcher surface for versioned UI maps and cooperative traversal jobs."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable

from sap_cli.ui_map.jobs import MapJobRecord, MapJobStore
from sap_cli.ui_map.search import UiMapSearch
from sap_cli.ui_map.store import UiMapStore
from sap_cli.ui_map.fingerprint import compute_map_fingerprint
from sap_cli.ui_map.view import map_view


UI_MAP_COMMANDS = frozenset(
    {
        "map_list",
        "map_search",
        "map_path",
        "map_report",
        "map_view",
        "map_job_status",
        "map_job_cancel",
        "map_augment_start",
        "map_traverse_start",
        "map_traverse_resume",
    }
)


def is_ui_map_command(command: str) -> bool:
    return command in UI_MAP_COMMANDS


def default_map_store() -> UiMapStore:
    from sap_cli.ui_map.runtime import try_get_ui_map_store

    cached = try_get_ui_map_store()
    if cached is not None:
        return cached
    root = os.getenv("SAP_CLI_UI_MAP_DIR") or str(Path.home() / ".sap_cli" / "ui_maps")
    return UiMapStore(root)


def default_job_store() -> MapJobStore:
    root = os.getenv("SAP_CLI_UI_MAP_JOB_DIR") or str(
        Path.home() / ".sap_cli" / "ui_map_jobs"
    )
    return MapJobStore(root)


def dispatch_ui_map_command(
    command: str,
    args: dict[str, Any],
    ctx: Any,
    *,
    get_map_store: Callable[[], UiMapStore] = default_map_store,
    get_job_store: Callable[[], MapJobStore] = default_job_store,
    get_scheduler: Callable[[], Any] | None = None,
    preflight_tcode: Callable[[str], Any] | None = None,
) -> dict[str, Any]:
    map_store = get_map_store()
    job_store = get_job_store()
    owner = str(ctx.agent_name or ctx.source or "unknown")
    slot_id = str(ctx.slot_id or "ses0")

    if command == "map_list":
        maps = map_store.list_latest(
            tcode=str(args.get("tcode", "")),
            system_id=str(args.get("system_id", "")),
            client=str(args.get("client", "")),
            language=str(args.get("language", "")),
        )
        summaries = [_manifest_summary(item) for item in maps]
        return {"count": len(summaries), "maps": summaries}

    if command in {"map_search", "map_path"}:
        ui_map = _load_map(map_store, args)
        index = UiMapSearch(ui_map)
        if command == "map_search":
            limit = _limit(args.get("limit", 50))
            kinds = args.get("kinds")
            if isinstance(kinds, str):
                kinds = [item.strip() for item in kinds.split(",") if item.strip()]
            results = index.search(str(args.get("query", "")), kinds=kinds, limit=limit)
            payload = [_search_summary(item) for item in results]
            return {
                "map_id": ui_map.manifest.map_id,
                "map_version": ui_map.manifest.map_version,
                "count": len(payload),
                "results": payload,
            }
        return _path_result(index, ui_map.manifest.map_version, args)

    if command == "map_report":
        return _structural_report(_load_map(map_store, args))

    if command == "map_view":
        return map_view(_load_map(map_store, args))

    if command == "map_job_status":
        job_id = str(args.get("job_id", "")).strip()
        if job_id:
            record = job_store.authorize(job_id, owner_agent=owner, slot_id=slot_id)
            return {"job": _job_summary(record)}
        limit = _limit(args.get("limit", 50))
        records = [
            item
            for item in job_store.list(status=args.get("status"))
            if (not item.owner_agent or item.owner_agent == owner)
            and item.slot_id == slot_id
        ][:limit]
        return {"count": len(records), "jobs": [_job_summary(item) for item in records]}

    if command == "map_job_cancel":
        job_id = _required(args, "job_id")
        job_store.authorize(job_id, owner_agent=owner, slot_id=slot_id)
        scheduler = None
        if get_scheduler is None:
            from sap_cli.ui_map.runtime import get_ui_map_scheduler

            try:
                scheduler = get_ui_map_scheduler()
            except RuntimeError:
                pass
        else:
            scheduler = get_scheduler()
        record = (
            scheduler.cancel(job_id)
            if scheduler is not None and hasattr(scheduler, "cancel")
            else job_store.request_cancel(job_id)
        )
        return {"job": _job_summary(record)}

    if get_scheduler is None:
        from sap_cli.ui_map.runtime import get_ui_map_scheduler

        scheduler = get_ui_map_scheduler()
    else:
        scheduler = get_scheduler()
    if command == "map_augment_start":
        ui_map = _load_map(map_store, args)
        scope = args.get("scope") or {}
        if not isinstance(scope, dict):
            raise ValueError("scope must be an object")
        record = scheduler.augment_start(
            ui_map=ui_map,
            from_state_id=_required(args, "from_state_id"),
            request=_required(args, "request"),
            scope=scope,
            owner_agent=owner,
            slot_id=slot_id,
        )
        return {"job": _job_summary(record)}
    if command == "map_traverse_start":
        tcode = _required(args, "tcode")
        if bool(args.get("navigate", True)) and preflight_tcode is not None:
            decision = preflight_tcode(tcode)
            if not bool(getattr(decision, "allowed", False)):
                raise PermissionError("transaction authorization denied")
            if bool(getattr(decision, "approval_required", False)):
                raise PermissionError("transaction approval is required before traversal")
        record = scheduler.start(
            tcode=tcode,
            map_id=str(args.get("map_id", "")),
            mode=str(args.get("mode", "passive")),
            budget=dict(args.get("budget") or {}),
            allowed_actions=list(args.get("allowed_actions") or []),
            allowed_branches=list(args.get("allowed_branches") or []),
            owner_agent=owner,
            slot_id=slot_id,
            navigate=bool(args.get("navigate", True)),
            test_client_confirmed=bool(args.get("test_client_confirmed", False)),
            semantic_review=bool(
                args.get("semantic_review", str(args.get("mode", "")).lower() == "guided")
            ),
        )
        return {"job": _job_summary(record)}
    if command == "map_traverse_resume":
        job_id = _required(args, "job_id")
        job_store.authorize(job_id, owner_agent=owner, slot_id=slot_id)
        answers = args.get("answers") or {}
        budget = args.get("budget") or {}
        if not isinstance(answers, dict):
            raise ValueError("answers must be an object")
        if not isinstance(budget, dict):
            raise ValueError("budget must be an object")
        if not answers and not budget:
            raise ValueError("answers or a larger budget is required")
        record = scheduler.resume(
            job_id,
            answers,
            budget=budget,
            owner_agent=owner,
            slot_id=slot_id,
        )
        return {"job": _job_summary(record)}
    raise ValueError(f"unsupported UI map command: {command}")


def _load_map(store: UiMapStore, args: dict[str, Any]):
    map_id = _required(args, "map_id")
    scope = {
        "tcode": _required(args, "tcode"),
        "system_id": str(args.get("system_id", "")),
        "client": str(args.get("client", "")),
        "language": str(args.get("language", "")),
    }
    version = args.get("version")
    if not any(scope[key] for key in ("system_id", "client", "language")):
        matches = [
            item
            for item in store.list_latest(tcode=scope["tcode"])
            if item.manifest.map_id == map_id
        ]
        if len(matches) != 1:
            raise ValueError(
                "map scope is ambiguous; provide system_id, client, and language"
            )
        selected = matches[0]
        if version is None or selected.manifest.map_version == int(version):
            return selected
        manifest = selected.manifest
        return store.load_version(
            map_id,
            int(version),
            tcode=manifest.tcode,
            system_id=manifest.system_id,
            client=manifest.client,
            language=manifest.language,
        )
    return (
        store.load_version(map_id, int(version), **scope)
        if version is not None
        else store.latest(map_id, **scope)
    )


def _manifest_summary(ui_map: Any) -> dict[str, Any]:
    manifest = ui_map.manifest
    return {
        "map_id": manifest.map_id,
        "map_version": manifest.map_version,
        "tcode": manifest.tcode,
        "system_id": manifest.system_id,
        "client": manifest.client,
        "language": manifest.language,
        "capture_mode": manifest.capture_mode,
        "states": len(ui_map.states),
        "transitions": len(ui_map.transitions),
        "pending_interactions": sum(
            item.status in {"pending", "answered"}
            for item in ui_map.pending_interactions
        ),
        "updated_at": manifest.updated_at,
    }


def _structural_report(ui_map: Any) -> dict[str, Any]:
    collections: dict[str, int] = {}
    collection_coverage: dict[str, int] = {}
    unreachable: dict[str, int] = {}
    scan_totals: dict[str, float] = {}
    components = 0
    modal_states = 0
    unknown_shells = 0
    partial_collections = 0
    unobserved_actionable_branches = 0
    for state in ui_map.states.values():
        components += len(state.components)
        modal_states += bool(state.modal_signature)
        unknown_shells += sum(
            component.type == "GuiShell"
            and not any(
                collection.component_logical_id == component.logical_id
                and collection.kind in {"grid", "alv", "tree"}
                for collection in state.collections
            )
            for component in state.components
        )
        for collection in state.collections:
            collections[collection.kind] = collections.get(collection.kind, 0) + 1
            collection_is_partial = False
            for area, status in collection.coverage.items():
                key = f"{area}:{status}"
                collection_coverage[key] = collection_coverage.get(key, 0) + 1
                normalized = str(status).casefold()
                if any(
                    marker in normalized
                    for marker in (
                        "partial",
                        "metadata_only",
                        "not_observed",
                        "unobserved",
                        "unknown",
                    )
                ):
                    collection_is_partial = True
                    if area in {"context_menu_actions", "unexpanded_descendants"}:
                        unobserved_actionable_branches += 1
            partial_collections += collection_is_partial
        metrics = state.normalized_status.get("scan_metrics", {})
        if isinstance(metrics, dict):
            for name, value in metrics.items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    scan_totals[name] = scan_totals.get(name, 0.0) + float(value)
    resolved_unreachable = 0
    for item in ui_map.manifest.unreachable:
        if item.get("resolved") is True:
            resolved_unreachable += 1
            continue
        reason = str(item.get("reason", "unknown"))
        unreachable[reason] = unreachable.get(reason, 0) + 1
    restored = [
        evidence.get("baseline_restored")
        for transition in ui_map.transitions
        for evidence in transition.evidence
        if isinstance(evidence, dict) and "baseline_restored" in evidence
    ]
    reversible_transitions = [
        transition for transition in ui_map.transitions if transition.reversible
    ]
    restored_transition_ids = {
        transition.transition_id
        for transition in reversible_transitions
        if any(
            isinstance(evidence, dict)
            and evidence.get("baseline_restored") is True
            for evidence in transition.evidence
        )
    }
    missing_restore_evidence = max(
        0, len(reversible_transitions) - len(restored_transition_ids)
    )
    incomplete_markers = {
        "partial",
        "metadata_only",
        "not_observed_until_opened",
        "unknown",
        "unobserved",
    }
    partial_coverage = {
        key: count
        for key, count in collection_coverage.items()
        if any(marker in key.casefold() for marker in incomplete_markers)
    }
    completeness = float(ui_map.manifest.completeness)
    actionable_interactions = [
        item
        for item in ui_map.pending_interactions
        if item.status in {"pending", "answered"}
        and item.reason == "unsafe_actionable_branches_require_user_guidance"
    ]
    pending_actionable_branches = 0
    for item in actionable_interactions:
        if isinstance(item.scope, dict):
            try:
                pending_actionable_branches += max(
                    0, int(item.scope.get("branch_count", 0))
                )
            except (TypeError, ValueError):
                pending_actionable_branches += len(
                    item.scope.get("coverage_branches", [])
                    if isinstance(item.scope.get("coverage_branches"), list)
                    else []
                )
    coverage_complete = bool(
        completeness >= 1.0
        and ui_map.states
        and not partial_coverage
        and not actionable_interactions
        and not unobserved_actionable_branches
        and not unreachable
        and not unknown_shells
        and not any(value is False for value in restored)
        and missing_restore_evidence == 0
    )
    return {
        "map_id": ui_map.manifest.map_id,
        "map_version": ui_map.manifest.map_version,
        "map_fingerprint": compute_map_fingerprint(ui_map),
        "scope": {
            "tcode": ui_map.manifest.tcode,
            "system_id": ui_map.manifest.system_id,
            "client": ui_map.manifest.client,
            "language": ui_map.manifest.language,
        },
        "counts": {
            "states": len(ui_map.states),
            "transitions": len(ui_map.transitions),
            "components": components,
            "collections_by_kind": dict(sorted(collections.items())),
            "collection_coverage": dict(sorted(collection_coverage.items())),
            "partial_collection_coverage": dict(sorted(partial_coverage.items())),
            "partial_collections": partial_collections,
            "pending_actionable_branches": pending_actionable_branches,
            "pending_actionable_states": len(
                {item.current_state_id for item in actionable_interactions}
            ),
            "unobserved_actionable_branches": unobserved_actionable_branches,
            "unknown_shells": unknown_shells,
            "modal_states": modal_states,
            "unreachable_by_reason": dict(sorted(unreachable.items())),
            "resolved_unreachable": resolved_unreachable,
            "restored_transitions": sum(value is True for value in restored),
            "restore_failures": sum(value is False for value in restored),
            "missing_restore_evidence": missing_restore_evidence,
        },
        "coverage_gate": {
            "complete": coverage_complete,
            "manifest_completeness": completeness,
            "has_partial_collections": bool(partial_coverage),
            "pending_actionable_branches": pending_actionable_branches,
            "unobserved_actionable_branches": unobserved_actionable_branches,
            "partial_collections": partial_collections,
            "has_unreachable": bool(unreachable),
            "has_unknown_shells": bool(unknown_shells),
            "has_missing_restore_evidence": bool(missing_restore_evidence),
        },
        "scan_metrics": dict(sorted(scan_totals.items())),
    }


def _search_summary(result: Any) -> dict[str, Any]:
    payload = {
        "kind": result.kind,
        "entity_id": result.entity_id,
        "state_id": result.state_id,
        "score": result.score,
        "matched_fields": list(result.matched_fields),
    }
    entity = result.entity
    if result.kind == "state":
        payload["summary"] = {
            "tcode": entity.tcode,
            "program": entity.program,
            "screen_number": entity.screen_number,
            "title": entity.title,
        }
    elif result.kind == "component":
        payload["summary"] = {
            "logical_id": entity.logical_id,
            "sap_id": entity.sap_id,
            "type": entity.type,
            "role": entity.role,
            "name": entity.name,
            "sensitive": entity.sensitive,
        }
    else:
        payload["summary"] = _transition_summary(entity)
    return payload


def _path_result(index: UiMapSearch, map_version: int, args: dict[str, Any]) -> dict[str, Any]:
    source = _required(args, "from_state_id")
    target_type = str(args.get("target_type", "state"))
    target = _required(args, "target")
    if target_type == "state":
        path = index.shortest_path(source, target)
        return {
            "map_version": map_version,
            "found": path is not None,
            "paths": [] if path is None else [[_transition_summary(item) for item in path]],
        }
    if target_type != "component":
        raise ValueError("target_type must be state or component")
    paths = index.paths_to_component(target, from_state_id=source, limit=_limit(args.get("limit", 50)))
    return {
        "map_version": map_version,
        "found": bool(paths),
        "paths": [
            {
                "state_id": item.state_id,
                "component": {
                    "logical_id": item.component.logical_id,
                    "sap_id": item.component.sap_id,
                    "type": item.component.type,
                    "role": item.component.role,
                    "name": item.component.name,
                },
                "transitions": [_transition_summary(step) for step in item.transitions],
            }
            for item in paths
        ],
    }


def _transition_summary(item: Any) -> dict[str, Any]:
    return {
        "transition_id": item.transition_id,
        "from_state_id": item.from_state_id,
        "to_state_id": item.to_state_id,
        "action": item.action,
        "target_logical_id": item.target_logical_id,
        "side_effect": item.side_effect.value,
        "reversible": item.reversible,
        "approval_required": item.approval_required,
    }


def _job_summary(record: MapJobRecord) -> dict[str, Any]:
    checkpoint = record.checkpoint
    semantic_links = checkpoint.ui_map.semantic_links if checkpoint is not None else []
    return {
        "job_id": record.job_id,
        "tcode": record.tcode,
        "map_id": record.map_id,
        "status": record.status,
        "slot_id": record.slot_id,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "stop_reason": record.stop_reason,
        "error": record.error,
        "pending_questions": record.pending_questions,
        "budget": dict(record.traversal_config.get("budget") or {}),
        "usage": {
            "elapsed_seconds": checkpoint.elapsed_seconds,
            "states": len(checkpoint.ui_map.states),
            "transitions": len(checkpoint.ui_map.transitions),
            "pages_visited": checkpoint.pages_visited,
            "nodes_visited": checkpoint.nodes_visited,
        }
        if checkpoint is not None
        else {},
        "semantic_links": {
            "proposed": sum(item.status == "proposed" for item in semantic_links),
            "confirmed": sum(item.status == "confirmed" for item in semantic_links),
            "rejected": sum(item.status == "rejected" for item in semantic_links),
        },
    }


def _required(args: dict[str, Any], name: str) -> str:
    value = str(args.get(name, "")).strip()
    if not value:
        raise ValueError(f"{name} is required")
    return value


def _limit(value: Any) -> int:
    result = int(value)
    if result < 1 or result > 500:
        raise ValueError("limit must be between 1 and 500")
    return result


__all__ = [
    "UI_MAP_COMMANDS",
    "default_job_store",
    "default_map_store",
    "dispatch_ui_map_command",
    "is_ui_map_command",
]
