from __future__ import annotations

from types import SimpleNamespace

import pytest

from sap_cli.ui_map.sap_traversal import SapTraversalAdapter
from sap_cli.ui_map.schema import (
    MapManifest,
    SideEffectClass,
    TraversalBudget,
    TraversalBranchRule,
    TraversalPolicy,
    UiMap,
)
from sap_cli.ui_map.traversal import (
    NeedsUserInput,
    TraversalCandidate,
    TraversalCheckpoint,
    TraversalEngine,
)


class Collection:
    def __init__(self, items=()):
        self.items = list(items)
        self.Count = len(self.items)

    def __call__(self, index):
        return self.items[index]


def make_session(name="A", *, modal=False):
    window = SimpleNamespace(
        Id="wnd[1]" if modal else "wnd[0]",
        Type="GuiModalWindow" if modal else "GuiMainWindow",
        Name="window",
        Text=name,
        IsModal=modal,
        Children=Collection(),
    )
    return SimpleNamespace(
        Children=Collection((window,)),
        ActiveWindow=window,
        Info=SimpleNamespace(
            Transaction="ZMAP",
            Program="ZPROGRAM",
            ScreenNumber={"A": 100, "B": 200, "C": 300}.get(name, 900),
        ),
    )


def make_session_with_unsafe_button(name="A"):
    button = SimpleNamespace(
        Id="wnd[0]/usr/btnSAVE",
        Type="GuiButton",
        Name="SAVE",
        Text="SAVE BUSINESS-SECRET",
        Visible=True,
        Enabled=True,
        Children=Collection(),
    )
    window = SimpleNamespace(
        Id="wnd[0]",
        Type="GuiMainWindow",
        Name="window",
        Text=name,
        IsModal=False,
        Children=Collection((button,)),
    )
    return SimpleNamespace(
        Children=Collection((window,)),
        ActiveWindow=window,
        Info=SimpleNamespace(
            Transaction="ZMAP", Program="ZPROGRAM", ScreenNumber=100
        ),
    )


class Adapter:
    def __init__(
        self,
        candidates=None,
        *,
        fail_actions=(),
        fail_restore=False,
        mismatch_restore=False,
    ):
        self._session = make_session("A")
        self.by_title = {"A": self._session, "B": make_session("B"), "C": make_session("C")}
        self.candidates = candidates or {}
        self.fail_actions = set(fail_actions)
        self.fail_restore = fail_restore
        self.mismatch_restore = mismatch_restore
        self.executed = []
        self.restore_attempts = 0
        self.answers = {}

    @property
    def session(self):
        return self._session

    def discover(self, state):
        return self.candidates.get(state.title, ())

    def execute(self, candidate):
        self.executed.append(candidate.action)
        if candidate.action in self.fail_actions:
            raise RuntimeError("injected action failure")
        target = candidate.params.get("to", "B")
        self._session = make_session(target, modal=candidate.params.get("modal", False))
        return {"target": target}

    def restore(self, baseline):
        self.restore_attempts += 1
        if self.fail_restore:
            raise RuntimeError("injected restore failure")
        self._session = (
            make_session("C")
            if self.mismatch_restore
            else self.by_title.get(baseline.title, make_session(baseline.title))
        )

    def reach(self, state):
        self._session = self.by_title.get(state.title, make_session(state.title))
        return True

    def apply_user_input(self, item, answer):
        self.answers[item.resume_token] = answer
        self.candidates["A"] = [
            TraversalCandidate(
                action="select_tab", side_effect=SideEffectClass.NAVIGATION, params={"to": "B"}
            )
        ]
        return True


def policy(mode="reversible", **budget):
    return TraversalPolicy(
        mode=mode,
        budget=TraversalBudget(**budget),
        allowed_side_effects={
            SideEffectClass.READ_ONLY,
            SideEffectClass.REVERSIBLE,
            SideEffectClass.NAVIGATION,
        }
        if mode != "passive"
        else {SideEffectClass.READ_ONLY},
    )


def manifest():
    return MapManifest(map_id="test-map", tcode="ZMAP", capture_mode="reversible")


