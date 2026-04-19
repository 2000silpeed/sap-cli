"""Phase 18-3: 레시피 버전닝 — _archive 폴더 + version_up/history 테스트."""

from __future__ import annotations

from pathlib import Path

import pytest

from sap_cli.recipe import Recipe, RecipeStep, RecipeStore


@pytest.fixture
def store(tmp_path: Path) -> RecipeStore:
    return RecipeStore(store_dir=tmp_path / "recipes")


def _save_v1(store: RecipeStore, rid: str = "va01-create",
             tcode: str = "VA01", success: int = 7) -> Recipe:
    r = Recipe(
        id=rid,
        description="VA01 신규 생성",
        context="원본 컨텍스트",
        keywords=["수주", tcode.lower()],
        steps=[
            RecipeStep(tool="sap_run", args={"tcode": tcode}),
            RecipeStep(tool="sap_set", args={"field": "F1", "value": "x"}),
            RecipeStep(tool="sap_press", args={"element": "btn[5]"}),
        ],
        success_count=success,
    )
    store.save(r)
    return r


class TestSchema:
    def test_default_version_is_1(self):
        r = Recipe(id="x", description="x")
        assert r.version == 1
        assert r.previous_version_archive == ""

    def test_legacy_json_loads_with_v1(self, store: RecipeStore, tmp_path: Path):
        legacy = tmp_path / "recipes" / "legacy.json"
        legacy.write_text(
            '{"id":"legacy","description":"old","steps":[]}',
            encoding="utf-8",
        )
        r = store.load("legacy")
        assert r is not None
        assert r.version == 1


class TestArchiveDir:
    def test_archive_dir_lazy_created_on_version_up(self, store: RecipeStore):
        # 평소엔 _archive 디렉토리를 만들지 않음 (atomic save 회귀 테스트 보존)
        assert not store._archive_dir.exists()
        _save_v1(store)
        store.version_up(
            "va01-create",
            new_steps=[RecipeStep(tool="sap_run", args={"tcode": "VA01"})],
        )
        assert store._archive_dir.exists()

    def test_archive_excluded_from_listing(self, store: RecipeStore):
        _save_v1(store, "active")
        # _archive 안에 임의 json — list_all/search에 잡히면 안 됨
        store._archive_dir.mkdir(parents=True, exist_ok=True)
        (store._archive_dir / "garbage.json").write_text(
            '{"id":"hidden","description":"hidden","steps":[]}', encoding="utf-8",
        )
        listed = store.list_all()
        ids = {item["id"] for item in listed}
        assert ids == {"active"}

        # search도 _archive 미스캔
        assert store.search("hidden") == []


class TestVersionUp:
    def test_version_increments(self, store: RecipeStore):
        _save_v1(store)
        new_steps = [
            RecipeStep(tool="sap_run", args={"tcode": "VA01"}),
            RecipeStep(tool="sap_press", args={"element": "btn[7]"}),  # 변경
        ]
        new = store.version_up("va01-create", new_steps=new_steps,
                               change_note="EHP8 패치로 btn[5]→btn[7]")

        assert new is not None
        assert new.version == 2
        assert new.previous_version_archive.startswith("va01-create__v1__")
        assert "btn[7]" in str(new.steps[-1].args)

    def test_archives_previous_file(self, store: RecipeStore):
        _save_v1(store)
        new_steps = [RecipeStep(tool="sap_run", args={"tcode": "VA01"})]
        new = store.version_up("va01-create", new_steps=new_steps)

        assert new is not None
        archive_path = store._archive_dir / new.previous_version_archive
        assert archive_path.exists()
        archived = store.load_archive(new.previous_version_archive)
        assert archived is not None
        assert archived.version == 1
        assert len(archived.steps) == 3

    def test_metadata_carried_over(self, store: RecipeStore):
        _save_v1(store, success=12)
        new = store.version_up(
            "va01-create",
            new_steps=[RecipeStep(tool="sap_run", args={"tcode": "VA01"})],
        )
        assert new is not None
        assert new.success_count == 12  # 승계
        assert new.description == "VA01 신규 생성"
        assert new.keywords == ["수주", "va01"]

    def test_failure_state_reset(self, store: RecipeStore):
        r = _save_v1(store)
        r.failure_count = 5
        r.disabled = True
        r.disabled_reason = "broken"
        store.save(r)
        new = store.version_up(
            "va01-create",
            new_steps=[RecipeStep(tool="sap_run", args={"tcode": "VA01"})],
        )
        assert new is not None
        assert new.failure_count == 0
        assert new.disabled is False
        assert new.disabled_reason == ""

    def test_change_note_appended(self, store: RecipeStore):
        _save_v1(store)
        new = store.version_up(
            "va01-create",
            new_steps=[RecipeStep(tool="sap_run", args={"tcode": "VA01"})],
            change_note="버튼 ID 변경",
        )
        assert new is not None
        assert "[v2 change_note] 버튼 ID 변경" in new.context
        assert "원본 컨텍스트" in new.context

    def test_unknown_returns_none(self, store: RecipeStore):
        result = store.version_up("nope", new_steps=[RecipeStep(tool="sap_run", args={})])
        assert result is None


