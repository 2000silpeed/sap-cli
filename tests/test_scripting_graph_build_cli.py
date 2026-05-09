from __future__ import annotations

import json

from typer.testing import CliRunner

from sap_cli.cli import app


runner = CliRunner()


def _json_output(result):
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def test_scripting_graph_build_cli_writes_generated_graph(tmp_path):
    source = tmp_path / "api.txt"
    out = tmp_path / "graph.generated.json"
    source.write_text(
        """
1 GuiSession Object
Methods
Public Sub SendVKey(ByVal key As Long)
Properties
Public Property Get Info() As Object
""",
        encoding="utf-8",
    )

    data = _json_output(
        runner.invoke(
            app,
            [
                "scripting-graph-build",
                str(source),
                "--out",
                str(out),
                "--source",
                "https://help.sap.com/example",
            ],
        )
    )

    assert data["status"] == "ok"
    assert data["nodes"] >= 3
    assert data["edges"] >= 2
    assert data["out"] == str(out)
    assert data["source"] == "https://help.sap.com/example"
    assert data["overlay_loaded_by_runtime"] is False
    generated = json.loads(out.read_text(encoding="utf-8"))
    assert any(node["id"] == "object:GuiSession" for node in generated["nodes"])
