"""중앙 명령 dispatcher.

CLI, daemon, MCP 서버가 모두 이 경로를 타도록 만들어
권한, 감사, 실행, 후처리 문맥을 한곳에 모은다.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
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
from .macro.player import MacroPlayer
from .ontology.store import OntologyStore
from .service import (
    execute_sap_action as _execute_sap_action,
    get_learner as _get_learner,
    get_macro_store as _get_macro_store,
    get_recorder as _get_recorder,
    get_sap as _get_sap,
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
    if command == "macro_control":
        return _dispatch_macro_control(args)
    if command == "macro_play":
        return _dispatch_macro_play(args, ctx)
    if command == "macro_list":
        return _dispatch_macro_list(args)
    if command == "macro_show":
        return _dispatch_macro_show(args)
    if command == "macro_delete":
        return _dispatch_macro_delete(args)
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
    if command == "ontology_query":
        return _dispatch_ontology_query(args)
    if command == "transcript_query":
        return _dispatch_transcript_query(args, policy)
    if command == "transcript_plan":
        return _dispatch_transcript_plan(args, policy)
    if command == "learn":
        return _dispatch_learn(ctx)
    if command == "process_list":
        return _dispatch_process_list()
    if command == "process_show":
        return _dispatch_process_show(args)
    if command == "process_create":
        return _dispatch_process_create(args)
    if command == "process_delete":
        return _dispatch_process_delete(args)
    if command == "process_start":
        return _dispatch_process_start(args)
    if command == "process_step":
        return _dispatch_process_step(args)
    if command == "process_status":
        return _dispatch_process_status()
    if command == "annotate":
        return _dispatch_annotate(args)
    if command == "annotate_dependency":
        return _dispatch_annotate_dependency(args)
    if command == "fill":
        return _dispatch_fill(args, ctx)
    if command == "ontology_ask":
        return _dispatch_ontology_ask(args)
    if command == "ontology_relationships":
        return _dispatch_ontology_relationships(args)
    if command == "field_dependencies":
        return _dispatch_field_dependencies(args)
    if command == "document_flow":
        return _dispatch_document_flow(args)
    if command == "process_path":
        return _dispatch_process_path(args)
    if command == "ontology_rebuild":
        return _dispatch_ontology_rebuild()
    if command == "capture":
        return _dispatch_capture(args, ctx)
    if command == "capture_list":
        return _dispatch_capture_list(args)
    if is_recipe_command(command):
        return dispatch_recipe_command(command, args, _RECIPE_HANDLERS)
    if command == "session_start":
        return _dispatch_session_start(args, ctx)
    if command == "session_end":
        return _dispatch_session_end(args)
    if command == "session_list":
        return _dispatch_session_list(args)
    if command == "session_show":
        return _dispatch_session_show(args)
    if command == "session_candidates":
        return _dispatch_session_candidates(args)
    if command == "session_approve":
        return _dispatch_session_approve(args)
    if command == "slots":
        return _dispatch_slots()
    if command == "slot_allocate":
        return _dispatch_slot_allocate(args, ctx)
    if command == "slot_release":
        return _dispatch_slot_release(args)
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
    command = str(args["command"])
    recorder = _get_recorder()

    if command == "start":
        macro_name = str(args.get("name", "unnamed"))
        recorder.start(macro_name)
        return {"recording": True, "name": macro_name}

    if command == "finish":
        store = _get_macro_store()
        macro_name = args.get("name") or recorder._name
        description = str(args.get("description", ""))
        variables = args.get("variables")
        macro = recorder.finish(
            name=macro_name,
            description=description,
            variables=variables,
        )
        path = store.save(macro)
        return {
            "saved": True,
            "macro_id": macro.id,
            "name": macro.name,
            "steps_count": len(macro.steps),
            "variables": [value.model_dump(mode="json") for value in macro.variables],
            "path": str(path),
        }

    if command == "cancel":
        recorder.cancel()
        return {"cancelled": True}

    if command == "status":
        steps = recorder.current_steps
        return {
            "recording": recorder.is_recording,
            "steps_count": len(steps),
            "steps": [
                {
                    "step_number": step.step_number,
                    "action": step.action.value,
                    "target_id": step.target_id,
                    "value": step.value,
                }
                for step in steps
            ],
        }

    if command == "undo":
        removed = recorder.undo_last()
        if removed:
            return {
                "undone": True,
                "removed_step": removed.step_number,
                "action": removed.action.value,
                "remaining_steps": len(recorder.current_steps),
            }
        return {"undone": False, "message": "취소할 단계가 없습니다."}

    raise ValueError(f"알 수 없는 명령: {command}")


def _dispatch_macro_play(
    args: dict[str, Any],
    ctx: DispatchContext,
) -> dict[str, Any]:
    store = _get_macro_store()
    macro_id = str(args["macro_id"])
    macro = store.load(macro_id)
    if not macro:
        raise ValueError(f"매크로 '{macro_id}'를 찾을 수 없습니다.")

    variables = args.get("variables", {}) or {}
    sap = _get_sap(ctx.slot_id)
    player = MacroPlayer(sap)

    if args.get("dry_run"):
        return player.dry_run(macro, variables).model_dump(mode="json")

    payload = player.play(macro, variables).model_dump(mode="json")
    return _with_post_action(payload, sap, "macro_play", ctx)


def _dispatch_macro_list(args: dict[str, Any]) -> dict[str, Any]:
    store = _get_macro_store()
    tcode = args.get("tcode")
    if tcode:
        macros = store.find_by_tcode(str(tcode))
        return {
            "macros": [macro.model_dump(mode="json") for macro in macros],
            "count": len(macros),
        }
    macros = store.list_macros()
    return {"macros": macros, "count": len(macros)}


def _utc_to_local(iso_str: str) -> str:
    """UTC ISO 문자열을 유저 로컬 타임존으로 변환."""
    if not iso_str:
        return iso_str
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone().isoformat()
    except (ValueError, TypeError):
        return iso_str


def _dispatch_macro_show(args: dict[str, Any]) -> dict[str, Any]:
    store = _get_macro_store()
    macro_id = str(args["macro_id"])
    macro = store.load(macro_id)
    if not macro:
        return {"error": True, "message": f"매크로 '{macro_id}'를 찾을 수 없습니다."}
    data = macro.model_dump(mode="json")
    data["created_at"] = _utc_to_local(data.get("created_at", ""))
    data["updated_at"] = _utc_to_local(data.get("updated_at", ""))
    return data


def _dispatch_macro_delete(args: dict[str, Any]) -> dict[str, Any]:
    macro_id = str(args["macro_id"])
    deleted = _get_macro_store().delete(macro_id)
    return {"deleted": deleted, "macro_id": macro_id}


def _dispatch_ontology_ask(args: dict[str, Any]) -> dict[str, Any]:
    """자연어 질문 → 온톨로지 그래프 탐색 → 구조화된 답변."""
    from .ontology.answer_formatter import OntologyAnswerFormatter
    from .ontology.question_parser import parse_question

    question = str(args.get("question", ""))
    if not question:
        raise ValueError("질문을 입력하세요. 예: sap ontology-ask 'VA01이 뭐야?'")

    store = _get_store()
    parsed = parse_question(question)
    formatter = OntologyAnswerFormatter(store)
    return formatter.answer(parsed)


def _dispatch_ontology_query_extended(
    store: OntologyStore, query_type: str, args: dict[str, Any]
) -> dict[str, Any]:
    """확장 쿼리 타입 핸들러: field_semantic, field_cross, process_guide, dependency_chain."""
    from .ontology.semantic_search import semantic_field_search

    if query_type == "field_semantic":
        search_term = args.get("field_search")
        if not search_term:
            raise ValueError("field_semantic 쿼리에는 field_search가 필요합니다.")
        matches = semantic_field_search(store, str(search_term))
        if not matches:
            return {"query_type": "field_semantic", "search": search_term, "count": 0, "results": [],
                    "hint": "일치하는 필드가 없습니다. 다른 키워드나 SAP 필드명(예: MATNR)을 시도하세요."}
        total = len(matches)
        truncated = total > 20
        results = [
            {"sap_field_name": m.sap_field_name, "tcode": m.tcode, "screen_number": m.screen_number,
             "label": m.label, "description": m.description, "synonyms": m.synonyms,
             "domain_context": m.domain_context, "validation": m.validation,
             "changeable": m.changeable, "score": round(m.score, 2), "match_source": m.match_source}
            for m in matches[:20]
        ]
        return {"query_type": "field_semantic", "search": search_term,
                "count": len(results), "total_count": total, "truncated": truncated, "results": results}

    if query_type == "field_cross":
        search_term = args.get("field_search")
        if not search_term:
            raise ValueError("field_cross 쿼리에는 field_search가 필요합니다.")
        field_name = str(search_term).upper()
        registry = store.load_field_registry()
        entry = registry.fields.get(field_name)
        if entry and entry.occurrences:
            locations = [
                {"tcode": o.tcode, "screen_number": o.screen_number, "label": o.label, "changeable": o.changeable}
                for o in entry.occurrences
            ]
            return {"query_type": "field_cross", "field": field_name, "description": entry.description,
                    "transaction_count": len({o.tcode for o in entry.occurrences}),
                    "total_occurrences": len(locations), "locations": locations}
        matches = semantic_field_search(store, str(search_term))
        if matches:
            seen: set[str] = set()
            locations = []
            for m in matches:
                key = f"{m.tcode}:{m.screen_number}:{m.sap_field_name}"
                if key not in seen:
                    seen.add(key)
                    locations.append({"tcode": m.tcode, "screen_number": m.screen_number,
                                      "sap_field_name": m.sap_field_name, "label": m.label, "changeable": m.changeable})
            return {"query_type": "field_cross", "search": search_term,
                    "note": "정확한 필드명 대신 시맨틱 검색 결과를 반환합니다.",
                    "transaction_count": len({l["tcode"] for l in locations}),
                    "total_occurrences": len(locations), "locations": locations}
        return {"query_type": "field_cross", "field": field_name, "count": 0, "locations": [],
                "hint": f"'{search_term}' 필드를 찾을 수 없습니다."}

    if query_type == "process_guide":
        process_id = args.get("field_search") or args.get("tcode")
        if not process_id:
            all_procs = store.list_processes()
            if not all_procs:
                return {"query_type": "process_guide", "error": "프로세스 데이터가 없습니다."}
            return {"query_type": "process_guide", "available_processes": all_procs,
                    "hint": "field_search에 프로세스 ID를 지정하세요."}
        from .ontology.question_parser import KOREAN_TO_PROCESS
        proc = None
        pid_str = str(process_id)
        resolved = KOREAN_TO_PROCESS.get(pid_str)
        if resolved:
            proc = store.load_process(resolved)
        if not proc:
            try:
                proc = store.load_process(pid_str)
            except ValueError:
                pass
        if not proc:
            for pid in store.list_processes():
                p = store.load_process(pid)
                if p and any(s.tcode.upper() == pid_str.upper() for s in p.steps):
                    proc = p
                    break
        if not proc:
            return {"query_type": "process_guide", "error": f"'{process_id}' 프로세스를 찾을 수 없습니다.",
                    "available": store.list_processes()}
        steps = [
            {"step_number": s.step_number, "tcode": s.tcode, "description": s.description,
             "input_fields": s.input_fields, "expected_output": s.expected_output}
            for s in proc.steps
        ]
        data_flows = [
            {"source_step": df.source_step, "target_step": df.target_step,
             "source_field": df.source_field, "target_field": df.target_field, "description": df.description}
            for df in proc.data_flows
        ]
        return {"query_type": "process_guide", "process_id": proc.id, "name": proc.name,
                "module": proc.module, "description": proc.description,
                "step_count": len(steps), "steps": steps, "data_flows": data_flows}

    if query_type == "dependency_chain":
        search_term = args.get("field_search")
        if not search_term:
            raise ValueError("dependency_chain 쿼리에는 field_search가 필요합니다.")
        field_name = str(search_term).upper()
        from .ontology.graph import OntologyGraph
        graph = OntologyGraph(store)
        graph.build_from_store()
        deps = graph.get_field_dependencies(field_name)
        if deps:
            determines = [d for d in deps if d["direction"] == "determines"]
            determined_by = [d for d in deps if d["direction"] == "determined_by"]
        else:
            determines = []
            determined_by = []
            dep_graph = store.load_field_dependencies()
            for rel in dep_graph.relationships:
                if rel.source_field.upper() == field_name:
                    determines.append({"field": rel.target_field, "dependency_type": rel.dependency_type.value,
                                        "description": rel.description, "observed_in": rel.observed_in})
                elif rel.target_field.upper() == field_name:
                    determined_by.append({"field": rel.source_field, "dependency_type": rel.dependency_type.value,
                                           "description": rel.description, "observed_in": rel.observed_in})
        dep_graph = store.load_field_dependencies()
        chain: list[str] = []
        _build_chain(dep_graph, field_name, chain, set(), depth=0, max_depth=5)
        return {"query_type": "dependency_chain", "field": field_name,
                "determines": determines, "determined_by": determined_by,
                "downstream_chain": chain}

    raise ValueError(f"알 수 없는 query_type: {query_type}")


def _build_chain(
    dep_graph: Any, field_name: str, chain: list[str], visited: set[str],
    depth: int, max_depth: int,
) -> None:
    if depth >= max_depth or field_name in visited:
        return
    visited.add(field_name)
    for rel in dep_graph.relationships:
        if rel.source_field.upper() == field_name.upper():
            target = rel.target_field.upper()
            entry = f"{field_name} → {target} ({rel.dependency_type.value})"
            if entry not in chain:
                chain.append(entry)
                _build_chain(dep_graph, target, chain, visited, depth + 1, max_depth)


def _dispatch_ontology_query(args: dict[str, Any]) -> dict[str, Any]:
    store = _get_store()
    if args.get("list_all"):
        return {
            "transactions": store.list_transactions(),
            "patterns": store.list_patterns(),
            "processes": store.list_processes(),
        }

    query_type = args.get("query_type")
    if query_type:
        return _dispatch_ontology_query_extended(store, query_type, args)

    tcode = args.get("tcode")
    if not tcode:
        raise ValueError("트랜잭션 코드를 지정하세요.")

    if field_search := args.get("field_search"):
        result = store.query_field(str(tcode), str(field_search))
        if result:
            return result
        return {"error": True, "message": f"필드 '{field_search}'를 찾을 수 없습니다."}

    if args.get("actions"):
        return {
            "transaction": tcode,
            "actions": store.query_actions(str(tcode)),
        }

    # action_search: 온톨로지 내 action_paths 검색
    if action_search := args.get("action_search"):
        ontology = store.load_transaction(str(tcode))
        if not ontology:
            return {"error": True, "message": f"'{tcode}' 온톨로지가 없습니다."}
        matches = ontology.find_action_path(str(action_search))
        if not matches:
            return {
                "transaction": tcode,
                "action_search": action_search,
                "count": 0,
                "paths": [],
                "hint": "일치하는 경로가 없습니다. sap_recipe_search로 외부 레시피를 검색하거나, 직접 트리를 탐색하세요.",
            }
        if len(matches) == 1:
            path, ap = matches[0]
            execution_plan = [
                {"order": i + 1, "tool": s.tool, "args": s.args, **({"hint": s.hint} if s.hint else {})}
                for i, s in enumerate(ap.steps)
            ]
            return {
                "transaction": tcode,
                "action_search": action_search,
                "count": 1,
                "exact_match": True,
                "path": path,
                "context": ap.context,
                "execution_plan": execution_plan,
                "instruction": (
                    f"경로 '{path}'가 검색되었습니다. "
                    "execution_plan의 단계를 순서대로 정확히 실행하세요. "
                    "별도 탐색(sap_tree_read, sap_screen)하지 마세요. "
                    "완료 후 sap_recipe_success를 호출하세요."
                ),
            }
        return {
            "transaction": tcode,
            "action_search": action_search,
            "count": len(matches),
            "exact_match": False,
            "paths": [
                {"path": p, "context": a.context, "keywords": a.keywords, "success_count": a.success_count}
                for p, a in matches
            ],
            "hint": "여러 경로가 검색되었습니다. context를 비교하여 적합한 경로를 선택한 후, action_search에 정확한 경로명을 입력하세요.",
        }

    ontology = store.load_transaction(str(tcode))
    if ontology:
        result = ontology.model_dump(mode="json")
        if ontology.recipes:
            recipe_store = _get_recipe_store()
            recipe_details = []
            for rid in ontology.recipes:
                r = recipe_store.load(rid)
                if r:
                    recipe_details.append({
                        "id": r.id,
                        "description": r.description,
                        "context": r.context,
                        "keywords": r.keywords,
                        "step_count": len(r.steps),
                        "success_count": r.success_count,
                    })
            recipe_details.sort(key=lambda x: -x["success_count"])
            if recipe_details:
                result["recipe_details"] = recipe_details
                result["recipe_hint"] = (
                    f"이 트랜잭션에 {len(recipe_details)}개의 레시피가 있습니다. "
                    "사용자의 요청과 description/context를 비교하여 적합한 레시피가 있으면 "
                    "sap_recipe_show(recipe_id=...)로 실행 계획을 조회하세요. "
                    "직접 탐색보다 레시피를 따르는 것이 빠르고 정확합니다."
                )
        if ontology.action_paths:
            result["available_actions"] = [
                {"path": p, "context": a.context, "step_count": len(a.steps), "success_count": a.success_count}
                for p, a in ontology.action_paths.items()
            ]
        return result
    return {"error": True, "message": f"'{tcode}' 온톨로지가 없습니다."}


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
    """필드 시맨틱 수동 입력/수정."""
    store = _get_store()
    tcode = str(args["tcode"]).upper()
    field_query = str(args["field"])

    ontology = store.load_transaction(tcode)
    if ontology is None:
        raise ValueError(f"온톨로지가 없습니다: {tcode}. 먼저 sap learn으로 학습하세요.")

    # 필드 검색: element_id 또는 field name으로 찾기
    updated = 0
    for screen in ontology.screens.values():
        for field in screen.fields:
            if field.id == field_query or field.name == field_query or field.semantic.sap_field_name == field_query:
                sem = field.semantic
                if "desc" in args and args["desc"]:
                    sem.description = args["desc"]
                if "desc_ko" in args and args["desc_ko"]:
                    sem.description_ko = args["desc_ko"]
                if "synonyms" in args and args["synonyms"]:
                    sem.synonyms = [s.strip() for s in args["synonyms"].split(",")]
                if "synonyms_ko" in args and args["synonyms_ko"]:
                    sem.synonyms_ko = [s.strip() for s in args["synonyms_ko"].split(",")]
                if "validation" in args and args["validation"]:
                    sem.validation = args["validation"]
                if "domain_context" in args and args["domain_context"]:
                    sem.domain_context = args["domain_context"]
                updated += 1

    if updated == 0:
        raise ValueError(f"필드를 찾을 수 없습니다: {field_query} (tcode={tcode})")

    store.save_transaction(ontology)
    return {
        "annotated": True,
        "tcode": tcode,
        "field": field_query,
        "screens_updated": updated,
    }


def _dispatch_annotate_dependency(args: dict[str, Any]) -> dict[str, Any]:
    """필드 종속 관계를 수동으로 추가."""
    from .ontology.schema import FieldDependencyType, FieldRelationship

    store = _get_store()
    source = str(args["source"]).upper()
    target = str(args["target"]).upper()
    dep_type_str = str(args.get("type") or "value_determines").lower()

    # 종속성 유형 검증
    valid_types = {t.value: t for t in FieldDependencyType}
    if dep_type_str not in valid_types:
        raise ValueError(
            f"잘못된 종속성 유형: {dep_type_str}. "
            f"가능한 값: {', '.join(valid_types.keys())}"
        )

    dep_type = valid_types[dep_type_str]
    description = args.get("description") or ""
    observed_in_raw = args.get("observed_in") or ""
    observed_in = [t.strip().upper() for t in observed_in_raw.split(",") if t.strip()] if observed_in_raw else []

    graph = store.load_field_dependencies()

    # 기존 동일 관계가 있으면 업데이트
    existing = None
    for rel in graph.relationships:
        if (
            rel.source_field.upper() == source
            and rel.target_field.upper() == target
            and rel.dependency_type == dep_type
        ):
            existing = rel
            break

    if existing:
        if description:
            existing.description = description
        for tcode in observed_in:
            if tcode not in existing.observed_in:
                existing.observed_in.append(tcode)
        action = "updated"
    else:
        new_rel = FieldRelationship(
            source_field=source,
            target_field=target,
            dependency_type=dep_type,
            description=description,
            observed_in=observed_in,
        )
        graph.relationships.append(new_rel)
        action = "added"

    store.save_field_dependencies(graph)
    return {
        "annotated": True,
        "action": action,
        "source_field": source,
        "target_field": target,
        "dependency_type": dep_type_str,
        "description": description,
        "observed_in": observed_in if observed_in else (existing.observed_in if existing else []),
        "total_relationships": len(graph.relationships),
    }


def _dispatch_process_list() -> dict[str, Any]:
    store = _get_store()
    process_ids = store.list_processes()
    processes = []
    for pid in process_ids:
        p = store.load_process(pid)
        if p:
            processes.append({
                "id": p.id,
                "name": p.name,
                "module": p.module,
                "steps_count": len(p.steps),
                "tags": p.tags,
            })
    return {"processes": processes, "count": len(processes)}


def _dispatch_process_show(args: dict[str, Any]) -> dict[str, Any]:
    process_id = str(args["process_id"])
    store = _get_store()
    process = store.load_process(process_id)
    if not process:
        return {"error": True, "message": f"프로세스 '{process_id}'를 찾을 수 없습니다."}
    return process.model_dump(mode="json")


def _dispatch_process_create(args: dict[str, Any]) -> dict[str, Any]:
    from .ontology.schema import BusinessProcess, DataFlow, ProcessStep

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
    store = _get_store()
    path = store.save_process(process)
    return {"saved": True, "process_id": process.id, "path": str(path)}


def _dispatch_process_delete(args: dict[str, Any]) -> dict[str, Any]:
    process_id = str(args["process_id"])
    deleted = _get_store().delete_process(process_id)
    return {"deleted": deleted, "process_id": process_id}


_active_executor: Any = None  # ProcessExecutor | None


def _dispatch_process_start(args: dict[str, Any]) -> dict[str, Any]:
    global _active_executor
    from .ontology.process_executor import ProcessExecutor

    process_id = str(args["process_id"])
    store = _get_store()
    process = store.load_process(process_id)
    if not process:
        return {"error": True, "message": f"프로세스 '{process_id}'를 찾을 수 없습니다."}

    _active_executor = ProcessExecutor(process)
    _active_executor.start()
    return _active_executor.get_status()


def _dispatch_process_step(args: dict[str, Any]) -> dict[str, Any]:
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
            step_number, outputs=outputs, status=status, error=error,
        )

    remaining = [
        s for s in _active_executor.process.steps
        if s.step_number > step_number
        and not any(r.step_number == s.step_number for r in _active_executor.execution.step_results)
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


def _dispatch_process_status() -> dict[str, Any]:
    if _active_executor is None:
        return {"error": True, "message": "실행 중인 프로세스가 없습니다."}
    return _active_executor.get_status()


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
    """필드 관계 조회 — 크로스 트랜잭션 필드 인덱스."""
    store = _get_store()

    if args.get("rebuild"):
        registry = store.rebuild_field_registry()
        return {
            "rebuilt": True,
            "field_count": len(registry.fields),
            "last_rebuilt": registry.last_rebuilt,
        }

    field_name = args.get("field")
    registry = store.load_field_registry()

    if field_name:
        key = field_name.upper()
        entry = registry.fields.get(key)
        if entry is None:
            # 부분 매칭
            matches = [
                e for k, e in registry.fields.items()
                if key in k.upper()
            ]
            if len(matches) == 1:
                entry = matches[0]
            elif matches:
                return {
                    "partial_matches": [
                        {
                            "sap_field_name": m.sap_field_name,
                            "description": m.description,
                            "transactions": sorted({o.tcode for o in m.occurrences}),
                        }
                        for m in matches
                    ]
                }
            else:
                return {"error": True, "message": f"필드 '{field_name}'를 레지스트리에서 찾을 수 없습니다."}
        return entry.model_dump(mode="json")

    # 필드 미지정 → 전체 요약
    return {
        "field_count": len(registry.fields),
        "last_rebuilt": registry.last_rebuilt,
        "fields": {
            k: {
                "description": v.description,
                "occurrence_count": len(v.occurrences),
                "transactions": sorted({o.tcode for o in v.occurrences}),
            }
            for k, v in sorted(registry.fields.items())
        },
    }


def _dispatch_field_dependencies(args: dict[str, Any]) -> dict[str, Any]:
    """필드 종속성 조회 — 글로벌 필드 종속성 그래프."""
    store = _get_store()

    if args.get("rebuild"):
        graph = store.rebuild_field_dependencies()
        return {
            "rebuilt": True,
            "relationship_count": len(graph.relationships),
            "last_rebuilt": graph.last_rebuilt,
        }

    field_name = args.get("field")
    graph = store.load_field_dependencies()

    if not graph.relationships:
        return {"error": True, "message": "필드 종속성 데이터가 없습니다. --rebuild로 먼저 구축하세요."}

    if field_name:
        key = field_name.upper()
        # source 또는 target에 해당 필드가 있는 관계 검색
        matches = [
            r for r in graph.relationships
            if key in r.source_field.upper() or key in r.target_field.upper()
        ]
        if not matches:
            return {"error": True, "message": f"필드 '{field_name}'의 종속 관계를 찾을 수 없습니다."}
        return {
            "field": field_name,
            "relationships": [r.model_dump(mode="json") for r in matches],
        }

    # 필드 미지정 → 전체 요약
    return {
        "relationship_count": len(graph.relationships),
        "last_rebuilt": graph.last_rebuilt,
        "relationships": [
            {
                "source": r.source_field,
                "target": r.target_field,
                "type": r.dependency_type,
                "observed_in": r.observed_in,
            }
            for r in graph.relationships
        ],
    }


def _dispatch_document_flow(args: dict[str, Any]) -> dict[str, Any]:
    """문서 흐름 조회 — 트랜잭션 간 문서 흐름 온톨로지."""
    store = _get_store()

    if args.get("rebuild"):
        flow = store.rebuild_document_flow()
        return {
            "rebuilt": True,
            "edge_count": len(flow.edges),
            "last_rebuilt": flow.last_rebuilt,
        }

    tcode = args.get("tcode")
    flow = store.load_document_flow()

    if not flow.edges:
        return {"error": True, "message": "문서 흐름 데이터가 없습니다. --rebuild로 먼저 구축하세요."}

    if tcode:
        key = tcode.upper()
        # source 또는 target에 해당 tcode가 있는 엣지 검색
        matches = [
            e for e in flow.edges
            if key == e.source_tcode.upper() or key == e.target_tcode.upper()
        ]
        if not matches:
            return {"error": True, "message": f"트랜잭션 '{tcode}'의 문서 흐름을 찾을 수 없습니다."}
        return {
            "tcode": tcode,
            "edges": [e.model_dump(mode="json") for e in matches],
        }

    # tcode 미지정 → 전체 요약
    return {
        "edge_count": len(flow.edges),
        "last_rebuilt": flow.last_rebuilt,
        "edges": [
            {
                "source": e.source_tcode,
                "target": e.target_tcode,
                "flow_type": e.flow_type,
                "linking_field": e.linking_field,
                "confidence": e.confidence,
            }
            for e in flow.edges
        ],
    }


def _dispatch_ontology_rebuild() -> dict[str, Any]:
    """온톨로지 전체 재구축 — field_registry + field_dependencies + document_flow 일괄."""
    store = _get_store()

    registry = store.rebuild_field_registry()
    deps = store.rebuild_field_dependencies()
    flow = store.rebuild_document_flow()

    return {
        "rebuilt": True,
        "field_registry": {
            "field_count": len(registry.fields),
            "last_rebuilt": registry.last_rebuilt,
        },
        "field_dependencies": {
            "relationship_count": len(deps.relationships),
            "last_rebuilt": deps.last_rebuilt,
        },
        "document_flow": {
            "edge_count": len(flow.edges),
            "last_rebuilt": flow.last_rebuilt,
        },
    }


def _dispatch_process_path(args: dict[str, Any]) -> dict[str, Any]:
    """두 트랜잭션 간 문서 흐름 경로 탐색 (BFS)."""
    from .ontology.graph import OntologyGraph

    from_tcode = args.get("from_tcode", "")
    to_tcode = args.get("to_tcode", "")
    if not from_tcode or not to_tcode:
        return {"error": True, "message": "from_tcode와 to_tcode가 필요합니다."}

    store = _get_store()
    graph = OntologyGraph(store)
    graph.build_from_store()

    path = graph.get_process_path(from_tcode, to_tcode)
    if path is None:
        return {
            "error": True,
            "message": f"{from_tcode} → {to_tcode} 경로를 찾을 수 없습니다. 문서 흐름 데이터를 --rebuild로 구축했는지 확인하세요.",
        }

    # 경로의 각 엣지에 대한 상세 정보 추가
    edges_detail = []
    flow = store.load_document_flow()
    flow_map = {(e.source_tcode, e.target_tcode): e for e in flow.edges}
    for i in range(len(path) - 1):
        edge = flow_map.get((path[i], path[i + 1]))
        if edge:
            edges_detail.append({
                "from": path[i],
                "to": path[i + 1],
                "linking_field": edge.linking_field,
                "flow_type": edge.flow_type.value,
                "confidence": edge.confidence,
            })
        else:
            edges_detail.append({"from": path[i], "to": path[i + 1]})

    return {
        "from": from_tcode.upper(),
        "to": to_tcode.upper(),
        "path": path,
        "path_display": " → ".join(path),
        "steps": edges_detail,
    }


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
    policy = load_policy()
    config = policy.session if policy else None
    if config and not config.enabled:
        return {"error": "세션 로깅이 비활성화되어 있습니다"}
    store = _get_session_store(config)

    sap_user, sap_system = "", ""
    sap = _get_existing_sap(ctx.slot_id)
    if sap is not None:
        try:
            info = sap.get_session_info()
            sap_user = info.user
            sap_system = info.system_name
        except Exception:
            pass

    session = store.create_session(
        user_request=str(args.get("user_request", "")),
        ai_reasoning=str(args.get("ai_reasoning", "")),
        agent=ctx.agent_name or "",
        sap_user=sap_user,
        sap_system=sap_system,
        source=ctx.source,
    )
    return {
        "session_id": session.id,
        "status": session.status,
        "started_at": session.started_at,
    }


def _dispatch_session_end(args: dict[str, Any]) -> dict[str, Any]:
    from .session_analyzer import analyze, session_to_recipe

    store = _get_session_store()
    session_id = str(args["session_id"])
    outcome = str(args.get("outcome", "success"))
    summary = str(args.get("outcome_summary", ""))
    session = store.finish_session(session_id, outcome=outcome, outcome_summary=summary)
    if session is None:
        return {"error": f"세션을 찾을 수 없습니다: {session_id}"}

    payload: dict[str, Any] = {
        "session_id": session.id,
        "status": session.status,
        "outcome": session.outcome,
        "step_count": len(session.steps),
        "finished_at": session.finished_at,
    }

    try:
        analysis = analyze(session)
        if session.candidate and not session.approved:
            store._save(session)
            preview = session_to_recipe(session)
            steps_preview = preview.get("steps", [])
            payload["recipe_suggestion"] = {
                "candidate": True,
                "suggested_recipe_id": preview.get("id", ""),
                "description": preview.get("description", ""),
                "core_step_count": len(steps_preview),
                "exploration_count": analysis.get("exploration_count", 0),
                "recovery_count": analysis.get("recovery_count", 0),
                "keywords": preview.get("keywords", []),
                "instruction": (
                    "이 세션은 재사용 가치가 있는 성공 경로로 평가되었습니다. "
                    "사용자에게 레시피 저장 의사를 확인한 후 sap_session_approve로 승인하세요. "
                    "approve 시 자동으로 sap_recipe_save가 실행됩니다."
                ),
            }
    except Exception as exc:
        logging.getLogger(__name__).warning(
            "recipe_suggestion 생성 실패(session=%s): %s", session_id, exc
        )

    return payload


def _dispatch_session_list(args: dict[str, Any]) -> dict[str, Any]:
    store = _get_session_store()
    sessions = store.list_sessions(
        date=args.get("date"),
        status=args.get("status"),
        limit=int(args.get("limit", 20)),
    )
    return {"sessions": sessions, "count": len(sessions)}


def _dispatch_session_show(args: dict[str, Any]) -> dict[str, Any]:
    store = _get_session_store()
    session_id = str(args["session_id"])
    session = store.load(session_id)
    if session is None:
        return {"error": f"세션을 찾을 수 없습니다: {session_id}"}
    return session.model_dump(mode="json", exclude_none=True)


def _dispatch_session_candidates(args: dict[str, Any]) -> dict[str, Any]:
    from .session_analyzer import analyze

    store = _get_session_store()
    sessions = store.list_sessions(
        status="completed",
        limit=int(args.get("limit", 50)),
    )
    candidates = []
    for s_summary in sessions:
        session = store.load(s_summary["id"])
        if session is None:
            continue
        analyze(session)
        if not session.candidate or session.approved:
            continue
        candidates.append({
            "id": session.id,
            "user_request": session.user_request,
            "outcome": session.outcome,
            "step_count": len(session.steps),
            "started_at": session.started_at,
            "sap_user": session.sap_user,
        })
    return {"candidates": candidates, "count": len(candidates)}


def _dispatch_session_approve(args: dict[str, Any]) -> dict[str, Any]:
    from .recipe import Recipe, RecipeStep as RStep
    from .session_analyzer import analyze, approve_session

    store = _get_session_store()
    session_id = str(args["session_id"])
    approved_by = str(args.get("approved_by", "admin"))

    session = store.load(session_id)
    if session is None:
        return {"error": f"세션을 찾을 수 없습니다: {session_id}"}

    analyze(session)
    if not session.candidate:
        return {"error": "이 세션은 승인 후보(candidate)가 아닙니다"}
    if session.approved:
        return {"error": f"이미 승인된 세션입니다 (recipe_id: {session.recipe_id})"}

    recipe_data = approve_session(session, approved_by=approved_by)
    if "error" in recipe_data:
        return recipe_data

    recipe_store = _get_recipe_store()
    steps = [RStep(**s) if isinstance(s, dict) else s for s in recipe_data.pop("steps", [])]
    recipe = Recipe(**recipe_data, steps=steps)
    recipe_store.save(recipe)

    store._save(session)

    linked_tcodes = _link_recipe_to_ontology(
        recipe.id, recipe.steps,
        description=recipe.description,
        context=recipe.context,
        keywords=recipe.keywords,
    )

    return {
        "approved": True,
        "session_id": session_id,
        "recipe_id": recipe.id,
        "step_count": len(recipe.steps),
        "linked_tcodes": linked_tcodes,
    }


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


SESSION_SKIP_COMMANDS = {
    "session_start", "session_end", "session_list", "session_show",
    "session_candidates", "session_approve",
    "transcript_query", "transcript_plan",
    "ontology_query", "ontology_ask", "macro_list", "macro_show", "macro_delete",
    "capture", "capture_list", "recipe_query", "recipe_pending",
}


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
    if not ctx.session_id or command in SESSION_SKIP_COMMANDS:
        return
    try:
        store = _get_session_store()
        if not store.auto_log_steps:
            return
        status_msg = ""
        if after_state and isinstance(after_state.get("status"), dict):
            status_msg = after_state["status"].get("message", "")
        store.add_step(
            ctx.session_id,
            command=command,
            tool_name=tool_name,
            arguments=arguments,
            outcome=outcome,
            error=error,
            before_state=before_state,
            after_state=after_state,
            status_message=status_msg,
        )
    except Exception:
        _logger.warning("세션 단계 기록 실패: session_id=%s, command=%s", ctx.session_id, command, exc_info=True)


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
    from .service import get_pool
    pool = get_pool()
    return {
        "slot_count": pool.slot_count(),
        "max_sessions": pool.max_sessions,
        "slots": pool.list_slots(),
    }


def _dispatch_slot_allocate(args: dict[str, Any], ctx: DispatchContext) -> dict[str, Any]:
    from .service import get_pool
    slot_id = str(args.get("slot_id", ""))
    if not slot_id:
        raise ValueError("slot_id가 필요합니다.")
    agent_name = args.get("agent_name") or ctx.agent_name
    system = args.get("system")
    session_index = int(args.get("session_index", -1))
    create_new = bool(args.get("create_new_session", False))

    pool = get_pool()
    wrapper = pool.get_or_create(slot_id, agent_name=agent_name)

    result: dict[str, Any] = {
        "allocated": True,
        "slot_id": slot_id,
        "agent_name": agent_name,
    }

    # create_new_session: 기존 연결에서 새 SAP 세션을 생성하여 할당
    if create_new:
        from .service import get_sap
        base_wrapper = get_sap("ses0")
        new_idx = base_wrapper.create_session()
        session_index = new_idx
        result["created_session_index"] = new_idx

    # 시스템이나 세션 인덱스가 지정되면 바로 연결
    if system or session_index >= 0:
        info = wrapper.connect(
            session_index=max(session_index, 0),
            system=system,
        )
        pool.update_slot_connection(
            slot_id,
            wrapper._last_connection_index,
            wrapper._last_session_index,
            system_id=system,
        )
        result["connected"] = True
        result["session"] = info.model_dump(mode="json")

    return result


def _dispatch_slot_release(args: dict[str, Any]) -> dict[str, Any]:
    from .service import get_pool
    slot_id = str(args.get("slot_id", ""))
    if not slot_id:
        raise ValueError("slot_id가 필요합니다.")
    close = args.get("close", False)

    pool = get_pool()
    if close:
        ok = pool.close(slot_id)
    else:
        ok = pool.release(slot_id)
    return {"released": ok, "slot_id": slot_id, "closed": close}
