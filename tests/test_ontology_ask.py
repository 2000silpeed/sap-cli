"""온톨로지 Q&A (Phase 15-2) 테스트 — 질문 파싱 + 답변 포매터."""

import pytest

from sap_cli.ontology.question_parser import (
    QuestionType,
    parse_question,
)
from sap_cli.ontology.answer_formatter import OntologyAnswerFormatter
from sap_cli.ontology.graph import OntologyGraph
from sap_cli.ontology.store import OntologyStore
from sap_cli.ontology.schema import (
    BusinessProcess,
    DataFlow,
    GuiComponentType,
    ProcessStep,
    SapField,
    ScreenOntology,
    SemanticContext,
    TransactionOntology,
)


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
                    validation="18자리 영숫자",
                    domain_context="자재 마스터 고유 식별자",
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
                ),
            ),
        ],
    )
    ont = TransactionOntology(tcode="MM03", title="자재 조회", module="MM")
    ont.add_screen(screen)
    return ont


@pytest.fixture
def populated_store(store, sample_va01, sample_mm03):
    store.save_transaction(sample_va01)
    store.save_transaction(sample_mm03)
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
    return store


@pytest.fixture
def formatter(populated_store):
    graph = OntologyGraph(populated_store)
    graph.build_from_store()
    return OntologyAnswerFormatter(populated_store, graph)


# ===================================================================
# 질문 파서 테스트
# ===================================================================

class TestQuestionParser:
    def test_transaction_summary_by_tcode(self):
        p = parse_question("VA01이 뭐야?")
        assert p.question_type == QuestionType.TRANSACTION_SUMMARY
        assert "VA01" in p.tcodes

    def test_transaction_summary_by_korean(self):
        p = parse_question("판매오더 알려줘")
        assert p.question_type == QuestionType.TRANSACTION_SUMMARY
        assert "VA01" in p.tcodes

    def test_korean_tcode_not_misclassified_as_process(self):
        p = parse_question("판매오더 화면 구조")
        assert p.question_type == QuestionType.TRANSACTION_SUMMARY
        assert "VA01" in p.tcodes

    def test_required_fields(self):
        p = parse_question("VA01 필수 필드")
        assert p.question_type == QuestionType.REQUIRED_FIELDS
        assert "VA01" in p.tcodes

    def test_field_meaning_by_sap_name(self):
        p = parse_question("AUART가 뭐야?")
        assert p.question_type == QuestionType.FIELD_MEANING
        assert "AUART" in p.fields

    def test_field_meaning_by_korean(self):
        p = parse_question("자재번호 뭐야?")
        assert p.question_type == QuestionType.FIELD_MEANING
        assert "MATNR" in p.fields

    def test_field_cross(self):
        p = parse_question("WERKS가 어디 나와?")
        assert p.question_type == QuestionType.FIELD_CROSS
        assert "WERKS" in p.fields

    def test_field_dependency(self):
        p = parse_question("AUART 바꾸면 뭐가 변해?")
        assert p.question_type == QuestionType.FIELD_DEPENDENCY
        assert "AUART" in p.fields

    def test_process(self):
        p = parse_question("판매 프로세스 알려줘")
        assert p.question_type == QuestionType.PROCESS
        assert "sales_to_billing" in p.process_keywords

    def test_pattern(self):
        p = parse_question("저장 흐름")
        assert p.question_type == QuestionType.PATTERN
        assert "저장" in p.pattern_keywords

    def test_macro_variable_pattern(self):
        p = parse_question("매크로 등록 변수화 정책")
        assert p.question_type == QuestionType.PATTERN
        assert "매크로" in p.pattern_keywords

    def test_korean_field_cross(self):
        p = parse_question("플랜트 어디서 사용?")
        assert p.question_type == QuestionType.FIELD_CROSS
        assert "WERKS" in p.fields

    def test_multiple_tcodes(self):
        p = parse_question("VA01이랑 MM03 비교해줘")
        assert "VA01" in p.tcodes
        assert "MM03" in p.tcodes


