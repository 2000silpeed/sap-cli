from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from sap_cli.dispatchers.ui_maps import dispatch_ui_map_command
from sap_cli.ui_map.jobs import MapJobStore
from sap_cli.ui_map.scheduler import UiMapScheduler
from sap_cli.ui_map.sap_traversal import SapTraversalAdapter
from sap_cli.ui_map.store import UiMapStore
from sap_cli.ui_map.schema import (
    CollectionDescriptor,
    MapManifest,
    SideEffectClass,
    SemanticLink,
    TraversalBudget,
    TraversalBranchRule,
    UiMap,
    UiComponent,
    UiState,
    UserInputRequest,
)
from sap_cli.ui_map.traversal import TraversalCandidate, TraversalCheckpoint
from sap_cli.jobs import DaemonJobCoordinator
from sap_cli.planning.compiler import PlanCompiler


class FakeSession:
    def __init__(self) -> None:
        self.screen = 100
        self.actions = 0


class FakeWrapper:
    def __init__(self) -> None:
        self.session = FakeSession()
        self.navigations = 0

    def run_transaction(self, tcode: str) -> None:
        assert tcode == "ZMAP"
        self.navigations += 1

    def get_session_info(self):
        return SimpleNamespace(
            system_name="", client="", language="", transaction="ZMAP"
        )


class FakeScanner:
    def scan(self, session: FakeSession, *, tcode: str = "") -> UiState:
        return UiState(
            tcode=tcode,
            program="ZPROGRAM",
            screen_number=session.screen,
            title=f"screen-{session.screen}",
        ).ensure_state_id()


class FakeAdapter:
    def __init__(self, session: FakeSession) -> None:
        self.session = session

    def discover(self, state: UiState):
        if state.screen_number < 300:
            yield TraversalCandidate(
                action="inspect",
                params={"next": state.screen_number + 100},
                side_effect=SideEffectClass.READ_ONLY,
                reversible=True,
            )

    def execute(self, candidate: TraversalCandidate) -> dict[str, Any]:
        self.session.actions += 1
        self.session.screen = int(candidate.params["next"])
        return {"observed": True}

    def restore(self, baseline: UiState) -> None:
        self.session.screen = baseline.screen_number

    def reach(self, state: UiState) -> bool:
        self.session.screen = state.screen_number
        return True

    def apply_user_input(self, item: UserInputRequest, answer: Any) -> bool:
        del item
        return answer is not None


def scheduler(tmp_path, wrapper: FakeWrapper):
    calls: list[str] = []

    def get_wrapper(slot_id: str) -> FakeWrapper:
        calls.append(slot_id)
        return wrapper

    value = UiMapScheduler(
        MapJobStore(tmp_path),
        get_wrapper,
        scanner=FakeScanner(),
        adapter_factory=FakeAdapter,
    )
    return value, calls


def test_start_is_immediate_and_does_not_touch_com(tmp_path) -> None:
    wrapper = FakeWrapper()
    runner, wrapper_calls = scheduler(tmp_path, wrapper)

    record = runner.start(
        tcode="zmap",
        map_id="DEV-100-ZMAP",
        owner_agent="mapper",
        slot_id="ses7",
        mode="reversible",
        test_client_confirmed=True,
        job_id="job-start",
    )

    assert record.status == "queued"
    assert record.traversal_config["navigate_pending"] is True
    assert record.traversal_config["mode"] == "reversible"
    assert record.owner_agent == "mapper"
    assert record.slot_id == "ses7"
    assert wrapper_calls == []
    assert wrapper.navigations == 0
    assert wrapper.session.actions == 0


def test_allowlisted_exact_branch_is_validated_and_persisted_for_restart(tmp_path) -> None:
    wrapper = FakeWrapper()
    runner, wrapper_calls = scheduler(tmp_path, wrapper)
    rule = TraversalBranchRule(
        action="press",
        source_state_id="state-a",
        target_logical_id="detail",
        target_sap_id="wnd[0]/usr/btnDETAIL",
        component_type="GuiButton",
        restore_vkey=3,
        expected_screen_number=200,
    )

    record = runner.start(
        tcode="ZMAP",
        map_id="map",
        mode="allowlisted",
        allowed_branches=[rule.model_dump(mode="json")],
        test_client_confirmed=True,
        job_id="exact-branch",
    )

    assert record.status == "queued"
    assert record.traversal_config["allowed_branches"] == [
        rule.model_dump(mode="json")
    ]
    assert wrapper_calls == []

    with pytest.raises(ValueError, match="allowlisted mode"):
        runner.start(
            tcode="ZMAP",
            map_id="bad-mode",
            mode="reversible",
            allowed_branches=[rule.model_dump(mode="json")],
            test_client_confirmed=True,
        )


def test_tick_hydrates_exact_rules_into_production_adapter_boundary(tmp_path) -> None:
    wrapper = FakeWrapper()
    configured: list[list[TraversalBranchRule]] = []

    class ConfigurableAdapter(FakeAdapter):
        def configure_branch_rules(self, rules):
            configured.append(list(rules))

        def discover(self, state):
            return ()

    runner = UiMapScheduler(
        MapJobStore(tmp_path),
        lambda _slot: wrapper,
        scanner=FakeScanner(),
        adapter_factory=ConfigurableAdapter,
    )
    rule = TraversalBranchRule(
        action="press",
        source_state_id="state-a",
        target_logical_id="detail",
        target_sap_id="wnd[0]/usr/btnDETAIL",
        component_type="GuiButton",
        restore_vkey=3,
    )
    runner.start(
        tcode="ZMAP",
        map_id="map",
        mode="allowlisted",
        allowed_branches=[rule.model_dump(mode="json")],
        test_client_confirmed=True,
        job_id="configure-exact-branch",
    )

    completed = runner.tick("configure-exact-branch")

    assert completed.status == "completed"
    assert [[item.rule_id for item in rules] for rules in configured] == [
        [rule.rule_id]
    ]


