from __future__ import annotations

import json

from typer.testing import CliRunner

import sap_cli.cli as cli
from sap_cli.cli import app


runner = CliRunner()


def test_recipe_replay_plan_cli_dispatches(monkeypatch):
    calls: list[tuple[str, dict, dict]] = []

    def fake_run_command(cmd: str, args: dict, **kwargs):
        calls.append((cmd, args, kwargs))

    monkeypatch.setattr(cli, "_run_command", fake_run_command)

    result = runner.invoke(
        app,
        [
            "recipe-replay-plan",
            "demo",
            "--current-tcode",
            "MM03",
            "--current-screen",
            "4000",
            "--allow-disabled",
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls == [
        (
            "recipe_replay_plan",
            {
                "recipe_id": "demo",
                "current_tcode": "MM03",
                "current_screen": "4000",
                "allow_disabled": True,
            },
            {},
        )
    ]

def test_recipe_replay_execute_cli_dispatches_with_json_file(monkeypatch, tmp_path):
    criteria_path = tmp_path / "criteria.json"
    criteria_path.write_text(
        json.dumps({"expected_tcode": "VA02", "require_no_modal": False}),
        encoding="utf-8",
    )
    calls: list[tuple[str, dict, dict]] = []

    def fake_run_command(cmd: str, args: dict, **kwargs):
        calls.append((cmd, args, kwargs))

    monkeypatch.setattr(cli, "_run_command", fake_run_command)

    result = runner.invoke(
        app,
        [
            "recipe-replay-execute",
            "demo",
            "--success-criteria",
            f"@{criteria_path}",
            "--no-save-success-criteria",
            "--no-auto-report",
            "--allow-modal",
            "--observation-scope",
            "screen",
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls == [
        (
            "recipe_replay_execute",
            {
                "recipe_id": "demo",
                "current_tcode": "",
                "current_screen": "",
                "allow_disabled": False,
                "allow_modal": True,
                "auto_report": False,
                "observation_scope": "screen",
                "success_criteria": {
                    "expected_tcode": "VA02",
                    "require_no_modal": False,
                },
                "save_success_criteria": False,
            },
            {"allow_daemon": True},
        )
    ]


def test_recipe_version_up_cli_accepts_steps_json(monkeypatch):
    calls: list[tuple[str, dict, dict]] = []

    def fake_run_command(cmd: str, args: dict, **kwargs):
        calls.append((cmd, args, kwargs))

    monkeypatch.setattr(cli, "_run_command", fake_run_command)

    result = runner.invoke(
        app,
        [
            "recipe-version-up",
            "demo",
            '[{"tool":"sap_run","args":{"tcode":"MM03"},"hint":"MM03 진입"}]',
            "--change-note",
            "screen changed",
            "--replay-success-criteria",
            '{"expected_tcode":"MM03"}',
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls == [
        (
            "recipe_version_up",
            {
                "recipe_id": "demo",
                "new_steps": [
                    {
                        "tool": "sap_run",
                        "args": {"tcode": "MM03"},
                        "hint": "MM03 진입",
                    }
                ],
                "change_note": "screen changed",
                "replay_success_criteria": {"expected_tcode": "MM03"},
            },
            {},
        )
    ]
