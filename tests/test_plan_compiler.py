from types import SimpleNamespace

from sap_cli.planning.compiler import PlanCompiler
from sap_cli.planning.schema import NaturalLanguageRequest
from sap_cli.ui_map.schema import (
    CollectionColumn,
    CollectionDescriptor,
    CollectionNode,
    MapManifest,
    SideEffectClass,
    Transition,
    UiComponent,
    UiMap,
    UiState,
)
from sap_cli.ui_map.store import UiMapStore


def saved_map(tmp_path, *, user_guided=False, late_input=False):
    start = UiState(
        state_id="start",
        tcode="MM03",
        program="SAPLMGMM",
        screen_number=100,
        title="Material display",
        components=[
            UiComponent(
                logical_id="material",
                sap_id="wnd[0]/usr/ctxtRMMG1-MATNR",
                type="GuiCTextField",
                role="field",
                name="MATERIAL",
                tooltip="자재번호",
                changeable=True,
            ),
            UiComponent(
                logical_id="basic-tab",
                sap_id="wnd[0]/usr/tabs/tabpBASIC",
                type="GuiTab",
                role="tab",
                name="BASIC",
                text="기본 데이터",
            ),
        ],
    )
    detail = UiState(
        state_id="detail",
        tcode="MM03",
        program="SAPLMGMM",
        screen_number=200,
        title="Basic data",
        components=[
            UiComponent(
                logical_id="basic-description",
                sap_id="wnd[0]/usr/txtMAKT-MAKTX",
                type="GuiTextField",
                role="output",
                name="DESCRIPTION",
                tooltip="기본 데이터 설명",
            ),
            *(
                [
                    UiComponent(
                        logical_id="plant",
                        sap_id="wnd[0]/usr/ctxtMARC-WERKS",
                        type="GuiCTextField",
                        role="field",
                        name="plant",
                        tooltip="플랜트",
                        changeable=True,
                    )
                ]
                if late_input
                else []
            ),
        ],
    )
    transition = Transition(
        transition_id="to-basic",
        from_state_id="start",
        to_state_id="detail",
        action="user_guided_navigation" if user_guided else "select_tab",
        target_logical_id="basic-tab",
        target_locator={"sap_id": "wnd[0]/usr/tabs/tabpBASIC", "component_type": "GuiTab"},
        side_effect=SideEffectClass.REVERSIBLE,
        reversible=True,
    )
    ui_map = UiMap(
        manifest=MapManifest(
            map_id="DEV-100-MM03",
            tcode="MM03",
            system_id="DEV",
            client="100",
            language="KO",
            api_version="800.08",
        ),
        states={"start": start, "detail": detail},
        transitions=[transition],
    )
    store = UiMapStore(tmp_path)
    return store, store.save(ui_map)


def saved_collection_map(tmp_path, *, user_guided=False, ambiguous=False):
    store, saved = saved_map(tmp_path, user_guided=user_guided)
    ui_map = saved.model_copy(deep=True)
    detail = ui_map.states["detail"]
    controls = [
        UiComponent(
            logical_id="material-grid",
            sap_id="wnd[0]/usr/cntlGRID/shellcont/shell",
            type="GuiGridView",
            sub_type="GridView",
            role="collection",
            name="MATERIAL_GRID",
        ),
        UiComponent(
            logical_id="navigation-tree",
            sap_id="wnd[0]/usr/cntlTREE/shellcont/shell",
            type="GuiTree",
            role="collection",
            name="NAVIGATION_TREE",
        ),
    ]
    collections = [
        CollectionDescriptor(
            component_logical_id="material-grid",
            kind="alv",
            columns=[
                CollectionColumn(id="MATNR", title="자재 번호", position=0),
                CollectionColumn(id="MAKTX", title="자재 설명", position=1),
            ],
        ),
        CollectionDescriptor(
            component_logical_id="navigation-tree",
            kind="tree",
            nodes=[
                CollectionNode(key="ROOT", text="자재 관리", path="/자재 관리"),
                CollectionNode(
                    key="BASIC", text="기본 데이터 노드", path="/자재 관리/기본 데이터"
                ),
            ],
        ),
    ]
    if ambiguous:
        controls.append(
            UiComponent(
                logical_id="second-grid",
                sap_id="wnd[0]/usr/cntlGRID2/shellcont/shell",
                type="GuiGridView",
                role="collection",
            )
        )
        collections.append(
            CollectionDescriptor(
                component_logical_id="second-grid",
                kind="grid",
                columns=[CollectionColumn(id="TEXT", title="자재 설명", position=0)],
            )
        )
    ui_map.states["detail"] = detail.model_copy(
        update={
            "components": [*detail.components, *controls],
            "collections": collections,
        }
    )
    return store, store.save(ui_map)


