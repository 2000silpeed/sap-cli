"""OntologyGraph 단위 테스트 - COM 불필요."""

import pytest

from sap_cli.ontology.graph import OntologyGraph
from sap_cli.ontology.schema import (
    GuiComponentType,
    LinkType,
    SapButton,
    SapField,
    ScreenOntology,
    SemanticContext,
    TransactionOntology,
)
from sap_cli.ontology.store import OntologyStore


@pytest.fixture
def store(tmp_path):
    return OntologyStore(knowledge_dir=tmp_path / "knowledge")


@pytest.fixture
def graph_with_data(store):
    screen1 = ScreenOntology(
        transaction="MM03",
        screen_number=100,
        program="SAPLMGMM",
        title="초기화면",
        fields=[
            SapField(
                id="wnd[0]/usr/ctxtRMMG1-MATNR",
                name="RMMG1-MATNR",
                type=GuiComponentType.CTEXT_FIELD,
                label="자재",
                changeable=True,
                semantic=SemanticContext(sap_field_name="RMMG1-MATNR"),
            ),
        ],
        buttons=[
            SapButton(id="wnd[0]/tbar[1]/btn[8]", name="btn8", text="실행"),
        ],
    )
    screen2 = ScreenOntology(
        transaction="MM03",
        screen_number=200,
        program="SAPLMGMM",
        title="기본데이터",
        fields=[
            SapField(
                id="wnd[0]/usr/txtMAKTX",
                name="MAKTX",
                type=GuiComponentType.TEXT_FIELD,
                label="자재내역",
                semantic=SemanticContext(sap_field_name="MAKTX"),
            ),
        ],
    )

    ontology = TransactionOntology(tcode="MM03", title="자재조회")
    ontology.add_screen(screen1)
    ontology.add_screen(screen2)
    store.save_transaction(ontology)

    # VA01 추가 (같은 화면번호 100 사용 - 네임스페이스 충돌 테스트)
    va01_screen = ScreenOntology(
        transaction="VA01",
        screen_number=100,
        program="SAPMV45A",
        title="수주생성 초기화면",
        fields=[
            SapField(
                id="wnd[0]/usr/ctxtVBAK-AUART",
                name="VBAK-AUART",
                type=GuiComponentType.CTEXT_FIELD,
                label="오더유형",
                changeable=True,
                semantic=SemanticContext(sap_field_name="VBAK-AUART"),
            ),
        ],
    )
    va01 = TransactionOntology(tcode="VA01", title="수주생성")
    va01.add_screen(va01_screen)
    store.save_transaction(va01)

    graph = OntologyGraph(store)
    graph.build_from_store()
    return graph


class TestGraphConstruction:
    def test_builds_nodes(self, graph_with_data):
        stats = graph_with_data.stats()
        assert stats["total_nodes"] > 0
        assert stats["node_types"]["transaction"] >= 2
        assert stats["node_types"]["screen"] >= 3

    def test_transaction_node_exists(self, graph_with_data):
        node = graph_with_data.get_node("tx:MM03")
        assert node is not None
        assert node.node_type == "transaction"

    def test_namespaced_screen_ids(self, graph_with_data):
        """같은 화면번호 100이 트랜잭션별로 분리되는지 확인."""
        mm03_screen = graph_with_data.get_node("screen:MM03:100")
        va01_screen = graph_with_data.get_node("screen:VA01:100")
        assert mm03_screen is not None
        assert va01_screen is not None
        assert mm03_screen.id != va01_screen.id

    def test_namespaced_field_ids(self, graph_with_data):
        """같은 필드가 트랜잭션/화면별로 분리되는지 확인."""
        mm03_field = graph_with_data.get_node("field:MM03:100:RMMG1-MATNR")
        va01_field = graph_with_data.get_node("field:VA01:100:VBAK-AUART")
        assert mm03_field is not None
        assert va01_field is not None


class TestGraphQueries:
    def test_get_transaction_screens(self, graph_with_data):
        screens = graph_with_data.get_transaction_screens("MM03")
        assert len(screens) == 2
        screen_nums = {n.properties["screen_number"] for n in screens}
        assert screen_nums == {100, 200}

    def test_get_screen_fields(self, graph_with_data):
        fields = graph_with_data.get_screen_fields("MM03", 100)
        assert len(fields) == 1
        assert fields[0].label == "자재"

    def test_find_path(self, graph_with_data):
        path = graph_with_data.find_path("tx:MM03", "screen:MM03:200")
        assert path is not None
        assert path[0] == "tx:MM03"
        assert path[-1] == "screen:MM03:200"

    def test_get_parents(self, graph_with_data):
        parents = graph_with_data.get_parents("screen:MM03:100")
        assert len(parents) == 1
        assert parents[0].id == "tx:MM03"

    def test_to_dict(self, graph_with_data):
        d = graph_with_data.to_dict()
        assert "nodes" in d
        assert "edges" in d
        assert "stats" in d
        assert len(d["nodes"]) > 0
