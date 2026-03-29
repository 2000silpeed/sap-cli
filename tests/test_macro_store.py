"""MacroStore 단위 테스트 - COM 불필요."""

import pytest
from pathlib import Path

from sap_cli.macro.store import MacroStore, _validate_id
from sap_cli.macro.schema import Macro, MacroStep
from sap_cli.ontology.schema import ActionType


@pytest.fixture
def store(tmp_path):
    return MacroStore(library_dir=tmp_path)


@pytest.fixture
def sample_macro():
    return Macro(
        id="test_macro_01",
        name="MM03 자재조회",
        tcode="MM03",
        description="자재 마스터 조회 매크로",
        steps=[
            MacroStep(
                step_number=1,
                action=ActionType.RUN_TRANSACTION,
                target_id="MM03",
                value="MM03",
            ),
            MacroStep(
                step_number=2,
                action=ActionType.SET_FIELD_VALUE,
                target_id="wnd[0]/usr/ctxtRMMG1-MATNR",
                value="100-100",
            ),
        ],
    )


class TestValidateId:
    def test_valid_ids(self):
        assert _validate_id("MM03", "tcode") == "MM03"
        assert _validate_id("test_macro_01", "id") == "test_macro_01"
        assert _validate_id("my-macro", "id") == "my-macro"

    def test_invalid_ids(self):
        with pytest.raises(ValueError):
            _validate_id("../../etc", "id")
        with pytest.raises(ValueError):
            _validate_id("", "id")
        with pytest.raises(ValueError):
            _validate_id("path/traversal", "id")
        with pytest.raises(ValueError):
            _validate_id("has space", "id")


class TestMacroStore:
    def test_save_and_load(self, store, sample_macro):
        store.save(sample_macro)
        loaded = store.load(sample_macro.id)
        assert loaded is not None
        assert loaded.id == sample_macro.id
        assert loaded.name == sample_macro.name
        assert len(loaded.steps) == 2

    def test_load_nonexistent(self, store):
        assert store.load("nonexistent") is None

    def test_list_macros(self, store, sample_macro):
        store.save(sample_macro)
        macros = store.list_macros()
        assert len(macros) == 1
        assert macros[0]["id"] == "test_macro_01"
        assert macros[0]["tcode"] == "MM03"

    def test_delete(self, store, sample_macro):
        store.save(sample_macro)
        assert store.delete(sample_macro.id) is True
        assert store.load(sample_macro.id) is None
        assert store.delete("nonexistent") is False

    def test_find_by_tcode(self, store, sample_macro):
        store.save(sample_macro)
        results = store.find_by_tcode("MM03")
        assert len(results) == 1
        assert results[0].tcode == "MM03"
        assert store.find_by_tcode("VA01") == []

    def test_find_by_name(self, store, sample_macro):
        store.save(sample_macro)
        results = store.find_by_name("자재")
        assert len(results) == 1
        assert store.find_by_name("없는이름") == []

    def test_path_traversal_rejected(self, store):
        with pytest.raises(ValueError):
            store.load("../../etc/passwd")
        with pytest.raises(ValueError):
            store.delete("../secret")
