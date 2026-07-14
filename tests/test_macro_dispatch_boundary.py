from types import SimpleNamespace

from sap_cli.dispatchers.macro import dispatch_macro_play
from sap_cli.macro.schema import Macro, MacroStep, MacroVariable
from sap_cli.ontology.schema import ActionType


class Store:
    def __init__(self, macro):
        self.macro = macro

    def resolve(self, reference):
        return self.macro if reference == self.macro.id else None


def context():
    return SimpleNamespace(
        slot_id="ses0",
        agent_name="agent",
        enforce_auth=True,
        include_post_action_context=True,
        include_screen_summary=False,
        sensitive_values=(),
        tool_name="sap_macro_play",
    )


def test_macro_tcode_and_every_step_use_central_dispatch_boundary():
    macro = Macro(
        id="m1",
        name="조회",
        tcode="MM03",
        steps=[
            MacroStep(
                step_number=1,
                action=ActionType.SET_FIELD_VALUE,
                target_id="wnd[0]/usr/ctxtMATNR",
                value="MAT-1",
            )
        ],
    )
    calls = []

    def dispatch(command, args, ctx):
        calls.append((command, args, ctx.include_post_action_context, ctx.tool_name))
        if command == "status":
            return {"type": "S", "message": "ok"}
        return {"ok": True}

    result = dispatch_macro_play(
        {"macro_id": "m1"},
        context(),
        lambda: Store(macro),
        lambda slot: object(),
        lambda payload, sap, command, ctx: payload,
        dispatch_step=dispatch,
        policy=None,
    )

    assert result["success"] is True
    assert [item[0] for item in calls] == ["run", "status", "set", "status"]
    assert all(item[2] is False and item[3] is None for item in calls)


def test_dangerous_or_unresolved_macro_is_blocked_before_any_dispatch_or_sap():
    dangerous = Macro(
        id="danger",
        name="저장",
        steps=[
            MacroStep(
                step_number=1,
                action=ActionType.PRESS_BUTTON,
                target_id="wnd[0]/tbar[0]/btn[11]",
                description="Save",
            )
        ],
    )
    missing = Macro(
        id="missing",
        name="누락",
        variables=[MacroVariable(name="material", required=True)],
        steps=[
            MacroStep(
                step_number=1,
                action=ActionType.SET_FIELD_VALUE,
                target_id="wnd[0]/usr/ctxtMATNR",
                value="${material}",
            )
        ],
    )
    calls = []
    sap_calls = []

    def run(macro):
        return dispatch_macro_play(
            {"macro_id": macro.id},
            context(),
            lambda: Store(macro),
            lambda slot: sap_calls.append(slot),
            lambda payload, sap, command, ctx: payload,
            dispatch_step=lambda *args: calls.append(args),
            policy=None,
        )

    assert run(dangerous)["stage"] == "preflight"
    assert run(missing)["stage"] == "compile"
    assert calls == []
    assert sap_calls == []


def test_macro_sensitive_variable_is_not_serialized_in_result():
    secret = "TOP-SECRET-4711"
    macro = Macro(
        id="secret",
        name="비밀",
        variables=[MacroVariable(name="password")],
        steps=[
            MacroStep(
                step_number=1,
                action=ActionType.SET_FIELD_VALUE,
                target_id="wnd[0]/usr/pwdPASSWORD",
                value="${password}",
            )
        ],
    )
    observed = []

    def dispatch(command, args, ctx):
        if command == "status":
            return {"type": "S"}
        observed.append(args.get("value"))
        return {"echo": args.get("value")}

    result = dispatch_macro_play(
        {"macro_id": "secret", "variables": {"password": secret}},
        context(),
        lambda: Store(macro),
        lambda slot: object(),
        lambda payload, sap, command, ctx: payload,
        dispatch_step=dispatch,
        policy=None,
    )

    assert observed == [secret]
    assert secret not in str(result)
