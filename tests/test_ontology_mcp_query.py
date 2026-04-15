"""온톨로지 MCP 쿼리 확장 (Phase 15-3) 테스트 — 시맨틱 검색 + 확장 쿼리 타입."""

import pytest

from sap_cli.ontology.schema import (
    BusinessProcess,
    DataFlow,
    FieldDependencyGraph,
    FieldDependencyType,
    FieldOccurrence,
    FieldRegistry,
    FieldRegistryEntry,
    FieldRelationship,
    GuiComponentType,
    ProcessStep,
    SapField,
    ScreenOntology,
    SemanticContext,
    TransactionOntology,
)
from sap_cli.ontology.semantic_search import (
    SemanticMatch,
    semantic_field_search,
    semantic_tcode_search,
)
from sap_cli.ontology.store import OntologyStore


# ===================================================================
# Fixtures
# ===================================================================

@pytest.fixture
def store(tmp_path):
    return OntologyStore(knowledge_dir=tmp_path / "knowledge")


@pytest.fixture
def sample_va01():
    screen = ScreenOntology(
        transaction="VA01",
        screen_number=100,
        program="SAPMV45A",
        title="판매오더 생성: 초기 화면",
        fields=[
            SapField(
                id="wnd[0]/usr/ctxtVBAK-AUART",
                name="VBAK-AUART",
                type=GuiComponentType.CTEXT_FIELD,
                label="오더 유형",
                changeable=True,
                required=True,
                semantic=SemanticContext(
                    sap_field_name="AUART",
                    description="Sales Order Type",
                    description_ko="판매오더 유형",
                    synonyms=["order type", "doc type"],
                    synonyms_ko=["오더유형", "문서유형"],
                    validation="2자리 영문 (OR, SO 등)",
                    domain_context="판매오더 생성 시 첫 번째로 입력. 후속 화면 구성을 결정한다.",
                ),
            ),
            SapField(
                id="wnd[0]/usr/ctxtVBAK-VKORG",
                name="VBAK-VKORG",
                type=GuiComponentType.CTEXT_FIELD,
                label="판매 조직",
                changeable=True,
                required=True,
                semantic=SemanticContext(
                    sap_field_name="VKORG",
                    description="Sales Organization",
                    validation="4자리 숫자",
                    domain_context="판매 영역의 최상위 조직 단위",
                ),
            ),
            SapField(
                id="wnd[0]/usr/ctxtKUNNR",
                name="KUAG-KUNNR",
                type=GuiComponentType.CTEXT_FIELD,
                label="주문자",
                changeable=True,
                required=True,
                semantic=SemanticContext(
                    sap_field_name="KUNNR",
                    description="Customer Number",
                    synonyms=["sold-to party"],
                    synonyms_ko=["고객번호", "주문자"],
                    domain_context="고객 마스터에서 관리되는 비즈니스 파트너 번호",
                ),
            ),
        ],
    )
    ont = TransactionOntology(
        tcode="VA01",
        title="판매오더 생성",
        module="SD",
        description="판매오더를 생성하는 트랜잭션",
        related_transactions=["VL01N", "VF01"],
    )
    ont.add_screen(screen)
    return ont


@pytest.fixture
def sample_mm03():
    screen = ScreenOntology(
        transaction="MM03",
        screen_number=4000,
        program="SAPLMGMM",
        title="자재 조회: 기본 데이터",
        fields=[
            SapField(
                id="wnd[0]/usr/ctxtRMMG1-MATNR",
                name="RMMG1-MATNR",
                type=GuiComponentType.CTEXT_FIELD,
                label="자재",
                changeable=False,
                required=True,
                semantic=SemanticContext(
                    sap_field_name="MATNR",
                    description="Material Number",
                    description_ko="자재 번호",
                    synonyms=["material", "품번"],
                    synonyms_ko=["자재번호", "품번"],
                    validation="18자리 영숫자",
                    domain_context="자재 마스터 고유 식별자. MM01(생성), MM02(변경), MM03(조회)에서 공통 사용",
                ),
            ),
            SapField(
                id="wnd[0]/usr/ctxtRMMG1-WERKS",
                name="RMMG1-WERKS",
                type=GuiComponentType.CTEXT_FIELD,
                label="플랜트",
                changeable=False,
                semantic=SemanticContext(
                    sap_field_name="WERKS",
                    description="Plant",
                    description_ko="플랜트",
                    synonyms=["plant", "factory"],
                    synonyms_ko=["공장", "사업장"],
                    domain_context="MRP, 재고관리, 원가계산의 조직 단위",
                ),
            ),
        ],
    )
    ont = TransactionOntology(tcode="MM03", title="자재 조회", module="MM")
    ont.add_screen(screen)
    return ont


