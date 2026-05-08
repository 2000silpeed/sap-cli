"""Graph-backed SAP GUI Scripting API ontology loader/query engine."""

from __future__ import annotations

import json
from collections import defaultdict, deque
from functools import lru_cache
from pathlib import Path
from typing import Any


DEFAULT_GRAPH_PATH = (
    Path(__file__).parent / "ontology" / "knowledge" / "scripting_api" / "graph.json"
)
DEFAULT_GENERATED_GRAPH_PATH = (
    Path(__file__).parent / "ontology" / "knowledge" / "scripting_api" / "graph.generated.json"
)


def merge_graph_data(*graphs: dict[str, Any]) -> dict[str, Any]:
    """Merge graph JSON documents while preserving curated seed metadata."""
    node_by_id: dict[str, dict[str, Any]] = {}
    edge_by_key: dict[tuple[Any, ...], dict[str, Any]] = {}
    sources_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    versions: list[str] = []

    for graph in graphs:
        if not graph:
            continue
        version = str(graph.get("version", ""))
        if version:
            versions.append(version)
        for source in graph.get("sources", []):
            key = (str(source.get("title", "")), str(source.get("url", "")))
            sources_by_key[key] = dict(source)
        for node in graph.get("nodes", []):
            node_id = str(node["id"])
            if node_id in node_by_id:
                # Keep curated seed fields, but fill missing data from generated overlays.
                node_by_id[node_id] = {**dict(node), **node_by_id[node_id]}
            else:
                node_by_id[node_id] = dict(node)
        for graph_edge in graph.get("edges", []):
            props = tuple(
                sorted(
                    (key, json.dumps(value, sort_keys=True, ensure_ascii=False))
                    for key, value in graph_edge.items()
                    if key not in {"source", "target", "type"}
                )
            )
            key = (
                str(graph_edge["source"]),
                str(graph_edge["target"]),
                str(graph_edge["type"]),
                props,
            )
            edge_by_key[key] = dict(graph_edge)

    return {
        "version": "+".join(dict.fromkeys(versions)),
        "description": "Merged SAP GUI Scripting API graph.",
        "runtime": {"generated_overlay_loaded": len([graph for graph in graphs if graph]) > 1},
        "sources": list(sources_by_key.values()),
        "nodes": list(node_by_id.values()),
        "edges": list(edge_by_key.values()),
    }


