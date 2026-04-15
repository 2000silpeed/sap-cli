"""Session Analyzer — 세션 정제 엔진 (Phase 14-2)."""

from __future__ import annotations

import logging
from typing import Any

from .ontology.schema import McpSession, SessionStep

logger = logging.getLogger(__name__)

EXPLORATION_TOOL_NAMES = frozenset({
    "sap_screen", "sap_get_status", "sap_modal", "sap_table_read",
    "sap_get_field", "sap_tree_read",
})

EXPLORATION_COMMANDS = frozenset({
    "screen", "status", "modal", "table", "get", "tree",
})

CANCEL_VKEYS = frozenset({3, 12})


def mark_exploration(steps: list[SessionStep]) -> None:
    for step in steps:
        if step.error:
            continue
        if step.tool_name in EXPLORATION_TOOL_NAMES or step.command in EXPLORATION_COMMANDS:
            step.is_exploration = True


def mark_recovery(steps: list[SessionStep]) -> None:
    n = len(steps)
    for i, step in enumerate(steps):
        if not step.error:
            continue
        step.is_recovery = True
        for j in range(i + 1, min(i + 4, n)):
            nxt = steps[j]
            args = nxt.arguments or {}
            vkey = args.get("key") or args.get("vkey")
            if vkey is not None and int(vkey) in CANCEL_VKEYS:
                nxt.is_recovery = True
                continue
            action = args.get("action", "")
            if action in ("n", "N", "no", "No"):
                nxt.is_recovery = True
                continue
            break


def remove_retries(steps: list[SessionStep]) -> list[SessionStep]:
    if not steps:
        return steps
    result: list[SessionStep] = []
    for step in steps:
        if (
            result
            and result[-1].command == step.command
            and result[-1].tool_name == step.tool_name
            and result[-1].arguments == step.arguments
            and result[-1].before_transaction == step.before_transaction
            and result[-1].before_screen == step.before_screen
        ):
            result[-1] = step
        else:
            result.append(step)
    return result


def extract_core_flow(steps: list[SessionStep]) -> list[SessionStep]:
    return [s for s in steps if not s.is_exploration and not s.is_recovery]


def mark_candidate(session: McpSession) -> None:
    if session.status != "completed" or session.outcome != "success":
        session.candidate = False
        return
    core = extract_core_flow(session.steps)
    core = remove_retries(core)
    has_tcode = any(
        s.before_transaction or s.after_transaction for s in core
    )
    session.candidate = len(core) >= 3 and has_tcode


def analyze(session: McpSession) -> dict[str, Any]:
    for step in session.steps:
        step.is_exploration = False
        step.is_recovery = False
    mark_exploration(session.steps)
    mark_recovery(session.steps)
    core = extract_core_flow(session.steps)
    deduplicated = remove_retries(core)
    mark_candidate(session)
    return {
        "session_id": session.id,
        "total_steps": len(session.steps),
        "exploration_count": sum(1 for s in session.steps if s.is_exploration),
        "recovery_count": sum(1 for s in session.steps if s.is_recovery),
        "core_steps": len(core),
        "deduplicated_steps": len(deduplicated),
        "candidate": session.candidate,
        "core_flow": [s.model_dump(mode="json", exclude_none=True) for s in deduplicated],
    }
