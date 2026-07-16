from __future__ import annotations

import json
import shutil
from concurrent.futures import ThreadPoolExecutor

import pytest

from sap_cli.ui_map.fingerprint import compute_map_fingerprint
from sap_cli.ui_map.schema import (
    MapManifest,
    SemanticLink,
    Transition,
    UiComponent,
    UiMap,
    UiState,
    UserInputRequest,
)
from sap_cli.ui_map.store import (
    CorruptUiMapError,
    UiMapNotFoundError,
    UiMapStore,
)


def _map(*, map_id: str = "se16-map", state_id: str = "state-1") -> UiMap:
    return UiMap(
        manifest=MapManifest(
            map_id=map_id,
            tcode="SE16",
            system_id="QAS",
            client="100",
            language="KO",
            api_version="8.00 PL08",
            gui_version="8.00 PL08",
            application_release="S4-2025",
        ),
        states={state_id: UiState(state_id=state_id, tcode="SE16")},
        pending_interactions=[
            UserInputRequest(
                question="조회할 테이블을 입력해 주세요.",
                current_state_id=state_id,
                resume_token="resume-1",
            )
        ],
    )


def test_save_versions_latest_and_load_version(tmp_path):
    store = UiMapStore(tmp_path)
    original = _map()

    first = store.save(original)
    second = store.save(original)

    assert original.manifest.map_version == 1
    assert first.manifest.map_version == 1
    assert second.manifest.map_version == 2
    assert first.manifest.created_at
    assert second.manifest.created_at == first.manifest.created_at
    assert store.list_versions(original.manifest) == [1, 2]
    assert store.latest(original.manifest).manifest.map_version == 2
    assert store.load_version(original.manifest, 1).manifest.map_version == 1
    assert list(tmp_path.rglob("*.tmp-*")) == []


def test_latest_head_is_normalized_and_does_not_load_snapshot(tmp_path, monkeypatch):
    store = UiMapStore(tmp_path)
    ui_map = _map()
    store.save(ui_map)
    saved = store.save(ui_map)

    def fail_if_snapshot_is_loaded(*_args, **_kwargs):
        raise AssertionError("latest_head must not load a map snapshot")

    monkeypatch.setattr(store, "_load_version", fail_if_snapshot_is_loaded)

    head = store.latest_head(
        "se16-map",
        tcode="se16",
        system_id="qas",
        client="100",
        language="ko",
    )

    assert head == {
        "version": 2,
        "content_fingerprint": compute_map_fingerprint(saved),
        "scope": {
            "map_id": "se16-map",
            "tcode": "SE16",
            "system_id": "QAS",
            "client": "100",
            "language": "KO",
        },
    }


def test_latest_head_returns_fresh_payload_and_tracks_new_version(tmp_path):
    store = UiMapStore(tmp_path)
    ui_map = _map()
    store.save(ui_map)

    first = store.latest_head(ui_map.manifest)
    first["scope"]["client"] = "tampered"
    store.save(ui_map)
    second = store.latest_head(ui_map.manifest)

    assert second["version"] == 2
    assert second["scope"]["client"] == "100"


def test_graph_slice_keeps_semantic_links_for_selected_states(tmp_path):
    store = UiMapStore(tmp_path)
    ui_map = _map()
    ui_map.semantic_links.append(
        SemanticLink(
            link_id="semantic-state-1",
            subject_kind="state",
            subject_id="state-1",
            predicate="has_ui_role",
            object_id="ui_role:overview",
            status="confirmed",
        )
    )
    saved = store.save(ui_map)

    sliced = store.load_graph_slice(saved.manifest, start_state_id="state-1")

    assert [(item.link_id, item.status) for item in sliced.semantic_links] == [
        ("semantic-state-1", "confirmed")
    ]
    assert [item.state_id for item in store.search_graph(saved.manifest, "overview")] == [
        "state-1"
    ]


def test_scope_separates_system_client_and_language(tmp_path):
    store = UiMapStore(tmp_path)
    first = _map()
    other_manifest = first.manifest.model_copy(update={"client": "200"})
    other = first.model_copy(update={"manifest": other_manifest}, deep=True)

    assert store.save(first).manifest.map_version == 1
    assert store.save(other).manifest.map_version == 1
    assert store.latest(first.manifest).manifest.client == "100"
    assert store.latest(other.manifest).manifest.client == "200"
    assert [item.manifest.client for item in store.list_latest(tcode="se16")] == [
        "100",
        "200",
    ]
    assert [item.manifest.client for item in store.list_latest(client="200")] == [
        "200"
    ]


