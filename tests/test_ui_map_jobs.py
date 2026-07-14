from __future__ import annotations

import json

import pytest

from sap_cli.ui_map.jobs import BUDGET_STOP_REASONS, MapJobStore
from sap_cli.ui_map.schema import MapManifest, UiMap, UserInputRequest
from sap_cli.ui_map.traversal import TraversalCheckpoint, TraversalResult


def checkpoint(*, question: bool = False, dirty: bool = False) -> TraversalCheckpoint:
    item = UserInputRequest(
        question="회사 코드를 알려 주세요.",
        reason="필수 입력값이 없습니다.",
        required_fields=["company_code"],
        choices=["1000", "2000"],
        risk="low",
        resume_token="company-1",
        scope={"actions": ["select_tab"]},
    )
    return TraversalCheckpoint(
        ui_map=UiMap(
            manifest=MapManifest(map_id="DEV-100-ZMAP", tcode="ZMAP"),
            pending_interactions=[item] if question else [],
        ),
        needs_user_input=[item] if question else [],
        in_flight={"dirty": True, "candidate": {"action": "scroll_to"}}
        if dirty
        else {},
    )


def test_create_checkpoint_question_resume_and_finish(tmp_path):
    store = MapJobStore(tmp_path)
    created = store.create(
        tcode="zmap",
        map_id="DEV-100-ZMAP",
        job_id="job-1",
        owner_agent="planner-a",
        slot_id="ses1",
        traversal_config={"mode": "reversible"},
    )
    assert created.status == "queued"
    assert created.tcode == "ZMAP"
    assert created.traversal_config == {"mode": "reversible"}
    assert store.authorize(
        "job-1", owner_agent="planner-a", slot_id="ses1"
    ).job_id == "job-1"
    with pytest.raises(PermissionError, match="another agent"):
        store.authorize("job-1", owner_agent="planner-b", slot_id="ses1")
    with pytest.raises(PermissionError, match="another SAP slot"):
        store.authorize("job-1", owner_agent="planner-a", slot_id="ses0")

    paused = store.save_checkpoint("job-1", checkpoint(question=True))
    assert paused.status == "needs_user_input"
    assert paused.pending_questions[0]["required_fields"] == ["company_code"]

    resumed = store.record_resume_answers(
        "job-1", {"company-1": {"company_code": "SECRET-1000"}}
    )
    assert resumed.status == "queued"
    assert resumed.resume_provenance[-1] == {
        "resume_token": "company-1",
        "provided": True,
        "fields": ["company_code"],
        "value_stored": False,
    }
    raw = (tmp_path / "job-1.json").read_text(encoding="utf-8")
    assert "SECRET-1000" not in raw

    result = TraversalResult(
        ui_map=checkpoint().ui_map,
        checkpoint=checkpoint(),
        stop_reason="complete",
    )
    finished = store.finish("job-1", result)
    assert finished.status == "completed"
    with pytest.raises(ValueError, match="terminal"):
        store.save_checkpoint("job-1", checkpoint())


def test_resume_rejects_unknown_token_missing_fields_and_false_continue(tmp_path):
    store = MapJobStore(tmp_path)
    store.create(tcode="ZMAP", map_id="map", job_id="resume-contract")
    item = UserInputRequest(
        question="계속할까요?",
        current_state_id="A",
        required_fields=["continue"],
        resume_token="known-token",
    )
    store.save_checkpoint(
        "resume-contract",
        TraversalCheckpoint(
            ui_map=UiMap(
                manifest=MapManifest(map_id="map", tcode="ZMAP"),
                pending_interactions=[item],
            ),
            needs_user_input=[item],
        ),
    )

    with pytest.raises(ValueError, match="unknown resume token"):
        store.record_resume_answers(
            "resume-contract", {"other": {"continue": True}}
        )
    with pytest.raises(ValueError, match="missing required fields"):
        store.record_resume_answers("resume-contract", {"known-token": {}})
    with pytest.raises(ValueError, match="continue must be true"):
        store.record_resume_answers(
            "resume-contract", {"known-token": {"continue": False}}
        )

    assert store.record_resume_answers(
        "resume-contract", {"known-token": {"continue": True}}
    ).status == "queued"


def test_checkpoint_callback_and_dirty_crash_recovery(tmp_path):
    store = MapJobStore(tmp_path)
    store.create(tcode="ZMAP", map_id="map", job_id="job-dirty")
    store.checkpoint_callback("job-dirty")(checkpoint(dirty=True))

    recovered = MapJobStore(tmp_path).recover_interrupted()

    assert [item.job_id for item in recovered] == ["job-dirty"]
    assert recovered[0].status == "interrupted"
    assert recovered[0].checkpoint.in_flight["dirty"] is True
    assert recovered[0].stop_reason == "dirty_checkpoint_recovery_required"


def test_cancel_is_idempotent_and_prevents_later_updates(tmp_path):
    store = MapJobStore(tmp_path)
    store.create(tcode="ZMAP", map_id="map", job_id="job-cancel")
    assert store.request_cancel("job-cancel").status == "cancelled"
    assert store.request_cancel("job-cancel").status == "cancelled"
    with pytest.raises(ValueError, match="terminal"):
        store.fail("job-cancel", "must not overwrite")


@pytest.mark.parametrize("job_id", ["../escape", "a/b", "", ".hidden", "x" * 129])
def test_job_id_path_traversal_is_rejected(tmp_path, job_id):
    store = MapJobStore(tmp_path)
    with pytest.raises(ValueError, match="invalid"):
        store.create(tcode="ZMAP", map_id="map", job_id=job_id)


def test_corrupt_job_json_is_not_silently_accepted(tmp_path):
    store = MapJobStore(tmp_path)
    (tmp_path / "broken.json").write_text("{not-json", encoding="utf-8")
    with pytest.raises((ValueError, json.JSONDecodeError)):
        store.get("broken")


def test_list_filters_status_and_is_deterministic(tmp_path):
    store = MapJobStore(tmp_path)
    store.create(tcode="Z1", map_id="m1", job_id="one")
    store.create(tcode="Z2", map_id="m2", job_id="two")
    store.request_cancel("two")

    assert [item.job_id for item in store.list(status="cancelled")] == ["two"]
    assert {item.job_id for item in store.list()} == {"one", "two"}


def test_failure_persistence_never_stores_raw_exception_text(tmp_path):
    store = MapJobStore(tmp_path)
    store.create(tcode="Z1", map_id="m1", job_id="failed")

    record = store.fail("failed", "password=TOP-SECRET 고객 1000")

    assert record.error == "map_job_failed"
    assert "TOP-SECRET" not in (tmp_path / "failed.json").read_text()


@pytest.mark.parametrize("reason", sorted(BUDGET_STOP_REASONS))
def test_budget_stop_is_paused_and_can_be_requeued_with_validated_budget(
    tmp_path, reason
):
    store = MapJobStore(tmp_path)
    store.create(
        tcode="ZMAP",
        map_id="map",
        job_id="budget",
        traversal_config={"budget": {"max_transitions": 1}},
    )
    result = TraversalResult(
        ui_map=checkpoint().ui_map,
        checkpoint=checkpoint(),
        stop_reason=reason,
    )

    paused = store.finish("budget", result)

    assert paused.status == "paused"
    resumed = store.resume_paused(
        "budget", budget={"max_transitions": 2, "max_states": 250}
    )
    assert resumed.status == "queued"
    assert resumed.stop_reason == ""
    assert resumed.traversal_config["budget"]["max_transitions"] == 2
