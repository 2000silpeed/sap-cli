"""Macro command dispatch helpers.

Macro command implementations live here while :mod:`sap_cli.dispatcher`
keeps thin private wrappers for backward-compatible imports and monkeypatch-
based tests.
"""

from __future__ import annotations

import copy
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from ..auth.preflight import preflight_tool_call
from ..macro.compiler import MacroCompileError, compile_macro
from ..macro.schema import MacroStepResult

Factory = Callable[..., Any]
PostActionHook = Callable[[dict[str, Any], Any, str, Any], dict[str, Any]]


def _redact_values(value: Any, secrets: tuple[str, ...]) -> Any:
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, "<redacted>")
        return value
    if isinstance(value, dict):
        return {key: _redact_values(item, secrets) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_values(item, secrets) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_values(item, secrets) for item in value)
    return value


MACRO_COMMANDS: frozenset[str] = frozenset(
    {
        "macro_control",
        "macro_play",
        "macro_list",
        "macro_show",
        "macro_delete",
    }
)


def is_macro_command(command: str) -> bool:
    """Return whether ``command`` belongs to the macro dispatcher group."""
    return command in MACRO_COMMANDS


def dispatch_macro_command(
    command: str,
    args: dict[str, Any],
    ctx: Any,
    *,
    get_recorder: Factory,
    get_macro_store: Factory,
    get_sap: Factory,
    with_post_action: PostActionHook,
    dispatch_step: Factory | None = None,
    policy: Any = None,
) -> dict[str, Any]:
    """Dispatch a macro command with facade-provided compatibility hooks."""
    if command == "macro_control":
        return dispatch_macro_control(args, get_recorder, get_macro_store)
    if command == "macro_play":
        return dispatch_macro_play(
            args,
            ctx,
            get_macro_store,
            get_sap,
            with_post_action,
            dispatch_step=dispatch_step,
            policy=policy,
        )
    if command == "macro_list":
        return dispatch_macro_list(args, get_macro_store)
    if command == "macro_show":
        return dispatch_macro_show(args, get_macro_store)
    if command == "macro_delete":
        return dispatch_macro_delete(args, get_macro_store)
    raise ValueError(f"미구현 macro 명령: {command}")


def dispatch_macro_control(
    args: dict[str, Any],
    get_recorder: Factory,
    get_macro_store: Factory,
) -> dict[str, Any]:
    """Control macro recording lifecycle."""
    command = str(args["command"])
    recorder = get_recorder()

    if command == "start":
        macro_name = str(args.get("name", "unnamed"))
        recorder.start(macro_name)
        return {"recording": True, "name": macro_name}

    if command == "finish":
        store = get_macro_store()
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
        removed_steps = recorder.undo(int(args.get("count", 1) or 1))
        removed = removed_steps[0] if removed_steps else None
        if removed:
            return {
                "undone": True,
                "removed_step": removed.step_number,
                "action": removed.action.value,
                "removed_count": len(removed_steps),
                "removed_steps": [step.step_number for step in removed_steps],
                "remaining_steps": len(recorder.current_steps),
            }
        return {"undone": False, "message": "취소할 단계가 없습니다."}

    raise ValueError(f"알 수 없는 명령: {command}")


