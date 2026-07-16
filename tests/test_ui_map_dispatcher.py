from types import SimpleNamespace

import pytest

from sap_cli.dispatchers.ui_maps import dispatch_ui_map_command
from sap_cli.ui_map.jobs import MapJobStore
from sap_cli.ui_map.schema import (
    CollectionDescriptor,
    MapManifest,
    SideEffectClass,
    Transition,
    UiComponent,
    UiMap,
    UiState,
    UserInputRequest,
)
from sap_cli.ui_map.store import UiMapStore
from sap_cli.ui_map.traversal import TraversalCheckpoint
from sap_cli.ui_map.traversal import TraversalResult


def graph():
    return UiMap(
        manifest=MapManifest(
            map_id="DEV-100-ZMAP", tcode="ZMAP", system_id="DEV", client="100"
        ),
        states={
            "a": UiState(
                state_id="a",
                tcode="ZMAP",
                title="Start",
                components=[
                    UiComponent(
                        logical_id="secret",
                        sap_id="wnd[0]/usr/pwdSECRET",
                        type="GuiPasswordField",
                        role="password",
                        name="PASSWORD",
                        text="DO-NOT-RETURN",
                        sensitive=True,
                    )
                ],
            ),
            "b": UiState(state_id="b", tcode="ZMAP", title="Details"),
        },
        transitions=[
            Transition(
                transition_id="ab",
                from_state_id="a",
                to_state_id="b",
                action="select_tab",
                target_logical_id="details",
                observed_params={"business_value": "DO-NOT-RETURN"},
                evidence=[{"value": "DO-NOT-RETURN"}],
                side_effect=SideEffectClass.REVERSIBLE,
                reversible=True,
            )
        ],
    )


@pytest.fixture
def stores(tmp_path):
    maps = UiMapStore(tmp_path / "maps")
    jobs = MapJobStore(tmp_path / "jobs")
    maps.save(graph())
    return maps, jobs


def call(command, args, stores, scheduler=None, *, agent="agent-a", slot="ses0"):
    maps, jobs = stores
    return dispatch_ui_map_command(
        command,
        args,
        SimpleNamespace(agent_name=agent, source="test", slot_id=slot),
        get_map_store=lambda: maps,
        get_job_store=lambda: jobs,
        get_scheduler=(lambda: scheduler) if scheduler is not None else None,
    )


def scope(**extra):
    return {
        "map_id": "DEV-100-ZMAP",
        "tcode": "ZMAP",
        "system_id": "DEV",
        "client": "100",
        **extra,
    }


def test_list_search_and_path_return_safe_summaries(stores):
    assert call("map_list", {"tcode": "zmap"}, stores)["count"] == 1
    searched = call("map_search", scope(query="PASSWORD"), stores)
    assert searched["results"][0]["summary"]["logical_id"] == "secret"
    assert "DO-NOT-RETURN" not in str(searched)

    path = call(
        "map_path",
        scope(from_state_id="a", target_type="state", target="b"),
        stores,
    )
    assert path["paths"][0][0]["transition_id"] == "ab"
    assert "observed_params" not in str(path)
    assert "DO-NOT-RETURN" not in str(path)

    report = call("map_report", scope(), stores)
    assert report["counts"]["states"] == 2
    assert report["counts"]["transitions"] == 1
    assert report["counts"]["components"] == 1
    assert len(report["map_fingerprint"]) == 64
    assert report["coverage_gate"]["complete"] is False
    assert report["coverage_gate"]["manifest_completeness"] == 0.0
    assert "DO-NOT-RETURN" not in str(report)

    view = call("map_view", scope(), stores)
    assert view["summary"] == {
        "states": 2,
        "connections": 1,
        "unresolved_connections": 0,
        "pending_interactions": 0,
    }
    assert view["outline"][0]["next"][0] == {
        "action": "select_tab",
        "target_logical_id": "details",
        "to_node_id": "S2",
        "status": "connected",
    }
    assert "S1 -->|select_tab · details| S2" in view["mermaid"]
    assert "DO-NOT-RETURN" not in str(view)


