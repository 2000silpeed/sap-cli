"""Daemon-memory LRU for exact, content-addressed UI-map snapshots."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from typing import Any
from collections.abc import Callable

from .fingerprint import compute_map_fingerprint
from .schema import MapManifest, UiMap
from .store import CorruptUiMapError, UiMapStore


@dataclass(slots=True)
class _Entry:
    ui_map: UiMap


class CachedUiMapStore:
    """Cache only the latest exact head after checking its pointer every time."""

    def __init__(
        self,
        store: UiMapStore,
        *,
        max_size: int = 64,
        on_head_change: Callable[..., Any] | None = None,
    ) -> None:
        if max_size < 1:
            raise ValueError("max_size must be positive")
        self.store = store
        self.max_size = max_size
        self._entries: OrderedDict[tuple[Any, ...], _Entry] = OrderedDict()
        self._on_head_change = on_head_change

    @property
    def root(self):
        return self.store.root

    def latest_head(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self.store.latest_head(*args, **kwargs)

    def latest(self, map_id: str | MapManifest, **scope: Any) -> UiMap:
        head = self.store.latest_head(map_id, **scope)
        exact_scope = self._head_scope(head)
        version = int(head["version"])
        fingerprint = str(head.get("content_fingerprint", ""))
        key = (*exact_scope, version, fingerprint)
        self._evict_stale_scope(exact_scope, key)
        entry = self._entries.get(key)
        if entry is None:
            loaded = self.store.load_version(map_id, version, **scope)
            if compute_map_fingerprint(loaded) != fingerprint:
                raise CorruptUiMapError("latest UI map fingerprint mismatch")
            while len(self._entries) >= self.max_size:
                self._entries.popitem(last=False)
            entry = _Entry(loaded.model_copy(deep=True))
            self._entries[key] = entry
        else:
            self._entries.move_to_end(key)
        return entry.ui_map.model_copy(deep=True)

    load_latest = latest

    def load_version(
        self, map_id: str | MapManifest, version: int, **scope: Any
    ) -> UiMap:
        head = self.store.latest_head(map_id, **scope)
        if int(head["version"]) == int(version):
            return self.latest(map_id, **scope)
        # Historical snapshots are not cached: their pointer cannot prove that
        # an external process has not modified a same-version file.
        return self.store.load_version(map_id, version, **scope)

    def save(self, ui_map: UiMap) -> UiMap:
        saved = self.store.save(ui_map)
        self.invalidate_manifest(saved.manifest)
        self._notify_head(saved)
        return saved

    def save_delta(self, delta: UiMap) -> UiMap:
        saved = self.store.save_delta(delta)
        self.invalidate_manifest(saved.manifest)
        self._notify_head(saved)
        return saved

    def invalidate_manifest(self, manifest: MapManifest) -> int:
        scope = (
            manifest.map_id,
            manifest.tcode.upper(),
            manifest.system_id.upper(),
            manifest.client,
            manifest.language.upper(),
        )
        return self._evict_scope(scope)

    def clear(self) -> None:
        self._entries.clear()

    def summaries(self) -> list[dict[str, Any]]:
        return [
            {
                "map_id": key[0],
                "tcode": key[1],
                "system_id": key[2],
                "client": key[3],
                "language": key[4],
                "version": key[5],
                "content_fingerprint": key[6],
            }
            for key in self._entries
        ]

    def __len__(self) -> int:
        return len(self._entries)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.store, name)

    @staticmethod
    def _head_scope(head: dict[str, Any]) -> tuple[str, str, str, str, str]:
        scope = head.get("scope")
        if not isinstance(scope, dict):
            raise CorruptUiMapError("latest UI map scope is invalid")
        return (
            str(scope.get("map_id", "")),
            str(scope.get("tcode", "")).upper(),
            str(scope.get("system_id", "")).upper(),
            str(scope.get("client", "")),
            str(scope.get("language", "")).upper(),
        )

    def _evict_stale_scope(
        self, scope: tuple[str, str, str, str, str], current_key: tuple[Any, ...]
    ) -> int:
        stale = [key for key in self._entries if key[:5] == scope and key != current_key]
        for key in stale:
            self._entries.pop(key, None)
        return len(stale)

    def _evict_scope(self, scope: tuple[str, str, str, str, str]) -> int:
        stale = [key for key in self._entries if key[:5] == scope]
        for key in stale:
            self._entries.pop(key, None)
        return len(stale)

    def _notify_head(self, ui_map: UiMap) -> None:
        if self._on_head_change is None:
            return
        manifest = ui_map.manifest
        self._on_head_change(
            map_id=manifest.map_id,
            tcode=manifest.tcode,
            system_id=manifest.system_id,
            client=manifest.client,
            language=manifest.language,
            head_version=manifest.map_version,
            head_fingerprint=compute_map_fingerprint(ui_map),
        )


__all__ = ["CachedUiMapStore"]
