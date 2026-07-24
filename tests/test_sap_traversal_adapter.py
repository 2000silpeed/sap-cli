from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from sap_cli.ui_map.sap_traversal import SapTraversalAdapter
from sap_cli.ui_map.schema import (
    CollectionDescriptor,
    CollectionNode,
    TraversalBranchRule,
    Transition,
    UiMap,
    MapManifest,
    UiComponent,
    UiState,
    UserInputRequest,
)
from sap_cli.ui_map.traversal import TraversalCandidate


class Tab:
    Type = "GuiTab"

    def __init__(self, sap_id: str, group: "TabGroup") -> None:
        self.Id = sap_id
        self.group = group
        self.select_calls = 0

    def Select(self) -> None:
        self.select_calls += 1
        self.group.selected = self.Id


class TabGroup:
    def __init__(self) -> None:
        self.selected = "tab/a"


class Scrollbar:
    def __init__(self, position: int, maximum: int, page_size: int) -> None:
        self.Position = position
        self.Maximum = maximum
        self.PageSize = page_size


class Grid:
    Type = "GuiGridView"

    def __init__(self, position: int = 7) -> None:
        self.VerticalScrollbar = Scrollbar(position, 30, 8)
        self.HorizontalScrollbar = Scrollbar(3, 20, 5)


class Tree:
    Type = "GuiShell"
    SubType = "Tree"

    def __init__(self) -> None:
        self.expanded = {"already"}
        self.expand_calls: list[str] = []
        self.collapse_calls: list[str] = []

    def IsFolderExpanded(self, key: str) -> bool:
        return key in self.expanded

    def ExpandNode(self, key: str) -> None:
        self.expand_calls.append(key)
        self.expanded.add(key)

    def CollapseNode(self, key: str) -> None:
        self.collapse_calls.append(key)
        self.expanded.discard(key)


class Session:
    def __init__(self, objects: dict[str, object]) -> None:
        self.objects = objects
        self.find_calls: list[str] = []

    def FindById(self, sap_id: str) -> object:
        self.find_calls.append(sap_id)
        return self.objects[sap_id]


def make_fixture() -> tuple[SapTraversalAdapter, UiState, TabGroup, Grid, Tree]:
    group = TabGroup()
    tab_a = Tab("tab/a", group)
    tab_b = Tab("tab/b", group)
    grid = Grid()
    tree = Tree()
    session = Session({"tab/a": tab_a, "tab/b": tab_b, "grid": grid, "tree": tree})
    state = UiState(
        tcode="ZMAP",
        active_tabs={"tabs": "tab-a"},
        components=[
            UiComponent(
                logical_id="tab-a", sap_id="tab/a", type="GuiTab",
                parent_logical_id="tabs", selected=True,
            ),
            UiComponent(
                logical_id="tab-b", sap_id="tab/b", type="GuiTab",
                parent_logical_id="tabs", selected=False,
            ),
            UiComponent(logical_id="grid", sap_id="grid", type="GuiGridView"),
            UiComponent(
                logical_id="tree", sap_id="tree", type="GuiShell", sub_type="Tree"
            ),
            # These controls must never turn into traversal candidates.
            UiComponent(
                logical_id="save", sap_id="btn/save", type="GuiButton", text="Save",
                supported_actions=["press"],
            ),
            UiComponent(
                logical_id="menu", sap_id="mbar/save", type="GuiMenu", text="Post",
                supported_actions=["select"],
            ),
        ],
        collections=[
            CollectionDescriptor(
                component_logical_id="grid",
                kind="grid",
                horizontal_position=3,
                vertical_position=7,
                toolbar_actions=[{"id": "EXPORT", "text": "Secret export label"}],
                coverage={"context_menu_actions": "not_observed_until_opened"},
            ),
            CollectionDescriptor(
                component_logical_id="tree",
                kind="tree",
                expanded_node_keys=["already"],
                nodes=[
                    CollectionNode(
                        key="new", depth=0, expandable=True, expanded=False
                    ),
                    CollectionNode(
                        key="already", depth=0, expandable=True, expanded=True
                    ),
                    CollectionNode(
                        key="leaf", depth=1, expandable=False, expanded=False
                    ),
                ],
            ),
        ],
    ).ensure_state_id()
    return SapTraversalAdapter(session), state, group, grid, tree


