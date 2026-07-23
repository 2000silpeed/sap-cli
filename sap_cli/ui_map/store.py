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
import shutil
import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from pydantic import ValidationError

from sap_cli.storage import atomic_write_json

from .schema import SCHEMA_VERSION, MapManifest, SemanticLink, Transition, UiComponent, UiMap, UiState, UserInputRequest
from .fingerprint import compute_map_fingerprint


class UiMapStoreError(RuntimeError):
    """Base error raised by the versioned UI-map store."""


class UiMapNotFoundError(UiMapStoreError):
    """The requested map scope or version does not exist."""


class CorruptUiMapError(UiMapStoreError):
    """A persisted pointer or map snapshot is malformed."""


@dataclass(frozen=True)
class GraphSearchHit:
    """A safe structural match from the persisted graph index."""

    state_id: str
    component_logical_id: str = ""
    score: int = 0


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

    def delete(
        self,
        map_id: str | MapManifest,
        *,
        tcode: str = "",
        system_id: str = "",
        client: str = "",
        language: str = "",
    ) -> dict[str, Any]:
        """Permanently remove every stored version of one exact map scope.

        Irreversible: deletes the scope directory — all numbered snapshots, the
        latest pointer, and the graph sidecars. A UI map holds only structural
        routes (no business values), so a deleted map can be rebuilt by scanning
        the screens again.
        """

        scope = self._scope(map_id, tcode, system_id, client, language)
        scope_dir = self._scope_dir(scope)
        with self._lock:
            if not scope_dir.exists():
                raise UiMapNotFoundError(
                    f"no UI map to delete: {scope[0]} ({scope[1]})"
                )
            versions = self._list_versions(scope)
            head_version = self._latest_version(scope, missing_ok=True)
            shutil.rmtree(scope_dir)
        return {
            "deleted": True,
            "map_id": scope[0],
            "scope": {
                "tcode": scope[1],
                "system_id": scope[2],
                "client": scope[3],
                "language": scope[4],
            },
            "versions_removed": len(versions),
            "head_version": head_version,
        }

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
            # Cooperative traversal checkpoints frequently carry a full copy
            # of the graph.  Do not turn an unchanged checkpoint into a new
            # immutable version: that would continuously invalidate plans
            # while the mapper is merely waiting for the next tick.
            if compute_map_fingerprint(merged) == compute_map_fingerprint(current):
                return current
            return self.save(merged)

    def has_unresolved_interactions(
        self,
        map_id: str | MapManifest,
        version: int | None = None,
        *,
        tcode: str = "",
        system_id: str = "",
        client: str = "",
        language: str = "",
    ) -> bool:
        """Return whether a map snapshot is waiting on human input.

        The graph index holds this small readiness bit, so planners can reject
        an in-progress map without loading its complete graph.  Older
        snapshots have no bit; only those use the compatibility snapshot read.
        """

        scope = self._scope(map_id, tcode, system_id, client, language)
        with self._lock:
            resolved_version = self._resolve_graph_version(scope, version)
            index = self._graph_index(scope, resolved_version)
            count = index.get("unresolved_interaction_count")
            if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
                return count > 0
            return any(
                item.status == "pending"
                for item in self._load_version(scope, resolved_version).pending_interactions
            )

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

    def list_latest_heads(
        self,
        *,
        tcode: str = "",
        system_id: str = "",
        client: str = "",
        language: str = "",
    ) -> list[dict[str, Any]]:
        """List validated latest pointers without materializing any UI map.

        This is the discovery entry point for a planner.  It intentionally
        returns only scope, version, and content fingerprint; callers must use
        :meth:`load_graph_slice` rather than ``latest`` when they need graph
        records.
        """

        filters = {
            "tcode": str(tcode).strip().upper(),
            "system_id": str(system_id).strip().upper(),
            "client": str(client).strip(),
            "language": str(language).strip().upper(),
        }
        results: list[dict[str, Any]] = []
        with self._lock:
            for pointer in sorted((self.root / "maps").glob("*/*/latest.json")):
                data = self._read_json(pointer, "latest pointer")
                if not isinstance(data, dict) or not isinstance(data.get("scope"), dict):
                    raise CorruptUiMapError(f"invalid latest pointer: {pointer}")
                scope_data = data["scope"]
                try:
                    scope = self._normalize_scope(
                        scope_data["map_id"], scope_data["tcode"],
                        scope_data.get("system_id", ""), scope_data.get("client", ""),
                        scope_data.get("language", ""),
                    )
                except (KeyError, TypeError, ValueError) as exc:
                    raise CorruptUiMapError(
                        f"invalid latest pointer scope: {pointer}"
                    ) from exc
                if pointer.parent != self._scope_dir(scope):
                    raise CorruptUiMapError(f"latest pointer path mismatch: {pointer}")
                values = dict(zip(
                    ("map_id", "tcode", "system_id", "client", "language"), scope
                ))
                if any(filters[key] and filters[key] != values[key] for key in filters):
                    continue
                version = data.get("version")
                if isinstance(version, bool) or not isinstance(version, int) or version < 1:
                    raise CorruptUiMapError(f"invalid latest version: {pointer}")
                fingerprint = str(data.get("content_fingerprint", ""))
                if not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
                    fingerprint = compute_map_fingerprint(self._load_version(scope, version))
                results.append({
                    "version": version,
                    "content_fingerprint": fingerprint,
                    "scope": values,
                })
        return sorted(results, key=lambda item: (
            item["scope"]["tcode"], item["scope"]["system_id"],
            item["scope"]["client"], item["scope"]["language"],
            item["scope"]["map_id"],
        ))

    def search_graph(
        self,
        map_id: str | MapManifest,
        query: str,
        *,
        version: int | None = None,
        tcode: str = "",
        system_id: str = "",
        client: str = "",
        language: str = "",
        limit: int = 50,
    ) -> list[GraphSearchHit]:
        """Search the compact, redacted graph index without loading states."""

        if limit < 0:
            raise ValueError("limit must not be negative")
        scope = self._scope(map_id, tcode, system_id, client, language)
        with self._lock:
            resolved_version = self._resolve_graph_version(scope, version)
            index = self._graph_index(scope, resolved_version)
        return self._search_graph_index(index, query, limit=limit)

    def load_graph_slice(
        self,
        map_id: str | MapManifest,
        version: int | None = None,
        *,
        tcode: str = "",
        system_id: str = "",
        client: str = "",
        language: str = "",
        start_state_id: str = "",
        goal_query: str = "",
        operation: str = "",
        goal_state_ids: Iterable[str] = (),
        required_state_ids: Iterable[str] = (),
        required_transition_ids: Iterable[str] = (),
        max_states: int = 64,
        max_depth: int = 12,
        neighborhood: int = 1,
    ) -> UiMap:
        """Load only the graph records relevant to one planning step.

        The on-disk graph keeps each state and each state adjacency list in a
        separate immutable file.  This method reads the compact index, then
        expands directed edges from ``start_state_id`` only until a structural
        goal is found or a deterministic bound is reached.  It never falls
        back to a full-map read once the graph sidecar exists.  For snapshots
        created by older versions, the sidecar is built once from that snapshot
        and every later slice read is bounded.

        ``goal_query`` and ``operation`` are structural search hints.  Their
        union with explicit ``goal_state_ids`` determines stopping targets.
        Returned maps preserve the exact original manifest but contain only
        selected states, connecting transitions, and relevant checkpoints.
        """

        if max_states < 1:
            raise ValueError("max_states must be positive")
        if max_depth < 0:
            raise ValueError("max_depth must not be negative")
        if neighborhood < 0:
            raise ValueError("neighborhood must not be negative")
        scope = self._scope(map_id, tcode, system_id, client, language)
        with self._lock:
            resolved_version = self._resolve_graph_version(scope, version)
            index = self._graph_index(scope, resolved_version)
            state_files = index["state_files"]
            roots = tuple(index.get("root_state_ids", ()))
            requested_goals = {str(item) for item in goal_state_ids if str(item)}
            for hint in (goal_query, operation):
                requested_goals.update(
                    hit.state_id for hit in self._search_graph_index(index, hint, limit=32)
                )
            requested_goals.intersection_update(state_files)
            start = str(start_state_id).strip()
            if not start:
                start = next((item for item in roots if item in state_files), "")
            if not start and state_files:
                start = min(state_files)
            if start and start not in state_files:
                raise UiMapNotFoundError(f"UI map state does not exist: {start}")

            selected, predecessor = self._bounded_forward_search(
                scope, resolved_version, index, start, requested_goals,
                max_states=max_states, max_depth=max_depth,
            )
            reached = next((item for item in sorted(requested_goals) if item in selected), "")
            route_states = self._route_states(predecessor, start, reached) if reached else set()
            core = route_states or selected
            selected = self._expand_neighborhood(
                scope, resolved_version, index, set(core),
                max_states=max_states, hops=neighborhood,
            )
            required_transitions = self._required_graph_transitions(
                scope, resolved_version, index, required_transition_ids
            )
            required_states = {str(item) for item in required_state_ids if str(item)}
            missing_states = required_states - set(state_files)
            if missing_states:
                raise UiMapNotFoundError(
                    "UI map state does not exist: " + ", ".join(sorted(missing_states))
                )
            selected.update(required_states)
            for transition in required_transitions:
                if transition.from_state_id in state_files:
                    selected.add(transition.from_state_id)
                if transition.to_state_id in state_files:
                    selected.add(transition.to_state_id)
            states = {
                state_id: self._read_graph_state(scope, resolved_version, index, state_id)
                for state_id in sorted(selected)
            }
            transitions = self._slice_transitions(
                scope, resolved_version, index, selected
            )
            transitions.extend(
                item for item in required_transitions
                if item.transition_id not in {edge.transition_id for edge in transitions}
            )
            transitions.sort(key=lambda item: item.transition_id)
            interactions = self._slice_interactions(
                scope, resolved_version, index, selected
            )
            semantic_links = [
                SemanticLink.model_validate(item)
                for item in index.get("semantic_links", [])
                if isinstance(item, dict)
                and self._semantic_link_in_states(item, selected)
            ]
            manifest = MapManifest.model_validate(index["manifest"])
            return UiMap(
                manifest=manifest,
                states=states,
                transitions=transitions,
                pending_interactions=interactions,
                semantic_links=semantic_links,
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
        # Write the immutable graph sidecar before publishing ``latest``.  A
        # failed sidecar write leaves only an unreachable numbered snapshot,
        # which save() already knows never to reuse.
        self._write_graph_snapshot(scope, ui_map)
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

    def _write_graph_snapshot(
        self, scope: tuple[str, str, str, str, str], ui_map: UiMap
    ) -> None:
        """Materialize an immutable, state-addressable graph sidecar.

        The index is intentionally structural and redacted.  It is the only
        shared file a normal planning search reads; state payloads and edge
        payloads remain independently addressable.
        """

        graph_dir = self._graph_dir(scope, ui_map.manifest.map_version)
        if (graph_dir / "index.json").exists():
            raise UiMapStoreError(
                f"UI map graph version already exists: {ui_map.manifest.map_version}"
            )
        state_files = {
            state_id: self._graph_file_name(state_id)
            for state_id in sorted(ui_map.states)
        }
        outgoing: dict[str, list[Transition]] = {state_id: [] for state_id in state_files}
        incoming: dict[str, list[Transition]] = {state_id: [] for state_id in state_files}
        transition_sources: dict[str, str] = {}
        for transition in sorted(ui_map.transitions, key=lambda item: item.transition_id):
            if transition.from_state_id in outgoing:
                outgoing[transition.from_state_id].append(transition)
                transition_sources[transition.transition_id] = transition.from_state_id
            if transition.to_state_id in incoming:
                incoming[transition.to_state_id].append(transition)
        for state_id, file_name in state_files.items():
            atomic_write_json(
                graph_dir / "states" / file_name,
                ui_map.states[state_id].model_dump(mode="json"), mode=0o600,
            )
            atomic_write_json(
                graph_dir / "outgoing" / file_name,
                [item.model_dump(mode="json") for item in outgoing[state_id]], mode=0o600,
            )
            atomic_write_json(
                graph_dir / "incoming" / file_name,
                [item.model_dump(mode="json") for item in incoming[state_id]], mode=0o600,
            )
            atomic_write_json(
                graph_dir / "interactions" / file_name,
                [
                    item.model_dump(mode="json")
                    for item in ui_map.pending_interactions
                    if item.current_state_id == state_id
                ],
                mode=0o600,
            )
        incoming_state_ids = {
            transition.to_state_id
            for transition in ui_map.transitions
            if transition.to_state_id in state_files
        }
        index = {
            "schema": "ui_map_graph_index_v1",
            "manifest": ui_map.manifest.model_dump(mode="json"),
            "state_files": state_files,
            "root_state_ids": sorted(set(state_files) - incoming_state_ids) or sorted(state_files)[:1],
            "transition_sources": transition_sources,
            "search_entries": self._graph_search_entries(ui_map),
            "global_interactions": [
                item.model_dump(mode="json")
                for item in ui_map.pending_interactions
                if not item.current_state_id or item.current_state_id not in state_files
            ],
            "unresolved_interaction_count": sum(
                item.status == "pending" for item in ui_map.pending_interactions
            ),
            "semantic_links": [
                item.model_dump(mode="json")
                for item in ui_map.semantic_links
            ],
        }
        # The index is the commit marker.  Do not move this write earlier.
        atomic_write_json(graph_dir / "index.json", index, mode=0o600)

    @staticmethod
    def _graph_file_name(state_id: str) -> str:
        return hashlib.sha256(state_id.encode("utf-8")).hexdigest() + ".json"

    @staticmethod
    def _semantic_link_in_states(item: dict[str, Any], states: set[str]) -> bool:
        subject_id = str(item.get("subject_id", ""))
        return subject_id in states or any(
            subject_id.startswith(f"{state_id}:") for state_id in states
        )

    def _graph_dir(self, scope: tuple[str, str, str, str, str], version: int) -> Path:
        return self._scope_dir(scope) / "graphs" / f"v{version:08d}"

    def _resolve_graph_version(
        self, scope: tuple[str, str, str, str, str], version: int | None
    ) -> int:
        if version is None:
            return int(self._latest_version(scope))
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise ValueError("version must be a positive integer")
        return version

    def _graph_index(
        self, scope: tuple[str, str, str, str, str], version: int
    ) -> dict[str, Any]:
        """Return a validated graph index, migrating one old snapshot if needed."""

        graph_dir = self._graph_dir(scope, version)
        index_path = graph_dir / "index.json"
        if not index_path.exists():
            # Backward compatibility for maps written before graph slices.  It
            # is deliberately a one-time migration, not a planner fallback.
            self._write_graph_snapshot(scope, self._load_version(scope, version))
        data = self._read_json(index_path, "UI map graph index")
        if not isinstance(data, dict) or data.get("schema") != "ui_map_graph_index_v1":
            raise CorruptUiMapError(f"invalid UI map graph index: {index_path}")
        try:
            manifest = MapManifest.model_validate(data["manifest"])
            state_files = data["state_files"]
        except (KeyError, TypeError, ValueError, ValidationError) as exc:
            raise CorruptUiMapError(f"invalid UI map graph index: {index_path}") from exc
        if self._scope_from_manifest(manifest) != scope or manifest.map_version != version:
            raise CorruptUiMapError(f"UI map graph index scope mismatch: {index_path}")
        if not isinstance(state_files, dict) or any(
            not isinstance(key, str) or not isinstance(value, str)
            or value != self._graph_file_name(key)
            for key, value in state_files.items()
        ):
            raise CorruptUiMapError(f"invalid UI map graph state index: {index_path}")
        return data

    @staticmethod
    def _graph_search_entries(ui_map: UiMap) -> list[dict[str, str]]:
        """Build a redacted structural search catalogue for state selection."""

        entries: list[dict[str, str]] = []
        semantic_by_subject: dict[str, list[str]] = {}
        for link in ui_map.semantic_links:
            if link.status != "confirmed":
                continue
            semantic_by_subject.setdefault(link.subject_id, []).append(link.object_id)
        for state_id in sorted(ui_map.states):
            state = ui_map.states[state_id]
            entries.append({
                "state_id": state_id,
                "component_logical_id": "",
                "text": " ".join(filter(None, (
                    state.tcode, state.program, str(state.screen_number), state.title,
                    *semantic_by_subject.get(state_id, []),
                ))),
            })
            for component in sorted(
                state.components, key=lambda item: (item.logical_id, item.sap_id)
            ):
                text = component.text if UiMapStore._graph_indexes_component_text(component) else ""
                entries.append({
                    "state_id": state_id,
                    "component_logical_id": component.logical_id,
                    "text": " ".join(filter(None, (
                        component.logical_id, component.sap_id, component.name,
                        text, component.tooltip, component.role, component.type,
                        component.sub_type,
                        *semantic_by_subject.get(
                            f"{state_id}:{component.logical_id}", []
                        ),
                    ))),
                })
        return entries

    @staticmethod
    def _graph_indexes_component_text(component: UiComponent) -> bool:
        if component.sensitive or component.role.casefold() in {"field", "input", "password", "value"}:
            return False
        return not any(marker in component.type.casefold() for marker in (
            "textfield", "password", "combobox",
        ))

    @staticmethod
    def _search_graph_index(
        index: dict[str, Any], query: str, *, limit: int
    ) -> list[GraphSearchHit]:
        normalized = " ".join(str(query).casefold().split())
        if not normalized or limit == 0:
            return []
        tokens = tuple(re.findall(r"[\w./\[\]-]+", normalized, re.UNICODE))
        best: dict[tuple[str, str], int] = {}
        entries = index.get("search_entries", ())
        if not isinstance(entries, list):
            raise CorruptUiMapError("invalid UI map graph search index")
        for entry in entries:
            if not isinstance(entry, dict):
                raise CorruptUiMapError("invalid UI map graph search entry")
            state_id = entry.get("state_id")
            component_id = entry.get("component_logical_id", "")
            value = " ".join(str(entry.get("text", "")).casefold().split())
            if not isinstance(state_id, str) or not isinstance(component_id, str):
                raise CorruptUiMapError("invalid UI map graph search entry")
            if value == normalized:
                score = 100
            elif value.startswith(normalized):
                score = 70
            elif normalized in value:
                score = 40
            elif tokens and all(token in value for token in tokens):
                score = 20 + len(tokens)
            else:
                continue
            key = (state_id, component_id)
            best[key] = max(best.get(key, 0), score)
        results = [
            GraphSearchHit(state_id, component_id, score)
            for (state_id, component_id), score in best.items()
        ]
        results.sort(key=lambda item: (-item.score, item.state_id, item.component_logical_id))
        return results[:limit]

    def _read_graph_state(
        self, scope: tuple[str, str, str, str, str], version: int,
        index: dict[str, Any], state_id: str,
    ) -> UiState:
        file_name = index["state_files"].get(state_id)
        if not isinstance(file_name, str):
            raise CorruptUiMapError(f"UI map state is not indexed: {state_id}")
        path = self._graph_dir(scope, version) / "states" / file_name
        try:
            state = UiState.model_validate(self._read_json(path, "UI map graph state"))
        except (ValidationError, TypeError, ValueError) as exc:
            raise CorruptUiMapError(f"invalid UI map graph state: {path}") from exc
        if state.state_id != state_id:
            raise CorruptUiMapError(f"UI map graph state id mismatch: {path}")
        return state

    def _read_graph_edges(
        self, scope: tuple[str, str, str, str, str], version: int,
        index: dict[str, Any], state_id: str, direction: str,
    ) -> list[Transition]:
        file_name = index["state_files"].get(state_id)
        if not isinstance(file_name, str):
            return []
        path = self._graph_dir(scope, version) / direction / file_name
        data = self._read_json(path, f"UI map graph {direction}")
        if not isinstance(data, list):
            raise CorruptUiMapError(f"invalid UI map graph {direction}: {path}")
        try:
            return [Transition.model_validate(item) for item in data]
        except (ValidationError, TypeError, ValueError) as exc:
            raise CorruptUiMapError(f"invalid UI map graph {direction}: {path}") from exc

    def _bounded_forward_search(
        self, scope: tuple[str, str, str, str, str], version: int,
        index: dict[str, Any], start: str, goals: set[str], *,
        max_states: int, max_depth: int,
    ) -> tuple[set[str], dict[str, tuple[str, Transition]]]:
        if not start:
            return set(), {}
        selected = {start}
        predecessor: dict[str, tuple[str, Transition]] = {}
        queue: deque[tuple[str, int]] = deque([(start, 0)])
        while queue:
            state_id, depth = queue.popleft()
            if state_id in goals or depth >= max_depth:
                continue
            for transition in self._read_graph_edges(
                scope, version, index, state_id, "outgoing"
            ):
                target = transition.to_state_id
                if target not in index["state_files"] or target in selected:
                    continue
                if len(selected) >= max_states:
                    return selected, predecessor
                selected.add(target)
                predecessor[target] = (state_id, transition)
                queue.append((target, depth + 1))
        return selected, predecessor

    @staticmethod
    def _route_states(
        predecessor: dict[str, tuple[str, Transition]], start: str, goal: str
    ) -> set[str]:
        if not start or not goal:
            return set()
        states = {goal}
        current = goal
        while current != start:
            previous = predecessor.get(current)
            if previous is None:
                return set()
            current = previous[0]
            states.add(current)
        return states

    def _expand_neighborhood(
        self, scope: tuple[str, str, str, str, str], version: int,
        index: dict[str, Any], selected: set[str], *, max_states: int, hops: int,
    ) -> set[str]:
        frontier = set(selected)
        for _ in range(hops):
            if not frontier or len(selected) >= max_states:
                break
            next_frontier: set[str] = set()
            for state_id in sorted(frontier):
                edges = self._read_graph_edges(scope, version, index, state_id, "outgoing")
                edges += self._read_graph_edges(scope, version, index, state_id, "incoming")
                for edge in edges:
                    neighbor = edge.to_state_id if edge.from_state_id == state_id else edge.from_state_id
                    if neighbor not in index["state_files"] or neighbor in selected:
                        continue
                    if len(selected) >= max_states:
                        return selected
                    selected.add(neighbor)
                    next_frontier.add(neighbor)
            frontier = next_frontier
        return selected

    def _slice_transitions(
        self, scope: tuple[str, str, str, str, str], version: int,
        index: dict[str, Any], state_ids: set[str],
    ) -> list[Transition]:
        transitions: dict[str, Transition] = {}
        for state_id in sorted(state_ids):
            for transition in self._read_graph_edges(scope, version, index, state_id, "outgoing"):
                if not transition.to_state_id or transition.to_state_id in state_ids:
                    transitions[transition.transition_id] = transition
        return [transitions[key] for key in sorted(transitions)]

    def _required_graph_transitions(
        self, scope: tuple[str, str, str, str, str], version: int,
        index: dict[str, Any], transition_ids: Iterable[str],
    ) -> list[Transition]:
        """Read only the adjacency files containing executor-required edges."""

        requested = {str(item) for item in transition_ids if str(item)}
        sources = index.get("transition_sources", {})
        if not isinstance(sources, dict):
            raise CorruptUiMapError("invalid UI map graph transition index")
        missing = requested - set(sources)
        if missing:
            raise UiMapNotFoundError(
                "UI map transition does not exist: " + ", ".join(sorted(missing))
            )
        by_id: dict[str, Transition] = {}
        for source in sorted({str(sources[item]) for item in requested}):
            for transition in self._read_graph_edges(scope, version, index, source, "outgoing"):
                if transition.transition_id in requested:
                    by_id[transition.transition_id] = transition
        missing = requested - set(by_id)
        if missing:
            raise CorruptUiMapError(
                "UI map graph transition index mismatch: " + ", ".join(sorted(missing))
            )
        return [by_id[item] for item in sorted(by_id)]

    def _slice_interactions(
        self, scope: tuple[str, str, str, str, str], version: int,
        index: dict[str, Any], state_ids: set[str],
    ) -> list[UserInputRequest]:
        interactions: list[UserInputRequest] = []
        for state_id in sorted(state_ids):
            file_name = index["state_files"].get(state_id)
            if not isinstance(file_name, str):
                continue
            path = self._graph_dir(scope, version) / "interactions" / file_name
            data = self._read_json(path, "UI map graph interactions")
            if not isinstance(data, list):
                raise CorruptUiMapError(f"invalid UI map graph interactions: {path}")
            try:
                interactions.extend(UserInputRequest.model_validate(item) for item in data)
            except (ValidationError, TypeError, ValueError) as exc:
                raise CorruptUiMapError(f"invalid UI map graph interactions: {path}") from exc
        if state_ids:
            try:
                interactions.extend(
                    UserInputRequest.model_validate(item)
                    for item in index.get("global_interactions", ())
                )
            except (ValidationError, TypeError, ValueError) as exc:
                raise CorruptUiMapError("invalid UI map global interactions") from exc
        return sorted(interactions, key=lambda item: (item.current_state_id, item.resume_token, item.question))

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
    "GraphSearchHit",
    "UiMapNotFoundError",
    "UiMapStore",
    "UiMapStoreError",
]
