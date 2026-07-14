from __future__ import annotations

import json

import pytest

from sap_cli.dispatchers.registry import COMMAND_REGISTRY
from sap_cli.macro.compiler import MacroCompileError, compile_macro
from sap_cli.macro.schema import Macro, MacroStep, MacroVariable
from sap_cli.ontology.schema import ActionType
from sap_cli.ui_map.schema import SideEffectClass


def _macro(*steps: MacroStep, tcode: str = "") -> Macro:
    return Macro(id="m1", name="compiled macro", tcode=tcode, steps=list(steps))


def _step(
    action: ActionType,
    *,
    number: int = 1,
    target: str = "wnd[0]/usr/control",
    value: str | None = None,
    description: str = "",
    params: dict | None = None,
) -> MacroStep:
    return MacroStep(
        step_number=number,
        action=action,
        target_id=target,
        value=value,
        description=description,
        params=params or {},
    )


def test_compile_macro_adds_synthetic_tcode_and_strictly_binds_nested_values():
    macro = Macro(
        id="vars",
        name="vars",
        tcode="${tcode}",
        variables=[
            MacroVariable(name="tcode"),
            MacroVariable(name="material"),
            MacroVariable(name="column", default="MATNR"),
        ],
        steps=[
            _step(
                ActionType.SET_FIELD_VALUE,
                target="wnd[0]/usr/ctxt${column}",
                value="MAT-${material}",
            ),
            _step(
                ActionType.PERFORM_UI_ACTION,
                number=2,
                target="wnd[0]/usr/grid",
                params={
                    "ui_action": "set_current_cell",
                    "row": 2,
                    "column": "${column}",
                },
            ),
        ],
    )

    compiled = compile_macro(
        macro,
        {"tcode": "va01", "material": "100-100"},
    )

    assert [step.command for step in compiled.steps] == [
        "run",
        "set",
        "ui_set_current_cell",
    ]
    assert compiled.steps[0].synthetic is True
    assert compiled.steps[0].params == {"tcode": "VA01"}
    assert compiled.steps[1].params == {
        "element_id": "wnd[0]/usr/ctxtMATNR",
        "value": "MAT-100-100",
    }
    assert compiled.steps[2].params["column"] == "MATNR"
    assert compiled.used_variables == ("column", "material", "tcode")


def test_every_action_type_maps_to_a_registered_closed_command():
    steps = [
        _step(ActionType.SET_FIELD_VALUE, number=1, value="x"),
        _step(ActionType.PRESS_BUTTON, number=2),
        _step(ActionType.SEND_VKEY, number=3, target="0"),
        _step(ActionType.RUN_TRANSACTION, number=4, target="MM03"),
        _step(ActionType.SELECT_TABLE_ROW, number=5, value="1"),
        _step(ActionType.SELECT_TABLE_CELL, number=6, params={"row": 1, "column": "A"}),
        _step(ActionType.SELECT_COMBO_ITEM, number=7, value="A"),
        _step(ActionType.TOGGLE_CHECKBOX, number=8, value="true"),
        _step(ActionType.SELECT_RADIO, number=9),
        _step(ActionType.SELECT_TAB, number=10),
        _step(ActionType.SELECT_MENU, number=11),
        _step(ActionType.READ_SCREEN, number=12, target="screen"),
        _step(ActionType.SELECT_TREE_NODE, number=13, value="N1"),
        _step(ActionType.EXPAND_TREE_NODE, number=14, value="N1"),
        _step(ActionType.DOUBLE_CLICK_TREE_NODE, number=15, value="N1"),
        _step(ActionType.COLLAPSE_TREE_NODE, number=16, value="N1"),
        _step(ActionType.CLICK_TABLE_CELL, number=17, params={"row": 1, "column": "A"}),
        _step(ActionType.DOUBLE_CLICK_TABLE_CELL, number=18, params={"row": 1, "column": "A"}),
        _step(ActionType.PERFORM_UI_ACTION, number=19, params={"ui_action": "focus"}),
    ]

    compiled = compile_macro(_macro(*steps))

    assert len(compiled.steps) == len(ActionType)
    assert {step.macro_step_number for step in compiled.steps} == set(range(1, 20))
    for step in compiled.steps:
        assert step.command in COMMAND_REGISTRY
        assert step.tool == COMMAND_REGISTRY[step.command].tool_name
        assert step.command not in {
            "macro_play",
            "recipe_replay_execute",
            "plan_execute",
        }