def dispatch_macro_play(
    args: dict[str, Any],
    ctx: Any,
    get_macro_store: Factory,
    get_sap: Factory,
    with_post_action: PostActionHook,
    *,
    dispatch_step: Factory | None = None,
    policy: Any = None,
) -> dict[str, Any]:
    """Compile every macro step, preflight all, then use central dispatch."""
    store = get_macro_store()
    macro_ref = str(args["macro_id"])
    macro = store.resolve(macro_ref)
    if not macro:
        raise ValueError(f"매크로 '{macro_ref}'를 찾을 수 없습니다. macro-list로 ID/이름을 확인하세요.")

    variables = args.get("variables", {}) or {}
    try:
        compiled = compile_macro(macro, variables)
    except MacroCompileError as exc:
        return {
            "macro_id": macro.id,
            "macro_name": macro.name,
            "success": False,
            "stage": "compile",
            "error": str(exc),
            "completed_steps": 0,
            "total_steps": len(macro.steps),
        }

    decisions = []
    for step in compiled.steps:
        decision = preflight_tool_call(
            step.tool,
            step.reveal_params(),
            policy=policy,
            agent_name=getattr(ctx, "agent_name", None),
            enforce_auth=bool(getattr(ctx, "enforce_auth", True)),
        )
        decisions.append((step, decision))
    blocked = [
        {
            "step_id": step.step_id,
            "allowed": decision.allowed,
            "approval_required": bool(
                decision.approval_required or step.approval_required
            ),
            "risk": decision.risk_metadata,
        }
        for step, decision in decisions
        if not decision.allowed
        or decision.approval_required
        or step.approval_required
    ]
    if blocked:
        return {
            "macro_id": macro.id,
            "macro_name": macro.name,
            "success": False,
            "stage": "preflight",
            "blocked_steps": blocked,
            "completed_steps": 0,
            "total_steps": len(macro.steps),
        }

    safe_variables = {
        name: variable.model_dump(mode="json")
        for name, variable in compiled.variables.items()
    }
    if args.get("dry_run"):
        return {
            "macro_id": macro.id,
            "macro_name": macro.name,
            "success": True,
            "dry_run": True,
            "variables_used": safe_variables,
            "compiled_steps": [
                step.model_dump(mode="json") for step in compiled.steps
            ],
            "total_steps": len(macro.steps),
            "completed_steps": 0,
        }
    if dispatch_step is None:
        raise RuntimeError("macro execution requires the central dispatcher callback")

    started_at = datetime.now().isoformat()
    child_ctx = copy.copy(ctx)
    child_ctx.tool_name = None
    child_ctx.include_post_action_context = False
    child_ctx.include_screen_summary = False
    step_results: list[dict[str, Any]] = []
    captured_data: dict[str, str] = {}
    for step in compiled.steps:
        child_ctx.sensitive_values = tuple(step.sensitive_values)
        try:
            result = dispatch_step(
                step.command,
                step.reveal_params(),
                child_ctx,
            )
            if step.command == "get" and isinstance(result, dict) and "value" in result:
                capture_as = str(step.reveal_params().get("capture_as") or step.target_locator.get("sap_id", ""))
                captured_data[capture_as] = str(result.get("value", ""))
            status = dispatch_step("status", {}, child_ctx)
            status_type = str(status.get("type", "S"))
            if status_type.upper() in {"E", "A"}:
                raise RuntimeError("SAP status indicates error or abort")
            actual_screen = 0
            if step.expected_screen is not None:
                session = dispatch_step("session", {}, child_ctx)
                actual_screen = int(session.get("screen_number", 0) or 0)
                if actual_screen != step.expected_screen:
                    raise RuntimeError("macro expected_screen mismatch")
            step_results.append(
                {
                    **MacroStepResult(
                        step_number=step.macro_step_number,
                        success=True,
                        actual_screen=actual_screen,
                        status_message=str(status.get("message", "")),
                        status_type=status_type,
                    ).model_dump(mode="json"),
                    "step_id": step.step_id,
                    "command": step.command,
                    "result": _redact_values(result, step.sensitive_values),
                }
            )
        except Exception:
            step_results.append(
                {
                    **MacroStepResult(
                        step_number=step.macro_step_number,
                        success=False,
                        error="macro step dispatch failed",
                    ).model_dump(mode="json"),
                    "step_id": step.step_id,
                    "command": step.command,
                }
            )
            return {
                "macro_id": macro.id,
                "macro_name": macro.name,
                "success": False,
                "stage": "execute",
                "variables_used": safe_variables,
                "step_results": step_results,
                "total_steps": len(macro.steps),
                "completed_steps": sum(item["success"] for item in step_results),
                "error_step": step.macro_step_number,
                "started_at": started_at,
                "finished_at": datetime.now().isoformat(),
                "captured_data": captured_data,
            }
        if step.wait_after > 0:
            time.sleep(step.wait_after)

    sap = get_sap(ctx.slot_id)
    payload = {
        "macro_id": macro.id,
        "macro_name": macro.name,
        "success": True,
        "variables_used": safe_variables,
        "step_results": step_results,
        "total_steps": len(macro.steps),
        "completed_steps": len([step for step in compiled.steps if not step.synthetic]),
        "started_at": started_at,
        "finished_at": datetime.now().isoformat(),
        "captured_data": captured_data,
    }
    return with_post_action(payload, sap, "macro_play", ctx)


def dispatch_macro_list(args: dict[str, Any], get_macro_store: Factory) -> dict[str, Any]:
    """List saved macros, optionally by tcode."""
    store = get_macro_store()
    tcode = args.get("tcode")
    if tcode:
        macros = store.find_by_tcode(str(tcode))
        return {
            "macros": [macro.model_dump(mode="json") for macro in macros],
            "count": len(macros),
        }
    macros = store.list_macros()
    return {"macros": macros, "count": len(macros)}


def utc_to_local(iso_str: str) -> str:
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


def dispatch_macro_show(args: dict[str, Any], get_macro_store: Factory) -> dict[str, Any]:
    """Return one saved macro."""
    store = get_macro_store()
    macro_ref = str(args["macro_id"])
    macro = store.resolve(macro_ref)
    if not macro:
        return {"error": True, "message": f"매크로 '{macro_ref}'를 찾을 수 없습니다."}
    data = macro.model_dump(mode="json")
    data["created_at"] = utc_to_local(data.get("created_at", ""))
    data["updated_at"] = utc_to_local(data.get("updated_at", ""))
    return data


def dispatch_macro_delete(args: dict[str, Any], get_macro_store: Factory) -> dict[str, Any]:
    """Delete a saved macro."""
    macro_ref = str(args["macro_id"])
    store = get_macro_store()
    macro = store.resolve(macro_ref)
    macro_id = macro.id if macro else macro_ref
    deleted = store.delete(macro_id)
    return {"deleted": deleted, "macro_id": macro_id, "requested": macro_ref}
