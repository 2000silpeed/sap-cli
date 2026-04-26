"""중앙 명령 dispatcher.

CLI, daemon, MCP 서버가 모두 이 경로를 타도록 만들어
권한, 감사, 실행, 후처리 문맥을 한곳에 모은다.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_logger = logging.getLogger(__name__)

from .auth.audit import AuditLogger
from .auth.checker import PermissionChecker
from .auth.policy import TOOL_PERMISSION_MAP, Permission, Policy, load_policy
from .auth.transcript import TranscriptLogger
from .command_parser import extract_tcode_from_command
from .com_wrapper import SapGuiWrapper
from .dispatchers.registry import (
    COMMAND_REGISTRY,
    COMMAND_TOOL_MAP,
    MCP_TOOL_TO_COMMAND,
    CommandDef,
)
from .dispatchers.process import (
    dispatch_process_command,
    dispatch_process_create as _dispatch_process_create_impl,
    dispatch_process_delete as _dispatch_process_delete_impl,
    dispatch_process_list as _dispatch_process_list_impl,
    dispatch_process_path as _dispatch_process_path_impl,
    dispatch_process_show as _dispatch_process_show_impl,
    dispatch_process_start as _dispatch_process_start_impl,
    dispatch_process_status as _dispatch_process_status_impl,
    dispatch_process_step as _dispatch_process_step_impl,
    is_process_command,
)
from .dispatchers.ontology import (
    build_chain as _build_chain_impl,
    dispatch_annotate as _dispatch_annotate_impl,
    dispatch_annotate_dependency as _dispatch_annotate_dependency_impl,
    dispatch_document_flow as _dispatch_document_flow_impl,
    dispatch_field_dependencies as _dispatch_field_dependencies_impl,
    dispatch_ontology_ask as _dispatch_ontology_ask_impl,
    dispatch_ontology_command,
    dispatch_ontology_query as _dispatch_ontology_query_impl,
    dispatch_ontology_query_extended as _dispatch_ontology_query_extended_impl,
    dispatch_ontology_rebuild as _dispatch_ontology_rebuild_impl,
    dispatch_ontology_relationships as _dispatch_ontology_relationships_impl,
    is_ontology_command,
)
from .dispatchers.macro import (
    dispatch_macro_command,
    dispatch_macro_control as _dispatch_macro_control_impl,
    dispatch_macro_delete as _dispatch_macro_delete_impl,
    dispatch_macro_list as _dispatch_macro_list_impl,
    dispatch_macro_play as _dispatch_macro_play_impl,
    dispatch_macro_show as _dispatch_macro_show_impl,
    is_macro_command,
    utc_to_local as _utc_to_local_impl,
)
from .dispatchers.recipe import (
    collect_recipe_hints as _collect_recipe_hints_impl,
    dispatch_recipe_chain as _dispatch_recipe_chain_impl,
    dispatch_recipe_command,
    dispatch_recipe_delete as _dispatch_recipe_delete_impl,
    dispatch_recipe_enable as _dispatch_recipe_enable_impl,
    dispatch_recipe_failure as _dispatch_recipe_failure_impl,
    dispatch_recipe_history as _dispatch_recipe_history_impl,
    dispatch_recipe_list as _dispatch_recipe_list_impl,
    dispatch_recipe_pending as _dispatch_recipe_pending_impl,
    dispatch_recipe_query as _dispatch_recipe_query_impl,
    dispatch_recipe_save as _dispatch_recipe_save_impl,
    dispatch_recipe_search as _dispatch_recipe_search_impl,
    dispatch_recipe_show as _dispatch_recipe_show_impl,
    dispatch_recipe_success as _dispatch_recipe_success_impl,
    dispatch_recipe_version_up as _dispatch_recipe_version_up_impl,
    delete_recipe_with_links as _delete_recipe_with_links_impl,
    extract_recipe_tcodes as _extract_recipe_tcodes_impl,
    get_recipe_store as _get_recipe_store_impl,
    is_recipe_command,
    link_recipe_to_ontology as _link_recipe_to_ontology_impl,
    unlink_recipe_from_ontology as _unlink_recipe_from_ontology_impl,
)
from .dispatchers.session import (
    SESSION_SKIP_COMMANDS,
    dispatch_session_approve as _dispatch_session_approve_impl,
    dispatch_session_candidates as _dispatch_session_candidates_impl,
    dispatch_session_command,
    dispatch_session_end as _dispatch_session_end_impl,
    dispatch_session_list as _dispatch_session_list_impl,
    dispatch_session_show as _dispatch_session_show_impl,
    dispatch_session_start as _dispatch_session_start_impl,
    is_session_command,
    log_session_step as _log_session_step_impl,
)
from .dispatchers.slot import (
    dispatch_slot_allocate as _dispatch_slot_allocate_impl,
    dispatch_slot_command,
    dispatch_slot_release as _dispatch_slot_release_impl,
    dispatch_slots as _dispatch_slots_impl,
    is_slot_command,
)
from .ontology.store import OntologyStore
from .service import (
    execute_sap_action as _execute_sap_action,
    get_learner as _get_learner,
    get_macro_store as _get_macro_store,
    get_recorder as _get_recorder,
    get_sap as _get_sap,
    get_pool as _get_pool,
    get_session_store as _get_session_store,
    get_store as _get_store,
    map_action as _map_action,
)
from .transcript_planner import TranscriptPlanner
from .transcript_store import TranscriptStore


POST_ACTION_COMMANDS = {
    "connect",
    "run",
    "set",
    "press",
    "vkey",
    "command",
    "record_step",
    "macro_play",
    "table_select",
    "table_doubleclick",
}

TRANSCRIPT_OMIT_COMMANDS = {
    "transcript_query",
    "transcript_plan",
    "session_start",
    "session_end",
    "session_list",
    "session_show",
    "session_candidates",
    "session_approve",
}


class PermissionDeniedError(Exception):
    """정책상 허용되지 않은 호출."""


class ApprovalRequiredError(Exception):
    """정책상 추가 승인이 필요한 호출."""

    def __init__(self, tool_name: str, reason: str) -> None:
        super().__init__(reason)
        self.tool_name = tool_name
        self.reason = reason

    def to_payload(self) -> dict[str, Any]:
        return {
            "approval_required": True,
            "tool": self.tool_name,
            "reason": self.reason,
        }


@dataclass(slots=True)
class AuthorizationDecision:
    agent: str
    permission: str
    resource: str
    allowed: bool
    reason: str = ""
    approval_required: bool = False
    approval_reason: str = ""


@dataclass(slots=True)
class DispatchContext:
    source: str = "cli"
    tool_name: str | None = None
    agent_name: str | None = None
    enforce_auth: bool = True
    include_post_action_context: bool = True
    include_screen_summary: bool = False
    session_id: str | None = None
    slot_id: str = "ses0"


def dispatch_command(
    command: str,
    arguments: dict[str, Any] | None = None,
    context: DispatchContext | None = None,
) -> dict[str, Any]:
    args = arguments or {}
    ctx = context or DispatchContext()

    if command not in COMMAND_REGISTRY:
        raise ValueError(f"알 수 없는 명령: {command}")

    cmd_def = COMMAND_REGISTRY[command]
    tool_name = ctx.tool_name or cmd_def.tool_name
    authz, policy = _evaluate_authorization(tool_name, args, ctx)
    _audit_authorization(policy, authz, tool_name, args)

    transcript = TranscriptLogger(policy.transcript if policy else None)
    should_log_transcript = transcript.should_log(authz.permission) and command not in TRANSCRIPT_OMIT_COMMANDS
    should_log_session = bool(ctx.session_id) and command not in SESSION_SKIP_COMMANDS
    need_state = should_log_transcript or should_log_session
    transcript_with_screen_summary = transcript.include_screen_summary or ctx.include_screen_summary
    before_state = None
    if need_state:
        before_state = _capture_transcript_state(
            command,
            include_screen_summary=transcript_with_screen_summary,
            slot_id=ctx.slot_id,
        )

    if not authz.allowed:
        _log_transcript(
            transcript,
            should_log_transcript=should_log_transcript,
            ctx=ctx,
            command=command,
            tool_name=tool_name,
            authz=authz,
            arguments=args,
            outcome="denied",
            before_state=before_state,
        )
        raise PermissionDeniedError(f"권한 없음: {authz.reason}")

    if authz.approval_required:
        _log_transcript(
            transcript,
            should_log_transcript=should_log_transcript,
            ctx=ctx,
            command=command,
            tool_name=tool_name,
            authz=authz,
            arguments=args,
            outcome="approval_required",
            before_state=before_state,
        )
        raise ApprovalRequiredError(tool_name, authz.approval_reason)

    try:
        result = _execute_command(command, args, ctx, policy)
    except Exception as exc:
        after_state = None
        if need_state:
            after_state = _capture_transcript_state(
                command,
                include_screen_summary=transcript_with_screen_summary,
                slot_id=ctx.slot_id,
            )
        _log_transcript(
            transcript,
            should_log_transcript=should_log_transcript,
            ctx=ctx,
            command=command,
            tool_name=tool_name,
            authz=authz,
            arguments=args,
            outcome="error",
            before_state=before_state,
            after_state=after_state,
            error=str(exc),
        )
        _log_session_step(
            ctx=ctx,
            command=command,
            tool_name=tool_name,
            arguments=args,
            outcome="error",
            error=str(exc),
            before_state=before_state,
            after_state=after_state,
        )
        raise

    after_state = _extract_transcript_state_from_result(result)
    if need_state and after_state is None:
        after_state = _capture_transcript_state(
            command,
            include_screen_summary=transcript_with_screen_summary,
            slot_id=ctx.slot_id,
        )
    _log_transcript(
        transcript,
        should_log_transcript=should_log_transcript,
        ctx=ctx,
        command=command,
        tool_name=tool_name,
        authz=authz,
        arguments=args,
        outcome="success",
        before_state=before_state,
        after_state=after_state,
        result=result,
    )
    _log_session_step(
        ctx=ctx,
        command=command,
        tool_name=tool_name,
        arguments=args,
        outcome="success",
        before_state=before_state,
        after_state=after_state,
    )
    return result


def _execute_command(
    command: str,
    args: dict[str, Any],
    ctx: DispatchContext,
    policy: Policy | None = None,
) -> dict[str, Any]:
    if command == "connect":
        return _dispatch_connect(args, ctx)
    if command == "connections":
        return _dispatch_connections(args, ctx)
    if command == "session":
        return _get_sap(ctx.slot_id).get_session_info().model_dump(mode="json")
    if command == "screen":
        return _dispatch_screen(args, ctx)
    if command == "run":
        sap = _get_sap(ctx.slot_id)
        tcode = str(args["tcode"])
        sap.run_transaction(tcode)
        info = sap.get_session_info()
        payload = {
            "executed": tcode,
            "current_transaction": info.transaction,
            "screen_number": info.screen_number,
        }
        hints = _collect_recipe_hints(tcode)
        if hints:
            payload["recipe_hints"] = hints
        return _with_post_action(payload, sap, command, ctx)
    if command == "set":
        sap = _get_sap(ctx.slot_id)
        element_id = str(args["element_id"])
        value = str(args["value"])
        if "/chk" in element_id:
            checked = value.lower() not in ("", "0", "false", "off", "no")
            sap.set_checkbox(element_id, checked)
        elif "/rad" in element_id:
            sap.select_radio(element_id)
        else:
            sap.set_field(element_id, value)
        payload = {
            "field": element_id,
            "value_set": value,
            "actual_value": sap.get_field(element_id),
        }
        return _with_post_action(payload, sap, command, ctx)
    if command == "get":
        sap = _get_sap(ctx.slot_id)
        element_ids = args.get("element_ids") or [str(args["element_id"])]
        if len(element_ids) == 1:
            return sap.get_field_info(element_ids[0])
        return [sap.get_field_info(eid) for eid in element_ids]
    if command == "press":
        sap = _get_sap(ctx.slot_id)
        element_id = str(args["element_id"])
        sap.press(element_id)
        payload = {"pressed": element_id}
        return _with_post_action(payload, sap, command, ctx)
    if command == "vkey":
        sap = _get_sap(ctx.slot_id)
        key = int(args["key"])
        sap.send_vkey(key)
        payload = {"vkey_sent": key}
        return _with_post_action(payload, sap, command, ctx)
    if command == "status":
        return _get_sap(ctx.slot_id).get_status().model_dump(mode="json")
    if command == "table":
        element_id = str(args["element_id"])
        max_rows = int(args.get("max_rows", 50))
        col_filter = args.get("columns")
        if isinstance(col_filter, str):
            col_filter = [c.strip() for c in col_filter.split(",")]
        return _get_sap(ctx.slot_id).read_table(element_id, max_rows=max_rows, columns=col_filter).model_dump(mode="json")
    if command == "tree":
        element_id = str(args["element_id"])
        node_key = str(args.get("node_key", "")).strip()
        depth = int(args.get("depth", -1))
        sap = _get_sap(ctx.slot_id)
        nodes = sap.read_tree(element_id)
        # node_key가 지정되면 해당 노드의 자식만 반환
        if node_key:
            target = _find_tree_node(nodes, node_key)
            if target is None:
                raise ValueError(f"노드를 찾을 수 없습니다: {node_key}")
            nodes = target.children
        # depth 제한 적용
        if depth >= 0:
            nodes = [_truncate_tree_depth(n, depth) for n in nodes]
        columns = getattr(sap, "_tree_columns", [])
        return {
            "element_id": element_id,
            **({"columns": columns} if columns else {}),
            **({"parent_node_key": node_key} if node_key else {}),
            **({"depth": depth} if depth >= 0 else {}),
            "node_count": sum(1 for _ in _flatten_tree_nodes(nodes)),
            "nodes": [n.model_dump(mode="json", exclude_none=True) for n in nodes],
        }
    if command == "tree_select":
        sap = _get_sap(ctx.slot_id)
        sap.tree_select_node(str(args["element_id"]), str(args["node_key"]))
        payload = {"action": "select", "element_id": args["element_id"], "node_key": args["node_key"]}
        return _with_post_action(payload, sap, command, ctx)
    if command == "tree_doubleclick":
        sap = _get_sap(ctx.slot_id)
        sap.tree_double_click_node(str(args["element_id"]), str(args["node_key"]))
        payload = {"action": "doubleclick", "element_id": args["element_id"], "node_key": args["node_key"]}
        return _with_post_action(payload, sap, command, ctx)
    if command == "tree_expand":
        sap = _get_sap(ctx.slot_id)
        sap.tree_expand_node(str(args["element_id"]), str(args["node_key"]))
        return {"action": "expand", "element_id": args["element_id"], "node_key": args["node_key"]}
    if command == "tree_collapse":
        sap = _get_sap(ctx.slot_id)
        sap.tree_collapse_node(str(args["element_id"]), str(args["node_key"]))
        return {"action": "collapse", "element_id": args["element_id"], "node_key": args["node_key"]}
    if command == "tree_press":
        sap = _get_sap(ctx.slot_id)
        used_col = sap.tree_press_button(
            str(args["element_id"]), str(args["node_key"]), str(args.get("column", "")))
        cached = sap._tree_action_cache.get(str(args["element_id"]), {})
        payload: dict[str, Any] = {
            "action": "press",
            "element_id": args["element_id"],
            "node_key": args["node_key"],
            "used_column": used_col,
        }
        if cached:
            payload["discovered_columns"] = cached
        return _with_post_action(payload, sap, command, ctx)
    if command == "command":
        sap = _get_sap(ctx.slot_id)
        command_text = str(args["cmd"])
        sap.send_command(command_text)
        payload = {"command": command_text}
        return _with_post_action(payload, sap, command, ctx)
    if command == "modal":
        return _collect_modal(_get_sap(ctx.slot_id))
    if command == "record_step":
        return _dispatch_record_step(args, ctx)
    if is_macro_command(command):
        return dispatch_macro_command(
            command,
            args,
            ctx,
            get_recorder=_get_recorder,
            get_macro_store=_get_macro_store,
            get_sap=_get_sap,
            with_post_action=_with_post_action,
        )
    if command == "table_select":
        sap = _get_sap(ctx.slot_id)
        element_id = str(args["element_id"])
        row = int(args["row"])
        sap.select_table_row(element_id, row)
        payload = {"selected": True, "table": element_id, "row": row}
        return _with_post_action(payload, sap, command, ctx)
    if command == "table_doubleclick":
        sap = _get_sap(ctx.slot_id)
        element_id = str(args["element_id"])
        row = int(args["row"])
        col = str(args.get("column", ""))
        sap.double_click_table_cell(element_id, row, col)
        payload = {"double_clicked": True, "table": element_id, "row": row, "column": col}
        return _with_post_action(payload, sap, command, ctx)
    if is_ontology_command(command):
        return dispatch_ontology_command(
            command,
            args,
            get_store=_get_store,
            get_recipe_store=_get_recipe_store,
        )
    if command == "transcript_query":
        return _dispatch_transcript_query(args, policy)
    if command == "transcript_plan":
        return _dispatch_transcript_plan(args, policy)
    if command == "learn":
        return _dispatch_learn(ctx)
    if is_process_command(command):
        return dispatch_process_command(command, args, get_store=_get_store)
    if command == "fill":
        return _dispatch_fill(args, ctx)
    if command == "capture":
        return _dispatch_capture(args, ctx)
    if command == "capture_list":
        return _dispatch_capture_list(args)
    if is_recipe_command(command):
        return dispatch_recipe_command(command, args, _RECIPE_HANDLERS)
    if is_session_command(command):
        return dispatch_session_command(
            command,
            args,
            ctx,
            get_session_store=_get_session_store,
            get_recipe_store=_get_recipe_store,
            get_existing_sap=_get_existing_sap,
            link_recipe_to_ontology=_link_recipe_to_ontology,
        )
    if is_slot_command(command):
        return dispatch_slot_command(
            command,
            args,
            ctx,
            get_pool=_get_pool,
            get_sap=_get_sap,
        )
    raise ValueError(f"미구현 명령: {command}")


def resolve_mcp_call(
    tool_name: str,
    arguments: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    command = MCP_TOOL_TO_COMMAND.get(tool_name)
    if command is None:
        raise ValueError(f"Unknown tool: {tool_name}")
    args = dict(arguments or {})
    arg_map = COMMAND_REGISTRY[command].mcp_arg_map
    if arg_map:
        for mcp_key, dispatch_key in arg_map.items():
            if mcp_key in args and dispatch_key not in args:
                args[dispatch_key] = args.pop(mcp_key)
    return command, args


def _dispatch_connect(args: dict[str, Any], ctx: DispatchContext) -> dict[str, Any]:
    from .service import get_pool

    pool = get_pool()
    sap = pool.get_or_create(ctx.slot_id, agent_name=ctx.agent_name)
    conn_idx = int(args.get("connection", args.get("connection_index", 0)))
    sess_idx = int(args.get("session", args.get("session_index", 0)))
    system = args.get("system")
    info = sap.connect(
        connection_index=conn_idx,
        session_index=sess_idx,
        system=system,
    )
    pool.update_slot_connection(ctx.slot_id, conn_idx, sess_idx, system_id=system)
    payload = {
        "connected": True,
        "slot_id": ctx.slot_id,
        "session": info.model_dump(mode="json"),
    }
    return _with_post_action(payload, sap, "connect", ctx)


def _dispatch_connections(args: dict[str, Any], ctx: DispatchContext) -> dict[str, Any]:
    sap = SapGuiWrapper()
    return sap.list_connections()


def _dispatch_screen(args: dict[str, Any], ctx: DispatchContext | None = None) -> dict[str, Any]:
    slot_id = ctx.slot_id if ctx else "ses0"
    sap = _get_sap(slot_id)
    screen = sap.get_screen()
    search = args.get("search")
    if search:
        return _search_screen(screen, search)
    if args.get("fields_only"):
        result: dict[str, Any] = {
            "transaction": screen.transaction,
            "screen_number": screen.screen_number,
            "fields": [field.model_dump(mode="json") for field in screen.fields],
        }
        if screen.tables:
            result["tables"] = [t.model_dump(mode="json") for t in screen.tables]
        return result
    if args.get("actions_only"):
        actions = screen.get_available_actions()
        return {
            "transaction": screen.transaction,
            "screen_number": screen.screen_number,
            "available_actions": [action.model_dump(mode="json") for action in actions],
        }
    data = screen.model_dump(mode="json")
    data["hint"] = "온톨로지 조회 가능: sap_field_dependencies(필드 종속성), sap_document_flow(문서 흐름), sap_ontology_relationships(필드 위치)"
    return data


def _search_screen(screen: Any, pattern: str) -> dict[str, Any]:
    """화면 요소를 패턴으로 검색 (ID, 이름, 라벨, 텍스트 부분 일치)."""
    p = pattern.lower()

    def _match(text: str) -> bool:
        return p in text.lower()

    matched_fields = []
    for f in screen.fields:
        if any(_match(s) for s in [f.id, f.name, f.label, getattr(f, "value", ""),
                                    getattr(f.semantic, "sap_field_name", ""),
                                    getattr(f.semantic, "description", "")] +
               getattr(f.semantic, "synonyms", [])):
            matched_fields.append(f.model_dump(mode="json"))

    matched_buttons = []
    for b in screen.buttons:
        if any(_match(s) for s in [b.id, b.name, b.text, b.tooltip]):
            matched_buttons.append(b.model_dump(mode="json"))

    matched_tabs = []
    for t in screen.tabs:
        if any(_match(s) for s in [t.id, t.name, t.text]):
            matched_tabs.append(t.model_dump(mode="json"))

    matched_tables = []
    for t in screen.tables:
        if any(_match(s) for s in [t.id, t.name]):
            matched_tables.append(t.model_dump(mode="json"))

    total = len(matched_fields) + len(matched_buttons) + len(matched_tabs) + len(matched_tables)
    result: dict[str, Any] = {
        "transaction": screen.transaction,
        "screen_number": screen.screen_number,
        "search": pattern,
        "total_matches": total,
    }
    if matched_fields:
        result["fields"] = matched_fields
    if matched_buttons:
        result["buttons"] = matched_buttons
    if matched_tabs:
        result["tabs"] = matched_tabs
    if matched_tables:
        result["tables"] = matched_tables
    return result


def _dispatch_record_step(
    args: dict[str, Any],
    ctx: DispatchContext,
) -> dict[str, Any]:
    sap = _get_sap(ctx.slot_id)
    recorder = _get_recorder()
    action = str(args["action"])
    target = str(args["target"])
    value = args.get("value")
    description = str(args.get("description", ""))

    action_type = _map_action(action)
    _execute_sap_action(sap, action.lower(), target, None if value is None else str(value))

    info = sap.get_session_info()
    record_value = None if value is None else str(value)
    if action.lower() in ("run", "vkey") and not record_value:
        record_value = target

    step = recorder.record_step(
        action=action_type,
        target_id=target,
        value=record_value,
        description=description,
        screen_number=info.screen_number,
    )

    payload = {
        "recorded": True,
        "step_number": step.step_number,
        "action": action,
        "target": target,
        "value": value,
        "screen_number": info.screen_number,
    }
    return _with_post_action(payload, sap, "record_step", ctx)


def _dispatch_macro_control(args: dict[str, Any]) -> dict[str, Any]:
    return _dispatch_macro_control_impl(args, _get_recorder, _get_macro_store)


def _dispatch_macro_play(
    args: dict[str, Any],
    ctx: DispatchContext,
) -> dict[str, Any]:
    return _dispatch_macro_play_impl(args, ctx, _get_macro_store, _get_sap, _with_post_action)


def _dispatch_macro_list(args: dict[str, Any]) -> dict[str, Any]:
    return _dispatch_macro_list_impl(args, _get_macro_store)


def _utc_to_local(iso_str: str) -> str:
    return _utc_to_local_impl(iso_str)


def _dispatch_macro_show(args: dict[str, Any]) -> dict[str, Any]:
    return _dispatch_macro_show_impl(args, _get_macro_store)


def _dispatch_macro_delete(args: dict[str, Any]) -> dict[str, Any]:
    return _dispatch_macro_delete_impl(args, _get_macro_store)


def _dispatch_ontology_ask(args: dict[str, Any]) -> dict[str, Any]:
    return _dispatch_ontology_ask_impl(args, _get_store)


def _dispatch_ontology_query_extended(
    store: OntologyStore, query_type: str, args: dict[str, Any]
) -> dict[str, Any]:
    return _dispatch_ontology_query_extended_impl(store, query_type, args)


def _build_chain(
    dep_graph: Any, field_name: str, chain: list[str], visited: set[str],
    depth: int, max_depth: int,
) -> None:
    return _build_chain_impl(dep_graph, field_name, chain, visited, depth, max_depth)


def _dispatch_ontology_query(args: dict[str, Any]) -> dict[str, Any]:
    return _dispatch_ontology_query_impl(args, _get_store, _get_recipe_store)


def _dispatch_transcript_query(
    args: dict[str, Any],
    policy: Policy | None,
) -> dict[str, Any]:
    store = TranscriptStore(policy.transcript if policy else None)
    return store.query(
        limit=int(args.get("limit", 20)),
        outcome=args.get("outcome"),
        command=args.get("command"),
        tool=args.get("tool"),
        agent=args.get("agent"),
        text=args.get("text"),
        full=bool(args.get("full", False)),
    )


def _dispatch_transcript_plan(
    args: dict[str, Any],
    policy: Policy | None,
) -> dict[str, Any]:
    store = TranscriptStore(policy.transcript if policy else None)
    planner = TranscriptPlanner(store, ontology_store=_make_ontology_store(policy))
    return planner.plan(
        limit=int(args.get("limit", 20)),
        index=int(args.get("index", 0)),
        outcome=args.get("outcome"),
        command=args.get("command"),
        tool=args.get("tool"),
        agent=args.get("agent"),
        text=args.get("text"),
    )


def _make_ontology_store(policy: Policy | None) -> OntologyStore:
    if policy is None:
        return OntologyStore()
    transcript_dir = Path(policy.transcript.log_dir).expanduser()
    knowledge_dir = transcript_dir.parent / "knowledge"
    return OntologyStore(knowledge_dir=knowledge_dir)


def _dispatch_learn(ctx: DispatchContext | None = None) -> dict[str, Any]:
    slot_id = ctx.slot_id if ctx else "ses0"
    screen = _get_learner(slot_id).learn_current_screen()
    return {
        "learned": True,
        "transaction": screen.transaction,
        "screen_number": screen.screen_number,
        "fields_count": len(screen.fields),
        "buttons_count": len(screen.buttons),
    }


def _dispatch_annotate(args: dict[str, Any]) -> dict[str, Any]:
    return _dispatch_annotate_impl(args, _get_store)


def _dispatch_annotate_dependency(args: dict[str, Any]) -> dict[str, Any]:
    return _dispatch_annotate_dependency_impl(args, _get_store)


def _dispatch_process_list() -> dict[str, Any]:
    return _dispatch_process_list_impl(_get_store)


def _dispatch_process_show(args: dict[str, Any]) -> dict[str, Any]:
    return _dispatch_process_show_impl(args, _get_store)


def _dispatch_process_create(args: dict[str, Any]) -> dict[str, Any]:
    return _dispatch_process_create_impl(args, _get_store)


def _dispatch_process_delete(args: dict[str, Any]) -> dict[str, Any]:
    return _dispatch_process_delete_impl(args, _get_store)


def _dispatch_process_start(args: dict[str, Any]) -> dict[str, Any]:
    return _dispatch_process_start_impl(args, _get_store)


def _dispatch_process_step(args: dict[str, Any]) -> dict[str, Any]:
    return _dispatch_process_step_impl(args)


def _dispatch_process_status() -> dict[str, Any]:
    return _dispatch_process_status_impl()


def _dispatch_fill(args: dict[str, Any], ctx: DispatchContext) -> dict[str, Any]:
    """여러 필드를 한번에 입력.

    필드명(MATNR) 또는 element_id 모두 지원.
    필드명인 경우 현재 화면을 스캔하여 element_id를 자동 매핑.
    """
    sap = _get_sap(ctx.slot_id)
    fields: dict[str, str] = args["fields"]
    do_enter = args.get("enter", True)

    screen = sap.get_screen()
    results: list[dict[str, Any]] = []
    errors: list[str] = []

    for key, value in fields.items():
        # 1) element_id로 직접 지정한 경우 (wnd[0]/... 형태)
        if key.startswith("wnd["):
            element_id = key
        else:
            # 2) 필드명으로 검색 (MATNR, WERKS 등)
            field = (
                screen.get_field_by_name(key)
                or screen.get_field_by_name(key.upper())
                or screen.get_field_by_label(key)
            )
            if field is None:
                errors.append(f"필드를 찾을 수 없습니다: {key}")
                continue
            element_id = field.id

        try:
            if "/chk" in element_id:
                checked = value.lower() not in ("", "0", "false", "off", "no")
                sap.set_checkbox(element_id, checked)
            elif "/rad" in element_id:
                sap.select_radio(element_id)
            else:
                sap.set_field(element_id, value)
            results.append({
                "field": key,
                "element_id": element_id,
                "value_set": value,
            })
        except Exception as e:
            errors.append(f"{key} ({element_id}): {e}")

    if do_enter and results and not errors:
        sap.send_vkey(0)

    payload: dict[str, Any] = {
        "filled": len(results),
        "results": results,
        "enter_sent": do_enter and bool(results) and not errors,
    }
    if errors:
        payload["errors"] = errors
    return _with_post_action(payload, sap, "fill", ctx)


def _dispatch_ontology_relationships(args: dict[str, Any]) -> dict[str, Any]:
    return _dispatch_ontology_relationships_impl(args, _get_store)


def _dispatch_field_dependencies(args: dict[str, Any]) -> dict[str, Any]:
    return _dispatch_field_dependencies_impl(args, _get_store)


def _dispatch_document_flow(args: dict[str, Any]) -> dict[str, Any]:
    return _dispatch_document_flow_impl(args, _get_store)


def _dispatch_ontology_rebuild() -> dict[str, Any]:
    return _dispatch_ontology_rebuild_impl(_get_store)


def _dispatch_process_path(args: dict[str, Any]) -> dict[str, Any]:
    return _dispatch_process_path_impl(args, _get_store)


def _flatten_tree_nodes(nodes: list) -> list:
    """트리 노드를 평탄화."""
    result = []
    for n in nodes:
        result.append(n)
        if n.children:
            result.extend(_flatten_tree_nodes(n.children))
    return result


def _find_tree_node(nodes: list, key: str):
    """트리에서 key에 해당하는 노드를 재귀 탐색."""
    for n in nodes:
        if n.key == key:
            return n
        if n.children:
            found = _find_tree_node(n.children, key)
            if found:
                return found
    return None


def _truncate_tree_depth(node, remaining_depth: int):
    """트리 깊이를 제한. 잘린 노드에는 child_count를 표시."""
    from sap_cli.ontology.schema import SapTreeNode
    if remaining_depth <= 0:
        # 이 노드까지만 표시, children은 잘라내고 child_count만 기록
        return SapTreeNode(
            key=node.key,
            text=node.text,
            level=node.level,
            items=node.items,
            children=[],
            child_count=len(node.children) if node.children else None,
        )
    # 아직 깊이 여유 있음 — 자식도 재귀 처리
    truncated_children = [_truncate_tree_depth(c, remaining_depth - 1) for c in node.children]
    return SapTreeNode(
        key=node.key,
        text=node.text,
        level=node.level,
        items=node.items,
        children=truncated_children,
        child_count=None,
    )


def _dispatch_capture(args: dict[str, Any], ctx: DispatchContext | None = None) -> dict[str, Any]:
    """현재 SAP GUI 화면 스크린샷 캡처."""
    from .screenshot import capture_sap_window, capture_for_step

    slot_id = ctx.slot_id if ctx else "ses0"
    sap = _get_sap(slot_id)
    info = sap.get_session_info()
    label = args.get("label") or ""
    output = args.get("output")

    session = sap._session  # COM 세션 → 정확한 윈도우 핸들 획득용

    if output:
        path = capture_sap_window(output, session=session)
    else:
        path = capture_for_step(
            tcode=info.transaction,
            step_number=0,
            screen_number=info.screen_number,
            label=label or "manual",
            session=session,
        )

    if path is None:
        raise RuntimeError("스크린샷 캡처에 실패했습니다.")

    return {
        "captured": True,
        "path": str(path),
        "transaction": info.transaction,
        "screen_number": info.screen_number,
        "label": label,
    }


def _dispatch_capture_list(args: dict[str, Any]) -> dict[str, Any]:
    """저장된 스크린샷 목록 조회."""
    from .screenshot import list_screenshots

    tcode = args.get("tcode")
    shots = list_screenshots(tcode)
    return {"screenshots": shots, "count": len(shots)}


def _get_recipe_store():
    return _get_recipe_store_impl()


def _collect_recipe_hints(tcode: str, limit: int = 3) -> dict[str, Any] | None:
    return _collect_recipe_hints_impl(tcode, _get_recipe_store, limit)


def _extract_recipe_tcodes(recipe) -> set[str]:
    return _extract_recipe_tcodes_impl(recipe)


def _unlink_recipe_from_ontology(recipe) -> list[str]:
    return _unlink_recipe_from_ontology_impl(recipe, _get_store, _extract_recipe_tcodes)


def _delete_recipe_with_links(recipe_id: str) -> dict[str, Any]:
    return _delete_recipe_with_links_impl(
        recipe_id,
        _get_recipe_store,
        _unlink_recipe_from_ontology,
    )


def _link_recipe_to_ontology(
    recipe_id: str,
    steps: list,
    description: str = "",
    context: str = "",
    keywords: list[str] | None = None,
) -> list[str]:
    return _link_recipe_to_ontology_impl(
        recipe_id,
        steps,
        _get_store,
        description=description,
        context=context,
        keywords=keywords,
    )


def _dispatch_recipe_save(args: dict[str, Any]) -> dict[str, Any]:
    return _dispatch_recipe_save_impl(
        args,
        _get_recipe_store,
        _unlink_recipe_from_ontology,
        _link_recipe_to_ontology,
        _delete_recipe_with_links,
    )


def _dispatch_recipe_search(args: dict[str, Any]) -> dict[str, Any]:
    return _dispatch_recipe_search_impl(args, _get_recipe_store)


def _dispatch_recipe_show(args: dict[str, Any]) -> dict[str, Any]:
    return _dispatch_recipe_show_impl(args, _get_recipe_store)


def _dispatch_recipe_list() -> dict[str, Any]:
    return _dispatch_recipe_list_impl(_get_recipe_store)


def _dispatch_recipe_delete(args: dict[str, Any]) -> dict[str, Any]:
    return _dispatch_recipe_delete_impl(args, _delete_recipe_with_links)


def _dispatch_recipe_success(args: dict[str, Any]) -> dict[str, Any]:
    return _dispatch_recipe_success_impl(args, _get_recipe_store)


def _dispatch_recipe_failure(args: dict[str, Any]) -> dict[str, Any]:
    """레시피 실행 실패 보고. 임계 도달 시 자동 비활성화."""
    return _dispatch_recipe_failure_impl(args, _get_recipe_store)


def _dispatch_recipe_enable(args: dict[str, Any]) -> dict[str, Any]:
    """비활성화된 레시피를 수동 재활성화."""
    return _dispatch_recipe_enable_impl(args, _get_recipe_store)


def _dispatch_recipe_version_up(args: dict[str, Any]) -> dict[str, Any]:
    """기존 레시피의 새 버전을 저장. 기존 버전은 _archive로 이동."""
    return _dispatch_recipe_version_up_impl(
        args,
        _get_recipe_store,
        _unlink_recipe_from_ontology,
        _link_recipe_to_ontology,
    )


def _dispatch_recipe_history(args: dict[str, Any]) -> dict[str, Any]:
    """레시피의 과거 버전 목록 + 현재 활성 버전 메타."""
    return _dispatch_recipe_history_impl(args, _get_recipe_store)


def _dispatch_recipe_chain(args: dict[str, Any]) -> dict[str, Any]:
    """여러 tcode를 걸치는 복합 레시피 검색."""
    return _dispatch_recipe_chain_impl(args, _get_recipe_store)


def _dispatch_recipe_query(args: dict[str, Any]) -> dict[str, Any]:
    """tcode + 키워드 통합 레시피 조회. AI 자동 참조용."""
    return _dispatch_recipe_query_impl(args, _get_recipe_store)


def _dispatch_recipe_pending(args: dict[str, Any]) -> dict[str, Any]:
    """미승인 candidate 세션 요약. 자동 제안된 레시피 후보를 한눈에 보여준다."""
    return _dispatch_recipe_pending_impl(args, _get_session_store)


_RECIPE_HANDLERS = {
    "recipe_save": _dispatch_recipe_save,
    "recipe_search": _dispatch_recipe_search,
    "recipe_show": _dispatch_recipe_show,
    "recipe_list": lambda _args: _dispatch_recipe_list(),
    "recipe_delete": _dispatch_recipe_delete,
    "recipe_success": _dispatch_recipe_success,
    "recipe_query": _dispatch_recipe_query,
    "recipe_pending": _dispatch_recipe_pending,
    "recipe_failure": _dispatch_recipe_failure,
    "recipe_enable": _dispatch_recipe_enable,
    "recipe_version_up": _dispatch_recipe_version_up,
    "recipe_history": _dispatch_recipe_history,
    "recipe_chain": _dispatch_recipe_chain,
}


def _dispatch_session_start(args: dict[str, Any], ctx: DispatchContext) -> dict[str, Any]:
    return _dispatch_session_start_impl(args, ctx, _get_session_store, _get_existing_sap)


def _dispatch_session_end(args: dict[str, Any]) -> dict[str, Any]:
    return _dispatch_session_end_impl(args, _get_session_store)


def _dispatch_session_list(args: dict[str, Any]) -> dict[str, Any]:
    return _dispatch_session_list_impl(args, _get_session_store)


def _dispatch_session_show(args: dict[str, Any]) -> dict[str, Any]:
    return _dispatch_session_show_impl(args, _get_session_store)


def _dispatch_session_candidates(args: dict[str, Any]) -> dict[str, Any]:
    return _dispatch_session_candidates_impl(args, _get_session_store)


def _dispatch_session_approve(args: dict[str, Any]) -> dict[str, Any]:
    return _dispatch_session_approve_impl(
        args,
        _get_session_store,
        _get_recipe_store,
        _link_recipe_to_ontology,
    )


def _evaluate_authorization(
    tool_name: str,
    args: dict[str, Any],
    ctx: DispatchContext,
) -> tuple[AuthorizationDecision, Policy | None]:
    policy = load_policy()
    checker = PermissionChecker(policy)
    configured_agent = _resolve_agent_name(policy, ctx.agent_name)
    if configured_agent:
        checker.set_agent(configured_agent)
    check = checker.check_tool(tool_name, args)
    permission = TOOL_PERMISSION_MAP.get(tool_name, "")
    resource = checker.resolve_resource(tool_name, args)
    effective_agent = checker.agent_name or configured_agent or "anonymous"

    if not ctx.enforce_auth:
        return (
            AuthorizationDecision(
                agent=effective_agent,
                permission=permission.value if permission else "",
                resource=resource,
                allowed=True,
                reason="auth_disabled",
            ),
            policy,
        )

    if not check:
        return (
            AuthorizationDecision(
                agent=effective_agent,
                permission=permission.value if permission else "",
                resource=resource,
                allowed=False,
                reason=check.reason,
            ),
            policy,
        )

    approval_reason = checker.requires_approval(tool_name, args)
    if approval_reason:
        return (
            AuthorizationDecision(
                agent=effective_agent,
                permission=permission.value if permission else "",
                resource=resource,
                allowed=True,
                reason=check.reason,
                approval_required=True,
                approval_reason=approval_reason,
            ),
            policy,
        )
    return (
        AuthorizationDecision(
            agent=effective_agent,
            permission=permission.value if permission else "",
            resource=resource,
            allowed=True,
            reason=check.reason,
        ),
        policy,
    )


def _audit_authorization(
    policy: Policy | None,
    authz: AuthorizationDecision,
    tool_name: str,
    arguments: dict[str, Any],
) -> None:
    auditor = AuditLogger(policy.audit if policy else None)
    if not authz.allowed:
        auditor.log(
            agent=authz.agent,
            tool_name=tool_name,
            arguments=arguments,
            allowed=False,
            reason=authz.reason,
            permission=authz.permission,
            resource=authz.resource,
        )
        return
    if authz.approval_required:
        auditor.log(
            agent=authz.agent,
            tool_name=tool_name,
            arguments=arguments,
            allowed=False,
            reason=f"approval_required: {authz.approval_reason}",
            permission=authz.permission,
            resource=authz.resource,
        )
        return
    auditor.log(
        agent=authz.agent,
        tool_name=tool_name,
        arguments=arguments,
        allowed=True,
        reason=authz.reason,
        permission=authz.permission,
        resource=authz.resource,
    )


def _resolve_agent_name(policy: Any, explicit_agent_name: str | None) -> str | None:
    env_agent = os.getenv("SAP_CLI_AGENT", "").strip() or None
    candidate = explicit_agent_name or env_agent
    if not policy or not candidate:
        return None
    if policy.get_agent(candidate):
        return candidate
    return None


def _log_transcript(
    transcript: TranscriptLogger,
    *,
    should_log_transcript: bool,
    ctx: DispatchContext,
    command: str,
    tool_name: str,
    authz: AuthorizationDecision,
    arguments: dict[str, Any],
    outcome: str,
    before_state: dict[str, Any] | None = None,
    after_state: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
    error: str = "",
) -> None:
    if not should_log_transcript:
        return

    def _has_session(state: dict[str, Any] | None) -> bool:
        return bool(state and isinstance(state.get("session"), dict))

    if not _has_session(before_state) and not _has_session(after_state):
        sap = _get_existing_sap(ctx.slot_id)
        if sap is not None:
            try:
                session = sap.get_session_info().model_dump(mode="json")
                if before_state is None:
                    before_state = {"session": session}
                else:
                    before_state["session"] = session
            except Exception:
                pass

    reason = authz.approval_reason if authz.approval_required else authz.reason
    transcript.log(
        source=ctx.source,
        command=command,
        tool_name=tool_name,
        agent=authz.agent,
        arguments=arguments,
        permission=authz.permission,
        resource=authz.resource,
        allowed=authz.allowed,
        approval_required=authz.approval_required,
        reason=reason,
        outcome=outcome,
        before=before_state,
        after=after_state,
        result=result,
        error=error,
    )


def _log_session_step(
    *,
    ctx: DispatchContext,
    command: str,
    tool_name: str,
    arguments: dict[str, Any],
    outcome: str,
    error: str = "",
    before_state: dict[str, Any] | None = None,
    after_state: dict[str, Any] | None = None,
) -> None:
    _log_session_step_impl(
        ctx=ctx,
        command=command,
        tool_name=tool_name,
        arguments=arguments,
        outcome=outcome,
        get_session_store=_get_session_store,
        logger=_logger,
        error=error,
        before_state=before_state,
        after_state=after_state,
    )


def _capture_transcript_state(
    command: str,
    *,
    include_screen_summary: bool,
    slot_id: str = "ses0",
) -> dict[str, Any] | None:
    if command not in COMMAND_REGISTRY:
        return None
    if command in {"ontology_query", "ontology_ask", "macro_control", "macro_list", "macro_show", "macro_delete", "transcript_query", "transcript_plan"}:
        return None

    sap = _get_existing_sap(slot_id)
    if sap is None:
        return None

    try:
        state = {
            "session": sap.get_session_info().model_dump(mode="json"),
            "status": sap.get_status().model_dump(mode="json"),
            "modal": _collect_modal(sap),
        }
        if include_screen_summary:
            state["screen_summary"] = _collect_screen_summary(sap)
        return state
    except Exception as exc:  # pragma: no cover - defensive path
        return {"capture_error": str(exc)}


def _extract_transcript_state_from_result(result: dict[str, Any]) -> dict[str, Any] | None:
    post_action = result.get("post_action")
    if not isinstance(post_action, dict):
        return None

    state: dict[str, Any] = {}
    for key in ("session", "status", "modal", "screen_summary"):
        value = post_action.get(key)
        if value is not None:
            state[key] = value
    return state or None


def _get_existing_sap(slot_id: str = "ses0") -> SapGuiWrapper | None:
    from .service import get_pool

    pool = get_pool()
    wrapper = pool.get(slot_id)
    if wrapper is None:
        return None
    try:
        return wrapper if wrapper.is_connected else None
    except Exception:
        return wrapper


def _with_post_action(
    payload: dict[str, Any],
    sap: SapGuiWrapper,
    command: str,
    ctx: DispatchContext,
) -> dict[str, Any]:
    if ctx.include_post_action_context and command in POST_ACTION_COMMANDS:
        payload["post_action"] = _collect_post_action(
            sap,
            include_screen_summary=ctx.include_screen_summary,
        )

    post_action = payload.get("post_action")
    if post_action:
        session = post_action.get("session", {})
        status = post_action.get("status", {})
        payload.setdefault("transaction", session.get("transaction"))
        payload.setdefault("screen_number", session.get("screen_number"))
        payload.setdefault("status", status)
    return payload


def _collect_post_action(
    sap: SapGuiWrapper,
    *,
    include_screen_summary: bool = False,
) -> dict[str, Any]:
    payload = {
        "session": sap.get_session_info().model_dump(mode="json"),
        "status": sap.get_status().model_dump(mode="json"),
        "modal": _collect_modal(sap),
    }
    if include_screen_summary:
        try:
            payload["screen_summary"] = _collect_screen_summary(sap)
        except Exception as exc:  # pragma: no cover - defensive path
            payload["screen_summary_error"] = str(exc)
    return payload


def _collect_screen_summary(sap: SapGuiWrapper) -> dict[str, Any]:
    screen = sap.get_screen()
    changeable_fields = screen.get_changeable_fields()
    required_fields = [field for field in changeable_fields if getattr(field, "required", False)]
    available_actions = screen.get_available_actions()
    return {
        "transaction": screen.transaction,
        "screen_number": screen.screen_number,
        "program": screen.program,
        "title": screen.title,
        "field_count": len(screen.fields),
        "changeable_field_count": len(changeable_fields),
        "required_field_count": len(required_fields),
        "button_count": len(screen.buttons),
        "tab_count": len(screen.tabs),
        "table_count": len(screen.tables),
        "changeable_fields": [_summarize_field(field) for field in changeable_fields[:8]],
        "required_fields": [_summarize_field(field) for field in required_fields[:5]],
        "available_actions": [_summarize_action(action) for action in available_actions[:10]],
    }


def _summarize_field(field: Any) -> dict[str, Any]:
    payload = {"id": str(getattr(field, "id", ""))}
    if name := getattr(field, "name", ""):
        payload["name"] = name
    if label := getattr(field, "label", ""):
        payload["label"] = label
    if field_type := _enum_value(getattr(field, "type", None)):
        payload["type"] = field_type
    if value := getattr(field, "value", ""):
        payload["value"] = value
    if getattr(field, "required", False):
        payload["required"] = True
    return payload


def _summarize_action(action: Any) -> dict[str, Any]:
    payload = {
        "type": _enum_value(getattr(action, "action_type", "")),
        "target_id": str(getattr(action, "target_id", "")),
    }
    if label := getattr(action, "target_label", ""):
        payload["label"] = label
    if description := getattr(action, "description", ""):
        payload["description"] = description
    return payload


def _enum_value(value: Any) -> Any:
    return getattr(value, "value", value)


def _collect_modal(sap: SapGuiWrapper) -> dict[str, Any]:
    has_modal = sap.has_modal()
    payload: dict[str, Any] = {"has_modal": has_modal}
    if has_modal:
        payload["text"] = sap.get_modal_text()
    return payload


# ---------------------------------------------------------------------------
# Session Pool dispatch
# ---------------------------------------------------------------------------

def _dispatch_slots() -> dict[str, Any]:
    return _dispatch_slots_impl(_get_pool)


def _dispatch_slot_allocate(args: dict[str, Any], ctx: DispatchContext) -> dict[str, Any]:
    return _dispatch_slot_allocate_impl(args, ctx, _get_pool, _get_sap)


def _dispatch_slot_release(args: dict[str, Any]) -> dict[str, Any]:
    return _dispatch_slot_release_impl(args, _get_pool)
