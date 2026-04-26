"""SAP GUI action command dispatch helpers.

Small SAP interaction commands live here while :mod:`sap_cli.dispatcher`
keeps facade routing and compatibility wrappers.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

Factory = Callable[..., Any]


SAP_ACTION_COMMANDS: frozenset[str] = frozenset(
    {
        "record_step",
        "table_select",
        "table_doubleclick",
    }
)


def is_sap_action_command(command: str) -> bool:
    """Return whether ``command`` belongs to the SAP action dispatcher group."""
    return command in SAP_ACTION_COMMANDS


def dispatch_sap_action_command(
    command: str,
    args: dict[str, Any],
    ctx: Any,
    *,
    get_sap: Factory,
    get_recorder: Factory,
    map_action: Factory,
    execute_sap_action: Factory,
    with_post_action: Factory,
) -> dict[str, Any]:
    """Dispatch an SAP action command with facade-provided compatibility hooks."""
    if command == "record_step":
        return dispatch_record_step(
            args,
            ctx,
            get_sap,
            get_recorder,
            map_action,
            execute_sap_action,
            with_post_action,
        )
    if command == "table_select":
        return dispatch_table_select(args, ctx, get_sap, with_post_action)
    if command == "table_doubleclick":
        return dispatch_table_doubleclick(args, ctx, get_sap, with_post_action)
    raise ValueError(f"미구현 SAP action 명령: {command}")


def dispatch_record_step(
    args: dict[str, Any],
    ctx: Any,
    get_sap: Factory,
    get_recorder: Factory,
    map_action: Factory,
    execute_sap_action: Factory,
    with_post_action: Factory,
) -> dict[str, Any]:
    """Execute and record one macro step."""
    sap = get_sap(ctx.slot_id)
    recorder = get_recorder()
    action = str(args["action"])
    target = str(args["target"])
    value = args.get("value")
    description = str(args.get("description", ""))

    action_type = map_action(action)
    execute_sap_action(sap, action.lower(), target, None if value is None else str(value))

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
    return with_post_action(payload, sap, "record_step", ctx)


def dispatch_table_select(
    args: dict[str, Any],
    ctx: Any,
    get_sap: Factory,
    with_post_action: Factory,
) -> dict[str, Any]:
    """Select a row in a SAP GUI table."""
    sap = get_sap(ctx.slot_id)
    element_id = str(args["element_id"])
    row = int(args["row"])
    sap.select_table_row(element_id, row)
    payload = {"selected": True, "table": element_id, "row": row}
    return with_post_action(payload, sap, "table_select", ctx)


def dispatch_table_doubleclick(
    args: dict[str, Any],
    ctx: Any,
    get_sap: Factory,
    with_post_action: Factory,
) -> dict[str, Any]:
    """Double-click a SAP GUI table cell."""
    sap = get_sap(ctx.slot_id)
    element_id = str(args["element_id"])
    row = int(args["row"])
    col = str(args.get("column", ""))
    sap.double_click_table_cell(element_id, row, col)
    payload = {"double_clicked": True, "table": element_id, "row": row, "column": col}
    return with_post_action(payload, sap, "table_doubleclick", ctx)
