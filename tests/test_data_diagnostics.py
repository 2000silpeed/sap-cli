"""Data directory diagnostic tests."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from sap_cli.cli import app
from sap_cli.data_diagnostics import data_locations, data_status


runner = CliRunner()


def _json_output(result) -> dict:
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def test_data_locations_reports_default_categories(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))

    data = data_locations()

    assert data["data_dir"] == str(tmp_path / ".sap_cli")
    assert data["exists"] is False
    assert set(data["categories"]) == {
        "policy",
        "recipes",
        "macros",
        "ontology",
        "audit",
        "transcripts",
        "sessions",
        "captures",
    }
    assert data["categories"]["recipes"]["path"] == str(tmp_path / ".sap_cli" / "recipes")
    assert data["categories"]["captures"]["path"] == str(tmp_path / ".sap_cli" / "screenshots")


def test_data_status_counts_files_and_sizes(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    root = tmp_path / ".sap_cli"
    recipes = root / "recipes"
    sessions = root / "sessions" / "2026-04-26"
    recipes.mkdir(parents=True)
    sessions.mkdir(parents=True)
    (root / "policy.json").write_text('{"version": 1}', encoding="utf-8")
    (recipes / "r1.json").write_text("abc", encoding="utf-8")
    (sessions / "s1.json").write_text("hello", encoding="utf-8")

    data = data_status()

    assert data["exists"] is True
    assert data["categories"]["policy"]["type"] == "file"
    assert data["categories"]["policy"]["path"] == str(root / "policy.json")
    assert data["categories"]["recipes"]["file_count"] == 1
    assert data["categories"]["recipes"]["size_bytes"] == 3
    assert data["categories"]["sessions"]["file_count"] == 1
    assert data["categories"]["sessions"]["dir_count"] == 1
    assert data["total_files"] == 3
    assert data["total_size_bytes"] == len('{"version": 1}') + 3 + 5


def test_data_diagnostic_cli_commands(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".sap_cli" / "macros").mkdir(parents=True)
    (tmp_path / ".sap_cli" / "macros" / "m1.json").write_text("{}", encoding="utf-8")

    locations = _json_output(runner.invoke(app, ["data-dir"]))
    status = _json_output(runner.invoke(app, ["data-status"]))

    assert locations["data_dir"] == str(tmp_path / ".sap_cli")
    assert locations["categories"]["macros"]["exists"] is True
    assert status["categories"]["macros"]["file_count"] == 1
