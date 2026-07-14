"""Build an auditable coverage ledger for the SAP GUI Scripting API graph."""

from __future__ import annotations

from collections import defaultdict
from functools import lru_cache
from typing import Any

from .scripting_api_graph import ScriptingApiGraph, get_default_graph
from .scripting_contracts import (
    has_tested_runtime_contract,
    unsupported_adapter_reasons,
)
from .events.catalog import get_event_spec
from .scripting_ontology import COMPONENT_CAPABILITIES
from .ui_map.schema import ApiCoverageManifest, ApiMemberCoverage


_EDGE_KINDS = {
    "HAS_METHOD": "method",
    "HAS_PROPERTY": "property",
    "HAS_EVENT": "event",
}
_READ_METHOD_PREFIXES = ("dump", "find", "get", "is", "list", "read")
_ADMIN_OBJECTS = {
    "CSapROTWrapper",
    "SapGuiAuto",
    "GuiApplication",
    "GuiConnection",
    "GuiContainer",
    "GuiSession",
    "GuiSessionInfo",
    "GuiUtils",
}
_ADMIN_ADAPTER_OBJECTS = {
    "CSapROTWrapper",
    "GuiApplication",
    "GuiConnection",
    "GuiContainer",
    "GuiSession",
    "GuiSessionInfo",
    "GuiUtils",
    "SapGuiAuto",
}
_COLLECTION_OBJECTS = {
    "Entries",
    "GuiCollection",
    "GuiComponentCollection",
    "GuiTableColumn",
    "GuiTableRow",
}
_VALUE_OBJECTS = {
    "GuiComboBoxEntry",
    "GuiEnum",
    "GuiScrollbar",
}
# Some controls are documented as exposing GuiShell/GuiVContainer behavior,
# but the PDF layout does not always yield a reliable EXTENDS edge. Keep this
# conservative allowlist tied to the official object catalog instead of
# treating every Gui* name as a visual component.
_VISUAL_OBJECTS = {
    "GuiChart",
    "GuiComboBoxControl",
    "GuiGraphAdapt",
    "GuiInputFieldControl",
    "GuiMap",
    "GuiNetChart",
    "GuiOfficeIntegration",
    "GuiSapChart",
    "GuiScrollContainer",
    "GuiSplitterContainer",
    "GuiStage",
    "GuiStatusBarLink",
    "GuiStatusPane",
    "GuiTree",
}