# ===================================================================
# 답변 포매터 테스트
# ===================================================================

class TestAnswerFormatter:
    def test_transaction_summary(self, formatter):
        parsed = parse_question("VA01이 뭐야?")
        result = formatter.answer(parsed)
        assert result["tcode"] == "VA01"
        assert result["title"] == "판매오더 생성"
        assert result["module"] == "SD"
        assert len(result["screens"]) > 0

    def test_transaction_not_found(self, formatter):
        parsed = parse_question("XK01이 뭐야?")
        result = formatter.answer(parsed)
        assert "error" in result or ("error" in result.get("transactions", [{}])[0] if "transactions" in result else "error" in result)

    def test_required_fields(self, formatter):
        parsed = parse_question("VA01 필수 필드")
        result = formatter.answer(parsed)
        assert result["tcode"] == "VA01"
        screens_with_req = result["screens"]
        assert len(screens_with_req) > 0
        all_req = [f for s in screens_with_req for f in s["required_fields"]]
        sap_names = [f["sap_field_name"] for f in all_req]
        assert "AUART" in sap_names
        assert "VKORG" in sap_names

    def test_field_meaning(self, formatter):
        parsed = parse_question("AUART가 뭐야?")
        result = formatter.answer(parsed)
        assert result["sap_field_name"] == "AUART"
        assert result["description"] == "Sales Order Type"
        assert len(result["occurrences"]) > 0

    def test_field_cross(self, formatter):
        parsed = parse_question("MATNR 어디서 사용?")
        result = formatter.answer(parsed)
        assert "field" in result
        assert result["field"] == "MATNR"
        locs = result.get("locations", [])
        assert any(loc["tcode"] == "MM03" for loc in locs)

    def test_process(self, formatter):
        parsed = parse_question("판매 프로세스")
        result = formatter.answer(parsed)
        assert result["name"] == "판매-대금청구 프로세스"
        assert len(result["steps"]) == 3

    def test_field_dependency_no_data(self, formatter):
        parsed = parse_question("AUART 종속 필드")
        result = formatter.answer(parsed)
        assert result["field"] == "AUART"

    def test_korean_transaction_query(self, formatter):
        parsed = parse_question("자재 마스터 알려줘")
        result = formatter.answer(parsed)
        assert result["tcode"] == "MM03"

    def test_result_includes_question_type(self, formatter):
        parsed = parse_question("VA01 필수 필드")
        result = formatter.answer(parsed)
        assert result["question_type"] == "REQUIRED_FIELDS"
        assert result["question"] == "VA01 필수 필드"

    def test_pattern_with_save_keyword(self, formatter, populated_store):
        populated_store.save_pattern("save_flow", {"name": "저장 흐름", "sequence": [{"step": "Ctrl+S"}]})
        parsed = parse_question("저장 패턴")
        result = formatter.answer(parsed)
        assert result["pattern_name"] == "save_flow"

    def test_pattern_with_macro_variable_keyword(self, formatter, populated_store):
        populated_store.save_pattern(
            "macro_variable_registration",
            {"name": "매크로 변수화", "policy": {"variableization": "forced"}},
        )
        parsed = parse_question("매크로 등록 변수화 정책")
        result = formatter.answer(parsed)
        assert result["pattern_name"] == "macro_variable_registration"
        assert result["policy"]["variableization"] == "forced"

    def test_graph_lazy_build(self, populated_store):
        """그래프는 필드 종속성 질문에서만 빌드됨."""
        formatter = OntologyAnswerFormatter(populated_store)
        assert not formatter._graph_built
        parsed = parse_question("VA01이 뭐야?")
        formatter.answer(parsed)
        assert not formatter._graph_built
        parsed = parse_question("AUART 종속 필드")
        formatter.answer(parsed)
        assert formatter._graph_built
