"""SAP GUI Scripting API graph ontology tests."""

from sap_cli.scripting_api_graph import get_default_graph, query_default_graph
from sap_cli.scripting_ontology import component_capability


def test_query_component_type_returns_subgraph():
    result = query_default_graph(component_type="GuiComboBox", intent="select", depth=2)

    node_ids = {node["id"] for node in result["nodes"]}
    edge_types = {edge["type"] for edge in result["edges"]}

    assert "object:GuiComboBox" in node_ids
    assert "collection:GuiComboBox.Entries" in node_ids
    assert "intent:select" in node_ids
    assert "HAS_COLLECTION" in edge_types
    assert "SUPPORTS_ACTION" in edge_types


def test_component_capability_is_graph_backed():
    capability = component_capability("GuiComboBox")

    assert capability["graph_node"] == "object:GuiComboBox"
    assert "Key" in capability["write"]
    assert "Entries" in capability["collections"]
    assert "select" in capability["actions"]


def test_default_graph_lists_api_objects_when_no_query():
    result = get_default_graph().query(depth=0)

    assert any(node["id"] == "object:GuiSession" for node in result["nodes"])
    assert any(node["id"] == "intent:set" for node in result["nodes"])