def test_report_counts_pending_actionable_and_partial_collection_gaps(tmp_path):
    maps = UiMapStore(tmp_path / "maps")
    jobs = MapJobStore(tmp_path / "jobs")
    ui_map = graph()
    ui_map.states["a"].collections = [
        CollectionDescriptor(
            component_logical_id="grid",
            kind="alv",
            coverage={
                "columns": "partial",
                "context_menu_actions": "not_observed_until_opened",
            },
        )
    ]
    ui_map.pending_interactions = [
        UserInputRequest(
            question="구조적 분기를 직접 확인해 주세요.",
            reason="unsafe_actionable_branches_require_user_guidance",
            current_state_id="a",
            resume_token="gap-a",
            scope={
                "branch_count": 7,
                "coverage_branches": [
                    {"kind": "GuiButton", "structural_id": "button-a"}
                ],
            },
        )
    ]
    maps.save(ui_map)

    report = call("map_report", scope(), (maps, jobs))

    assert report["counts"]["pending_actionable_branches"] == 7
    assert report["counts"]["pending_actionable_states"] == 1
    assert report["counts"]["unobserved_actionable_branches"] == 1
    assert report["counts"]["partial_collections"] == 1
    assert report["coverage_gate"]["complete"] is False
    assert report["coverage_gate"]["pending_actionable_branches"] == 7


def test_job_access_is_owner_and_slot_scoped_and_cancel_is_idempotent(stores):
    _, jobs = stores
    jobs.create(
        tcode="ZMAP",
        map_id="map",
        job_id="job-1",
        owner_agent="agent-a",
        slot_id="ses0",
    )
    assert call("map_job_status", {"job_id": "job-1"}, stores)["job"]["status"] == "queued"
    assert call("map_job_cancel", {"job_id": "job-1"}, stores)["job"]["status"] == "cancelled"
    assert call("map_job_cancel", {"job_id": "job-1"}, stores)["job"]["status"] == "cancelled"
    with pytest.raises(PermissionError):
        call("map_job_status", {"job_id": "job-1"}, stores, agent="agent-b")
    with pytest.raises(PermissionError):
        call("map_job_status", {"job_id": "job-1"}, stores, slot="ses1")


def test_start_and_resume_delegate_without_echoing_answer(stores):
    _, jobs = stores

    class Scheduler:
        def start(self, **kwargs):
            return jobs.create(
                tcode=kwargs["tcode"],
                map_id=kwargs["map_id"] or "generated",
                job_id="job-start",
                owner_agent=kwargs["owner_agent"],
                slot_id=kwargs["slot_id"],
            )

        def resume(self, job_id, answers, **kwargs):
            assert answers == {"token": {"company_code": "SECRET"}}
            assert kwargs == {
                "budget": {},
                "owner_agent": "agent-a",
                "slot_id": "ses0",
            }
            return jobs.record_resume_answers(job_id, answers)

    scheduler = Scheduler()
    started = call(
        "map_traverse_start", {"tcode": "ZMAP", "mode": "passive"}, stores, scheduler
    )
    jobs.save_checkpoint(
        "job-start",
        TraversalCheckpoint(
            ui_map=graph(),
            needs_user_input=[
                UserInputRequest(question="회사 코드?", resume_token="token")
            ],
        ),
    )
    resumed = call(
        "map_traverse_resume",
        {"job_id": "job-start", "answers": {"token": {"company_code": "SECRET"}}},
        stores,
        scheduler,
    )
    assert started["job"]["status"] == "queued"
    assert resumed["job"]["status"] == "queued"
    assert "SECRET" not in str(resumed)


