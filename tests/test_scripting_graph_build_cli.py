from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from sap_cli.cli import app
from scripts import build_sap_scripting_graph as legacy_builder


runner = CliRunner()


def _json_output(result):
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def test_scripting_graph_build_cli_writes_strict_official_graph(tmp_path):
    out = tmp_path / "graph.generated.json"
    graph = {
        "version": "8.00 PL08",
        "sources": [{"url": "https://help.sap.com/official"}],
        "nodes": [{"id": "object:GuiSession", "type": "api_object"}],
        "edges": [],
    }
    counts = {
        "topics": 96,
        "objects": 76,
        "enums": 10,
        "enum_values": 438,
        "methods": 466,
        "properties": 403,
        "events": 17,
        "extends": 67,
    }

    with (
        patch(
            "scripts.build_sap_help_manifest.fetch_official_pages",
            return_value=({"metadata": True}, [{"toc": True}], {"page": "body"}),
        ) as fetch,
        patch("scripts.build_sap_help_manifest.build_graph", return_value=graph),
        patch(
            "scripts.build_sap_help_manifest.validate_graph", return_value=counts
        ) as validate,
    ):
        data = _json_output(
            runner.invoke(
                app,
                [
                    "scripting-graph-build",
                    "--out",
                    str(out),
                    "--request-delay",
                    "0",
                ],
            )
        )

    assert data["status"] == "ok"
    assert data["validation"] == "strict_official_xhtml"
    assert data["counts"] == counts
    assert data["out"] == str(out)
    assert data["source"] == "https://help.sap.com/official"
    assert data["overlay_loaded_by_runtime"] is False
    fetch.assert_called_once()
    validate.assert_called_once_with(graph, [{"toc": True}], strict=True)
    generated = json.loads(out.read_text(encoding="utf-8"))
    assert any(node["id"] == "object:GuiSession" for node in generated["nodes"])


def test_legacy_builder_cannot_overwrite_runtime_overlay(tmp_path, monkeypatch):
    protected = tmp_path / "runtime" / "graph.generated.json"
    protected.parent.mkdir()
    protected.write_text("protected", encoding="utf-8")
    monkeypatch.setattr(legacy_builder, "DEFAULT_RUNTIME_OVERLAY", protected)

    with pytest.raises(ValueError, match="cannot overwrite"):
        legacy_builder.write_graph({"nodes": [], "edges": []}, str(protected))

    assert protected.read_text(encoding="utf-8") == "protected"