def test_each_tick_executes_at_most_one_transition(tmp_path) -> None:
    wrapper = FakeWrapper()
    runner, _ = scheduler(tmp_path, wrapper)
    runner.start(
        tcode="ZMAP",
        map_id="map",
        mode="reversible",
        test_client_confirmed=True,
        job_id="job-slice",
    )

    first = runner.tick("job-slice")

    assert first is not None
    assert first.status == "running"
    assert wrapper.navigations == 1
    assert wrapper.session.actions == 1
    assert first.checkpoint is not None
    assert len(first.checkpoint.ui_map.transitions) == 1

    second = runner.tick("job-slice")
    assert second is not None
    assert wrapper.session.actions == 2
    assert len(second.checkpoint.ui_map.transitions) == 2


def test_cooperative_tick_persists_the_partial_graph_for_later_planning(tmp_path) -> None:
    wrapper = FakeWrapper()
    maps = UiMapStore(tmp_path / "maps")
    runner = UiMapScheduler(
        MapJobStore(tmp_path / "jobs"),
        lambda _slot: wrapper,
        scanner=FakeScanner(),
        adapter_factory=FakeAdapter,
        map_store=maps,
    )
    runner.start(
        tcode="ZMAP",
        map_id="map",
        mode="reversible",
        test_client_confirmed=True,
        job_id="persist-between-ticks",
    )

    current = runner.tick("persist-between-ticks")

    assert current is not None and current.status == "running"
    assert len(maps.latest("map", tcode="ZMAP").transitions) == 1
    assert current.checkpoint is not None
    assert current.checkpoint.ui_map.manifest.map_version == 1


def test_guided_mode_lists_one_safe_candidate_and_executes_only_the_selection(tmp_path):
    wrapper = FakeWrapper()
    class ChoiceAdapter(FakeAdapter):
        def discover(self, state: UiState):
            if state.screen_number == 100:
                for next_screen in (200, 300):
                    yield TraversalCandidate(
                        action="inspect",
                        params={"next": next_screen},
                        side_effect=SideEffectClass.READ_ONLY,
                        reversible=True,
                    )

    runner = UiMapScheduler(
        MapJobStore(tmp_path),
        lambda _slot: wrapper,
        scanner=FakeScanner(),
        adapter_factory=ChoiceAdapter,
    )
    runner.start(
        tcode="ZMAP",
        map_id="map",
        mode="guided",
        test_client_confirmed=True,
        job_id="guided-one",
    )

    waiting = runner.tick("guided-one")

    assert waiting is not None
    assert waiting.status == "needs_user_input"
    assert wrapper.session.actions == 0
    question = waiting.pending_questions[0]
    assert question["reason"] == "guided_candidate_selection"
    assert question["required_fields"] == ["candidate_id"]
    assert len(question["choices"]) == 2
    choice = question["choices"][1]
    assert choice["action"] == "inspect"
    assert choice["reversible"] is True

    with pytest.raises(ValueError, match="unknown guided candidate"):
        runner.resume(waiting.job_id, {question["resume_token"]: {"candidate_id": "forged"}})

    runner.resume(
        waiting.job_id,
        {question["resume_token"]: {"candidate_id": choice["candidate_id"]}},
    )
    expanded = runner.tick(waiting.job_id)

    assert expanded is not None
    # The selected action ran once, then the newly discovered screen offered
    # its own list instead of continuing automatically.
    assert expanded.status == "needs_user_input"
    assert wrapper.session.actions == 1
    assert expanded.checkpoint is not None
    assert len(expanded.checkpoint.ui_map.transitions) == 1
    assert expanded.checkpoint.traversal_scope.get("guided_selection") is None
    next_question = expanded.pending_questions[0]
    assert next_question["reason"] == "guided_candidate_selection"
    assert next_question["choices"][0]["candidate_id"] != choice["candidate_id"]


def test_guided_semantic_review_is_mcp_confirmed_before_candidate_selection(tmp_path):
    wrapper = FakeWrapper()

    class Reviewer:
        def propose(self, state: UiState):
            return [
                SemanticLink(
                    link_id="semantic-screen-100",
                    subject_kind="state",
                    subject_id=state.state_id,
                    predicate="has_ui_role",
                    object_id="ui_role:selection",
                    confidence=0.55,
                )
            ]

    runner = UiMapScheduler(
        MapJobStore(tmp_path),
        lambda _slot: wrapper,
        scanner=FakeScanner(),
        adapter_factory=FakeAdapter,
        semantic_reviewer=Reviewer(),
    )
    runner.start(
        tcode="ZMAP",
        map_id="semantic-map",
        mode="guided",
        test_client_confirmed=True,
        semantic_review=True,
        job_id="semantic-guided",
    )

    review = runner.tick("semantic-guided")

    assert review is not None and review.status == "needs_user_input"
    question = review.pending_questions[0]
    assert question["reason"] == "semantic_review"
    assert question["required_fields"] == ["accepted_candidate_ids"]
    candidate_id = question["choices"][0]["candidate_id"]
    assert wrapper.session.actions == 0

    runner.resume(
        review.job_id,
        {question["resume_token"]: {"accepted_candidate_ids": [candidate_id]}},
    )
    guided = runner.tick(review.job_id)

    assert guided is not None and guided.status == "needs_user_input"
    assert guided.pending_questions[0]["reason"] == "guided_candidate_selection"
    assert guided.checkpoint is not None
    link = guided.checkpoint.ui_map.semantic_links[0]
    assert link.link_id == candidate_id
    assert link.status == "confirmed"
    assert link.provenance["reviewed_by"] == "mcp_ai"


