"""Durable, versioned storage for lossless SAP GUI maps.

This store is intentionally separate from :mod:`sap_cli.ontology.store`.
Every write creates an immutable numbered snapshot and atomically advances a
small ``latest.json`` pointer.  Scope values are hashed before being used as a
directory name, so SAP namespace tcodes do not become filesystem paths.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from sap_cli.storage import atomic_write_json

from .schema import SCHEMA_VERSION, MapManifest, UiMap
from .fingerprint import compute_map_fingerprint


class UiMapStoreError(RuntimeError):
    """Base error raised by the versioned UI-map store."""


class UiMapNotFoundError(UiMapStoreError):
    """The requested map scope or version does not exist."""


class CorruptUiMapError(UiMapStoreError):
    """A persisted pointer or map snapshot is malformed."""


_VERSION_RE = re.compile(r"^v0*([1-9][0-9]*)\.json$")
_LOCKS_GUARD = threading.Lock()
_ROOT_LOCKS: dict[str, threading.RLock] = {}


def _root_lock(root: Path) -> threading.RLock:
    key = str(root.resolve())
    with _LOCKS_GUARD:
        return _ROOT_LOCKS.setdefault(key, threading.RLock())


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class UiMapStore:
    """Persist immutable :class:`UiMap` versions within an exact SAP scope."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self._lock = _root_lock(self.root)

    def save(self, ui_map: UiMap) -> UiMap:
        """Save a full snapshot with the next version for its exact scope."""

        scope = self._scope_from_manifest(ui_map.manifest)
        with self._lock:
            # A prior process may have written a snapshot but failed before the
            # latest pointer was replaced.  Never reuse that immutable number.
            latest_version = self._latest_version(scope, missing_ok=True)
            existing_created_at = ""
            if latest_version is not None:
                existing_created_at = self._load_version(
                    scope, latest_version
                ).manifest.created_at
            existing_versions = self._list_versions(scope)
            next_version = (max(existing_versions) if existing_versions else 0) + 1
            saved = self._prepare_snapshot(
                ui_map, next_version, existing_created_at=existing_created_at
            )
            self._write_snapshot(scope, saved)
            return saved

    def save_delta(self, delta: UiMap) -> UiMap:
        """Merge a delta into the latest snapshot and save a new version.

        ``UiMap.merge_delta`` is the sole merge implementation.  This preserves
        pending user interactions and records payload conflicts without
        overwriting existing state or transition records.
        """

        scope = self._scope_from_manifest(delta.manifest)
        with self._lock:
            latest_version = self._latest_version(scope, missing_ok=True)
            if latest_version is None:
                return self.save(delta)
            current = self._load_version(scope, latest_version)
            merged = current.model_copy(deep=True)
            merged.merge_delta(delta)
            return self.save(merged)

    def latest(
        self,
        map_id: str | MapManifest,
        *,
        tcode: str = "",
        system_id: str = "",
        client: str = "",
        language: str = "",
    ) -> UiMap:
        """Load the latest snapshot in an exact map/environment scope."""

        scope = self._scope(map_id, tcode, system_id, client, language)
        with self._lock:
            version = self._latest_version(scope)
            return self._load_version(scope, version)

    load_latest = latest

    def latest_head(
        self,
        map_id: str | MapManifest,
        *,
        tcode: str = "",
        system_id: str = "",
        client: str = "",
        language: str = "",
    ) -> dict[str, Any]:
        """Return the validated latest pointer without loading its snapshot.

        The small value is suitable for cache invalidation checks.  Callers get
        a fresh dictionary, so mutating it cannot affect the store or another
        caller.  Scope normalization and corruption checks are identical to
        :meth:`latest`.
        """

        scope = self._scope(map_id, tcode, system_id, client, language)
        with self._lock:
            version = self._latest_version(scope)
            pointer = self._read_json(
                self._scope_dir(scope) / "latest.json", "latest pointer"
            )
            fingerprint = str(pointer.get("content_fingerprint", ""))
            if not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
                # Backward-compatible validation for pointers written before
                # content fingerprints were introduced.
                fingerprint = compute_map_fingerprint(
                    self._load_version(scope, version)
                )
        return {
            "version": version,
            "content_fingerprint": fingerprint,
            "scope": dict(
                zip(
                    ("map_id", "tcode", "system_id", "client", "language"),
                    scope,
                )
            ),
        }

    def list_latest(
        self,
        *,
        tcode: str = "",
        system_id: str = "",
        client: str = "",
        language: str = "",
    ) -> list[UiMap]:
        """Discover latest maps, optionally filtered by SAP environment scope."""

        filters = {
            "tcode": str(tcode).strip().upper(),
            "system_id": str(system_id).strip().upper(),
            "client": str(client).strip(),
            "language": str(language).strip().upper(),
        }
        results: list[UiMap] = []
        with self._lock:
            for pointer in sorted((self.root / "maps").glob("*/*/latest.json")):
                data = self._read_json(pointer, "latest pointer")
                if not isinstance(data, dict) or not isinstance(data.get("scope"), dict):
                    raise CorruptUiMapError(f"invalid latest pointer: {pointer}")
                scope_data = data["scope"]
                try:
                    scope = self._normalize_scope(
                        scope_data["map_id"],
                        scope_data["tcode"],
                        scope_data.get("system_id", ""),
                        scope_data.get("client", ""),
                        scope_data.get("language", ""),
                    )
                except (KeyError, TypeError, ValueError) as exc:
                    raise CorruptUiMapError(
                        f"invalid latest pointer scope: {pointer}"
                    ) from exc
                actual_dir = self._scope_dir(scope)
                if pointer.parent != actual_dir:
                    raise CorruptUiMapError(f"latest pointer path mismatch: {pointer}")
                values = dict(
                    zip(("map_id", "tcode", "system_id", "client", "language"), scope)
                )
                if any(filters[key] and filters[key] != values[key] for key in filters):
                    continue
                version = data.get("version")
                if isinstance(version, bool) or not isinstance(version, int) or version < 1:
                    raise CorruptUiMapError(f"invalid latest version: {pointer}")
                results.append(self._load_version(scope, version))
        return sorted(
            results,
            key=lambda item: (
                item.manifest.tcode,
                item.manifest.system_id,
                item.manifest.client,
                item.manifest.language,
                item.manifest.map_id,
            ),
        )

    def load_version(
        self,
        map_id: str | MapManifest,
        version: int,
        *,
        tcode: str = "",
        system_id: str = "",
        client: str = "",
        language: str = "",
    ) -> UiMap:
        """Load one immutable numbered snapshot."""

        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise ValueError("version must be a positive integer")
        scope = self._scope(map_id, tcode, system_id, client, language)
        with self._lock:
            return self._load_version(scope, version)

    def list_versions(
        self,
        map_id: str | MapManifest,
        *,
        tcode: str = "",
        system_id: str = "",
        client: str = "",
        language: str = "",
    ) -> list[int]:
        """Return all valid snapshot version numbers in ascending order."""

        scope = self._scope(map_id, tcode, system_id, client, language)
        versions_dir = self._scope_dir(scope) / "versions"
        with self._lock:
            return self._list_versions(scope)

    def _list_versions(self, scope: tuple[str, str, str, str, str]) -> list[int]:
        versions_dir = self._scope_dir(scope) / "versions"
        if not versions_dir.exists():
            return []
        versions = []
        for path in versions_dir.iterdir():
            match = _VERSION_RE.fullmatch(path.name)
            if match and path.is_file():
                versions.append(int(match.group(1)))
        return sorted(versions)

    @staticmethod
    def stale_reasons(
        ui_map: UiMap,
        *,
        api_version: str = "",
        gui_version: str = "",
        application_release: str = "",
        schema_version: str = SCHEMA_VERSION,
    ) -> dict[str, dict[str, str]]:
        """Describe version mismatches that make a saved map stale.

        Empty expected runtime versions are treated as unknown and do not make
        the map stale.  Schema version defaults to the version understood by
        this package.
        """

        expected = {
            "api_version": api_version,
            "gui_version": gui_version,
            "application_release": application_release,
            "schema_version": schema_version,
        }
        reasons: dict[str, dict[str, str]] = {}
        for field_name, wanted in expected.items():
            if not wanted:
                continue
            stored = str(getattr(ui_map.manifest, field_name) or "")
            if stored != str(wanted):
                reasons[field_name] = {
                    "stored": stored,
                    "current": str(wanted),
                }
        return reasons

    @classmethod
    def is_stale(cls, ui_map: UiMap, **current_versions: str) -> bool:
        """Return whether any API/GUI/application/schema version differs."""

        return bool(cls.stale_reasons(ui_map, **current_versions))

    def _prepare_snapshot(
        self, ui_map: UiMap, version: int, *, existing_created_at: str = ""
    ) -> UiMap:
        now = _utc_now()
        created_at = existing_created_at or ui_map.manifest.created_at or now
        manifest = ui_map.manifest.model_copy(
            update={
                "map_version": version,
                "created_at": created_at,
                "updated_at": now,
            }
        )
        return ui_map.model_copy(update={"manifest": manifest}, deep=True)

    def _write_snapshot(self, scope: tuple[str, str, str, str, str], ui_map: UiMap) -> None:
        scope_dir = self._scope_dir(scope)
        version = ui_map.manifest.map_version
        version_path = scope_dir / "versions" / f"v{version:08d}.json"
        if version_path.exists():
            raise UiMapStoreError(f"map version already exists: {version}")
        data = ui_map.model_dump(mode="json")
        atomic_write_json(version_path, data, mode=0o600)
        atomic_write_json(
            scope_dir / "latest.json",
            {
                "version": version,
                "content_fingerprint": compute_map_fingerprint(ui_map),
                "scope": {
                    "map_id": scope[0],
                    "tcode": scope[1],
                    "system_id": scope[2],
                    "client": scope[3],
                    "language": scope[4],
                },
            },
            mode=0o600,
        )

    def _latest_version(
        self,
        scope: tuple[str, str, str, str, str],
        *,
        missing_ok: bool = False,
    ) -> int | None:
        pointer = self._scope_dir(scope) / "latest.json"
        if not pointer.exists():
            if missing_ok:
                return None
            raise UiMapNotFoundError("no UI map exists for the requested scope")
        data = self._read_json(pointer, "latest pointer")
        if not isinstance(data, dict):
            raise CorruptUiMapError(f"invalid latest pointer: {pointer}")
        version = data.get("version")
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise CorruptUiMapError(f"invalid latest version: {pointer}")
        expected_scope = dict(zip(
            ("map_id", "tcode", "system_id", "client", "language"), scope
        ))
        if data.get("scope") != expected_scope:
            raise CorruptUiMapError(f"latest pointer scope mismatch: {pointer}")
        return version

    def _load_version(
        self, scope: tuple[str, str, str, str, str], version: int
    ) -> UiMap:
        path = self._scope_dir(scope) / "versions" / f"v{version:08d}.json"
        if not path.is_file():
            raise UiMapNotFoundError(f"UI map version does not exist: {version}")
        data = self._read_json(path, "UI map snapshot")
        try:
            ui_map = UiMap.model_validate(data)
        except (ValidationError, TypeError, ValueError) as exc:
            raise CorruptUiMapError(f"invalid UI map snapshot: {path}") from exc
        if self._scope_from_manifest(ui_map.manifest) != scope:
            raise CorruptUiMapError(f"UI map snapshot scope mismatch: {path}")
        if ui_map.manifest.map_version != version:
            raise CorruptUiMapError(f"UI map snapshot version mismatch: {path}")
        return ui_map

    @staticmethod
    def _read_json(path: Path, label: str) -> Any:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            raise CorruptUiMapError(f"cannot read {label}: {path}") from exc

    def _scope(
        self,
        map_id: str | MapManifest,
        tcode: str,
        system_id: str,
        client: str,
        language: str,
    ) -> tuple[str, str, str, str, str]:
        if isinstance(map_id, MapManifest):
            if any((tcode, system_id, client, language)):
                raise ValueError("scope keywords cannot be combined with MapManifest")
            return self._scope_from_manifest(map_id)
        return self._normalize_scope(map_id, tcode, system_id, client, language)

    @classmethod
    def _scope_from_manifest(
        cls, manifest: MapManifest
    ) -> tuple[str, str, str, str, str]:
        return cls._normalize_scope(
            manifest.map_id,
            manifest.tcode,
            manifest.system_id,
            manifest.client,
            manifest.language,
        )

    @classmethod
    def _normalize_scope(
        cls, map_id: str, tcode: str, system_id: str, client: str, language: str
    ) -> tuple[str, str, str, str, str]:
        values = {
            "map_id": str(map_id).strip(),
            "tcode": str(tcode).strip().upper(),
            "system_id": str(system_id).strip().upper(),
            "client": str(client).strip(),
            "language": str(language).strip().upper(),
        }
        if not values["map_id"] or not values["tcode"]:
            raise ValueError("map_id and tcode are required")
        for name, value in values.items():
            cls._reject_path_traversal(name, value)
        return tuple(values[name] for name in (
            "map_id", "tcode", "system_id", "client", "language"
        ))  # type: ignore[return-value]

    @staticmethod
    def _reject_path_traversal(name: str, value: str) -> None:
        if "\x00" in value:
            raise ValueError(f"{name} contains a NUL byte")
        parts = value.replace("\\", "/").split("/")
        if ".." in parts or "." in parts:
            raise ValueError(f"{name} contains a path traversal segment")
        if name == "map_id" and ("/" in value or "\\" in value):
            raise ValueError("map_id must not contain path separators")

    def _scope_dir(self, scope: tuple[str, str, str, str, str]) -> Path:
        payload = json.dumps(scope, ensure_ascii=False, separators=(",", ":"))
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        path = self.root / "maps" / digest[:2] / digest
        root = self.root.resolve()
        resolved = path.resolve()
        if os.path.commonpath((str(root), str(resolved))) != str(root):
            raise ValueError("resolved UI map path escapes store root")
        return resolved


__all__ = [
    "CorruptUiMapError",
    "UiMapNotFoundError",
    "UiMapStore",
    "UiMapStoreError",
]
