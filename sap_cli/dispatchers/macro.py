"""Macro command dispatch helpers.

Macro command implementations live here while :mod:`sap_cli.dispatcher`
keeps thin private wrappers for backward-compatible imports and monkeypatch-
based tests.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from ..macro.player import MacroPlayer

Factory = Callable[..., Any]
PostActionHook = Callable[[dict[str, Any], Any, str, Any], dict[str, Any]]


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
) -> dict[str, Any]:
    """Dispatch a macro command with facade-provided compatibility hooks."""
    if command == "macro_control":
        return dispatch_macro_control(args, get_recorder, get_macro_store)
    if command == "macro_play":
        return dispatch_macro_play(args, ctx, get_macro_store, get_sap, with_post_action)
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


def dispatch_macro_play(
    args: dict[str, Any],
    ctx: Any,
    get_macro_store: Factory,
    get_sap: Factory,
    with_post_action: PostActionHook,
) -> dict[str, Any]:
    """Play or dry-run a saved macro."""
    store = get_macro_store()
    macro_ref = str(args["macro_id"])
    macro = store.resolve(macro_ref)
    if not macro:
        raise ValueError(f"매크로 '{macro_ref}'를 찾을 수 없습니다. macro-list로 ID/이름을 확인하세요.")

    variables = args.get("variables", {}) or {}
    sap = get_sap(ctx.slot_id)
    player = MacroPlayer(sap)

    if args.get("dry_run"):
        return player.dry_run(macro, variables).model_dump(mode="json")

    payload = player.play(macro, variables).model_dump(mode="json")
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