def test_cancelled_job_never_resolves_wrapper_or_executes(tmp_path) -> None:
    wrapper = FakeWrapper()
    runner, wrapper_calls = scheduler(tmp_path, wrapper)
    runner.start(
        tcode="ZMAP",
        map_id="map",
        mode="reversible",
        test_client_confirmed=True,
        job_id="job-cancel",
    )
    runner.job_store.request_cancel("job-cancel")

    record = runner.tick("job-cancel")

    assert record is not None and record.status == "cancelled"
    assert wrapper_calls == []
    assert wrapper.session.actions == 0


def test_resume_answers_are_consumed_once_and_never_written_to_disk(tmp_path) -> None:
    wrapper = FakeWrapper()
    runner, _ = scheduler(tmp_path, wrapper)
    runner.start(
        tcode="ZMAP",
        map_id="map",
        owner_agent="mapper",
        slot_id="ses1",
        navigate_pending=False,
        mode="reversible",
        test_client_confirmed=True,
        job_id="job-answer",
    )
    question = UserInputRequest(
        question="회사 코드를 알려 주세요",
        required_fields=["company_code"],
        resume_token="company-code",
    )
    state = FakeScanner().scan(wrapper.session, tcode="ZMAP")
    checkpoint = TraversalCheckpoint(
        ui_map=UiMap(
            manifest=MapManifest(map_id="map", tcode="ZMAP"),
            states={state.state_id: state},
            pending_interactions=[question],
        ),
        frontier=[state.state_id],
        depths={state.state_id: 0},
        needs_user_input=[question],
    )
    runner.job_store.save_checkpoint("job-answer", checkpoint)

    runner.resume(
        "job-answer",
        {"company-code": {"company_code": "SECRET-1000"}},
        owner_agent="mapper",
        slot_id="ses1",
    )
    assert runner.has_pending_answers("job-answer")

    runner.tick("job-answer")

    assert not runner.has_pending_answers("job-answer")
    raw = (tmp_path / "job-answer.json").read_text(encoding="utf-8")
    assert "SECRET-1000" not in raw
    assert "company_code" in raw


def test_dirty_checkpoint_is_interrupted_without_com(tmp_path) -> None:
    wrapper = FakeWrapper()
    runner, wrapper_calls = scheduler(tmp_path, wrapper)
    runner.start(
        tcode="ZMAP",
        map_id="map",
        mode="reversible",
        test_client_confirmed=True,
        job_id="job-dirty",
    )
    state = FakeScanner().scan(wrapper.session, tcode="ZMAP")
    checkpoint = TraversalCheckpoint(
        ui_map=UiMap(
            manifest=MapManifest(map_id="map", tcode="ZMAP"),
            states={state.state_id: state},
        ),
        in_flight={"dirty": True, "candidate": {"action": "inspect"}},
    )
    runner.job_store.save_checkpoint("job-dirty", checkpoint)

    record = runner.tick("job-dirty")

    assert record is not None and record.status == "interrupted"
    assert wrapper_calls == []
    assert wrapper.session.actions == 0


def test_full_transition_budget_is_not_exceeded(tmp_path) -> None:
    wrapper = FakeWrapper()
    runner, _ = scheduler(tmp_path, wrapper)
    runner.start(
        tcode="ZMAP",
        map_id="map",
        budget=TraversalBudget(max_transitions=1),
        mode="reversible",
        test_client_confirmed=True,
        job_id="job-budget",
    )

    finished = runner.tick("job-budget")

    assert finished is not None and finished.status == "paused"
    assert finished.stop_reason == "max_transitions"
    assert wrapper.session.actions == 1


def test_budget_pause_releases_lease_and_same_job_resumes_with_larger_budget(tmp_path):
    wrapper = FakeWrapper()
    coordinator = DaemonJobCoordinator()
    runner = UiMapScheduler(
        MapJobStore(tmp_path),
        lambda _slot: wrapper,
        scanner=FakeScanner(),
        adapter_factory=FakeAdapter,
        coordinator=coordinator,
    )
    runner.start(
        tcode="ZMAP",
        map_id="map",
        budget=TraversalBudget(max_transitions=1),
        mode="reversible",
        test_client_confirmed=True,
        job_id="budget-resume",
    )
    paused = runner.tick("budget-resume")

    assert paused is not None and paused.status == "paused"
    assert coordinator.get("map:budget-resume").status == "paused"
    assert coordinator.lease_for_slot("ses0") is None

    resumed = runner.resume(
        "budget-resume", budget={"max_transitions": 3}
    )
    assert resumed.status == "queued"
    assert resumed.traversal_config["budget"]["max_transitions"] == 3

    current = resumed
    for _ in range(5):
        current = runner.tick("budget-resume")
        if current is not None and current.status == "completed":
            break
    assert current is not None and current.status == "completed"
    assert wrapper.session.actions == 2


