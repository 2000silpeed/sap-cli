"""CLI daemon default routing tests."""

from __future__ import annotations

import pytest


def test_cli_uses_daemon_by_default(monkeypatch):
    from sap_cli import cli

    monkeypatch.delenv("SAP_CLI_USE_DAEMON", raising=False)

    assert cli._use_daemon_default() is True


def test_cli_can_disable_daemon_with_env(monkeypatch):
    from sap_cli import cli

    monkeypatch.setenv("SAP_CLI_USE_DAEMON", "0")

    assert cli._use_daemon_default() is False


def test_try_daemon_autostarts_when_not_running(monkeypatch, capsys):
    from sap_cli import cli

    calls = []

    class FakeDaemon:
        @staticmethod
        def send_command(cmd, args, context=None):
            calls.append(("send", cmd, args, context))
            if len(calls) == 1:
                return None
            return {"ok": True, "data": {"via": "daemon"}}

        @staticmethod
        def start_daemon():
            calls.append(("start",))
            return True

    import sap_cli

    monkeypatch.setitem(__import__("sys").modules, "sap_cli.daemon", FakeDaemon)
    monkeypatch.setattr(sap_cli, "daemon", FakeDaemon, raising=False)

    assert cli._try_daemon("session", {}, slot_id="ses0") is True
    assert calls[0][0] == "send"
    assert calls[1] == ("start",)
    assert calls[2][0] == "send"
    assert '"via": "daemon"' in capsys.readouterr().out


def test_cli_screen_preserves_fields_only_args(monkeypatch):
    from sap_cli import cli

    calls = []
    monkeypatch.setattr(
        cli,
        "_run_command",
        lambda cmd, args, allow_daemon=False, slot_id=None: calls.append(
            (cmd, args, allow_daemon, slot_id)
        ),
    )

    cli.screen(fields_only=True, actions_only=False, search=None)

    assert calls == [
        (
            "screen",
            {"fields_only": True, "actions_only": False, "search": None},
            True,
            None,
        )
    ]


@pytest.mark.parametrize("command", ["plan_preview", "plan_approve", "plan_execute"])
def test_plan_security_commands_never_fall_back_to_local_dispatch(monkeypatch, command):
    import pytest
    from typer import Exit
    from sap_cli import cli

    monkeypatch.setattr(cli, "_try_daemon", lambda *args, **kwargs: False)
    monkeypatch.setattr(cli, "_dispatch_local", lambda *args, **kwargs: pytest.fail("local fallback"))
    monkeypatch.setattr(cli, "_use_daemon_default", lambda: True)

    with pytest.raises(Exit):
        cli._run_command(
            command,
            {"plan_id": "p", "plan_hash": "h", "execution_id": "e"},
            allow_daemon=True,
        )


def test_common_job_commands_never_fall_back_to_local_dispatch(monkeypatch):
    import pytest
    from typer import Exit
    from sap_cli import cli

    monkeypatch.setattr(cli, "_try_daemon", lambda *args, **kwargs: False)
    monkeypatch.setattr(cli, "_dispatch_local", lambda *args, **kwargs: pytest.fail("local fallback"))
    monkeypatch.setattr(cli, "_use_daemon_default", lambda: True)

    for command in ("job_status", "job_reconcile"):
        with pytest.raises(Exit):
            cli._run_command(command, {"job_id": "plan:e"}, allow_daemon=True)


@pytest.mark.parametrize(
    ("command", "args"),
    [
        ("map_augment_start", {"map_id": "map", "tcode": "ZMAP", "from_state_id": "state", "request": "누락 화면"}),
        ("map_traverse_start", {"tcode": "ZMAP"}),
        ("map_traverse_resume", {"job_id": "map-job", "answers": {"token": {"continue": True}}}),
        ("map_job_cancel", {"job_id": "map-job"}),
    ],
)
def test_map_job_commands_never_fall_back_to_local_dispatch(monkeypatch, command, args):
    import pytest
    from typer import Exit
    from sap_cli import cli

    monkeypatch.setattr(cli, "_try_daemon", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        cli, "_dispatch_local", lambda *args, **kwargs: pytest.fail("local fallback")
    )
    monkeypatch.setattr(cli, "_use_daemon_default", lambda: True)

    with pytest.raises(Exit):
        cli._run_command(
            command,
            args,
            allow_daemon=True,
        )


def test_plan_execute_uses_long_bounded_daemon_timeout(monkeypatch):
    from sap_cli.daemon import command_timeout

    monkeypatch.setenv("SAP_CLI_PLAN_EXECUTE_TIMEOUT", "7200")
    assert command_timeout("plan_execute") == 7200.0
    assert command_timeout("session") == 60.0

    monkeypatch.setenv("SAP_CLI_PLAN_EXECUTE_TIMEOUT", "999999")
    assert command_timeout("plan_execute") == 86400.0
