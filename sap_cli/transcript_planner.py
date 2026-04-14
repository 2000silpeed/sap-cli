"""Transcript 기반 review/replay planner."""

from __future__ import annotations

import re
from typing import Any

from .ontology.store import OntologyStore
from .transcript_store import TranscriptStore


class TranscriptPlanner:
    """Transcript entry를 재실행 전 검토용 계획으로 변환."""

    def __init__(
        self,
        store: TranscriptStore,
        ontology_store: OntologyStore | None = None,
    ) -> None:
        self._store = store
        self._ontology_store = ontology_store or OntologyStore()

    def plan(
        self,
        *,
        limit: int = 20,
        index: int = 0,
        outcome: str | None = None,
        command: str | None = None,
        tool: str | None = None,
        agent: str | None = None,
        text: str | None = None,
    ) -> dict[str, Any]:
        normalized_limit = max(1, min(int(limit), 100))
        normalized_index = max(0, int(index))
        entries = self._store.query(
            limit=max(normalized_limit, normalized_index + 1),
            outcome=outcome,
            command=command,
            tool=tool,
            agent=agent,
            text=text,
            full=True,
        )

        matched = entries["entries"]
        if normalized_index >= len(matched):
            return {
                "found": False,
                "message": "조건에 맞는 transcript entry를 찾지 못했습니다.",
                "selected_index": normalized_index,
                "returned": len(matched),
                "filters": entries["filters"],
                "log_dir": entries["log_dir"],
                "has_logs": entries["has_logs"],
            }

        entry = matched[normalized_index]
        return {
            "found": True,
            "selected_index": normalized_index,
            "filters": entries["filters"],
            "log_dir": entries["log_dir"],
            "entry": _summarize_selected_entry(entry),
            "risk_level": _compute_risk_level(entry),
            "replayable": _is_replayable(entry),
            "requires_human_approval": bool(entry.get("approval_required")),
            "why": _build_why(entry),
            "ontology_context": _build_ontology_context(entry, self._ontology_store),
            "preconditions": _build_preconditions(entry),
            "suggested_checks": _build_suggested_checks(entry),
            "proposed_sequence": _build_sequence(entry),
        }


def _summarize_selected_entry(entry: dict[str, Any]) -> dict[str, Any]:
    before_session = _nested(entry, "before", "session")
    after_session = _nested(entry, "after", "session")
    after_status = _nested(entry, "after", "status")
    after_screen = _nested(entry, "after", "screen_summary")

    sap_user = entry.get("sap_user") or after_session.get("user") or before_session.get("user") or ""
    sap_system = entry.get("sap_system") or after_session.get("system_name") or before_session.get("system_name") or ""

    summary = {
        "ts": entry.get("ts"),
        "source": entry.get("source"),
        "command": entry.get("command"),
        "tool": entry.get("tool"),
        "agent": entry.get("agent"),
        "sap_user": sap_user,
        "sap_system": sap_system,
        "outcome": entry.get("outcome"),
        "permission": entry.get("permission"),
        "resource": entry.get("resource"),
        "reason": entry.get("reason"),
        "arguments": entry.get("arguments", {}),
        "transaction_before": before_session.get("transaction"),
        "transaction_after": after_session.get("transaction"),
        "screen_before": before_session.get("screen_number"),
        "screen_after": after_session.get("screen_number"),
    }
    if after_status:
        summary["status"] = after_status
    if after_screen:
        summary["screen_summary"] = after_screen
    if error := entry.get("error"):
        summary["error"] = error
    return summary


def _compute_risk_level(entry: dict[str, Any]) -> str:
    permission = str(entry.get("permission", "")).lower()
    outcome = str(entry.get("outcome", "")).lower()
    if outcome in {"denied", "approval_required", "error"}:
        return "high"
    if permission in {"admin", "write"}:
        return "high"
    if permission == "execute":
        if _is_save_like(entry):
            return "high"
        return "medium"
    return "low"


def _is_replayable(entry: dict[str, Any]) -> bool:
    outcome = str(entry.get("outcome", "")).lower()
    if outcome in {"approval_required", "denied"}:
        return False
    return True