def test_max_states_pause_replays_unexecuted_candidate_after_budget_extension(tmp_path):
    wrapper = FakeWrapper()
    runner, _ = scheduler(tmp_path, wrapper)
    runner.start(
        tcode="ZMAP",
        map_id="map",
        budget=TraversalBudget(max_states=1, max_transitions=10),
        mode="reversible",
        test_client_confirmed=True,
        job_id="state-budget",
    )

    paused = runner.tick("state-budget")

    assert paused.status == "paused"
    assert paused.stop_reason == "max_states"
    assert wrapper.session.actions == 0
    runner.resume("state-budget", budget={"max_states": 3})
    current = None
    for _ in range(5):
        current = runner.tick("state-budget")
        if current is not None and current.status == "completed":
            break
    assert current is not None and current.status == "completed"
    assert wrapper.session.actions == 2


def test_budget_resume_rejects_same_smaller_and_already_consumed_limits(tmp_path):
    wrapper = FakeWrapper()
    runner, _ = scheduler(tmp_path, wrapper)
    runner.start(
        tcode="ZMAP",
        map_id="map",
        budget=TraversalBudget(max_transitions=1),
        mode="reversible",
        test_client_confirmed=True,
        job_id="budget-invalid",
    )
    assert runner.tick("budget-invalid").status == "paused"

    with pytest.raises(ValueError, match="must increase"):
        runner.resume("budget-invalid", budget={"max_transitions": 1})
    with pytest.raises(ValueError, match="cannot decrease"):
        runner.resume(
            "budget-invalid",
            budget={"max_transitions": 2, "max_states": 249},
        )

    record = runner.job_store.get("budget-invalid")
    record.checkpoint.elapsed_seconds = 1000
    with pytest.raises(ValueError, match="must exceed current usage: max_seconds"):
        runner._expanded_budget(record, {"max_transitions": 2})


def test_paused_budget_job_hydrates_without_reacquiring_slot(tmp_path):
    wrapper = FakeWrapper()
    jobs = MapJobStore(tmp_path / "jobs")
    first = UiMapScheduler(
        jobs,
        lambda _slot: wrapper,
        scanner=FakeScanner(),
        adapter_factory=FakeAdapter,
        coordinator=DaemonJobCoordinator(),
    )
    first.start(
        tcode="ZMAP",
        map_id="map",
        budget=TraversalBudget(max_transitions=1),
        mode="reversible",
        test_client_confirmed=True,
        job_id="paused-restart",
    )
    assert first.tick("paused-restart").status == "paused"

    coordinator = DaemonJobCoordinator()
    restarted = UiMapScheduler(
        jobs,
        lambda _slot: wrapper,
        scanner=FakeScanner(),
        adapter_factory=FakeAdapter,
        coordinator=coordinator,
    )
    counts = restarted.hydrate_persisted_jobs()

    assert counts["paused"] == 1
    assert coordinator.get("map:paused-restart").status == "paused"
    assert coordinator.lease_for_slot("ses0") is None
    assert restarted.resume(
        "paused-restart", budget={"max_transitions": 3}
    ).status == "queued"


def test_active_mode_requires_explicit_test_client_confirmation(tmp_path) -> None:
    wrapper = FakeWrapper()
    runner, _ = scheduler(tmp_path, wrapper)
    with pytest.raises(ValueError, match="test_client_confirmed"):
        runner.start(tcode="ZMAP", map_id="map", mode="reversible")


def test_common_coordinator_prevents_two_map_jobs_interleaving_same_slot(tmp_path):
    wrapper = FakeWrapper()
    coordinator = DaemonJobCoordinator()
    calls = []
    runner = UiMapScheduler(
        MapJobStore(tmp_path),
        lambda slot_id: calls.append(slot_id) or wrapper,
        scanner=FakeScanner(),
        adapter_factory=FakeAdapter,
        coordinator=coordinator,
    )
    for job_id in ("first", "second"):
        runner.start(
            tcode="ZMAP",
            map_id=job_id,
            mode="reversible",
            test_client_confirmed=True,
            slot_id="ses0",
            job_id=job_id,
        )

    blocked = runner.tick("second")
    assert blocked.status == "queued"
    assert calls == []
    assert coordinator.lease_for_slot("ses0").job_id == "map:first"

    runner.tick("first")
    assert calls == ["ses0"]
    assert wrapper.session.actions == 1


def test_cancel_between_slices_releases_common_slot_lease(tmp_path):
    wrapper = FakeWrapper()
    coordinator = DaemonJobCoordinator()
    runner = UiMapScheduler(
        MapJobStore(tmp_path),
        lambda _slot: wrapper,
        scanner=FakeScanner(),
        adapter_factory=FakeAdapter,
        coordinator=coordinator,
    )
    runner.start(
        tcode="ZMAP", map_id="map", mode="reversible",
        test_client_confirmed=True, job_id="cancel"
    )
    assert coordinator.lease_for_slot("ses0") is not None

    record = runner.cancel("cancel")

    assert record.status == "cancelled"
    assert coordinator.lease_for_slot("ses0") is None