def test_compiles_korean_request_from_saved_map_without_live_scan(tmp_path):
    store, _ = saved_map(tmp_path)
    compiler = PlanCompiler(store)
    request = NaturalLanguageRequest(
        text="티코드 MM03에서 자재번호 'MAT-100'의 기본 데이터 조회",
        system_id="DEV",
        client="100",
    )

    result = compiler.compile(request, goal_query="기본 데이터")

    assert result.validation.valid
    assert result.plan is not None and result.plan.verify_hash()
    assert [step.command for step in result.plan.steps] == [
        "run",
        "ui_set",
        "ui_select",
    ]
    assert result.plan.steps[1].params["value"] == "${material}"
    assert result.plan.steps[2].map_transition_id == "to-basic"
    assert result.plan.scan_policy == "fast"


def test_current_state_omits_tcode_navigation_step(tmp_path):
    store, _ = saved_map(tmp_path)
    result = PlanCompiler(store).compile(
        "tcode MM03 기본 데이터 조회",
        current_state_id="start",
        goal_query="기본 데이터",
    )
    assert result.plan is not None
    assert [step.command for step in result.plan.steps] == ["ui_select"]


def test_input_on_later_screen_is_set_immediately_after_saved_transition(tmp_path):
    store, _ = saved_map(tmp_path, late_input=True)
    request = NaturalLanguageRequest(
        text="tcode MM03 플랜트 조회",
        supplied_values={"plant": "1000"},
    )

    result = PlanCompiler(store).compile(request, goal_query="plant")

    assert result.validation.valid
    assert result.plan is not None
    assert [step.command for step in result.plan.steps] == [
        "run",
        "ui_select",
        "ui_set",
    ]
    input_step = result.plan.steps[-1]
    assert input_step.target_logical_id == "plant"
    assert input_step.params == {
        "element_id": "wnd[0]/usr/ctxtMARC-WERKS",
        "value": "${plant}",
    }
    assert input_step.expected_from_fingerprint == result.plan.steps[1].expected_to_fingerprint


def test_equal_input_field_candidates_return_question_instead_of_first_match(tmp_path):
    store = UiMapStore(tmp_path)
    store.save(
        UiMap(
            manifest=MapManifest(map_id="ambiguous-input", tcode="ZAMB"),
            states={
                "start": UiState(
                    state_id="start",
                    tcode="ZAMB",
                    components=[
                        UiComponent(
                            logical_id="company-primary",
                            sap_id="wnd[0]/usr/ctxtPRIMARY-BUKRS",
                            type="GuiCTextField",
                            name="company_code",
                            changeable=True,
                        ),
                        UiComponent(
                            logical_id="company-secondary",
                            sap_id="wnd[0]/usr/ctxtSECONDARY-BUKRS",
                            type="GuiCTextField",
                            name="company_code",
                            changeable=True,
                        ),
                        UiComponent(
                            logical_id="result",
                            sap_id="wnd[0]/usr/txtRESULT",
                            type="GuiTextField",
                            text="조회 결과",
                        ),
                    ],
                )
            },
        )
    )

    result = PlanCompiler(store).compile(
        NaturalLanguageRequest(
            text="tcode ZAMB 결과 조회",
            supplied_values={"company_code": "1000"},
        ),
        goal_query="result",
    )

    assert result.plan is not None
    assert not result.validation.valid
    assert result.validation.unresolved_inputs == ["company_code"]
    assert [step.command for step in result.plan.steps] == ["run"]
    question = result.validation.questions[0]
    assert question["code"] == "input_target_ambiguous"
    assert [choice["logical_id"] for choice in question["choices"]] == [
        "company-primary",
        "company-secondary",
    ]