@pytest.fixture
def sample_me21n():
    screen = ScreenOntology(
        transaction="ME21N",
        screen_number=200,
        program="SAPLME21N",
        title="구매오더 생성",
        fields=[
            SapField(
                id="wnd[0]/usr/ctxtEKKO-WERKS",
                name="EKKO-WERKS",
                type=GuiComponentType.CTEXT_FIELD,
                label="플랜트",
                changeable=True,
                semantic=SemanticContext(
                    sap_field_name="WERKS",
                    description="Plant",
                    synonyms_ko=["공장"],
                    domain_context="구매오더의 납품 플랜트",
                ),
            ),
        ],
    )
    ont = TransactionOntology(tcode="ME21N", title="구매오더 생성", module="MM")
    ont.add_screen(screen)
    return ont


@pytest.fixture
def populated_store(store, sample_va01, sample_mm03, sample_me21n):
    store.save_transaction(sample_va01)
    store.save_transaction(sample_mm03)
    store.save_transaction(sample_me21n)

    proc = BusinessProcess(
        id="sales_to_billing",
        name="판매-대금청구 프로세스",
        module="SD",
        description="판매오더 → 출하 → 대금청구",
        steps=[
            ProcessStep(step_number=0, tcode="VA01", description="판매오더 생성", input_fields={"AUART": "OR"}),
            ProcessStep(step_number=1, tcode="VL01N", description="출하 생성"),
            ProcessStep(step_number=2, tcode="VF01", description="대금청구 생성"),
        ],
        data_flows=[
            DataFlow(source_step=0, source_field="VBELN", target_step=1, target_field="VBELN", description="판매오더번호 전달"),
        ],
    )
    store.save_process(proc)

    registry = FieldRegistry(fields={
        "WERKS": FieldRegistryEntry(
            sap_field_name="WERKS",
            description="Plant",
            occurrences=[
                FieldOccurrence(tcode="MM03", screen_number=4000, element_id="wnd[0]/usr/ctxtRMMG1-WERKS", label="플랜트", changeable=False),
                FieldOccurrence(tcode="ME21N", screen_number=200, element_id="wnd[0]/usr/ctxtEKKO-WERKS", label="플랜트", changeable=True),
            ],
        ),
        "MATNR": FieldRegistryEntry(
            sap_field_name="MATNR",
            description="Material Number",
            occurrences=[
                FieldOccurrence(tcode="MM03", screen_number=4000, element_id="wnd[0]/usr/ctxtRMMG1-MATNR", label="자재", changeable=False),
            ],
        ),
    })
    store.save_field_registry(registry)

    dep_graph = FieldDependencyGraph(relationships=[
        FieldRelationship(
            source_field="WERKS",
            target_field="LGORT",
            dependency_type=FieldDependencyType.VALUE_DETERMINES,
            description="플랜트에 따라 저장위치 목록이 결정됨",
            observed_in=["MM03", "MIGO"],
        ),
        FieldRelationship(
            source_field="AUART",
            target_field="VKORG",
            dependency_type=FieldDependencyType.FIELD_ACTIVATION,
            description="오더유형에 따라 판매조직 입력 범위가 달라짐",
            observed_in=["VA01"],
        ),
    ])
    store.save_field_dependencies(dep_graph)

    return store


# ===================================================================
# 시맨틱 검색 테스트
# ===================================================================

class TestSemanticFieldSearch:

    def test_exact_field_name(self, populated_store):
        results = semantic_field_search(populated_store, "MATNR")
        assert len(results) >= 1
        assert results[0].sap_field_name == "MATNR"
        assert results[0].score == 1.0

    def test_korean_synonym(self, populated_store):
        results = semantic_field_search(populated_store, "자재번호")
        assert len(results) >= 1
        top = results[0]
        assert top.sap_field_name == "MATNR"
        assert top.score > 0.5

    def test_english_synonym(self, populated_store):
        results = semantic_field_search(populated_store, "order type")
        assert len(results) >= 1
        found = [r for r in results if r.sap_field_name == "AUART"]
        assert len(found) >= 1

    def test_label_match(self, populated_store):
        results = semantic_field_search(populated_store, "판매 조직")
        assert len(results) >= 1
        found = [r for r in results if r.sap_field_name == "VKORG"]
        assert len(found) >= 1

    def test_domain_context_match(self, populated_store):
        results = semantic_field_search(populated_store, "재고관리")
        found = [r for r in results if r.sap_field_name == "WERKS"]
        assert len(found) >= 1

    def test_no_match(self, populated_store):
        results = semantic_field_search(populated_store, "xyznonexistent")
        assert len(results) == 0

    def test_cross_transaction_field(self, populated_store):
        results = semantic_field_search(populated_store, "WERKS")
        tcodes = {r.tcode for r in results}
        assert "MM03" in tcodes
        assert "ME21N" in tcodes


