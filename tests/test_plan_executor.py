from __future__ import annotations

from dataclasses import dataclass

import pytest

from sap_cli.planning.executor import PlanExecutor
from sap_cli.planning.schema import ExecutionPlan, PlanStep
from sap_cli.ui_map.fingerprint import compute_map_fingerprint
from sap_cli.ui_map.schema import (
    MapManifest,
    SideEffectClass,
    Transition,
    UiComponent,
    UiMap,
    UiState,
)


class _Dump:
    def __init__(self, value):
        self.value = value

    def model_dump(self, mode="json"):
        return self.value


class _Sap:
    def __init__(
        self,
        *,
        system_name="DEV",
        client="100",
        language="KO",
        program="SAPMV45A",
        tab_selected=True,
    ):
        self.get_screen_calls = 0
        self.actions = []
        self.status_types = ["S"]
        self.modal = False
        self.system_name = system_name
        self.client = client
        self.language = language
        self.program = program
        self.tab_selected = tab_selected

    def wait_until_idle(self):
        return True

    def get_session_info(self):
        return _Dump(
            {
                "transaction": "VA01",
                "screen_number": "0101",
                "program": self.program,
                "system_name": self.system_name,
                "client": self.client,
                "language": self.language,
            }
        )

    def get_status(self):
        value = self.status_types.pop(0) if len(self.status_types) > 1 else self.status_types[0]
        return _Dump({"type": value, "message": "status"})

    def has_modal(self):
        return self.modal

    def get_modal_text(self):
        return "modal"

    def get_screen(self):
        self.get_screen_calls += 1
        return _Dump({"screen_number": "0101"})

    def run_transaction(self, tcode):
        self.actions.append(("run", tcode))

    def perform_ui_action(self, element_id, action, value=None, **kwargs):
        self.actions.append((action, element_id, value))
        return {"value": value}

    def get_component_property(self, element_id, property_name):
        assert property_name == "Selected"
        return {"value": self.tab_selected}

    def inspect_element(self, element_id):
        return {"id": element_id, "visible": True}

    def set_checkbox(self, element_id, value):
        self.actions.append(("checkbox", element_id, value))

    def select_radio(self, element_id):
        self.actions.append(("radio", element_id))


@dataclass
class _MapStore:
    ui_map: UiMap

    def load_version(self, map_id, version, **scope):
        self.called = (map_id, version, scope)
        return self.ui_map

    def latest_head(self, map_id, **scope):
        return {"version": self.ui_map.manifest.map_version}


@dataclass
class _GraphMapStore(_MapStore):
    """Test double that proves execution asks for plan-addressed records."""

    def __post_init__(self):
        self.slice_calls = []
        self.full_loads = 0

    def load_version(self, map_id, version, **scope):
        self.full_loads += 1
        raise AssertionError("execution must not load the full UI map")

    def load_graph_slice(self, map_id, version, **kwargs):
        self.slice_calls.append((map_id, version, kwargs))
        return self.ui_map

    def latest_head(self, map_id, **scope):
        return {
            "version": self.ui_map.manifest.map_version,
            "content_fingerprint": compute_map_fingerprint(self.ui_map),
        }


def _map(*, component_text="Material"):
    state = UiState(
        state_id="s1",
        tcode="VA01",
        program="SAPMV45A",
        screen_number=101,
        components=[
            UiComponent(
                logical_id="field",
                sap_id="wnd[0]/usr/ctxtFIELD",
                type="GuiTextField",
                text=component_text,
            )
        ],
    )
    return UiMap(
        manifest=MapManifest(
            map_id="map-1",
            tcode="VA01",
            map_version=3,
            system_id="DEV",
            client="100",
            language="KO",
        ),
        states={"s1": state},
    )


def test_targeted_subscreen_requires_positive_visibility_evidence():
    ui_map = _map()
    ui_map.states["s1"].components.append(
        UiComponent(
            logical_id="subscreen",
            sap_id="wnd[0]/usr/subSCREEN",
            type="GuiSimpleContainer",
            visible=True,
        )
    )
    ui_map.states["s1"].active_subscreens = ["subscreen"]
    sap = _Sap()
    sap.inspect_element = lambda _element_id: {"visible": False}

    result = _executor(ui_map, sap).execute(_plan(ui_map))

    assert result.status == "failed"
    assert "active SAP subscreen does not match plan UI map" in result.errors