class ScriptingApiGraph:
    """Small graph query helper for SAP GUI Scripting API metadata."""

    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data
        self.nodes: dict[str, dict[str, Any]] = {
            str(node["id"]): dict(node) for node in data.get("nodes", [])
        }
        self.edges: list[dict[str, Any]] = [dict(edge) for edge in data.get("edges", [])]
        self.out_edges: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.in_edges: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for edge in self.edges:
            self.out_edges[str(edge["source"])].append(edge)
            self.in_edges[str(edge["target"])].append(edge)

    @classmethod
    def load(cls, path: str | Path | None = None) -> "ScriptingApiGraph":
        graph_path = Path(path) if path else DEFAULT_GRAPH_PATH
        data = json.loads(graph_path.read_text(encoding="utf-8"))
        if path is None and DEFAULT_GENERATED_GRAPH_PATH.exists():
            generated_data = json.loads(DEFAULT_GENERATED_GRAPH_PATH.read_text(encoding="utf-8"))
            data = merge_graph_data(data, generated_data)
        return cls(data)

    def query(
        self,
        *,
        node_id: str = "",
        component_type: str = "",
        intent: str = "",
        depth: int = 1,
    ) -> dict[str, Any]:
        """Return a relevant subgraph by node, component type, and/or intent."""
        seeds: set[str] = set()
        if node_id:
            seeds.add(node_id)
        if component_type:
            seeds.add(f"object:{component_type}")
        if intent:
            seeds.add(f"intent:{intent}")

        if not seeds:
            seeds = {
                nid for nid, node in self.nodes.items()
                if node.get("type") in {"api_object", "intent"}
            }
            depth = 0

        visited = self._expand(seeds, depth)
        edges = [
            edge for edge in self.edges
            if edge["source"] in visited and edge["target"] in visited
        ]
        runtime = {
            "nodes_total": len(self.nodes),
            "edges_total": len(self.edges),
            "generated_overlay_path": str(DEFAULT_GENERATED_GRAPH_PATH),
            "generated_overlay_exists": DEFAULT_GENERATED_GRAPH_PATH.exists(),
            "generated_overlay_loaded": bool(
                self.data.get("runtime", {}).get("generated_overlay_loaded", False)
            ),
        }
        warnings = []
        if not runtime["generated_overlay_loaded"]:
            warnings.append(
                "graph.generated.json is not loaded; only the curated seed graph is available. "
                "Generate sap_cli/ontology/knowledge/scripting_api/graph.generated.json from the official SAP PDF "
                "to query the full API overlay."
            )
        return {
            "version": self.data.get("version", ""),
            "sources": self.data.get("sources", []),
            "runtime": runtime,
            "warnings": warnings,
            "query": {
                "node_id": node_id,
                "component_type": component_type,
                "intent": intent,
                "depth": depth,
            },
            "nodes": [self.nodes[nid] for nid in sorted(visited) if nid in self.nodes],
            "edges": edges,
        }

    def capability(self, component_type: str, subtype: str = "") -> dict[str, Any]:
        """Build component capability metadata from graph edges."""
        object_id = f"object:{component_type}"
        if object_id not in self.nodes:
            return {}

        read: list[str] = []
        write: list[str] = []
        actions: list[str] = []
        methods: list[str] = []
        collections: list[str] = []
        notes: list[str] = []

        for edge in self.out_edges.get(object_id, []):
            target = self.nodes.get(str(edge["target"]), {})
            edge_type = str(edge.get("type", ""))
            target_name = str(target.get("name", target.get("id", "")))
            if edge_type == "HAS_PROPERTY":
                access = str(edge.get("access", target.get("access", ""))).lower()
                if "read" in access or not access:
                    read.append(target_name)
                if "write" in access:
                    write.append(target_name)
            elif edge_type == "HAS_METHOD":
                methods.append(target_name)
            elif edge_type == "HAS_COLLECTION":
                collections.append(target_name)
                read.append(target_name)
            elif edge_type == "SUPPORTS_ACTION":
                actions.append(str(target.get("name", "") or target.get("id", "").removeprefix("intent:")))
            elif edge_type == "HAS_NOTE":
                notes.append(str(target.get("text", target_name)))

        if subtype:
            subtype_id = f"subtype:{component_type}:{subtype}"
            for edge in self.out_edges.get(subtype_id, []):
                target = self.nodes.get(str(edge["target"]), {})
                if edge.get("type") == "SUPPORTS_ACTION":
                    actions.append(str(target.get("name", "") or target["id"].removeprefix("intent:")))

        return {
            "read": sorted(set(read)),
            "write": sorted(set(write)),
            "methods": sorted(set(methods)),
            "collections": sorted(set(collections)),
            "actions": sorted(set(actions)),
            **({"notes": notes} if notes else {}),
            "graph_node": object_id,
            "graph_source": str(DEFAULT_GRAPH_PATH),
        }

    def _expand(self, seeds: set[str], depth: int) -> set[str]:
        visited = {seed for seed in seeds if seed in self.nodes}
        queue: deque[tuple[str, int]] = deque((seed, 0) for seed in visited)
        while queue:
            current, dist = queue.popleft()
            if dist >= depth:
                continue
            for edge in [*self.out_edges.get(current, []), *self.in_edges.get(current, [])]:
                for nid in (str(edge["source"]), str(edge["target"])):
                    if nid not in visited and nid in self.nodes:
                        visited.add(nid)
                        queue.append((nid, dist + 1))
        return visited


@lru_cache(maxsize=1)
def get_default_graph() -> ScriptingApiGraph:
    return ScriptingApiGraph.load()


def query_default_graph(
    *,
    node_id: str = "",
    component_type: str = "",
    intent: str = "",
    depth: int = 1,
) -> dict[str, Any]:
    return get_default_graph().query(
        node_id=node_id,
        component_type=component_type,
        intent=intent,
        depth=depth,
    )