def test_augment_start_loads_existing_map_and_creates_user_checkpoint(stores):
    _, jobs = stores

    class Scheduler:
        def augment_start(self, **kwargs):
            ui_map = kwargs.pop("ui_map")
            assert ui_map.manifest.map_id == "DEV-100-ZMAP"
            assert kwargs == {
                "from_state_id": "a",
                "request": "누락된 승인 전 상세 화면을 추가해 주세요.",
                "scope": {"target_logical_ids": ["details"]},
                "owner_agent": "agent-a",
                "slot_id": "ses0",
            }
            record = jobs.create(
                tcode=ui_map.manifest.tcode,
                map_id=ui_map.manifest.map_id,
                job_id="augment-start",
                owner_agent="agent-a",
                slot_id="ses0",
            )
            question = UserInputRequest(
                question=kwargs["request"],
                current_state_id="a",
                resume_token="augment-token",
            )
            return jobs.save_checkpoint(
                record.job_id,
                TraversalCheckpoint(
                    ui_map=ui_map,
                    frontier=["a"],
                    needs_user_input=[question],
                ),
            )

    result = call(
        "map_augment_start",
        scope(
            from_state_id="a",
            request="누락된 승인 전 상세 화면을 추가해 주세요.",
            scope={"target_logical_ids": ["details"]},
        ),
        stores,
        Scheduler(),
    )

    assert result["job"]["status"] == "needs_user_input"
    assert result["job"]["pending_questions"][0]["resume_token"] == "augment-token"


def test_traversal_navigation_preflights_tcode_before_scheduler_start(stores):
    calls = []

    class Scheduler:
        def start(self, **kwargs):
            calls.append(kwargs)

    with pytest.raises(PermissionError, match="transaction authorization denied"):
        dispatch_ui_map_command(
            "map_traverse_start",
            {"tcode": "SU01", "navigate": True},
            SimpleNamespace(agent_name="agent-a", source="test", slot_id="ses0"),
            get_map_store=lambda: stores[0],
            get_job_store=lambda: stores[1],
            get_scheduler=lambda: Scheduler(),
            preflight_tcode=lambda _tcode: SimpleNamespace(
                allowed=False, approval_required=False
            ),
        )
    assert calls == []


def test_traversal_forwards_exact_allowlisted_branch_rules(stores):
    calls = []
    jobs = stores[1]

    class Scheduler:
        def start(self, **kwargs):
            calls.append(kwargs)
            return jobs.create(
                job_id="branch-job",
                tcode=kwargs["tcode"],
                map_id=kwargs["map_id"],
                owner_agent=kwargs["owner_agent"],
                slot_id=kwargs["slot_id"],
                traversal_config={},
            )

    branch = {
        "action": "press",
        "source_state_id": "a",
        "target_logical_id": "detail",
        "target_sap_id": "wnd[0]/usr/btnDETAIL",
        "component_type": "GuiButton",
        "restore_vkey": 3,
    }
    call(
        "map_traverse_start",
        {
            "tcode": "ZMAP",
            "map_id": "map",
            "mode": "allowlisted",
            "allowed_branches": [branch],
            "test_client_confirmed": True,
            "navigate": False,
        },
        stores,
        Scheduler(),
    )

    assert calls[0]["allowed_branches"] == [branch]


def test_paused_job_resume_accepts_larger_budget_and_reports_usage(stores):
    _, jobs = stores
    record = jobs.create(
        tcode="ZMAP",
        map_id="map",
        job_id="budget-paused",
        owner_agent="agent-a",
        slot_id="ses0",
        traversal_config={"budget": {"max_transitions": 1}},
    )
    checkpoint = TraversalCheckpoint(ui_map=graph(), elapsed_seconds=12.5)
    jobs.finish(
        record.job_id,
        TraversalResult(
            ui_map=checkpoint.ui_map,
            checkpoint=checkpoint,
            stop_reason="max_transitions",
        ),
    )

    class Scheduler:
        def resume(self, job_id, answers, **kwargs):
            assert answers == {}
            assert kwargs["budget"] == {"max_transitions": 2}
            assert kwargs["owner_agent"] == "agent-a"
            assert kwargs["slot_id"] == "ses0"
            return jobs.resume_paused(
                job_id, budget={"max_transitions": 2}
            )

    resumed = call(
        "map_traverse_resume",
        {"job_id": "budget-paused", "budget": {"max_transitions": 2}},
        stores,
        Scheduler(),
    )

    assert resumed["job"]["status"] == "queued"
    assert resumed["job"]["budget"]["max_transitions"] == 2
    assert resumed["job"]["usage"]["elapsed_seconds"] == 12.5