def test_single_screen_input_binding_remains_backward_compatible(tmp_path):
    store = UiMapStore(tmp_path)
    store.save(
        UiMap(
            manifest=MapManifest(map_id="single-screen", tcode="ZONE"),
            states={
                "start": UiState(
                    state_id="start",
                    tcode="ZONE",
                    components=[
                        UiComponent(
                            logical_id="company_code",
                            sap_id="wnd[0]/usr/ctxtBUKRS",
                            type="GuiCTextField",
                            name="company_code",
                            changeable=True,
                        ),
                        UiComponent(
                            logical_id="result",
                            sap_id="wnd[0]/usr/txtRESULT",
                            type="GuiTextField",
                            text="조회 결과",
                        ),
                    ],
                )
            },
        )
    )

    result = PlanCompiler(store).compile(
        NaturalLanguageRequest(
            text="tcode ZONE 결과 조회",
            supplied_values={"company_code": "1000"},
        ),
        goal_query="result",
    )

    assert result.validation.valid
    assert result.plan is not None
    assert [step.command for step in result.plan.steps] == ["run", "ui_set"]
    assert result.plan.steps[-1].params["value"] == "${company_code}"


def test_non_display_operation_cannot_succeed_by_reaching_screen_only(tmp_path):
    store, _ = saved_map(tmp_path)

    result = PlanCompiler(store).compile(
        "tcode MM03 기본 데이터 생성", goal_query="기본 데이터"
    )

    assert result.plan is not None
    assert not result.validation.valid
    assert result.validation.errors == ["operation_action_missing:create"]


def test_outgoing_terminal_press_on_matched_component_is_appended(tmp_path):
    store = UiMapStore(tmp_path)
    start = UiState(
        state_id="start",
        tcode="ZSAVE",
        components=[
            UiComponent(
                logical_id="save-button",
                sap_id="wnd[0]/tbar[0]/btn[11]",
                type="GuiButton",
                text="저장",
            )
        ],
    )
    saved = UiState(state_id="saved", tcode="ZSAVE", title="저장 완료")
    store.save(
        UiMap(
            manifest=MapManifest(map_id="save-map", tcode="ZSAVE"),
            states={"start": start, "saved": saved},
            transitions=[
                Transition(
                    transition_id="save",
                    from_state_id="start",
                    to_state_id="saved",
                    action="press",
                    target_logical_id="save-button",
                    target_locator={
                        "sap_id": "wnd[0]/tbar[0]/btn[11]",
                        "component_type": "GuiButton",
                    },
                    side_effect=SideEffectClass.COMMIT,
                    approval_required=True,
                )
            ],
        )
    )

    result = PlanCompiler(store).compile("tcode ZSAVE 저장", goal_query="저장")

    assert result.validation.valid
    assert result.plan is not None
    assert [step.command for step in result.plan.steps] == ["run", "ui_press"]
    assert result.plan.goal_state_id == "saved"
    assert result.plan.steps[-1].map_transition_id == "save"
    assert result.plan.steps[-1].approval_required


def test_unrelated_commit_does_not_fulfill_requested_delete(tmp_path):
    store = UiMapStore(tmp_path)
    start = UiState(
        state_id="start",
        tcode="ZDELETE",
        components=[
            UiComponent(
                logical_id="save-button",
                sap_id="wnd[0]/tbar[0]/btn[11]",
                type="GuiButton",
                text="저장",
            )
        ],
    )
    saved = UiState(
        state_id="saved",
        tcode="ZDELETE",
        components=[
                UiComponent(
                    logical_id="delete-result",
                    sap_id="wnd[0]/usr/txtRESULT",
                    type="GuiTextField",
                    text="삭제 결과",
                    tooltip="삭제 결과",
                )
        ],
    )
    store.save(
        UiMap(
            manifest=MapManifest(map_id="delete-map", tcode="ZDELETE"),
            states={"start": start, "saved": saved},
            transitions=[
                Transition(
                    transition_id="save",
                    from_state_id="start",
                    to_state_id="saved",
                    action="press",
                    target_logical_id="save-button",
                    target_locator={
                        "sap_id": "wnd[0]/tbar[0]/btn[11]",
                        "component_type": "GuiButton",
                    },
                    side_effect=SideEffectClass.COMMIT,
                    approval_required=True,
                )
            ],
        )
    )

    result = PlanCompiler(store).compile(
        "tcode ZDELETE 삭제", goal_query="삭제 결과"
    )

    assert result.plan is not None
    assert not result.validation.valid
    assert result.validation.errors == ["operation_action_missing:delete"]


def test_missing_tcode_map_and_stale_map_are_explicit(tmp_path):
    store, _ = saved_map(tmp_path)
    compiler = PlanCompiler(store)
    assert compiler.compile("기본 데이터 조회").validation.errors == ["tcode_missing"]
    assert compiler.compile("tcode VA03 조회").validation.errors == ["map_missing"]
    stale = compiler.compile(
        "tcode MM03 기본 데이터 조회",
        goal_query="기본 데이터",
        runtime_versions={"api_version": "800.09"},
    )
    assert stale.validation.errors == ["saved_map_is_stale"]