def _step(order=1, **updates):
    values = dict(
        step_id=f"step-{order}",
        order=order,
        command="ui_set",
        tool="sap_ui_set",
        params={"element_id": f"wnd[0]/usr/ctxtF{order}", "value": "${value}"},
        expected_from_fingerprint="same-screen",
        target_logical_id="field",
        target_locator={"sap_id": "wnd[0]/usr/ctxtFIELD"},
        side_effect=SideEffectClass.NAVIGATION,
        map_id="map-1",
        map_version=3,
    )
    values.update(updates)
    return PlanStep(**values)


def _plan(ui_map, *, steps=None, variables=None, scan_policy="fast"):
    fingerprint = compute_map_fingerprint(ui_map)
    fixed_steps = [
        step.model_copy(update={"map_fingerprint": fingerprint})
        for step in (steps or [_step()])
    ]
    return ExecutionPlan(
        plan_id="plan-1",
        original_request="값 입력",
        tcode="VA01",
        map_id="map-1",
        map_scope={"system_id": "DEV", "client": "100", "language": "KO"},
        map_version=3,
        map_fingerprint=fingerprint,
        start_state_id="s1",
        goal_state_id="s1",
        goal_component_id="field",
        variables=variables or {"value": {"value": "100"}},
        scan_policy=scan_policy,
        steps=fixed_steps,
    )


def _executor(
    ui_map,
    sap,
    *,
    get_sap_calls=None,
    action=None,
    probe=None,
    auth=None,
    set_group=None,
):
    def get_sap():
        if get_sap_calls is not None:
            get_sap_calls.append(True)
        return sap

    return PlanExecutor(
        get_sap=get_sap,
        map_store=_MapStore(ui_map),
        auth_hook=auth or (lambda *args: {"allowed": True}),
        execute_action=action,
        execute_set_group=set_group,
        probe=probe,
    )


def test_execution_loads_only_plan_addressed_graph_records():
    ui_map = _map()
    store = _GraphMapStore(ui_map)
    executor = PlanExecutor(
        get_sap=lambda: _Sap(),
        map_store=store,
        auth_hook=lambda *args: {"allowed": True},
    )

    result = executor.execute(_plan(ui_map))

    assert result.status == "success"
    assert store.full_loads == 0
    assert len(store.slice_calls) == 1
    _, _, kwargs = store.slice_calls[0]
    assert kwargs["required_state_ids"] == ("s1", "s1")
    assert kwargs["required_transition_ids"] == ()
    assert kwargs["max_states"] == 1
    assert kwargs["max_depth"] == 0
    assert kwargs["neighborhood"] == 0


def test_hash_map_and_all_authorization_gates_precede_sap_access():
    ui_map = _map()
    calls = []
    plan = _plan(ui_map)
    plan.steps[0].params["value"] = "tampered"
    result = _executor(ui_map, _Sap(), get_sap_calls=calls).execute(plan)
    assert result.status == "blocked"
    assert calls == []


@pytest.mark.parametrize(
    "sap_kwargs",
    [
        {"system_name": "PRD"},
        {"client": "200"},
        {"language": "EN"},
        {"system_name": ""},
    ],
)
def test_live_session_scope_is_checked_before_first_action_even_for_run_step(
    sap_kwargs,
):
    ui_map = _map()
    sap = _Sap(**sap_kwargs)
    actions = []
    run_step = _step(
        command="run",
        tool="sap_run",
        params={"tcode": "VA01"},
        expected_from_fingerprint="",
    )

    result = _executor(
        ui_map,
        sap,
        action=lambda *args: actions.append(args),
    ).execute(_plan(ui_map, steps=[run_step]))

    assert result.status == "failed"
    assert result.errors == ["live SAP session scope does not match plan UI map"]
    assert actions == []
    assert sap.actions == []


def test_live_session_scope_read_failure_is_fail_closed_before_action():
    ui_map = _map()
    sap = _Sap()
    sap.get_session_info = lambda: (_ for _ in ()).throw(RuntimeError("offline"))
    actions = []

    result = _executor(
        ui_map,
        sap,
        action=lambda *args: actions.append(args),
    ).execute(_plan(ui_map))

    assert result.status == "failed"
    assert result.errors == ["live SAP session scope could not be verified"]
    assert actions == []


def test_initial_program_signature_blocks_same_dynpro_from_another_program():
    ui_map = _map()
    sap = _Sap(program="SAPLWRONG")
    actions = []

    result = _executor(
        ui_map,
        sap,
        action=lambda *args: actions.append(args),
    ).execute(_plan(ui_map))

    assert result.status == "failed"
    assert result.errors == ["initial SAP program does not match plan"]
    assert actions == []


