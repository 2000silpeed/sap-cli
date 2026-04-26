"""Phase 18-2: 레시피 품질 관리 — 실패 카운트 + 자동 비활성화 테스트."""

from __future__ import annotations

from pathlib import Path
import time

import pytest

from sap_cli.recipe import (
    AUTO_DISABLE_FAILURE_RATE,
    AUTO_DISABLE_MIN_TOTAL,
    Recipe,
    RecipeStep,
    RecipeStore,
    efficiency_score,
    replay_quality_summary,
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
        assert r.last_succeeded_at == 0.0
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
        assert r.last_succeeded_at == 0.0


class TestQualityScore:
    def test_success_rate_boosts_more_reliable_recipe(self, store: RecipeStore):
        reliable = _save(store, "va01-reliable", success=4, failure=0)
        flaky = _save(store, "va01-flaky", success=10, failure=10)

        assert efficiency_score(reliable) > efficiency_score(flaky)

    def test_recent_failure_penalizes_recipe(self, store: RecipeStore):
        clean = _save(store, "va01-clean", success=5, failure=0)
        recently_failed = _save(store, "va01-recent-fail", success=5, failure=1)
        recently_failed.last_failed_at = time.time()
        store.save(recently_failed)

        assert efficiency_score(clean) > efficiency_score(recently_failed)

    def test_disabled_penalty_applies_when_included(self, store: RecipeStore):
        active = _save(store, "va01-active", success=1, failure=0)
        disabled = _save(store, "va01-disabled", success=10, failure=0, disabled=True)

        assert efficiency_score(active) > efficiency_score(disabled)

    def test_increment_success_records_last_succeeded_at(self, store: RecipeStore):
        _save(store, "va01-success", success=0)

        store.increment_success("va01-success")

        r = store.load("va01-success")
        assert r is not None
        assert r.success_count == 1
        assert r.last_succeeded_at > 0

    def test_query_tiebreak_uses_quality_score(self, store: RecipeStore):
        _save(store, "va01-flaky", success=10, failure=10)
        _save(store, "va01-reliable", success=4, failure=0)

        results = store.query_for_ai(tcode="VA01")

        assert [r.id for _, r in results][:2] == ["va01-reliable", "va01-flaky"]

    def test_find_by_tcode_orders_by_quality_score(self, store: RecipeStore):
        _save(store, "va01-flaky", success=10, failure=10)
        _save(store, "va01-reliable", success=4, failure=0)

        results = store.find_by_tcode("VA01")

        assert [r.id for r in results][:2] == ["va01-reliable", "va01-flaky"]

    def test_replay_success_and_speed_boost_quality_score(self, store: RecipeStore):
        stable = _save(store, "va01-replay-stable", success=3, failure=0)
        slow_failed = _save(store, "va01-replay-slow-failed", success=3, failure=0)

        store.record_replay_execution(
            stable.id,
            status="success",
            execution_ms=1_000,
            system_name="TST",
            client="100",
        )
        store.record_replay_execution(
            slow_failed.id,
            status="failed",
            execution_ms=60_000,
            failure_type="sap_status_error",
            system_name="TST",
            client="100",
        )

        stable = store.load(stable.id)
        slow_failed = store.load(slow_failed.id)
        assert stable is not None
        assert slow_failed is not None
        assert replay_quality_summary(stable)["success_rate"] == 1.0
        assert replay_quality_summary(slow_failed)["success_rate"] == 0.0
        assert efficiency_score(stable) > efficiency_score(slow_failed)

    def test_query_tiebreak_uses_replay_quality_metadata(self, store: RecipeStore):
        _save(store, "va01-replay-failed", success=3, failure=0)
        _save(store, "va01-replay-success", success=3, failure=0)
        store.record_replay_execution(
            "va01-replay-failed",
            status="failed",
            execution_ms=60_000,
            failure_type="unexpected_modal",
            system_name="TST",
            client="100",
        )
        store.record_replay_execution(
            "va01-replay-success",
            status="success",
            execution_ms=1_000,
            system_name="TST",
            client="100",
        )

        results = store.query_for_ai(tcode="VA01")

        assert [r.id for _, r in results][:2] == [
            "va01-replay-success",
            "va01-replay-failed",
        ]


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
