from __future__ import annotations

from types import SimpleNamespace

from sap_cli.ui_map.recorder import MapRecorder
from sap_cli.ui_map.store import UiMapStore


class Collection:
    def __init__(self, items=()):
        self.items = list(items)
        self.Count = len(self.items)

    def __call__(self, index):
        return self.items[index]


def element(sap_id, ctype, *, children=(), **values):
    defaults = {
        "Id": sap_id,
        "Type": ctype,
        "Name": sap_id.rsplit("/", 1)[-1],
        "Text": "",
        "Tooltip": "",
        "IconName": "",
        "Visible": True,
        "Enabled": True,
        "Changeable": False,
        "Required": False,
        "Selected": False,
        "Children": Collection(children),
    }
    defaults.update(values)
    return SimpleNamespace(**defaults)


def screen(screen_number, *, extra_tab_text=""):
    tab1 = element("wnd[0]/usr/tabs/tabpTAB1", "GuiTab", Text="General")
    tab2 = element("wnd[0]/usr/tabs/tabpTAB2", "GuiTab", Text="Sales" + extra_tab_text)
    strip = element(
        "wnd[0]/usr/tabs", "GuiTabStrip", children=(tab1, tab2), SelectedTab=tab1
    )
    window = element(
        "wnd[0]", "GuiMainWindow", Text=f"screen-{screen_number}", children=(strip,)
    )
    return SimpleNamespace(
        Children=Collection([window]),
        ActiveWindow=window,
        Info=SimpleNamespace(
            User="TESTER",
            Transaction="MM03",
            Program="SAPLMGMM",
            ScreenNumber=screen_number,
            SystemName="DEV",
            Client="100",
            Language="EN",
        ),
    )


class FakeWrapper:
    """A wrapper whose live screen the test advances between actions."""

    def __init__(self):
        self._screen = screen(100)

    @property
    def session(self):
        return self._screen

    def advance(self, screen_number, **kw):
        self._screen = screen(screen_number, **kw)

    def get_session_info(self):
        return SimpleNamespace(
            model_dump=lambda mode="json": {
                "system_name": "DEV", "client": "100", "language": "EN",
                "transaction": "MM03",
            }
        )


class FakeEventManager:
    def __init__(self):
        self.events = []
        self.unsubscribed = []

    def subscribe(self, **kwargs):
        self.subscription = kwargs
        return {"subscription_id": "manual-1"}

    def poll(self, subscription_id, *, cursor=0, limit=100):
        selected = [item for item in self.events if item["sequence"] > cursor][:limit]
        return {
            "events": selected,
            "next_cursor": selected[-1]["sequence"] if selected else cursor,
            "lost_count": 0,
        }

    def unsubscribe(self, subscription_id):
        self.unsubscribed.append(subscription_id)
        return True


def test_start_records_initial_screen_and_scope(tmp_path):
    store = UiMapStore(tmp_path)
    wrapper = FakeWrapper()
    rec = MapRecorder(store, lambda slot: wrapper)

    status = rec.start(map_id="mm03-rec", tcode="mm03", slot_id="ses0")

    assert status["recording"] is True
    assert status["map_id"] == "mm03-rec"
    assert status["scope"]["client"] == "100"
    assert status["states_recorded"] == 1
    assert status["needs_user_input"] is True
    assert any(item["action"] == "select_tab" for item in status["safe_actions"])
    saved = store.latest("mm03-rec", tcode="MM03", system_id="DEV", client="100", language="EN")
    assert len(saved.states) == 1


def test_press_on_tab_records_new_state_and_replayable_transition(tmp_path):
    store = UiMapStore(tmp_path)
    wrapper = FakeWrapper()
    rec = MapRecorder(store, lambda slot: wrapper)
    rec.start(map_id="mm03-rec", tcode="mm03", slot_id="ses0")

    # Operator clicks tab2 via MCP; the screen changes.
    wrapper.advance(200, extra_tab_text=" (detail)")
    out = rec.record("press", {"element_id": "wnd[0]/usr/tabs/tabpTAB2"})

    assert out is not None
    assert out["transition"] == "select_tab"
    saved = store.latest("mm03-rec", tcode="MM03", system_id="DEV", client="100", language="EN")
    assert len(saved.states) == 2
    assert len(saved.transitions) == 1
    tr = saved.transitions[0]
    assert tr.action == "select_tab"
    assert tr.target_logical_id  # points at the pressed tab
    assert rec.states_recorded == 2
    assert rec.transitions_recorded == 1


def test_no_screen_change_records_nothing(tmp_path):
    store = UiMapStore(tmp_path)
    wrapper = FakeWrapper()
    rec = MapRecorder(store, lambda slot: wrapper)
    rec.start(map_id="mm03-rec", tcode="mm03", slot_id="ses0")

    # Same screen → nothing recorded.
    out = rec.record("press", {"element_id": "wnd[0]/usr/tabs/tabpTAB2"})
    assert out is None
    assert rec.states_recorded == 1


def test_leaving_the_map_tcode_is_not_folded_in(tmp_path):
    store = UiMapStore(tmp_path)
    wrapper = FakeWrapper()
    rec = MapRecorder(store, lambda slot: wrapper)
    rec.start(map_id="mm03-rec", tcode="mm03", slot_id="ses0")

    # A navigation that lands on a different transaction is out of scope.
    other = screen(100)
    other.Info.Transaction = "VA03"
    wrapper._screen = other
    out = rec.record("run", {"tcode": "VA03"})
    assert out is None
    assert rec.states_recorded == 1


