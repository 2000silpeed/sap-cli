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

The bundled seed graph remains the safe runtime fallback; generated graphs
should be reviewed because vendor documentation layouts can vary.
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