def _build_why(entry: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    outcome = str(entry.get("outcome", "")).lower()
    permission = str(entry.get("permission", "")).lower()

    if outcome == "success":
        reasons.append("원본 transcript에서는 이 단계가 성공했습니다.")
    elif outcome == "approval_required":
        reasons.append("원본 transcript에서 추가 승인이 필요했습니다.")
    elif outcome == "denied":
        reasons.append("원본 transcript에서 권한 부족으로 거부됐습니다.")
    elif outcome == "error":
        reasons.append("원본 transcript에서 실행 에러가 발생했습니다.")

    if permission == "read":
        reasons.append("읽기 계열 단계라서 재검토 위험은 낮습니다.")
    elif permission == "execute":
        reasons.append("실행 계열 단계라서 현재 화면과 모달 상태를 먼저 맞춰야 합니다.")
    elif permission in {"write", "admin"}:
        reasons.append("쓰기/관리 계열 단계라서 사람 검토 없이 바로 재실행하지 않는 편이 안전합니다.")

    if _is_save_like(entry):
        reasons.append("저장에 해당할 가능성이 있어 승인 또는 사람 검토가 필요합니다.")
    return reasons


def _build_preconditions(entry: dict[str, Any]) -> list[str]:
    preconditions: list[str] = ["SAP GUI가 실행 중이고 올바른 세션에 연결되어 있어야 합니다."]
    before_session = _nested(entry, "before", "session")
    transaction = before_session.get("transaction")
    screen_number = before_session.get("screen_number")
    command = str(entry.get("command", "")).lower()

    if transaction:
        if command in {"run", "command"}:
            preconditions.append(
                f"원본 실행 전 트랜잭션은 '{transaction}' 이었습니다. 재실행 시에도 같은 시작 상태인지 먼저 확인하세요."
            )
        else:
            suffix = f", screen {screen_number}" if screen_number is not None else ""
            preconditions.append(
                f"이 단계는 '{transaction}'{suffix} 상태에서 기록됐습니다. 같은 화면인지 먼저 확인해야 합니다."
            )

    if entry.get("approval_required"):
        preconditions.append("이 단계는 원본에서 승인 필요 상태였습니다. 재실행 전에 사람 승인을 받아야 합니다.")
    return preconditions


def _build_suggested_checks(entry: dict[str, Any]) -> list[str]:
    checks = [
        "실행 전에 sap session 또는 sap screen으로 현재 화면이 원본 before 상태와 같은지 확인",
        "실행 직후 sap status로 성공/경고/에러를 확인",
        "모달 가능성이 있으니 sap modal로 팝업 존재 여부를 확인",
    ]
    if tcode := _infer_tcode(entry):
        checks.append(f"재실행 전에 sap ontology {tcode} 또는 sap_ontology_query로 구조 지식을 다시 확인")
    if _nested(entry, "after", "screen_summary"):
        checks.append("필요하면 sap screen --fields-only 또는 sap screen으로 after 화면이 기대한 구조인지 확인")
    if _is_save_like(entry):
        checks.append("저장성 액션이면 문서번호 생성이나 상태바 저장 메시지를 반드시 확인")
    return checks


def _build_sequence(entry: dict[str, Any]) -> list[dict[str, Any]]:
    sequence = [
        {
            "step": 1,
            "kind": "check",
            "command": "sap connect",
            "purpose": "세션 연결 확인",
        },
        {
            "step": 2,
            "kind": "check",
            "command": "sap session",
            "purpose": "현재 transaction/screen 상태가 원본 before 상태와 같은지 확인",
        },
    ]

    if tcode := _infer_tcode(entry):
        sequence.append(
            {
                "step": len(sequence) + 1,
                "kind": "knowledge_check",
                "command": f"sap ontology {tcode}",
                "purpose": "재실행 전에 이 트랜잭션의 구조적 지식을 다시 확인",
            }
        )

    replay_command = _to_cli_command(entry)
    if replay_command:
        sequence.append(
            {
                "step": len(sequence) + 1,
                "kind": "candidate_action",
                "command": replay_command,
                "purpose": "원본 transcript의 단일 단계를 재현",
            }
        )

    sequence.extend(
        [
            {
                "step": len(sequence) + 1,
                "kind": "check",
                "command": "sap status",
                "purpose": "상태바 메시지 확인",
            },
            {
                "step": len(sequence) + 2,
                "kind": "check",
                "command": "sap modal",
                "purpose": "팝업 발생 여부 확인",
            },
        ]
    )

    if _nested(entry, "after", "screen_summary"):
        sequence.append(
            {
                "step": len(sequence) + 1,
                "kind": "check",
                "command": "sap screen --fields-only",
                "purpose": "after 화면의 입력 가능 필드와 구조를 재확인",
            }
        )
    return sequence


def _to_cli_command(entry: dict[str, Any]) -> str | None:
    command = str(entry.get("command", ""))
    arguments = entry.get("arguments", {}) or {}

    if command == "run":
        return f'sap run {arguments.get("tcode", "")}'.strip()
    if command == "command":
        return f'sap command "{arguments.get("cmd", "")}"'
    if command == "set":
        return f'sap set "{arguments.get("element_id", "")}" "{arguments.get("value", "")}"'
    if command == "get":
        return f'sap get "{arguments.get("element_id", "")}"'
    if command == "press":
        return f'sap press "{arguments.get("element_id", "")}"'
    if command == "vkey":
        return f'sap vkey {arguments.get("key", "")}'.strip()
    if command == "record_step":
        parts = [
            "sap record-step",
            f'--action {arguments.get("action", "")}',
            f'--target "{arguments.get("target", "")}"',
        ]
        if "value" in arguments and arguments.get("value") is not None:
            parts.append(f'--value "{arguments.get("value", "")}"')
        return " ".join(parts)
    if command == "macro_play":
        return f'sap macro-play {arguments.get("macro_id", "")}'
    return None


def _build_ontology_context(
    entry: dict[str, Any],
    ontology_store: OntologyStore,
) -> dict[str, Any]:
    if not (tcode := _infer_tcode(entry)):
        return {
            "available": False,
            "reason": "transcript에서 명확한 트랜잭션 코드를 추론하지 못했습니다.",
        }

    ontology = ontology_store.load_transaction(tcode)
    context: dict[str, Any] = {
        "available": ontology is not None,
        "tcode": tcode,
        "query_hint": f"sap ontology {tcode}",
        "tool_hint": f'sap_ontology_query(tcode="{tcode}")',
    }
    if ontology is None:
        context["reason"] = "로컬 온톨로지 저장소에 이 트랜잭션 지식이 아직 없습니다."
        return context

    context.update(
        {
            "title": ontology.title,
            "screen_count": len(ontology.screens),
            "related_transactions": ontology.related_transactions,
        }
    )
    return context


def _infer_tcode(entry: dict[str, Any]) -> str | None:
    arguments = entry.get("arguments", {}) or {}
    command = str(entry.get("command", "")).lower()

    if tcode := _normalize_tcode(arguments.get("tcode")):
        return tcode
    if command == "command":
        if tcode := _extract_tcode_from_command(arguments.get("cmd")):
            return tcode
    if tcode := _normalize_tcode(_nested(entry, "after", "session").get("transaction")):
        return tcode
    if tcode := _normalize_tcode(_nested(entry, "before", "session").get("transaction")):
        return tcode

    resource = entry.get("resource")
    if isinstance(resource, str) and resource.startswith("tx:"):
        return _normalize_tcode(resource[3:])
    return None


def _normalize_tcode(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().upper()
    if not normalized or normalized == "SESSION_MANAGER":
        return None
    if re.fullmatch(r"[A-Z0-9_/-]+", normalized) is None:
        return None
    return normalized


def _extract_tcode_from_command(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip().upper()
    if not stripped:
        return None
    stripped = stripped.lstrip("/")
    if stripped.startswith(("N", "O")):
        stripped = stripped[1:]
    if stripped.startswith("*"):
        stripped = stripped[1:]
    candidate = stripped.split()[0]
    return _normalize_tcode(candidate)


def _is_save_like(entry: dict[str, Any]) -> bool:
    command = str(entry.get("command", "")).lower()
    arguments = entry.get("arguments", {}) or {}
    if command == "vkey" and int(arguments.get("key", -1)) == 11:
        return True
    text = (
        f"{entry.get('reason', '')} "
        f"{entry.get('resource', '')} "
        f"{entry.get('tool', '')} "
        f"{entry.get('command', '')} "
        f"{entry.get('error', '')}"
    ).lower()
    return "save" in text or "저장" in text


def _nested(entry: dict[str, Any], *keys: str) -> dict[str, Any]:
    current: Any = entry
    for key in keys:
        if not isinstance(current, dict):
            return {}
        current = current.get(key, {})
    return current if isinstance(current, dict) else {}