def test_targeted_active_tab_signature_blocks_same_program_and_dynpro_variant():
    ui_map = _map()
    state = ui_map.states["s1"]
    state.components.append(
        UiComponent(
            logical_id="tab-details",
            sap_id="wnd[0]/usr/tabsMAIN/tabpDETAILS",
            type="GuiTab",
            selected=True,
        )
    )
    state.active_tabs = {"wnd[0]/usr/tabsMAIN": "tab-details"}
    actions = []

    result = _executor(
        ui_map,
        _Sap(tab_selected=False),
        action=lambda *args: actions.append(args),
    ).execute(_plan(ui_map))

    assert result.status == "failed"
    assert result.errors == ["active SAP tab does not match plan UI map"]
    assert actions == []


def _same_dynpro_tab_transition_map() -> UiMap:
    """Two logical states intentionally share a dynpro but select different tabs."""

    ui_map = _map()
    source = ui_map.states["s1"]
    source.components.extend(
        [
            UiComponent(
                logical_id="tab-basic",
                sap_id="wnd[0]/usr/tabsMAIN/tabpBASIC",
                type="GuiTab",
                selected=True,
            ),
            UiComponent(
                logical_id="open-details",
                sap_id="wnd[0]/usr/btnDETAILS",
                type="GuiButton",
            ),
        ]
    )
    source.active_tabs = {"wnd[0]/usr/tabsMAIN": "tab-basic"}
    target = UiState(
        state_id="s2",
        tcode="VA01",
        program="SAPMV45A",
        screen_number=101,
        components=[
            UiComponent(
                logical_id="tab-details",
                sap_id="wnd[0]/usr/tabsMAIN/tabpDETAILS",
                type="GuiTab",
                selected=True,
            ),
        ],
        active_tabs={"wnd[0]/usr/tabsMAIN": "tab-details"},
    )
    ui_map.states[target.state_id] = target
    ui_map.transitions.append(
        Transition(
            transition_id="to-details",
            from_state_id=source.state_id,
            to_state_id=target.state_id,
            action="press",
            target_logical_id="open-details",
            target_locator={"sap_id": "wnd[0]/usr/btnDETAILS"},
            side_effect=SideEffectClass.NAVIGATION,
        )
    )
    return ui_map


def test_fast_transition_verifies_target_tab_state_without_screen_scan():
    """A dynpro-identical wrong tab must not be accepted as a map transition."""

    ui_map = _same_dynpro_tab_transition_map()
    source = ui_map.states["s1"]
    target = ui_map.states["s2"]
    step = _step(
        command="ui_press",
        tool="sap_ui_press",
        target_logical_id="open-details",
        target_locator={"sap_id": "wnd[0]/usr/btnDETAILS", "type": "GuiButton"},
        params={"element_id": "wnd[0]/usr/btnDETAILS"},
        map_transition_id="to-details",
        expected_from_fingerprint=source.compute_fingerprint(),
        expected_to_fingerprint=target.compute_fingerprint(),
    )
    plan = _plan(ui_map, steps=[step])
    sap = _Sap()
    sap.get_component_property = lambda element_id, _property: {
        "value": element_id.endswith("tabpBASIC")
    }
    actions = []

    result = _executor(
        ui_map,
        sap,
        action=lambda *_args: actions.append("press") or {"ok": True},
    ).execute(plan)

    assert result.status == "failed"
    assert result.errors == ["active SAP tab does not match plan UI map"]
    assert actions == ["press"]
    assert sap.get_screen_calls == 0
    assert result.get_screen_count == 0


def test_fast_structural_anchor_mismatch_blocks_before_action_without_screen_scan():
    """A saved shell type is a bounded structural fingerprint anchor."""

    ui_map = _map()
    ui_map.states["s1"].components.append(
        UiComponent(
            logical_id="main-toolbar",
            sap_id="wnd[0]/tbar[1]",
            type="GuiToolbar",
            visible=True,
        )
    )
    sap = _Sap()
    sap.inspect_element = lambda element_id: {
        "id": element_id,
        "type": "GuiStatusbar",
        "visible": True,
    }
    actions = []

    result = _executor(
        ui_map,
        sap,
        action=lambda *_args: actions.append("action") or {"ok": True},
    ).execute(_plan(ui_map))

    assert result.status == "failed"
    assert result.errors == ["expected SAP structural state does not match plan UI map"]
    assert actions == []
    assert sap.get_screen_calls == 0
    assert result.get_screen_count == 0


