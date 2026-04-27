"""중앙 dispatcher 단위 테스트."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from sap_cli.auth.policy import (
    AgentPolicy,
    AuditConfig,
    DangerousReport,
    DangerousTransaction,
    Policy,
    RelationBinding,
    TranscriptConfig,
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


class _FakeField:
    def __init__(self, element_id: str, *, label: str, required: bool = False, value: str = ""):
        self.id = element_id
        self.name = label
        self.label = label
        self.type = SimpleNamespace(value="text_field")
        self.value = value
        self.required = required


class _FakeAction:
    def __init__(self, action_type: str, target_id: str, label: str, description: str):
        self.action_type = SimpleNamespace(value=action_type)
        self.target_id = target_id
        self.target_label = label
        self.description = description


class _FakeScreen:
    def __init__(self, transaction: str, screen_number: int):
        self.transaction = transaction
        self.screen_number = screen_number
        self.program = "SAPLMGMM"
        self.title = "Material Display"
        self.fields = [
            _FakeField("wnd[0]/usr/ctxtRMMG1-MATNR", label="Material", required=True),
            _FakeField("wnd[0]/usr/ctxtRMMG1-WERKS", label="Plant"),
        ]
        self.buttons = [SimpleNamespace(id="wnd[0]/tbar[1]/btn[8]", text="Execute")]
        self.tabs = [SimpleNamespace(id="wnd[0]/usr/tabsTAB/tabpSP01", text="Basic Data 1")]
        self.tables = []

    def get_changeable_fields(self):
        return list(self.fields)

    def get_available_actions(self):
        return [
            _FakeAction(
                "set_field_value",
                "wnd[0]/usr/ctxtRMMG1-MATNR",
                "Material",
                "Set material number",
            ),
            _FakeAction(
                "press_button",
                "wnd[0]/tbar[1]/btn[8]",
                "Execute",
                "Execute the current selection",
            ),
        ]


class _FakeSap:
    def __init__(self):
        self.transaction = "SESSION_MANAGER"
        self.screen_number = 100
        self.modal_text = None
        self.last_field = None
        self.vkeys = []

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

    def send_vkey(self, key: int) -> None:
        self.vkeys.append(key)
        self.screen_number = 4002

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

    def get_screen(self):
        return _FakeScreen(self.transaction, self.screen_number)

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
        transcript=TranscriptConfig(
            enabled=True,
            log_dir=str(tmp_path / "transcripts"),
            include_reads=True,
            include_screen_summary=True,
        ),
    )



def test_dispatch_run_includes_post_action_context(monkeypatch, tmp_path):
    fake_sap = _FakeSap()
    monkeypatch.setattr("sap_cli.dispatcher._get_sap", lambda slot_id="ses0": fake_sap)
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


def test_dispatch_run_can_prepare_and_execute_report(monkeypatch, tmp_path):
    fake_sap = _FakeSap()
    monkeypatch.setattr("sap_cli.dispatcher._get_sap", lambda slot_id="ses0": fake_sap)
    monkeypatch.setattr(
        "sap_cli.dispatcher.load_policy",
        lambda: _make_policy(tmp_path),
    )

    result = dispatch_command(
        "run",
        {"tcode": "SA38", "report": "rsparam", "execute_report": True},
        DispatchContext(source="cli"),
    )

    assert result["executed"] == "SA38"
    assert result["report"] == "RSPARAM"
    assert result["program"] == "RSPARAM"
    assert result["report_field"] == "wnd[0]/usr/ctxtRS38M-PROGRAMM"
    assert result["report_executed"] is True
    assert result["execute_vkey"] == 8
    assert fake_sap.last_field == ("wnd[0]/usr/ctxtRS38M-PROGRAMM", "RSPARAM")
    assert fake_sap.vkeys == [8]


def test_dispatch_run_dangerous_report_requires_approval_before_execution(
    monkeypatch,
    tmp_path,
):
    fake_sap = _FakeSap()
    policy = _make_policy(tmp_path)
    policy.dangerous_reports = [
        DangerousReport(
            report="RSPARAM",
            reason="시스템 파라미터 조회",
            category="system_configuration_report",
            preflight_code="dangerous_system_report",
        )
    ]

    monkeypatch.setattr("sap_cli.dispatcher._get_sap", lambda slot_id="ses0": fake_sap)
    monkeypatch.setattr("sap_cli.dispatcher.load_policy", lambda: policy)

    with pytest.raises(ApprovalRequiredError) as exc:
        dispatch_command(
            "run",
            {"tcode": "SA38", "report": "RSPARAM", "execute_report": True},
            DispatchContext(source="cli"),
        )

    assert "시스템 파라미터" in exc.value.reason
    assert fake_sap.transaction == "SESSION_MANAGER"
    assert fake_sap.last_field is None
    assert fake_sap.vkeys == []



def test_dispatch_run_can_include_screen_summary(monkeypatch, tmp_path):
    fake_sap = _FakeSap()
    monkeypatch.setattr("sap_cli.dispatcher._get_sap", lambda slot_id="ses0": fake_sap)
    monkeypatch.setattr(
        "sap_cli.dispatcher.load_policy",
        lambda: _make_policy(tmp_path),
    )

    result = dispatch_command(
        "run",
        {"tcode": "MM03"},
        DispatchContext(source="mcp", include_screen_summary=True),
    )

    summary = result["post_action"]["screen_summary"]
    assert summary["title"] == "Material Display"
    assert summary["changeable_field_count"] == 2
    assert summary["required_field_count"] == 1
    assert summary["changeable_fields"][0]["id"] == "wnd[0]/usr/ctxtRMMG1-MATNR"
    assert summary["available_actions"][0]["type"] == "set_field_value"



def test_dispatch_command_requires_approval_for_dangerous_transaction(
    monkeypatch,
    tmp_path,
):
    fake_sap = _FakeSap()
    policy = _make_policy(tmp_path)
    policy.dangerous_transactions = [
        DangerousTransaction(tcode="SM30", reason="테이블 직접 수정")
    ]

    monkeypatch.setattr("sap_cli.dispatcher._get_sap", lambda slot_id="ses0": fake_sap)
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
    monkeypatch.setattr("sap_cli.dispatcher._get_sap", lambda slot_id="ses0": fake_sap)
    monkeypatch.setattr("sap_cli.dispatcher.load_policy", lambda: readonly_policy)

    with pytest.raises(PermissionDeniedError):
        dispatch_command(
            "set",
            {"element_id": "wnd[0]/usr/ctxtTEST", "value": "100-100"},
            DispatchContext(source="cli"),
        )


def test_dispatch_run_writes_transcript_with_before_after(monkeypatch, tmp_path):
    fake_sap = _FakeSap()
    monkeypatch.setattr("sap_cli.dispatcher._get_sap", lambda slot_id="ses0": fake_sap)
    monkeypatch.setattr("sap_cli.service.get_sap", lambda slot_id="ses0": fake_sap)
    monkeypatch.setattr("sap_cli.dispatcher._get_existing_sap", lambda slot_id="ses0": fake_sap)
    monkeypatch.setattr(
        "sap_cli.dispatcher.load_policy",
        lambda: _make_policy(tmp_path),
    )

    dispatch_command(
        "run",
        {"tcode": "MM03"},
        DispatchContext(source="mcp", include_screen_summary=True),
    )

    files = list((tmp_path / "transcripts").glob("*.jsonl"))
    assert len(files) == 1
    entry = json.loads(files[0].read_text(encoding="utf-8").strip())
    assert entry["outcome"] == "success"
    assert entry["command"] == "run"
    assert entry["before"]["session"]["transaction"] == "SESSION_MANAGER"
    assert entry["after"]["session"]["transaction"] == "MM03"
    assert entry["after"]["screen_summary"]["title"] == "Material Display"
    assert entry["result"]["executed"] == "MM03"


def test_dispatch_approval_required_writes_transcript(monkeypatch, tmp_path):
    fake_sap = _FakeSap()
    policy = _make_policy(tmp_path)
    policy.dangerous_transactions = [
        DangerousTransaction(tcode="SM30", reason="테이블 직접 수정")
    ]

    monkeypatch.setattr("sap_cli.dispatcher._get_sap", lambda slot_id="ses0": fake_sap)
    monkeypatch.setattr("sap_cli.service.get_sap", lambda slot_id="ses0": fake_sap)
    monkeypatch.setattr("sap_cli.dispatcher._get_existing_sap", lambda slot_id="ses0": fake_sap)
    monkeypatch.setattr(
        "sap_cli.dispatcher.load_policy",
        lambda: Policy.model_validate(policy.model_dump()),
    )

    with pytest.raises(ApprovalRequiredError):
        dispatch_command(
            "command",
            {"cmd": "/nSM30"},
            DispatchContext(source="cli"),
        )

    files = list((tmp_path / "transcripts").glob("*.jsonl"))
    assert len(files) == 1
    entry = json.loads(files[0].read_text(encoding="utf-8").strip())
    assert entry["outcome"] == "approval_required"
    assert entry["approval_required"] is True
    assert entry["resource"] == "tx:SM30"
    assert entry["before"]["session"]["transaction"] == "SESSION_MANAGER"


def test_dispatch_transcript_query_reads_entries(monkeypatch, tmp_path):
    policy = _make_policy(tmp_path)
    transcript_dir = tmp_path / "transcripts"
    transcript_dir.mkdir(exist_ok=True)
    transcript_dir.joinpath("transcript-2026-04-01.jsonl").write_text(
        json.dumps(
            {
                "ts": "2026-04-01T00:00:00+00:00",
                "source": "mcp",
                "command": "run",
                "tool": "sap_run",
                "agent": "standard",
                "outcome": "success",
                "allowed": True,
                "approval_required": False,
                "permission": "execute",
                "resource": "tx:MM03",
                "reason": "operator on tx:*",
                "after": {
                    "session": {"transaction": "MM03", "screen_number": 4000},
                    "screen_summary": {"title": "Material Display"},
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("sap_cli.dispatcher.load_policy", lambda: policy)

    result = dispatch_command(
        "transcript_query",
        {"limit": 5, "command": "run"},
        DispatchContext(source="cli"),
    )

    assert result["returned"] == 1
    assert result["entries"][0]["command"] == "run"
    assert result["entries"][0]["screen_title"] == "Material Display"
    assert transcript_dir.joinpath("transcript-2026-04-01.jsonl").read_text(encoding="utf-8").count("\n") == 0


def test_dispatch_transcript_plan_returns_review_guidance(monkeypatch, tmp_path):
    policy = _make_policy(tmp_path)
    transcript_dir = tmp_path / "transcripts"
    transcript_dir.mkdir(exist_ok=True)
    transcript_dir.joinpath("transcript-2026-04-01.jsonl").write_text(
        json.dumps(
            {
                "ts": "2026-04-01T00:00:00+00:00",
                "source": "mcp",
                "command": "set",
                "tool": "sap_set_field",
                "agent": "standard",
                "outcome": "success",
                "allowed": True,
                "approval_required": False,
                "permission": "write",
                "resource": "element:wnd[0]/usr/ctxtRMMG1-MATNR",
                "reason": "operator (general permission)",
                "arguments": {
                    "element_id": "wnd[0]/usr/ctxtRMMG1-MATNR",
                    "value": "100-100",
                },
                "before": {
                    "session": {"transaction": "MM03", "screen_number": 4000},
                },
                "after": {
                    "session": {"transaction": "MM03", "screen_number": 4000},
                    "status": {"type": "S", "message": "ok"},
                    "screen_summary": {"title": "Material Display"},
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("sap_cli.dispatcher.load_policy", lambda: policy)

    result = dispatch_command(
        "transcript_plan",
        {"command": "set"},
        DispatchContext(source="cli"),
    )

    assert result["found"] is True
    assert result["risk_level"] == "high"
    assert result["entry"]["command"] == "set"
    assert result["ontology_context"]["tcode"] == "MM03"
    assert result["proposed_sequence"][2]["command"] == "sap ontology MM03"
    assert result["proposed_sequence"][3]["command"].startswith('sap set ')
    assert transcript_dir.joinpath("transcript-2026-04-01.jsonl").read_text(encoding="utf-8").count("\n") == 0