def build_api_coverage_manifest(
    graph: ScriptingApiGraph | None = None,
    *,
    api_version: str = "",
    live_tested: dict[str, list[str]] | None = None,
) -> ApiCoverageManifest:
    """Return one coverage row for every documented object and member.

    Generic method/property access counts as an adapter only for objects that
    inherit from ``GuiComponent``. Administrative objects need a dedicated
    adapter and remain visible gaps until one exists.
    """

    graph = graph or get_default_graph()
    live_tested = live_tested or {}
    official_overlay = any(node.get("source_topic") for node in graph.nodes.values())
    object_nodes = {
        node_id: node
        for node_id, node in graph.nodes.items()
        if node.get("type") in {"api_object", "api_collection"}
        and (not official_overlay or node.get("source_topic"))
    }
    owners = _member_owners(graph, official_only=official_overlay)
    enum_nodes = {
        node_id: node
        for node_id, node in graph.nodes.items()
        if node.get("type") == "api_enum"
    }
    visual_objects = {
        _object_name(graph, node_id)
        for node_id in object_nodes
        if _object_category(graph, node_id) == "visual"
    }
    rows: list[ApiMemberCoverage] = []

    for node_id, node in sorted(object_nodes.items()):
        object_name = str(node.get("name") or node_id.removeprefix("object:"))
        category = _object_category(graph, node_id)
        modeled = object_name in COMPONENT_CAPABILITIES
        generic_supported = (
            object_name in visual_objects
            or object_name in _ADMIN_ADAPTER_OBJECTS
            or category in {"collection", "value"}
            or object_name == "GuiComponent"
        )
        rows.append(
            ApiMemberCoverage(
                object_name=object_name,
                member_name=object_name,
                member_kind="object",
                object_category=category,
                modeled=modeled,
                adapter_supported=modeled or generic_supported,
                policy_classified=True,
                permission="read",
                risk_level="low",
                access_path=(
                    "typed_component_capability"
                    if modeled
                    else (
                        "generic_element_inspection"
                        if object_name in visual_objects
                        else (
                            "api_object_scope"
                            if object_name in _ADMIN_ADAPTER_OBJECTS
                            else "api_object_handle"
                        )
                    ) if generic_supported else ""
                ),
                unit_tested=modeled,
                live_tested_versions=live_tested.get(node_id, []),
            )
        )

    for node_id, node in sorted(enum_nodes.items()):
        enum_name = str(node.get("name") or node_id.removeprefix("enum:"))
        rows.append(
            ApiMemberCoverage(
                object_name=enum_name,
                member_name=enum_name,
                member_kind="enum",
                object_category="value",
                adapter_supported=True,
                policy_classified=True,
                permission="read",
                risk_level="low",
                access_path="scripting_query",
                unit_tested=True,
                live_tested_versions=live_tested.get(node_id, []),
            )
        )
        for edge in graph.out_edges.get(node_id, []):
            if edge.get("type") != "HAS_ENUM_VALUE":
                continue
            value_id = str(edge["target"])
            value_node = graph.nodes.get(value_id, {})
            value_name = str(
                value_node.get("name") or value_id.rsplit(".", 1)[-1]
            )
            rows.append(
                ApiMemberCoverage(
                    object_name=enum_name,
                    member_name=value_name,
                    member_kind="enum_value",
                    object_category="value",
                    adapter_supported=True,
                    policy_classified=True,
                    permission="read",
                    risk_level="low",
                    access_path="scripting_query",
                    unit_tested=True,
                    live_tested_versions=live_tested.get(value_id, []),
                )
            )
    for member_id, owner_entries in sorted(owners.items()):
        member_node = graph.nodes.get(member_id, {})
        member_name = str(member_node.get("name") or member_id.rsplit(".", 1)[-1])
        for owner_id, member_kind, edge in owner_entries:
            object_name = owner_id.removeprefix("object:")
            category = _object_category(graph, owner_id)
            documented_access = str(
                edge.get("access") or member_node.get("access") or ""
            ).lower()
            permission, risk = classify_member_policy(
                member_kind, member_name, documented_access
            )
            modeled = _is_explicitly_modeled(
                object_name, member_kind, member_name
            )
            generic_supported = (
                object_name in visual_objects
                or object_name in _ADMIN_ADAPTER_OBJECTS
                or category in {"collection", "value"}
                or object_name == "GuiComponent"
            ) and member_kind in {"method", "property"}
            adapter_notes = unsupported_adapter_reasons(
                object_name, member_kind, member_name, member_node
            )
            generic_supported = generic_supported and not adapter_notes
            event_scope = (
                "application" if object_name == "GuiApplication" else "session"
            )
            event_supported = (
                member_kind == "event"
                and get_event_spec(event_scope, member_name) is not None
            )
            if modeled:
                access_path = "typed_component_capability"
            elif event_supported:
                access_path = "event_subscription"
            elif generic_supported:
                if object_name in _ADMIN_ADAPTER_OBJECTS:
                    access_path = "api_object_scope"
                elif category in {"collection", "value"} or object_name == "GuiComponent":
                    access_path = "api_object_handle"
                else:
                    access_path = (
                        "call_method"
                        if member_kind == "method"
                        else "get_property/set_property"
                    )
            else:
                access_path = ""
            notes: list[str] = []
            if member_kind == "event" and not event_supported:
                notes.append("event subscription adapter is not implemented")
            elif not generic_supported and not modeled:
                notes.append("dedicated adapter required for non-component object")
            notes.extend(adapter_notes)
            rows.append(
                ApiMemberCoverage(
                    object_name=object_name,
                    member_name=member_name,
                    member_kind=member_kind,
                    object_category=category,
                    modeled=modeled,
                    adapter_supported=modeled or generic_supported or event_supported,
                    policy_classified=True,
                    permission=permission,
                    risk_level=risk,
                    access_path=access_path,
                    documented_access=documented_access,
                    unit_tested=(
                        modeled
                        or event_supported
                        or has_tested_runtime_contract(
                            object_name, member_kind, member_name, member_node
                        )
                    ),
                    live_tested_versions=live_tested.get(member_id, []),
                    notes=notes,
                )
            )

    sources = graph.data.get("sources", [])
    source = ", ".join(
        str(item.get("url") or item.get("title") or "")
        for item in sources
        if item.get("url") or item.get("title")
    )
    return ApiCoverageManifest(
        api_version=api_version or str(graph.data.get("version", "")),
        source=source,
        members=rows,
    )