def test_transition_expected_fingerprint_mismatch_blocks_before_sap_access():
    """The graph edge contract must agree with the immutable state records."""

    ui_map = _same_dynpro_tab_transition_map()
    source = ui_map.states["s1"]
    step = _step(
        command="ui_press",
        tool="sap_ui_press",
        target_logical_id="open-details",
        target_locator={"sap_id": "wnd[0]/usr/btnDETAILS", "type": "GuiButton"},
        params={"element_id": "wnd[0]/usr/btnDETAILS"},
        map_transition_id="to-details",
        expected_from_fingerprint=source.compute_fingerprint(),
        expected_to_fingerprint="0" * 64,
    )
    accesses = []

    result = _executor(
        ui_map, _Sap(), get_sap_calls=accesses
    ).execute(_plan(ui_map, steps=[step]))

    assert result.status == "blocked"
    assert result.errors == [
        "step step-1: expected target state fingerprint does not match UI map"
    ]
    assert accesses == []


def test_final_goal_component_must_remain_visible_without_full_rescan():
    ui_map = _map()
    sap = _Sap()
    sap.inspect_element = lambda _element_id: {"visible": False}

    result = _executor(ui_map, sap).execute(_plan(ui_map))

    assert result.status == "failed"
    assert result.errors == ["goal SAP component does not match plan UI map"]
    assert sap.get_screen_calls == 0


def test_newer_ui_map_blocks_stale_plan_before_sap_access():
    ui_map = _map()
    store = _MapStore(ui_map)
    calls = []

    def latest_head(map_id, **scope):
        return {"version": 4}

    store.latest_head = latest_head
    executor = PlanExecutor(
        get_sap=lambda: calls.append(True),
        map_store=store,
        auth_hook=lambda *args: {"allowed": True},
    )
    result = executor.execute(_plan(ui_map))

    assert result.status == "blocked"
    assert result.errors == ["stale plan: a newer UI map version is available"]
    assert calls == []

    changed_map = _map()
    changed_map.manifest.api_version = "different"
    result = _executor(changed_map, _Sap(), get_sap_calls=calls).execute(
        _plan(ui_map)
    )
    assert result.status == "blocked"
    assert "UI map fingerprint mismatch" in result.errors
    assert calls == []

    denied = _plan(ui_map)
    result = _executor(
        ui_map,
        _Sap(),
        get_sap_calls=calls,
        auth=lambda *args: {"allowed": False},
    ).execute(denied)
    assert result.status == "blocked"
    assert calls == []


def test_approval_grants_are_checked_before_sap_access():
    ui_map = _map()
    calls = []
    step = _step(
        command="ui_press",
        tool="sap_ui_press",
        params={"element_id": "wnd[0]/tbar[0]/btn[11]"},
        side_effect=SideEffectClass.COMMIT,
        approval_required=True,
        commit_boundary=True,
    )
    plan = _plan(ui_map, steps=[step])
    result = _executor(ui_map, _Sap(), get_sap_calls=calls).execute(plan)
    assert result.status == "blocked"
    assert calls == []


def test_current_tcode_without_run_step_is_authorized_before_sap_access():
    ui_map = _map()
    calls = []

    def auth(command, tool, params, step):
        if tool == "sap_run":
            return {"allowed": False}
        return {"allowed": True}

    result = _executor(
        ui_map, _Sap(), get_sap_calls=calls, auth=auth
    ).execute(_plan(ui_map))

    assert result.status == "blocked"
    assert result.errors == ["transaction authorization denied"]
    assert calls == []


def test_runtime_semantic_safety_is_aggregated_before_sap_access():
    ui_map = _map(component_text="Save document")
    calls = []
    step = _step(
        command="ui_press",
        tool="sap_ui_press",
        params={"element_id": "wnd[0]/tbar[0]/btn[11]"},
        target_locator={
            "sap_id": "wnd[0]/tbar[0]/btn[11]",
            "text": "Save document",
        },
    )
    result = _executor(ui_map, _Sap(), get_sap_calls=calls).execute(
        _plan(ui_map, steps=[step])
    )
    assert result.status == "blocked"
    assert any("runtime" in error for error in result.errors)
    assert calls == []


