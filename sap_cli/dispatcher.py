"""중앙 명령 dispatcher.

CLI, daemon, MCP 서버가 모두 이 경로를 타도록 만들어
권한, 감사, 실행, 후처리 문맥을 한곳에 모은다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .auth.audit import AuditLogger
from .auth.checker import PermissionChecker
from .auth.policy import TOOL_PERMISSION_MAP, Permission, Policy, load_policy
from .auth.transcript import TranscriptLogger
from .command_parser import extract_tcode_from_command
from .com_wrapper import SapGuiWrapper
from .macro.player import MacroPlayer
from .ontology.store import OntologyStore
from .service import (
    execute_sap_action as _execute_sap_action,
    get_learner as _get_learner,
    get_macro_store as _get_macro_store,
    get_recorder as _get_recorder,
    get_sap as _get_sap,
    get_store as _get_store,
    map_action as _map_action,
)
from .transcript_planner import TranscriptPlanner
from .transcript_store import TranscriptStore

@dataclass(slots=True)
class CommandDef:
    tool_name: str
    permission: Permission
    mcp_arg_map: dict[str, str] | None = None  # MCP인자명 -> dispatcher인자명


COMMAND_REGISTRY: dict[str, CommandDef] = {
    "connect":        CommandDef("sap_connect",           Permission.ADMIN),
    "session":        CommandDef("sap_session",           Permission.READ),
    "screen":         CommandDef("sap_screen",            Permission.READ),
    "run":            CommandDef("sap_run",               Permission.EXECUTE),
    "set":            CommandDef("sap_set_field",         Permission.WRITE),
    "get":            CommandDef("sap_get_field",         Permission.READ),
    "press":          CommandDef("sap_press",             Permission.EXECUTE),
    "vkey":           CommandDef("sap_send_vkey",         Permission.EXECUTE, {"vkey": "key"}),
    "status":         CommandDef("sap_get_status",        Permission.READ),
    "table":          CommandDef("sap_table_read",        Permission.READ),
    "command":        CommandDef("sap_command",           Permission.EXECUTE),
    "modal":          CommandDef("sap_modal",             Permission.READ),
    "ontology_query": CommandDef("sap_ontology_query",    Permission.READ),
    "transcript_query": CommandDef("sap_transcript_query", Permission.READ),
    "transcript_plan": CommandDef("sap_transcript_plan", Permission.READ),
    "learn":          CommandDef("sap_learn",             Permission.ADMIN),
    "record_step":    CommandDef("sap_macro_record_step", Permission.WRITE),
    "macro_control":  CommandDef("sap_macro_control",     Permission.ADMIN),
    "macro_play":     CommandDef("sap_macro_play",        Permission.EXECUTE),
    "macro_list":     CommandDef("sap_macro_list",        Permission.READ),
    "macro_show":     CommandDef("sap_macro_show",        Permission.READ),
    "macro_delete":   CommandDef("sap_macro_delete",      Permission.ADMIN),
    "table_select":   CommandDef("sap_table_select",      Permission.EXECUTE),
    "table_doubleclick": CommandDef("sap_table_doubleclick", Permission.EXECUTE),
    "process_list":   CommandDef("sap_process_list",      Permission.READ),
    "process_show":   CommandDef("sap_process_show",      Permission.READ),
    "process_create": CommandDef("sap_process_create",    Permission.ADMIN),
    "process_delete": CommandDef("sap_process_delete",    Permission.ADMIN),
    "process_start":  CommandDef("sap_process_start",     Permission.EXECUTE),
    "process_step":   CommandDef("sap_process_step",      Permission.EXECUTE),
    "process_status": CommandDef("sap_process_status",    Permission.READ),
    "annotate":       CommandDef("sap_annotate_field",    Permission.WRITE),
    "annotate_dependency": CommandDef("sap_annotate_dependency", Permission.WRITE),
    "fill":           CommandDef("sap_fill_fields",       Permission.WRITE),
    "tree":           CommandDef("sap_tree_read",          Permission.READ),
    "tree_select":    CommandDef("sap_tree_select",        Permission.EXECUTE),
    "tree_doubleclick": CommandDef("sap_tree_doubleclick", Permission.EXECUTE),
    "tree_expand":    CommandDef("sap_tree_expand",        Permission.EXECUTE),
    "tree_collapse":  CommandDef("sap_tree_collapse",      Permission.EXECUTE),
    "tree_press":     CommandDef("sap_tree_press",         Permission.EXECUTE),
    "ontology_relationships": CommandDef("sap_ontology_relationships", Permission.READ),
    "field_dependencies": CommandDef("sap_field_dependencies", Permission.READ),
    "document_flow":      CommandDef("sap_document_flow",      Permission.READ),
    "process_path":       CommandDef("sap_process_path",       Permission.READ),
    "ontology_rebuild":   CommandDef("sap_ontology_rebuild",   Permission.ADMIN),
    "capture":        CommandDef("sap_capture",           Permission.READ),
    "capture_list":   CommandDef("sap_capture_list",      Permission.READ),
    "recipe_save":    CommandDef("sap_recipe_save",       Permission.WRITE),
    "recipe_search":  CommandDef("sap_recipe_search",     Permission.READ),
    "recipe_list":    CommandDef("sap_recipe_list",       Permission.READ),
    "recipe_delete":  CommandDef("sap_recipe_delete",     Permission.ADMIN),
    "recipe_success": CommandDef("sap_recipe_success",    Permission.WRITE),
}

COMMAND_TOOL_MAP: dict[str, str] = {cmd: d.tool_name for cmd, d in COMMAND_REGISTRY.items()}
MCP_TOOL_TO_COMMAND: dict[str, str] = {d.tool_name: cmd for cmd, d in COMMAND_REGISTRY.items()}

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
    transcript_with_screen_summary = transcript.include_screen_summary or ctx.include_screen_summary
    before_state = None
    if should_log_transcript:
        before_state = _capture_transcript_state(
            command,
            include_screen_summary=transcript_with_screen_summary,
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
        if should_log_transcript:
            after_state = _capture_transcript_state(
                command,
                include_screen_summary=transcript_with_screen_summary,
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
        raise

    after_state = _extract_transcript_state_from_result(result)
    if should_log_transcript and after_state is None:
        after_state = _capture_transcript_state(
            command,
            include_screen_summary=transcript_with_screen_summary,
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
    return result


def _execute_command(
    command: str,
    args: dict[str, Any],
    ctx: DispatchContext,
    policy: Policy | None = None,
) -> dict[str, Any]:
    if command == "connect":
        return _dispatch_connect(args, ctx)
    if command == "session":
        return _get_sap().get_session_info().model_dump(mode="json")
    if command == "screen":
        return _dispatch_screen(args)
    if command == "run":
        sap = _get_sap()
        tcode = str(args["tcode"])
        sap.run_transaction(tcode)
        info = sap.get_session_info()
        payload = {
            "executed": tcode,
            "current_transaction": info.transaction,
            "screen_number": info.screen_number,
        }
        return _with_post_action(payload, sap, command, ctx)
    if command == "set":
        sap = _get_sap()
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
        sap = _get_sap()
        element_ids = args.get("element_ids") or [str(args["element_id"])]
        if len(element_ids) == 1:
            return sap.get_field_info(element_ids[0])
        return [sap.get_field_info(eid) for eid in element_ids]
    if command == "press":
        sap = _get_sap()
        element_id = str(args["element_id"])
        sap.press(element_id)
        payload = {"pressed": element_id}
        return _with_post_action(payload, sap, command, ctx)
    if command == "vkey":
        sap = _get_sap()
        key = int(args["key"])
        sap.send_vkey(key)
        payload = {"vkey_sent": key}
        return _with_post_action(payload, sap, command, ctx)
    if command == "status":
        return _get_sap().get_status().model_dump(mode="json")
    if command == "table":
        element_id = str(args["element_id"])
        return _get_sap().read_table(element_id).model_dump(mode="json")
    if command == "tree":
        element_id = str(args["element_id"])
        node_key = str(args.get("node_key", "")).strip()
        depth = int(args.get("depth", -1))
        sap = _get_sap()
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
        sap = _get_sap()
        sap.tree_select_node(str(args["element_id"]), str(args["node_key"]))
        payload = {"action": "select", "element_id": args["element_id"], "node_key": args["node_key"]}
        return _with_post_action(payload, sap, command, ctx)
    if command == "tree_doubleclick":
        sap = _get_sap()
        sap.tree_double_click_node(str(args["element_id"]), str(args["node_key"]))
        payload = {"action": "doubleclick", "element_id": args["element_id"], "node_key": args["node_key"]}
        return _with_post_action(payload, sap, command, ctx)
    if command == "tree_expand":
        sap = _get_sap()
        sap.tree_expand_node(str(args["element_id"]), str(args["node_key"]))
        return {"action": "expand", "element_id": args["element_id"], "node_key": args["node_key"]}
    if command == "tree_collapse":
        sap = _get_sap()
        sap.tree_collapse_node(str(args["element_id"]), str(args["node_key"]))
        return {"action": "collapse", "element_id": args["element_id"], "node_key": args["node_key"]}
    if command == "tree_press":
        sap = _get_sap()
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
        sap = _get_sap()
        command_text = str(args["cmd"])
        sap.send_command(command_text)
        payload = {"command": command_text}
        return _with_post_action(payload, sap, command, ctx)
    if command == "modal":
        return _collect_modal(_get_sap())
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
        sap = _get_sap()
        element_id = str(args["element_id"])
        row = int(args["row"])
        sap.select_table_row(element_id, row)
        payload = {"selected": True, "table": element_id, "row": row}
        return _with_post_action(payload, sap, command, ctx)
    if command == "table_doubleclick":
        sap = _get_sap()
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
        return _dispatch_learn()
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
        return _dispatch_capture(args)
    if command == "capture_list":
        return _dispatch_capture_list(args)
    if command == "recipe_save":
        return _dispatch_recipe_save(args)
    if command == "recipe_search":
        return _dispatch_recipe_search(args)
    if command == "recipe_list":
        return _dispatch_recipe_list()
    if command == "recipe_delete":
        return _dispatch_recipe_delete(args)
    if command == "recipe_success":
        return _dispatch_recipe_success(args)
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
    import sap_cli.service as _svc

    sap = SapGuiWrapper()
    info = sap.connect(
        int(args.get("connection", args.get("connection_index", 0))),
        int(args.get("session", args.get("session_index", 0))),
    )
    _svc._sap = sap
    payload = {
        "connected": True,
        "session": info.model_dump(mode="json"),
    }
    return _with_post_action(payload, sap, "connect", ctx)


def _dispatch_screen(args: dict[str, Any]) -> dict[str, Any]:
    sap = _get_sap()
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
    sap = _get_sap()
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
    sap = _get_sap()
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


def _dispatch_ontology_query(args: dict[str, Any]) -> dict[str, Any]:
    store = _get_store()
    if args.get("list_all"):
        return {
            "transactions": store.list_transactions(),
            "patterns": store.list_patterns(),
            "processes": store.list_processes(),
        }

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

    ontology = store.load_transaction(str(tcode))
    if ontology:
        return ontology.model_dump(mode="json")
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


def _dispatch_learn() -> dict[str, Any]:
    screen = _get_learner().learn_current_screen()
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
    sap = _get_sap()
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


def _dispatch_capture(args: dict[str, Any]) -> dict[str, Any]:
    """현재 SAP GUI 화면 스크린샷 캡처."""
    from .screenshot import capture_sap_window, capture_for_step

    sap = _get_sap()
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
    from .recipe import RecipeStore
    return RecipeStore()


def _dispatch_recipe_save(args: dict[str, Any]) -> dict[str, Any]:
    from .recipe import Recipe, RecipeStep

    store = _get_recipe_store()
    recipe_id = str(args["recipe_id"])
    description = str(args["description"])
    keywords = args.get("keywords", [])
    if isinstance(keywords, str):
        keywords = [k.strip() for k in keywords.split(",")]
    steps_raw = args.get("steps", [])
    steps = [RecipeStep(**s) if isinstance(s, dict) else s for s in steps_raw]

    existing = store.load(recipe_id)
    if existing:
        existing.description = description
        existing.keywords = keywords
        existing.steps = steps
        store.save(existing)
        return {"saved": recipe_id, "updated": True, "step_count": len(steps)}

    recipe = Recipe(
        id=recipe_id,
        description=description,
        keywords=keywords,
        steps=steps,
    )
    store.save(recipe)
    return {"saved": recipe_id, "updated": False, "step_count": len(steps)}


def _dispatch_recipe_search(args: dict[str, Any]) -> dict[str, Any]:
    store = _get_recipe_store()
    query = str(args["query"])
    results = store.search(query)
    return {
        "query": query,
        "count": len(results),
        "recipes": [r.model_dump(mode="json") for r in results],
    }


def _dispatch_recipe_list() -> dict[str, Any]:
    store = _get_recipe_store()
    recipes = store.list_all()
    return {"count": len(recipes), "recipes": recipes}


def _dispatch_recipe_delete(args: dict[str, Any]) -> dict[str, Any]:
    store = _get_recipe_store()
    recipe_id = str(args["recipe_id"])
    deleted = store.delete(recipe_id)
    return {"deleted": deleted, "recipe_id": recipe_id}


def _dispatch_recipe_success(args: dict[str, Any]) -> dict[str, Any]:
    store = _get_recipe_store()
    recipe_id = str(args["recipe_id"])
    store.increment_success(recipe_id)
    recipe = store.load(recipe_id)
    return {
        "recipe_id": recipe_id,
        "success_count": recipe.success_count if recipe else 0,
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


def _capture_transcript_state(
    command: str,
    *,
    include_screen_summary: bool,
) -> dict[str, Any] | None:
    if command not in COMMAND_REGISTRY:
        return None
    if command in {"ontology_query", "macro_control", "macro_list", "macro_show", "macro_delete", "transcript_query", "transcript_plan"}:
        return None

    sap = _get_existing_sap()
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


def _get_existing_sap() -> SapGuiWrapper | None:
    import sap_cli.service as _svc

    sap = getattr(_svc, "_sap", None)
    if sap is None:
        return None
    try:
        is_connected = getattr(sap, "is_connected")
    except Exception:  # pragma: no cover - defensive path
        return sap
    return sap if is_connected else None


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
