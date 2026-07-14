from __future__ import annotations

from sap_cli.scripting_api_graph import ScriptingApiGraph, get_default_graph
from sap_cli.dispatchers.sap_actions import dispatch_scripting_query
from sap_cli.scripting_coverage import (
    build_api_coverage_manifest,
    classify_member_policy,
    coverage_summary,
    default_coverage_report,
    lookup_member_coverage,
)


def _graph() -> ScriptingApiGraph:
    return ScriptingApiGraph(
        {
            "version": "8.00 PL08",
            "sources": [{"title": "official", "url": "https://help.sap.com/example"}],
            "nodes": [
                {"id": "object:GuiComponent", "type": "api_object", "name": "GuiComponent"},
                {"id": "object:GuiTextField", "type": "api_object", "name": "GuiTextField"},
                {"id": "object:GuiApplication", "type": "api_object", "name": "GuiApplication"},
                {"id": "method:GuiTextField.SetFocus", "type": "api_method", "name": "SetFocus"},
                {"id": "property:GuiTextField.Text", "type": "api_property", "name": "Text", "access": "read-write"},
                {"id": "method:GuiApplication.OpenConnection", "type": "api_method", "name": "OpenConnection"},
                {"id": "event:GuiApplication.Error", "type": "api_event", "name": "Error"},
            ],
            "edges": [
                {"source": "object:GuiTextField", "target": "object:GuiComponent", "type": "EXTENDS"},
                {"source": "object:GuiTextField", "target": "method:GuiTextField.SetFocus", "type": "HAS_METHOD"},
                {"source": "object:GuiTextField", "target": "property:GuiTextField.Text", "type": "HAS_PROPERTY", "access": "read-write"},
                {"source": "object:GuiApplication", "target": "method:GuiApplication.OpenConnection", "type": "HAS_METHOD"},
                {"source": "object:GuiApplication", "target": "event:GuiApplication.Error", "type": "HAS_EVENT"},
            ],
        }
    )


def test_coverage_has_one_row_per_documented_object_and_member() -> None:
    manifest = build_api_coverage_manifest(_graph())
    assert len(manifest.members) == 7
    assert manifest.api_version == "8.00 PL08"
    assert manifest.source == "https://help.sap.com/example"
    assert all(row.policy_classified for row in manifest.members)


def test_visual_component_members_have_generic_fallback() -> None:
    manifest = build_api_coverage_manifest(_graph())
    text = next(
        row for row in manifest.members
        if row.object_name == "GuiTextField" and row.member_name == "Text"
    )
    assert text.adapter_supported
    assert text.access_path in {"typed_component_capability", "get_property/set_property"}
    assert text.permission == "write"
    assert text.risk_level == "high"


def test_verified_events_use_subscription_adapter() -> None:
    summary = coverage_summary(build_api_coverage_manifest(_graph()))
    assert "GuiApplication.OpenConnection:method" not in summary["gaps"]["adapter_missing"]
    assert "GuiApplication.Error:event" not in summary["gaps"]["adapter_missing"]
    assert summary["counts"]["policy_classified"] == summary["counts"]["total"]
    assert summary["adapter_gap_groups"]["administrative"]["event"] == 0


def test_unknown_member_policy_defaults_to_high_risk_write() -> None:
    assert classify_member_policy("method", "DoSomething") == ("write", "high")
    assert classify_member_policy("property", "Text", "read-only") == ("read", "low")
    assert classify_member_policy("event", "Change") == ("admin", "high")


def test_default_report_bounds_gap_payload() -> None:
    report = default_coverage_report(gap_limit=2)
    assert report["counts"]["total"] > 0
    assert all(len(values) <= 2 for values in report["gaps"].values())
    assert report["gap_counts"]["live_untested"] >= len(report["gaps"]["live_untested"])


def test_default_manifest_contains_only_official_objects_and_members() -> None:
    manifest = build_api_coverage_manifest()
    assert len(manifest.members) == 76 + 466 + 403 + 17 + 10 + 438
    assert not any(row.object_name == "Entries" for row in manifest.members)


