"""Phase 18-4: 크로스 트랜잭션 레시피 — tcode_sequence + chain_search 테스트."""

from __future__ import annotations

from pathlib import Path

import pytest

from sap_cli.recipe import Recipe, RecipeStep, RecipeStore


@pytest.fixture
def store(tmp_path: Path) -> RecipeStore:
    return RecipeStore(store_dir=tmp_path / "recipes")


def _multi(rid: str, tcodes: list[str], success: int = 0, disabled: bool = False) -> Recipe:
    steps = []
    for tc in tcodes:
        steps.append(RecipeStep(tool="sap_run", args={"tcode": tc}))
        steps.append(RecipeStep(tool="sap_press", args={"element": "btn[0]"}))
    return Recipe(
        id=rid,
        description=f"{' → '.join(tcodes)} 복합",
        keywords=[t.lower() for t in tcodes],
        steps=steps,
        success_count=success,
        disabled=disabled,
    )


class TestSchema:
    def test_default_empty_sequence(self):
        r = Recipe(id="x", description="x")
        assert r.tcode_sequence == []

    def test_legacy_json_loads(self, store: RecipeStore, tmp_path: Path):
        legacy = tmp_path / "recipes" / "legacy.json"
        legacy.write_text(
            '{"id":"legacy","description":"old","steps":[]}',
            encoding="utf-8",
        )
        r = store.load("legacy")
        assert r is not None
        assert r.tcode_sequence == []


class TestAutoFillOnSave:
    def test_save_extracts_sequence(self, store: RecipeStore):
        r = _multi("chain1", ["SPRO", "MM03", "VA01"])
        store.save(r)
        loaded = store.load("chain1")
        assert loaded is not None
        assert loaded.tcode_sequence == ["SPRO", "MM03", "VA01"]

    def test_save_overrides_stale_sequence(self, store: RecipeStore):
        r = _multi("chain2", ["VA01"])
        r.tcode_sequence = ["WRONG"]  # 사용자가 잘못 채워도 save 시 덮어씀
        store.save(r)
        loaded = store.load("chain2")
        assert loaded is not None
        assert loaded.tcode_sequence == ["VA01"]

    def test_legacy_in_memory_fallback(self, store: RecipeStore, tmp_path: Path):
        # 저장하지 않고 로드만 한 레거시
        legacy = tmp_path / "recipes" / "leg.json"
        import json
        legacy.write_text(json.dumps({
            "id": "leg",
            "description": "legacy",
            "steps": [
                {"tool": "sap_run", "args": {"tcode": "ME21N"}, "hint": ""},
                {"tool": "sap_press", "args": {"element": "btn[0]"}, "hint": ""},
            ],
        }), encoding="utf-8")
        r = store.load("leg")
        assert r is not None
        assert r.tcode_sequence == []
        assert store._tcode_seq(r) == ("ME21N",)


class TestFindChain:
    def test_match_all_subset(self, store: RecipeStore):
        store.save(_multi("a", ["SPRO", "MM03", "VA01"]))
        store.save(_multi("b", ["SPRO", "MM03"]))
        store.save(_multi("c", ["VA01"]))

        results = store.find_chain(["SPRO", "MM03"], match="all")
        ids = [r.id for r in results]
        # a와 b 모두 SPRO, MM03 포함
        assert set(ids) == {"a", "b"}
        # 더 긴 시퀀스가 위
        assert ids[0] == "a"

    def test_match_any(self, store: RecipeStore):
        store.save(_multi("a", ["SPRO", "MM03"]))
        store.save(_multi("b", ["VA01"]))
        store.save(_multi("c", ["ME21N"]))

        results = store.find_chain(["MM03", "VA01"], match="any")
        ids = {r.id for r in results}
        assert ids == {"a", "b"}

    def test_disabled_excluded(self, store: RecipeStore):
        store.save(_multi("a", ["SPRO", "MM03"]))
        store.save(_multi("b", ["SPRO", "MM03"], disabled=True))

        results = store.find_chain(["SPRO"])
        assert {r.id for r in results} == {"a"}

    def test_empty_tcodes(self, store: RecipeStore):
        assert store.find_chain([]) == []
        assert store.find_chain(["", "  "]) == []

    def test_invalid_match(self, store: RecipeStore):
        with pytest.raises(ValueError):
            store.find_chain(["SPRO"], match="some")

    def test_case_insensitive(self, store: RecipeStore):
        store.save(_multi("a", ["SPRO", "MM03"]))
        results = store.find_chain(["spro", "mm03"])
        assert len(results) == 1
        assert results[0].id == "a"


