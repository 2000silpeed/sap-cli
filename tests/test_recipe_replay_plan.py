"""Phase E-2: Recipe replay precheck/dry-run/verify/report plan tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from sap_cli.dispatcher import DispatchContext, dispatch_command
from sap_cli.recipe import Recipe, RecipeStep, RecipeStore


def _store(tmp_path: Path) -> RecipeStore:
    return RecipeStore(store_dir=tmp_path / "recipes")


class _Dump:
    def __init__(self, data):
        self._data = data

    def model_dump(self, mode="json"):
        return self._data

    def __getattr__(self, name):
        try:
            return self._data[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


class _FakeScreen:
    transaction = "VA01"
    screen_number = "0101"
    program = "SAPMV45A"
    title = "Create Sales Order"
    fields = []
    buttons = []
    tabs = []
    tables = []

    def get_changeable_fields(self):
        return []

    def get_available_actions(self):
        return []


class _ReplaySap:
    def __init__(self, *, status_type="S", modal_text=None):
        self.transaction = "SESSION_MANAGER"
        self.screen_number = "0000"
        self.status_type = status_type
        self.modal_text = modal_text
        self.calls = []

    def run_transaction(self, tcode):
        self.calls.append(("run", tcode))
        self.transaction = tcode
        self.screen_number = "0101"

    def set_field(self, element_id, value):
        self.calls.append(("set", element_id, value))

    def get_field(self, element_id):
        return ""

    def press(self, element_id):
        self.calls.append(("press", element_id))

    def get_session_info(self):
        return _Dump({
            "transaction": self.transaction,
            "screen_number": self.screen_number,
            "session_id": "ses[0]",
            "system_name": "TST",
            "client": "100",
        })

    def get_status(self):
        return _Dump({"type": self.status_type, "message": "status message"})

    def has_modal(self):
        return self.modal_text is not None

    def get_modal_text(self):
        return self.modal_text or ""

    def get_screen(self):
        screen = _FakeScreen()
        screen.transaction = self.transaction
        screen.screen_number = self.screen_number
        return screen


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


def test_replay_execute_runs_steps_and_auto_reports_success(tmp_path: Path):
    store = _store(tmp_path)
    store.save(Recipe(
        id="va01-execute",
        description="수주 생성 execute",
        steps=[
            RecipeStep(tool="sap_run", args={"tcode": "VA01"}),
            RecipeStep(tool="sap_set_field", args={"element_id": "F1", "value": "1000"}),
            RecipeStep(tool="sap_press", args={"element_id": "B1"}),
        ],
    ))
    sap = _ReplaySap()

    with (
        patch("sap_cli.dispatcher._get_recipe_store", return_value=store),
        patch("sap_cli.dispatcher._get_sap", return_value=sap),
    ):
        result = dispatch_command(
            "recipe_replay_execute",
            {"recipe_id": "va01-execute", "current_tcode": "VA01"},
            DispatchContext(source="mcp", enforce_auth=False),
        )

    assert result["status"] == "success"
    assert result["verify"]["status"] == "ok"
    assert result["execution_ms"] >= 0
    assert result["report_called"] is True
    assert result["report"]["success_count"] == 1
    assert result["replay_metrics"]["replay_execution_count"] == 1
    assert result["replay_metrics"]["replay_last_status"] == "success"
    assert result["replay_metrics"]["replay_system_client_stats"]["TST/100"]["success"] == 1
    assert all(step["execution_ms"] >= 0 for step in result["executed_steps"])
    assert [call[0] for call in sap.calls] == ["run", "set", "press"]
    saved = store.load("va01-execute")
    assert saved is not None
    assert saved.replay_execution_count == 1
    assert saved.replay_average_execution_ms >= 0
    assert saved.replay_system_client_stats["TST/100"]["success"] == 1


def test_replay_execute_verify_failure_auto_reports_failure(tmp_path: Path):
    store = _store(tmp_path)
    store.save(Recipe(
        id="va01-fail",
        description="수주 생성 실패",
        success_count=2,
        steps=[RecipeStep(tool="sap_run", args={"tcode": "VA01"})],
    ))
    sap = _ReplaySap(status_type="E")

    with (
        patch("sap_cli.dispatcher._get_recipe_store", return_value=store),
        patch("sap_cli.dispatcher._get_sap", return_value=sap),
    ):
        result = dispatch_command(
            "recipe_replay_execute",
            {"recipe_id": "va01-fail", "current_tcode": "VA01"},
            DispatchContext(source="mcp", enforce_auth=False),
        )

    assert result["status"] == "failed"
    assert result["stage"] == "verify"
    assert result["verify"]["issues"][0]["code"] == "sap_status_error"
    assert result["report_called"] is True
    assert result["report"]["failure_count"] == 1
    assert result["replay_metrics"]["replay_last_status"] == "failed"
    assert result["replay_metrics"]["replay_last_failure_type"] == "sap_status_error"
    assert result["replay_metrics"]["replay_failure_type_counts"]["sap_status_error"] == 1
    assert result["replay_metrics"]["replay_system_client_stats"]["TST/100"]["failed"] == 1
    saved = store.load("va01-fail")
    assert saved is not None
    assert saved.replay_failure_type_counts["sap_status_error"] == 1
    assert saved.replay_system_client_stats["TST/100"]["last_failure_type"] == "sap_status_error"


def test_replay_execute_blocks_non_sap_recipe_management_step(tmp_path: Path):
    store = _store(tmp_path)
    store.save(Recipe(
        id="unsafe",
        description="unsafe",
        steps=[RecipeStep(tool="sap_recipe_delete", args={"recipe_id": "other"})],
    ))
    sap = _ReplaySap()

    with (
        patch("sap_cli.dispatcher._get_recipe_store", return_value=store),
        patch("sap_cli.dispatcher._get_sap", return_value=sap),
    ):
        result = dispatch_command(
            "recipe_replay_execute",
            {"recipe_id": "unsafe"},
            DispatchContext(source="mcp", enforce_auth=False),
        )

    assert result["status"] == "blocked"
    assert result["stage"] == "preflight"
    assert result["preflight"]["issues"][0]["code"] == "disallowed_tool"
    assert result["report_called"] is False
    assert sap.calls == []
