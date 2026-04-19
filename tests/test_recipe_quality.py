"""Phase 18-2: 레시피 품질 관리 — 실패 카운트 + 자동 비활성화 테스트."""

from __future__ import annotations

from pathlib import Path

import pytest

from sap_cli.recipe import (
    AUTO_DISABLE_FAILURE_RATE,
    AUTO_DISABLE_MIN_TOTAL,
    Recipe,
    RecipeStep,
    RecipeStore,
    should_auto_disable,
)


@pytest.fixture
def store(tmp_path: Path) -> RecipeStore:
    return RecipeStore(store_dir=tmp_path / "recipes")


def _save(store: RecipeStore, rid: str, *, success: int = 0, failure: int = 0,
          disabled: bool = False, tcode: str = "VA01") -> Recipe:
    r = Recipe(
        id=rid,
        description=rid,
        keywords=[tcode.lower()],
        steps=[RecipeStep(tool="sap_run", args={"tcode": tcode})],
        success_count=success,
        failure_count=failure,
        disabled=disabled,
    )
    store.save(r)
    return r


class TestSchema:
    def test_new_fields_default(self):
        r = Recipe(id="x", description="x")
        assert r.failure_count == 0
        assert r.disabled is False
        assert r.disabled_reason == ""
        assert r.last_failed_at == 0.0

    def test_legacy_json_loads(self, store: RecipeStore, tmp_path: Path):
        legacy = tmp_path / "recipes" / "legacy.json"
        legacy.write_text(
            '{"id":"legacy","description":"old","steps":[]}',
            encoding="utf-8",
        )
        r = store.load("legacy")
        assert r is not None
        assert r.failure_count == 0
        assert r.disabled is False


class TestAutoDisableThreshold:
    def test_low_total_no_disable(self):
        assert should_auto_disable(1, 1) is False
        assert should_auto_disable(0, 2) is False

    def test_above_threshold_disables(self):
        assert should_auto_disable(0, 3) is True
        assert should_auto_disable(1, 3) is True
        assert should_auto_disable(2, 2) is True

    def test_below_threshold_keeps(self):
        assert should_auto_disable(3, 1) is False
        assert should_auto_disable(5, 2) is False

    def test_constants_exposed(self):
        assert AUTO_DISABLE_MIN_TOTAL == 3
        assert 0.0 < AUTO_DISABLE_FAILURE_RATE <= 1.0


class TestReportFailure:
    def test_increments_failure(self, store: RecipeStore):
        _save(store, "va01-a", success=5)
        r = store.report_failure("va01-a")
        assert r is not None
        assert r.failure_count == 1
        assert r.last_failed_at > 0
        assert r.disabled is False  # success=5, failure=1 → 안전

    def test_unknown_returns_none(self, store: RecipeStore):
        assert store.report_failure("nope") is None

    def test_auto_disables_at_threshold(self, store: RecipeStore):
        _save(store, "va01-b", success=0, failure=2)
        r = store.report_failure("va01-b", reason="화면 변경")
        assert r is not None
        assert r.failure_count == 3
        assert r.disabled is True
        assert "auto:" in r.disabled_reason
        assert "화면 변경" in r.disabled_reason

    def test_does_not_redisable(self, store: RecipeStore):
        _save(store, "va01-c", success=0, failure=5, disabled=True)
        r = store.load("va01-c")
        assert r is not None
        r.disabled_reason = "manual: keep"
        store.save(r)
        store.report_failure("va01-c")
        r2 = store.load("va01-c")
        assert r2.disabled_reason == "manual: keep"


class TestEnable:
    def test_enable_resets(self, store: RecipeStore):
        _save(store, "va01-d", success=1, failure=5, disabled=True)
        r = store.enable("va01-d")
        assert r is not None
        assert r.disabled is False
        assert r.disabled_reason == ""
        assert r.failure_count == 0

    def test_enable_keep_failures(self, store: RecipeStore):
        _save(store, "va01-e", success=1, failure=5, disabled=True)
        r = store.enable("va01-e", reset_failure_count=False)
        assert r is not None
        assert r.disabled is False
        assert r.failure_count == 5


class TestSearchExcludesDisabled:
    def test_search_skips_disabled(self, store: RecipeStore):
        _save(store, "va01-active", success=1)
        _save(store, "va01-broken", disabled=True)
        results = store.search("va01")
        ids = {r.id for r in results}
        assert "va01-active" in ids
        assert "va01-broken" not in ids

    def test_search_include_disabled(self, store: RecipeStore):
        _save(store, "va01-broken", disabled=True)
        results = store.search("va01", include_disabled=True)
        assert any(r.id == "va01-broken" for r in results)

    def test_find_by_tcode_skips_disabled(self, store: RecipeStore):
        _save(store, "good", tcode="MM03")
        _save(store, "bad", tcode="MM03", disabled=True)
        results = store.find_by_tcode("MM03")
        assert {r.id for r in results} == {"good"}

    def test_query_for_ai_skips_disabled(self, store: RecipeStore):
        _save(store, "good", tcode="ME21N")
        _save(store, "bad", tcode="ME21N", disabled=True)
        results = store.query_for_ai(tcode="ME21N")
        assert {r.id for _, r in results} == {"good"}

    def test_find_similar_skips_disabled(self, store: RecipeStore):
        _save(store, "good", tcode="VA01")
        _save(store, "bad", tcode="VA01", disabled=True)
        results = store.find_similar(
            description="va01",
            context="",
            keywords=["va01"],
            steps=[RecipeStep(tool="sap_run", args={"tcode": "VA01"})],
        )
        assert {r.id for _, r in results} == {"good"}


class TestDispatchFailure:
    def test_dispatch_failure(self, store: RecipeStore, monkeypatch: pytest.MonkeyPatch):
        from sap_cli.dispatcher import _dispatch_recipe_failure

        _save(store, "va01-x", success=0, failure=2)
        monkeypatch.setattr("sap_cli.dispatcher._get_recipe_store", lambda: store)

        result = _dispatch_recipe_failure({"recipe_id": "va01-x", "reason": "btn 변경"})

        assert result["disabled"] is True
        assert result["failure_count"] == 3
        assert "btn 변경" in result["disabled_reason"]

    def test_dispatch_enable(self, store: RecipeStore, monkeypatch: pytest.MonkeyPatch):
        from sap_cli.dispatcher import _dispatch_recipe_enable

        _save(store, "va01-y", failure=5, disabled=True)
        monkeypatch.setattr("sap_cli.dispatcher._get_recipe_store", lambda: store)

        result = _dispatch_recipe_enable({"recipe_id": "va01-y"})

        assert result["disabled"] is False
        assert result["failure_count"] == 0

    def test_dispatch_failure_unknown(self, store: RecipeStore, monkeypatch: pytest.MonkeyPatch):
        from sap_cli.dispatcher import _dispatch_recipe_failure

        monkeypatch.setattr("sap_cli.dispatcher._get_recipe_store", lambda: store)

        result = _dispatch_recipe_failure({"recipe_id": "nope"})

        assert result.get("error") is True