def test_inactive_recorder_ignores_actions(tmp_path):
    store = UiMapStore(tmp_path)
    rec = MapRecorder(store, lambda slot: FakeWrapper())
    assert rec.record("press", {"element_id": "x"}) is None


def test_finish_and_cancel_reset(tmp_path):
    store = UiMapStore(tmp_path)
    rec = MapRecorder(store, lambda slot: FakeWrapper())
    rec.start(map_id="mm03-rec", tcode="mm03", slot_id="ses0")
    done = rec.finish()
    assert done["finished"] is True
    assert done["unobserved_branches"] == 2
    assert rec.active is False
    assert rec.record("press", {"element_id": "x"}) is None
    saved = store.latest(
        "mm03-rec", tcode="MM03", system_id="DEV", client="100", language="EN"
    )
    gaps = [
        item
        for item in saved.manifest.unreachable
        if item["reason"] == "recording_branch_not_observed"
    ]
    assert {item["target_logical_id"] for item in gaps} == {
        "wnd[0]/usr/tabs/tabpTAB1",
        "wnd[0]/usr/tabs/tabpTAB2",
    }


def test_recorded_branch_is_not_marked_unobserved(tmp_path):
    store = UiMapStore(tmp_path)
    wrapper = FakeWrapper()
    rec = MapRecorder(store, lambda slot: wrapper)
    rec.start(map_id="mm03-rec", tcode="MM03")
    wrapper.advance(200, extra_tab_text=" (detail)")
    rec.record("press", {"element_id": "wnd[0]/usr/tabs/tabpTAB2"})

    done = rec.finish()

    assert done["unobserved_branches"] == 3
    saved = store.latest(
        "mm03-rec", tcode="MM03", system_id="DEV", client="100", language="EN"
    )
    unresolved = [
        item
        for item in saved.manifest.unreachable
        if item.get("resolved") is not True
    ]
    assert not any(
        item.get("state_id") == next(iter(saved.states))
        and item.get("target_logical_id") == "wnd[0]/usr/tabs/tabpTAB2"
        for item in unresolved
    )


def test_partial_recording_drives_semantic_goal_inference(tmp_path):
    from sap_cli.planning.compiler import PlanCompiler

    store = UiMapStore(tmp_path)
    rec = MapRecorder(store, lambda slot: FakeWrapper())
    rec.start(map_id="mm03-partial", tcode="MM03")
    rec.finish()

    result = PlanCompiler(store).compile(
        "tcode MM03 구매 정보 조회",
        map_id="mm03-partial",
        goal_query="구매",
    )

    assert result.plan is None
    assert result.validation.errors == ["goal_semantic_inference_required"]
    question = result.validation.questions[0]
    assert question["decision_owner"] == "calling_llm"
    assert question["choices"]
    assert all(choice["branch"]["target_logical_id"] for choice in question["choices"])


def test_manual_record_consumes_hit_and_end_request(tmp_path):
    store = UiMapStore(tmp_path)
    wrapper = FakeWrapper()
    manager = FakeEventManager()
    rec = MapRecorder(store, lambda slot: wrapper, get_event_manager=lambda: manager)

    status = rec.start_manual(map_id="mm03-manual", tcode="MM03")
    assert status["mode"] == "manual"
    assert wrapper.session.ActiveWindow.ElementVisualizationMode is True

    wrapper.advance(200, extra_tab_text=" (detail)")
    manager.events = [
        {
            "sequence": 1,
            "event_name": "Hit",
            "payload": {"Component": {"id": "wnd[0]/usr/tabs/tabpTAB2"}},
        },
        {"sequence": 2, "event_name": "EndRequest", "payload": {}},
    ]
    result = rec.poll_manual()

    assert result["screens_recorded"] == 1
    assert rec.transitions_recorded == 1
    rec.finish()
    assert manager.unsubscribed == ["manual-1"]


def test_dispatch_hook_records_navigating_action_for_active_slot(tmp_path):
    # The central dispatch hook must fold a navigating action into the active
    # recorder for that slot, and never touch anything when inactive.
    from sap_cli.dispatcher import _maybe_record_map_step
    from sap_cli.dispatcher import DispatchContext
    from sap_cli.ui_map import runtime

    store = UiMapStore(tmp_path)
    wrapper = FakeWrapper()
    runtime.set_map_recorder_factory(lambda slot: MapRecorder(store, lambda s: wrapper))
    try:
        rec = runtime.get_map_recorder("ses0")
        rec.start(map_id="mm03-rec", tcode="mm03", slot_id="ses0")

        # No active recorder on a different slot → no-op.
        _maybe_record_map_step(
            "press", {"element_id": "wnd[0]/usr/tabs/tabpTAB2"},
            DispatchContext(slot_id="other"),
        )
        assert rec.states_recorded == 1

        # Active slot + navigating command → recorded.
        wrapper.advance(200, extra_tab_text=" (detail)")
        _maybe_record_map_step(
            "press", {"element_id": "wnd[0]/usr/tabs/tabpTAB2"},
            DispatchContext(slot_id="ses0"),
        )
        assert rec.states_recorded == 2

        # Non-navigating command is ignored by the hook.
        _maybe_record_map_step("screen", {}, DispatchContext(slot_id="ses0"))
        assert rec.states_recorded == 2
    finally:
        runtime.set_map_recorder_factory(None)
