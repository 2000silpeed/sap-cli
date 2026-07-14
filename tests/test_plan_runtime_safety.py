from sap_cli.planning.safety import classify_runtime_step, runtime_safety_errors
from sap_cli.planning.schema import PlanStep
from sap_cli.ui_map.schema import (
    MapManifest,
    SideEffectClass,
    Transition,
    UiComponent,
    UiMap,
    UiState,
)


def mapped_step(text="저장"):
    ui_map = UiMap(
        manifest=MapManifest(map_id="m", tcode="ZTX"),
        states={
            "s": UiState(
                state_id="s",
                components=[
                    UiComponent(
                        logical_id="button",
                        sap_id="wnd[0]/tbar/btn[11]",
                        type="GuiButton",
                        text=text,
                        icon_name="ICON_SYSTEM_SAVE",
                    )
                ],
            )
        },
        transitions=[
            Transition(
                transition_id="t",
                from_state_id="s",
                action="press",
                target_logical_id="button",
            )
        ],
    )
    step = PlanStep(
        step_id="press",
        order=1,
        command="ui_press",
        tool="sap_ui_press",
        target_logical_id="button",
        params={"element_id": "wnd[0]/tbar/btn[11]"},
        map_transition_id="t",
        side_effect=SideEffectClass.NAVIGATION,
    )
    return ui_map, step


def test_saved_button_semantics_escalate_even_if_plan_claims_navigation():
    ui_map, step = mapped_step()
    safety = classify_runtime_step(step, ui_map)
    assert safety.side_effect == SideEffectClass.COMMIT
    assert safety.approval_required
    assert runtime_safety_errors(step, ui_map) == [
        "step press: runtime side-effect escalation",
        "step press: runtime approval required",
    ]


def test_vkey_11_and_official_member_risk_require_approval():
    ui_map = UiMap(manifest=MapManifest(map_id="m", tcode="ZTX"))
    save = PlanStep(
        step_id="save",
        order=1,
        command="vkey",
        tool="sap_send_vkey",
        params={"key": 11},
    )
    risky = PlanStep(
        step_id="api",
        order=1,
        command="ui_call_method",
        tool="sap_ui_call_method",
        params={},
        target_locator={"official_member_risk": "critical"},
    )
    assert classify_runtime_step(save, ui_map).side_effect == SideEffectClass.COMMIT
    assert classify_runtime_step(risky, ui_map).approval_required


def test_compiled_send_vkey_11_cannot_bypass_runtime_commit_safety():
    ui_map = UiMap(manifest=MapManifest(map_id="m", tcode="ZTX"))
    save = PlanStep(
        step_id="save",
        order=1,
        command="ui_call_method",
        tool="sap_ui_call_method",
        params={"value": "SendVKey", "method_args": [11]},
    )
    safety = classify_runtime_step(save, ui_map)
    assert safety.side_effect == SideEffectClass.COMMIT
    assert safety.approval_required
