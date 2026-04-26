"""Phase 14-4: Recipe 활용 (AI 자동 참조) 테스트."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch, MagicMock

import pytest

from sap_cli.recipe import Recipe, RecipeStep, RecipeStore


# ---------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------

def _make_recipe(
    recipe_id: str,
    description: str = "테스트 레시피",
    context: str = "",
    keywords: list[str] | None = None,
    tcode: str = "VA01",
    extra_steps: list[RecipeStep] | None = None,
    success_count: int = 0,
) -> Recipe:
    steps = extra_steps or [
        RecipeStep(tool="sap_run", args={"tcode": tcode}, hint="트랜잭션 실행"),
        RecipeStep(tool="sap_set", args={"element_id": "F1", "value": "${value}"}, hint="필드 입력"),
        RecipeStep(tool="sap_press", args={"element_id": "btn[11]"}, hint="저장"),
    ]
    return Recipe(
        id=recipe_id,
        description=description,
        context=context,
        keywords=keywords or [],
        steps=steps,
        success_count=success_count,
    )


@pytest.fixture
def recipe_dir(tmp_path: Path) -> Path:
    return tmp_path / "recipes"


@pytest.fixture
def store(recipe_dir: Path) -> RecipeStore:
    return RecipeStore(store_dir=recipe_dir)


# ---------------------------------------------------------------
# RecipeStore.query_for_ai
# ---------------------------------------------------------------

class TestQueryForAi:
    def test_by_tcode_only(self, store: RecipeStore):
        r1 = _make_recipe("VA01_recipe1", tcode="VA01", description="수주 생성")
        r2 = _make_recipe("MM03_recipe1", tcode="MM03", description="자재 조회")
        store.save(r1)
        store.save(r2)

        results = store.query_for_ai(tcode="VA01")
        assert len(results) == 1
        assert results[0][1].id == "VA01_recipe1"

    def test_by_keywords_only(self, store: RecipeStore):
        r1 = _make_recipe("r1", keywords=["출고", "납품"], description="출고 납품 생성")
        r2 = _make_recipe("r2", keywords=["자재", "조회"], description="자재 마스터 조회")
        store.save(r1)
        store.save(r2)

        results = store.query_for_ai(keywords="출고 납품")
        ids = [r.id for _, r in results]
        assert "r1" in ids
        assert "r2" not in ids

    def test_tcode_plus_keywords(self, store: RecipeStore):
        r1 = _make_recipe("VL33N_r1", tcode="VL33N", keywords=["출고", "변경"],
                          description="출고 납품 변경")
        r2 = _make_recipe("VL33N_r2", tcode="VL33N", keywords=["납품", "삭제"],
                          description="납품 삭제")
        r3 = _make_recipe("VA01_r1", tcode="VA01", keywords=["수주"],
                          description="수주 생성")
        store.save(r1)
        store.save(r2)
        store.save(r3)

        results = store.query_for_ai(tcode="VL33N", keywords="출고 변경")
        assert len(results) >= 1
        assert results[0][1].id == "VL33N_r1"
        assert results[0][0] > results[1][0]

    def test_no_match(self, store: RecipeStore):
        r1 = _make_recipe("VA01_r1", tcode="VA01", keywords=["수주"])
        store.save(r1)

        results = store.query_for_ai(tcode="ZZ99")
        assert len(results) == 0

    def test_empty_store(self, store: RecipeStore):
        results = store.query_for_ai(tcode="VA01")
        assert results == []

    def test_success_count_tiebreak(self, store: RecipeStore):
        r1 = _make_recipe("VA01_r1", tcode="VA01", success_count=2,
                          keywords=["수주"], description="수주")
        r2 = _make_recipe("VA01_r2", tcode="VA01", success_count=10,
                          keywords=["수주"], description="수주")
        store.save(r1)
        store.save(r2)

        results = store.query_for_ai(tcode="VA01", keywords="수주")
        assert results[0][1].id == "VA01_r2"

    def test_id_contains_tcode(self, store: RecipeStore):
        r = _make_recipe("ME21N_abc123", tcode="XX00", description="구매오더")
        store.save(r)

        results = store.query_for_ai(tcode="ME21N")
        assert len(results) == 1
        assert results[0][1].id == "ME21N_abc123"

    def test_context_keyword_match(self, store: RecipeStore):
        r = _make_recipe("r1", keywords=[], description="일반",
                         context="shipping point 설정 작업")
        store.save(r)

        results = store.query_for_ai(keywords="shipping point")
        assert len(results) == 1


# ---------------------------------------------------------------
# _dispatch_recipe_query
# ---------------------------------------------------------------

class TestDispatchRecipeQuery:
    def _setup_store(self, tmp_path: Path) -> RecipeStore:
        store = RecipeStore(store_dir=tmp_path / "recipes")
        store.save(_make_recipe(
            "VL33N_r1", tcode="VL33N",
            keywords=["출고", "납품", "변경"],
            description="출고 납품 변경",
            context="VL33N에서 납품 문서의 출고일자를 변경하는 작업",
            success_count=5,
        ))
        store.save(_make_recipe(
            "VL33N_r2", tcode="VL33N",
            keywords=["납품", "삭제"],
            description="납품 삭제",
            context="VL33N에서 납품 삭제",
        ))
        return store

    def test_single_match_returns_execution_plan(self, tmp_path: Path):
        from sap_cli.dispatcher import _dispatch_recipe_query

        store = RecipeStore(store_dir=tmp_path / "recipes")
        store.save(_make_recipe(
            "SOLO_recipe", tcode="MM03",
            keywords=["자재", "조회"],
            description="자재 마스터 조회",
        ))

        with patch("sap_cli.dispatcher._get_recipe_store", return_value=store):
            result = _dispatch_recipe_query({"tcode": "MM03", "keywords": "자재"})

        assert result["count"] == 1
        assert result["exact_match"] is True
        assert result["recipe_id"] == "SOLO_recipe"
        assert "quality_score" in result
        assert "replay_quality" in result
        assert "execution_plan" in result
        assert len(result["execution_plan"]) == 3
        assert result["execution_plan"][0]["tool"] == "sap_run"
        assert "instruction" in result

    def test_multiple_match_returns_list(self, tmp_path: Path):
        from sap_cli.dispatcher import _dispatch_recipe_query

        store = self._setup_store(tmp_path)

        with patch("sap_cli.dispatcher._get_recipe_store", return_value=store):
            result = _dispatch_recipe_query({"tcode": "VL33N"})

        assert result["count"] == 2
        assert result["exact_match"] is False
        assert len(result["recipes"]) == 2
        assert result["recipes"][0]["relevance_score"] >= result["recipes"][1]["relevance_score"]
        assert "quality_score" in result["recipes"][0]
        assert "replay_quality" in result["recipes"][0]
        assert "hint" in result

    def test_show_and_list_expose_replay_quality_meta(self, tmp_path: Path):
        from sap_cli.dispatcher import _dispatch_recipe_list, _dispatch_recipe_show

        store = RecipeStore(store_dir=tmp_path / "recipes")
        store.save(_make_recipe("VA01_replay_meta", tcode="VA01"))
        store.record_replay_execution(
            "VA01_replay_meta",
            status="success",
            execution_ms=1234,
            system_name="TST",
            client="100",
        )
        store.record_replay_execution(
            "VA01_replay_meta",
            status="failed",
            execution_ms=2000,
            failure_type="sap_status_error",
            system_name="TST",
            client="100",
        )

        with patch("sap_cli.dispatcher._get_recipe_store", return_value=store):
            shown = _dispatch_recipe_show({"recipe_id": "VA01_replay_meta"})
            listed = _dispatch_recipe_list()

        assert shown["quality_score"] > 0
        assert shown["replay_quality"]["execution_count"] == 2
        assert shown["replay_quality"]["success_rate"] == 0.5
        assert shown["replay_quality"]["failure_type_counts"]["sap_status_error"] == 1
        assert shown["replay_quality"]["system_client_stats"]["TST/100"]["total"] == 2
        assert listed["recipes"][0]["quality_score"] > 0
        assert listed["recipes"][0]["replay_quality"]["average_execution_ms"] == 1617
        assert "failure_type_counts" not in listed["recipes"][0]["replay_quality"]
        assert "system_client_stats" not in listed["recipes"][0]["replay_quality"]

    def test_no_match(self, tmp_path: Path):
        from sap_cli.dispatcher import _dispatch_recipe_query

        store = RecipeStore(store_dir=tmp_path / "recipes")

        with patch("sap_cli.dispatcher._get_recipe_store", return_value=store):
            result = _dispatch_recipe_query({"tcode": "ZZ99"})

        assert result["count"] == 0
        assert result["recipes"] == []

    def test_missing_both_params(self):
        from sap_cli.dispatcher import _dispatch_recipe_query

        with pytest.raises(ValueError, match="tcode 또는 keywords"):
            _dispatch_recipe_query({})

    def test_keywords_only(self, tmp_path: Path):
        from sap_cli.dispatcher import _dispatch_recipe_query

        store = self._setup_store(tmp_path)

        with patch("sap_cli.dispatcher._get_recipe_store", return_value=store):
            result = _dispatch_recipe_query({"keywords": "출고 변경"})

        assert result["count"] >= 1
        ids = [r["id"] for r in result.get("recipes", [])] if result["count"] > 1 else [result.get("recipe_id")]
        assert "VL33N_r1" in ids


# ---------------------------------------------------------------
# ontology_query 확장
# ---------------------------------------------------------------

class TestOntologyQueryRecipeEnhanced:
    def _make_ontology_with_recipes(self, tmp_path: Path):
        from sap_cli.ontology.schema import TransactionOntology
        from sap_cli.ontology.store import OntologyStore

        ont_store = OntologyStore(str(tmp_path / "knowledge"))
        ontology = TransactionOntology(tcode="VL33N")
        ontology.recipes = ["VL33N_r1", "VL33N_r2"]
        ont_store.save_transaction(ontology)

        recipe_store = RecipeStore(store_dir=tmp_path / "recipes")
        recipe_store.save(_make_recipe(
            "VL33N_r1", tcode="VL33N",
            keywords=["출고", "변경"],
            description="출고 납품 변경",
            context="VL33N에서 납품 출고일 변경",
            success_count=5,
        ))
        recipe_store.save(_make_recipe(
            "VL33N_r2", tcode="VL33N",
            keywords=["납품", "삭제"],
            description="납품 삭제",
            context="VL33N에서 납품 문서 삭제",
            success_count=1,
        ))
        return ont_store, recipe_store

    def test_recipe_details_include_context_keywords(self, tmp_path: Path):
        from sap_cli.dispatcher import _dispatch_ontology_query

        ont_store, recipe_store = self._make_ontology_with_recipes(tmp_path)

        with patch("sap_cli.dispatcher._get_store", return_value=ont_store), \
             patch("sap_cli.dispatcher._get_recipe_store", return_value=recipe_store):
            result = _dispatch_ontology_query({"tcode": "VL33N"})

        assert "recipe_details" in result
        assert len(result["recipe_details"]) == 2
        first = result["recipe_details"][0]
        assert first["success_count"] == 5
        assert "context" in first
        assert "keywords" in first
        assert first["context"] == "VL33N에서 납품 출고일 변경"
        assert "출고" in first["keywords"]

    def test_recipe_details_sorted_by_success(self, tmp_path: Path):
        from sap_cli.dispatcher import _dispatch_ontology_query

        ont_store, recipe_store = self._make_ontology_with_recipes(tmp_path)

        with patch("sap_cli.dispatcher._get_store", return_value=ont_store), \
             patch("sap_cli.dispatcher._get_recipe_store", return_value=recipe_store):
            result = _dispatch_ontology_query({"tcode": "VL33N"})

        details = result["recipe_details"]
        assert details[0]["success_count"] >= details[1]["success_count"]

    def test_recipe_hint_present(self, tmp_path: Path):
        from sap_cli.dispatcher import _dispatch_ontology_query

        ont_store, recipe_store = self._make_ontology_with_recipes(tmp_path)

        with patch("sap_cli.dispatcher._get_store", return_value=ont_store), \
             patch("sap_cli.dispatcher._get_recipe_store", return_value=recipe_store):
            result = _dispatch_ontology_query({"tcode": "VL33N"})

        assert "recipe_hint" in result
        assert "2개의 레시피" in result["recipe_hint"]
        assert "sap_recipe_show" in result["recipe_hint"]

    def test_no_recipes_no_hint(self, tmp_path: Path):
        from sap_cli.dispatcher import _dispatch_ontology_query
        from sap_cli.ontology.schema import TransactionOntology
        from sap_cli.ontology.store import OntologyStore

        ont_store = OntologyStore(str(tmp_path / "knowledge"))
        ontology = TransactionOntology(tcode="VA01")
        ont_store.save_transaction(ontology)

        with patch("sap_cli.dispatcher._get_store", return_value=ont_store):
            result = _dispatch_ontology_query({"tcode": "VA01"})

        assert "recipe_hint" not in result
        assert "recipe_details" not in result


# ---------------------------------------------------------------
# MCP 도구 등록
# ---------------------------------------------------------------

class TestMcpToolRegistration:
    def test_sap_recipe_query_in_tools(self):
        from sap_cli.mcp_server import TOOL_DEFINITIONS
        names = [t["name"] for t in TOOL_DEFINITIONS]
        assert "sap_recipe_query" in names

    def test_sap_recipe_query_schema(self):
        from sap_cli.mcp_server import TOOL_DEFINITIONS
        tool = next(t for t in TOOL_DEFINITIONS if t["name"] == "sap_recipe_query")
        props = tool["inputSchema"]["properties"]
        assert "tcode" in props
        assert "keywords" in props

    def test_command_registry_has_recipe_query(self):
        from sap_cli.dispatcher import COMMAND_REGISTRY
        assert "recipe_query" in COMMAND_REGISTRY
        assert COMMAND_REGISTRY["recipe_query"].tool_name == "sap_recipe_query"


# ---------------------------------------------------------------
# execution_plan 형식 검증
# ---------------------------------------------------------------

class TestExecutionPlanFormat:
    def test_plan_has_order_tool_args(self, store: RecipeStore):
        r = _make_recipe(
            "test_plan", tcode="VA01",
            extra_steps=[
                RecipeStep(tool="sap_run", args={"tcode": "VA01"}, hint="실행"),
                RecipeStep(tool="sap_set", args={"element_id": "F1", "value": "X"}),
                RecipeStep(tool="sap_press", args={"element_id": "btn[11]"}, hint="저장"),
            ],
        )
        store.save(r)

        from sap_cli.dispatcher import _dispatch_recipe_query
        with patch("sap_cli.dispatcher._get_recipe_store", return_value=store):
            result = _dispatch_recipe_query({"tcode": "VA01"})

        plan = result["execution_plan"]
        assert plan[0]["order"] == 1
        assert plan[0]["tool"] == "sap_run"
        assert plan[0]["args"]["tcode"] == "VA01"
        assert plan[0]["hint"] == "실행"
        assert "hint" not in plan[1]
        assert plan[2]["hint"] == "저장"