def classify_member_policy(
    member_kind: str,
    member_name: str,
    documented_access: str = "",
) -> tuple[str, str]:
    """Classify every documented member, defaulting unknown calls to high risk."""

    if member_kind == "event":
        return "admin", "high"
    if member_kind in {"object", "enum", "enum_value"}:
        return "read", "low"
    if member_kind == "property":
        return ("write", "high") if "write" in documented_access else ("read", "low")
    normalized = member_name.lower()
    if normalized.startswith(_READ_METHOD_PREFIXES):
        return "read", "low"
    if any(token in normalized for token in ("delete", "remove", "close", "create")):
        return "write", "high"
    if normalized.startswith(("press", "select", "send", "set", "modify", "open")):
        return "execute", "medium"
    return "write", "high"


def coverage_summary(manifest: ApiCoverageManifest) -> dict[str, Any]:
    """Return counts plus remaining gaps grouped by coverage stage."""

    adapter_gaps = [row for row in manifest.members if not row.adapter_supported]
    return {
        "api_version": manifest.api_version,
        "source": manifest.source,
        "counts": manifest.counts(),
        "gaps": {
            "unmodeled": _row_ids(manifest, "modeled"),
            "adapter_missing": _row_ids(manifest, "adapter_supported"),
            "policy_unclassified": _row_ids(manifest, "policy_classified"),
            "unit_untested": _row_ids(manifest, "unit_tested"),
            "live_untested": [
                f"{row.object_name}.{row.member_name}:{row.member_kind}"
                for row in manifest.members if not row.live_tested
            ],
        },
        "adapter_gap_groups": {
            category: {
                kind: sum(
                    row.object_category == category and row.member_kind == kind
                    for row in adapter_gaps
                )
                for kind in (
                    "object", "method", "property", "event", "enum", "enum_value"
                )
            }
            for category in (
                "visual", "administrative", "collection", "value", "unknown"
            )
        },
    }


def default_coverage_report(
    *, include_gaps: bool = True, gap_limit: int = 50
) -> dict[str, Any]:
    """Build the runtime ledger report with bounded gap samples."""

    if gap_limit < 0:
        raise ValueError("gap_limit must be zero or greater")
    from .scripting_live_coverage import load_live_tested_members

    report = coverage_summary(
        build_api_coverage_manifest(live_tested=load_live_tested_members())
    )
    gaps = report["gaps"]
    report["gap_counts"] = {name: len(values) for name, values in gaps.items()}
    if include_gaps:
        report["gaps"] = {name: values[:gap_limit] for name, values in gaps.items()}
        report["gaps_truncated"] = {
            name: len(values) > gap_limit for name, values in gaps.items()
        }
    else:
        report.pop("gaps", None)
    return report


@lru_cache(maxsize=1)
def _default_manifest() -> ApiCoverageManifest:
    return build_api_coverage_manifest()


