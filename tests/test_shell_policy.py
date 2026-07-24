from __future__ import annotations

import pytest

from sap_cli.shell_policy import (
    attach_runtime_guidance,
    normalize_external_step,
    runtime_shell_guidance,
)


def test_windows_guidance_is_explicitly_powershell():
    guidance = runtime_shell_guidance("Windows")

    assert guidance["preferred_shell"] == "powershell"
    assert "bash" in guidance["forbidden_shells"]
    assert guidance["venv_activate"] == r".venv\Scripts\Activate.ps1"


def test_windows_response_gets_runtime_guidance():
    result = attach_runtime_guidance({"ok": True}, "Windows")

    assert result["ok"] is True
    assert result["runtime_guidance"]["preferred_shell"] == "powershell"


@pytest.mark.parametrize(
    "command",
    [
        "bash scripts/run.sh",
        "source .venv/bin/activate",
        "export SAP_CLIENT=100",
        "python prepare.py && sap run MM03",
        "mkdir -p output",
        "python ~/scripts/prepare.py",
    ],
)
def test_windows_rejects_bash_only_recipe_commands(command):
    with pytest.raises(ValueError, match="requires PowerShell"):
        normalize_external_step(
            cli_invocation=command,
            step_kind="external",
            platform_name="Windows",
        )


def test_windows_normalizes_legacy_bash_kind_for_portable_command():
    kind, shell = normalize_external_step(
        cli_invocation="python scripts/prepare_input.py",
        step_kind="bash",
        platform_name="Windows",
    )

    assert kind == "powershell"
    assert shell == "powershell"


def test_posix_keeps_bash_default():
    kind, shell = normalize_external_step(
        cli_invocation="python scripts/prepare_input.py",
        step_kind="external",
        platform_name="Linux",
    )

    assert kind == "external"
    assert shell == "bash"