def test_completed_job_persists_versioned_map(tmp_path) -> None:
    wrapper = FakeWrapper()
    jobs = MapJobStore(tmp_path / "jobs")
    maps = UiMapStore(tmp_path / "maps")
    runner = UiMapScheduler(
        jobs,
        lambda _slot: wrapper,
        scanner=FakeScanner(),
        adapter_factory=FakeAdapter,
        map_store=maps,
    )
    runner.start(tcode="ZMAP", map_id="persisted", mode="passive", job_id="job-map")

    finished = runner.tick("job-map")

    assert finished is not None and finished.status == "completed"
    saved = maps.latest("persisted", tcode="ZMAP")
    assert saved.manifest.map_version == 1
    assert len(saved.states) == 1


def test_completed_map_can_be_augmented_by_manual_move_as_non_destructive_delta(tmp_path):
    wrapper = FakeWrapper()
    jobs = MapJobStore(tmp_path / "jobs")
    maps = UiMapStore(tmp_path / "maps")
    source = FakeScanner().scan(wrapper.session, tcode="ZMAP")
    original = maps.save(
        UiMap(
            manifest=MapManifest(map_id="existing", tcode="ZMAP"),
            states={source.state_id: source},
        )
    )
    runner = UiMapScheduler(
        jobs,
        lambda _slot: wrapper,
        scanner=FakeScanner(),
        adapter_factory=SapTraversalAdapter,
        map_store=maps,
    )

    paused = runner.augment_start(
        ui_map=original,
        from_state_id=source.state_id,
        request="누락된 상세 화면으로 이동해 주세요.",
        owner_agent="mapper",
        slot_id="ses0",
        job_id="augment-one",
    )

    assert paused.status == "needs_user_input"
    assert wrapper.navigations == 0
    token = paused.pending_questions[0]["resume_token"]
    wrapper.session.screen = 200
    runner.resume(
        paused.job_id,
        {token: {"continue": True}},
        owner_agent="mapper",
        slot_id="ses0",
    )
    finished = runner.tick(paused.job_id)

    assert finished is not None and finished.status == "completed"
    saved = maps.latest("existing", tcode="ZMAP")
    assert saved.manifest.map_version == 2
    assert source.state_id in saved.states
    assert {state.screen_number for state in saved.states.values()} == {100, 200}
    guided = [
        item for item in saved.transitions if item.action == "user_guided_navigation"
    ]
    assert len(guided) == 1
    assert guided[0].from_state_id == source.state_id
    assert guided[0].to_state_id in saved.states
    assert saved.pending_interactions[-1].status == "resolved"


def test_user_taught_structural_route_is_saved_and_compiles_from_latest_map(tmp_path):
    wrapper = FakeWrapper()
    jobs = MapJobStore(tmp_path / "jobs")
    maps = UiMapStore(tmp_path / "maps")
    source = FakeScanner().scan(wrapper.session, tcode="ZMAP").model_copy(
        update={
            "components": [
                UiComponent(
                    logical_id="open-detail",
                    sap_id="wnd[0]/usr/btnDETAIL",
                    type="GuiButton",
                    visible=True,
                    enabled=True,
                    supported_actions=["press"],
                )
            ]
        }
    )
    original = maps.save(
        UiMap(
            manifest=MapManifest(map_id="existing", tcode="ZMAP"),
            states={source.state_id: source},
        )
    )
    runner = UiMapScheduler(
        jobs, lambda _slot: wrapper, scanner=FakeScanner(),
        adapter_factory=SapTraversalAdapter, map_store=maps,
    )
    paused = runner.augment_start(
        ui_map=original, from_state_id=source.state_id, request="상세 화면", job_id="teach-route"
    )
    token = paused.pending_questions[0]["resume_token"]
    assert {"transition": {"action": "press", "target_logical_id": "open-detail"}} in paused.pending_questions[0]["choices"]

    wrapper.session.screen = 200
    runner.resume(
        paused.job_id,
        {
            token: {
                "continue": True,
                "transition": {"action": "press", "target_logical_id": "open-detail"},
            }
        },
    )
    assert runner.tick(paused.job_id).status == "completed"

    saved = maps.latest("existing", tcode="ZMAP")
    taught = next(item for item in saved.transitions if item.action == "press")
    assert taught.target_locator == {
        "sap_id": "wnd[0]/usr/btnDETAIL", "component_type": "GuiButton"
    }
    result = PlanCompiler(maps).compile(
        "tcode ZMAP screen 조회", goal_query="screen-200"
    )
    assert result.validation.valid
    assert [step.command for step in result.plan.steps] == ["run", "ui_press"]


