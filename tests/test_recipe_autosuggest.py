"""Phase 18-1: 자동 레시피 제안 — session_end 자동 평가 + sap_recipe_pending 테스트."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from sap_cli.auth.policy import SessionConfig
from sap_cli.dispatcher import _dispatch_recipe_pending, _dispatch_session_end
from sap_cli.ontology.schema import McpSession, SessionStep
from sap_cli.session_store import SessionStore


def _step(
    n: int,
    *,
    command: str = "set",
    tool: str = "sap_set",
    args: dict[str, Any] | None = None,
    before_tx: str = "VL33N",
    after_tx: str = "VL33N",
    error: str = "",
) -> SessionStep:
    return SessionStep(
        step_number=n,
        timestamp="2026-04-19T10:00:00",
        command=command,
        tool_name=tool,
        arguments=args or {},
        intent="",
        outcome="success" if not error else "error",
        error=error,
        before_transaction=before_tx,
        after_transaction=after_tx,
        before_screen=100,
        after_screen=100,
    )


def _candidate_session(sid: str = "abcdef123456") -> McpSession:
    return McpSession(
        id=sid,
        started_at="2026-04-19T10:00:00",
        finished_at="",
        status="active",
        agent="agent",
        sap_user="USER",
        sap_system="DEV",
        user_request="VL33N에서 배송 변경",
        ai_reasoning="VL33N → 문서번호 입력 → 저장",
        steps=[
            _step(1, command="run", tool="sap_run", args={"tcode": "VL33N"},
                  before_tx="", after_tx="VL33N"),
            _step(2, command="set", tool="sap_set",
                  args={"field": "LIKP-VBELN", "value": "80012345"}),
            _step(3, command="press", tool="sap_press",
                  args={"element": "btn[0]"}),
            _step(4, command="press", tool="sap_press",
                  args={"element": "btn[11]"}),
        ],
    )


def _exploration_only_session(sid: str = "fedcba654321") -> McpSession:
    """탐색만 있고 후보로 분류되지 않을 세션."""
    return McpSession(
        id=sid,
        started_at="2026-04-19T11:00:00",
        finished_at="",
        status="active",
        agent="agent",
        user_request="화면만 봤음",
        ai_reasoning="",
        steps=[
            _step(1, command="screen", tool="sap_screen", args={}),
            _step(2, command="screen", tool="sap_screen", args={}),
        ],
    )


@pytest.fixture
def isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SessionStore:
    store = SessionStore(SessionConfig(log_dir=str(tmp_path / "sessions")))
    monkeypatch.setattr("sap_cli.dispatcher._get_session_store", lambda *a, **k: store)
    return store


class TestSessionEndAutoSuggest:
    def test_candidate_session_includes_suggestion(self, isolated_store: SessionStore):
        session = _candidate_session()
        isolated_store._save(session)
        result = _dispatch_session_end({"session_id": session.id, "outcome": "success"})

        assert result["outcome"] == "success"
        assert "recipe_suggestion" in result
        sug = result["recipe_suggestion"]
        assert sug["candidate"] is True
        assert sug["suggested_recipe_id"].startswith("VL33N_")
        assert sug["core_step_count"] >= 3
        assert "VL33N" in sug["keywords"]

    def test_non_candidate_session_no_suggestion(self, isolated_store: SessionStore):
        session = _exploration_only_session()
        isolated_store._save(session)
        result = _dispatch_session_end({"session_id": session.id, "outcome": "success"})

        assert "recipe_suggestion" not in result

    def test_failed_session_no_suggestion(self, isolated_store: SessionStore):
        session = _candidate_session()
        isolated_store._save(session)
        result = _dispatch_session_end({"session_id": session.id, "outcome": "failed"})

        assert "recipe_suggestion" not in result

    def test_already_approved_no_suggestion(self, isolated_store: SessionStore):
        session = _candidate_session()
        session.candidate = True
        session.approved = True
        isolated_store._save(session)
        result = _dispatch_session_end({"session_id": session.id, "outcome": "success"})

        assert "recipe_suggestion" not in result

    def test_unknown_session_returns_error(self, isolated_store: SessionStore):
        result = _dispatch_session_end({"session_id": "deadbeef0001", "outcome": "success"})

        assert "error" in result
        assert "recipe_suggestion" not in result


class TestRecipePending:
    def test_lists_unapproved_candidates(self, isolated_store: SessionStore):
        s1 = _candidate_session("aaaaaa111111")
        s1.status = "completed"
        s1.outcome = "success"
        isolated_store._save(s1)
        s2 = _candidate_session("bbbbbb222222")
        s2.status = "completed"
        s2.outcome = "success"
        isolated_store._save(s2)

        result = _dispatch_recipe_pending({})

        assert result["count"] == 2
        ids = {p["session_id"] for p in result["pending_suggestions"]}
        assert ids == {"aaaaaa111111", "bbbbbb222222"}

    def test_excludes_approved(self, isolated_store: SessionStore):
        s = _candidate_session("aaaaaa111111")
        s.status = "completed"
        s.outcome = "success"
        s.approved = True
        s.candidate = True
        isolated_store._save(s)

        result = _dispatch_recipe_pending({})

        assert result["count"] == 0

    def test_excludes_non_candidates(self, isolated_store: SessionStore):
        s = _exploration_only_session("ccccc1111111")
        s.status = "completed"
        s.outcome = "success"
        isolated_store._save(s)

        result = _dispatch_recipe_pending({})

        assert result["count"] == 0

    def test_respects_limit(self, isolated_store: SessionStore):
        for i in range(5):
            sid = f"{i:012d}"
            sid = sid.replace("9", "a")  # ensure hex
            sid = "".join(c if c in "0123456789abcdef" else "a" for c in sid)
            s = _candidate_session(sid)
            s.status = "completed"
            s.outcome = "success"
            isolated_store._save(s)

        result = _dispatch_recipe_pending({"limit": 2})

        assert result["count"] == 2