def test_same_fingerprint_ui_sets_have_no_intermediate_probe():
    ui_map = _map()
    sap = _Sap()
    probe_calls = []
    action_calls = []

    def probe(_sap, full):
        probe_calls.append(full)
        return {"status": {"type": "S"}, "modal": {"has_modal": False}}

    group_calls = []

    def set_group(grouped, _sap):
        group_calls.append([step.step_id for step, _ in grouped])
        for step, params in grouped:
            action_calls.append((step.command, step.step_id, params["value"]))
        return [{"ok": True} for _ in grouped]

    steps = [_step(1), _step(2)]
    result = _executor(ui_map, sap, probe=probe, set_group=set_group).execute(
        _plan(ui_map, steps=steps)
    )
    assert result.status == "success"
    assert action_calls == [("ui_set", "step-1", "100"), ("ui_set", "step-2", "100")]
    assert group_calls == [["step-1", "step-2"]]
    assert probe_calls == [False, False]


def test_set_group_uses_saved_component_type_not_fragile_sap_id_substrings():
    from sap_cli.planning.executor import _default_execute_set_group

    sap = _Sap()
    checkbox = _step(
        1,
        params={"element_id": "wnd[0]/usr/custom-toggle", "value": "true"},
        target_locator={"type": "GuiCheckBox"},
    )
    radio = _step(
        2,
        params={"element_id": "wnd[0]/usr/custom-choice", "value": "X"},
        target_locator={"component_type": "GuiRadioButton"},
    )
    _default_execute_set_group([(checkbox, checkbox.params), (radio, radio.params)], sap)
    assert sap.actions == [
        ("checkbox", "wnd[0]/usr/custom-toggle", True),
        ("radio", "wnd[0]/usr/custom-choice"),
    ]


def test_cheap_never_scans_and_full_scans_once():
    ui_map = _map()
    cheap_sap = _Sap()
    cheap = _executor(ui_map, cheap_sap).execute(_plan(ui_map))
    assert cheap.status == "success"
    assert cheap_sap.get_screen_calls == 0

    full_sap = _Sap()
    full = _executor(ui_map, full_sap).execute(_plan(ui_map), verification="full")
    assert full.status == "success"
    assert full_sap.get_screen_calls == 1
    assert full.get_screen_count == 1


@pytest.mark.parametrize("status_type", ["E", "A"])
def test_roundtrip_error_status_stops_before_next_step(status_type):
    ui_map = _map()
    sap = _Sap()
    sap.status_types = ["S", status_type]
    calls = []

    def action(command, params, _sap, step):
        calls.append(step.step_id)
        return {}

    steps = [
        _step(
            1,
            command="run",
            tool="sap_run",
            params={"tcode": "VA01"},
            expected_from_fingerprint="",
        ),
        _step(2),
    ]
    result = _executor(ui_map, sap, action=action).execute(_plan(ui_map, steps=steps))
    assert result.status == "failed"
    assert calls == ["step-1"]
    assert result.traces[0].attempts == 1


def test_unexpected_modal_stops_immediately():
    ui_map = _map()
    sap = _Sap()
    probe_count = 0
    calls = []

    def probe(_sap, full):
        nonlocal probe_count
        probe_count += 1
        return {
            "status": {"type": "S"},
            "modal": {"has_modal": probe_count > 1, "text": "unexpected"},
        }

    def action(command, params, _sap, step):
        calls.append(step.step_id)
        return {}

    steps = [
        _step(1, command="run", tool="sap_run", params={"tcode": "VA01"}),
        _step(2),
    ]
    result = _executor(ui_map, sap, action=action, probe=probe).execute(
        _plan(ui_map, steps=steps)
    )
    assert result.status == "failed"
    assert result.errors == ["unexpected SAP modal"]
    assert calls == ["step-1"]


def test_expected_modal_requires_exact_observed_signature():
    ui_map = _map()

    def probe(_sap, full):
        return {
            "status": {"type": "S"},
            "modal": {"has_modal": True},
        }

    step = _step(
        command="ui_press",
        tool="sap_ui_press",
        params={"element_id": "wnd[0]/usr/btnOPEN"},
        modal={"signature": "GuiModalWindow:wnd[1]"},
    )
    result = _executor(ui_map, _Sap(), probe=probe).execute(
        _plan(ui_map, steps=[step])
    )

    assert result.status == "failed"
    assert result.errors == ["unexpected SAP modal"]


