from sap_cli.ui_map.cache import CachedUiMapStore
from sap_cli.ui_map.schema import MapManifest, UiMap, UiState
from sap_cli.ui_map.store import UiMapStore


def make_map(map_id="map", client="100", title="one"):
    return UiMap(
        manifest=MapManifest(
            map_id=map_id, tcode="ZMAP", system_id="DEV", client=client
        ),
        states={"s": UiState(state_id="s", tcode="ZMAP", title=title)},
    )


def test_latest_cache_hits_but_rechecks_head_and_returns_copy(tmp_path, monkeypatch):
    raw = UiMapStore(tmp_path)
    saved = raw.save(make_map())
    cache = CachedUiMapStore(raw)
    loads = []
    original = raw.load_version

    def counted(*args, **kwargs):
        loads.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(raw, "load_version", counted)
    first = cache.latest(saved.manifest)
    first.states["s"].title = "tampered"
    second = cache.latest(saved.manifest)

    assert len(loads) == 1
    assert second.states["s"].title == "one"
    assert len(cache) == 1


def test_new_head_invalidates_only_same_scope(tmp_path):
    raw = UiMapStore(tmp_path)
    first = raw.save(make_map())
    other = raw.save(make_map(map_id="other"))
    cache = CachedUiMapStore(raw)
    cache.latest(first.manifest)
    cache.latest(other.manifest)

    raw.save(make_map(title="two"))
    latest = cache.latest(first.manifest)

    assert latest.manifest.map_version == 2
    assert latest.states["s"].title == "two"
    assert len(cache) == 2
    assert {item["map_id"] for item in cache.summaries()} == {"map", "other"}


def test_lru_capacity_and_historical_versions_are_not_cached(tmp_path):
    raw = UiMapStore(tmp_path)
    first = raw.save(make_map(map_id="one"))
    second = raw.save(make_map(map_id="two"))
    raw.save(make_map(map_id="one", title="new"))
    cache = CachedUiMapStore(raw, max_size=1)

    historical = cache.load_version(first.manifest, 1)
    assert historical.manifest.map_version == 1
    assert len(cache) == 0
    cache.latest(first.manifest)
    cache.latest(second.manifest)

    assert len(cache) == 1
    assert cache.summaries()[0]["map_id"] == "two"


def test_save_notifies_exact_plan_dependency_invalidator(tmp_path):
    raw = UiMapStore(tmp_path)
    notifications = []
    cache = CachedUiMapStore(raw, on_head_change=lambda **value: notifications.append(value))

    saved = cache.save(make_map())

    assert notifications == [{
        "map_id": "map", "tcode": "ZMAP", "system_id": "DEV",
        "client": "100", "language": "", "head_version": 1,
        "head_fingerprint": notifications[0]["head_fingerprint"],
    }]
    assert len(notifications[0]["head_fingerprint"]) == 64
    assert saved.manifest.map_version == 1