def test_ambiguous_operation_is_hitl_and_explicit_success_criteria_are_kept(tmp_path):
    store, _ = saved_map(tmp_path)
    ambiguous = PlanCompiler(store).compile("tcode MM03 기본 데이터")
    assert ambiguous.plan is None
    assert ambiguous.validation.errors == ["operation_ambiguous"]
    assert ambiguous.validation.questions == [
        {
            "code": "operation_ambiguous",
            "question": "조회, 변경, 생성, 저장 등 수행할 작업을 구체적으로 알려 주세요.",
        }
    ]

    resolved = PlanCompiler(store).compile(
        "tcode MM03 기본 데이터 조회. 성공 조건: 설명이 표시됨",
        goal_query="기본 데이터",
    )
    assert resolved.plan is not None
    assert resolved.plan.success_criteria == ["설명이 표시됨"]


def test_same_saved_inputs_produce_identical_plan_snapshot(tmp_path):
    store, _ = saved_map(tmp_path)
    compiler = PlanCompiler(store)
    request = NaturalLanguageRequest(
        text="tcode MM03 기본 데이터 조회",
        supplied_values={"material": "MAT-100"},
    )

    first = compiler.compile(request, goal_query="기본 데이터")
    second = compiler.compile(request, goal_query="기본 데이터")

    assert first.plan is not None and second.plan is not None
    assert first.plan.model_dump(mode="json") == second.plan.model_dump(mode="json")
    assert first.plan.plan_hash == second.plan.plan_hash


def test_required_saved_map_field_without_value_returns_structured_question(tmp_path):
    store = UiMapStore(tmp_path)
    store.save(
        UiMap(
            manifest=MapManifest(map_id="required-map", tcode="ZREQ"),
            states={
                "start": UiState(
                    state_id="start",
                    tcode="ZREQ",
                    components=[
                        UiComponent(
                            logical_id="company_code",
                            sap_id="wnd[0]/usr/ctxtBUKRS",
                            type="GuiCTextField",
                            name="company_code",
                            required=True,
                            changeable=True,
                        ),
                        UiComponent(
                            logical_id="result",
                            sap_id="wnd[0]/usr/txtRESULT",
                            type="GuiTextField",
                            text="조회 결과",
                        ),
                    ],
                )
            },
        )
    )

    result = PlanCompiler(store).compile(
        "tcode ZREQ 결과 조회", goal_query="result"
    )

    assert result.plan is not None
    assert result.validation.valid is False
    assert result.validation.unresolved_inputs == ["company_code"]
    assert result.validation.questions[0]["field"] == "company_code"


def test_user_guided_edge_is_not_silently_made_executable(tmp_path):
    store, _ = saved_map(tmp_path, user_guided=True)
    result = PlanCompiler(store).compile(
        "tcode MM03 기본 데이터 조회", goal_query="기본 데이터"
    )
    assert result.plan is None
    assert result.validation.errors == ["goal_requires_user_guidance"]


def test_longer_replayable_path_is_preferred_over_short_manual_path(tmp_path):
    store = UiMapStore(tmp_path)
    start = UiState(
        state_id="start",
        tcode="ZROUTE",
        components=[
            UiComponent(
                logical_id="first-tab",
                sap_id="wnd[0]/usr/tabs/tabpFIRST",
                type="GuiTab",
            )
        ],
    )
    middle = UiState(
        state_id="middle",
        tcode="ZROUTE",
        components=[
            UiComponent(
                logical_id="second-tab",
                sap_id="wnd[0]/usr/tabs/tabpSECOND",
                type="GuiTab",
            )
        ],
    )
    detail = UiState(
        state_id="detail",
        tcode="ZROUTE",
        components=[
            UiComponent(
                logical_id="detail-output",
                sap_id="wnd[0]/usr/lblDETAIL",
                type="GuiLabel",
                text="상세 결과",
            )
        ],
    )
    store.save(
        UiMap(
            manifest=MapManifest(map_id="route-map", tcode="ZROUTE"),
            states={"start": start, "middle": middle, "detail": detail},
            transitions=[
                Transition(
                    transition_id="manual-direct",
                    from_state_id="start",
                    to_state_id="detail",
                    action="user_guided_navigation",
                ),
                Transition(
                    transition_id="first",
                    from_state_id="start",
                    to_state_id="middle",
                    action="select_tab",
                    target_logical_id="first-tab",
                    target_locator={
                        "sap_id": "wnd[0]/usr/tabs/tabpFIRST",
                        "component_type": "GuiTab",
                    },
                    side_effect=SideEffectClass.REVERSIBLE,
                    reversible=True,
                ),
                Transition(
                    transition_id="second",
                    from_state_id="middle",
                    to_state_id="detail",
                    action="select_tab",
                    target_logical_id="second-tab",
                    target_locator={
                        "sap_id": "wnd[0]/usr/tabs/tabpSECOND",
                        "component_type": "GuiTab",
                    },
                    side_effect=SideEffectClass.REVERSIBLE,
                    reversible=True,
                ),
            ],
        )
    )

    result = PlanCompiler(store).compile(
        "tcode ZROUTE 상세 결과 조회", goal_query="상세 결과"
    )

    assert result.validation.valid
    assert result.plan is not None
    assert [step.map_transition_id for step in result.plan.steps[1:]] == [
        "first",
        "second",
    ]