def candidates_by_action(adapter: SapTraversalAdapter, state: UiState):
    candidates = list(adapter.discover(state))
    return candidates, {candidate.action: candidate for candidate in candidates}


def test_required_input_emits_memory_only_question_and_applies_enter() -> None:
    class Input:
        Text = ""

    class Window:
        def __init__(self) -> None:
            self.vkeys: list[int] = []

        def SendVKey(self, key: int) -> None:
            self.vkeys.append(key)

    field = Input()
    window = Window()
    adapter = SapTraversalAdapter(Session({"field": field, "wnd[0]": window}))
    state = UiState(
        tcode="MM03",
        components=[
            UiComponent(
                logical_id="material",
                sap_id="field",
                type="GuiCTextField",
                name="MATNR",
                visible=True,
                enabled=True,
                changeable=True,
                required=True,
            )
        ],
    ).ensure_state_id()

    request = next(
        item.needs_user_input
        for item in adapter.discover(state)
        if item.action == "runtime_input"
    )
    assert request is not None
    assert request.reason == "runtime_input_required"
    assert request.provenance["answer_value_stored"] is False
    assert adapter.apply_user_input(request, {"input_1": "TEST_MATNR"}) is True
    assert field.Text == "TEST_MATNR"
    assert window.vkeys == [0]


def test_discover_emits_safe_actions_and_one_consolidated_manual_gap() -> None:
    adapter, state, _, _, _ = make_fixture()

    candidates, by_action = candidates_by_action(adapter, state)

    assert [item.action for item in candidates] == [
        "select_tab", "scroll_to", "scroll_to", "expand_node", "manual_branch_gap"
    ]
    safe = [item for item in candidates if item.needs_user_input is None]
    assert all(item.reversible for item in safe)
    assert by_action["scroll_to"].params["position"] == 15
    assert by_action["scroll_to"].params["previous_position"] == 7
    assert by_action["scroll_to"].page_cost == 1
    assert by_action["expand_node"].params == {
        "node_key": "new", "was_expanded": False
    }
    assert by_action["expand_node"].node_cost == 1
    gaps = [item.needs_user_input for item in candidates if item.needs_user_input]
    assert len(gaps) == 1
    assert gaps[0].reason == "unsafe_actionable_branches_require_user_guidance"
    assert gaps[0].scope["branch_count"] == 4
    assert gaps[0].provenance["requires_distinct_screen"] is True
    serialized = json.dumps(gaps[0].model_dump(mode="json"), ensure_ascii=False)
    assert "Secret export label" not in serialized
    assert '"Save"' not in serialized
    assert '"Post"' not in serialized


@pytest.mark.parametrize("action", ["press", "menu", "toolbar", "save", "post"])
def test_execute_has_no_generic_or_dangerous_action_escape_hatch(action: str) -> None:
    adapter, _, _, _, _ = make_fixture()
    candidate = TraversalCandidate(
        action=action,
        target_locator={"sap_id": "btn/save"},
    )

    with pytest.raises(ValueError, match="unsupported traversal action"):
        adapter.execute(candidate)


class BranchButton:
    Type = "GuiButton"

    def __init__(self, callback=None) -> None:
        self.press_calls = 0
        self.callback = callback

    def Press(self) -> None:
        self.press_calls += 1
        if self.callback is not None:
            self.callback()


class BranchWindow:
    def __init__(self, callback=None) -> None:
        self.vkeys: list[int] = []
        self.callback = callback

    def SendVKey(self, key: int) -> None:
        self.vkeys.append(key)
        if self.callback is not None:
            self.callback(key)