def test_passive_only_scans_and_never_discovers_or_executes():
    adapter = Adapter({"A": [TraversalCandidate(action="select_tab")]})
    result = TraversalEngine(policy("passive")).run(adapter, manifest=manifest())
    assert result.stop_reason == "passive_complete"
    assert len(result.ui_map.states) == 1
    assert result.ui_map.transitions == []
    assert adapter.executed == []


def test_max_depth_stops_expansion_but_keeps_discovered_boundary_state():
    adapter = Adapter(
        {
            "A": [TraversalCandidate(action="select_tab", params={"to": "B"})],
            "B": [TraversalCandidate(action="expand_node", params={"to": "C"})],
        }
    )
    result = TraversalEngine(policy(max_depth=1)).run(adapter, manifest=manifest())
    assert len(result.ui_map.states) == 2
    assert adapter.executed == ["select_tab"]
    assert max(result.checkpoint.depths.values()) == 1


def test_cross_transaction_discovery_is_not_added_to_the_current_tcode_map():
    class CrossTransactionAdapter(Adapter):
        def execute(self, candidate):
            self.executed.append(candidate.action)
            self._session = self.by_title["B"]
            return {"target": "B"}

    adapter = CrossTransactionAdapter(
        {"A": [TraversalCandidate(action="select_tab", params={"to": "B"})]}
    )
    adapter.by_title["B"].Info.Transaction = "SE16"
    result = TraversalEngine(policy()).run(adapter, manifest=manifest())
    assert {state.tcode for state in result.ui_map.states.values()} == {"ZMAP"}
    assert result.stop_reason == "cross_transaction_transition"
    assert any(
        item["reason"] == "cross_transaction_transition"
        for item in result.ui_map.manifest.unreachable
    )


def test_reversible_traversal_restores_each_action_and_avoids_fingerprint_loops():
    adapter = Adapter(
        {
            "A": [TraversalCandidate(action="select_tab", params={"to": "B"})],
            "B": [TraversalCandidate(action="select_tab", params={"to": "A"})],
        }
    )
    result = TraversalEngine(policy()).run(adapter, manifest=manifest())
    assert len(result.ui_map.states) == 2
    assert len(result.ui_map.transitions) == 2
    assert adapter.restore_attempts == 2
    assert adapter.executed == ["select_tab", "select_tab"]


def test_forbidden_side_effects_execute_zero_times_even_when_adapter_proposes_them():
    candidates = [
        TraversalCandidate(action="read", side_effect=SideEffectClass.READ_ONLY),
        TraversalCandidate(action="save", side_effect=SideEffectClass.COMMIT),
        TraversalCandidate(action="delete", side_effect=SideEffectClass.DESTRUCTIVE),
        TraversalCandidate(action="send", side_effect=SideEffectClass.EXTERNAL),
    ]
    adapter = Adapter({"A": candidates})
    TraversalEngine(policy()).run(adapter, manifest=manifest())
    assert adapter.executed == ["read"]
    assert not ({"save", "delete", "send"} & set(adapter.executed))


def test_reversible_false_and_dangerous_target_metadata_are_blocked():
    adapter = Adapter(
        {
            "A": [
                TraversalCandidate(action="select_tab", reversible=False),
                TraversalCandidate(
                    action="open_detail",
                    side_effect=SideEffectClass.READ_ONLY,
                ),
                TraversalCandidate(
                    action="press",
                    target_locator={"text": "Save document"},
                ),
                TraversalCandidate(
                    action="press-safe-name",
                    params={"function_code": "&POST"},
                ),
                TraversalCandidate(action="inspect", side_effect=SideEffectClass.READ_ONLY),
            ]
        }
    )
    TraversalEngine(policy()).run(adapter, manifest=manifest())
    assert adapter.executed == ["inspect"]