def test_default_manifest_covers_enums_and_supported_signature_contracts() -> None:
    manifest = build_api_coverage_manifest()
    summary = coverage_summary(manifest)

    assert sum(row.member_kind == "enum" for row in manifest.members) == 10
    assert sum(row.member_kind == "enum_value" for row in manifest.members) == 438
    assert summary["gaps"]["adapter_missing"] == []

    rows = {
        (row.object_name, row.member_name, row.member_kind): row
        for row in manifest.members
    }
    assert rows[("GuiEnum", "Next", "method")].adapter_supported
    assert rows[("GuiCollection", "NewEnum", "property")].adapter_supported


def test_enum_metadata_adapter_resolves_every_official_enum_node() -> None:
    graph = get_default_graph()
    enum_node_ids = {
        node_id
        for node_id, node in graph.nodes.items()
        if node.get("type") in {"api_enum", "api_enum_value"}
    }

    assert len(enum_node_ids) == 10 + 438
    for node_id in enum_node_ids:
        result = graph.query(node_id=node_id, depth=0)
        assert [node["id"] for node in result["nodes"]] == [node_id]


def test_collection_members_use_opaque_handle_adapter() -> None:
    manifest = build_api_coverage_manifest()
    row = next(
        row
        for row in manifest.members
        if row.object_name == "GuiComponentCollection"
        and row.member_name == "ElementAt"
    )
    assert row.adapter_supported
    assert row.access_path == "api_object_handle"


def test_scripting_query_can_return_coverage_without_sap_connection() -> None:
    report = dispatch_scripting_query(
        {"coverage": True, "include_gaps": False, "gap_limit": 0}
    )
    assert report["counts"]["policy_classified"] == report["counts"]["total"]
    assert "gaps" not in report


def test_default_member_lookup_resolves_documented_member() -> None:
    row = lookup_member_coverage("GuiGridView", "method", "ContextMenu")
    assert row is not None
    assert row.policy_classified
    assert row.object_category == "visual"


def test_default_graph_object_categories_match_official_object_roles() -> None:
    manifest = build_api_coverage_manifest()
    objects = {
        row.object_name: row
        for row in manifest.members
        if row.member_kind == "object"
    }

    assert objects["GuiVContainer"].object_category == "visual"
    assert objects["GuiNetChart"].object_category == "visual"
    assert objects["GuiStatusBarLink"].object_category == "visual"
    assert objects["GuiTableColumn"].object_category == "collection"
    assert objects["GuiTableRow"].object_category == "collection"
    assert objects["GuiContainer"].object_category == "administrative"
    assert objects["GuiComboBoxEntry"].object_category == "value"
    assert objects["GuiEnum"].object_category == "value"
    assert objects["GuiScrollbar"].object_category == "value"

    # GuiComponent is the common abstract base for visual and administrative
    # objects. Claiming either category would overstate its runtime adapter.
    unknown = {
        row.object_name
        for row in objects.values()
        if row.object_category == "unknown"
    }
    assert unknown == {"GuiComponent"}


def test_gui_name_alone_does_not_make_unknown_object_visual() -> None:
    graph = ScriptingApiGraph(
        {
            "nodes": [
                {
                    "id": "object:GuiUndocumentedControl",
                    "type": "api_object",
                    "name": "GuiUndocumentedControl",
                }
            ],
            "edges": [],
        }
    )

    row = build_api_coverage_manifest(graph).members[0]

    assert row.object_category == "unknown"
    assert not row.adapter_supported


def test_descendant_of_gui_vcontainer_is_visual_without_name_allowlist() -> None:
    graph = ScriptingApiGraph(
        {
            "nodes": [
                {
                    "id": "object:GuiVContainer",
                    "type": "api_object",
                    "name": "GuiVContainer",
                },
                {
                    "id": "object:CustomVisualContainer",
                    "type": "api_object",
                    "name": "CustomVisualContainer",
                },
            ],
            "edges": [
                {
                    "source": "object:CustomVisualContainer",
                    "target": "object:GuiVContainer",
                    "type": "EXTENDS",
                }
            ],
        }
    )
    objects = {
        row.object_name: row
        for row in build_api_coverage_manifest(graph).members
        if row.member_kind == "object"
    }

    assert objects["GuiVContainer"].object_category == "visual"
    assert objects["CustomVisualContainer"].object_category == "visual"
