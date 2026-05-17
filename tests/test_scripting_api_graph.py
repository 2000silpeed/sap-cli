"""SAP GUI Scripting API graph ontology tests."""

from scripts.build_sap_scripting_graph import build_graph
from sap_cli.scripting_api_graph import get_default_graph, merge_graph_data, query_default_graph
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
    assert result["runtime"]["nodes_total"] >= len(result["nodes"])
    assert "generated_overlay_loaded" in result["runtime"]


def test_build_graph_parses_numbered_pdf_headings_and_signatures():
    graph = build_graph(
        """
        GuiComboBox Object . . . . . . . . . . . . . . . . . . . . . . 75
        1.2.12 GuiComboBox Object
        GuiComboBox extends the GuiVComponent Object [page 281].
        Methods
        Method
        Syntax
        PUBLIC
        SetKeySpace
        Public Sub SetKeySpace()
        Properties
        Key (Read-write)
        Public Property Key As String
        Text (Read-only)
        Public Property Text As String
        1.2.65 GuiTextField
        GuiTextField extends the GuiVComponent Object [page 281].
        Methods
        GetListProperty
        Public Function GetListProperty( _
        ) As String
        """,
        source="https://help.sap.com/example.pdf",
    )

    node_ids = {node["id"] for node in graph["nodes"]}
    edge_tuples = {(edge["source"], edge["target"], edge["type"]) for edge in graph["edges"]}

    assert "object:GuiComboBox" in node_ids
    assert "object:GuiTextField" in node_ids
    assert "method:GuiComboBox.PUBLIC" not in node_ids
    assert ("object:GuiComboBox", "object:GuiVComponent", "EXTENDS") in edge_tuples
    assert ("object:GuiComboBox", "property:GuiComboBox.Key", "HAS_PROPERTY") in edge_tuples
    assert ("object:GuiComboBox", "method:GuiComboBox.SetKeySpace", "HAS_METHOD") in edge_tuples
    assert ("object:GuiTextField", "method:GuiTextField.GetListProperty", "HAS_METHOD") in edge_tuples


def test_build_graph_skips_pdf_table_and_example_artifacts():
    graph = build_graph(
        """
        1.2.30 GuiGridView Object
        Methods
        ContextMenu
        Example
        Error
        Button
        Ascending
        Descending
        GUI
        Public Sub DoubleClickCurrentCell()
        1.2.40 GuiMainWindow Object
        Methods
        Remarks
        Public Sub SendVKey( _
        ) As Void
        """,
        source="https://help.sap.com/example.pdf",
    )

    node_ids = {node["id"] for node in graph["nodes"]}

    assert "method:GuiGridView.DoubleClickCurrentCell" in node_ids
    assert "method:GuiMainWindow.SendVKey" in node_ids
    assert "method:GuiGridView.ContextMenu" not in node_ids
    assert "method:GuiGridView.Example" not in node_ids
    assert "method:GuiGridView.Error" not in node_ids
    assert "method:GuiGridView.Button" not in node_ids
    assert "method:GuiGridView.Ascending" not in node_ids
    assert "method:GuiGridView.Descending" not in node_ids
    assert "method:GuiGridView.GUI" not in node_ids
    assert "method:GuiMainWindow.Remarks" not in node_ids


def test_merge_graph_data_preserves_seed_and_adds_generated_members():
    seed = {
        "version": "seed",
        "sources": [{"title": "seed", "url": "seed://graph"}],
        "nodes": [
            {"id": "object:GuiComboBox", "type": "api_object", "name": "GuiComboBox", "curated": True},
            {"id": "intent:select", "type": "intent", "name": "select"},
        ],
        "edges": [
            {"source": "object:GuiComboBox", "target": "intent:select", "type": "SUPPORTS_ACTION"},
        ],
    }
    generated = build_graph(
        """
        1.2.12 GuiComboBox Object
        Properties
        Value (Read-write)
        Public Property Value As String
        """,
        source="https://help.sap.com/sap_gui_scripting_api.pdf",
    )

    merged = merge_graph_data(seed, generated)
    nodes = {node["id"]: node for node in merged["nodes"]}
    edge_tuples = {(edge["source"], edge["target"], edge["type"]) for edge in merged["edges"]}

    assert nodes["object:GuiComboBox"]["curated"] is True
    assert merged["runtime"]["generated_overlay_loaded"] is True
    assert "property:GuiComboBox.Value" in nodes
    assert ("object:GuiComboBox", "intent:select", "SUPPORTS_ACTION") in edge_tuples
    assert ("object:GuiComboBox", "property:GuiComboBox.Value", "HAS_PROPERTY") in edge_tuples
