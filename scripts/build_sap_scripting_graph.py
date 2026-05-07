#!/usr/bin/env python3
"""Build a SAP GUI Scripting API graph from exported official documentation.

This script is intentionally dependency-free. It accepts a plain-text or
Markdown export of the official SAP GUI Scripting API guide and extracts a
best-effort object/property/method/event graph.

Recommended source:
- SAP GUI Scripting API PDF from SAP Help Portal.

Workflow:
1. Export the official PDF/HTML to text or Markdown.
2. Run:
   python scripts/build_sap_scripting_graph.py api.txt \
     --out sap_cli/ontology/knowledge/scripting_api/graph.generated.json
3. Review the generated graph before replacing graph.json.

The bundled seed graph remains the safe runtime fallback; generated graphs
should be reviewed because vendor documentation layouts can vary.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


OBJECT_HEADING = re.compile(r"^#+\s+(Gui[A-Za-z0-9]+)\s+Object\b|^(Gui[A-Za-z0-9]+)\s+Object\b")
SECTION_HEADING = re.compile(r"^#+\s+(Methods|Properties|Events)\b|^(Methods|Properties|Events)\s*$", re.I)
MEMBER_LINE = re.compile(r"^`?([A-Za-z_][A-Za-z0-9_]*)`?\s*(?:\((Read-only|Read-write)\))?", re.I)


def node(node_id: str, node_type: str, name: str, **props: Any) -> dict[str, Any]:
    return {"id": node_id, "type": node_type, "name": name, **props}


def edge(source: str, target: str, edge_type: str, **props: Any) -> dict[str, Any]:
    return {"source": source, "target": target, "type": edge_type, **props}


def build_graph(text: str, *, source: str = "") -> dict[str, Any]:
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    current_object = ""
    current_section = ""

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        object_match = OBJECT_HEADING.match(line)
        if object_match:
            current_object = object_match.group(1) or object_match.group(2)
            current_section = ""
            obj_id = f"object:{current_object}"
            nodes[obj_id] = node(obj_id, "api_object", current_object)
            continue

        section_match = SECTION_HEADING.match(line)
        if section_match:
            current_section = (section_match.group(1) or section_match.group(2) or "").lower()
            continue

        if not current_object or current_section not in {"methods", "properties", "events"}:
            continue

        member_match = MEMBER_LINE.match(line)
        if not member_match:
            continue
        member_name = member_match.group(1)
        access = (member_match.group(2) or "").lower().replace("-", "-")
        if member_name in {"Public", "ByVal", "Function", "Sub", "Property"}:
            continue

        obj_id = f"object:{current_object}"
        if current_section == "methods":
            mid = f"method:{current_object}.{member_name}"
            nodes[mid] = node(mid, "api_method", member_name)
            edges.append(edge(obj_id, mid, "HAS_METHOD"))
        elif current_section == "properties":
            pid = f"property:{current_object}.{member_name}"
            nodes[pid] = node(pid, "api_property", member_name, access=access or "read")
            edges.append(edge(obj_id, pid, "HAS_PROPERTY", access=access or "read"))
        elif current_section == "events":
            eid = f"event:{current_object}.{member_name}"
            nodes[eid] = node(eid, "api_event", member_name)
            edges.append(edge(obj_id, eid, "HAS_EVENT"))

    return {
        "version": "generated-review-required",
        "description": "Generated SAP GUI Scripting API graph. Review before runtime use.",
        "sources": [{"title": "SAP GUI Scripting API export", "url": source}] if source else [],
        "nodes": list(nodes.values()),
        "edges": edges,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path, help="Text/Markdown export of SAP GUI Scripting API docs")
    parser.add_argument("--out", type=Path, required=True, help="Output graph JSON")
    parser.add_argument("--source", default="", help="Official source URL")
    args = parser.parse_args()

    graph = build_graph(args.input.read_text(encoding="utf-8"), source=args.source)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"nodes": len(graph["nodes"]), "edges": len(graph["edges"]), "out": str(args.out)}))


if __name__ == "__main__":
    main()
