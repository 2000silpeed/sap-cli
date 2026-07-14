"""Durable, value-free evidence ledger for successful live API member calls."""

from __future__ import annotations

import os
import re
import threading
from pathlib import Path
from typing import Any

from .storage import atomic_write_json, safe_read_json


_MEMBER_ID = re.compile(r"^(?:method|property|event):[A-Za-z][A-Za-z0-9_]*\.[A-Za-z][A-Za-z0-9_]*$")
_VERSION = re.compile(r"^[A-Za-z0-9 ._+/-]{1,64}$")
_LOCK = threading.RLock()


def default_live_coverage_path() -> Path:
    configured = os.getenv("SAP_CLI_API_LIVE_COVERAGE_FILE", "").strip()
    return Path(configured or "~/.sap_cli/api_live_coverage.json").expanduser()


def load_live_tested_members(path: str | Path | None = None) -> dict[str, list[str]]:
    """Return validated member-id → tested API versions evidence only."""

    raw = safe_read_json(path or default_live_coverage_path())
    members = raw.get("members", {}) if isinstance(raw, dict) else {}
    if not isinstance(members, dict):
        return {}
    result: dict[str, list[str]] = {}
    for member_id, versions in members.items():
        if not isinstance(member_id, str) or not _MEMBER_ID.fullmatch(member_id):
            continue
        if not isinstance(versions, list):
            continue
        valid = sorted(
            {str(version) for version in versions if _VERSION.fullmatch(str(version))}
        )
        if valid:
            result[member_id] = valid
    return result


def record_live_member(
    member_id: str, *, api_version: str, path: str | Path | None = None
) -> None:
    """Atomically add successful live evidence; never stores SAP values or IDs."""

    if not _MEMBER_ID.fullmatch(member_id):
        raise ValueError("invalid API member id")
    from .scripting_api_graph import get_default_graph

    if member_id not in get_default_graph().nodes:
        raise ValueError("API member id is not present in the official graph")
    version = str(api_version).strip()
    if not _VERSION.fullmatch(version):
        raise ValueError("invalid API version")
    target = Path(path or default_live_coverage_path()).expanduser()
    with _LOCK:
        members = load_live_tested_members(target)
        members[member_id] = sorted(set(members.get(member_id, [])) | {version})
        atomic_write_json(
            target,
            {"schema_version": "sap-api-live-coverage.v1", "members": members},
            mode=0o600,
        )


__all__ = ["default_live_coverage_path", "load_live_tested_members", "record_live_member"]
