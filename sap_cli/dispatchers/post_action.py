"""Post-action context and screen summary helpers.

The public dispatcher facade keeps private compatibility wrappers while the
actual post-action enrichment logic lives here.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

Factory = Callable[..., Any]


POST_ACTION_COMMANDS: frozenset[str] = frozenset(
    {
        "connect",
        "run",
        "set",
        "press",
        "vkey",
        "command",
        "ui_action",
        "record_step",
        "macro_play",
        "table_select",
        "table_doubleclick",
    }
)


def with_post_action(
    payload: dict[str, Any],
    sap: Any,
    command: str,
    ctx: Any,
    post_action_commands: frozenset[str],
    collect_post_action: Factory,
) -> dict[str, Any]:
    """Attach post-action context and promote common fields to the payload."""
    if ctx.include_post_action_context and command in post_action_commands:
        wait_until_idle = getattr(sap, "wait_until_idle", None)
        if callable(wait_until_idle):
            wait_until_idle()
        payload["post_action"] = collect_post_action(
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


def collect_post_action(
    sap: Any,
    *,
    include_screen_summary: bool = False,
    collect_modal: Factory,
    collect_screen_summary: Factory,
) -> dict[str, Any]:
    """Collect session, status, modal, and optional screen summary context."""
    payload = {
        "session": sap.get_session_info().model_dump(mode="json"),
        "status": sap.get_status().model_dump(mode="json"),
        "modal": collect_modal(sap),
    }
    if include_screen_summary:
        try:
            payload["screen_summary"] = collect_screen_summary(sap)
        except Exception as exc:  # pragma: no cover - defensive path
            payload["screen_summary_error"] = str(exc)
    return payload


def collect_screen_summary(sap: Any) -> dict[str, Any]:
    """Collect a compact summary of the current SAP GUI screen."""
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
        "changeable_fields": [summarize_field(field) for field in changeable_fields[:8]],
        "required_fields": [summarize_field(field) for field in required_fields[:5]],
        "available_actions": [summarize_action(action) for action in available_actions[:10]],
    }


def summarize_field(field: Any) -> dict[str, Any]:
    """Summarize a screen field for transcript/post-action context."""
    payload = {"id": str(getattr(field, "id", ""))}
    if name := getattr(field, "name", ""):
        payload["name"] = name
    if label := getattr(field, "label", ""):
        payload["label"] = label
    if field_type := enum_value(getattr(field, "type", None)):
        payload["type"] = field_type
    if value := getattr(field, "value", ""):
        payload["value"] = value
    if getattr(field, "required", False):
        payload["required"] = True
    return payload


def summarize_action(action: Any) -> dict[str, Any]:
    """Summarize an available screen action."""
    payload = {
        "type": enum_value(getattr(action, "action_type", "")),
        "target_id": str(getattr(action, "target_id", "")),
    }
    if label := getattr(action, "target_label", ""):
        payload["label"] = label
    if description := getattr(action, "description", ""):
        payload["description"] = description
    return payload


def enum_value(value: Any) -> Any:
    """Return the value of an enum-like object or the object itself."""
    return getattr(value, "value", value)
