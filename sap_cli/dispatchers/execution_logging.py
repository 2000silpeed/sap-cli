"""Audit, transcript, and execution-state logging helpers.

The central dispatcher keeps thin compatibility wrappers for older tests and
private imports, while the implementation lives here.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..auth.audit import AuditLogger

Factory = Callable[..., Any]


def audit_authorization(
    policy: Any,
    authz: Any,
    tool_name: str,
    arguments: dict[str, Any],
) -> None:
    """Write an audit entry for an authorization decision."""
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


def log_transcript(
    transcript: Any,
    *,
    should_log_transcript: bool,
    ctx: Any,
    command: str,
    tool_name: str,
    authz: Any,
    arguments: dict[str, Any],
    outcome: str,
    get_existing_sap: Callable[[str], Any],
    before_state: dict[str, Any] | None = None,
    after_state: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
    error: str = "",
) -> None:
    """Write a transcript entry, adding session context when needed."""
    if not should_log_transcript:
        return

    def _has_session(state: dict[str, Any] | None) -> bool:
        return bool(state and isinstance(state.get("session"), dict))

    if not _has_session(before_state) and not _has_session(after_state):
        sap = get_existing_sap(ctx.slot_id)
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


def capture_transcript_state(
    command: str,
    *,
    include_screen_summary: bool,
    slot_id: str,
    command_registry: dict[str, Any],
    get_existing_sap: Callable[[str], Any],
    collect_modal: Factory,
    collect_screen_summary: Factory,
) -> dict[str, Any] | None:
    """Capture compact SAP state for transcript/session logging."""
    if command not in command_registry:
        return None
    if command in {
        "ontology_query",
        "ontology_ask",
        "macro_control",
        "macro_list",
        "macro_show",
        "macro_delete",
        "transcript_query",
        "transcript_plan",
    }:
        return None

    sap = get_existing_sap(slot_id)
    if sap is None:
        return None

    try:
        state = {
            "session": sap.get_session_info().model_dump(mode="json"),
            "status": sap.get_status().model_dump(mode="json"),
            "modal": collect_modal(sap),
        }
        if include_screen_summary:
            state["screen_summary"] = collect_screen_summary(sap)
        return state
    except Exception as exc:  # pragma: no cover - defensive path
        return {"capture_error": str(exc)}


def extract_transcript_state_from_result(result: dict[str, Any]) -> dict[str, Any] | None:
    """Extract transcript state from post-action enriched command results."""
    post_action = result.get("post_action")
    if not isinstance(post_action, dict):
        return None

    state: dict[str, Any] = {}
    for key in ("session", "status", "modal", "screen_summary"):
        value = post_action.get(key)
        if value is not None:
            state[key] = value
    return state or None
