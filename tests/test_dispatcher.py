"""중앙 dispatcher 단위 테스트."""

from __future__ import annotations

import pytest

from sap_cli.auth.policy import (
    AgentPolicy,
    AuditConfig,
    DangerousTransaction,
    Policy,
    RelationBinding,
)
from sap_cli.dispatcher import (
    ApprovalRequiredError,
    DispatchContext,
    PermissionDeniedError,
    dispatch_command,
)


class _FakeDump:
    def __init__(self, payload):
        self._payload = payload

    def model_dump(self, mode="json"):
        assert mode == "json"
        return self._payload

    def __getattr__(self, name):
        try:
            return self._payload[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


class _FakeSap:
    def __init__(self):
        self.transaction = "SESSION_MANAGER"
        self.screen_number = 100
        self.modal_text = None
        self.last_field = None

    def run_transaction(self, tcode: str) -> None:
        self.transaction = tcode
        self.screen_number = 4000

    def set_field(self, element_id: str, value: str) -> None:
        self.last_field = (element_id, value)
        self.screen_number = 4001

    def get_field(self, element_id: str) -> str:
        if self.last_field and self.last_field[0] == element_id:
            return self.last_field[1]
        return ""

    def send_command(self, command: str) -> None:
        self.transaction = command.lstrip("/n").lstrip("/o") or self.transaction
        self.screen_number = 4100

    def get_session_info(self):
        return _FakeDump(
            {
                "transaction": self.transaction,
                "screen_number": self.screen_number,
                "session_id": "ses[0]",
            }
        )

    def get_status(self):
        return _FakeDump({"type": "S", "message": "ok"})

    def has_modal(self) -> bool:
        return self.modal_text is not None

    def get_modal_text(self) -> str:
        return self.modal_text or ""


def _make_policy(tmp_path, relation: str = "operator") -> Policy:
    return Policy(
        agents={
            "standard": AgentPolicy(
                relations=[RelationBinding(resource="tx:*", relation=relation)],
            )
        },
        active_agent="standard",
        audit=AuditConfig(enabled=True, log_dir=str(tmp_path / "audit")),
    )


def test_dispatch_run_includes_post_action_context(monkeypatch, tmp_path):
    fake_sap = _FakeSap()
    monkeypatch.setattr("sap_cli.dispatcher._get_sap", lambda: fake_sap)
    monkeypatch.setattr(
        "sap_cli.dispatcher.load_policy",
        lambda: _make_policy(tmp_path),
    )

    result = dispatch_command(
        "run",
        {"tcode": "MM03"},
        DispatchContext(source="cli"),
    )

    assert result["executed"] == "MM03"
    assert result["transaction"] == "MM03"
    assert result["screen_number"] == 4000
    assert result["post_action"]["status"]["type"] == "S"
    assert result["post_action"]["modal"]["has_modal"] is False


def test_dispatch_command_requires_approval_for_dangerous_transaction(
    monkeypatch,
    tmp_path,
):
    fake_sap = _FakeSap()
    policy = _make_policy(tmp_path)
    policy.dangerous_transactions = [
        DangerousTransaction(tcode="SM30", reason="테이블 직접 수정")
    ]

    monkeypatch.setattr("sap_cli.dispatcher._get_sap", lambda: fake_sap)
    monkeypatch.setattr("sap_cli.dispatcher.load_policy", lambda: Policy.model_validate(policy.model_dump()))

    with pytest.raises(ApprovalRequiredError):
        dispatch_command(
            "command",
            {"cmd": "/nSM30"},
            DispatchContext(source="cli"),
        )


def test_dispatch_set_denied_for_readonly(monkeypatch, tmp_path):
    fake_sap = _FakeSap()
    readonly_policy = _make_policy(tmp_path, relation="viewer")
    monkeypatch.setattr("sap_cli.dispatcher._get_sap", lambda: fake_sap)
    monkeypatch.setattr("sap_cli.dispatcher.load_policy", lambda: readonly_policy)

    with pytest.raises(PermissionDeniedError):
        dispatch_command(
            "set",
            {"element_id": "wnd[0]/usr/ctxtTEST", "value": "100-100"},
            DispatchContext(source="cli"),
        )
