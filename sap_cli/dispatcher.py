"""중앙 명령 dispatcher.

CLI, daemon, MCP 서버가 모두 이 경로를 타도록 만들어
권한, 감사, 실행, 후처리 문맥을 한곳에 모은다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from .auth.audit import AuditLogger
from .auth.checker import PermissionChecker
from .auth.policy import TOOL_PERMISSION_MAP, Permission, load_policy
from .command_parser import extract_tcode_from_command
from .com_wrapper import SapGuiError, SapGuiWrapper
from .macro.player import MacroPlayer
from .service import (
    execute_sap_action as _execute_sap_action,
    get_learner as _get_learner,
    get_macro_store as _get_macro_store,
    get_recorder as _get_recorder,
    get_sap as _get_sap,
    get_store as _get_store,
    map_action as _map_action,
)

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
    "learn":          CommandDef("sap_learn",             Permission.ADMIN),
    "record_step":    CommandDef("sap_macro_record_step", Permission.WRITE),
    "macro_control":  CommandDef("sap_macro_control",     Permission.ADMIN),
    "macro_play":     CommandDef("sap_macro_play",        Permission.EXECUTE),
    "macro_list":     CommandDef("sap_macro_list",        Permission.READ),
    "macro_show":     CommandDef("sap_macro_show",        Permission.READ),
    "macro_delete":   CommandDef("sap_macro_delete",      Permission.ADMIN),
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
class DispatchContext:
    source: str = "cli"
    tool_name: str | None = None
    agent_name: str | None = None
    enforce_auth: bool = True
    include_post_action_context: bool = True


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

    if ctx.enforce_auth and tool_name:
        _authorize(tool_name, args, ctx)

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
    if command == "learn":
        return _dispatch_learn()

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


def _dispatch_learn() -> dict[str, Any]:
    screen = _get_learner().learn_current_screen()
    return {
        "learned": True,
        "transaction": screen.transaction,
        "screen_number": screen.screen_number,
        "fields_count": len(screen.fields),
        "buttons_count": len(screen.buttons),
    }


def _authorize(tool_name: str, args: dict[str, Any], ctx: DispatchContext) -> None:
    policy = load_policy()
    checker = PermissionChecker(policy)
    configured_agent = _resolve_agent_name(policy, ctx.agent_name)
    if configured_agent:
        checker.set_agent(configured_agent)
    auditor = AuditLogger(policy.audit if policy else None)

    check = checker.check_tool(tool_name, args)
    permission = TOOL_PERMISSION_MAP.get(tool_name, "")
    resource = checker.resolve_resource(tool_name, args)
    effective_agent = checker.agent_name or configured_agent or "anonymous"

    if not check:
        auditor.log(
            agent=effective_agent,
            tool_name=tool_name,
            arguments=args,
            allowed=False,
            reason=check.reason,
            permission=permission.value if permission else "",
            resource=resource,
        )
        raise PermissionDeniedError(f"권한 없음: {check.reason}")

    approval_reason = checker.requires_approval(tool_name, args)
    if approval_reason:
        auditor.log(
            agent=effective_agent,
            tool_name=tool_name,
            arguments=args,
            allowed=False,
            reason=f"approval_required: {approval_reason}",
            permission=permission.value if permission else "",
            resource=resource,
        )
        raise ApprovalRequiredError(tool_name, approval_reason)

    auditor.log(
        agent=effective_agent,
        tool_name=tool_name,
        arguments=args,
        allowed=True,
        reason=check.reason,
        permission=permission.value if permission else "",
        resource=resource,
    )


def _resolve_agent_name(policy: Any, explicit_agent_name: str | None) -> str | None:
    env_agent = os.getenv("SAP_CLI_AGENT", "").strip() or None
    candidate = explicit_agent_name or env_agent
    if not policy or not candidate:
        return None
    if policy.get_agent(candidate):
        return candidate
    return None


def _with_post_action(
    payload: dict[str, Any],
    sap: SapGuiWrapper,
    command: str,
    ctx: DispatchContext,
) -> dict[str, Any]:
    if ctx.include_post_action_context and command in POST_ACTION_COMMANDS:
        payload["post_action"] = _collect_post_action(sap)

    post_action = payload.get("post_action")
    if post_action:
        session = post_action.get("session", {})
        status = post_action.get("status", {})
        payload.setdefault("transaction", session.get("transaction"))
        payload.setdefault("screen_number", session.get("screen_number"))
        payload.setdefault("status", status)
    return payload


def _collect_post_action(sap: SapGuiWrapper) -> dict[str, Any]:
    session = sap.get_session_info().model_dump(mode="json")
    status = sap.get_status().model_dump(mode="json")
    return {
        "session": session,
        "status": status,
        "modal": _collect_modal(sap),
    }


def _collect_modal(sap: SapGuiWrapper) -> dict[str, Any]:
    has_modal = sap.has_modal()
    payload: dict[str, Any] = {"has_modal": has_modal}
    if has_modal:
        payload["text"] = sap.get_modal_text()
    return payload
