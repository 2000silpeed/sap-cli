from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest
from pydantic import ValidationError

from sap_cli.jobs.store import (
    JobLedgerConflictError,
    JobLedgerNotFoundError,
    PlanJobLedger,
    PlanJobRecord,
)


class Clock:
    def __init__(self) -> None:
        self.value = 0

    def __call__(self) -> str:
        self.value += 1
        return f"2026-07-15T00:00:{self.value:02d}+00:00"


def create(ledger: PlanJobLedger, *, job_id: str = "job-1") -> PlanJobRecord:
    return ledger.create(
        job_id=job_id,
        execution_id=f"exec-{job_id}",
        plan_id="plan-1",
        plan_hash="sha256-plan",
        request_digest="sha256-request",
        owner="agent-a",
        slot="ses0",
    )


def test_durable_record_is_value_free_strict_json_and_mode_0600(tmp_path: Path) -> None:
    secret = "NEVER-PERSIST-ME-4711"
    ledger = PlanJobLedger(tmp_path, clock=Clock())
    record = create(ledger)
    path = tmp_path / "job-1.json"
    raw = path.read_text(encoding="utf-8")
    payload = json.loads(raw)

    assert record.status == "running"
    assert set(payload) == {
        "job_id",
        "execution_id",
        "plan_id",
        "plan_hash",
        "request_digest",
        "owner",
        "slot",
        "status",
        "created_at",
        "updated_at",
        "started_at",
        "finished_at",
        "error_code",
    }
    for forbidden in ("values", "grants", "result", "params", "plan", "payload"):
        assert forbidden not in payload
    assert secret not in raw
    assert stat.S_IMODE(path.stat().st_mode) == 0o600

    with pytest.raises(ValidationError):
        PlanJobRecord.model_validate({**payload, "result": {"secret": secret}})


def test_create_is_idempotent_only_for_the_exact_immutable_scope(tmp_path: Path) -> None:
    ledger = PlanJobLedger(tmp_path, clock=Clock())
    first = create(ledger)
    replay = create(ledger)

    assert replay == first
    with pytest.raises(JobLedgerConflictError, match="another execution scope"):
        ledger.create(
            job_id="job-1",
            execution_id="different",
            plan_id="plan-1",
            plan_hash="sha256-plan",
            request_digest="sha256-request",
            owner="agent-a",
            slot="ses0",
        )


def test_update_preserves_scope_and_uses_only_safe_error_codes(tmp_path: Path) -> None:
    clock = Clock()
    ledger = PlanJobLedger(tmp_path, clock=clock)
    original = create(ledger)
    failed = ledger.update(
        original.job_id,
        status="failed",
        error_code="Exception: password=secret",
    )

    assert failed.scope == original.scope
    assert failed.error_code == "job_failed"
    assert failed.finished_at is not None
    assert "secret" not in (tmp_path / "job-1.json").read_text(encoding="utf-8")

    with pytest.raises(JobLedgerConflictError, match="invalid durable"):
        ledger.update("job-1", status="running")


def test_safe_machine_error_code_is_preserved_for_unknown_outcome(tmp_path: Path) -> None:
    ledger = PlanJobLedger(tmp_path, clock=Clock())
    create(ledger)

    unknown = ledger.update(
        "job-1",
        status="outcome_unknown",
        error_code="commit_outcome_unknown",
    )

    assert unknown.error_code == "commit_outcome_unknown"


def test_reopen_atomically_recovers_running_to_outcome_unknown(tmp_path: Path) -> None:
    original = PlanJobLedger(tmp_path, clock=Clock())
    create(original, job_id="running")
    create(original, job_id="finished")
    original.update("finished", status="completed")

    reopened = PlanJobLedger(tmp_path, clock=Clock())

    assert [item.job_id for item in reopened.startup_recovered] == ["running"]
    recovered = reopened.get("running")
    assert recovered.status == "outcome_unknown"
    assert recovered.error_code == "startup_outcome_unknown"
    assert recovered.finished_at is not None
    assert reopened.get("finished").status == "completed"
    assert reopened.recover_running() == []


def test_get_list_and_scope_filters_return_durable_records(tmp_path: Path) -> None:
    ledger = PlanJobLedger(tmp_path, clock=Clock())
    create(ledger, job_id="one")
    ledger.create(
        job_id="two",
        execution_id="exec-two",
        plan_id="plan-2",
        plan_hash="hash-2",
        request_digest="digest-2",
        owner="agent-b",
        slot="ses1",
        status="queued",
    )

    assert ledger.get("one").execution_id == "exec-one"
    assert [item.job_id for item in ledger.list()] == ["one", "two"]
    assert [item.job_id for item in ledger.list(status="queued")] == ["two"]
    assert [item.job_id for item in ledger.list(owner="agent-a", slot="ses0")] == [
        "one"
    ]
    with pytest.raises(JobLedgerNotFoundError):
        ledger.get("missing")


def test_authorization_helpers_reject_cross_owner_slot_and_plan_scope(
    tmp_path: Path,
) -> None:
    ledger = PlanJobLedger(tmp_path, clock=Clock())
    expected = create(ledger)

    assert ledger.authorize("job-1", owner="agent-a", slot="ses0") == expected
    assert (
        ledger.authorize_scope(
            "job-1",
            owner="agent-a",
            slot="ses0",
            execution_id="exec-job-1",
            plan_id="plan-1",
            plan_hash="sha256-plan",
            request_digest="sha256-request",
        )
        == expected
    )
    for scope in (
        {"owner": "agent-b"},
        {"slot": "ses1"},
        {"execution_id": "exec-other"},
        {"plan_id": "plan-other"},
        {"plan_hash": "hash-other"},
        {"request_digest": "digest-other"},
    ):
        with pytest.raises(PermissionError, match="another execution scope"):
            ledger.authorize_scope("job-1", **scope)


@pytest.mark.parametrize("job_id", ["../escape", "/absolute", "has space", ""])
def test_job_id_cannot_escape_the_store(tmp_path: Path, job_id: str) -> None:
    ledger = PlanJobLedger(tmp_path, clock=Clock())

    with pytest.raises(ValueError, match="job_id"):
        create(ledger, job_id=job_id)
