#!/usr/bin/env python3
"""Build a version-pinned SAP GUI Scripting graph from SAP Help Portal XHTML.

Unlike the legacy PDF text parser, this builder reads the official table of
contents and the Methods/Properties/Events tables used by SAP Help Portal.
Direct declarations, inherited members, enumerations, signatures, parameters,
return types, access modes, and source-page hashes remain distinguishable.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable


PRODUCT_URL = "sap_gui_for_windows"
DELIVERABLE_URL = "b47d018c3b9b45e897faf66a6c0885a8"
OBJECTS_TOPIC = "a2e9357389334dc89eecc1fb13999ee3.html"
VERSION = "800.08"
LANGUAGE = "en-US"
STATE = "PRODUCTION"
METADATA_ENDPOINT = "https://help.sap.com/http.svc/deliverableMetadata"
PAGE_ENDPOINT = "https://help.sap.com/http.svc/pagecontent"
PDF_URL = (
    "https://help.sap.com/doc/9215986e54174174854b0af6bb14305a/"
    "800.08/en-US/sap_gui_scripting_api.pdf"
)
EXPECTED_COUNTS = {
    "topics": 96,
    "objects": 76,
    "enums": 10,
    "enum_values": 438,
    "methods": 466,
    "properties": 403,
    "events": 17,
    "extends": 67,
}


@dataclass
class HtmlNode:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list["HtmlNode"] = field(default_factory=list)
    text_parts: list[str] = field(default_factory=list)

    def text(self) -> str:
        parts = [*self.text_parts]
        for child in self.children:
            parts.append(child.text())
        return _normalize_space(" ".join(parts))

    def descendants(self, tag: str | None = None) -> Iterable["HtmlNode"]:
        for child in self.children:
            if tag is None or child.tag == tag:
                yield child
            yield from child.descendants(tag)

    def first(self, tag: str) -> "HtmlNode | None":
        return next(self.descendants(tag), None)


class _TreeParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = HtmlNode("document")
        self.stack = [self.root]

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        node = HtmlNode(tag.lower(), {key: value or "" for key, value in attrs})
        self.stack[-1].children.append(node)
        if tag.lower() not in {
            "area", "base", "br", "col", "embed", "hr", "img", "input",
            "link", "meta", "param", "source", "track", "wbr",
        }:
            self.stack.append(node)

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        node = HtmlNode(tag.lower(), {key: value or "" for key, value in attrs})
        self.stack[-1].children.append(node)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.stack[-1].text_parts.append(data)


def parse_html(body: str) -> HtmlNode:
    parser = _TreeParser()
    parser.feed(body)
    parser.close()
    return parser.root


def parse_object_page(
    body: str,
    *,
    object_name: str,
    object_kind: str = "api_object",
    source_topic: str = "",
) -> dict[str, Any]:
    """Parse direct declarations from one official object page."""

    root = parse_html(body)
    extends_all = _parse_extends(body, object_name)
    members: list[dict[str, Any]] = []
    inherited: list[dict[str, Any]] = []

    for section in root.descendants("section"):
        section_type = section.attrs.get("type", "").lower()
        if section_type not in {"methods", "properties", "events"}:
            continue
        member_kind = section_type[:-1] if section_type != "properties" else "property"
        table = section.first("table")
        if table is None:
            continue
        tbody = table.first("tbody")
        if tbody is None:
            continue
        for row in [child for child in tbody.children if child.tag == "tr"]:
            cells = [child for child in row.children if child.tag == "td"]
            if not cells:
                continue
            first = cells[0]
            row_text = first.text()
            if row_text.lower().startswith("all "):
                inherited.append(
                    {
                        "member_kind": member_kind,
                        "text": row_text,
                    }
                )
                continue
            # Most pages keep the name and VB declaration in the first cell,
            # but the ROT helper pages put the declaration in a later cell.
            # Prefer the declaration name because it also corrects occasional
            # Help Portal display-name typos (for example CurrentCel + "l").
            signature = _row_signature(cells, member_kind)
            name = _name_from_signature(signature, member_kind)
            if not name:
                name = _member_name(first, member_kind)
            if not name:
                name = _plain_member_name(first)
            if not name:
                continue
            description = cells[1].text() if len(cells) > 1 else ""
            access = _property_access(row_text) if member_kind == "property" else ""
            signature_meta = parse_signature(signature, member_kind)
            if member_kind == "method" and signature_meta["return_type"] in {
                "",
                "Object",
                "Variant",
            }:
                documented_return = _documented_return_type(cells)
                if documented_return:
                    signature_meta["return_type"] = documented_return
                elif object_name == "CSapROTWrapper" and name == "GetROTEntry":
                    signature_meta["return_type"] = "SapGuiAuto"
            members.append(
                {
                    "name": name,
                    "kind": member_kind,
                    "signature": signature,
                    "parameters": signature_meta["parameters"],
                    "return_type": signature_meta["return_type"],
                    "property_type": signature_meta["property_type"],
                    "access": access,
                    "description": description,
                    "source_topic": source_topic,
                }
            )

    if not extends_all:
        inferred = _infer_base_from_inherited(inherited)
        if inferred:
            extends_all = [inferred]

    return {
        "name": object_name,
        "kind": object_kind,
        "extends": extends_all[0] if extends_all else "",
        "extends_all": extends_all,
        "members": members,
        "inherited": inherited,
        "source_topic": source_topic,
        "source_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
    }


def parse_enum_page(
    body: str, *, enum_name: str, source_topic: str = ""
) -> dict[str, Any]:
    root = parse_html(body)
    values: list[dict[str, Any]] = []
    for table in root.descendants("table"):
        tbody = table.first("tbody")
        if tbody is None:
            continue
        for row in [child for child in tbody.children if child.tag == "tr"]:
            cells = [child for child in row.children if child.tag == "td"]
            if not cells:
                continue
            name = _member_name(cells[0], "enum_value")
            if not name:
                text = cells[0].text().split(" ", 1)[0]
                name = text if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", text) else ""
            if not name:
                continue
            values.append(
                {
                    "name": name,
                    "value": cells[1].text() if len(cells) > 1 else "",
                    "description": cells[2].text() if len(cells) > 2 else "",
                }
            )
        if values:
            break
    return {
        "name": enum_name,
        "values": values,
        "source_topic": source_topic,
        "source_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
    }


def parse_signature(signature: str, member_kind: str) -> dict[str, Any]:
    signature = _normalize_space(signature.replace("_", " "))
    parameters: list[dict[str, Any]] = []
    match = re.search(r"\((.*?)\)", signature, re.I)
    if match:
        for raw in _split_parameters(match.group(1)):
            param = re.search(
                r"(?:(Optional)\s+)?(?:(ByVal|ByRef)\s+)?"
                r"([A-Za-z_][A-Za-z0-9_]*)\s+As\s+([A-Za-z0-9_()]+)",
                raw,
                re.I,
            )
            if param:
                parameters.append(
                    {
                        "name": param.group(3),
                        "type": param.group(4),
                        "optional": bool(param.group(1)),
                        "passing": (param.group(2) or "").lower(),
                    }
                )
    return_type = ""
    property_type = ""
    if member_kind == "method":
        result = re.search(r"\)\s+As\s+([A-Za-z0-9_()]+)\s*$", signature, re.I)
        return_type = result.group(1) if result else ""
    elif member_kind == "property":
        result = re.search(r"\bAs\s+([A-Za-z0-9_()]+)\s*$", signature, re.I)
        property_type = result.group(1) if result else ""
    return {
        "parameters": parameters,
        "return_type": return_type,
        "property_type": property_type,
    }


def build_graph(
    *,
    metadata: dict[str, Any],
    toc: list[dict[str, Any]],
    pages: dict[str, str],
) -> dict[str, Any]:
    """Build a graph from already fetched official pages."""

    object_topics, enum_topics = classify_toc(toc)
    objects = []
    enums = []
    for topic in object_topics:
        title = str(topic["t"])
        name = title.removesuffix(" Object").removesuffix(" Collection").strip()
        kind = "api_collection" if title.endswith(" Collection") else "api_object"
        objects.append(
            parse_object_page(
                pages[topic["u"]],
                object_name=name,
                object_kind=kind,
                source_topic=topic["u"],
            )
        )
    for topic in enum_topics:
        enums.append(
            parse_enum_page(
                pages[topic["u"]],
                enum_name=str(topic["t"]),
                source_topic=topic["u"],
            )
        )

    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    source_pages: list[dict[str, Any]] = []
    for obj in objects:
        object_id = f"object:{obj['name']}"
        nodes[object_id] = {
            "id": object_id,
            "type": obj["kind"],
            "name": obj["name"],
            "source_topic": obj["source_topic"],
        }
        source_pages.append(
            {"topic": obj["source_topic"], "sha256": obj["source_sha256"]}
        )
        for base in obj["extends_all"]:
            base_id = f"object:{base}"
            nodes.setdefault(base_id, {"id": base_id, "type": "api_object", "name": base})
            edges.append({"source": object_id, "target": base_id, "type": "EXTENDS"})
        for member in obj["members"]:
            prefix = member["kind"]
            member_id = f"{prefix}:{obj['name']}.{member['name']}"
            nodes[member_id] = {
                "id": member_id,
                "type": f"api_{prefix}",
                "name": member["name"],
                **{key: value for key, value in member.items() if key not in {"name", "kind"} and value not in ("", [], None)},
            }
            edges.append(
                {
                    "source": object_id,
                    "target": member_id,
                    "type": f"HAS_{prefix.upper()}",
                    **({"access": member["access"]} if member["access"] else {}),
                }
            )
    for enum in enums:
        enum_id = f"enum:{enum['name']}"
        nodes[enum_id] = {"id": enum_id, "type": "api_enum", "name": enum["name"]}
        source_pages.append(
            {"topic": enum["source_topic"], "sha256": enum["source_sha256"]}
        )
        for value in enum["values"]:
            value_id = f"enum_value:{enum['name']}.{value['name']}"
            nodes[value_id] = {
                "id": value_id,
                "type": "api_enum_value",
                "name": value["name"],
                "value": value["value"],
                "description": value["description"],
            }
            edges.append({"source": enum_id, "target": value_id, "type": "HAS_ENUM_VALUE"})

    deliverable = metadata["data"]["deliverable"]
    return {
        "version": deliverable["versionName"],
        "schema_version": "2.0",
        "description": "Version-pinned SAP GUI Scripting API graph from SAP Help Portal XHTML.",
        "sources": [
            {
                "title": f"SAP GUI Scripting API {deliverable['versionName']}",
                "url": (
                    "https://help.sap.com/docs/sap_gui_for_windows/"
                    f"{DELIVERABLE_URL}/babdf65f4d0a4bd8b40f5ff132cb12fa.html"
                ),
                "pdf": PDF_URL,
                "version": deliverable["version"],
                "deliverable_id": deliverable["id"],
                "build_no": deliverable["buildNo"],
            }
        ],
        "source_pages": sorted(source_pages, key=lambda item: item["topic"]),
        "nodes": list(nodes.values()),
        "edges": edges,
    }


def classify_toc(
    toc: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    root = toc[0]
    object_topics: list[dict[str, Any]] = []
    enum_topics: list[dict[str, Any]] = []
    for child in root.get("c", []):
        if child.get("t") == "Objects":
            object_topics.extend(child.get("c", []))
        elif child.get("t") == "Enumerations":
            enum_topics.extend(child.get("c", []))
    for top in toc[1:]:
        if top.get("t") in {
            "SAP GUI Scripting ROT Entry Helper",
            "SAP GUI Scripting ROT Access Helper",
        }:
            object_topics.extend(top.get("c", []))
    return object_topics, enum_topics


def fetch_official_pages(
    *,
    version: str = VERSION,
    snapshot_dir: Path | None = None,
    request_delay: float = 0.02,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, str]]:
    metadata_url = METADATA_ENDPOINT + "?" + urllib.parse.urlencode(
        {
            "product_url": PRODUCT_URL,
            "deliverable_url": DELIVERABLE_URL,
            "topic_url": OBJECTS_TOPIC,
            "version": version,
            "loadlandingpageontopicnotfound": "true",
            "language": LANGUAGE,
            "state": STATE,
        }
    )
    metadata = _fetch_json(metadata_url)
    deliverable = metadata["data"]["deliverable"]
    if deliverable["version"] != version:
        raise RuntimeError(
            f"SAP Help version mismatch: expected {version}, got {deliverable['version']}"
        )
    toc_url = PAGE_ENDPOINT + "?" + urllib.parse.urlencode(
        {
            "deliverableInfo": 1,
            "deliverable_id": deliverable["id"],
            "buildNo": deliverable["buildNo"],
            "file_path": OBJECTS_TOPIC,
        }
    )
    toc_payload = _fetch_json(toc_url)
    toc = toc_payload["data"]["deliverable"]["fullToc"]
    object_topics, enum_topics = classify_toc(toc)
    topics = [*object_topics, *enum_topics]
    pages: dict[str, str] = {}
    for topic in topics:
        path = str(topic["u"])
        url = PAGE_ENDPOINT + "?" + urllib.parse.urlencode(
            {
                "deliverableInfo": 0,
                "deliverable_id": deliverable["id"],
                "buildNo": deliverable["buildNo"],
                "file_path": path,
            }
        )
        payload = _fetch_json(url)
        current = payload["data"]["currentPage"]
        if current["u"] != path:
            raise RuntimeError(f"topic mismatch: requested {path}, got {current['u']}")
        pages[path] = payload["data"]["body"]
        if request_delay:
            time.sleep(request_delay)

    if snapshot_dir is not None:
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        _write_json(snapshot_dir / "metadata.json", metadata)
        _write_json(snapshot_dir / "toc.json", toc_payload)
        page_dir = snapshot_dir / "pages"
        page_dir.mkdir(exist_ok=True)
        for path, body in pages.items():
            (page_dir / path).write_text(body, encoding="utf-8")
    return metadata, toc, pages


def validate_graph(graph: dict[str, Any], toc: list[dict[str, Any]], *, strict: bool) -> dict[str, int]:
    counts = {
        "topics": sum(1 for _ in _walk_toc(toc)),
        "objects": sum(node.get("type") in {"api_object", "api_collection"} for node in graph["nodes"]),
        "enums": sum(node.get("type") == "api_enum" for node in graph["nodes"]),
        "enum_values": sum(node.get("type") == "api_enum_value" for node in graph["nodes"]),
        "methods": sum(node.get("type") == "api_method" for node in graph["nodes"]),
        "properties": sum(node.get("type") == "api_property" for node in graph["nodes"]),
        "events": sum(node.get("type") == "api_event" for node in graph["nodes"]),
        "extends": sum(edge.get("type") == "EXTENDS" for edge in graph["edges"]),
    }
    node_ids = {node["id"] for node in graph["nodes"]}
    dangling = [edge for edge in graph["edges"] if edge["source"] not in node_ids or edge["target"] not in node_ids]
    if dangling:
        raise RuntimeError(f"dangling graph edges: {len(dangling)}")
    if strict:
        mismatches = {
            key: (EXPECTED_COUNTS[key], counts[key])
            for key in EXPECTED_COUNTS if counts[key] != EXPECTED_COUNTS[key]
        }
        if mismatches:
            raise RuntimeError(f"official PL08 golden count mismatch: {mismatches}")
    return counts


def _member_name(cell: HtmlNode, member_kind: str) -> str:
    for node in cell.descendants("kbd"):
        if "userinput" in node.attrs.get("class", ""):
            name = node.text().strip()
            if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
                return name
    return ""


def _signature(cell: HtmlNode) -> str:
    for node in cell.descendants("pre"):
        if "codeblock" in node.attrs.get("class", ""):
            return _normalize_space(node.text())
    return ""


def _row_signature(cells: list[HtmlNode], member_kind: str) -> str:
    """Return the declaration from any cell, ignoring embedded examples."""

    for cell in cells:
        for node in cell.descendants("pre"):
            if "codeblock" not in node.attrs.get("class", ""):
                continue
            candidate = _normalize_space(node.text())
            if _name_from_signature(candidate, member_kind):
                return candidate
    return ""


def _documented_return_type(cells: list[HtmlNode]) -> str:
    for cell in cells:
        text = cell.text()
        match = re.search(r"Return\s+Type\s*:\s*(Gui[A-Za-z0-9]+)", text, re.I)
        if match:
            return match.group(1)
    return ""


def _plain_member_name(cell: HtmlNode) -> str:
    """Accept a bare identifier used by a few declaration-less API rows."""

    name = cell.text().strip()
    return name if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) else ""


def _name_from_signature(signature: str, member_kind: str) -> str:
    patterns = {
        "method": r"Public\s+(?:Sub|Function)\s+([A-Za-z_][A-Za-z0-9_]*)",
        "property": r"Public\s+Property(?:\s+(?:Get|Let|Set))?\s+([A-Za-z_][A-Za-z0-9_]*)",
        "event": r"Public\s+Event\s+([A-Za-z_][A-Za-z0-9_]*)",
    }
    match = re.search(patterns.get(member_kind, r"$^"), signature, re.I)
    return match.group(1) if match else ""


def _property_access(text: str) -> str:
    match = re.search(r"\((Read-only|Read-write)\)", text, re.I)
    return match.group(1).lower() if match else ""


def _parse_extends(body: str, object_name: str) -> list[str]:
    """Read direct bases from the clause following ``extends``.

    A description can link other related classes before the actual base class.
    Limiting extraction to the ``extends ... .`` clause avoids turning those
    contextual links into false inheritance edges. ``GuiVContainer`` is the
    one documented multiple-interface case and contributes both linked bases.
    """

    bases: list[str] = []
    for paragraph in re.findall(r"<p\b[^>]*>(.*?)</p>", body, re.I | re.S):
        match = re.search(r"\bextends\b(.*)$", paragraph, re.I | re.S)
        if not match:
            continue
        clause = match.group(1)
        for anchor_text in re.findall(
            r"<a\b[^>]*>(.*?)</a>", clause, re.I | re.S
        ):
            candidate = re.sub(
                r"\s+(?:Object|Collection)$",
                "",
                parse_html(anchor_text).text().strip(),
                flags=re.I,
            )
            if re.fullmatch(r"Gui[A-Za-z0-9]+", candidate):
                bases.append(candidate)
        if not bases:
            plain = parse_html(clause).text()
            plain_match = re.search(r"\b(Gui[A-Za-z0-9]+)\b", plain)
            if plain_match:
                bases.append(plain_match.group(1))
        if bases:
            break

    if not bases:
        # Minimal fixture/older offline text fallback.
        match = re.search(
            rf"\b{re.escape(object_name)}\s+extends(?:\s+the)?\s+"
            r"(Gui[A-Za-z0-9]+)(?:\s+Object)?\b",
            parse_html(body).text(),
            re.I,
        )
        if match:
            bases.append(match.group(1))
    return list(dict.fromkeys(bases))


def _infer_base_from_inherited(inherited: list[dict[str, Any]]) -> str:
    """Infer the nearest interface when a page omits its extends sentence.

    Fourteen PL08 pages expose the hierarchy only through inherited-member
    headers. The order below is from the most specialized documented
    interface to its ancestors, preventing transitive bases from being emitted
    as direct EXTENDS edges.
    """

    text = " ".join(str(item.get("text", "")) for item in inherited)
    for candidate in (
        "GuiShell",
        "GuiFrameWindow",
        "GuiTextField",
        "GuiVContainer",
        "GuiVComponent",
        "GuiContainer",
        "GuiComponentCollection",
        "GuiComponent",
    ):
        if re.search(rf"\b{re.escape(candidate)}\b", text, re.I):
            return candidate
    return ""


def _split_parameters(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def _normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _walk_toc(items: list[dict[str, Any]]) -> Iterable[dict[str, Any]]:
    for item in items:
        yield item
        yield from _walk_toc(item.get("c", []))


def _fetch_json(url: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url, headers={"User-Agent": "sap-cli-official-manifest-builder/1.0"}
    )
    with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--version", default=VERSION)
    parser.add_argument("--snapshot-dir")
    parser.add_argument("--request-delay", type=float, default=0.02)
    parser.add_argument("--no-strict", action="store_true")
    args = parser.parse_args()
    metadata, toc, pages = fetch_official_pages(
        version=args.version,
        snapshot_dir=Path(args.snapshot_dir) if args.snapshot_dir else None,
        request_delay=args.request_delay,
    )
    graph = build_graph(metadata=metadata, toc=toc, pages=pages)
    counts = validate_graph(graph, toc, strict=not args.no_strict)
    _write_json(Path(args.out), graph)
    print(json.dumps({"out": args.out, **counts}, ensure_ascii=False))


if __name__ == "__main__":
    main()
