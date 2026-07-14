from __future__ import annotations

from sap_cli.ui_map.schema import (
    CollectionDescriptor,
    MapManifest,
    Transition,
    UiComponent,
    UiMap,
    UiState,
    UserInputRequest,
)
from sap_cli.ui_map.search import UiMapSearch, search_map


def _map() -> UiMap:
    states = {
        "start": UiState(
            state_id="start",
            tcode="VA03",
            program="SAPMV45A",
            screen_number=100,
            title="판매 오더 조회",
            components=[
                UiComponent(
                    logical_id="sales-order",
                    sap_id="wnd[0]/usr/ctxtVBAK-VBELN",
                    type="GuiCTextField",
                    role="field",
                    name="VBELN",
                    text="50000123",
                    tooltip="판매 문서",
                ),
                UiComponent(
                    logical_id="secret-field",
                    sap_id="wnd[0]/usr/pwdSECRET",
                    type="GuiPasswordField",
                    role="password",
                    name="PASSWORD",
                    text="never-index-this",
                    volatile_properties={"value": "nor-this"},
                    sensitive=True,
                ),
            ],
        ),
        "items": UiState(
            state_id="items",
            tcode="VA03",
            program="SAPMV45A",
            screen_number=4001,
            title="품목 개요",
            components=[
                UiComponent(
                    logical_id="item-grid",
                    sap_id="wnd[0]/usr/tabsTAXI_TABSTRIP_OVERVIEW/tabpT\\01",
                    type="GuiGridView",
                    role="alv",
                    name="ITEMS",
                    tooltip="Sales items",
                )
            ],
            collections=[
                CollectionDescriptor(
                    component_logical_id="item-grid",
                    kind="alv",
                    coverage={"complete": False, "reason": "next page not visited"},
                )
            ],
        ),
        "detail": UiState(
            state_id="detail",
            tcode="VA03",
            program="SAPMV45A",
            screen_number=4900,
            title="품목 상세",
        ),
    }
    transitions = [
        Transition(
            transition_id="z-long",
            from_state_id="start",
            to_state_id="detail",
            action="open detail",
            target_logical_id="sales-order",
        ),
        Transition(
            transition_id="a-items",
            from_state_id="start",
            to_state_id="items",
            action="select tab",
            target_logical_id="item-grid",
            guards=[{"kind": "title", "expected": "판매 오더 조회"}],
            evidence=[
                {"source": "user-guided", "note": "품목 탭 확인"},
                {"sensitive": True, "value": "hidden-evidence"},
                {"password": "also-hidden"},
            ],
        ),
        Transition(
            transition_id="b-detail",
            from_state_id="items",
            to_state_id="detail",
            action="double click",
            target_logical_id="item-grid",
        ),
    ]
    return UiMap(
        manifest=MapManifest(
            map_id="va03", tcode="VA03", unreachable=[{"reason": "modal unknown"}]
        ),
        states=states,
        transitions=transitions,
        pending_interactions=[
            UserInputRequest(
                question="조회 조직을 선택해 주세요",
                current_state_id="start",
                resume_token="r1",
            ),
            UserInputRequest(
                question="완료",
                current_state_id="detail",
                resume_token="r2",
                status="resolved",
            ),
        ],
    )


def test_searches_state_component_and_transition_fields() -> None:
    index = UiMapSearch(_map())

    assert {item.entity_id for item in index.search_states("SAPMV45A")} == {
        "start",
        "items",
        "detail",
    }
    assert index.search_states("4001")[0].entity_id == "items"
    assert index.search_states("품목 개요")[0].entity_id == "items"
    assert index.search_components("VBAK-VBELN")[0].entity_id == "sales-order"
    assert index.search_components("Sales items")[0].entity_id == "item-grid"
    assert index.search_transitions("user-guided")[0].entity_id == "a-items"
    assert index.search_transitions("판매 오더 조회")[0].entity_id == "a-items"


def test_ranking_and_ties_are_deterministic() -> None:
    index = UiMapSearch(_map())
    first = [(item.kind, item.entity_id, item.score) for item in index.search("VA03")]
    second = [(item.kind, item.entity_id, item.score) for item in index.search("va03")]

    assert first == second
    assert [item[1] for item in first] == ["detail", "items", "start"]
    assert search_map(_map(), "item-grid", kinds=("component",))[0].kind == "component"


def test_sensitive_values_and_value_shaped_evidence_are_not_indexed() -> None:
    index = UiMapSearch(_map())

    assert index.search("never-index-this") == []
    assert index.search("nor-this") == []
    assert index.search("50000123") == []
    assert index.search("hidden-evidence") == []
    assert index.search("also-hidden") == []
    assert index.search_components("PASSWORD")[0].entity_id == "secret-field"


def test_shortest_path_uses_bfs_and_stable_transition_order() -> None:
    graph = _map()
    index = UiMapSearch(graph)

    assert [item.transition_id for item in index.shortest_path("start", "detail") or []] == [
        "z-long"
    ]
    assert index.shortest_path("start", "start") == []
    assert index.shortest_path("detail", "start") is None
    assert index.shortest_path("missing", "start") is None


def test_shortest_path_can_exclude_manual_edges_and_use_longer_replayable_route() -> None:
    graph = _map().model_copy(deep=True)
    graph.transitions = [
        Transition(
            transition_id="manual-direct",
            from_state_id="start",
            to_state_id="detail",
            action="user_guided_navigation",
        ),
        Transition(
            transition_id="to-items",
            from_state_id="start",
            to_state_id="items",
            action="select_tab",
        ),
        Transition(
            transition_id="to-detail",
            from_state_id="items",
            to_state_id="detail",
            action="press",
        ),
    ]
    index = UiMapSearch(graph)

    assert [
        item.transition_id for item in index.shortest_path("start", "detail") or []
    ] == ["manual-direct"]
    assert [
        item.transition_id
        for item in index.shortest_path(
            "start",
            "detail",
            excluded_actions={"user-guided-navigation"},
        )
        or []
    ] == ["to-items", "to-detail"]


def test_paths_to_component_returns_reachable_shortest_paths() -> None:
    paths = UiMapSearch(_map()).paths_to_component("item-grid", from_state_id="start")

    assert len(paths) == 1
    assert paths[0].state_id == "items"
    assert paths[0].component.logical_id == "item-grid"
    assert [item.transition_id for item in paths[0].transitions] == ["a-items"]


def test_unresolved_interactions_and_unreachable_coverage() -> None:
    index = UiMapSearch(_map())

    assert [item.resume_token for item in index.unresolved_interactions()] == ["r1"]
    gaps = index.unreachable_coverage()
    assert any(item.get("reason") == "modal unknown" for item in gaps)
    assert any(item.get("component_logical_id") == "item-grid" for item in gaps)