@pytest.mark.parametrize(
    ("action", "attribute", "item_id", "expected_command"),
    [
        (
            "press_toolbar_button",
            "toolbar_actions",
            "DISPLAY_DETAIL",
            "ui_press_toolbar_button",
        ),
        (
            "select_context_menu_item",
            "context_menu_actions",
            "OPEN_DETAIL",
            "ui_select_context_menu_item",
        ),
    ],
)
def test_user_taught_alv_structural_route_is_saved_and_compiles(
    tmp_path, action, attribute, item_id, expected_command
):
    """A user-confirmed ALV action must become an executable graph edge.

    The map never guesses these actions: the descriptor has to have been
    scanned and the augmentation answer must select its exact stable ID.
    """

    wrapper = FakeWrapper()
    jobs = MapJobStore(tmp_path / "jobs")
    maps = UiMapStore(tmp_path / "maps")
    grid = UiComponent(
        logical_id="result-grid",
        sap_id="wnd[0]/usr/cntlALV/shell",
        type="GuiGridView",
        visible=True,
        enabled=True,
        supported_actions=["press_toolbar_button", "select_context_menu_item"],
    )
    collection_kwargs = {attribute: [{"id": item_id, "enabled": True, "visible": True}]}
    source = FakeScanner().scan(wrapper.session, tcode="ZMAP").model_copy(
        update={
            "components": [grid],
            "collections": [
                CollectionDescriptor(
                    component_logical_id=grid.logical_id,
                    kind="alv",
                    **collection_kwargs,
                )
            ],
        }
    )
    original = maps.save(
        UiMap(
            manifest=MapManifest(map_id="existing", tcode="ZMAP"),
            states={source.state_id: source},
        )
    )
    runner = UiMapScheduler(
        jobs,
        lambda _slot: wrapper,
        scanner=FakeScanner(),
        adapter_factory=SapTraversalAdapter,
        map_store=maps,
    )
    paused = runner.augment_start(
        ui_map=original,
        from_state_id=source.state_id,
        request="상세 화면",
        job_id=f"teach-{action}",
    )
    token = paused.pending_questions[0]["resume_token"]
    transition = {
        "action": action,
        "target_logical_id": grid.logical_id,
        "item_id": item_id,
    }
    assert {"transition": transition} in paused.pending_questions[0]["choices"]

    wrapper.session.screen = 200
    runner.resume(
        paused.job_id,
        {token: {"continue": True, "transition": transition}},
    )
    assert runner.tick(paused.job_id).status == "completed"

    saved = maps.latest("existing", tcode="ZMAP")
    taught = next(item for item in saved.transitions if item.action == action)
    assert taught.target_locator == {
        "sap_id": grid.sap_id,
        "component_type": "GuiGridView",
        "item_id": item_id,
    }
    assert taught.observed_params == {"item_id": item_id}
    result = PlanCompiler(maps).compile(
        "tcode ZMAP screen 조회", goal_query="screen-200"
    )
    assert result.validation.valid
    assert [step.command for step in result.plan.steps] == ["run", expected_command]
    assert result.plan.steps[-1].params["value"] == item_id


def test_augmentation_never_offers_or_accepts_dangerous_structural_routes(tmp_path):
    wrapper = FakeWrapper()
    grid = UiComponent(
        logical_id="result-grid",
        sap_id="wnd[0]/usr/cntlALV/shell",
        type="GuiGridView",
        visible=True,
        enabled=True,
        supported_actions=["press_toolbar_button"],
    )
    source = FakeScanner().scan(wrapper.session, tcode="ZMAP").model_copy(
        update={
            "components": [
                grid,
                UiComponent(
                    logical_id="저장", sap_id="wnd[0]/tbar/btnSAVE", type="GuiButton",
                    visible=True, enabled=True, supported_actions=["press"],
                ),
            ],
            "collections": [
                CollectionDescriptor(
                    component_logical_id=grid.logical_id,
                    kind="alv",
                    toolbar_actions=[{"id": "저장", "enabled": True, "visible": True}],
                )
            ],
        }
    )
    maps = UiMapStore(tmp_path / "maps")
    original = maps.save(
        UiMap(
            manifest=MapManifest(map_id="existing", tcode="ZMAP"),
            states={source.state_id: source},
        )
    )
    runner = UiMapScheduler(
        MapJobStore(tmp_path / "jobs"),
        lambda _slot: wrapper,
        scanner=FakeScanner(),
        adapter_factory=SapTraversalAdapter,
        map_store=maps,
    )
    paused = runner.augment_start(
        ui_map=original, from_state_id=source.state_id, request="상세 화면"
    )
    question = paused.pending_questions[0]
    assert question["choices"] == [{"continue": True}]
    assert runner._dangerous_structural_marker("SAVE")
    assert runner._dangerous_structural_marker("저장")

    wrapper.session.screen = 200
    runner.resume(
        paused.job_id,
        {
            question["resume_token"]: {
                "continue": True,
                "transition": {
                    "action": "press_toolbar_button",
                    "target_logical_id": grid.logical_id,
                    "item_id": "저장",
                },
            }
        },
    )
    assert runner.tick(paused.job_id).status == "needs_user_input"


def test_augmentation_requires_a_replayable_choice_when_source_has_one(tmp_path):
    wrapper = FakeWrapper()
    source = FakeScanner().scan(wrapper.session, tcode="ZMAP").model_copy(
        update={
            "components": [
                UiComponent(
                    logical_id="open-detail",
                    sap_id="wnd[0]/usr/btnDETAIL",
                    type="GuiButton",
                    visible=True,
                    enabled=True,
                    supported_actions=["press"],
                )
            ]
        }
    )
    maps = UiMapStore(tmp_path / "maps")
    original = maps.save(
        UiMap(
            manifest=MapManifest(map_id="existing", tcode="ZMAP"),
            states={source.state_id: source},
        )
    )
    runner = UiMapScheduler(
        MapJobStore(tmp_path / "jobs"),
        lambda _slot: wrapper,
        scanner=FakeScanner(),
        adapter_factory=SapTraversalAdapter,
        map_store=maps,
    )
    paused = runner.augment_start(
        ui_map=original,
        from_state_id=source.state_id,
        request="상세 화면",
        job_id="require-replayable-choice",
    )
    question = paused.pending_questions[0]

    assert question["required_fields"] == ["continue", "transition"]
    with pytest.raises(ValueError, match="resume answer missing required fields: transition"):
        runner.resume(paused.job_id, {question["resume_token"]: {"continue": True}})