def test_allowlisted_requires_explicit_action_and_page_node_budgets_checkpoint():
    p = policy("allowlisted", max_pages_per_collection=1, max_nodes_per_tree=2)
    p.allowed_actions = {"scroll-to"}
    adapter = Adapter(
        {
            "A": [
                TraversalCandidate(action="not-listed", page_cost=1),
                TraversalCandidate(action="scroll_to", page_cost=1, node_cost=2, params={"to": "B"}),
            ],
            "B": [TraversalCandidate(action="scroll_to", page_cost=1, params={"to": "C"})],
        }
    )
    result = TraversalEngine(p).run(adapter, manifest=manifest())
    assert adapter.executed == ["scroll_to"]
    assert result.stop_reason == "max_pages_per_collection"
    restored = TraversalCheckpoint.from_json(result.checkpoint.to_json())
    assert restored.pages_visited == 1
    assert restored.nodes_visited == 2
    assert len(restored.ui_map.states) == 2


def test_exact_allowlisted_navigation_branch_records_target_and_restores_baseline():
    initial = make_session("A")
    initial_state = TraversalEngine(policy("passive")).scanner.scan(
        initial, tcode="ZMAP"
    )
    rule = TraversalBranchRule(
        action="press",
        source_state_id=initial_state.state_id,
        target_logical_id="detail",
        target_sap_id="wnd[0]/usr/btnDETAIL",
        component_type="GuiButton",
        restore_vkey=3,
        expected_program="ZPROGRAM",
        expected_screen_number=200,
    )
    p = policy("allowlisted")
    p.allowed_actions = {"press"}
    p.allowed_branches = [rule]
    adapter = Adapter(
        {
            "A": [
                TraversalCandidate(
                    action="press",
                    target_logical_id="detail",
                    target_locator={
                        "sap_id": "wnd[0]/usr/btnDETAIL",
                        "component_type": "GuiButton",
                        "branch_rule_id": rule.rule_id,
                    },
                    params={
                        "to": "B",
                        "branch_rule_id": rule.rule_id,
                        "restore_vkey": 3,
                    },
                    side_effect=SideEffectClass.NAVIGATION,
                    reversible=False,
                    expected_program="ZPROGRAM",
                    expected_screen_number=200,
                )
            ]
        }
    )

    result = TraversalEngine(p).run(adapter, manifest=manifest())

    assert result.stop_reason == "complete"
    assert adapter.executed == ["press"]
    assert adapter.restore_attempts == 1
    assert {item.title for item in result.ui_map.states.values()} == {"A", "B"}
    assert result.ui_map.transitions[0].action == "press"
    assert result.ui_map.transitions[0].evidence[-1] == {
        "baseline_restored": True
    }


def test_engine_rejects_branch_candidate_that_does_not_match_exact_rule() -> None:
    initial_state = TraversalEngine(policy("passive")).scanner.scan(
        make_session("A"), tcode="ZMAP"
    )
    rule = TraversalBranchRule(
        action="press",
        source_state_id=initial_state.state_id,
        target_logical_id="detail",
        target_sap_id="wnd[0]/usr/btnDETAIL",
        component_type="GuiButton",
        restore_vkey=3,
    )
    p = policy("allowlisted")
    p.allowed_actions = {"press"}
    p.allowed_branches = [rule]
    adapter = Adapter(
        {
            "A": [
                TraversalCandidate(
                    action="press",
                    target_logical_id="other-button",
                    target_locator={
                        "sap_id": "wnd[0]/usr/btnOTHER",
                        "component_type": "GuiButton",
                        "branch_rule_id": rule.rule_id,
                    },
                    params={
                        "to": "B",
                        "branch_rule_id": rule.rule_id,
                        "restore_vkey": 3,
                    },
                    side_effect=SideEffectClass.NAVIGATION,
                    reversible=False,
                )
            ]
        }
    )

    result = TraversalEngine(p).run(adapter, manifest=manifest())

    assert result.stop_reason == "complete"
    assert adapter.executed == []
    assert len(result.ui_map.states) == 1
    assert result.ui_map.transitions == []


@pytest.mark.parametrize("fail_restore", [False, True])
def test_injected_action_failure_always_attempts_restore(fail_restore):
    adapter = Adapter(
        {"A": [TraversalCandidate(action="select_tab", params={"to": "B"})]},
        fail_actions={"select_tab"},
        fail_restore=fail_restore,
    )
    result = TraversalEngine(policy()).run(adapter, manifest=manifest())
    assert adapter.restore_attempts == 1
    reasons = {item["reason"] for item in result.ui_map.manifest.unreachable}
    assert "action_failed" in reasons
    if fail_restore:
        assert "restore_failed" in reasons
    assert result.ui_map.transitions[0].to_state_id == ""
    serialized = result.ui_map.model_dump_json()
    assert "message" not in serialized
    assert "injected" not in serialized


