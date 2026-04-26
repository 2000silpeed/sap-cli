"""Form/screen learning command dispatch helpers.

The top-level :mod:`sap_cli.dispatcher` module keeps thin private wrappers for
backward-compatible imports and monkeypatch-based tests while the actual
``fill``/``learn`` implementations live here.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

Factory = Callable[..., Any]


FORM_COMMANDS: frozenset[str] = frozenset(
    {
        "fill",
        "learn",
    }
)


def is_form_command(command: str) -> bool:
    """Return whether ``command`` belongs to the form dispatcher group."""
    return command in FORM_COMMANDS


def dispatch_form_command(
    command: str,
    args: dict[str, Any],
    ctx: Any,
    *,
    get_sap: Factory,
    get_learner: Factory,
    with_post_action: Factory,
) -> dict[str, Any]:
    """Dispatch a form command with facade-provided compatibility hooks."""
    if command == "fill":
        return dispatch_fill(args, ctx, get_sap, with_post_action)
    if command == "learn":
        return dispatch_learn(ctx, get_learner)
    raise ValueError(f"미구현 form 명령: {command}")


def dispatch_learn(ctx: Any | None, get_learner: Factory) -> dict[str, Any]:
    """Learn metadata for the current SAP GUI screen."""
    slot_id = ctx.slot_id if ctx else "ses0"
    screen = get_learner(slot_id).learn_current_screen()
    return {
        "learned": True,
        "transaction": screen.transaction,
        "screen_number": screen.screen_number,
        "fields_count": len(screen.fields),
        "buttons_count": len(screen.buttons),
    }


def dispatch_fill(
    args: dict[str, Any],
    ctx: Any,
    get_sap: Factory,
    with_post_action: Factory,
) -> dict[str, Any]:
    """Fill multiple fields on the current screen.

    Supports either element IDs or field names. Field names are resolved from
    the current screen before setting values.
    """
    sap = get_sap(ctx.slot_id)
    fields: dict[str, str] = args["fields"]
    do_enter = args.get("enter", True)

    screen = sap.get_screen()
    results: list[dict[str, Any]] = []
    errors: list[str] = []

    for key, value in fields.items():
        if key.startswith("wnd["):
            element_id = key
        else:
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
            results.append(
                {
                    "field": key,
                    "element_id": element_id,
                    "value_set": value,
                }
            )
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
    return with_post_action(payload, sap, "fill", ctx)
