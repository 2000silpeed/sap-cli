"""Data directory diagnostic tests."""

from __future__ import annotations

import json
import os
import tarfile
import time
import zipfile
from pathlib import Path

from typer.testing import CliRunner

from sap_cli.cli import app
from sap_cli.data_diagnostics import data_backup, data_clean, data_locations, data_status


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


def test_data_backup_creates_zip_with_recipes_macros_and_ontology(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    root = tmp_path / ".sap_cli"
    (root / "recipes").mkdir(parents=True)
    (root / "macros").mkdir()
    (root / "knowledge" / "transactions").mkdir(parents=True)
    (root / "recipes" / "r1.json").write_text('{"id":"r1"}', encoding="utf-8")
    (root / "macros" / "m1.json").write_text('{"id":"m1"}', encoding="utf-8")
    (root / "knowledge" / "transactions" / "MM03.json").write_text("{}", encoding="utf-8")

    result = data_backup()

    assert result["status"] == "ok"
    assert result["format"] == "zip"
    assert result["file_count"] == 3
    archive_path = Path(result["archive"])
    assert archive_path.exists()
    with zipfile.ZipFile(archive_path) as archive:
        names = set(archive.namelist())
    assert "backup_manifest.json" in names
    assert "recipes/r1.json" in names
    assert "macros/m1.json" in names
    assert "knowledge/transactions/MM03.json" in names


def test_data_backup_supports_tar_output(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    root = tmp_path / ".sap_cli"
    (root / "recipes").mkdir(parents=True)
    (root / "recipes" / "r1.json").write_text("{}", encoding="utf-8")
    output = tmp_path / "backup.tar.gz"

    result = data_backup(output=output, archive_format="tar")

    assert result["archive"] == str(output)
    with tarfile.open(output, "r:gz") as archive:
        names = set(archive.getnames())
    assert "backup_manifest.json" in names
    assert "recipes/r1.json" in names


def test_data_clean_dry_run_reports_old_logs_without_deleting(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    root = tmp_path / ".sap_cli"
    audit = root / "audit"
    audit.mkdir(parents=True)
    old_file = audit / "audit-2026-01-01.jsonl"
    new_file = audit / "audit-2026-04-26.jsonl"
    old_file.write_text("old", encoding="utf-8")
    new_file.write_text("new", encoding="utf-8")
    old_ts = time.time() - (40 * 24 * 60 * 60)
    os.utime(old_file, (old_ts, old_ts))

    result = data_clean(older_than_days=30, dry_run=True, categories=["audit"])

    assert result["dry_run"] is True
    assert result["candidate_file_count"] == 1
    assert result["deleted_file_count"] == 0
    assert old_file.exists()
    assert new_file.exists()


def test_data_clean_apply_deletes_only_old_selected_logs(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    root = tmp_path / ".sap_cli"
    session_dir = root / "sessions" / "2026-01-01"
    transcript_dir = root / "transcripts"
    session_dir.mkdir(parents=True)
    transcript_dir.mkdir(parents=True)
    old_session = session_dir / "s1.json"
    old_transcript = transcript_dir / "t1.jsonl"
    old_session.write_text("old", encoding="utf-8")
    old_transcript.write_text("old", encoding="utf-8")
    old_ts = time.time() - (60 * 24 * 60 * 60)
    os.utime(old_session, (old_ts, old_ts))
    os.utime(old_transcript, (old_ts, old_ts))

    result = data_clean(older_than_days=30, dry_run=False, categories=["sessions"])

    assert result["dry_run"] is False
    assert result["deleted_file_count"] == 1
    assert not old_session.exists()
    assert old_transcript.exists()


def test_data_maintenance_cli_commands(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    root = tmp_path / ".sap_cli"
    (root / "recipes").mkdir(parents=True)
    (root / "recipes" / "r1.json").write_text("{}", encoding="utf-8")
    (root / "audit").mkdir()
    old_audit = root / "audit" / "audit-old.jsonl"
    old_audit.write_text("old", encoding="utf-8")
    old_ts = time.time() - (31 * 24 * 60 * 60)
    os.utime(old_audit, (old_ts, old_ts))

    backup = _json_output(runner.invoke(app, ["data-backup"]))
    clean = _json_output(
        runner.invoke(app, ["data-clean", "--older-than-days", "30", "--category", "audit"])
    )

    assert Path(backup["archive"]).exists()
    assert backup["file_count"] == 1
    assert clean["dry_run"] is True
    assert clean["candidate_file_count"] == 1
    assert old_audit.exists()