def test_restore_mismatch_stops_immediately_and_discards_discovered_state():
    adapter = Adapter(
        {
            "A": [
                TraversalCandidate(action="select_tab", params={"to": "B"}),
                TraversalCandidate(action="expand_node", params={"to": "C"}),
            ]
        },
        mismatch_restore=True,
    )
    result = TraversalEngine(policy()).run(adapter, manifest=manifest())
    assert result.stop_reason == "baseline_restore_failed"
    assert adapter.executed == ["select_tab"]
    assert len(result.ui_map.states) == 1
    assert result.ui_map.transitions[0].to_state_id == ""
    assert result.ui_map.manifest.unreachable[-1]["reason"] == "baseline_fingerprint_mismatch"


def test_dirty_checkpoint_failure_prevents_execute():
    calls = []

    def failing_store(checkpoint):
        calls.append(checkpoint)
        raise OSError("disk full")

    adapter = Adapter({"A": [TraversalCandidate(action="select_tab", params={"to": "B"})]})
    result = TraversalEngine(policy(), checkpoint_callback=failing_store).run(
        adapter, manifest=manifest()
    )
    assert result.stop_reason == "checkpoint_write_failed"
    assert adapter.executed == []
    assert calls[0].in_flight["dirty"] is True
    assert calls[0].in_flight["candidate"]["action"] == "select_tab"


def test_transition_id_is_deterministic_and_checkpoint_marks_write_ahead_dirty():
    checkpoints = []
    candidate = TraversalCandidate(action="select_tab", params={"to": "B"})
    adapter = Adapter({"A": [candidate]})
    result = TraversalEngine(policy(), checkpoint_callback=checkpoints.append).run(
        adapter, manifest=manifest()
    )
    dirty = next(item for item in checkpoints if item.in_flight)
    assert dirty.in_flight["transition_id"] == result.ui_map.transitions[0].transition_id
    assert TraversalEngine._transition_id(
        result.ui_map.transitions[0].from_state_id, candidate
    ) == result.ui_map.transitions[0].transition_id


def test_unexpected_modal_is_saved_as_evidence_then_stops_traversal():
    adapter = Adapter(
        {"A": [TraversalCandidate(action="select_tab", params={"to": "LOGIN", "modal": True})]}
    )
    result = TraversalEngine(policy()).run(adapter, manifest=manifest())
    assert result.stop_reason == "unexpected_modal"
    assert len(result.ui_map.states) == 2
    assert result.ui_map.transitions[0].to_state_id
    assert result.ui_map.manifest.unreachable[-1]["reason"] == "unexpected_modal"


def test_existing_modal_with_changed_meaning_is_unexpected():
    adapter = Adapter(
        {"A": [TraversalCandidate(action="select_tab", params={"to": "B", "modal": True})]}
    )
    adapter._session = make_session("A", modal=True)
    adapter.by_title["A"] = adapter._session
    result = TraversalEngine(policy()).run(adapter, manifest=manifest())
    assert any(
        item["reason"] == "unexpected_modal"
        for item in result.ui_map.manifest.unreachable
    )
    assert len(result.ui_map.states) == 2