def test_sensitive_input_is_only_referenced_in_step_preview(tmp_path):
    store, _ = saved_map(tmp_path)
    request = NaturalLanguageRequest(
        text="tcode MM03 기본 데이터 조회 password='TOP-SECRET'",
        supplied_values={"material": "MAT-100"},
    )
    result = PlanCompiler(store).compile(request, goal_query="기본 데이터")
    assert result.plan is not None
    dumped = result.plan.model_dump()
    assert "TOP-SECRET" not in str(dumped)


def test_collection_column_compiles_map_path_then_closed_table_read(tmp_path):
    store, _ = saved_collection_map(tmp_path)

    result = PlanCompiler(store).compile(
        "tcode MM03 자재 설명 조회", goal_query="자재 설명"
    )

    assert result.validation.valid
    assert result.plan is not None
    assert [step.command for step in result.plan.steps] == [
        "run",
        "ui_select",
        "ui_read_table",
    ]
    target = result.plan.steps[-1]
    assert target.target_logical_id == "material-grid"
    assert target.params == {
        "element_id": "wnd[0]/usr/cntlGRID/shellcont/shell",
        "column": "MAKTX",
        "columns": ["MAKTX"],
    }
    assert target.side_effect == SideEffectClass.READ_ONLY


def test_collection_tree_node_compiles_map_path_then_closed_select(tmp_path):
    store, _ = saved_collection_map(tmp_path)

    result = PlanCompiler(store).compile(
        "tcode MM03 기본 데이터 노드 조회", goal_query="기본 데이터 노드"
    )

    assert result.validation.valid
    assert result.plan is not None
    assert [step.command for step in result.plan.steps] == [
        "run",
        "ui_select",
        "ui_select_node",
    ]
    target = result.plan.steps[-1]
    assert target.target_logical_id == "navigation-tree"
    assert target.params == {
        "element_id": "wnd[0]/usr/cntlTREE/shellcont/shell",
        "node_key": "BASIC",
    }


def test_collection_ambiguity_returns_hitl_validation_without_plan(tmp_path):
    store, _ = saved_collection_map(tmp_path, ambiguous=True)

    result = PlanCompiler(store).compile(
        "tcode MM03 자재 설명 조회", goal_query="자재 설명"
    )

    assert result.plan is None
    assert result.validation.errors == ["collection_target_ambiguous"]
    assert "지정해 주세요" in result.validation.warnings[0]


def test_collection_path_with_user_guided_edge_is_not_a_plan(tmp_path):
    store, _ = saved_collection_map(tmp_path, user_guided=True)

    result = PlanCompiler(store).compile(
        "tcode MM03 자재 설명 조회", goal_query="자재 설명"
    )

    assert result.plan is None
    assert result.validation.errors == ["collection_target_requires_user_guidance"]


def test_only_verified_recipes_are_attached_to_plan(tmp_path):
    store, _ = saved_map(tmp_path)

    class Recipes:
        def query_for_ai(self, **kwargs):
            assert kwargs["tcode"] == "MM03"
            assert kwargs["include_disabled"] is False
            return [
                (0.9, SimpleNamespace(id="verified", success_count=3, failure_count=0)),
                (0.8, SimpleNamespace(id="never-ran", success_count=0, failure_count=0)),
                (0.7, SimpleNamespace(id="unreliable", success_count=1, failure_count=1)),
            ]

    result = PlanCompiler(store, recipe_store=Recipes()).compile(
        "tcode MM03 기본 데이터 조회", goal_query="기본 데이터"
    )

    assert result.plan is not None
    assert result.plan.recipe_ids == ["verified"]
