"""Cross-platform shell guidance and Windows command validation."""

from __future__ import annotations

import platform
import re
from typing import Any


_BASH_ONLY = (
    (re.compile(r"(^|[;&|]\s*)(?:bash|sh)\b", re.I), "bash/sh invocation"),
    (re.compile(r"(^|[;&|]\s*)source\s+", re.I), "source command"),
    (re.compile(r"(^|[;&|]\s*)export\s+[A-Za-z_]", re.I), "export syntax"),
    (re.compile(r"&&"), "bash && chaining"),
    (re.compile(r"\bmkdir\s+-p\b", re.I), "mkdir -p"),
    (re.compile(r"(^|\s)(?:/tmp/|~/)"), "Unix path"),
)


def runtime_shell_guidance(platform_name: str | None = None) -> dict[str, Any]:
    """Return an explicit shell contract for tool-using LLMs."""

    system = (platform_name or platform.system()).strip() or "Unknown"
    if system.casefold() == "windows":
        return {
            "platform": "Windows",
            "preferred_shell": "powershell",
            "shell_executable": "powershell.exe",
            "forbidden_shells": ["bash", "sh", "wsl"],
            "venv_activate": r".venv\Scripts\Activate.ps1",
            "environment_assignment": '$env:NAME = "value"',
            "command_separator": ";",
            "instruction": (
                "Generate and run PowerShell syntax only. Do not emit bash/sh, "
                "source, export, &&, or Unix paths."
            ),
        }
    return {
        "platform": system,
        "preferred_shell": "bash",
        "shell_executable": "bash",
        "instruction": "Use the native POSIX shell syntax for this runtime.",
    }


def attach_runtime_guidance(
    payload: dict[str, Any], platform_name: str | None = None
) -> dict[str, Any]:
    """Attach shell policy on Windows without replacing command data."""

    guidance = runtime_shell_guidance(platform_name)
    if guidance["platform"] == "Windows" and "runtime_guidance" not in payload:
        payload = dict(payload)
        payload["runtime_guidance"] = guidance
    return payload


def normalize_external_step(
    *,
    cli_invocation: str,
    step_kind: str,
    shell: str = "",
    platform_name: str | None = None,
) -> tuple[str, str]:
    """Normalize a recorded external step and reject Bash syntax on Windows."""

    guidance = runtime_shell_guidance(platform_name)
    normalized_kind = str(step_kind or "external").strip().lower()
    normalized_shell = str(shell or "").strip().lower()
    if guidance["platform"] != "Windows":
        return normalized_kind, normalized_shell or "bash"

    violations = [
        description
        for pattern, description in _BASH_ONLY
        if pattern.search(str(cli_invocation or ""))
    ]
    if normalized_shell in {"bash", "sh", "wsl"}:
        violations.append(f"shell={normalized_shell}")
    if violations:
        raise ValueError(
            "Windows external recipe step requires PowerShell; unsupported Bash "
            "syntax: " + ", ".join(dict.fromkeys(violations))
        )
    if normalized_kind in {"bash", "sh", "shell"}:
        normalized_kind = "powershell"
    return normalized_kind, "powershell"


__all__ = [
    "attach_runtime_guidance",
    "normalize_external_step",
    "runtime_shell_guidance",
]