def test_needs_user_input_checkpoint_resume_and_incremental_merge_preserve_graph():
    blocker = NeedsUserInput(
        question="회사 코드를 선택해 주세요.",
        reason="후보가 두 개입니다.",
        current_state="",
        required_fields=["company_code"],
        choices=[{"value": "1000"}, {"value": "2000"}],
        resume_token="company-code-1",
    )
    adapter = Adapter(
        {"A": [TraversalCandidate(action="choose-company", needs_user_input=blocker)]}
    )
    engine = TraversalEngine(policy())
    paused = engine.run(adapter, manifest=manifest())
    assert paused.stop_reason == "needs_user_input"
    assert paused.checkpoint.needs_user_input[0].required_fields == ["company_code"]
    assert paused.ui_map.pending_interactions[0].status == "pending"
    serialized = paused.checkpoint.to_json()

    resumed = engine.run(
        adapter,
        checkpoint=TraversalCheckpoint.from_json(serialized),
        answers={"company-code-1": {"company_code": "1000"}},
    )
    assert adapter.answers["company-code-1"] == {"company_code": "1000"}
    assert len(resumed.ui_map.states) == 2
    assert len(resumed.ui_map.transitions) == 1
    assert resumed.ui_map.pending_interactions[0].status == "resolved"
    assert resumed.ui_map.pending_interactions[0].provenance == {
        "answer_source": "user",
        "answered_fields": ["company_code"],
        "answer_value_stored": False,
    }
    assert set(paused.ui_map.states) <= set(resumed.ui_map.states)

    merged = TraversalEngine.merge(paused.ui_map, resumed.ui_map)
    assert set(merged.states) == set(resumed.ui_map.states)
    assert len(merged.transitions) == 1


def test_production_adapter_pauses_once_and_requires_manual_screen_delta():
    session = make_session_with_unsafe_button()
    adapter = SapTraversalAdapter(session)
    engine = TraversalEngine(policy())

    paused = engine.run(adapter, manifest=manifest())

    assert paused.stop_reason == "needs_user_input"
    assert len(paused.checkpoint.needs_user_input) == 1
    request = paused.checkpoint.needs_user_input[0]
    assert request.scope["branch_count"] == 1
    assert "BUSINESS-SECRET" not in request.model_dump_json()

    unchanged = engine.run(
        adapter,
        checkpoint=paused.checkpoint,
        answers={request.resume_token: {"continue": True}},
    )
    assert unchanged.stop_reason == "needs_user_input"
    assert unchanged.ui_map.transitions == []

    session.ActiveWindow.Text = "B"
    session.Info.ScreenNumber = 200
    session.ActiveWindow.Children = Collection()
    resumed = engine.run(
        adapter,
        checkpoint=unchanged.checkpoint,
        answers={request.resume_token: {"continue": True}},
    )

    assert resumed.stop_reason == "complete"
    assert {state.title for state in resumed.ui_map.states.values()} == {"A", "B"}
    assert [item.action for item in resumed.ui_map.transitions] == [
        "user_guided_navigation"
    ]


def test_manual_branch_route_teaching_becomes_replayable_without_augmentation_only_provenance():
    session = make_session_with_unsafe_button()
    adapter = SapTraversalAdapter(session)
    engine = TraversalEngine(policy())
    paused = engine.run(adapter, manifest=manifest())
    request = paused.checkpoint.needs_user_input[0]
    source = paused.ui_map.states[request.current_state_id]
    button = next(item for item in source.components if item.type == "GuiButton")

    session.ActiveWindow.Text = "B"
    session.Info.ScreenNumber = 200
    session.ActiveWindow.Children = Collection()
    resumed = engine.run(
        adapter,
        checkpoint=paused.checkpoint,
        answers={
            request.resume_token: {
                "continue": True,
                "transition": {"action": "press", "target_logical_id": button.logical_id},
            }
        },
    )

    taught = next(item for item in resumed.ui_map.transitions if item.action == "press")
    assert taught.evidence[0]["source"] == "user_taught_transition"
    assert taught.target_locator["sap_id"] == button.sap_id


def test_manual_user_navigation_is_scanned_and_added_as_provenanced_delta():
    blocker = NeedsUserInput(
        question="누락된 상세 화면으로 직접 이동한 뒤 계속해 주세요.",
        reason="자동 탐색으로 안전하게 열 수 없는 화면입니다.",
        resume_token="manual-detail-1",
        scope={"actions": ["inspect"]},
    )

    class ManualAdapter(Adapter):
        def apply_user_input(self, item, answer):
            self.answers[item.resume_token] = answer
            self._session = self.by_title["B"]
            self.candidates["A"] = []
            self.candidates["B"] = []
            return True

    adapter = ManualAdapter(
        {"A": [TraversalCandidate(action="manual", needs_user_input=blocker)]}
    )
    engine = TraversalEngine(policy())
    paused = engine.run(adapter, manifest=manifest())
    resumed = engine.run(
        adapter,
        checkpoint=paused.checkpoint,
        answers={"manual-detail-1": {"continue": True}},
    )

    assert {state.title for state in resumed.ui_map.states.values()} == {"A", "B"}
    guided = next(
        transition
        for transition in resumed.ui_map.transitions
        if transition.action == "user_guided_navigation"
    )
    assert guided.evidence[0]["source"] == "user"
    assert guided.evidence[0]["resume_token"] == "manual-detail-1"
    assert resumed.checkpoint.traversal_scope == {"actions": ["inspect"]}


