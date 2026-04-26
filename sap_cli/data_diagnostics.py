"""Local SAP CLI data directory diagnostics and maintenance."""

from __future__ import annotations

import io
import json
import tarfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BACKUP_CATEGORIES = ("recipes", "macros", "ontology")
CLEAN_CATEGORIES = ("audit", "transcripts", "sessions")


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


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


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


def _archive_name(now: datetime, archive_format: str) -> str:
    suffix = "tar.gz" if archive_format == "tar" else "zip"
    return f"sap-cli-data-backup-{now.strftime('%Y%m%d-%H%M%S')}.{suffix}"


def _iter_files(path: Path) -> list[Path]:
    if not path.exists():
        return []
    if path.is_file():
        return [path]
    return sorted(p for p in path.rglob("*") if p.is_file())


def _archive_arcname(data_root: Path, category: str, base_path: Path, file_path: Path) -> str:
    try:
        return str(file_path.relative_to(data_root))
    except ValueError:
        if base_path.is_file():
            return f"{category}/{file_path.name}"
        return str(Path(category) / file_path.relative_to(base_path))


def _backup_manifest(
    data_root: Path,
    archive_format: str,
    categories: dict[str, dict[str, Any]],
    now: datetime,
) -> dict[str, Any]:
    return {
        "generated_at": now.isoformat(),
        "data_dir": str(data_root),
        "format": archive_format,
        "categories": categories,
    }


def data_backup(
    root: str | Path | None = None,
    output: str | Path | None = None,
    archive_format: str = "zip",
) -> dict[str, Any]:
    """Create a recipe/macro/ontology backup archive."""
    archive_format = archive_format.lower().strip()
    if archive_format not in {"zip", "tar"}:
        raise ValueError("archive_format must be 'zip' or 'tar'")

    data_root = Path(root).expanduser() if root is not None else default_data_dir()
    now = _utc_now()
    archive_path = (
        Path(output).expanduser()
        if output is not None
        else data_root / "backups" / _archive_name(now, archive_format)
    )
    archive_path.parent.mkdir(parents=True, exist_ok=True)

    locations = data_locations(data_root)
    category_report: dict[str, dict[str, Any]] = {}
    file_entries: list[tuple[Path, str]] = []
    for category in BACKUP_CATEGORIES:
        info = locations["categories"][category]
        base_path = Path(info["path"])
        files = _iter_files(base_path)
        category_report[category] = {
            "path": str(base_path),
            "exists": base_path.exists(),
            "file_count": len(files),
            "included": bool(files),
        }
        for file_path in files:
            file_entries.append((file_path, _archive_arcname(data_root, category, base_path, file_path)))

    manifest = _backup_manifest(data_root, archive_format, category_report, now)
    manifest_bytes = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")

    if archive_format == "zip":
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("backup_manifest.json", manifest_bytes)
            for file_path, arcname in file_entries:
                archive.write(file_path, arcname)
    else:
        with tarfile.open(archive_path, "w:gz") as archive:
            info = tarfile.TarInfo("backup_manifest.json")
            info.size = len(manifest_bytes)
            info.mtime = now.timestamp()
            archive.addfile(info, io.BytesIO(manifest_bytes))
            for file_path, arcname in file_entries:
                archive.add(file_path, arcname=arcname, recursive=False)

    return {
        "status": "ok",
        "data_dir": str(data_root),
        "archive": str(archive_path),
        "format": archive_format,
        "file_count": len(file_entries),
        "size_bytes": archive_path.stat().st_size,
        "categories": category_report,
    }


def _cleanup_empty_dirs(path: Path) -> int:
    removed = 0
    if not path.exists() or not path.is_dir():
        return removed
    for child in sorted((p for p in path.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        try:
            child.rmdir()
        except OSError:
            continue
        removed += 1
    return removed


def data_clean(
    root: str | Path | None = None,
    older_than_days: int = 30,
    dry_run: bool = True,
    categories: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Delete old audit/transcript/session files, or report what would be deleted."""
    if older_than_days < 1:
        raise ValueError("older_than_days must be at least 1")

    selected = tuple(categories) if categories is not None else CLEAN_CATEGORIES
    unsupported = sorted(set(selected) - set(CLEAN_CATEGORIES))
    if unsupported:
        raise ValueError(f"unsupported clean categories: {', '.join(unsupported)}")

    data_root = Path(root).expanduser() if root is not None else default_data_dir()
    cutoff_ts = _utc_now().timestamp() - (older_than_days * 24 * 60 * 60)
    locations = data_locations(data_root)
    category_report: dict[str, dict[str, Any]] = {}
    total_files = 0
    total_size_bytes = 0
    removed_empty_dirs = 0

    for category in selected:
        base_path = Path(locations["categories"][category]["path"])
        candidates: list[dict[str, Any]] = []
        for file_path in _iter_files(base_path):
            try:
                stat = file_path.stat()
            except OSError:
                continue
            if stat.st_mtime >= cutoff_ts:
                continue
            item = {
                "path": str(file_path),
                "size_bytes": stat.st_size,
                "mtime": _iso_mtime(stat.st_mtime),
            }
            candidates.append(item)
            total_files += 1
            total_size_bytes += stat.st_size
            if not dry_run:
                file_path.unlink()

        if not dry_run:
            removed_empty_dirs += _cleanup_empty_dirs(base_path)

        category_report[category] = {
            "path": str(base_path),
            "exists": base_path.exists(),
            "candidate_file_count": len(candidates),
            "candidate_size_bytes": sum(int(item["size_bytes"]) for item in candidates),
            "files": candidates,
        }

    return {
        "status": "ok",
        "data_dir": str(data_root),
        "dry_run": dry_run,
        "older_than_days": older_than_days,
        "deleted_file_count": 0 if dry_run else total_files,
        "candidate_file_count": total_files,
        "candidate_size_bytes": total_size_bytes,
        "removed_empty_dir_count": 0 if dry_run else removed_empty_dirs,
        "categories": category_report,
    }