def test_commit_action_is_attempted_only_once_even_on_failure():
    ui_map = _map()
    sap = _Sap()
    calls = []

    def fail(command, params, _sap, step):
        calls.append(step.step_id)
        raise RuntimeError("transient")

    step = _step(
        command="ui_press",
        tool="sap_ui_press",
        params={"element_id": "wnd[0]/tbar[0]/btn[11]"},
        side_effect=SideEffectClass.COMMIT,
        approval_required=True,
        commit_boundary=True,
    )
    plan = _plan(ui_map, steps=[step])
    result = _executor(ui_map, sap, action=fail).execute(
        plan, approval_grants={step.step_id}
    )
    assert result.status == "failed"
    assert result.commit_attempted
    assert calls == [step.step_id]
    assert result.traces[0].attempts == 1


def test_idempotent_action_retries_only_allowlisted_transient_com_error():
    ui_map = _map()
    calls = []

    class ComError(RuntimeError):
        hresult = 0x8001010A

    def action(command, params, _sap, step):
        calls.append(step.step_id)
        if len(calls) == 1:
            raise ComError("private business value")
        return {"ok": True}

    step = _step(idempotent=True, retry={"max_attempts": 2})
    executor = PlanExecutor(
        get_sap=lambda: _Sap(),
        map_store=_MapStore(ui_map),
        auth_hook=lambda *args: {"allowed": True},
        execute_action=action,
        sleep=lambda _delay: None,
    )
    result = executor.execute(_plan(ui_map, steps=[step]))

    assert result.status == "success"
    assert calls == ["step-1", "step-1"]
    assert result.traces[0].attempts == 2
    assert result.traces[0].failure_code == ""


def test_fast_locator_drift_never_uses_full_rescan():
    ui_map = _map()
    rescans = []
    step = _step(idempotent=True, retry={"max_attempts": 2})
    executor = PlanExecutor(
        get_sap=lambda: _Sap(),
        map_store=_MapStore(ui_map),
        auth_hook=lambda *args: {"allowed": True},
        execute_action=lambda *_args: (_ for _ in ()).throw(
            RuntimeError("FindById element not found")
        ),
        full_rescan_recovery=lambda *args: rescans.append(True),
        sleep=lambda _delay: None,
    )
    result = executor.execute(_plan(ui_map, steps=[step]))
    assert result.status == "failed"
    assert rescans == []
    assert result.get_screen_count == 0


def test_adaptive_locator_drift_uses_targeted_then_full_rescan_once_and_reauthorizes():
    ui_map = _map()
    actions = []
    targeted = []
    rescans = []
    authorized_ids = []

    def action(command, params, _sap, step):
        actions.append(params["element_id"])
        if params["element_id"] != "wnd[0]/usr/ctxtRECOVERED":
            raise RuntimeError("FindById element not found")
        return {"ok": True}

    def auth(command, tool, params, step):
        authorized_ids.append(params.get("element_id", ""))
        return {"allowed": True}

    step = _step(idempotent=True, retry={"max_attempts": 2})
    executor = PlanExecutor(
        get_sap=lambda: _Sap(),
        map_store=_MapStore(ui_map),
        auth_hook=auth,
        execute_action=action,
        targeted_locator_recovery=lambda *args: targeted.append(True),
        full_rescan_recovery=lambda *args: (
            rescans.append(True)
            or {"element_id": "wnd[0]/usr/ctxtRECOVERED", "value": "100"}
        ),
        sleep=lambda _delay: None,
    )
    result = executor.execute(_plan(ui_map, steps=[step], scan_policy="adaptive"))

    assert result.status == "success"
    assert actions == ["wnd[0]/usr/ctxtF1", "wnd[0]/usr/ctxtRECOVERED"]
    assert targeted == [True]
    assert rescans == [True]
    assert authorized_ids[-1] == "wnd[0]/usr/ctxtRECOVERED"
    assert result.traces[0].recovery["targeted_inspect"]["used"] == 1
    assert result.traces[0].recovery["full_rescan"]["used"] == 1


def test_sensitive_values_are_raw_for_action_but_redacted_from_trace_and_result():
    ui_map = _map()
    secret = "SECRET-4711"
    observed = []

    def action(command, params, sap, step):
        observed.append(params["value"])
        return {"echo": params["value"]}

    plan = _plan(
        ui_map,
        variables={"value": {"value": secret, "sensitive": True}},
    )
    result = _executor(ui_map, _Sap(), action=action).execute(plan)
    assert result.status == "success"
    assert observed == [secret]
    assert secret not in result.model_dump_json()
    assert result.traces[0].params["value"] == "***REDACTED***"