def lookup_member_coverage(
    object_name: str, member_kind: str, member_name: str
) -> ApiMemberCoverage | None:
    """Resolve an exact or inherited member from the default API ledger."""

    member_kind = member_kind.lower()
    manifest = _default_manifest()
    graph = get_default_graph()
    object_node = next(
        (
            node_id for node_id, node in graph.nodes.items()
            if node.get("type") in {"api_object", "api_collection"}
            and str(node.get("name", "")).lower() == object_name.lower()
        ),
        f"object:{object_name}",
    )
    canonical_name = _object_name(graph, object_node)
    exact = next(
        (
            row for row in manifest.members
            if row.object_name.lower() == canonical_name.lower()
            and row.member_kind == member_kind
            and row.member_name.lower() == member_name.lower()
        ),
        None,
    )
    if exact is not None:
        return exact

    pending = [object_node]
    seen: set[str] = set()
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        base_name = current.removeprefix("object:")
        inherited = next(
            (
                row for row in manifest.members
                if row.object_name.lower() == base_name.lower()
                and row.member_kind == member_kind
                and row.member_name.lower() == member_name.lower()
            ),
            None,
        )
        if inherited is not None:
            return inherited
        pending.extend(
            str(edge["target"])
            for edge in graph.out_edges.get(current, [])
            if edge.get("type") == "EXTENDS"
        )
    return None


def _row_ids(manifest: ApiCoverageManifest, field: str) -> list[str]:
    return [
        f"{row.object_name}.{row.member_name}:{row.member_kind}"
        for row in manifest.members if not getattr(row, field)
    ]


def _member_owners(
    graph: ScriptingApiGraph,
    *,
    official_only: bool = False,
) -> dict[str, list[tuple[str, str, dict[str, Any]]]]:
    owners: dict[str, list[tuple[str, str, dict[str, Any]]]] = defaultdict(list)
    for edge in graph.edges:
        member_kind = _EDGE_KINDS.get(str(edge.get("type")))
        if not member_kind:
            continue
        if official_only and not graph.nodes.get(str(edge["target"]), {}).get(
            "source_topic"
        ):
            continue
        if official_only:
            owner_name = str(edge["source"]).removeprefix("object:")
            if not str(edge["target"]).startswith(
                f"{member_kind}:{owner_name}."
            ):
                continue
        owners[str(edge["target"])].append((str(edge["source"]), member_kind, edge))
    return owners


def _inherits_from(graph: ScriptingApiGraph, node_id: str, root_id: str) -> bool:
    pending = [node_id]
    seen: set[str] = set()
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        for edge in graph.out_edges.get(current, []):
            if edge.get("type") != "EXTENDS":
                continue
            target = str(edge["target"])
            if target == root_id:
                return True
            pending.append(target)
    return False


def _is_visual_object(graph: ScriptingApiGraph, node_id: str) -> bool:
    roots = {"object:GuiVComponent", "object:GuiVContainer", "object:GuiShell"}
    return node_id in roots or any(
        _inherits_from(graph, node_id, root_id) for root_id in roots
    ) or _object_name(graph, node_id) in {
        *COMPONENT_CAPABILITIES,
        *_VISUAL_OBJECTS,
    }


def _object_category(graph: ScriptingApiGraph, node_id: str) -> str:
    name = _object_name(graph, node_id)
    if name in _ADMIN_OBJECTS:
        return "administrative"
    if name in _COLLECTION_OBJECTS:
        return "collection"
    if name in _VALUE_OBJECTS:
        return "value"
    if _is_visual_object(graph, node_id):
        return "visual"
    return "unknown"


def _object_name(graph: ScriptingApiGraph, node_id: str) -> str:
    node = graph.nodes.get(node_id, {})
    return str(
        node.get("name")
        or node_id.removeprefix("object:").removeprefix("collection:")
    )


def _is_explicitly_modeled(
    object_name: str,
    member_kind: str,
    member_name: str,
) -> bool:
    capability = COMPONENT_CAPABILITIES.get(object_name, {})
    if not capability:
        return False
    if member_kind == "method":
        values = [
            *capability.get("methods", []),
            *capability.get("write", []),
            *capability.get("read", []),
        ]
    elif member_kind == "property":
        values = [*capability.get("read", []), *capability.get("write", [])]
    else:
        return False
    return member_name.lower() in {str(value).lower() for value in values}