def test_exact_allowlisted_button_is_typed_and_restored_by_declared_vkey() -> None:
    button = BranchButton()
    window = BranchWindow()
    session = Session({"wnd[0]/usr/btnDETAIL": button})
    session.ActiveWindow = window
    state = UiState(
        tcode="ZMAP",
        components=[
            UiComponent(
                logical_id="detail",
                sap_id="wnd[0]/usr/btnDETAIL",
                type="GuiButton",
                visible=True,
                enabled=True,
            )
        ],
    ).ensure_state_id()
    rule = TraversalBranchRule(
        action="press",
        source_state_id=state.state_id,
        target_logical_id="detail",
        target_sap_id="wnd[0]/usr/btnDETAIL",
        component_type="GuiButton",
        restore_vkey=3,
        expected_program="ZPROGRAM",
        expected_screen_number=200,
    )
    adapter = SapTraversalAdapter(session, branch_rules=[rule])

    candidates = list(adapter.discover(state))
    branch = next(item for item in candidates if item.action == "press")
    assert branch.params["branch_rule_id"] == rule.rule_id
    assert branch.expected_program == "ZPROGRAM"
    assert branch.expected_screen_number == 200
    assert all(item.needs_user_input is None for item in candidates)

    adapter.execute(branch)
    assert button.press_calls == 1
    assert adapter.pending_restore_token == {
        "action": "allowlisted_branch",
        "branch_rule_id": rule.rule_id,
        "restore_vkey": 3,
    }
    adapter.restore(state)
    assert window.vkeys == [3]


@pytest.mark.parametrize(
    ("action", "attribute", "item_id", "method_name"),
    [
        ("press_toolbar_button", "toolbar_actions", "DETAIL", "PressToolbarButton"),
        (
            "select_context_menu_item",
            "context_menu_actions",
            "OPEN_DETAIL",
            "SelectContextMenuItem",
        ),
    ],
)
def test_exact_allowlisted_alv_branch_is_typed_and_restored(
    action, attribute, item_id, method_name
) -> None:
    """Native GuiGridView ALV branches are executable only as exact rules."""

    class BranchGrid:
        Type = "GuiGridView"

        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        def PressToolbarButton(self, value: str) -> None:
            self.calls.append(("PressToolbarButton", value))

        def SelectContextMenuItem(self, value: str) -> None:
            self.calls.append(("SelectContextMenuItem", value))

    grid = BranchGrid()
    window = BranchWindow()
    session = Session({"wnd[0]/usr/cntlALV/shell": grid})
    session.ActiveWindow = window
    collection_kwargs = {attribute: [{"id": item_id, "enabled": True, "visible": True}]}
    state = UiState(
        tcode="ZMAP",
        components=[
            UiComponent(
                logical_id="result-grid",
                sap_id="wnd[0]/usr/cntlALV/shell",
                type="GuiGridView",
                visible=True,
                enabled=True,
                supported_actions=[action],
            )
        ],
        collections=[
            CollectionDescriptor(
                component_logical_id="result-grid", kind="alv", **collection_kwargs
            )
        ],
    ).ensure_state_id()
    rule = TraversalBranchRule(
        action=action,
        source_state_id=state.state_id,
        target_logical_id="result-grid",
        target_sap_id="wnd[0]/usr/cntlALV/shell",
        component_type="GuiGridView",
        item_id=item_id,
        restore_vkey=3,
    )
    adapter = SapTraversalAdapter(session, branch_rules=[rule])

    candidate = next(item for item in adapter.discover(state) if item.action == action)
    assert candidate.target_locator["item_id"] == item_id
    assert candidate.params["branch_rule_id"] == rule.rule_id
    adapter.execute(candidate)

    assert grid.calls == [(method_name, item_id)]
    adapter.restore(state)
    assert window.vkeys == [3]


