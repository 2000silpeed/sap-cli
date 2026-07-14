import threading

import pytest

from sap_cli.planning.approvals import (
    ApprovalTokenConsumedError,
    ApprovalTokenExpiredError,
    ApprovalTokenInvalidError,
    ApprovalTokenError,
    PlanApprovalStore,
)


class Clock:
    value = 100.0

    def __call__(self):
        return self.value


def issue(store: PlanApprovalStore):
    return store.issue(
        plan_id="plan-1",
        plan_hash="hash-1",
        step_ids=["step-1", "step-2", "step-1"],
        subject_agent="operator",
        slot_id="ses0",
        ttl_seconds=30,
    )


def test_token_is_scope_bound_and_consumed_once_without_raw_storage():
    store = PlanApprovalStore(token_factory=lambda: "opaque-secret-token")
    receipt = issue(store)

    assert receipt.token == "opaque-secret-token"
    assert "opaque-secret-token" not in repr(store._records)
    assert store.validate(
        receipt.token,
        plan_id="plan-1",
        plan_hash="hash-1",
        subject_agent="operator",
        slot_id="ses0",
    ) == {"step-1", "step-2"}
    assert store.consume(
        receipt.token,
        plan_id="plan-1",
        plan_hash="hash-1",
        subject_agent="operator",
        slot_id="ses0",
    ) == {"step-1", "step-2"}
    with pytest.raises(ApprovalTokenConsumedError):
        store.validate(
            receipt.token,
            plan_id="plan-1",
            plan_hash="hash-1",
            subject_agent="operator",
            slot_id="ses0",
        )


@pytest.mark.parametrize(
    "changed",
    [
        {"plan_id": "plan-2"},
        {"plan_hash": "hash-2"},
        {"subject_agent": "other"},
        {"slot_id": "ses1"},
    ],
)
def test_token_rejects_every_scope_mismatch(changed):
    store = PlanApprovalStore(token_factory=lambda: "token")
    receipt = issue(store)
    scope = {
        "plan_id": "plan-1",
        "plan_hash": "hash-1",
        "subject_agent": "operator",
        "slot_id": "ses0",
    }
    scope.update(changed)
    with pytest.raises(ApprovalTokenInvalidError, match="scope mismatch"):
        store.consume(receipt.token, **scope)


def test_expiry_unknown_token_capacity_and_owner_thread_are_fail_closed():
    clock = Clock()
    store = PlanApprovalStore(
        max_tokens=1, clock=clock, token_factory=lambda: "token"
    )
    receipt = issue(store)
    with pytest.raises(ApprovalTokenError, match="capacity"):
        issue(store)
    clock.value += 31
    with pytest.raises(ApprovalTokenExpiredError):
        store.consume(
            receipt.token,
            plan_id="plan-1",
            plan_hash="hash-1",
            subject_agent="operator",
            slot_id="ses0",
        )
    with pytest.raises(ApprovalTokenInvalidError):
        store.validate(
            "unknown",
            plan_id="plan-1",
            plan_hash="hash-1",
            subject_agent="operator",
            slot_id="ses0",
        )

    errors = []
    thread = threading.Thread(target=lambda: _off_thread(store, errors))
    thread.start()
    thread.join()
    assert errors and isinstance(errors[0], ApprovalTokenError)


def _off_thread(store, errors):
    try:
        store.clear()
    except Exception as exc:
        errors.append(exc)
