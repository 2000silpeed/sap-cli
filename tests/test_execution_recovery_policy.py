from __future__ import annotations

import pytest

from sap_cli.com_wrapper import SapGuiError
from sap_cli.planning.recovery import (
    BackoffPolicy,
    RecoveryBudget,
    RecoveryErrorKind,
    RetryPolicy,
    attempt_limit,
    classify_execution_error,
    safe_error_code,
)
from sap_cli.ui_map.schema import SideEffectClass


class _ComError(Exception):
    def __init__(self, hresult: int, message: str = "sensitive") -> None:
        super().__init__(hresult, message)
        self.hresult = hresult


@pytest.mark.parametrize(
    "hresult",
    [0x80010001, -2147418111, 0x8001010A, -2147417846],
)
def test_classifier_finds_allowlisted_hresult_through_sap_gui_cause_chain(hresult):
    try:
        raise _ComError(hresult, "customer 1000 secret")
    except _ComError as cause:
        wrapped = SapGuiError("field failure: customer 1000 secret")
        wrapped.__cause__ = cause

    assert classify_execution_error(wrapped) is RecoveryErrorKind.TRANSIENT_COM
    assert safe_error_code(wrapped) == "transient_com"
    assert "customer" not in safe_error_code(wrapped)


def test_classifier_does_not_retry_an_unknown_com_hresult():
    error = _ComError(0x80004005)
    assert classify_execution_error(error) is RecoveryErrorKind.PERMANENT
    assert not RetryPolicy.retryable(classify_execution_error(error))


@pytest.mark.parametrize("hresult", [0x80010108, 0x800401FD, 0x800706BA])
def test_disconnected_hresult_has_priority_and_is_not_retryable(hresult):
    error = _ComError(hresult)
    assert classify_execution_error(error) is RecoveryErrorKind.DISCONNECTED
    assert not RetryPolicy.retryable(classify_execution_error(error))


def test_classifier_recognizes_explicit_busy_and_locator_drift_only():
    busy = RuntimeError("SAP GUI session is busy")
    locator = SapGuiError("FindById element not found")
    vague = SapGuiError("the requested operation failed")

    assert classify_execution_error(busy) is RecoveryErrorKind.TRANSIENT_BUSY
    assert classify_execution_error(locator) is RecoveryErrorKind.LOCATOR_DRIFT
    assert classify_execution_error(vague) is RecoveryErrorKind.PERMANENT


def test_business_error_uses_structured_marker_not_business_text():
    structured = RuntimeError("private value")
    structured.status_type = "E"  # type: ignore[attr-defined]

    assert classify_execution_error(structured) is RecoveryErrorKind.BUSINESS
    assert classify_execution_error(RuntimeError("business error")) is RecoveryErrorKind.PERMANENT


def test_attempt_limits_are_capped_by_effect_idempotency_and_request():
    policy = RetryPolicy()

    assert policy.attempt_limit(side_effect=SideEffectClass.READ_ONLY) == 3
    assert policy.attempt_limit(
        side_effect=SideEffectClass.READ_ONLY,
        requested_attempts={"max_attempts": 9},
    ) == 3
    assert policy.attempt_limit(
        side_effect=SideEffectClass.READ_ONLY,
        requested_attempts={"max_attempts": 2},
    ) == 2
    assert policy.attempt_limit(
        side_effect=SideEffectClass.NAVIGATION,
        idempotent=True,
    ) == 2
    assert policy.attempt_limit(
        side_effect=SideEffectClass.REVERSIBLE,
        idempotent=False,
    ) == 1


@pytest.mark.parametrize(
    "effect",
    [SideEffectClass.COMMIT, SideEffectClass.DESTRUCTIVE, SideEffectClass.EXTERNAL],
)
def test_commit_like_effects_are_never_retried(effect):
    assert attempt_limit(
        side_effect=effect,
        idempotent=True,
        requested_attempts=20,
    ) == 1


def test_commit_boundary_or_attempt_latch_forces_one_attempt():
    policy = RetryPolicy()
    assert policy.attempt_limit(
        side_effect=SideEffectClass.READ_ONLY,
        commit_boundary=True,
    ) == 1
    assert policy.attempt_limit(
        side_effect=SideEffectClass.READ_ONLY,
        commit_attempted=True,
    ) == 1


def test_only_transient_kinds_are_retryable():
    retryable = {
        kind for kind in RecoveryErrorKind if RetryPolicy.retryable(kind)
    }
    assert retryable == {
        RecoveryErrorKind.TRANSIENT_BUSY,
        RecoveryErrorKind.TRANSIENT_COM,
    }


def test_backoff_is_capped_and_sleep_is_injected():
    sleeps: list[float] = []
    policy = BackoffPolicy(initial_seconds=0.1, multiplier=3, max_seconds=0.5)

    assert policy.wait(0, sleep=sleeps.append) == 0
    assert policy.wait(1, sleep=sleeps.append) == pytest.approx(0.1)
    assert policy.wait(2, sleep=sleeps.append) == pytest.approx(0.3)
    assert policy.wait(3, sleep=sleeps.append) == pytest.approx(0.5)
    assert sleeps == pytest.approx([0.1, 0.3, 0.5])


def test_recovery_budget_allows_each_expensive_recovery_once():
    budget = RecoveryBudget()

    assert budget.consume_targeted_inspect()
    assert not budget.consume_targeted_inspect()
    assert budget.consume_full_rescan()
    assert not budget.consume_full_rescan()
    assert budget.snapshot() == {
        "targeted_inspect": {"used": 1, "remaining": 0},
        "full_rescan": {"used": 1, "remaining": 0},
    }


def test_recovery_budget_can_disable_a_recovery_path():
    budget = RecoveryBudget(targeted_inspects=0, full_rescans=0)
    assert not budget.consume_targeted_inspect()
    assert not budget.consume_full_rescan()