@pytest.mark.parametrize(
    ("step", "effect"),
    [
        (_step(ActionType.SEND_VKEY, target="11"), SideEffectClass.COMMIT),
        (_step(ActionType.PRESS_BUTTON, target="wnd[0]/tbar[0]/btn[11]"), SideEffectClass.COMMIT),
        (_step(ActionType.PRESS_BUTTON, description="Post document"), SideEffectClass.COMMIT),
        (_step(ActionType.PRESS_BUTTON, description="Delete document"), SideEffectClass.DESTRUCTIVE),
        (_step(ActionType.PRESS_BUTTON, description="Release order"), SideEffectClass.COMMIT),
        (_step(ActionType.PRESS_BUTTON, description="Send message"), SideEffectClass.COMMIT),
        (_step(ActionType.PRESS_BUTTON, description="Print output"), SideEffectClass.EXTERNAL),
        (
            _step(ActionType.PERFORM_UI_ACTION, params={"ui_action": "call_method"}),
            SideEffectClass.EXTERNAL,
        ),
        (
            _step(ActionType.PERFORM_UI_ACTION, params={"ui_action": "set_property"}),
            SideEffectClass.COMMIT,
        ),
        (
            _step(ActionType.PERFORM_UI_ACTION, params={"ui_action": "close"}),
            SideEffectClass.DESTRUCTIVE,
        ),
    ],
)
def test_commit_and_high_risk_semantics_require_approval_and_never_retry(step, effect):
    compiled_step = compile_macro(_macro(step)).steps[0]

    assert compiled_step.side_effect == effect
    assert compiled_step.approval_required is True
    assert compiled_step.commit_boundary is True
    assert compiled_step.retry == {"max_attempts": 1}
    assert compiled_step.risk in {"high", "critical"}


def test_vkey_27_is_classified_as_print_external_effect():
    step = compile_macro(
        _macro(_step(ActionType.SEND_VKEY, target="27"))
    ).steps[0]
    assert step.side_effect == SideEffectClass.EXTERNAL
    assert step.approval_required is True


def test_missing_or_transitively_unresolved_variables_are_rejected():
    macro = Macro(
        id="missing",
        name="missing",
        variables=[MacroVariable(name="material")],
        steps=[_step(ActionType.SET_FIELD_VALUE, value="${material}")],
    )

    with pytest.raises(MacroCompileError, match="missing required"):
        compile_macro(macro)
    with pytest.raises(MacroCompileError, match="unresolved"):
        compile_macro(macro, {"material": "${other}"})


def test_unknown_ui_action_and_unused_runtime_input_are_rejected_before_execution():
    with pytest.raises(MacroCompileError, match="not registered"):
        compile_macro(
            _macro(
                _step(
                    ActionType.PERFORM_UI_ACTION,
                    params={"ui_action": "not_a_real_action"},
                )
            )
        )

    with pytest.raises(MacroCompileError, match="unused macro variables"):
        compile_macro(_macro(_step(ActionType.READ_SCREEN)), {"typo": "value"})


def test_sensitive_variables_are_masked_in_default_serialization_but_revealable_to_runtime():
    secret = "s3cr3t-value"
    macro = Macro(
        id="secret",
        name="secret",
        variables=[MacroVariable(name="password")],
        steps=[
            _step(
                ActionType.SET_FIELD_VALUE,
                target="wnd[0]/usr/pwdPASSWORD",
                value="prefix-${password}",
            )
        ],
    )

    compiled = compile_macro(
        macro,
        {"password": {"value": secret, "sensitive": True}},
    )
    default_json = compiled.model_dump_json()

    assert secret not in default_json
    assert "<redacted>" in default_json
    assert compiled.steps[0].reveal_params()["value"] == f"prefix-{secret}"
    revealed = compiled.model_dump(
        mode="json", context={"reveal_sensitive": True}
    )
    assert revealed["variables"]["password"]["value"] == secret
    assert revealed["steps"][0]["params"]["value"] == f"prefix-{secret}"
    assert secret not in json.dumps(compiled.model_dump(mode="json"))


def test_expected_screen_and_wait_are_preserved_for_runtime_postconditions():
    source = MacroStep(
        step_number=1,
        action=ActionType.PRESS_BUTTON,
        target_id="wnd[0]/tbar[1]/btn[8]",
        expected_screen=200,
        wait_after=0.25,
    )
    compiled = compile_macro(_macro(source)).steps[0]
    assert compiled.expected_screen == 200
    assert compiled.wait_after == 0.25