def test_save_delta_uses_non_destructive_merge_and_preserves_interactions(tmp_path):
    store = UiMapStore(tmp_path)
    base = store.save(_map())
    delta = UiMap(
        manifest=base.manifest,
        states={"state-2": UiState(state_id="state-2", tcode="SE16")},
        pending_interactions=[
            UserInputRequest(
                question="두 번째 분기를 열까요?",
                current_state_id="state-2",
                resume_token="resume-2",
            )
        ],
    )

    merged = store.save_delta(delta)

    assert merged.manifest.map_version == 2
    assert set(merged.states) == {"state-1", "state-2"}
    assert [item.resume_token for item in merged.pending_interactions] == [
        "resume-1",
        "resume-2",
    ]
    assert len(store.load_version(base.manifest, 1).states) == 1


def test_save_delta_advances_interaction_lifecycle_without_replacing_question(tmp_path):
    store = UiMapStore(tmp_path)
    base = store.save(_map())
    resolved = base.model_copy(deep=True)
    resolved.pending_interactions[0] = resolved.pending_interactions[0].model_copy(
        update={
            "status": "resolved",
            "provenance": {
                "answer_source": "user",
                "answered_fields": ["table_name"],
                "answer_value_stored": False,
            },
        }
    )

    saved = store.save_delta(resolved)

    interaction = saved.pending_interactions[0]
    assert interaction.question == "조회할 테이블을 입력해 주세요."
    assert interaction.status == "resolved"
    assert interaction.provenance["answer_value_stored"] is False
    assert store.load_version(base.manifest, 1).pending_interactions[0].status == "pending"


def test_save_delta_does_not_create_a_version_for_an_unchanged_checkpoint(tmp_path):
    store = UiMapStore(tmp_path)
    base = store.save(_map())

    saved = store.save_delta(base.model_copy(deep=True))

    assert saved.manifest.map_version == 1
    assert store.list_versions(base.manifest) == [1]


def test_graph_index_reports_pending_interactions_without_loading_full_map(tmp_path):
    store = UiMapStore(tmp_path)
    base = store.save(_map())

    assert store.has_unresolved_interactions(base.manifest) is True


def test_save_delta_full_copy_does_not_resolve_existing_coverage_gap(tmp_path):
    store = UiMapStore(tmp_path)
    base = _map()
    base.manifest.unreachable = [
        {
            "state_id": "state-1",
            "reason": "baseline_restore_failed",
            "action": "select_tab",
        }
    ]
    saved = store.save(base)
    full_copy = saved.model_copy(deep=True)
    full_copy.states["unrelated"] = UiState(state_id="unrelated", tcode="SE16")

    merged = store.save_delta(full_copy)

    gap = merged.manifest.unreachable[0]
    assert gap["gap_id"].startswith("gap-")
    assert gap["state_id"] == "state-1"
    assert gap["reason"] == "baseline_restore_failed"
    assert "resolved" not in gap


def test_stale_comparison_covers_all_runtime_versions():
    ui_map = _map()

    assert not UiMapStore.is_stale(
        ui_map,
        api_version="8.00 PL08",
        gui_version="8.00 PL08",
        application_release="S4-2025",
        schema_version="1.0",
    )
    reasons = UiMapStore.stale_reasons(
        ui_map,
        api_version="8.00 PL09",
        gui_version="8.10",
        application_release="S4-2026",
        schema_version="2.0",
    )
    assert set(reasons) == {
        "api_version",
        "gui_version",
        "application_release",
        "schema_version",
    }


def test_corrupt_latest_and_snapshot_are_rejected(tmp_path):
    store = UiMapStore(tmp_path)
    ui_map = _map()
    store.save(ui_map)
    latest_path = next(tmp_path.rglob("latest.json"))
    latest_path.write_text("{broken", encoding="utf-8")

    with pytest.raises(CorruptUiMapError):
        store.latest(ui_map.manifest)

    latest_path.write_text(
        json.dumps(
            {
                "version": 1,
                "scope": {
                    "map_id": "se16-map",
                    "tcode": "SE16",
                    "system_id": "QAS",
                    "client": "100",
                    "language": "KO",
                },
            }
        ),
        encoding="utf-8",
    )
    snapshot = next(tmp_path.rglob("v00000001.json"))
    snapshot.write_text("[]", encoding="utf-8")
    with pytest.raises(CorruptUiMapError):
        store.load_version(ui_map.manifest, 1)


@pytest.mark.parametrize(
    "field,value",
    [("map_id", "../escape"), ("tcode", "SE16/../escape"), ("system_id", "..")],
)
def test_path_traversal_scope_is_rejected(tmp_path, field, value):
    ui_map = _map()
    manifest = ui_map.manifest.model_copy(update={field: value})
    poisoned = ui_map.model_copy(update={"manifest": manifest})

    with pytest.raises(ValueError, match="path traversal|path separators"):
        UiMapStore(tmp_path).save(poisoned)
    assert not (tmp_path.parent / "escape").exists()


