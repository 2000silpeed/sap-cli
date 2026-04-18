"""Recipe 중복 탐지 + HITL 테스트."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from sap_cli.recipe import Recipe, RecipeStep, RecipeStore, efficiency_score


@pytest.fixture
def store(tmp_path: Path) -> RecipeStore:
    return RecipeStore(store_dir=tmp_path / "recipes")


def _recipe(
    rid: str,
    desc: str = "",
    context: str = "",
    keywords: list[str] | None = None,
    tcode: str = "VA01",
    n_steps: int = 3,
    success: int = 0,
) -> Recipe:
    steps = [RecipeStep(tool="sap_run", args={"tcode": tcode})]
    for i in range(n_steps - 1):
        steps.append(RecipeStep(tool="sap_set", args={"element_id": f"F{i}", "value": "x"}))
    return Recipe(
        id=rid,
        description=desc or rid,
        context=context,
        keywords=keywords or [],
        steps=steps,
        success_count=success,
    )


class TestFindSimilar:
    def test_identical_content_high_similarity(self, store: RecipeStore):
        store.save(_recipe("va01-a", desc="수주 생성", keywords=["수주", "sales"], tcode="VA01"))
        results = store.find_similar(
            description="수주 생성",
            context="",
            keywords=["수주", "sales"],
            steps=[RecipeStep(tool="sap_run", args={"tcode": "VA01"})],
        )
        assert len(results) == 1
        sim, r = results[0]
        assert r.id == "va01-a"
        assert sim >= 0.6

    def test_different_tcode_low_similarity(self, store: RecipeStore):
        store.save(_recipe("va01-a", keywords=["수주"], tcode="VA01"))
        results = store.find_similar(
            description="자재 조회",
            context="",
            keywords=["자재"],
            steps=[RecipeStep(tool="sap_run", args={"tcode": "MM03"})],
        )
        assert results == []

    def test_exclude_self(self, store: RecipeStore):
        store.save(_recipe("va01-a", keywords=["수주"], tcode="VA01"))
        results = store.find_similar(
            description="수주 생성",
            context="",
            keywords=["수주"],
            steps=[RecipeStep(tool="sap_run", args={"tcode": "VA01"})],
            exclude_id="va01-a",
        )
        assert results == []


class TestEfficiencyScore:
    def test_fewer_steps_scores_higher(self):
        short = _recipe("a", n_steps=2)
        long = _recipe("b", n_steps=10)
        assert efficiency_score(short) > efficiency_score(long)

    def test_more_success_scores_higher(self):
        proven = _recipe("a", n_steps=3, success=10)
        new = _recipe("b", n_steps=3, success=0)
        assert efficiency_score(proven) > efficiency_score(new)


class TestDispatchRecipeSaveHITL:
    def _patch_store(self, store: RecipeStore):
        return patch("sap_cli.dispatcher._get_recipe_store", return_value=store)

    def _patch_link(self):
        return patch("sap_cli.dispatcher._link_recipe_to_ontology", return_value=[])

    def test_new_recipe_no_duplicate_saves(self, store: RecipeStore):
        from sap_cli.dispatcher import _dispatch_recipe_save
        with self._patch_store(store), self._patch_link():
            result = _dispatch_recipe_save({
                "recipe_id": "va01-new",
                "description": "수주 생성",
                "context": "표준 수주",
                "keywords": ["수주"],
                "steps": [{"tool": "sap_run", "args": {"tcode": "VA01"}}],
            })
        assert result["status"] == "saved"
        assert store.load("va01-new") is not None

    def test_duplicate_returns_candidates_without_on_duplicate(self, store: RecipeStore):
        from sap_cli.dispatcher import _dispatch_recipe_save
        store.save(_recipe(
            "va01-existing",
            desc="수주 생성 기존",
            context="표준 수주 생성 경로",
            keywords=["수주", "sales"],
            tcode="VA01",
            n_steps=3,
            success=5,
        ))
        with self._patch_store(store), self._patch_link():
            result = _dispatch_recipe_save({
                "recipe_id": "va01-new",
                "description": "수주 생성 신규",
                "context": "표준 수주 생성",
                "keywords": ["수주", "sales"],
                "steps": [{"tool": "sap_run", "args": {"tcode": "VA01"}}],
            })
        assert result["status"] == "duplicate_candidates"
        assert len(result["candidates"]) == 1
        assert result["candidates"][0]["id"] == "va01-existing"
        assert result["candidate_ids"] == ["va01-existing"]
        assert result["recommended"] in ("keep_existing", "replace", "keep_both")
        assert store.load("va01-new") is None  # 저장 안됨

    def test_on_duplicate_keep_existing_skips(self, store: RecipeStore):
        from sap_cli.dispatcher import _dispatch_recipe_save
        store.save(_recipe("va01-existing", keywords=["수주"], tcode="VA01"))
        with self._patch_store(store), self._patch_link():
            result = _dispatch_recipe_save({
                "recipe_id": "va01-new",
                "description": "수주 생성",
                "context": "",
                "keywords": ["수주"],
                "steps": [{"tool": "sap_run", "args": {"tcode": "VA01"}}],
                "on_duplicate": "keep_existing",
            })
        assert result["status"] == "skipped"
        assert store.load("va01-new") is None
        assert store.load("va01-existing") is not None

    def test_on_duplicate_replace_requires_approved_ids(self, store: RecipeStore):
        from sap_cli.dispatcher import _dispatch_recipe_save
        store.save(_recipe("va01-existing", keywords=["수주"], tcode="VA01", success=7))
        with self._patch_store(store), self._patch_link():
            result = _dispatch_recipe_save({
                "recipe_id": "va01-new",
                "description": "수주 생성",
                "context": "",
                "keywords": ["수주"],
                "steps": [{"tool": "sap_run", "args": {"tcode": "VA01"}}],
                "on_duplicate": "replace",
            })
        assert result["status"] == "error"
        assert "approved_ids" in result["message"]
        assert store.load("va01-existing") is not None

    def test_on_duplicate_replace_deletes_and_inherits(self, store: RecipeStore):
        from sap_cli.dispatcher import _dispatch_recipe_save
        store.save(_recipe(
            "va01-existing",
            desc="수주 생성",
            keywords=["수주"],
            tcode="VA01",
            success=7,
        ))
        with self._patch_store(store), self._patch_link():
            result = _dispatch_recipe_save({
                "recipe_id": "va01-new",
                "description": "수주 생성 최적화",
                "context": "표준 수주",
                "keywords": ["수주"],
                "steps": [{"tool": "sap_run", "args": {"tcode": "VA01"}}],
                "on_duplicate": "replace",
                "approved_ids": ["va01-existing"],
            })
        assert result["status"] == "saved"
        assert result["inherited_success_count"] == 7
        assert "va01-existing" in result["replaced_ids"]
        assert store.load("va01-existing") is None
        new_r = store.load("va01-new")
        assert new_r is not None
        assert new_r.success_count == 7

    def test_on_duplicate_replace_rejects_candidate_drift(self, store: RecipeStore):
        from sap_cli.dispatcher import _dispatch_recipe_save
        store.save(_recipe("va01-existing1", keywords=["수주"], tcode="VA01", success=3))
        store.save(_recipe("va01-existing2", keywords=["수주"], tcode="VA01", success=2))
        with self._patch_store(store), self._patch_link():
            result = _dispatch_recipe_save({
                "recipe_id": "va01-new",
                "description": "수주 생성",
                "context": "",
                "keywords": ["수주"],
                "steps": [{"tool": "sap_run", "args": {"tcode": "VA01"}}],
                "on_duplicate": "replace",
                "approved_ids": ["va01-existing1"],  # existing2는 누락
            })
        assert result["status"] == "candidate_drift"
        assert "va01-existing2" in result["drift_ids"]
        assert store.load("va01-existing1") is not None
        assert store.load("va01-existing2") is not None
        assert store.load("va01-new") is None

    def test_on_duplicate_keep_both_forces_save(self, store: RecipeStore):
        from sap_cli.dispatcher import _dispatch_recipe_save
        store.save(_recipe("va01-existing", keywords=["수주"], tcode="VA01", success=3))
        with self._patch_store(store), self._patch_link():
            result = _dispatch_recipe_save({
                "recipe_id": "va01-new",
                "description": "수주 생성",
                "context": "",
                "keywords": ["수주"],
                "steps": [{"tool": "sap_run", "args": {"tcode": "VA01"}}],
                "on_duplicate": "keep_both",
            })
        assert result["status"] == "saved"
        assert result["inherited_success_count"] == 0
        assert store.load("va01-existing") is not None
        assert store.load("va01-new") is not None

    def test_invalid_on_duplicate_returns_error(self, store: RecipeStore):
        from sap_cli.dispatcher import _dispatch_recipe_save
        with self._patch_store(store), self._patch_link():
            result = _dispatch_recipe_save({
                "recipe_id": "va01-new",
                "description": "test",
                "context": "",
                "keywords": [],
                "steps": [],
                "on_duplicate": "merge",
            })
        assert result["status"] == "error"

    def test_update_existing_id_bypasses_dedup(self, store: RecipeStore):
        """동일 recipe_id 업데이트는 유사도 검사 없이 즉시 덮어씀."""
        from sap_cli.dispatcher import _dispatch_recipe_save
        store.save(_recipe("va01-a", keywords=["수주"], tcode="VA01"))
        store.save(_recipe("va01-b", keywords=["수주", "sales"], tcode="VA01"))
        with self._patch_store(store), self._patch_link():
            result = _dispatch_recipe_save({
                "recipe_id": "va01-a",
                "description": "수주 생성 업데이트",
                "context": "",
                "keywords": ["수주"],
                "steps": [{"tool": "sap_run", "args": {"tcode": "VA01"}}],
            })
        assert result["status"] == "saved"
        assert result["updated"] is True


class TestCollectRecipeHints:
    def test_returns_hints_when_tcode_has_recipes(self, tmp_path: Path):
        from sap_cli.dispatcher import _collect_recipe_hints
        store = RecipeStore(store_dir=tmp_path / "r")
        store.save(_recipe("va01-a", desc="수주 생성", tcode="VA01", success=5))
        store.save(_recipe("va01-b", desc="크레딧 메모", tcode="VA01", success=2))
        with patch("sap_cli.dispatcher._get_recipe_store", return_value=store):
            hints = _collect_recipe_hints("VA01")
        assert hints is not None
        assert hints["available"] == 2
        assert len(hints["candidates"]) == 2
        assert hints["candidates"][0]["id"] == "va01-a"  # success_count 높은 순
        assert "instruction" in hints

    def test_returns_none_when_no_recipes(self, tmp_path: Path):
        from sap_cli.dispatcher import _collect_recipe_hints
        store = RecipeStore(store_dir=tmp_path / "r")
        with patch("sap_cli.dispatcher._get_recipe_store", return_value=store):
            assert _collect_recipe_hints("ZZ99") is None

    def test_limits_candidates(self, tmp_path: Path):
        from sap_cli.dispatcher import _collect_recipe_hints
        store = RecipeStore(store_dir=tmp_path / "r")
        for i in range(5):
            store.save(_recipe(f"va01-{i}", tcode="VA01", success=i))
        with patch("sap_cli.dispatcher._get_recipe_store", return_value=store):
            hints = _collect_recipe_hints("VA01", limit=3)
        assert hints["available"] == 5
        assert len(hints["candidates"]) == 3

    def test_empty_tcode_returns_none(self):
        from sap_cli.dispatcher import _collect_recipe_hints
        assert _collect_recipe_hints("") is None

    def test_hints_exclude_sensitive_context(self, tmp_path: Path):
        from sap_cli.dispatcher import _collect_recipe_hints
        store = RecipeStore(store_dir=tmp_path / "r")
        store.save(_recipe(
            "va01-a",
            desc="수주 생성",
            context="사내 비밀 사전조건 A",
            keywords=["수주", "사내식별자"],
            tcode="VA01",
        ))
        with patch("sap_cli.dispatcher._get_recipe_store", return_value=store):
            hints = _collect_recipe_hints("VA01")
        cand = hints["candidates"][0]
        assert "context" not in cand
        assert "keywords" not in cand
        assert "사내 비밀" not in str(hints)


class TestAtomicSaveAndCorruption:
    def test_corrupt_json_returns_none_and_logs(self, tmp_path: Path, caplog):
        import logging
        store = RecipeStore(store_dir=tmp_path / "r")
        path = tmp_path / "r" / "broken.json"
        path.write_text("{not valid json", encoding="utf-8")
        with caplog.at_level(logging.WARNING, logger="sap_cli.recipe"):
            result = store.load("broken")
        assert result is None
        assert any("corrupt recipe JSON" in rec.message for rec in caplog.records)

    def test_save_is_atomic_no_tmp_leftover(self, tmp_path: Path):
        store = RecipeStore(store_dir=tmp_path / "r")
        store.save(_recipe("va01-a", tcode="VA01"))
        files = list((tmp_path / "r").iterdir())
        # 오직 최종 JSON만 남고 tmp 파일은 없어야 함
        assert all(f.name.endswith(".json") for f in files)
        assert all(".tmp-" not in f.name for f in files)


class _FakeOntStore:
    def __init__(self, ontologies: dict):
        self._map = dict(ontologies)

    def load_transaction(self, tc):
        return self._map.get(tc)

    def save_transaction(self, o):
        self._map[o.tcode] = o

    def list_transactions(self):
        return list(self._map.keys())


class TestDeleteWithLinks:
    def test_delete_cleans_ontology_links(self, tmp_path: Path):
        from sap_cli.dispatcher import _delete_recipe_with_links
        from sap_cli.ontology.schema import ActionPath, ActionStep, TransactionOntology

        recipe_store = RecipeStore(store_dir=tmp_path / "recipes")
        recipe_store.save(_recipe(
            "va01-a", desc="수주 생성", tcode="VA01", success=3,
        ))

        onto = TransactionOntology(tcode="VA01", recipes=["va01-a", "other"])
        onto.action_paths["수주 생성"] = ActionPath(
            context="", keywords=[], steps=[ActionStep(tool="sap_run", args={"tcode": "VA01"})],
        )
        fake_ont = _FakeOntStore({"VA01": onto})
        with patch("sap_cli.dispatcher._get_recipe_store", return_value=recipe_store), \
             patch("sap_cli.dispatcher._get_store", return_value=fake_ont):
            result = _delete_recipe_with_links("va01-a")

        assert result["deleted"] is True
        assert "VA01" in result["cleaned_tcodes"]
        assert "va01-a" not in fake_ont._map["VA01"].recipes
        assert "other" in fake_ont._map["VA01"].recipes
        assert "수주 생성" not in fake_ont._map["VA01"].action_paths

    def test_delete_with_empty_tcodes_scans_all_ontologies(self, tmp_path: Path):
        """tcode를 추출할 수 없는 레시피 삭제 시에도 stale 링크 제거."""
        from sap_cli.dispatcher import _delete_recipe_with_links
        from sap_cli.ontology.schema import ActionPath, ActionStep, TransactionOntology

        recipe_store = RecipeStore(store_dir=tmp_path / "recipes")
        # sap_run/sap_command 없는 레시피
        r = Recipe(
            id="orphan-a",
            description="고아 레시피",
            steps=[RecipeStep(tool="sap_set", args={"element_id": "F1", "value": "x"})],
        )
        recipe_store.save(r)

        onto = TransactionOntology(tcode="XK03", recipes=["orphan-a", "keep"])
        onto.action_paths["고아 레시피"] = ActionPath(
            context="", keywords=[], steps=[ActionStep(tool="sap_set", args={})],
        )
        fake_ont = _FakeOntStore({"XK03": onto})
        with patch("sap_cli.dispatcher._get_recipe_store", return_value=recipe_store), \
             patch("sap_cli.dispatcher._get_store", return_value=fake_ont):
            result = _delete_recipe_with_links("orphan-a")

        assert result["deleted"] is True
        assert "XK03" in result["cleaned_tcodes"]
        assert "orphan-a" not in fake_ont._map["XK03"].recipes
        assert "keep" in fake_ont._map["XK03"].recipes
        assert "고아 레시피" not in fake_ont._map["XK03"].action_paths


class TestUpdateUnlinksStaleOntology:
    def test_update_removes_old_action_paths(self, tmp_path: Path):
        """기존 레시피의 description이 바뀌면 옛 path_key를 ontology에서 제거."""
        from sap_cli.dispatcher import _dispatch_recipe_save
        from sap_cli.ontology.schema import ActionPath, ActionStep, TransactionOntology

        recipe_store = RecipeStore(store_dir=tmp_path / "recipes")
        recipe_store.save(_recipe("va01-a", desc="옛 설명", tcode="VA01"))

        onto = TransactionOntology(tcode="VA01", recipes=["va01-a"])
        onto.action_paths["옛 설명"] = ActionPath(
            context="", keywords=[], steps=[ActionStep(tool="sap_run", args={"tcode": "VA01"})],
        )
        fake_ont = _FakeOntStore({"VA01": onto})
        with patch("sap_cli.dispatcher._get_recipe_store", return_value=recipe_store), \
             patch("sap_cli.dispatcher._get_store", return_value=fake_ont):
            result = _dispatch_recipe_save({
                "recipe_id": "va01-a",
                "description": "새 설명",
                "context": "",
                "keywords": [],
                "steps": [{"tool": "sap_run", "args": {"tcode": "VA01"}}],
            })
        assert result["status"] == "saved"
        assert result["updated"] is True
        assert "옛 설명" not in fake_ont._map["VA01"].action_paths
        assert "새 설명" in fake_ont._map["VA01"].action_paths


class TestLoadDistinguishesErrors:
    def test_io_error_logged_separately(self, tmp_path: Path, caplog):
        import logging
        store = RecipeStore(store_dir=tmp_path / "r")
        store.save(_recipe("va01-a", tcode="VA01"))
        with caplog.at_level(logging.WARNING, logger="sap_cli.recipe"), \
             patch.object(Path, "read_text", side_effect=PermissionError("no perm")):
            result = store.load("va01-a")
        assert result is None
        assert any("I/O error" in rec.message for rec in caplog.records)
        assert not any("corrupt recipe JSON" in rec.message for rec in caplog.records)
