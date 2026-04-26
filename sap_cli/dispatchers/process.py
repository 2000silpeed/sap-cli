"""Business process command dispatch helpers.

Process command implementations live here while :mod:`sap_cli.dispatcher`
keeps thin private wrappers for backward-compatible imports and monkeypatch-
based tests.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

StoreFactory = Callable[[], Any]


PROCESS_COMMANDS: frozenset[str] = frozenset(
    {
        "process_list",
        "process_show",
        "process_create",
        "process_delete",
        "process_start",
        "process_step",
        "process_status",
        "process_path",
    }
)


def is_process_command(command: str) -> bool:
    """Return whether ``command`` belongs to the process dispatcher group."""
    return command in PROCESS_COMMANDS


def dispatch_process_command(
    command: str,
    args: dict[str, Any],
    *,
    get_store: StoreFactory,
) -> dict[str, Any]:
    """Dispatch a process command with facade-provided compatibility hooks."""
    if command == "process_list":
        return dispatch_process_list(get_store)
    if command == "process_show":
        return dispatch_process_show(args, get_store)
    if command == "process_create":
        return dispatch_process_create(args, get_store)
    if command == "process_delete":
        return dispatch_process_delete(args, get_store)
    if command == "process_start":
        return dispatch_process_start(args, get_store)
    if command == "process_step":
        return dispatch_process_step(args)
    if command == "process_status":
        return dispatch_process_status()
    if command == "process_path":
        return dispatch_process_path(args, get_store)
    raise ValueError(f"미구현 process 명령: {command}")


def dispatch_process_list(get_store: StoreFactory) -> dict[str, Any]:
    """List business process definitions."""
    store = get_store()
    process_ids = store.list_processes()
    processes = []
    for pid in process_ids:
        p = store.load_process(pid)
        if p:
            processes.append(
                {
                    "id": p.id,
                    "name": p.name,
                    "module": p.module,
                    "steps_count": len(p.steps),
                    "tags": p.tags,
                }
            )
    return {"processes": processes, "count": len(processes)}


def dispatch_process_show(args: dict[str, Any], get_store: StoreFactory) -> dict[str, Any]:
    """Return one business process definition."""
    process_id = str(args["process_id"])
    process = get_store().load_process(process_id)
    if not process:
        return {"error": True, "message": f"프로세스 '{process_id}'를 찾을 수 없습니다."}
    return process.model_dump(mode="json")


def dispatch_process_create(args: dict[str, Any], get_store: StoreFactory) -> dict[str, Any]:
    """Create a business process definition."""
    from ..ontology.schema import BusinessProcess, DataFlow, ProcessStep

    process_id = str(args["id"])
    name = str(args["name"])
    steps_raw = args.get("steps", [])
    data_flows_raw = args.get("data_flows", [])

    steps = [ProcessStep(**s) for s in steps_raw]
    data_flows = [DataFlow(**d) for d in data_flows_raw]

    process = BusinessProcess(
        id=process_id,
        name=name,
        module=str(args.get("module", "")),
        description=str(args.get("description", "")),
        steps=steps,
        data_flows=data_flows,
        tags=args.get("tags", []),
    )
    errors = process.validate_integrity()
    if errors:
        return {"error": True, "message": "프로세스 무결성 검증 실패", "errors": errors}
    path = get_store().save_process(process)
    return {"saved": True, "process_id": process.id, "path": str(path)}


def dispatch_process_delete(args: dict[str, Any], get_store: StoreFactory) -> dict[str, Any]:
    """Delete a business process definition."""
    process_id = str(args["process_id"])
    deleted = get_store().delete_process(process_id)
    return {"deleted": deleted, "process_id": process_id}


_active_executor: Any = None  # ProcessExecutor | None


def dispatch_process_start(args: dict[str, Any], get_store: StoreFactory) -> dict[str, Any]:
    """Start executing a business process definition."""
    global _active_executor
    from ..ontology.process_executor import ProcessExecutor

    process_id = str(args["process_id"])
    process = get_store().load_process(process_id)
    if not process:
        return {"error": True, "message": f"프로세스 '{process_id}'를 찾을 수 없습니다."}

    _active_executor = ProcessExecutor(process)
    _active_executor.start()
    return _active_executor.get_status()


def dispatch_process_step(args: dict[str, Any]) -> dict[str, Any]:
    """Record completion/skipping of one process execution step."""
    global _active_executor
    if _active_executor is None:
        return {"error": True, "message": "실행 중인 프로세스가 없습니다. process_start를 먼저 호출하세요."}

    step_number = int(args["step_number"])
    status = str(args.get("status", "success"))
    outputs = args.get("outputs", {}) or {}
    error = str(args.get("error", ""))

    if status == "skipped":
        try:
            _active_executor.skip_step(step_number, error)
        except ValueError as e:
            return {"error": True, "message": str(e)}
    else:
        _active_executor.complete_step(
            step_number,
            outputs=outputs,
            status=status,
            error=error,
        )

    remaining = [
        s
        for s in _active_executor.process.steps
        if s.step_number > step_number
        and not any(
            r.step_number == s.step_number for r in _active_executor.execution.step_results
        )
    ]

    result = _active_executor.get_status()

    if not remaining:
        has_error = any(r.status == "error" for r in _active_executor.execution.step_results)
        final_status = "failed" if has_error else "completed"
        path = _active_executor.finish(final_status)
        result["saved_to"] = str(path)
        result["status"] = final_status
        _active_executor = None

    return result


def dispatch_process_status() -> dict[str, Any]:
    """Return the current process execution status."""
    if _active_executor is None:
        return {"error": True, "message": "실행 중인 프로세스가 없습니다."}
    return _active_executor.get_status()


def dispatch_process_path(args: dict[str, Any], get_store: StoreFactory) -> dict[str, Any]:
    """Find a document-flow path between two transactions."""
    from ..ontology.graph import OntologyGraph

    from_tcode = args.get("from_tcode", "")
    to_tcode = args.get("to_tcode", "")
    if not from_tcode or not to_tcode:
        return {"error": True, "message": "from_tcode와 to_tcode가 필요합니다."}

    store = get_store()
    graph = OntologyGraph(store)
    graph.build_from_store()

    path = graph.get_process_path(from_tcode, to_tcode)
    if path is None:
        return {
            "error": True,
            "message": f"{from_tcode} → {to_tcode} 경로를 찾을 수 없습니다. 문서 흐름 데이터를 --rebuild로 구축했는지 확인하세요.",
        }

    edges_detail = []
    flow = store.load_document_flow()
    flow_map = {(e.source_tcode, e.target_tcode): e for e in flow.edges}
    for i in range(len(path) - 1):
        edge = flow_map.get((path[i], path[i + 1]))
        if edge:
            edges_detail.append(
                {
                    "from": path[i],
                    "to": path[i + 1],
                    "linking_field": edge.linking_field,
                    "flow_type": edge.flow_type.value,
                    "confidence": edge.confidence,
                }
            )
        else:
            edges_detail.append({"from": path[i], "to": path[i + 1]})

    return {
        "from": from_tcode.upper(),
        "to": to_tcode.upper(),
        "path": path,
        "path_display": " → ".join(path),
        "steps": edges_detail,
    }
