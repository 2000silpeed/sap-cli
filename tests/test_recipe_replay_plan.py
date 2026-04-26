"""Phase E-2: Recipe replay precheck/dry-run/verify/report plan tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from sap_cli.recipe import Recipe, RecipeStep, RecipeStore


def _store(tmp_path: Path) -> RecipeStore:
    return RecipeStore(store_dir=tmp_path / "recipes")


def test_recipe_step_precondition_round_trips_in_execution_plan(tmp_path: Path):
    from sap_cli.dispatcher import _dispatch_recipe_show

    store = _store(tmp_path)
    store.save(Recipe(
        id="va01-precondition",
        description="수주 저장",
        steps=[
            RecipeStep(
                tool="sap_press",
                args={"element_id": "wnd[0]/tbar[0]/btn[11]"},
                hint="저장",
                precondition={"screen_contains": "Create Sales Order"},
            ),
        ],
    ))

    with patch("sap_cli.dispatcher._get_recipe_store", return_value=store):
        result = _dispatch_recipe_show({"recipe_id": "va01-precondition"})

    assert result["execution_plan"][0]["precondition"] == {
        "screen_contains": "Create Sales Order"
    }


def test_replay_plan_returns_full_validation_flow(tmp_path: Path):
    from sap_cli.dispatcher import _dispatch_recipe_replay_plan

    store = _store(tmp_path)
    store.save(Recipe(
        id="va01-replay",
        description="수주 생성 replay",
        context="VA01에서 필수 필드 입력 후 저장",
        success_count=3,
        steps=[
            RecipeStep(tool="sap_run", args={"tcode": "VA01"}, hint="VA01 실행"),
            RecipeStep(
                tool="sap_set_field",
                args={"element_id": "F1", "value": "1000"},
                precondition={"tcode": "VA01"},
            ),
            RecipeStep(tool="sap_press", args={"element_id": "btn[11]"}),
        ],
    ))

    with patch("sap_cli.dispatcher._get_recipe_store", return_value=store):
        result = _dispatch_recipe_replay_plan({
            "recipe_id": "va01-replay",
            "current_tcode": "VA01",
            "current_screen": "0101",
        })

    assert result["flow"] == ["precheck", "dry_run_plan", "execute", "verify", "report"]
    assert result["precheck"]["status"] == "ready"
    assert result["precheck"]["required_start_tcode"] == "VA01"
    assert result["precheck"]["checks"][0]["status"] == "ok"
    assert result["dry_run_plan"][1]["precondition"] == {"tcode": "VA01"}
    assert result["execute"]["allowed"] is True
    assert result["verify"]["required_observations"] == [
        "sap_status",
        "sap_modal",
        "sap_screen_summary",
    ]
    assert result["report"]["on_success"]["tool"] == "sap_recipe_success"
    assert result["report"]["on_failure"]["tool"] == "sap_recipe_failure"
    assert result["report"]["on_failure"]["auto_call_candidate"] is True


def test_replay_plan_blocks_disabled_recipe_by_default(tmp_path: Path):
    from sap_cli.dispatcher import _dispatch_recipe_replay_plan

    store = _store(tmp_path)
    store.save(Recipe(
        id="disabled",
        description="disabled",
        disabled=True,
        disabled_reason="broken",
        steps=[RecipeStep(tool="sap_run", args={"tcode": "MM03"})],
    ))

    with patch("sap_cli.dispatcher._get_recipe_store", return_value=store):
        blocked = _dispatch_recipe_replay_plan({"recipe_id": "disabled"})
        allowed = _dispatch_recipe_replay_plan({
            "recipe_id": "disabled",
            "allow_disabled": True,
        })

    assert blocked["precheck"]["status"] == "blocked"
    assert blocked["execute"]["allowed"] is False
    assert allowed["precheck"]["status"] == "needs_review"
    assert allowed["execute"]["allowed"] is True


def test_replay_plan_reports_start_tcode_mismatch(tmp_path: Path):
    from sap_cli.dispatcher import _dispatch_recipe_replay_plan

    store = _store(tmp_path)
    store.save(Recipe(
        id="no-nav",
        description="현재 화면 기반 작업",
        steps=[
            RecipeStep(tool="sap_set_field", args={"element_id": "F1", "value": "x"}),
            RecipeStep(tool="sap_command", args={"cmd": "/nVA01"}),
        ],
    ))

    with patch("sap_cli.dispatcher._get_recipe_store", return_value=store):
        result = _dispatch_recipe_replay_plan({
            "recipe_id": "no-nav",
            "current_tcode": "MM03",
        })

    assert result["precheck"]["status"] == "needs_review"
    assert result["precheck"]["issues"][0]["code"] == "start_tcode_mismatch"
    assert result["precheck"]["issues"][0]["expected"] == "VA01"
    assert result["precheck"]["issues"][0]["actual"] == "MM03"