class TestHistory:
    def test_empty_history(self, store: RecipeStore):
        _save_v1(store)
        items = store.history("va01-create")
        assert items == []

    def test_history_after_two_version_ups(self, store: RecipeStore):
        _save_v1(store)
        store.version_up(
            "va01-create",
            new_steps=[RecipeStep(tool="sap_run", args={"tcode": "VA01"})],
        )
        store.version_up(
            "va01-create",
            new_steps=[RecipeStep(tool="sap_run", args={"tcode": "VA01"}),
                       RecipeStep(tool="sap_press", args={"element": "btn[8]"})],
        )
        items = store.history("va01-create")
        versions = [i["version"] for i in items]
        assert versions == [2, 1]

    def test_load_archive_security(self, store: RecipeStore):
        with pytest.raises(ValueError):
            store.load_archive("../etc/passwd")
        with pytest.raises(ValueError):
            store.load_archive("foo/bar.json")


class TestDispatchVersioning:
    def test_dispatch_version_up(self, store: RecipeStore, monkeypatch: pytest.MonkeyPatch):
        from sap_cli.dispatcher import _dispatch_recipe_version_up

        _save_v1(store)
        monkeypatch.setattr("sap_cli.dispatcher._get_recipe_store", lambda: store)
        # 온톨로지 사이드이펙트 차단
        monkeypatch.setattr("sap_cli.dispatcher._unlink_recipe_from_ontology", lambda r: [])
        monkeypatch.setattr("sap_cli.dispatcher._link_recipe_to_ontology",
                            lambda *a, **k: [])

        result = _dispatch_recipe_version_up({
            "recipe_id": "va01-create",
            "new_steps": [{"tool": "sap_run", "args": {"tcode": "VA01"}}],
            "change_note": "테스트",
        })

        assert result["new_version"] == 2
        assert result["previous_version_archive"].startswith("va01-create__v1__")

    def test_dispatch_version_up_empty_steps(self, store: RecipeStore, monkeypatch: pytest.MonkeyPatch):
        from sap_cli.dispatcher import _dispatch_recipe_version_up

        _save_v1(store)
        monkeypatch.setattr("sap_cli.dispatcher._get_recipe_store", lambda: store)

        result = _dispatch_recipe_version_up({
            "recipe_id": "va01-create",
            "new_steps": [],
        })
        assert result.get("error") is True

    def test_dispatch_history(self, store: RecipeStore, monkeypatch: pytest.MonkeyPatch):
        from sap_cli.dispatcher import _dispatch_recipe_history

        _save_v1(store)
        store.version_up(
            "va01-create",
            new_steps=[RecipeStep(tool="sap_run", args={"tcode": "VA01"})],
        )
        monkeypatch.setattr("sap_cli.dispatcher._get_recipe_store", lambda: store)

        result = _dispatch_recipe_history({"recipe_id": "va01-create"})
        assert result["current_version"] == 2
        assert result["archive_count"] == 1
