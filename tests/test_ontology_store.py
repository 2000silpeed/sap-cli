"""OntologyStore 단위 테스트 - COM 불필요."""

import pytest
from pathlib import Path

from sap_cli.ontology.store import OntologyStore, _validate_id
from sap_cli.ontology.schema import (
    ScreenOntology,
    SapField,
    TransactionOntology,
    GuiComponentType,
)


@pytest.fixture
def store(tmp_path):
    return OntologyStore(knowledge_dir=tmp_path / "knowledge")


@pytest.fixture
def sample_screen():
    return ScreenOntology(
        transaction="MM03",
        screen_number=100,
        program="SAPLMGMM",
        title="자재 조회: 초기 화면",
        fields=[
            SapField(
                id="wnd[0]/usr/ctxtRMMG1-MATNR",
                name="RMMG1-MATNR",
                type=GuiComponentType.CTEXT_FIELD,
                label="자재",
                changeable=True,
            ),
        ],
    )


class TestValidateId:
    def test_valid_tcode(self):
        assert _validate_id("MM03", "tcode") == "MM03"
        assert _validate_id("VA01", "tcode") == "VA01"

    def test_path_traversal_rejected(self):
        with pytest.raises(ValueError):
            _validate_id("../../secret", "tcode")
        with pytest.raises(ValueError):
            _validate_id("MM03/../../etc", "tcode")


class TestTransactionCRUD:
    def test_save_and_load(self, store, sample_screen):
        ontology = TransactionOntology(tcode="MM03", title="자재조회")
        ontology.add_screen(sample_screen)
        store.save_transaction(ontology)

        loaded = store.load_transaction("MM03")
        assert loaded is not None
        assert loaded.tcode == "MM03"
        assert 100 in loaded.screens

    def test_load_nonexistent(self, store):
        assert store.load_transaction("ZZZZ") is None

    def test_list_transactions(self, store, sample_screen):
        ontology = TransactionOntology(tcode="MM03")
        ontology.add_screen(sample_screen)
        store.save_transaction(ontology)
        assert "MM03" in store.list_transactions()

    def test_delete(self, store, sample_screen):
        ontology = TransactionOntology(tcode="MM03")
        store.save_transaction(ontology)
        assert store.delete_transaction("MM03") is True
        assert store.load_transaction("MM03") is None


class TestScreenOntology:
    def test_save_and_load_screen(self, store, sample_screen):
        store.save_screen("MM03", sample_screen)
        loaded = store.load_screen("MM03", 100)
        assert loaded is not None
        assert loaded.screen_number == 100
        assert len(loaded.fields) == 1

    def test_load_screen_nonexistent(self, store):
        assert store.load_screen("ZZZZ", 100) is None


class TestPatterns:
    def test_save_and_load_pattern(self, store):
        pattern = {"name": "save_flow", "steps": ["fill", "check", "save"]}
        store.save_pattern("save_flow", pattern)
        loaded = store.load_pattern("save_flow")
        assert loaded is not None
        assert loaded["name"] == "save_flow"

    def test_list_patterns(self, store):
        store.save_pattern("p1", {"name": "p1"})
        store.save_pattern("p2", {"name": "p2"})
        patterns = set(store.list_patterns())
        assert "p1" in patterns
        assert "p2" in patterns

    def test_pattern_path_traversal_rejected(self, store):
        with pytest.raises(ValueError):
            store.save_pattern("../../etc", {"bad": True})


class TestSeedData:
    def test_seed_copies_on_first_init(self, tmp_path):
        """새 디렉토리에 OntologyStore 생성 시 시드 데이터 복사."""
        store = OntologyStore(knowledge_dir=tmp_path / "fresh_knowledge")
        seed_dir = Path(__file__).parent.parent / "sap_cli" / "ontology" / "knowledge"
        if (seed_dir / "transactions" / "MM03.json").exists():
            assert store.load_transaction("MM03") is not None
