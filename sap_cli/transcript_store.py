"""Transcript 조회 유틸리티."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .auth.policy import TranscriptConfig


class TranscriptStore:
    """JSONL transcript 조회기."""

    def __init__(self, config: TranscriptConfig | None = None) -> None:
        self._config = config or TranscriptConfig()
        self._log_dir = Path(self._config.log_dir).expanduser()

    @property
    def log_dir(self) -> Path:
        return self._log_dir

    def query(
        self,
        *,
        limit: int = 20,
        outcome: str | None = None,
        command: str | None = None,
        tool: str | None = None,
        agent: str | None = None,
        text: str | None = None,
        full: bool = False,
    ) -> dict[str, Any]:
        normalized_limit = max(1, min(int(limit), 100))
        filters = {
            "outcome": _normalize(outcome),
            "command": _normalize(command),
            "tool": _normalize(tool),
            "agent": _normalize(agent),
            "text": _normalize(text),
        }

        entries: list[dict[str, Any]] = []
        scanned = 0
        for entry in self.iter_entries():
            scanned += 1
            if not _matches_entry(entry, filters):
                continue
            entries.append(entry if full else _summarize_entry(entry))
            if len(entries) >= normalized_limit:
                break

        return {
            "entries": entries,
            "returned": len(entries),
            "limit": normalized_limit,
            "filters": {key: value for key, value in filters.items() if value},
            "log_dir": str(self._log_dir),
            "has_logs": self._log_dir.exists(),
            "full": full,
            "scanned": scanned,
        }

    def iter_entries(self) -> list[dict[str, Any]]:
        if not self._log_dir.exists():
            return []

        entries: list[dict[str, Any]] = []
        for path in sorted(self._log_dir.glob("transcript-*.jsonl"), reverse=True):
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for line in reversed(lines):
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    entries.append(payload)
        return entries


def _matches_entry(entry: dict[str, Any], filters: dict[str, str | None]) -> bool:
    if filters["outcome"] and _normalize(entry.get("outcome")) != filters["outcome"]:
        return False
    if filters["command"] and _normalize(entry.get("command")) != filters["command"]:
        return False
    if filters["tool"] and _normalize(entry.get("tool")) != filters["tool"]:
        return False
    if filters["agent"] and _normalize(entry.get("agent")) != filters["agent"]:
        return False

    text_filter = filters["text"]
    if text_filter:
        haystack = json.dumps(entry, ensure_ascii=False).lower()
        if text_filter not in haystack:
            return False
    return True


def _summarize_entry(entry: dict[str, Any]) -> dict[str, Any]:
    before_session = _nested(entry, "before", "session")
    after_session = _nested(entry, "after", "session")
    after_status = _nested(entry, "after", "status")
    after_screen = _nested(entry, "after", "screen_summary")

    summary = {
        "ts": entry.get("ts"),
        "source": entry.get("source"),
        "command": entry.get("command"),
        "tool": entry.get("tool"),
        "agent": entry.get("agent"),
        "sap_user": entry.get("sap_user"),
        "sap_system": entry.get("sap_system"),
        "outcome": entry.get("outcome"),
        "allowed": entry.get("allowed"),
        "approval_required": entry.get("approval_required"),
        "permission": entry.get("permission"),
        "resource": entry.get("resource"),
        "reason": entry.get("reason"),
        "transaction_before": before_session.get("transaction"),
        "transaction_after": after_session.get("transaction"),
        "screen_before": before_session.get("screen_number"),
        "screen_after": after_session.get("screen_number"),
    }

    if after_status:
        summary["status"] = after_status
    if after_screen:
        summary["screen_title"] = after_screen.get("title")
    if error := entry.get("error"):
        summary["error"] = error
    return summary


def _nested(entry: dict[str, Any], *keys: str) -> dict[str, Any]:
    current: Any = entry
    for key in keys:
        if not isinstance(current, dict):
            return {}
        current = current.get(key, {})
    return current if isinstance(current, dict) else {}


def _normalize(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    return text or None
