from __future__ import annotations

import dataclasses
import threading

import pytest

from sap_cli.jobs.coordinator import (
    DaemonJobCoordinator,
    JobCapacityError,
    JobConflictError,
    JobStateError,
    OwnerThreadError,
)


class Clock:
    def __init__(self) -> None:
        self.value = 10.0

    def __call__(self) -> float:
        self.value += 1.0
        return self.value


def submit(
    jobs: DaemonJobCoordinator,
    job_id: str,
    *,
    kind: str = "plan_execute",
    slot_id: str = "ses0",
):
    return jobs.submit(
        job_id,
        kind,  # type: ignore[arg-type]
        f"digest-{job_id}",
        slot_id=slot_id,
        agent_name="agent-a",
    )


def test_shared_fifo_is_fair_across_plan_and_map_jobs() -> None:
    jobs = DaemonJobCoordinator(clock=Clock())
    submit(jobs, "plan-1", kind="plan_execute", slot_id="ses0")
    submit(jobs, "map-1", kind="map_traverse", slot_id="ses1")
    submit(jobs, "plan-2", kind="plan_execute", slot_id="ses2")

    assert jobs.claim_next().job_id == "plan-1"
    assert jobs.claim_next().job_id == "map-1"
    assert jobs.claim_next().job_id == "plan-2"
    assert jobs.claim_next() is None


def test_busy_slot_does_not_head_of_line_block_another_free_slot() -> None:
    jobs = DaemonJobCoordinator(clock=Clock())
    submit(jobs, "running", slot_id="ses0")
    assert jobs.claim_next().job_id == "running"
    submit(jobs, "same-slot", slot_id="ses0")
    submit(jobs, "free-slot", slot_id="ses1")

    assert jobs.claim_next().job_id == "free-slot"
    assert jobs.claim_next() is None

    jobs.complete("running")
    assert jobs.claim_next().job_id == "same-slot"


def test_exact_claim_never_overtakes_earlier_job_on_same_slot() -> None:
    jobs = DaemonJobCoordinator(clock=Clock())
    submit(jobs, "map", kind="map_traverse", slot_id="ses0")
    submit(jobs, "plan", slot_id="ses0")
    submit(jobs, "other", slot_id="ses1")

    assert jobs.claim("plan") is None
    assert jobs.claim("other").job_id == "other"
    assert jobs.claim("map").job_id == "map"


def test_needs_user_input_keeps_lease_and_requires_explicit_resume() -> None:
    jobs = DaemonJobCoordinator(clock=Clock())
    submit(jobs, "map", kind="map_traverse", slot_id="ses0")
    submit(jobs, "waiting-plan", slot_id="ses0")
    assert jobs.claim_next().job_id == "map"

    paused = jobs.needs_user_input("map")
    assert paused.status == "needs_user_input"
    assert jobs.lease_for_slot("ses0").job_id == "map"
    assert jobs.claim_next() is None

    resumed = jobs.resume("map")
    assert resumed.status == "queued"
    assert jobs.claim_next().job_id == "map"
    jobs.complete("map")
    assert jobs.claim_next().job_id == "waiting-plan"


def test_budget_pause_releases_slot_and_can_be_explicitly_resumed() -> None:
    jobs = DaemonJobCoordinator(clock=Clock())
    submit(jobs, "map", kind="map_traverse", slot_id="ses0")
    assert jobs.claim("map").status == "running"

    paused = jobs.pause("map")

    assert paused.status == "paused"
    assert jobs.lease_for_slot("ses0") is None
    resumed = jobs.resume("map")
    assert resumed.status == "queued"
    assert jobs.claim("map").status == "running"


def test_outcome_unknown_quarantines_slot_until_explicit_reconcile() -> None:
    jobs = DaemonJobCoordinator(clock=Clock())
    submit(jobs, "uncertain", slot_id="ses0")
    submit(jobs, "blocked", slot_id="ses0")
    jobs.claim_next()

    unknown = jobs.mark_outcome_unknown(
        "uncertain", error_code="commit_outcome_unknown"
    )
    assert unknown.status == "outcome_unknown"
    lease = jobs.lease_for_slot("ses0")
    assert lease.quarantined is True
    assert jobs.claim_next() is None

    # Same job id is a status replay, never an implicit retry.
    replay = jobs.submit(
        "uncertain",
        "plan_execute",
        "digest-uncertain",
        slot_id="ses0",
        agent_name="agent-a",
    )
    assert replay.status == "outcome_unknown"
    assert replay.replayed is True
    assert jobs.queued_count == 1

    reconciled = jobs.reconcile("uncertain", "completed")
    assert reconciled.status == "completed"
    assert jobs.lease_for_slot("ses0") is None
    assert jobs.claim_next().job_id == "blocked"


def test_reconcile_failure_uses_only_safe_error_codes() -> None:
    jobs = DaemonJobCoordinator(clock=Clock())
    submit(jobs, "job")
    jobs.claim_next()
    jobs.mark_outcome_unknown("job", error_code="password=secret")
    assert jobs.get("job").error_code == "outcome_unknown"

    failed = jobs.reconcile(
        "job", "failed", error_code="Exception: password=secret"
    )
    assert failed.error_code == "reconciled_failure"