def test_exact_branch_rejects_forged_candidate_and_dangerous_rule() -> None:
    adapter, state, _, _, _ = make_fixture()
    forged = TraversalCandidate(
        action="press",
        target_logical_id="detail",
        target_locator={"sap_id": "wnd[0]/usr/btnDETAIL"},
        params={"branch_rule_id": "not-configured"},
    )
    with pytest.raises(ValueError, match="no exact rule"):
        adapter.execute(forged)

    with pytest.raises(ValueError, match="dangerous action"):
        TraversalBranchRule(
            action="press",
            source_state_id=state.state_id,
            target_logical_id="save",
            target_sap_id="wnd[0]/tbar/btnSAVE",
            component_type="GuiButton",
            restore_vkey=3,
        )


def test_reach_replays_saved_exact_branch_without_creating_restore_token() -> None:
    session = Session({})
    session.Info = SimpleNamespace(Program="ZPROGRAM", ScreenNumber=100)

    def go_detail() -> None:
        session.Info.ScreenNumber = 200

    button = BranchButton(go_detail)
    session.objects["wnd[0]/usr/btnDETAIL"] = button
    session.ActiveWindow = BranchWindow()
    source = UiState(
        tcode="ZMAP",
        program="ZPROGRAM",
        screen_number=100,
        components=[
            UiComponent(
                logical_id="detail",
                sap_id="wnd[0]/usr/btnDETAIL",
                type="GuiButton",
            )
        ],
    ).ensure_state_id()
    target = UiState(
        tcode="ZMAP", program="ZPROGRAM", screen_number=200
    ).ensure_state_id()
    rule = TraversalBranchRule(
        action="press",
        source_state_id=source.state_id,
        target_logical_id="detail",
        target_sap_id="wnd[0]/usr/btnDETAIL",
        component_type="GuiButton",
        restore_vkey=3,
        expected_program="ZPROGRAM",
        expected_screen_number=200,
    )
    transition = Transition(
        transition_id="detail-branch",
        from_state_id=source.state_id,
        to_state_id=target.state_id,
        action="press",
        target_logical_id="detail",
        target_locator={
            "sap_id": "wnd[0]/usr/btnDETAIL",
            "component_type": "GuiButton",
        },
        side_effect="navigation",
        reversible=False,
    )
    graph = UiMap(
        manifest=MapManifest(map_id="map", tcode="ZMAP"),
        states={source.state_id: source, target.state_id: target},
        transitions=[transition],
    )
    adapter = SapTraversalAdapter(session, branch_rules=[rule])
    adapter.configure_reach_graph(graph)

    assert adapter.reach(target) is True
    assert session.Info.ScreenNumber == 200
    assert button.press_calls == 1
    assert adapter.pending_restore_token is None


def test_select_tab_records_serializable_inverse_and_restores_previous_tab() -> None:
    adapter, state, group, _, _ = make_fixture()
    _, candidates = candidates_by_action(adapter, state)

    adapter.execute(candidates["select_tab"])

    assert group.selected == "tab/b"
    assert adapter.pending_restore_token == {
        "action": "select_tab", "previous_tab_sap_id": "tab/a"
    }
    json.dumps(adapter.pending_restore_token)

    adapter.restore(state)
    assert group.selected == "tab/a"
    assert adapter.pending_restore_token is None


def test_scroll_restores_exact_nonzero_position() -> None:
    adapter, state, _, grid, _ = make_fixture()
    _, candidates = candidates_by_action(adapter, state)

    adapter.execute(candidates["scroll_to"])
    assert grid.VerticalScrollbar.Position == 15
    assert adapter.pending_restore_token == {
        "action": "scroll_to", "sap_id": "grid", "axis": "vertical", "position": 7
    }

    adapter.restore(state)
    assert grid.VerticalScrollbar.Position == 7


def test_horizontal_scroll_restores_exact_nonzero_position() -> None:
    adapter, state, _, grid, _ = make_fixture()
    horizontal = next(
        item
        for item in adapter.discover(state)
        if item.action == "scroll_to" and item.params["axis"] == "horizontal"
    )

    adapter.execute(horizontal)
    assert grid.HorizontalScrollbar.Position == 8
    assert adapter.pending_restore_token == {
        "action": "scroll_to",
        "sap_id": "grid",
        "axis": "horizontal",
        "position": 3,
    }

    adapter.restore(state)
    assert grid.HorizontalScrollbar.Position == 3


