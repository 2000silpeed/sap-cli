"""Phase 1 contracts for versioned SAP GUI map models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from sap_cli.ontology.schema import (
    GuiComponentType,
    ScreenOntology,
    SapButton,
    SapField,
    SapTable,
    SapTableColumn,
)
from sap_cli.ui_map.fingerprint import compute_map_fingerprint
from sap_cli.ui_map.schema import (
    ApiCoverageManifest,
    ApiMemberCoverage,
    CollectionColumn,
    CollectionDescriptor,
    MapManifest,
    SideEffectClass,
    Transition,
    TraversalBudget,
    TraversalPolicy,
    UiComponent,
    UiMap,
    UiState,
    UserInputRequest,
    WindowDescriptor,
    legacy_screen_to_state,
    structural_fingerprint,
)


def _component(**updates: object) -> UiComponent:
    data: dict[str, object] = {
        "logical_id": "material-number",
        "sap_id": "wnd[0]/usr/ctxtRMMG1-MATNR",
        "type": "GuiCTextField",
        "role": "input",
        "text": "Material",
        "visible": True,
        "enabled": True,
        "changeable": True,
        "static_properties": {"max_length": 40},
        "volatile_properties": {"value": "MAT-100", "caret_position": 3},
        "supported_actions": ["set", "focus", "search_help"],
    }
    data.update(updates)
    return UiComponent(**data)


def _state(**updates: object) -> UiState:
    data: dict[str, object] = {
        "tcode": "MM03",
        "program": "SAPLMGMM",
        "screen_number": 60,
        "title": "Display Material",
        "mode": "display",
        "active_tabs": {"wnd[0]/usr/tabsTABSPR1": "TAB1"},
        "window_stack": [
            WindowDescriptor(
                sap_id="wnd[0]", type="GuiMainWindow", title="Display Material"
            )
        ],
        "components": [_component()],
        "collections": [
            CollectionDescriptor(
                component_logical_id="plant-grid",
                kind="alv",
                columns=[
                    CollectionColumn(id="WERKS", position=0),
                    CollectionColumn(id="LGORT", position=1),
                ],
                selection_mode="single",
                toolbar_actions=[{"action": "read_table"}],
            )
        ],
    }
    data.update(updates)
    return UiState(**data)


def test_map_models_round_trip_through_json() -> None:
    state = _state().ensure_state_id()
    transition = Transition(
        transition_id="select-basic-data",
        from_state_id=state.state_id,
        action="select",
        target_logical_id="basic-data-tab",
        observed_params={"tab": "TAB1"},
        to_state_id="next-state",
        side_effect=SideEffectClass.REVERSIBLE,
    )
    manifest = MapManifest(
        map_id="DEV-100-en-MM03",
        api_version="PL08",
        map_version=1,
        system_id="DEV",
        client="100",
        language="en",
        gui_version="8.00 PL08",
        tcode="MM03",
    )
    ui_map = UiMap(
        manifest=manifest,
        states={state.state_id: state},
        transitions=[transition],
    )

    restored = UiMap.model_validate_json(ui_map.model_dump_json())

    assert restored == ui_map
    assert restored.states[state.state_id].components[0].sap_id.endswith("RMMG1-MATNR")


def test_map_fingerprint_covers_complete_snapshot_content() -> None:
    state = _state(state_id="state-1")
    manifest = MapManifest(map_id="DEV-100-en-MM03", tcode="MM03")
    baseline = UiMap(manifest=manifest, states={"state-1": state})
    changed_component = baseline.model_copy(deep=True)
    changed_component.states["state-1"].components[0].tooltip = "Search material"
    changed_interaction = baseline.model_copy(
        update={
            "pending_interactions": [
                UserInputRequest(
                    question="Select a view.",
                    current_state_id="state-1",
                    resume_token="resume-1",
                )
            ]
        },
        deep=True,
    )

    fingerprint = compute_map_fingerprint(baseline)

    assert fingerprint == compute_map_fingerprint(
        UiMap.model_validate_json(baseline.model_dump_json())
    )
    assert fingerprint != compute_map_fingerprint(changed_component)
    assert fingerprint != compute_map_fingerprint(changed_interaction)


def test_user_input_request_round_trips_as_pending_interaction() -> None:
    request = UserInputRequest(
        question="Which sales organization should be used?",
        reason="The map cannot infer a required business value.",
        current_state_id="state-initial",
        required_fields=["sales_organization"],
        choices=["1000", "2000"],
        risk="high",
        resume_token="resume-001",
        scope={"tcode": "VA01", "step": 2},
        provenance={"source": "planner"},
    )
    ui_map = UiMap(
        manifest=MapManifest(map_id="DEV-100-en-VA01", tcode="VA01"),
        pending_interactions=[request],
    )

    restored = UiMap.model_validate_json(ui_map.model_dump_json())

    assert restored.pending_interactions == [request]
    assert restored.pending_interactions[0].status == "pending"


def test_merge_delta_adds_only_new_records_and_preserves_existing_payloads() -> None:
    existing_state = _state(state_id="state-1", title="Original title")
    new_state = _state(state_id="state-2", active_tabs={"tabs": "TAB2"})
    existing_transition = Transition(
        transition_id="transition-1",
        from_state_id="state-1",
        to_state_id="state-2",
        action="select",
        target_logical_id="original-target",
        side_effect=SideEffectClass.REVERSIBLE,
    )
    new_transition = Transition(
        transition_id="transition-2",
        from_state_id="state-2",
        action="read",
        side_effect=SideEffectClass.READ_ONLY,
    )
    manifest = MapManifest(
        map_id="DEV-100-en-MM03",
        tcode="MM03",
        system_id="DEV",
        client="100",
        language="en",
    )
    ui_map = UiMap(
        manifest=manifest,
        states={"state-1": existing_state},
        transitions=[existing_transition],
    )
    delta = UiMap(
        manifest=manifest.model_copy(update={"map_version": 2}),
        states={
            "state-1": existing_state.model_copy(update={"title": "Incoming title"}),
            "state-2": new_state,
        },
        transitions=[
            existing_transition.model_copy(update={"target_logical_id": "incoming-target"}),
            new_transition,
        ],
        pending_interactions=[
            UserInputRequest(
                question="Open the missing detail tab, then continue.",
                current_state_id="state-2",
                resume_token="resume-002",
            )
        ],
    )

    result = ui_map.merge_delta(delta)

    assert result == {
        "states_added": 1,
        "transitions_added": 1,
        "interactions_added": 1,
        "conflicts_recorded": 2,
    }
    assert ui_map.states["state-1"].title == "Original title"
    assert ui_map.states["state-2"] == new_state
    assert ui_map.transitions[0].target_logical_id == "original-target"
    assert [item.transition_id for item in ui_map.transitions] == [
        "transition-1",
        "transition-2",
    ]
    assert {item["kind"] for item in ui_map.merge_conflicts} == {"state", "transition"}


def test_merge_delta_deduplicates_identical_records_and_interactions() -> None:
    state = _state(state_id="state-1")
    transition = Transition(
        transition_id="transition-1",
        from_state_id="state-1",
        action="read",
        side_effect=SideEffectClass.READ_ONLY,
    )
    interaction = UserInputRequest(
        question="Confirm the manually selected view.",
        current_state_id="state-1",
        resume_token="resume-identical",
    )
    manifest = MapManifest(map_id="DEV-100-en-MM03", tcode="MM03")
    ui_map = UiMap(
        manifest=manifest,
        states={"state-1": state},
        transitions=[transition],
        pending_interactions=[interaction],
    )
    delta = ui_map.model_copy(deep=True)

    result = ui_map.merge_delta(delta)

    assert result == {
        "states_added": 0,
        "transitions_added": 0,
        "interactions_added": 0,
        "conflicts_recorded": 0,
    }
    assert len(ui_map.states) == len(ui_map.transitions) == len(ui_map.pending_interactions) == 1
    assert ui_map.merge_conflicts == []


def test_merge_delta_preserves_new_unreachable_coverage_without_duplicates() -> None:
    manifest = MapManifest(
        map_id="DEV-100-en-MM03",
        tcode="MM03",
        unreachable=[{"state_id": "a", "reason": "permission_denied"}],
    )
    ui_map = UiMap(manifest=manifest)
    delta = UiMap(
        manifest=manifest.model_copy(
            update={
                "unreachable": [
                    {"state_id": "a", "reason": "permission_denied"},
                    {"state_id": "b", "reason": "input_required"},
                ]
            },
            deep=True,
        )
    )

    ui_map.merge_delta(delta)

    assert [
        {key: value for key, value in item.items() if key != "gap_id"}
        for item in ui_map.manifest.unreachable
    ] == [
        {"state_id": "a", "reason": "permission_denied"},
        {"state_id": "b", "reason": "input_required"},
    ]
    assert all(item["gap_id"].startswith("gap-") for item in ui_map.manifest.unreachable)


def test_merge_delta_does_not_resolve_gap_when_unrelated_state_is_added() -> None:
    manifest = MapManifest(
        map_id="DEV-100-en-MM03",
        tcode="MM03",
        unreachable=[{"state_id": "missing", "reason": "input_required"}],
    )
    ui_map = UiMap(manifest=manifest)
    delta = UiMap(
        manifest=manifest.model_copy(deep=True),
        states={"missing": _state(state_id="missing")},
    )

    ui_map.merge_delta(delta)

    assert ui_map.manifest.unreachable[0]["state_id"] == "missing"
    assert ui_map.manifest.unreachable[0]["reason"] == "input_required"
    assert ui_map.manifest.unreachable[0]["gap_id"].startswith("gap-")
    assert "resolved" not in ui_map.manifest.unreachable[0]


def test_merge_delta_advances_only_explicit_gap_resolution_lifecycle() -> None:
    manifest = MapManifest(
        map_id="DEV-100-en-MM03",
        tcode="MM03",
        unreachable=[
            {
                "gap_id": "gap-company-screen",
                "state_id": "start",
                "reason": "input_required",
            }
        ],
    )
    ui_map = UiMap(manifest=manifest)
    delta = UiMap(
        manifest=manifest.model_copy(
            update={
                "unreachable": [
                    {
                        "gap_id": "gap-company-screen",
                        "state_id": "start",
                        "reason": "input_required",
                        "resolved": True,
                        "resolved_by_state_id": "company-screen",
                        "resolution_resume_token": "augment-1",
                    }
                ]
            },
            deep=True,
        )
    )

    ui_map.merge_delta(delta)

    assert ui_map.manifest.unreachable == [
        {
            "gap_id": "gap-company-screen",
            "state_id": "start",
            "reason": "input_required",
            "resolved": True,
            "resolved_by_state_id": "company-screen",
            "resolution_resume_token": "augment-1",
        }
    ]

    ui_map.merge_delta(UiMap(manifest=manifest.model_copy(deep=True)))
    assert ui_map.manifest.unreachable[0]["resolved"] is True


def test_structural_fingerprint_distinguishes_modal_meaning() -> None:
    first = _state(
        title="Select Company",
        window_stack=[
            WindowDescriptor(
                sap_id="wnd[1]",
                type="GuiModalWindow",
                title="Select Company",
                modal=True,
                signature="GuiModalWindow:wnd[1]:one",
            )
        ],
        components=[
            _component(
                logical_id="message",
                sap_id="wnd[1]/usr/lblMSG",
                type="GuiLabel",
                text="Choose a company",
                selected=False,
            )
        ],
    )
    second = first.model_copy(deep=True)
    second.title = "Confirm Delete"
    second.window_stack[0].title = "Confirm Delete"
    second.window_stack[0].signature = "GuiModalWindow:wnd[1]:two"
    second.components[0].text = "Delete document?"

    assert structural_fingerprint(first) != structural_fingerprint(second)


def test_structural_fingerprint_ignores_modal_business_identifier() -> None:
    first = _state(
        title="Confirm Delete",
        window_stack=[
            WindowDescriptor(
                sap_id="wnd[1]",
                type="GuiModalWindow",
                title="Confirm Delete 100",
                modal=True,
                signature="raw-one",
            )
        ],
        components=[
            _component(
                logical_id="message",
                sap_id="wnd[1]/usr/lblMSG",
                type="GuiLabel",
                text="Delete document 100?",
                selected=False,
            )
        ],
    )
    second = first.model_copy(deep=True)
    second.window_stack[0].title = "Confirm Delete 200"
    second.window_stack[0].signature = "raw-two"
    second.components[0].text = "Delete document 200?"

    assert structural_fingerprint(first) == structural_fingerprint(second)


@pytest.mark.parametrize(
    ("manifest_update", "conflict_field"),
    [
        ({"map_id": "OTHER-MAP"}, "map_id"),
        ({"tcode": "VA01"}, "tcode"),
        ({"client": "200"}, "client"),
        ({"system_id": "QAS"}, "system_id"),
    ],
)
def test_merge_delta_rejects_map_identity_or_environment_conflicts(
    manifest_update: dict[str, str],
    conflict_field: str,
) -> None:
    existing_state = _state(state_id="state-1")
    manifest = MapManifest(
        map_id="DEV-100-en-MM03",
        tcode="MM03",
        system_id="DEV",
        client="100",
        language="en",
    )
    ui_map = UiMap(manifest=manifest, states={"state-1": existing_state})
    delta = UiMap(
        manifest=manifest.model_copy(update=manifest_update),
        states={"state-2": _state(state_id="state-2")},
    )

    with pytest.raises(ValueError, match=conflict_field):
        ui_map.merge_delta(delta)

    assert list(ui_map.states) == ["state-1"]
    assert ui_map.merge_conflicts[-1]["kind"] == "map_identity"
    assert conflict_field in ui_map.merge_conflicts[-1]["fields"]


def test_api_coverage_manifest_counts_each_stage() -> None:
    manifest = ApiCoverageManifest(
        api_version="8.00 PL08",
        members=[
            ApiMemberCoverage(
                object_name="GuiCTextField",
                member_name="Text",
                member_kind="property",
                modeled=True,
                adapter_supported=True,
                policy_classified=True,
                unit_tested=True,
                live_tested_versions=["8.00 PL08"],
            ),
            ApiMemberCoverage(
                object_name="GuiSession",
                member_name="Change",
                member_kind="event",
            ),
        ],
    )

    assert manifest.counts() == {
        "total": 2,
        "discovered": 2,
        "modeled": 1,
        "adapter_supported": 1,
        "policy_classified": 1,
        "unit_tested": 1,
        "live_tested": 1,
    }


def test_structural_fingerprint_is_deterministic_and_ignores_volatile_values() -> None:
    first = _state()
    second = _state(
        title="A runtime-specific title is not structural",
        components=[
            _component(
                volatile_properties={
                    "value": "A-DIFFERENT-MATERIAL",
                    "caret_position": 18,
                    "document_number": "4900009999",
                }
            )
        ],
    )

    assert structural_fingerprint(first) == structural_fingerprint(first)
    assert structural_fingerprint(first) == structural_fingerprint(second)


def test_business_selection_value_does_not_create_a_new_ui_state() -> None:
    baseline = _state(components=[_component(selected=False)])
    checked = _state(components=[_component(selected=True)])
    assert baseline.compute_fingerprint() == checked.compute_fingerprint()


def test_collection_viewport_position_creates_an_explorable_ui_state() -> None:
    baseline = _state(
        collections=[
            CollectionDescriptor(
                component_logical_id="alv",
                kind="alv",
                visible_range=(0, 19),
                vertical_position=0,
            )
        ]
    )
    next_page = baseline.model_copy(
        update={
            "collections": [
                CollectionDescriptor(
                    component_logical_id="alv",
                    kind="alv",
                    visible_range=(20, 39),
                    vertical_position=20,
                )
            ]
        }
    )

    assert baseline.compute_fingerprint() != next_page.compute_fingerprint()


@pytest.mark.parametrize(
    "change",
    [
        {"active_tabs": {"wnd[0]/usr/tabsTABSPR1": "TAB2"}},
        {
            "window_stack": [
                WindowDescriptor(sap_id="wnd[0]", type="GuiMainWindow"),
                WindowDescriptor(sap_id="wnd[1]", type="GuiModalWindow", modal=True),
            ],
            "modal_signature": "wnd[1]:Select View",
        },
        {"mode": "change"},
    ],
    ids=["active-tab", "modal", "mode"],
)
def test_same_dynpro_can_hold_distinct_actionable_states(change: dict[str, object]) -> None:
    baseline = _state()
    changed = _state(**change)

    assert baseline.screen_number == changed.screen_number
    assert baseline.compute_fingerprint() != changed.compute_fingerprint()


def test_active_window_changes_state_fingerprint() -> None:
    first = _state(
        window_stack=[
            WindowDescriptor(sap_id="wnd[0]", type="GuiMainWindow", active=True),
            WindowDescriptor(sap_id="wnd[1]", type="GuiModalWindow", modal=True),
        ]
    )
    second = first.model_copy(
        update={
            "window_stack": [
                WindowDescriptor(sap_id="wnd[0]", type="GuiMainWindow"),
                WindowDescriptor(
                    sap_id="wnd[1]", type="GuiModalWindow", modal=True, active=True
                ),
            ]
        }
    )
    assert first.compute_fingerprint() != second.compute_fingerprint()


def test_legacy_screen_is_converted_without_losing_actionable_elements() -> None:
    legacy = ScreenOntology(
        transaction="MM03",
        screen_number=100,
        program="SAPLMGMM",
        title="Display Material: Initial Screen",
        fields=[
            SapField(
                id="wnd[0]/usr/ctxtRMMG1-MATNR",
                name="RMMG1-MATNR",
                type=GuiComponentType.CTEXT_FIELD,
                label="Material",
                value="MAT-100",
                changeable=True,
                required=True,
            )
        ],
        buttons=[SapButton(id="wnd[0]/tbar[1]/btn[8]", text="Execute")],
        tabs=[SapButton(id="wnd[0]/usr/tabsTABSPR1/tabpTAB1", text="Basic Data")],
        tables=[
            SapTable(
                id="wnd[0]/usr/tblPLANTS",
                name="Plants",
                type=GuiComponentType.TABLE_CONTROL,
                row_count=12,
                column_count=2,
                columns=[
                    SapTableColumn(name="WERKS", title="Plant", position=0),
                    SapTableColumn(name="LGORT", title="Storage Location", position=1),
                ],
            )
        ],
    )

    state = legacy_screen_to_state(legacy)

    assert (state.tcode, state.program, state.screen_number) == ("MM03", "SAPLMGMM", 100)
    assert {item.sap_id for item in state.components} >= {
        "wnd[0]/usr/ctxtRMMG1-MATNR",
        "wnd[0]/tbar[1]/btn[8]",
        "wnd[0]/usr/tabsTABSPR1/tabpTAB1",
    }
    field = next(item for item in state.components if item.sap_id.endswith("RMMG1-MATNR"))
    assert field.volatile_properties["value"] == "MAT-100"
    assert state.collections[0].columns[0].id == "WERKS"
    assert state.state_id == state.compute_fingerprint()


@pytest.mark.parametrize(
    "field,value",
    [
        ("max_depth", -1),
        ("max_states", 0),
        ("max_transitions", 0),
        ("max_pages_per_collection", 0),
        ("max_seconds", 0),
        ("max_rescans_per_checkpoint", 2),
    ],
)
def test_traversal_budget_rejects_unsafe_limits(field: str, value: int) -> None:
    values = TraversalBudget().model_dump()
    values[field] = value
    with pytest.raises(ValidationError):
        TraversalBudget(**values)


def test_destructive_transition_requires_explicit_approval() -> None:
    with pytest.raises(ValidationError):
        Transition(
            transition_id="delete",
            from_state_id="before",
            action="press",
            target_logical_id="delete-button",
            to_state_id="after",
            side_effect=SideEffectClass.DESTRUCTIVE,
        )


def test_traversal_policy_blocks_side_effects_and_dangerous_actions() -> None:
    policy = TraversalPolicy(
        mode="reversible",
        allowed_side_effects={SideEffectClass.READ_ONLY, SideEffectClass.REVERSIBLE},
        allowed_actions={"read", "select-tab", "save"},
    )

    assert policy.permits("select_tab", SideEffectClass.REVERSIBLE)
    assert not policy.permits("save", SideEffectClass.REVERSIBLE)
    assert not policy.permits("press", SideEffectClass.COMMIT)

    with pytest.raises(ValidationError):
        TraversalPolicy(
            mode="allowlisted",
            allowed_side_effects={SideEffectClass.READ_ONLY, SideEffectClass.DESTRUCTIVE},
        )
