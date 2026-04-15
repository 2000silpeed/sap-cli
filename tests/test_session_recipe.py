"""Phase 14-3: 세션→레시피 변환 + 승인 흐름 + 온톨로지 반영 테스트."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest

from sap_cli.ontology.schema import McpSession, SessionStep
from sap_cli.recipe import Recipe, RecipeStep, RecipeStore
from sap_cli.session_analyzer import (
    analyze,
    approve_session,
    session_to_recipe,
    _generalize_args,
    _extract_tcode,
    _extract_keywords,
    _safe_recipe_id,
)
from sap_cli.session_store import SessionStore


# ---------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------

def _make_step(
    step_number: int,
    command: str = "run",
    tool_name: str = "sap_run",
    arguments: dict[str, Any] | None = None,
    intent: str = "",
    outcome: str = "success",
    error: str = "",
    before_transaction: str = "",
    after_transaction: str = "",
    before_screen: int = 0,
    after_screen: int = 0,
) -> SessionStep:
    return SessionStep(
        step_number=step_number,
        timestamp="2026-04-15T10:00:00",
        command=command,
        tool_name=tool_name,
        arguments=arguments or {},
        intent=intent,
        outcome=outcome,
        error=error,
        before_transaction=before_transaction,
        after_transaction=after_transaction,
        before_screen=before_screen,
        after_screen=after_screen,
    )


def _make_candidate_session() -> McpSession:
    """성공 + 3단계 이상 + tcode 포함하는 candidate 세션."""
    return McpSession(
        id="abcdef123456",
        started_at="2026-04-15T10:00:00",
        finished_at="2026-04-15T10:05:00",
        status="completed",
        agent="test-agent",
        sap_user="TESTUSER",
        sap_system="DEV",
        user_request="VL33N에서 배송 문서 변경",
        ai_reasoning="VL33N 실행 → 문서 번호 입력 → 저장",
        steps=[
            _make_step(1, command="run", tool_name="sap_run",
                       arguments={"tcode": "VL33N"},
                       intent="VL33N 실행",
                       after_transaction="VL33N", after_screen=100),
            _make_step(2, command="screen", tool_name="sap_screen",
                       intent="화면 확인",
                       before_transaction="VL33N", before_screen=100),
            _make_step(3, command="set", tool_name="sap_set",
                       arguments={"field": "LIKP-VBELN", "value": "80012345"},
                       intent="배송 문서 번호 입력",
                       before_transaction="VL33N", before_screen=100,
                       after_transaction="VL33N", after_screen=100),
            _make_step(4, command="press", tool_name="sap_press",
                       arguments={"element": "btn[0]"},
                       intent="실행 버튼 클릭",
                       before_transaction="VL33N", before_screen=100,
                       after_transaction="VL33N", after_screen=200),
            _make_step(5, command="set", tool_name="sap_set",
                       arguments={"field": "LIPS-LFIMG", "value": "10"},
                       intent="수량 변경",
                       before_transaction="VL33N", before_screen=200,
                       after_transaction="VL33N", after_screen=200),
            _make_step(6, command="press", tool_name="sap_press",
                       arguments={"element": "btn[11]"},
                       intent="저장",
                       before_transaction="VL33N", before_screen=200,
                       after_transaction="VL33N", after_screen=100),
        ],
        outcome="success",
        outcome_summary="배송 문서 80012345 변경 완료",
    )


# ---------------------------------------------------------------
# 변수 치환 테스트
# ---------------------------------------------------------------

class TestGeneralizeArgs:
    def test_tcode_replaced(self):
        result = _generalize_args({"tcode": "VL33N"})
        assert result["tcode"] == "${tcode}"

    def test_document_number_replaced(self):
        result = _generalize_args({"document_number": "80012345"})
        assert result["document_number"] == "${document_number}"

    def test_short_number_not_replaced(self):
        result = _generalize_args({"document_number": "123"})
        assert result["document_number"] == "123"

    def test_row_replaced(self):
        result = _generalize_args({"row": "5"})
        assert result["row"] == "${row}"

    def test_non_string_preserved(self):
        result = _generalize_args({"key": 12, "flag": True})
        assert result == {"key": 12, "flag": True}

    def test_unknown_key_preserved(self):
        result = _generalize_args({"field": "LIKP-VBELN", "value": "80012345"})
        assert result["field"] == "LIKP-VBELN"
        assert result["value"] == "${value}"

    def test_count_replaced(self):
        result = _generalize_args({"count": "46"})
        assert result["count"] == "${count}"

    def test_int_row_replaced(self):
        result = _generalize_args({"row": 5})
        assert result["row"] == "${row}"

    def test_int_count_replaced(self):
        result = _generalize_args({"count": 46})
        assert result["count"] == "${count}"

    def test_vkey_preserved(self):
        result = _generalize_args({"vkey": 11})
        assert result["vkey"] == 11

    def test_element_preserved(self):
        result = _generalize_args({"element": "btn[0]"})
        assert result["element"] == "btn[0]"

    def test_field_preserved(self):
        result = _generalize_args({"field": "LIKP-VBELN"})
        assert result["field"] == "LIKP-VBELN"

    def test_value_non_numeric_preserved(self):
        result = _generalize_args({"value": "A01"})
        assert result["value"] == "A01"

    def test_value_numeric_replaced(self):
        result = _generalize_args({"value": "100"})
        assert result["value"] == "${value}"

    def test_value_decimal_replaced(self):
        result = _generalize_args({"value": "10.5"})
        assert result["value"] == "${value}"


# ---------------------------------------------------------------
# tcode / keyword 추출 테스트
# ---------------------------------------------------------------

class TestExtract:
    def test_extract_tcode_from_after(self):
        steps = [_make_step(1, after_transaction="VL33N")]
        assert _extract_tcode(steps) == "VL33N"

    def test_extract_tcode_from_before(self):
        steps = [_make_step(1, before_transaction="mm03")]
        assert _extract_tcode(steps) == "MM03"

    def test_extract_tcode_empty(self):
        steps = [_make_step(1)]
        assert _extract_tcode(steps) == ""

    def test_extract_tcode_prefers_sap_run(self):
        steps = [
            _make_step(1, command="screen", tool_name="sap_screen",
                       before_transaction="SE38"),
            _make_step(2, command="run", tool_name="sap_run",
                       arguments={"tcode": "VA01"},
                       after_transaction="VA01"),
        ]
        assert _extract_tcode(steps) == "VA01"

    def test_extract_keywords(self):
        session = McpSession(
            id="aaa111222333",
            user_request="VL33N에서 배송 처리",
            status="completed",
            outcome="success",
        )
        kw = _extract_keywords(session, "VL33N")
        assert "VL33N" in kw
        assert "배송" in kw


class TestSafeRecipeId:
    def test_normal_tcode(self):
        assert _safe_recipe_id("VL33N", "abc123") == "VL33N_abc123"

    def test_namespace_tcode_slash_replaced(self):
        result = _safe_recipe_id("/SAPAPO/RRP3", "abc123")
        assert "/" not in result
        assert "abc123" in result

    def test_empty_tcode(self):
        assert _safe_recipe_id("", "abc123") == "abc123"


# ---------------------------------------------------------------
# 세션→레시피 변환 테스트
# ---------------------------------------------------------------

class TestSessionToRecipe:
    def test_basic_conversion(self):
        session = _make_candidate_session()
        result = session_to_recipe(session)
        assert result["id"].startswith("VL33N_")
        assert result["source_session"] == session.id
        assert result["description"] == session.user_request
        assert result["context"] == session.ai_reasoning
        assert len(result["steps"]) > 0
        assert "VL33N" in result["keywords"]

    def test_steps_exclude_exploration(self):
        session = _make_candidate_session()
        result = session_to_recipe(session)
        tools = [s["tool"] for s in result["steps"]]
        assert "sap_screen" not in tools

    def test_variable_substitution_in_steps(self):
        session = _make_candidate_session()
        result = session_to_recipe(session)
        tcode_steps = [s for s in result["steps"] if s["tool"] == "sap_run"]
        if tcode_steps:
            assert tcode_steps[0]["args"]["tcode"] == "${tcode}"

    def test_approved_by_set(self):
        session = _make_candidate_session()
        result = session_to_recipe(session, approved_by="manager1")
        assert result["approved_by"] == "manager1"

    def test_hint_from_intent(self):
        session = _make_candidate_session()
        result = session_to_recipe(session)
        hints = [s["hint"] for s in result["steps"] if s["hint"]]
        assert len(hints) > 0


# ---------------------------------------------------------------
# 승인 흐름 테스트
# ---------------------------------------------------------------

class TestApproveSession:
    def test_approve_candidate(self):
        session = _make_candidate_session()
        analyze(session)
        assert session.candidate is True

        result = approve_session(session)
        assert "error" not in result
        assert session.approved is True
        assert session.recipe_id is not None
        assert result["id"] == session.recipe_id

    def test_approve_non_candidate_rejected(self):
        session = McpSession(
            id="bbb222333444",
            status="failed",
            outcome="failed",
        )
        analyze(session)
        result = approve_session(session)
        assert "error" in result

    def test_approve_already_approved_rejected(self):
        session = _make_candidate_session()
        analyze(session)
        approve_session(session)
        result = approve_session(session)
        assert "error" in result
        assert "이미 승인" in result["error"]


# ---------------------------------------------------------------
# Dispatcher 통합 테스트 (session_candidates, session_approve)
# ---------------------------------------------------------------

class TestDispatchSessionCandidates:
    def test_candidates_returns_list(self, tmp_path):
        from sap_cli.auth.policy import SessionConfig
        store = SessionStore(config=SessionConfig(log_dir=str(tmp_path)))
        session = store.create_session(
            user_request="VL33N 배송 처리",
            ai_reasoning="VL33N → 배송",
            agent="test",
            sap_user="USER1",
        )
        store.add_step(session.id, command="run", tool_name="sap_run",
                       arguments={"tcode": "VL33N"},
                       outcome="success",
                       after_state={"session": {"transaction": "VL33N", "screen_number": 100}})
        store.add_step(session.id, command="set", tool_name="sap_set",
                       arguments={"field": "F1", "value": "V1"},
                       outcome="success",
                       before_state={"session": {"transaction": "VL33N", "screen_number": 100}},
                       after_state={"session": {"transaction": "VL33N", "screen_number": 100}})
        store.add_step(session.id, command="press", tool_name="sap_press",
                       arguments={"element": "btn[0]"},
                       outcome="success",
                       before_state={"session": {"transaction": "VL33N", "screen_number": 100}},
                       after_state={"session": {"transaction": "VL33N", "screen_number": 200}})
        store.add_step(session.id, command="set", tool_name="sap_set",
                       arguments={"field": "F2", "value": "V2"},
                       outcome="success",
                       before_state={"session": {"transaction": "VL33N", "screen_number": 200}},
                       after_state={"session": {"transaction": "VL33N", "screen_number": 200}})
        store.finish_session(session.id, outcome="success", outcome_summary="완료")

        loaded = store.load(session.id)
        assert loaded is not None
        analysis = analyze(loaded)
        assert analysis["candidate"] is True

    def test_approve_creates_recipe_and_links_ontology(self, tmp_path):
        from sap_cli.auth.policy import SessionConfig
        from sap_cli.ontology.store import OntologyStore
        from sap_cli.ontology.schema import TransactionOntology

        sessions_dir = tmp_path / "sessions"
        knowledge_dir = tmp_path / "knowledge"
        recipes_dir = tmp_path / "recipes"

        store = SessionStore(config=SessionConfig(log_dir=str(sessions_dir)))
        session = store.create_session(
            user_request="VL33N 배송 처리",
            ai_reasoning="VL33N → 배송",
            agent="test",
            sap_user="USER1",
        )
        store.add_step(session.id, command="run", tool_name="sap_run",
                       arguments={"tcode": "VL33N"},
                       outcome="success",
                       after_state={"session": {"transaction": "VL33N", "screen_number": 100}})
        store.add_step(session.id, command="set", tool_name="sap_set",
                       arguments={"field": "F1", "value": "V1"},
                       outcome="success",
                       before_state={"session": {"transaction": "VL33N", "screen_number": 100}},
                       after_state={"session": {"transaction": "VL33N", "screen_number": 100}})
        store.add_step(session.id, command="press", tool_name="sap_press",
                       arguments={"element": "btn[0]"},
                       outcome="success",
                       before_state={"session": {"transaction": "VL33N", "screen_number": 100}},
                       after_state={"session": {"transaction": "VL33N", "screen_number": 200}})
        store.add_step(session.id, command="set", tool_name="sap_set",
                       arguments={"field": "F2", "value": "V2"},
                       outcome="success",
                       before_state={"session": {"transaction": "VL33N", "screen_number": 200}},
                       after_state={"session": {"transaction": "VL33N", "screen_number": 200}})
        store.finish_session(session.id, outcome="success", outcome_summary="완료")

        loaded = store.load(session.id)
        analyze(loaded)
        assert loaded.candidate is True

        recipe_data = approve_session(loaded, approved_by="admin")
        assert "error" not in recipe_data
        assert loaded.approved is True

        recipe_store = RecipeStore(store_dir=recipes_dir)
        steps = [RecipeStep(**s) if isinstance(s, dict) else s for s in recipe_data.pop("steps", [])]
        recipe = Recipe(**recipe_data, steps=steps)
        recipe_store.save(recipe)

        saved_recipe = recipe_store.load(recipe.id)
        assert saved_recipe is not None
        assert saved_recipe.source_session == session.id
        assert saved_recipe.approved_by == "admin"
        assert len(saved_recipe.steps) > 0

        ont_store = OntologyStore(knowledge_dir=knowledge_dir)
        ont = TransactionOntology(tcode="VL33N", title="배송 문서 변경")
        ont.recipes.append(recipe.id)
        ont_store.save_transaction(ont)

        reloaded = ont_store.load_transaction("VL33N")
        assert recipe.id in reloaded.recipes


class TestRecipeModelExtension:
    def test_source_session_field(self):
        r = Recipe(id="test-1", description="테스트", source_session="abc123", approved_by="admin")
        assert r.source_session == "abc123"
        assert r.approved_by == "admin"

    def test_serialize_deserialize(self, tmp_path):
        store = RecipeStore(store_dir=tmp_path)
        r = Recipe(
            id="test-2",
            description="테스트 레시피",
            source_session="sess123",
            approved_by="manager",
            steps=[RecipeStep(tool="sap_run", args={"tcode": "${tcode}"}, hint="실행")],
        )
        store.save(r)
        loaded = store.load("test-2")
        assert loaded.source_session == "sess123"
        assert loaded.approved_by == "manager"
        assert loaded.steps[0].args["tcode"] == "${tcode}"


# ---------------------------------------------------------------
# 엣지 케이스
# ---------------------------------------------------------------

class TestEdgeCases:
    def test_session_no_tcode(self):
        session = McpSession(
            id="ccc333444555",
            status="completed",
            outcome="success",
            user_request="테스트",
            steps=[
                _make_step(1, command="set", tool_name="sap_set"),
                _make_step(2, command="press", tool_name="sap_press"),
                _make_step(3, command="set", tool_name="sap_set"),
            ],
        )
        result = session_to_recipe(session)
        assert result["id"] == session.id

    def test_empty_session_conversion(self):
        session = McpSession(
            id="ddd444555666",
            status="completed",
            outcome="success",
            user_request="빈 세션",
            steps=[],
        )
        result = session_to_recipe(session)
        assert result["steps"] == []

    def test_recovery_steps_excluded_from_recipe(self):
        session = McpSession(
            id="eee555666777",
            started_at="2026-04-15T10:00:00",
            status="completed",
            outcome="success",
            user_request="에러 복구 포함 세션",
            steps=[
                _make_step(1, command="run", tool_name="sap_run",
                           arguments={"tcode": "VA01"},
                           after_transaction="VA01", after_screen=100),
                _make_step(2, command="set", tool_name="sap_set",
                           arguments={"field": "F1", "value": "X"},
                           outcome="error", error="필드 오류",
                           before_transaction="VA01", before_screen=100),
                _make_step(3, command="vkey", tool_name="sap_vkey",
                           arguments={"key": 12},
                           before_transaction="VA01", before_screen=100,
                           after_transaction="VA01", after_screen=100),
                _make_step(4, command="set", tool_name="sap_set",
                           arguments={"field": "F1", "value": "Y"},
                           before_transaction="VA01", before_screen=100,
                           after_transaction="VA01", after_screen=100),
                _make_step(5, command="press", tool_name="sap_press",
                           arguments={"element": "btn[11]"},
                           before_transaction="VA01", before_screen=100,
                           after_transaction="VA01", after_screen=200),
            ],
        )
        result = session_to_recipe(session)
        tools_and_args = [(s["tool"], s.get("args", {})) for s in result["steps"]]
        error_steps = [t for t, a in tools_and_args if t == "sap_vkey" and a.get("key") == 12]
        assert len(error_steps) == 0
