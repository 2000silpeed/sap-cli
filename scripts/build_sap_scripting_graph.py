#!/usr/bin/env python3
"""Build a SAP GUI Scripting API graph from official documentation.

This script is intentionally dependency-free. It accepts a plain-text or
Markdown export of the official SAP GUI Scripting API guide, or an official
HTTP(S) URL. PDF URLs are converted through the local ``pdftotext`` command and
then parsed into a best-effort object/property/method/event graph.

Recommended source:
- SAP GUI Scripting API PDF from SAP Help Portal.

Workflow:
1. Run against a local text/Markdown export, stdin, or an official URL:
   python scripts/build_sap_scripting_graph.py api.txt \
     --out sap_cli/ontology/knowledge/scripting_api/graph.generated.json
   python scripts/build_sap_scripting_graph.py https://.../sap_gui_scripting_api.pdf \
     --out sap_cli/ontology/knowledge/scripting_api/graph.generated.json
   python scripts/build_sap_scripting_graph.py - --out graph.generated.json < api.txt
2. Review the generated graph before replacing graph.json.

The bundled seed graph remains the safe runtime fallback. When
``sap_cli/ontology/knowledge/scripting_api/graph.generated.json`` exists, the
runtime loader automatically overlays it on the curated seed graph so generated
API objects/members can be queried without losing curated intents/actions.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any


OBJECT_HEADING = re.compile(
    r"^(?:(?P<section>\d+(?:\.\d+)*)\s+)?"
    r"(?P<name>Gui[A-Za-z0-9]+|SapGuiAuto|CSapROTWrapper)"
    r"(?:\s+(?P<kind>Object|Collection))?\s*$",
    re.I,
)
SECTION_HEADING = re.compile(r"^#+\s+(Methods|Properties|Events)\b|^(Methods|Properties|Events)\s*$", re.I)
PROPERTY_TITLE = re.compile(r"^`?([A-Z][A-Za-z0-9_]*)`?\s*\((Read-only|Read-write)\)\s*$", re.I)
PROPERTY_SIGNATURE = re.compile(
    r"^Public\s+Property\s+(?:Get\s+|Let\s+|Set\s+)?([A-Za-z_][A-Za-z0-9_]*)\b",
    re.I,
)
METHOD_SIGNATURE = re.compile(r"^Public\s+(?:Sub|Function)\s+([A-Za-z_][A-Za-z0-9_]*)\b", re.I)
METHOD_NAME = re.compile(r"^`?([A-Z][A-Za-z0-9_]*)`?\s*$")
EXTENDS_LINE = re.compile(
    r"\bextends(?:\s+the)?\s+(Gui[A-Za-z0-9]+|SapGuiAuto|CSapROTWrapper)(?:\s+Object)?\b",
    re.I,
)
SKIP_MEMBERS = {
    "All",
    "Byte",
    "Class",
    "Description",
    "Event",
    "Function",
    "Long",
    "Method",
    "Object",
    "Property",
    "Public",
    "Returns",
    "SAP",
    "String",
    "Sub",
    "Syntax",
}
SKIP_MEMBERS_LOWER = {member.lower() for member in SKIP_MEMBERS}


def node(node_id: str, node_type: str, name: str, **props: Any) -> dict[str, Any]:
    return {"id": node_id, "type": node_type, "name": name, **props}


def edge(source: str, target: str, edge_type: str, **props: Any) -> dict[str, Any]:
    return {"source": source, "target": target, "type": edge_type, **props}


def _match_object_heading(line: str) -> tuple[str, str] | None:
    if ". ." in line or "[page" in line.lower():
        return None
    match = OBJECT_HEADING.match(line)
    if not match:
        return None
    if not match.group("section") and not match.group("kind"):
        return None
    name = match.group("name")
    kind = "api_collection" if (match.group("kind") or "").lower() == "collection" else "api_object"
    return name, kind


def build_graph(text: str, *, source: str = "") -> dict[str, Any]:
    nodes: dict[str, dict[str, Any]] = {}
    edges: dict[tuple[Any, ...], dict[str, Any]] = {}
    current_object = ""
    current_section = ""
    pending_property_access: dict[str, str] = {}

    def add_node(node_id: str, node_type: str, name: str, **props: Any) -> None:
        existing = nodes.get(node_id, {})
        nodes[node_id] = {**node(node_id, node_type, name), **existing, **props}

    def add_edge(source_id: str, target_id: str, edge_type: str, **props: Any) -> None:
        key = (source_id, target_id, edge_type, tuple(sorted(props.items())))
        edges[key] = edge(source_id, target_id, edge_type, **props)

    def add_member(member_name: str, section: str, access: str = "") -> None:
        if not current_object or member_name.lower() in SKIP_MEMBERS_LOWER:
            return
        obj_id = f"object:{current_object}"
        if section == "methods":
            mid = f"method:{current_object}.{member_name}"
            add_node(mid, "api_method", member_name)
            add_edge(obj_id, mid, "HAS_METHOD")
        elif section == "properties":
            normalized_access = access or pending_property_access.pop(member_name, "") or "read"
            pid = f"property:{current_object}.{member_name}"
            add_node(pid, "api_property", member_name, access=normalized_access)
            add_edge(obj_id, pid, "HAS_PROPERTY", access=normalized_access)
        elif section == "events":
            eid = f"event:{current_object}.{member_name}"
            add_node(eid, "api_event", member_name)
            add_edge(obj_id, eid, "HAS_EVENT")

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        object_match = _match_object_heading(line)
        if object_match:
            current_object, node_type = object_match
            current_section = ""
            pending_property_access.clear()
            obj_id = f"object:{current_object}"
            add_node(obj_id, node_type, current_object)
            continue

        section_match = SECTION_HEADING.match(line)
        if section_match:
            current_section = (section_match.group(1) or section_match.group(2) or "").lower()
            pending_property_access.clear()
            continue

        if current_object:
            for base_object in EXTENDS_LINE.findall(line):
                base_id = f"object:{base_object}"
                add_node(base_id, "api_object", base_object)
                add_edge(f"object:{current_object}", base_id, "EXTENDS")

        if not current_object or current_section not in {"methods", "properties", "events"}:
            continue

        property_title = PROPERTY_TITLE.match(line)
        if property_title and current_section == "properties":
            member_name = property_title.group(1)
            access = property_title.group(2).lower()
            pending_property_access[member_name] = access
            add_member(member_name, "properties", access)
            continue

        property_signature = PROPERTY_SIGNATURE.match(line)
        if property_signature:
            add_member(property_signature.group(1), "properties")
            continue

        method_signature = METHOD_SIGNATURE.match(line)
        if method_signature:
            add_member(method_signature.group(1), "methods")
            continue

        method_name = METHOD_NAME.match(line)
        if method_name and current_section in {"methods", "events"}:
            add_member(method_name.group(1), current_section)

    return {
        "version": "generated-review-required",
        "description": "Generated SAP GUI Scripting API graph. Review before runtime use.",
        "sources": [{"title": "SAP GUI Scripting API export", "url": source}] if source else [],
        "nodes": list(nodes.values()),
        "edges": list(edges.values()),
    }


def _decode_response(data: bytes, *, source: str, content_type: str = "") -> str:
    is_pdf = source.lower().split("?", 1)[0].endswith(".pdf") or "pdf" in content_type.lower()
    if not is_pdf:
        return data.decode("utf-8", errors="replace")

    try:
        result = subprocess.run(
            ["pdftotext", "-", "-"],
            input=data,
            capture_output=True,
            check=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("PDF URL을 직접 읽으려면 로컬에 pdftotext(poppler)가 필요합니다.") from exc
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"PDF 텍스트 변환 실패: {stderr}") from exc

    return result.stdout.decode("utf-8", errors="replace")


def read_source(source: str) -> tuple[str, str]:
    if source == "-":
        return sys.stdin.read(), ""

    if source.startswith(("http://", "https://")):
        request = urllib.request.Request(source, headers={"User-Agent": "sap-cli-scripting-graph-builder/1.0"})
        with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310 - explicit user-provided docs URL
            data = response.read()
            content_type = response.headers.get("Content-Type", "")
        return _decode_response(data, source=source, content_type=content_type), source

    return Path(source).read_text(encoding="utf-8"), ""


def write_graph(graph: dict[str, Any], out: str) -> None:
    payload = json.dumps(graph, ensure_ascii=False, indent=2)
    if out == "-":
        print(payload)
        return

    out_path = Path(out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(payload, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", help="Text/Markdown export, stdin '-', or official HTTP(S) URL")
    parser.add_argument("--out", required=True, help="Output graph JSON path, or '-' for stdout")
    parser.add_argument("--source", default="", help="Official source URL override for graph metadata")
    args = parser.parse_args()

    text, detected_source = read_source(args.input)
    graph = build_graph(text, source=args.source or detected_source)
    write_graph(graph, args.out)
    if args.out != "-":
        print(json.dumps({"nodes": len(graph["nodes"]), "edges": len(graph["edges"]), "out": args.out}))


if __name__ == "__main__":
    main()