def test_namespace_tcode_is_supported_without_becoming_a_path(tmp_path):
    ui_map = _map()
    manifest = ui_map.manifest.model_copy(update={"tcode": "/ACME/REPORT"})
    ui_map = ui_map.model_copy(update={"manifest": manifest})
    store = UiMapStore(tmp_path)

    store.save(ui_map)

    assert store.latest(manifest).manifest.tcode == "/ACME/REPORT"
    assert not (tmp_path / "ACME").exists()


def test_missing_scope_or_version_raises(tmp_path):
    store = UiMapStore(tmp_path)
    manifest = _map().manifest
    assert store.list_versions(manifest) == []
    with pytest.raises(UiMapNotFoundError):
        store.latest(manifest)
    with pytest.raises(UiMapNotFoundError):
        store.load_version(manifest, 1)


def test_concurrent_writes_across_store_instances_get_unique_versions(tmp_path):
    ui_map = _map()
    stores = [UiMapStore(tmp_path), UiMapStore(tmp_path)]

    def save(index: int) -> int:
        return stores[index % 2].save(ui_map).manifest.map_version

    with ThreadPoolExecutor(max_workers=8) as pool:
        versions = sorted(pool.map(save, range(20)))

    assert versions == list(range(1, 21))
    assert stores[0].list_versions(ui_map.manifest) == list(range(1, 21))
    assert stores[1].latest(ui_map.manifest).manifest.map_version == 20


def _graph_map() -> UiMap:
    ui_map = _map(state_id="start")
    ui_map.states.update({
        "items": UiState(
            state_id="items", tcode="SE16", title="품목 목록",
            components=[],
        ),
        "detail": UiState(
            state_id="detail", tcode="SE16", title="품목 상세",
        ),
        "unrelated": UiState(
            state_id="unrelated", tcode="SE16", title="무관한 화면",
        ),
    })
    ui_map.states["items"].components = [
        UiComponent(
            logical_id="item-grid", sap_id="wnd[0]/grid", type="GuiGridView",
            role="alv", tooltip="품목 목록",
        )
    ]
    ui_map.transitions = [
        Transition(
            transition_id="to-items", from_state_id="start", to_state_id="items",
            action="select_tab",
        ),
        Transition(
            transition_id="to-detail", from_state_id="items", to_state_id="detail",
            action="press",
        ),
        Transition(
            transition_id="to-unrelated", from_state_id="start", to_state_id="unrelated",
            action="press",
        ),
    ]
    return ui_map


def test_graph_slice_reads_route_without_materializing_full_snapshot(tmp_path, monkeypatch):
    store = UiMapStore(tmp_path)
    saved = store.save(_graph_map())
    original = store._load_version

    def fail_full_load(*_args, **_kwargs):
        raise AssertionError("graph slice must not load v*.json")

    monkeypatch.setattr(store, "_load_version", fail_full_load)
    sliced = store.load_graph_slice(
        saved.manifest, start_state_id="start", goal_query="품목 상세", neighborhood=0
    )

    assert set(sliced.states) == {"start", "items", "detail"}
    assert [item.transition_id for item in sliced.transitions] == ["to-detail", "to-items"]
    assert sliced.manifest == saved.manifest
    monkeypatch.setattr(store, "_load_version", original)


def test_graph_search_heads_and_required_records_are_bounded(tmp_path, monkeypatch):
    store = UiMapStore(tmp_path)
    saved = store.save(_graph_map())

    monkeypatch.setattr(
        store, "_load_version", lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("pointer/index path must not load full map")
        )
    )
    heads = store.list_latest_heads(tcode="se16", client="100")
    hits = store.search_graph(saved.manifest, "item-grid")
    sliced = store.load_graph_slice(
        saved.manifest, required_state_ids=("detail",),
        required_transition_ids=("to-items",), max_states=1, neighborhood=0,
    )

    assert heads[0]["version"] == 1
    assert heads[0]["content_fingerprint"] == compute_map_fingerprint(saved)
    assert hits[0].state_id == "items"
    assert set(sliced.states) == {"detail", "start", "items"}
    assert [item.transition_id for item in sliced.transitions] == ["to-detail", "to-items"]


def test_old_snapshot_is_migrated_once_to_a_graph_sidecar(tmp_path, monkeypatch):
    store = UiMapStore(tmp_path)
    saved = store.save(_graph_map())
    graph_dir = next(tmp_path.rglob("graphs/v00000001"))
    shutil.rmtree(graph_dir)
    calls = []
    original = store._load_version

    def counted(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(store, "_load_version", counted)
    store.load_graph_slice(saved.manifest, start_state_id="start", max_depth=0)
    store.load_graph_slice(saved.manifest, start_state_id="start", max_depth=0)

    assert len(calls) == 1
    assert graph_dir.joinpath("index.json").is_file()
