"""중앙 명령 dispatcher.

CLI, daemon, MCP 서버가 모두 이 경로를 타도록 만들어
권한, 감사, 실행, 후처리 문맥을 한곳에 모은다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
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
    "process_list":   CommandDef("sap_process_list",      Permission.READ),
    "process_show":   CommandDef("sap_process_show",      Permission.READ),
    "process_create": CommandDef("sap_process_create",    Permission.ADMIN),
    "process_delete": CommandDef("sap_process_delete",    Permission.ADMIN),
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
        sap.set_field(element_id, value)
        payload = {
            "field": element_id,
            "value_set": value,
            "actual_value": sap.get_field(element_id),
        }
        return _with_post_action(payload, sap, command, ctx)
    if command == "get":
        element_id = str(args["element_id"])
        return {"field": element_id, "value": _get_sap().get_field(element_id)}
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
    if args.get("fields_only"):
        fields = screen.get_changeable_fields()
        return {
            "transaction": screen.transaction,
            "screen_number": screen.screen_number,
            "changeable_fields": [field.model_dump(mode="json") for field in fields],
        }
    if args.get("actions_only"):
        actions = screen.get_available_actions()
        return {
            "transaction": screen.transaction,
            "screen_number": screen.screen_number,
            "available_actions": [action.model_dump(mode="json") for action in actions],
        }
    return screen.model_dump(mode="json")


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


def _dispatch_macro_show(args: dict[str, Any]) -> dict[str, Any]:
    store = _get_macro_store()
    macro_id = str(args["macro_id"])
    macro = store.load(macro_id)
    if not macro:
        return {"error": True, "message": f"매크로 '{macro_id}'를 찾을 수 없습니다."}
    return macro.model_dump(mode="json")


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
