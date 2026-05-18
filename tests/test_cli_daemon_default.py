"""CLI daemon default routing tests."""

from __future__ import annotations


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

    monkeypatch.setitem(__import__("sys").modules, "sap_cli.daemon", FakeDaemon)

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