def test_multiple_user_guided_resumes_keep_extending_the_same_map():
    first = NeedsUserInput(
        question="첫 번째 누락 화면으로 직접 이동한 뒤 계속해 주세요.",
        reason="자동 탐색으로 안전하게 열 수 없는 화면입니다.",
        resume_token="manual-screen-b",
    )
    second = NeedsUserInput(
        question="두 번째 누락 화면으로 직접 이동한 뒤 계속해 주세요.",
        reason="사용자가 추가로 지정한 화면입니다.",
        resume_token="manual-screen-c",
    )

    class RepeatedManualAdapter(Adapter):
        def apply_user_input(self, item, answer):
            self.answers[item.resume_token] = answer
            if item.resume_token == "manual-screen-b":
                self._session = self.by_title["B"]
                self.candidates["A"] = []
                self.candidates["B"] = [
                    TraversalCandidate(action="manual", needs_user_input=second)
                ]
            else:
                self._session = self.by_title["C"]
                self.candidates["B"] = []
                self.candidates["C"] = []
            return True

    adapter = RepeatedManualAdapter(
        {"A": [TraversalCandidate(action="manual", needs_user_input=first)]}
    )
    engine = TraversalEngine(policy())

    first_pause = engine.run(adapter, manifest=manifest())
    second_pause = engine.run(
        adapter,
        checkpoint=TraversalCheckpoint.from_json(first_pause.checkpoint.to_json()),
        answers={"manual-screen-b": {"continue": True}},
    )
    completed = engine.run(
        adapter,
        checkpoint=TraversalCheckpoint.from_json(second_pause.checkpoint.to_json()),
        answers={"manual-screen-c": {"continue": True}},
    )

    assert first_pause.stop_reason == "needs_user_input"
    assert second_pause.stop_reason == "needs_user_input"
    assert completed.stop_reason == "complete"
    assert {state.title for state in completed.ui_map.states.values()} == {"A", "B", "C"}
    guided = [
        transition
        for transition in completed.ui_map.transitions
        if transition.action == "user_guided_navigation"
    ]
    assert len(guided) == 2
    assert {item.evidence[0]["resume_token"] for item in guided} == {
        "manual-screen-b",
        "manual-screen-c",
    }
    assert {
        item.resume_token: item.status
        for item in completed.ui_map.pending_interactions
    } == {"manual-screen-b": "resolved", "manual-screen-c": "resolved"}
    assert set(first_pause.ui_map.states) <= set(second_pause.ui_map.states)
    assert set(second_pause.ui_map.states) <= set(completed.ui_map.states)


def test_user_scope_limits_resume_to_requested_action_only():
    blocker = NeedsUserInput(
        question="어느 분기만 추가할까요?",
        resume_token="branch-1",
        scope={"actions": ["select_tab"]},
    )

    class ScopedAdapter(Adapter):
        def apply_user_input(self, item, answer):
            self.answers[item.resume_token] = answer
            self.candidates["A"] = [
                TraversalCandidate(action="expand_node", params={"to": "C"}),
                TraversalCandidate(action="select_tab", params={"to": "B"}),
            ]
            return True

    adapter = ScopedAdapter(
        {"A": [TraversalCandidate(action="choose-branch", needs_user_input=blocker)]}
    )
    engine = TraversalEngine(policy())
    paused = engine.run(adapter, manifest=manifest())
    resumed = engine.run(
        adapter,
        checkpoint=paused.checkpoint,
        answers={"branch-1": "select_tab"},
    )

    assert adapter.executed == ["select_tab"]
    assert {state.title for state in resumed.ui_map.states.values()} == {"A", "B"}
