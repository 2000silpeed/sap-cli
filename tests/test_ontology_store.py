"""OntologyStore 단위 테스트 - COM 불필요."""

import json
import pytest
from pathlib import Path

import sap_cli.ontology.store as store_module
from sap_cli.ontology.store import OntologyStore, _validate_id
from sap_cli.ontology.schema import (
    BusinessProcess,
    DataFlow,
    DocumentFlow,
    DocumentFlowEdge,
    FieldDependencyGraph,
    FieldDependencyType,
    FieldRegistry,
    FieldRegistryEntry,
    FieldRelationship,
    ProcessStep,
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


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _fail_atomic_write(*_args, **_kwargs) -> None:
    raise OSError("simulated ontology write failure")


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

    def test_save_failure_preserves_existing_file(self, store, monkeypatch):
        path = store.transactions_dir / "MM03.json"
        old = TransactionOntology(tcode="MM03", title="old").model_dump(
            mode="json", exclude_none=True
        )
        _write_json(path, old)
        before = path.read_text(encoding="utf-8")

        monkeypatch.setattr(store_module, "atomic_write_json", _fail_atomic_write)

        with pytest.raises(OSError):
            store.save_transaction(TransactionOntology(tcode="MM03", title="new"))

        assert path.read_text(encoding="utf-8") == before


class TestScreenOntology:
    def test_save_and_load_screen(self, store, sample_screen):
        store.save_screen("MM03", sample_screen)
        loaded = store.load_screen("MM03", 100)
        assert loaded is not None
        assert loaded.screen_number == 100
        assert len(loaded.fields) == 1

    def test_load_screen_nonexistent(self, store):
        assert store.load_screen("ZZZZ", 100) is None


class TestMetaRegistry:
    def test_save_failure_preserves_existing_file(self, store, monkeypatch):
        path = store.meta_edges_dir / "registry.json"
        _write_json(path, {"edges": []})
        before = path.read_text(encoding="utf-8")
        _ = store.meta_registry

        monkeypatch.setattr(store_module, "atomic_write_json", _fail_atomic_write)

        with pytest.raises(OSError):
            store.save_meta_registry()

        assert path.read_text(encoding="utf-8") == before


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

    def test_save_failure_preserves_existing_file(self, store, monkeypatch):
        path = store.patterns_dir / "save_flow.json"
        _write_json(path, {"name": "old"})
        before = path.read_text(encoding="utf-8")

        monkeypatch.setattr(store_module, "atomic_write_json", _fail_atomic_write)

        with pytest.raises(OSError):
            store.save_pattern("save_flow", {"name": "new"})

        assert path.read_text(encoding="utf-8") == before


class TestBusinessProcess:
    @pytest.fixture
    def sample_process(self):
        return BusinessProcess(
            id="test_procurement",
            name="기본 자재 조달",
            module="MM",
            description="자재 생성 → 구매오더 → 입고",
            steps=[
                ProcessStep(step_number=0, tcode="MM01", description="자재 생성",
                            expected_output=["MATNR"]),
                ProcessStep(step_number=1, tcode="ME21N", description="구매오더 생성",
                            expected_output=["EBELN"]),
            ],
            data_flows=[
                DataFlow(source_step=0, source_field="MATNR",
                         target_step=1, target_field="MATNR",
                         description="자재번호 전달"),
            ],
            tags=["조달", "구매"],
        )

    def test_save_and_load(self, store, sample_process):
        store.save_process(sample_process)
        loaded = store.load_process("test_procurement")
        assert loaded is not None
        assert loaded.id == "test_procurement"
        assert loaded.name == "기본 자재 조달"
        assert len(loaded.steps) == 2
        assert loaded.steps[0].tcode == "MM01"
        assert loaded.steps[1].tcode == "ME21N"

    def test_data_flow(self, store, sample_process):
        store.save_process(sample_process)
        loaded = store.load_process("test_procurement")
        assert len(loaded.data_flows) == 1
        df = loaded.data_flows[0]
        assert df.source_step == 0
        assert df.source_field == "MATNR"
        assert df.target_step == 1
        assert df.target_field == "MATNR"

    def test_list_processes(self, store, sample_process):
        store.save_process(sample_process)
        assert "test_procurement" in store.list_processes()

    def test_delete_process(self, store, sample_process):
        store.save_process(sample_process)
        assert store.delete_process("test_procurement") is True
        assert store.load_process("test_procurement") is None

    def test_load_nonexistent(self, store):
        assert store.load_process("nonexistent") is None

    def test_delete_nonexistent(self, store):
        assert store.delete_process("nonexistent") is False

    def test_path_traversal_rejected(self, store):
        with pytest.raises(ValueError):
            store.save_process(BusinessProcess(id="../../evil", name="bad"))

    def test_save_failure_preserves_existing_file(self, store, sample_process, monkeypatch):
        path = store.processes_dir / "test_procurement.json"
        old = sample_process.model_copy(update={"name": "old"}).model_dump(
            mode="json", exclude_none=True
        )
        _write_json(path, old)
        before = path.read_text(encoding="utf-8")

        monkeypatch.setattr(store_module, "atomic_write_json", _fail_atomic_write)

        with pytest.raises(OSError):
            store.save_process(sample_process.model_copy(update={"name": "new"}))

        assert path.read_text(encoding="utf-8") == before

    def test_validate_integrity_ok(self, sample_process):
        assert sample_process.validate_integrity() == []

    def test_validate_duplicate_step_numbers(self):
        p = BusinessProcess(
            id="dup", name="dup",
            steps=[
                ProcessStep(step_number=0, tcode="MM01"),
                ProcessStep(step_number=0, tcode="ME21N"),
            ],
        )
        errors = p.validate_integrity()
        assert any("중복" in e for e in errors)

    def test_validate_invalid_data_flow_ref(self):
        p = BusinessProcess(
            id="bad_ref", name="bad",
            steps=[ProcessStep(step_number=0, tcode="MM01")],
            data_flows=[DataFlow(source_step=0, source_field="X", target_step=9, target_field="Y")],
        )
        errors = p.validate_integrity()
        assert any("target_step 9" in e for e in errors)

    def test_validate_backward_data_flow(self):
        p = BusinessProcess(
            id="backward", name="backward",
            steps=[
                ProcessStep(step_number=0, tcode="MM01"),
                ProcessStep(step_number=1, tcode="ME21N"),
            ],
            data_flows=[DataFlow(source_step=1, source_field="X", target_step=0, target_field="Y")],
        )
        errors = p.validate_integrity()
        assert any("크거나 같습니다" in e for e in errors)

    def test_seed_process(self, tmp_path):
        """processes 시드 데이터도 복사되는지 확인."""
        store = OntologyStore(knowledge_dir=tmp_path / "fresh")
        seed_dir = Path(__file__).parent.parent / "sap_cli" / "ontology" / "knowledge"
        if (seed_dir / "processes" / "procurement_basic.json").exists():
            assert store.load_process("procurement_basic") is not None


class TestSeedData:
    def test_seed_copies_on_first_init(self, tmp_path):
        """새 디렉토리에 OntologyStore 생성 시 시드 데이터 복사."""
        store = OntologyStore(knowledge_dir=tmp_path / "fresh_knowledge")
        seed_dir = Path(__file__).parent.parent / "sap_cli" / "ontology" / "knowledge"
        if (seed_dir / "transactions" / "MM03.json").exists():
            assert store.load_transaction("MM03") is not None


class TestOntologyGlobalIndexes:
    def test_save_field_registry_failure_preserves_existing_file(self, store, monkeypatch):
        path = store.knowledge_dir / "field_registry.json"
        old = FieldRegistry(
            fields={
                "MATNR": FieldRegistryEntry(
                    sap_field_name="MATNR",
                    description="old",
                )
            }
        ).model_dump(mode="json", exclude_none=True)
        _write_json(path, old)
        before = path.read_text(encoding="utf-8")

        monkeypatch.setattr(store_module, "atomic_write_json", _fail_atomic_write)

        with pytest.raises(OSError):
            store.save_field_registry(
                FieldRegistry(
                    fields={
                        "MATNR": FieldRegistryEntry(
                            sap_field_name="MATNR",
                            description="new",
                        )
                    }
                )
            )

        assert path.read_text(encoding="utf-8") == before

    def test_save_field_dependencies_failure_preserves_existing_file(
        self, store, monkeypatch
    ):
        path = store.knowledge_dir / "field_relationships.json"
        old = FieldDependencyGraph(
            relationships=[
                FieldRelationship(
                    source_field="MATNR",
                    target_field="MAKTX",
                    dependency_type=FieldDependencyType.LOOKUP,
                    description="old",
                )
            ]
        ).model_dump(mode="json", exclude_none=True)
        _write_json(path, old)
        before = path.read_text(encoding="utf-8")

        monkeypatch.setattr(store_module, "atomic_write_json", _fail_atomic_write)

        with pytest.raises(OSError):
            store.save_field_dependencies(
                FieldDependencyGraph(
                    relationships=[
                        FieldRelationship(
                            source_field="WERKS",
                            target_field="LGORT",
                            dependency_type=FieldDependencyType.VALUE_DETERMINES,
                            description="new",
                        )
                    ]
                )
            )

        assert path.read_text(encoding="utf-8") == before

    def test_save_document_flow_failure_preserves_existing_file(self, store, monkeypatch):
        path = store.knowledge_dir / "document_flow.json"
        old = DocumentFlow(
            edges=[DocumentFlowEdge(source_tcode="ME21N", target_tcode="MIGO")]
        ).model_dump(mode="json", exclude_none=True)
        _write_json(path, old)
        before = path.read_text(encoding="utf-8")

        monkeypatch.setattr(store_module, "atomic_write_json", _fail_atomic_write)

        with pytest.raises(OSError):
            store.save_document_flow(
                DocumentFlow(
                    edges=[DocumentFlowEdge(source_tcode="VA01", target_tcode="VL01N")]
                )
            )

        assert path.read_text(encoding="utf-8") == before
