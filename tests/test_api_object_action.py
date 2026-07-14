from __future__ import annotations

import pytest

from sap_cli.com_wrapper import SapGuiError, SapGuiWrapper


class FakeInfo:
    User = "TESTER"
    Client = "100"
    Transaction = "MM03"


class FakeWindow:
    Type = "GuiMainWindow"
    Text = "Main"


class FakeCollection:
    Count = 2

    def ElementAt(self, index: int) -> str:
        return f"item:{index}"


class FakeByRef:
    def __init__(self, _parameter_type: str, initial: object) -> None:
        self.value = initial


class FakeGuiEnum:
    def Clone(self, enum_out: FakeByRef) -> int:
        enum_out.value = FakeGuiEnum()
        return 0

    def Next(
        self, count: int, values_out: FakeByRef, fetched_out: FakeByRef
    ) -> int:
        values_out.value = tuple(f"item:{index}" for index in range(count))
        fetched_out.value = count
        return 0


class FakeNativeGuiEnum:
    _sap_cli_native_enum = True

    def Clone(self) -> "FakeNativeGuiEnum":
        return FakeNativeGuiEnum()

    def Next(self, count: int) -> tuple[str, ...]:
        return tuple(f"native:{index}" for index in range(count))


class FakeGuiCollectionWithEnum:
    Count = 2

    def __init__(self) -> None:
        self.NewEnum = FakeGuiEnum()


class FakeSession:
    def __init__(self) -> None:
        self.Info = FakeInfo()
        self.ActiveWindow = FakeWindow()
        self.SomeWritable = "old"
        self.Children = FakeCollection()

    def SendCommand(self, value: str) -> str:
        return f"sent:{value}"


def _wrapper() -> SapGuiWrapper:
    wrapper = SapGuiWrapper()
    wrapper._session = FakeSession()
    wrapper._connection = object()
    wrapper._application = object()
    wrapper._sap_gui_auto = type(
        "FakeSapGuiAuto", (), {"GetScriptingEngine": wrapper._application}
    )()
    wrapper._rot_wrapper = type(
        "FakeRotWrapper", (), {"GetROTEntry": lambda self, name: wrapper._sap_gui_auto}
    )()
    wrapper._byref_variant_factory = FakeByRef
    return wrapper


def test_api_object_scope_calls_method_and_returns_policy() -> None:
    result = _wrapper().perform_api_object_action(
        "session", "call_method", "SendCommand", method_args=["A"]
    )
    assert result["result"] == "sent:A"
    assert result["object_type"] == "GuiSession"
    assert result["member_policy"]["risk_level"] == "medium"


def test_successful_windows_api_object_call_records_canonical_live_evidence(
    tmp_path, monkeypatch
) -> None:
    from sap_cli.scripting_live_coverage import load_live_tested_members

    monkeypatch.setattr("sap_cli.com_wrapper._WIN32", True)
    monkeypatch.setenv("SAP_CLI_API_LIVE_COVERAGE_FILE", str(tmp_path / "live.json"))
    _wrapper().perform_api_object_action(
        "session", "call_method", "SendCommand", method_args=["A"]
    )
    assert "method:GuiSession.SendCommand" in load_live_tested_members()


def test_live_probe_rejects_write_or_value_bearing_members_before_execution():
    wrapper = _wrapper()
    with pytest.raises(SapGuiError, match="low-risk"):
        wrapper.probe_documented_member(
            scope="session", action="call_method", member_name="SendCommand"
        )
    with pytest.raises(SapGuiError, match="exactly one"):
        wrapper.probe_documented_member(
            scope="session", element_id="wnd[0]", member_name="Client"
        )


def test_api_object_scope_rejects_undocumented_member_before_getattr() -> None:
    with pytest.raises(SapGuiError, match="공식 API graph에 없는"):
        _wrapper().perform_api_object_action(
            "session", "call_method", "Echo", method_args=["A"]
        )


def test_api_object_scope_reads_session_info_property() -> None:
    result = _wrapper().perform_api_object_action(
        "session_info", "get_property", "Client"
    )
    assert result["value"] == "100"
    assert result["object_type"] == "GuiSessionInfo"


def test_api_object_scope_rejects_private_and_unknown_scope() -> None:
    wrapper = _wrapper()
    with pytest.raises(SapGuiError, match="public API member"):
        wrapper.perform_api_object_action("session", "get_property", "_secret")
    with pytest.raises(SapGuiError, match="scope은"):
        wrapper.perform_api_object_action("rot", "get_property", "Name")


def test_api_object_scope_rejects_documented_read_only_property() -> None:
    with pytest.raises(SapGuiError, match="읽기 전용"):
        _wrapper().perform_api_object_action(
            "session_info", "set_property", "Client", property_value="200"
        )