@pytest.mark.parametrize(
    "session_update",
    [
        {"system_name": "QAS"},
        {"client": "200"},
        {"language": "EN"},
        {"transaction": "VA03"},
    ],
)
def test_augmentation_refuses_wrong_live_sap_scope_before_scanning(
    tmp_path, session_update
):
    wrapper = FakeWrapper()
    source = FakeScanner().scan(wrapper.session, tcode="ZMAP")
    original = UiMap(
        manifest=MapManifest(
            map_id="existing", tcode="ZMAP", system_id="DEV", client="100", language="KO"
        ),
        states={source.state_id: source},
    )
    maps = UiMapStore(tmp_path / "maps")
    original = maps.save(original)
    jobs = MapJobStore(tmp_path / "jobs")
    runner = UiMapScheduler(
        jobs, lambda _slot: wrapper, scanner=FakeScanner(),
        adapter_factory=SapTraversalAdapter, map_store=maps,
    )
    session = {
        "system_name": "DEV", "client": "100", "language": "KO", "transaction": "ZMAP"
    }
    session.update(session_update)
    wrapper.get_session_info = lambda: SimpleNamespace(**session)
    paused = runner.augment_start(
        ui_map=original, from_state_id=source.state_id, request="상세 화면", job_id="scope"
    )
    token = paused.pending_questions[0]["resume_token"]
    wrapper.session.screen = 200
    runner.resume(paused.job_id, {token: {"continue": True}})

    failed = runner.tick(paused.job_id)

    assert failed is not None and failed.status == "failed"
    assert maps.latest(
        "existing", tcode="ZMAP", system_id="DEV", client="100", language="KO"
    ).manifest.map_version == 1


def test_augmentation_stays_paused_until_the_user_reaches_a_distinct_screen(tmp_path):
    wrapper = FakeWrapper()
    jobs = MapJobStore(tmp_path / "jobs")
    maps = UiMapStore(tmp_path / "maps")
    source = FakeScanner().scan(wrapper.session, tcode="ZMAP")
    original = maps.save(
        UiMap(
            manifest=MapManifest(map_id="existing", tcode="ZMAP"),
            states={source.state_id: source},
        )
    )
    runner = UiMapScheduler(
        jobs,
        lambda _slot: wrapper,
        scanner=FakeScanner(),
        adapter_factory=SapTraversalAdapter,
        map_store=maps,
    )
    paused = runner.augment_start(
        ui_map=original,
        from_state_id=source.state_id,
        request="다른 상세 화면으로 이동해 주세요.",
        job_id="augment-no-move",
    )
    token = paused.pending_questions[0]["resume_token"]

    runner.resume(paused.job_id, {token: {"continue": True}})
    still_paused = runner.tick(paused.job_id)

    assert still_paused is not None and still_paused.status == "needs_user_input"
    saved = maps.latest("existing", tcode="ZMAP")
    assert set(saved.states) == {source.state_id}
    assert saved.transitions == []
    assert saved.pending_interactions[-1].status == "pending"


def test_needs_user_input_augmentation_hydrates_and_resumes_after_restart(tmp_path):
    wrapper = FakeWrapper()
    jobs = MapJobStore(tmp_path / "jobs")
    maps = UiMapStore(tmp_path / "maps")
    source = FakeScanner().scan(wrapper.session, tcode="ZMAP")
    original = maps.save(
        UiMap(
            manifest=MapManifest(map_id="existing", tcode="ZMAP"),
            states={source.state_id: source},
        )
    )
    first = UiMapScheduler(
        jobs,
        lambda _slot: wrapper,
        scanner=FakeScanner(),
        adapter_factory=SapTraversalAdapter,
        map_store=maps,
        coordinator=DaemonJobCoordinator(),
    )
    paused = first.augment_start(
        ui_map=original,
        from_state_id=source.state_id,
        request="재시작 뒤에도 추가할 상세 화면",
        owner_agent="mapper",
        slot_id="ses9",
        job_id="augment-restart",
    )

    restarted_coordinator = DaemonJobCoordinator()
    restarted = UiMapScheduler(
        jobs,
        lambda _slot: wrapper,
        scanner=FakeScanner(),
        adapter_factory=SapTraversalAdapter,
        map_store=maps,
        coordinator=restarted_coordinator,
    )
    counts = restarted.hydrate_persisted_jobs()

    assert counts["needs_user_input"] == 1
    assert restarted_coordinator.get("map:augment-restart").status == "needs_user_input"
    assert restarted_coordinator.lease_for_slot("ses9").job_id == "map:augment-restart"
    wrapper.session.screen = 200
    token = paused.pending_questions[0]["resume_token"]
    restarted.resume(
        paused.job_id,
        {token: {"continue": True}},
        owner_agent="mapper",
        slot_id="ses9",
    )
    finished = restarted.tick(paused.job_id)

    assert finished is not None and finished.status == "completed"
    assert restarted_coordinator.get("map:augment-restart").status == "completed"
    saved = maps.latest("existing", tcode="ZMAP")
    assert {state.screen_number for state in saved.states.values()} == {100, 200}