class TestSemanticTcodeSearch:

    def test_korean_keyword(self, populated_store):
        results = semantic_tcode_search(populated_store, "자재조회")
        assert len(results) >= 1
        assert results[0]["tcode"] == "MM03"

    def test_title_match(self, populated_store):
        results = semantic_tcode_search(populated_store, "구매오더")
        found = [r for r in results if r["tcode"] == "ME21N"]
        assert len(found) >= 1

    def test_exact_tcode(self, populated_store):
        results = semantic_tcode_search(populated_store, "VA01")
        assert len(results) >= 1
        assert results[0]["tcode"] == "VA01"


# ===================================================================
# dispatcher 확장 쿼리 타입 테스트
# ===================================================================

class TestDispatchOntologyQueryExtended:

    def test_field_semantic_query(self, populated_store):
        from sap_cli.dispatcher import _dispatch_ontology_query_extended
        result = _dispatch_ontology_query_extended(
            populated_store, "field_semantic", {"field_search": "품번"}
        )
        assert result["query_type"] == "field_semantic"
        assert result["count"] >= 1
        found = [r for r in result["results"] if r["sap_field_name"] == "MATNR"]
        assert len(found) >= 1

    def test_field_semantic_no_match(self, populated_store):
        from sap_cli.dispatcher import _dispatch_ontology_query_extended
        result = _dispatch_ontology_query_extended(
            populated_store, "field_semantic", {"field_search": "zzzznonexistent"}
        )
        assert result["count"] == 0
        assert "hint" in result

    def test_field_semantic_missing_param(self, populated_store):
        from sap_cli.dispatcher import _dispatch_ontology_query_extended
        with pytest.raises(ValueError, match="field_search"):
            _dispatch_ontology_query_extended(populated_store, "field_semantic", {})

    def test_field_cross_from_registry(self, populated_store):
        from sap_cli.dispatcher import _dispatch_ontology_query_extended
        result = _dispatch_ontology_query_extended(
            populated_store, "field_cross", {"field_search": "WERKS"}
        )
        assert result["query_type"] == "field_cross"
        assert result["transaction_count"] == 2
        assert result["total_occurrences"] == 2

    def test_field_cross_fallback_semantic(self, populated_store):
        from sap_cli.dispatcher import _dispatch_ontology_query_extended
        result = _dispatch_ontology_query_extended(
            populated_store, "field_cross", {"field_search": "고객번호"}
        )
        assert result["query_type"] == "field_cross"
        assert result["total_occurrences"] >= 1

    def test_field_cross_no_match(self, populated_store):
        from sap_cli.dispatcher import _dispatch_ontology_query_extended
        result = _dispatch_ontology_query_extended(
            populated_store, "field_cross", {"field_search": "ZZZZFIELD"}
        )
        assert result["count"] == 0

    def test_process_guide_by_id(self, populated_store):
        from sap_cli.dispatcher import _dispatch_ontology_query_extended
        result = _dispatch_ontology_query_extended(
            populated_store, "process_guide", {"field_search": "sales_to_billing"}
        )
        assert result["query_type"] == "process_guide"
        assert result["step_count"] == 3
        assert len(result["data_flows"]) == 1

    def test_process_guide_by_korean(self, populated_store):
        from sap_cli.dispatcher import _dispatch_ontology_query_extended
        result = _dispatch_ontology_query_extended(
            populated_store, "process_guide", {"field_search": "판매흐름"}
        )
        assert result["query_type"] == "process_guide"
        assert result["name"] == "판매-대금청구 프로세스"

    def test_process_guide_list(self, populated_store):
        from sap_cli.dispatcher import _dispatch_ontology_query_extended
        result = _dispatch_ontology_query_extended(
            populated_store, "process_guide", {}
        )
        assert "available_processes" in result

    def test_dependency_chain(self, populated_store):
        from sap_cli.dispatcher import _dispatch_ontology_query_extended
        result = _dispatch_ontology_query_extended(
            populated_store, "dependency_chain", {"field_search": "WERKS"}
        )
        assert result["query_type"] == "dependency_chain"
        assert result["field"] == "WERKS"
        assert len(result["determines"]) >= 1
        assert any("LGORT" in d.get("field", d.get("target_field", "")) for d in result["determines"])

    def test_dependency_chain_with_downstream(self, populated_store):
        from sap_cli.dispatcher import _dispatch_ontology_query_extended
        result = _dispatch_ontology_query_extended(
            populated_store, "dependency_chain", {"field_search": "AUART"}
        )
        assert result["field"] == "AUART"
        chain = result["downstream_chain"]
        assert any("VKORG" in entry for entry in chain)

    def test_dependency_chain_missing_param(self, populated_store):
        from sap_cli.dispatcher import _dispatch_ontology_query_extended
        with pytest.raises(ValueError, match="field_search"):
            _dispatch_ontology_query_extended(populated_store, "dependency_chain", {})

    def test_unknown_query_type(self, populated_store):
        from sap_cli.dispatcher import _dispatch_ontology_query_extended
        with pytest.raises(ValueError, match="알 수 없는"):
            _dispatch_ontology_query_extended(populated_store, "invalid_type", {})
