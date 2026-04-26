"""Local SAP CLI data directory diagnostics."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def default_data_dir() -> Path:
    """Return the default SAP CLI data root."""
    return Path.home() / ".sap_cli"


def _policy_path(root: Path) -> Path:
    yaml_path = root / "policy.yaml"
    json_path = root / "policy.json"
    if yaml_path.exists():
        return yaml_path
    if json_path.exists():
        return json_path
    return yaml_path


def data_locations(root: str | Path | None = None) -> dict[str, Any]:
    """Return canonical local data locations without creating directories."""
    data_root = Path(root).expanduser() if root is not None else default_data_dir()
    policy_path = _policy_path(data_root)
    categories: dict[str, dict[str, Any]] = {
        "policy": {
            "path": str(policy_path),
            "exists": policy_path.exists(),
            "alternates": [str(data_root / "policy.yaml"), str(data_root / "policy.json")],
        },
        "recipes": {"path": str(data_root / "recipes"), "exists": (data_root / "recipes").exists()},
        "macros": {"path": str(data_root / "macros"), "exists": (data_root / "macros").exists()},
        "ontology": {"path": str(data_root / "knowledge"), "exists": (data_root / "knowledge").exists()},
        "audit": {"path": str(data_root / "audit"), "exists": (data_root / "audit").exists()},
        "transcripts": {
            "path": str(data_root / "transcripts"),
            "exists": (data_root / "transcripts").exists(),
        },
        "sessions": {"path": str(data_root / "sessions"), "exists": (data_root / "sessions").exists()},
        "captures": {
            "path": str(data_root / "screenshots"),
            "exists": (data_root / "screenshots").exists(),
        },
    }
    return {
        "data_dir": str(data_root),
        "exists": data_root.exists(),
        "categories": categories,
    }


def _iso_mtime(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def _status_for_path(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "path": str(path),
            "exists": False,
            "type": "missing",
            "file_count": 0,
            "dir_count": 0,
            "size_bytes": 0,
            "latest_mtime": "",
        }

    if path.is_file():
        stat = path.stat()
        return {
            "path": str(path),
            "exists": True,
            "type": "file",
            "file_count": 1,
            "dir_count": 0,
            "size_bytes": stat.st_size,
            "latest_mtime": _iso_mtime(stat.st_mtime),
        }

    file_count = 0
    dir_count = 0
    size_bytes = 0
    latest_mtime = path.stat().st_mtime
    for child in path.rglob("*"):
        try:
            stat = child.stat()
        except OSError:
            continue
        latest_mtime = max(latest_mtime, stat.st_mtime)
        if child.is_dir():
            dir_count += 1
        elif child.is_file():
            file_count += 1
            size_bytes += stat.st_size

    return {
        "path": str(path),
        "exists": True,
        "type": "directory",
        "file_count": file_count,
        "dir_count": dir_count,
        "size_bytes": size_bytes,
        "latest_mtime": _iso_mtime(latest_mtime),
    }


def data_status(root: str | Path | None = None) -> dict[str, Any]:
    """Return file counts and byte sizes for SAP CLI local data locations."""
    locations = data_locations(root)
    categories: dict[str, dict[str, Any]] = {}
    total_files = 0
    total_size_bytes = 0

    for name, info in locations["categories"].items():
        status = _status_for_path(Path(info["path"]))
        if name == "policy":
            status["alternates"] = info["alternates"]
        categories[name] = status
        total_files += status["file_count"]
        total_size_bytes += status["size_bytes"]

    return {
        "data_dir": locations["data_dir"],
        "exists": locations["exists"],
        "total_files": total_files,
        "total_size_bytes": total_size_bytes,
        "categories": categories,
    }