def test_expand_restores_only_node_opened_by_adapter() -> None:
    adapter, state, _, _, tree = make_fixture()
    _, candidates = candidates_by_action(adapter, state)

    adapter.execute(candidates["expand_node"])
    assert tree.expanded == {"already", "new"}
    adapter.restore(state)

    assert tree.expanded == {"already"}
    assert tree.collapse_calls == ["new"]


def test_execute_expand_rejects_drift_when_node_is_already_open() -> None:
    adapter, state, _, _, tree = make_fixture()
    candidate = TraversalCandidate(
        action="expand_node",
        target_locator={
            "sap_id": "tree", "component_type": "GuiShell", "collection_kind": "tree"
        },
        params={"node_key": "already", "was_expanded": False},
    )

    with pytest.raises(ValueError, match="tree state drift"):
        adapter.execute(candidate)

    assert "already" in tree.expanded
    assert tree.collapse_calls == []


def test_execute_scroll_rejects_drift_without_storing_restore_token() -> None:
    adapter, state, _, grid, _ = make_fixture()
    _, candidates = candidates_by_action(adapter, state)
    grid.VerticalScrollbar.Position = 9

    with pytest.raises(ValueError, match="scroll state drift"):
        adapter.execute(candidates["scroll_to"])

    assert grid.VerticalScrollbar.Position == 9
    assert adapter.pending_restore_token is None


def test_reach_reproduces_tab_scroll_and_expanded_tree_state() -> None:
    adapter, state, group, grid, tree = make_fixture()
    group.selected = "tab/b"
    grid.VerticalScrollbar.Position = 22
    grid.HorizontalScrollbar.Position = 17
    tree.expanded = {"new"}

    assert adapter.reach(state) is True

    assert group.selected == "tab/a"
    assert grid.VerticalScrollbar.Position == 7
    assert grid.HorizontalScrollbar.Position == 3
    assert tree.expanded == {"already"}
    assert tree.collapse_calls == ["new"]
    assert tree.expand_calls == ["already"]


def test_lowercase_find_by_id_is_supported() -> None:
    adapter, state, group, grid, tree = make_fixture()

    class LowercaseSession:
        def __init__(self, objects: dict[str, object]) -> None:
            self.objects = objects

        def findById(self, sap_id: str) -> object:
            return self.objects[sap_id]

    lower = SapTraversalAdapter(
        LowercaseSession(
            {
                "tab/a": Tab("tab/a", group),
                "tab/b": Tab("tab/b", group),
                "grid": grid,
                "tree": tree,
            }
        )
    )

    assert {item.action for item in lower.discover(state)} == {
        "select_tab", "scroll_to", "expand_node", "manual_branch_gap"
    }


def test_manual_gap_is_deterministic_and_bounded_without_business_text() -> None:
    adapter, state, _, _, _ = make_fixture()
    state.components.extend(
        UiComponent(
            logical_id=f"button-{index}",
            sap_id=f"wnd[0]/usr/btn[{index}]",
            type="GuiButton",
            text=f"BUSINESS-SECRET-{index}",
        )
        for index in range(40)
    )

    first = [item for item in adapter.discover(state) if item.needs_user_input][0]
    second = [item for item in adapter.discover(state) if item.needs_user_input][0]
    request = first.needs_user_input

    assert request.resume_token == second.needs_user_input.resume_token
    assert request.scope["branch_count"] == 44
    assert len(request.scope["coverage_branches"]) == 32
    assert request.scope["truncated"] is True
    assert "BUSINESS-SECRET" not in request.model_dump_json()


def test_apply_user_input_never_writes_to_com() -> None:
    adapter, _, _, _, _ = make_fixture()
    before = list(adapter.session.find_calls)
    item = UserInputRequest(question="회사 코드를 선택해 주세요", resume_token="q1")

    assert adapter.apply_user_input(item, {"company_code": "1000"}) is True
    assert adapter.session.find_calls == before
    assert adapter.apply_user_input(item, None) is False