def test_api_object_return_uses_reusable_slot_local_handle() -> None:
    wrapper = _wrapper()
    children = wrapper.perform_api_object_action(
        "session", "get_property", "Children"
    )["value"]

    assert children["kind"] == "collection_handle"
    assert children["object_type"] == "GuiComponentCollection"
    assert children["count"] == 2

    count = wrapper.perform_api_object_action(
        f"handle:{children['handle_id']}", "get_property", "Count"
    )
    assert count["value"] == 2


def test_api_object_handle_is_invalid_after_disconnect() -> None:
    wrapper = _wrapper()
    children = wrapper.perform_api_object_action(
        "session", "get_property", "Children"
    )["value"]
    wrapper.disconnect()

    with pytest.raises(SapGuiError, match="unknown or expired"):
        wrapper.perform_api_object_action(
            f"handle:{children['handle_id']}", "get_property", "Count"
        )


def test_rot_and_sap_gui_auto_root_scopes_are_available() -> None:
    wrapper = _wrapper()
    rot_result = wrapper.perform_api_object_action(
        "rot_wrapper", "call_method", "GetROTEntry", method_args=["SAPGUI"]
    )
    assert rot_result["object_type"] == "CSapROTWrapper"
    assert rot_result["result"]["object_type"] == "SapGuiAuto"

    engine = wrapper.perform_api_object_action(
        "sap_gui_auto", "call_method", "GetScriptingEngine"
    )
    assert engine["object_type"] == "SapGuiAuto"
    assert engine["result"]["object_type"] == "GuiApplication"


@pytest.mark.parametrize(
    "collection_type",
    ["GuiCollection", "GuiComponentCollection", "GuiTableColumn", "GuiTableRow"],
)
def test_new_enum_unknown_property_returns_typed_handle(collection_type: str) -> None:
    wrapper = _wrapper()
    collection = wrapper._api_handles.register(
        FakeGuiCollectionWithEnum(), collection_type
    )

    result = wrapper.perform_api_object_action(
        f"handle:{collection.handle_id}", "get_property", "NewEnum"
    )["value"]

    assert result["kind"] == "object_handle"
    assert result["object_type"] == "GuiEnum"


def test_gui_enum_next_and_clone_marshal_byref_outputs() -> None:
    wrapper = _wrapper()
    enum = wrapper._api_handles.register(FakeGuiEnum(), "GuiEnum")
    scope = f"handle:{enum.handle_id}"

    next_result = wrapper.perform_api_object_action(
        scope, "call_method", "Next", method_args=[2]
    )["result"]
    clone_result = wrapper.perform_api_object_action(
        scope, "call_method", "Clone", method_args=[]
    )["result"]

    assert next_result == {
        "return_value": 0,
        "out_parameters": {
            "rgvar": ["item:0", "item:1"],
            "pceltFetched": 2,
        },
    }
    cloned = clone_result["out_parameters"]["ppenum"]
    assert clone_result["return_value"] == 0
    assert cloned["kind"] == "object_handle"
    assert cloned["object_type"] == "GuiEnum"


def test_gui_enum_prefers_native_ienumvariant_call_contract() -> None:
    wrapper = _wrapper()
    enum = wrapper._api_handles.register(FakeNativeGuiEnum(), "GuiEnum")
    scope = f"handle:{enum.handle_id}"

    next_result = wrapper.perform_api_object_action(
        scope, "call_method", "Next", method_args=[2]
    )["result"]
    clone_result = wrapper.perform_api_object_action(
        scope, "call_method", "Clone", method_args=[]
    )["result"]

    assert next_result["out_parameters"] == {
        "rgvar": ["native:0", "native:1"],
        "pceltFetched": 2,
    }
    assert clone_result["out_parameters"]["ppenum"]["object_type"] == "GuiEnum"


def test_method_handle_input_is_resolved_and_type_checked() -> None:
    wrapper = _wrapper()
    enum_value = FakeGuiEnum()
    enum = wrapper._api_handles.register(enum_value, "GuiEnum")
    received: list[object] = []

    result = wrapper._invoke_documented_method(
        lambda value: received.append(value) or 0,
        object_type="FixtureObject",
        method_name="AcceptEnum",
        method_args=[{"handle_id": enum.handle_id}],
        member_policy={
            "parameters": [
                {
                    "name": "value",
                    "type": "GuiEnum",
                    "optional": False,
                    "passing": "byval",
                }
            ]
        },
    )

    assert result == 0
    assert received == [enum_value]
    with pytest.raises(SapGuiError, match="handle type mismatch"):
        wrapper._invoke_documented_method(
            lambda value: value,
            object_type="FixtureObject",
            method_name="AcceptCollection",
            method_args=[f"handle:{enum.handle_id}"],
            member_policy={
                "parameters": [
                    {
                        "name": "value",
                        "type": "GuiCollection",
                        "optional": False,
                        "passing": "byval",
                    }
                ]
            },
        )