class TestDispatchChain:
    def test_dispatch_chain_basic(self, store: RecipeStore, monkeypatch: pytest.MonkeyPatch):
        from sap_cli.dispatcher import _dispatch_recipe_chain

        store.save(_multi("a", ["SPRO", "MM03", "VA01"]))
        store.save(_multi("b", ["VA01"]))
        monkeypatch.setattr("sap_cli.dispatcher._get_recipe_store", lambda: store)

        result = _dispatch_recipe_chain({"tcodes": ["SPRO", "MM03"]})
        assert result["count"] == 1
        rec = result["recipes"][0]
        assert rec["id"] == "a"
        assert rec["cross_tcode"] is True
        assert rec["tcode_sequence"] == ["SPRO", "MM03", "VA01"]

    def test_dispatch_chain_match_any(self, store: RecipeStore, monkeypatch: pytest.MonkeyPatch):
        from sap_cli.dispatcher import _dispatch_recipe_chain

        store.save(_multi("a", ["SPRO"]))
        store.save(_multi("b", ["VA01"]))
        monkeypatch.setattr("sap_cli.dispatcher._get_recipe_store", lambda: store)

        result = _dispatch_recipe_chain({"tcodes": ["SPRO", "VA01"], "match": "any"})
        assert result["count"] == 2

    def test_dispatch_chain_invalid_match(self, store: RecipeStore, monkeypatch: pytest.MonkeyPatch):
        from sap_cli.dispatcher import _dispatch_recipe_chain
        monkeypatch.setattr("sap_cli.dispatcher._get_recipe_store", lambda: store)

        result = _dispatch_recipe_chain({"tcodes": ["SPRO"], "match": "bogus"})
        assert result.get("error") is True

    def test_dispatch_chain_empty(self, store: RecipeStore, monkeypatch: pytest.MonkeyPatch):
        from sap_cli.dispatcher import _dispatch_recipe_chain
        monkeypatch.setattr("sap_cli.dispatcher._get_recipe_store", lambda: store)

        result = _dispatch_recipe_chain({"tcodes": []})
        assert result.get("error") is True

    def test_dispatch_chain_csv_string(self, store: RecipeStore, monkeypatch: pytest.MonkeyPatch):
        from sap_cli.dispatcher import _dispatch_recipe_chain

        store.save(_multi("a", ["SPRO", "MM03"]))
        monkeypatch.setattr("sap_cli.dispatcher._get_recipe_store", lambda: store)

        result = _dispatch_recipe_chain({"tcodes": "SPRO, MM03"})
        assert result["count"] == 1


class TestRecipeHintsCrossTcode:
    def test_hints_show_cross_tcode_metadata(self, store: RecipeStore, monkeypatch: pytest.MonkeyPatch):
        from sap_cli.dispatcher import _collect_recipe_hints

        store.save(_multi("chain", ["SPRO", "MM03"], success=2))
        store.save(_multi("simple", ["SPRO"], success=2))
        monkeypatch.setattr("sap_cli.dispatcher._get_recipe_store", lambda: store)

        hints = _collect_recipe_hints("SPRO")
        assert hints is not None
        cands = {c["id"]: c for c in hints["candidates"]}
        assert cands["chain"].get("cross_tcode") is True
        assert cands["chain"]["tcode_sequence"] == ["SPRO", "MM03"]
        assert cands["simple"].get("cross_tcode") is None  # 단일 tcode는 메타 미포함
