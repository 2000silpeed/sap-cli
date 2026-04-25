"""공통 storage 유틸 테스트."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from sap_cli.storage import atomic_write_json, atomic_write_text, safe_read_json


def test_atomic_write_text_writes_file_and_cleans_tmp(tmp_path: Path):
    path = tmp_path / "data.json"

    atomic_write_text(path, '{"ok": true}', mode=0o644)

    assert path.read_text(encoding="utf-8") == '{"ok": true}'
    assert all(".tmp-" not in child.name for child in tmp_path.iterdir())


def test_atomic_write_text_keeps_existing_file_on_write_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    path = tmp_path / "data.txt"
    path.write_text("old", encoding="utf-8")

    def fail_write(fd: int, data: bytes | memoryview) -> int:
        raise OSError("simulated write failure")

    monkeypatch.setattr(os, "write", fail_write)

    with pytest.raises(OSError, match="simulated write failure"):
        atomic_write_text(path, "new")

    assert path.read_text(encoding="utf-8") == "old"
    assert all(".tmp-" not in child.name for child in tmp_path.iterdir())


def test_atomic_write_text_retries_short_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    path = tmp_path / "data.txt"
    real_write = os.write
    calls = 0

    def short_write(fd: int, data: bytes | memoryview) -> int:
        nonlocal calls
        calls += 1
        return real_write(fd, bytes(data[:1]))

    monkeypatch.setattr(os, "write", short_write)

    atomic_write_text(path, "abc")

    assert path.read_text(encoding="utf-8") == "abc"
    assert calls >= 3


def test_atomic_write_json_and_safe_read_json(tmp_path: Path):
    path = tmp_path / "data.json"

    atomic_write_json(path, {"message": "안녕"})

    assert safe_read_json(path) == {"message": "안녕"}
    assert safe_read_json(tmp_path / "missing.json") is None
    path.write_text("{not json", encoding="utf-8")
    assert safe_read_json(path) is None
