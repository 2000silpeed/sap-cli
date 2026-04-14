"""실행 transcript 기록."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .policy import TranscriptConfig

logger = logging.getLogger(__name__)

_SENSITIVE_KEYS = {"password", "secret", "token"}


class TranscriptLogger:
    """AI 실행 추적용 JSONL transcript."""

    def __init__(self, config: TranscriptConfig | None = None) -> None:
        if config is None:
            config = TranscriptConfig()
        self._enabled = config.enabled
        self._log_dir = Path(config.log_dir).expanduser()
        self._include_reads = config.include_reads
        self._include_screen_summary = config.include_screen_summary
        if self._enabled:
            self._log_dir.mkdir(parents=True, exist_ok=True)

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def include_screen_summary(self) -> bool:
        return self._include_screen_summary

    def should_log(self, permission: str) -> bool:
        if not self._enabled:
            return False
        if not self._include_reads and permission == "read":
            return False
        return True

    def log(
        self,
        *,
        source: str,
        command: str,
        tool_name: str,
        agent: str,
        arguments: dict[str, Any],
        permission: str,
        resource: str,
        allowed: bool,
        approval_required: bool,
        reason: str,
        outcome: str,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
        error: str = "",
    ) -> None:
        if not self._enabled:
            return

        sap_user = ""
        sap_system = ""
        for state in (after, before, result):
            if state and isinstance(state, dict):
                session = state.get("session") or state.get("post_action", {}).get("session")
                if isinstance(session, dict):
                    sap_user = sap_user or session.get("user", "")
                    sap_system = sap_system or session.get("system_name", "")
            if sap_user and sap_system:
                break

        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "source": source,
            "agent": agent,
            "sap_user": sap_user,
            "sap_system": sap_system,
            "command": command,
            "tool": tool_name,
            "permission": permission,
            "resource": resource,
            "allowed": allowed,
            "approval_required": approval_required,
            "reason": reason,
            "outcome": outcome,
            "arguments": _sanitize_payload(arguments),
        }
        if before is not None:
            entry["before"] = _sanitize_payload(before)
        if after is not None:
            entry["after"] = _sanitize_payload(after)
        if result is not None:
            entry["result"] = _sanitize_payload(result)
        if error:
            entry["error"] = error

        log_file = self._log_dir / f"transcript-{datetime.now().strftime('%Y-%m-%d')}.jsonl"
        try:
            with open(log_file, "a", encoding="utf-8") as file:
                file.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as exc:  # pragma: no cover - defensive logging path
            logger.warning("transcript 기록 실패: %s", exc)


def _sanitize_payload(value: Any, *, depth: int = 0) -> Any:
    if depth >= 5:
        return "<truncated>"

    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= 40:
                sanitized["__truncated__"] = f"{len(value) - 40} more keys"
                break
            if str(key).lower() in _SENSITIVE_KEYS:
                continue
            sanitized[str(key)] = _sanitize_payload(item, depth=depth + 1)
        return sanitized

    if isinstance(value, (list, tuple)):
        items = [_sanitize_payload(item, depth=depth + 1) for item in value[:20]]
        if len(value) > 20:
            items.append(f"<{len(value) - 20} more items>")
        return items

    if isinstance(value, str):
        if len(value) > 500:
            return f"{value[:500]}...<truncated>"
        return value

    if value is None or isinstance(value, (bool, int, float)):
        return value

    if hasattr(value, "model_dump"):
        try:
            return _sanitize_payload(value.model_dump(mode="json"), depth=depth + 1)
        except Exception:  # pragma: no cover - defensive path
            return str(value)

    return str(value)
