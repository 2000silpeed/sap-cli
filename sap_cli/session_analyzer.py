"""Session Analyzer — 세션 정제 + 레시피 변환 엔진 (Phase 14-2, 14-3)."""

from __future__ import annotations

import logging
import re
import time
import uuid
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


# ---------------------------------------------------------------
# Phase 14-3: 세션 → 레시피 변환 + 변수 치환
# ---------------------------------------------------------------

_DOC_NUMBER_RE = re.compile(r"^\d{8,10}$")
_NUMERIC_VALUE_RE = re.compile(r"^\d+(\.\d+)?$")

_GENERALIZE_KEYS: dict[str, tuple[str, re.Pattern[str] | None]] = {
    "tcode": ("${tcode}", None),
    "document_number": ("${document_number}", _DOC_NUMBER_RE),
    "row": ("${row}", None),
    "count": ("${count}", None),
}

_PRESERVE_KEYS = frozenset({"key", "vkey", "element", "field", "cmd", "action"})


def _generalize_args(args: dict[str, Any]) -> dict[str, Any]:
    """구체값을 ${variable} 플레이스홀더로 치환."""
    result: dict[str, Any] = {}
    for k, v in args.items():
        if k in _PRESERVE_KEYS:
            result[k] = v
            continue
        sv = str(v) if isinstance(v, (int, float)) else v if isinstance(v, str) else None
        if sv is None:
            result[k] = v
            continue
        if k in _GENERALIZE_KEYS:
            placeholder, pattern = _GENERALIZE_KEYS[k]
            if pattern is None or pattern.match(sv):
                result[k] = placeholder
                continue
        if k == "value" and _NUMERIC_VALUE_RE.match(sv):
            result[k] = "${value}"
            continue
        result[k] = v
    return result


def _extract_tcode(steps: list[SessionStep]) -> str:
    """세션 단계에서 주 tcode를 추출. sap_run 명령 우선."""
    for step in steps:
        if step.tool_name in ("sap_run", "sap_command"):
            tcode = step.arguments.get("tcode", "") or step.arguments.get("cmd", "")
            if isinstance(tcode, str) and tcode:
                cleaned = tcode.lstrip("/nNoO")
                if cleaned:
                    return cleaned.upper()
    for step in steps:
        tcode = step.after_transaction or step.before_transaction
        if tcode:
            return tcode.upper()
    return ""


def _extract_keywords(session: McpSession, tcode: str) -> list[str]:
    """세션 정보에서 검색 키워드를 추출."""
    keywords: list[str] = []
    if tcode:
        keywords.append(tcode)
    words = session.user_request.split()
    for w in words:
        w_clean = w.strip(".,!?\"'()[]{}").strip()
        if len(w_clean) >= 2 and w_clean not in keywords:
            keywords.append(w_clean)
    return keywords[:10]


def _safe_recipe_id(tcode: str, session_id: str) -> str:
    """recipe_id를 [A-Za-z0-9_-]+ 형식으로 안전하게 생성."""
    safe_tcode = re.sub(r"[^A-Za-z0-9_\-]", "_", tcode) if tcode else ""
    return f"{safe_tcode}_{session_id}" if safe_tcode else session_id


def session_to_recipe(session: McpSession, approved_by: str = "") -> dict[str, Any]:
    """정제된 세션을 Recipe dict로 변환.

    Returns dict compatible with recipe.Recipe(**result).
    """
    from .recipe import RecipeStep as RStep

    analysis = analyze(session)
    core_flow: list[dict[str, Any]] = analysis["core_flow"]

    core_steps = [SessionStep.model_validate(s) for s in core_flow]
    tcode = _extract_tcode(core_steps) or _extract_tcode(session.steps)
    keywords = _extract_keywords(session, tcode)

    recipe_steps: list[dict[str, Any]] = []
    for i, step_data in enumerate(core_flow):
        generalized_args = _generalize_args(step_data.get("arguments", {}))
        recipe_steps.append({
            "tool": step_data.get("tool_name", ""),
            "args": generalized_args,
            "hint": step_data.get("intent", ""),
        })

    recipe_id = _safe_recipe_id(tcode, session.id)

    return {
        "id": recipe_id,
        "description": session.user_request or session.ai_reasoning or f"{tcode} 워크플로우",
        "context": session.ai_reasoning,
        "keywords": keywords,
        "steps": recipe_steps,
        "source_session": session.id,
        "created_by": approved_by,
        "updated_by": approved_by,
        "approved_by": approved_by,
        "shared_scope": "private",
        "team": "",
    }


def approve_session(
    session: McpSession,
    approved_by: str = "admin",
) -> dict[str, Any]:
    """candidate 세션을 승인하고 레시피 dict를 반환.

    세션의 approved, recipe_id 필드를 업데이트한다.
    """
    if not session.candidate:
        return {"error": "이 세션은 승인 후보(candidate)가 아닙니다"}
    if session.approved:
        return {"error": f"이미 승인된 세션입니다 (recipe_id: {session.recipe_id})"}

    recipe_data = session_to_recipe(session, approved_by=approved_by)
    session.approved = True
    session.recipe_id = recipe_data["id"]

    return recipe_data