def test_snapshots_are_immutable_and_cannot_contain_inputs_or_results() -> None:
    jobs = DaemonJobCoordinator(clock=Clock())
    snapshot = jobs.submit(
        "job",
        "plan_execute",
        "digest-containing-no-values",
        slot_id="ses0",
        agent_name="agent-a",
    )
    public = dataclasses.asdict(snapshot)

    assert "request_digest" not in public
    assert "payload" not in public
    assert "result" not in public
    assert "checkpoint" not in public
    with pytest.raises(dataclasses.FrozenInstanceError):
        snapshot.status = "running"  # type: ignore[misc]


def test_capacity_is_bounded_and_reclamation_is_explicit() -> None:
    jobs = DaemonJobCoordinator(max_jobs=2, max_queued=2, clock=Clock())
    submit(jobs, "one")
    submit(jobs, "two", slot_id="ses1")
    with pytest.raises(JobCapacityError, match="record"):
        submit(jobs, "three", slot_id="ses2")

    jobs.claim_next()
    jobs.complete("one")
    with pytest.raises(JobCapacityError):
        submit(jobs, "three", slot_id="ses2")
    assert jobs.forget("one") is True
    assert submit(jobs, "three", slot_id="ses2").status == "queued"


def test_queue_capacity_does_not_count_running_or_paused_jobs() -> None:
    jobs = DaemonJobCoordinator(max_jobs=3, max_queued=1, clock=Clock())
    submit(jobs, "map", kind="map_traverse")
    jobs.claim_next()
    jobs.needs_user_input("map")
    submit(jobs, "queued", slot_id="ses1")
    with pytest.raises(JobCapacityError, match="queue"):
        submit(jobs, "too-many", slot_id="ses2")


def test_duplicate_submission_conflict_and_terminal_no_rerun() -> None:
    jobs = DaemonJobCoordinator(clock=Clock())
    submit(jobs, "job")
    jobs.claim_next()
    jobs.complete("job")

    replay = submit(jobs, "job")
    assert replay.status == "completed"
    assert replay.replayed is True
    assert jobs.claim_next() is None

    with pytest.raises(JobConflictError):
        jobs.submit(
            "job",
            "map_traverse",
            "different",
            slot_id="ses0",
            agent_name="agent-a",
        )


def test_invalid_state_transitions_cannot_release_or_reuse_slot() -> None:
    jobs = DaemonJobCoordinator(clock=Clock())
    submit(jobs, "job")
    with pytest.raises(JobStateError):
        jobs.complete("job")
    jobs.claim_next()
    assert jobs.cancel("job").status == "cancelled"
    submit(jobs, "unknown")
    jobs.claim_next()
    jobs.mark_outcome_unknown("unknown")
    with pytest.raises(JobStateError):
        jobs.forget("unknown")
    with pytest.raises(ValueError):
        jobs.reconcile("unknown", "running")  # type: ignore[arg-type]


def test_cancel_queued_and_paused_jobs() -> None:
    jobs = DaemonJobCoordinator(clock=Clock())
    submit(jobs, "queued")
    assert jobs.cancel("queued").status == "cancelled"
    assert jobs.claim_next() is None

    submit(jobs, "paused", kind="map_traverse")
    jobs.claim_next()
    jobs.needs_user_input("paused")
    assert jobs.cancel("paused").status == "cancelled"
    assert jobs.lease_for_slot("ses0") is None

    submit(jobs, "resumed", kind="map_traverse")
    jobs.claim_next()
    jobs.needs_user_input("resumed")
    jobs.resume("resumed")
    assert jobs.lease_for_slot("ses0").status == "queued"
    assert jobs.cancel("resumed").status == "cancelled"
    assert jobs.lease_for_slot("ses0") is None


def test_terminal_history_can_be_forgotten_after_slot_is_reused() -> None:
    jobs = DaemonJobCoordinator(clock=Clock())
    submit(jobs, "first")
    submit(jobs, "second")
    jobs.claim_next()
    jobs.complete("first")
    assert jobs.claim_next().job_id == "second"

    assert jobs.forget("first") is True
    assert jobs.lease_for_slot("ses0").job_id == "second"


def test_all_public_state_access_is_owner_thread_confined() -> None:
    jobs = DaemonJobCoordinator(clock=Clock())
    failures: list[BaseException] = []

    def access_from_worker() -> None:
        for operation in (
            lambda: submit(jobs, "job"),
            jobs.claim_next,
            jobs.snapshots,
            lambda: jobs.lease_for_slot("ses0"),
        ):
            try:
                operation()
            except BaseException as exc:  # collect thread exception for assertion
                failures.append(exc)

    thread = threading.Thread(target=access_from_worker)
    thread.start()
    thread.join()

    assert len(failures) == 4
    assert all(isinstance(exc, OwnerThreadError) for exc in failures)
    assert len(jobs) == 0
