from __future__ import annotations

from scripts.build_sap_help_manifest import (
    build_graph,
    parse_enum_page,
    parse_object_page,
    parse_signature,
)


OBJECT_HTML = """
<html><body>
<p>GuiChild extends the <a href="base.html">GuiVComponent Object</a>.</p>
<section type="Methods"><table><tbody>
<tr><td colspan="2">All methods of the GuiVComponent Object: FindById</td></tr>
<tr><td><kbd class="ph userinput">FindThing</kbd>
<pre class="pre codeblock">Public Function FindThing( _
 ByVal Name As String, _
 Optional ByVal Raise As Variant _
) As GuiComponent</pre></td><td>Finds one thing.</td></tr>
</tbody></table></section>
<section type="Properties"><table><tbody>
<tr><td><kbd class="ph userinput">Text</kbd> (Read-write)
<pre class="pre codeblock">Public Property Text As String</pre></td><td>Value.</td></tr>
</tbody></table></section>
<section type="Events"><table><tbody>
<tr><td><kbd class="ph userinput">Change</kbd>
<pre class="pre codeblock">Public Event Change( _
 ByVal Component As GuiComponent _
)</pre></td><td>Changed.</td></tr>
</tbody></table></section>
</body></html>
"""


ENUM_HTML = """
<html><body><table><tbody>
<tr><td><kbd class="ph userinput">GuiTypeButton</kbd></td><td>40</td><td>Button</td></tr>
<tr><td><kbd class="ph userinput">GuiTypeLabel</kbd></td><td>41</td><td>Label</td></tr>
</tbody></table></body></html>
"""


def test_object_page_reads_only_direct_table_rows_and_signatures() -> None:
    parsed = parse_object_page(OBJECT_HTML, object_name="GuiChild")
    assert parsed["extends"] == "GuiVComponent"
    assert parsed["extends_all"] == ["GuiVComponent"]
    assert [item["name"] for item in parsed["members"]] == ["FindThing", "Text", "Change"]
    method = parsed["members"][0]
    assert method["return_type"] == "GuiComponent"
    assert method["parameters"] == [
        {"name": "Name", "type": "String", "optional": False, "passing": "byval"},
        {"name": "Raise", "type": "Variant", "optional": True, "passing": "byval"},
    ]
    assert parsed["members"][1]["access"] == "read-write"
    assert len(parsed["inherited"]) == 1


def test_enum_page_is_not_parsed_as_an_object() -> None:
    parsed = parse_enum_page(ENUM_HTML, enum_name="GuiComponentType")
    assert [item["name"] for item in parsed["values"]] == ["GuiTypeButton", "GuiTypeLabel"]


def test_signature_parser_keeps_optional_parameter_and_return_type() -> None:
    parsed = parse_signature(
        "Public Function OpenConnection(ByVal Description As String, Optional ByVal Sync As Variant) As GuiConnection",
        "method",
    )
    assert parsed["parameters"][1]["optional"] is True
    assert parsed["return_type"] == "GuiConnection"


def test_object_page_handles_official_nonstandard_method_rows() -> None:
    body = """
    <section type="Methods"><table><tbody>
    <tr><td><kbd class="ph userinput">PressButtonCurrentCel</kbd>l
      <pre class="pre codeblock">Public Sub PressButtonCurrentCell()</pre></td><td>Click.</td></tr>
    <tr><td>GetScriptingEngine<p>Description and example.</p>
      <pre class="pre codeblock">Set app = SapGuiAuto.GetScriptingEngine</pre></td>
      <td><pre class="pre codeblock">Public Function GetScriptingEngine() As Object</pre>
      <p>Return Type: GuiApplication</p></td></tr>
    <tr><td>Press</td><td>Triggers the action.</td></tr>
    </tbody></table></section>
    """
    parsed = parse_object_page(body, object_name="NonstandardObject")
    assert [item["name"] for item in parsed["members"]] == [
        "PressButtonCurrentCell",
        "GetScriptingEngine",
        "Press",
    ]
    assert parsed["members"][1]["return_type"] == "GuiApplication"


def test_object_page_preserves_multiple_direct_bases() -> None:
    body = """
    <p>GuiVContainer extends the <a>GuiContainer Object</a> and the
    <a>GuiVComponent Object</a>.</p>
    """
    parsed = parse_object_page(body, object_name="GuiVContainer")
    assert parsed["extends_all"] == ["GuiContainer", "GuiVComponent"]


def test_object_page_infers_nearest_base_from_inherited_headers() -> None:
    body = """
    <section type="Methods"><table><tbody>
    <tr><td colspan="2">All methods of the GuiVComponent Object: DumpState</td></tr>
    <tr><td colspan="2">All methods of the GuiVContainer Object: FindAllByName</td></tr>
    <tr><td colspan="2">All methods of the GuiShell Object: SelectContextMenuItem</td></tr>
    </tbody></table></section>
    """
    parsed = parse_object_page(body, object_name="GuiTree")
    assert parsed["extends_all"] == ["GuiShell"]


def test_graph_uses_separate_object_enum_and_member_nodes() -> None:
    metadata = {
        "data": {
            "deliverable": {
                "versionName": "8.00 PL08",
                "version": "800.08",
                "id": 1,
                "buildNo": 6,
            }
        }
    }
    toc = [
        {
            "t": "SAP GUI Scripting API",
            "u": "root.html",
            "c": [
                {"t": "Objects", "u": "objects.html", "c": [{"t": "GuiChild Object", "u": "child.html", "c": []}]},
                {"t": "Enumerations", "u": "enums.html", "c": [{"t": "GuiComponentType", "u": "enum.html", "c": []}]},
            ],
        }
    ]
    graph = build_graph(
        metadata=metadata,
        toc=toc,
        pages={"child.html": OBJECT_HTML, "enum.html": ENUM_HTML},
    )
    nodes = {node["id"]: node for node in graph["nodes"]}
    assert nodes["object:GuiChild"]["type"] == "api_object"
    assert nodes["enum:GuiComponentType"]["type"] == "api_enum"
    assert "method:GuiChild.FindThing" in nodes
    assert "property:GuiChild.Text" in nodes
    assert "event:GuiChild.Change" in nodes
