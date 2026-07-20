from __future__ import annotations

import io
import json

import pytest
import typer

from sap_cli.cli import _json_arg


def test_inline_json_passes_through():
    assert _json_arg('{"a": 1}') == '{"a": 1}'
    assert json.loads(_json_arg('{"a": 1}')) == {"a": 1}


def test_at_file_reads_json_from_path(tmp_path):
    # The exact case that breaks when embedded on a PowerShell command line:
    # nested double quotes. From a file there is no shell quoting at all.
    payload = '{"guided-1a2b":{"candidate_id":"c1"}}'
    p = tmp_path / "answer.json"
    p.write_text(payload, encoding="utf-8")

    resolved = _json_arg(f"@{p}", label="answers")
    assert json.loads(resolved) == {"guided-1a2b": {"candidate_id": "c1"}}


def test_at_dash_reads_from_stdin(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO('{"max_states": 200}'))
    assert json.loads(_json_arg("@-", label="budget")) == {"max_states": 200}


def test_missing_file_raises_bad_parameter():
    with pytest.raises(typer.BadParameter):
        _json_arg("@/no/such/file-xyz.json", label="answers")


def test_at_prefix_is_unambiguous_for_real_json():
    # Real JSON never starts with '@' (always {, [, ", digit, t/f/n), so the
    # sentinel cannot collide with a legitimate inline value.
    for literal in ('{"x":1}', "[1,2]", '"s"', "42", "true", "null"):
        assert _json_arg(literal) == literal