def test_hydration_restores_queued_and_clean_running_jobs(tmp_path):
    jobs = MapJobStore(tmp_path / "jobs")
    jobs.create(
        tcode="ZMAP", map_id="queued", job_id="queued", slot_id="ses1"
    )
    jobs.create(
        tcode="ZMAP", map_id="running", job_id="running", slot_id="ses2"
    )
    state = UiState(state_id="state", tcode="ZMAP")
    jobs.save_checkpoint(
        "running",
        TraversalCheckpoint(
            ui_map=UiMap(
                manifest=MapManifest(map_id="running", tcode="ZMAP"),
                states={state.state_id: state},
            ),
            frontier=[state.state_id],
        ),
    )
    coordinator = DaemonJobCoordinator()
    runner = UiMapScheduler(
        jobs,
        lambda _slot: FakeWrapper(),
        scanner=FakeScanner(),
        adapter_factory=FakeAdapter,
        coordinator=coordinator,
    )

    counts = runner.hydrate_persisted_jobs()

    assert counts == {
        "queued": 1,
        "running": 1,
        "needs_user_input": 0,
        "paused": 0,
        "interrupted": 0,
    }
    assert coordinator.get("map:queued").status == "queued"
    assert coordinator.get("map:running").status == "running"


def test_dispatcher_augmentation_resume_persists_manual_screen_end_to_end(tmp_path):
    wrapper = FakeWrapper()
    jobs = MapJobStore(tmp_path / "jobs")
    maps = UiMapStore(tmp_path / "maps")
    source = FakeScanner().scan(wrapper.session, tcode="ZMAP")
    maps.save(
        UiMap(
            manifest=MapManifest(map_id="existing", tcode="ZMAP"),
            states={source.state_id: source},
        )
    )
    runner = UiMapScheduler(
        jobs,
        lambda _slot: wrapper,
        scanner=FakeScanner(),
        adapter_factory=SapTraversalAdapter,
        map_store=maps,
        coordinator=DaemonJobCoordinator(),
    )
    ctx = SimpleNamespace(agent_name="mapper", source="test", slot_id="ses0")
    dependencies = {
        "get_map_store": lambda: maps,
        "get_job_store": lambda: jobs,
        "get_scheduler": lambda: runner,
    }

    started = dispatch_ui_map_command(
        "map_augment_start",
        {
            "map_id": "existing",
            "tcode": "ZMAP",
            "from_state_id": source.state_id,
            "request": "사용자가 지정한 누락 상세 화면",
        },
        ctx,
        **dependencies,
    )
    job_id = started["job"]["job_id"]
    token = started["job"]["pending_questions"][0]["resume_token"]
    assert started["job"]["status"] == "needs_user_input"

    wrapper.session.screen = 200
    resumed = dispatch_ui_map_command(
        "map_traverse_resume",
        {"job_id": job_id, "answers": {token: {"continue": True}}},
        ctx,
        **dependencies,
    )
    assert resumed["job"]["status"] == "queued"
    finished = runner.tick(job_id)

    assert finished is not None and finished.status == "completed"
    saved = maps.latest("existing", tcode="ZMAP")
    assert {state.screen_number for state in saved.states.values()} == {100, 200}
    assert any(item.action == "user_guided_navigation" for item in saved.transitions)


def test_tick_records_live_client_identity_without_production_judgement(tmp_path):
    # A test client that happens to be numbered 100 must be recorded verbatim
    # and the job must run normally — the number is never treated as production.
    from sap_cli.dispatchers.ui_maps import _job_summary

    wrapper = FakeWrapper()
    wrapper.get_session_info = lambda: SimpleNamespace(
        system_name="DEV",
        client="100",
        user="TESTER",
        language="EN",
        transaction="ZMAP",
    )
    runner, _ = scheduler(tmp_path, wrapper)
    record = runner.start(tcode="ZMAP", map_id="scope-map", mode="passive")

    runner.tick(record.job_id)

    stored = runner.job_store.get(record.job_id)
    assert stored.observed_scope["client"] == "100"
    assert stored.observed_scope["system_id"] == "DEV"
    assert stored.observed_scope["user"] == "TESTER"
    # 100 is not production: a passive scan still completes.
    assert stored.status == "completed"

    summary = _job_summary(stored)
    assert summary["observed_scope"]["client"] == "100"
    assert summary["test_client_confirmed"] is False


def test_scope_mismatch_surfaces_specific_reason_not_generic_failure(tmp_path):
    # If the live system/client/language differs from what the map was recorded
    # on, the job must fail with an explicit map_scope_mismatch so the cause is
    # visible — this is a scope difference, never a client-number production
    # verdict. Live client 100 vs a map recorded on client 200.
    wrapper = FakeWrapper()
    wrapper.get_session_info = lambda: SimpleNamespace(
        system_name="DEV", client="100", user="T", language="EN", transaction="ZMAP",
    )
    store = MapJobStore(tmp_path)
    record = store.create(
        tcode="ZMAP",
        map_id="scope-map",
        traversal_config={
            "mode": "passive",
            "navigate_pending": False,
            "map_scope": {"system_id": "DEV", "client": "200", "language": "EN"},
        },
    )

    def get_wrapper(slot_id: str) -> FakeWrapper:
        return wrapper

    runner = UiMapScheduler(
        store, get_wrapper, scanner=FakeScanner(), adapter_factory=FakeAdapter
    )
    runner.tick(record.job_id)

    failed = store.get(record.job_id)
    assert failed.status == "failed"
    assert failed.error == "map_scope_mismatch"
