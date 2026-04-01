"""Transcript store 단위 테스트."""

from __future__ import annotations

import json

from sap_cli.auth.policy import TranscriptConfig
from sap_cli.transcript_store import TranscriptStore


def test_query_filters_and_summarizes_entries(tmp_path):
    log_dir = tmp_path / "transcripts"
    log_dir.mkdir()
    entries = [
        {
            "ts": "2026-04-01T00:00:00+00:00",
            "source": "mcp",
            "command": "run",
            "tool": "sap_run",
            "agent": "operator",
            "outcome": "success",
            "allowed": True,
            "approval_required": False,
            "permission": "execute",
            "resource": "tx:MM03",
            "reason": "operator on tx:*",
            "before": {"session": {"transaction": "SESSION_MANAGER", "screen_number": 100}},
            "after": {
                "session": {"transaction": "MM03", "screen_number": 4000},
                "status": {"type": "S", "message": "ok"},
                "screen_summary": {"title": "Material Display"},
            },
        },
        {
            "ts": "2026-04-01T00:01:00+00:00",
            "source": "mcp",
            "command": "command",
            "tool": "sap_command",
            "agent": "operator",
            "outcome": "approval_required",
            "allowed": True,
            "approval_required": True,
            "permission": "execute",
            "resource": "tx:SM30",
            "reason": "테이블 직접 수정",
        },
    ]
    path = log_dir / "transcript-2026-04-01.jsonl"
    path.write_text("\n".join(json.dumps(entry, ensure_ascii=False) for entry in entries), encoding="utf-8")

    store = TranscriptStore(TranscriptConfig(log_dir=str(log_dir)))
    result = store.query(limit=5, outcome="success")

    assert result["returned"] == 1
    assert result["entries"][0]["command"] == "run"
    assert result["entries"][0]["transaction_after"] == "MM03"
    assert result["entries"][0]["screen_title"] == "Material Display"


def test_query_full_returns_raw_entries(tmp_path):
    log_dir = tmp_path / "transcripts"
    log_dir.mkdir()
    entry = {
        "ts": "2026-04-01T00:00:00+00:00",
        "source": "cli",
        "command": "run",
        "tool": "sap_run",
        "agent": "operator",
        "outcome": "success",
        "allowed": True,
        "approval_required": False,
        "permission": "execute",
        "resource": "tx:MM03",
        "reason": "operator on tx:*",
        "result": {"executed": "MM03"},
    }
    path = log_dir / "transcript-2026-04-01.jsonl"
    path.write_text(json.dumps(entry, ensure_ascii=False), encoding="utf-8")

    store = TranscriptStore(TranscriptConfig(log_dir=str(log_dir)))
    result = store.query(limit=1, full=True)

    assert result["returned"] == 1
    assert result["full"] is True
    assert result["entries"][0]["result"]["executed"] == "MM03"
