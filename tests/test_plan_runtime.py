from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from sap_cli.planning.runtime import (
    ExecutionConflictError,
    PlanMapDependencyError,
    OwnerThreadError,
    PlanNotFoundError,
    PlanRegistry,
    PlanRuntime,
    SlotLeaseError,
    get_plan_runtime,
    set_plan_runtime,
)
from sap_cli.planning.schema import ExecutionPlan, PlanStep, PlanVariable


class Clock:
    def __init__(self) -> None:
        self.value = 100.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def plan(
    name: str,
    *,
    secret: str = "",
    map_id: str = "map",
    system_id: str = "DEV",
    client: str = "100",
    language: str = "KO",
    map_version: int = 1,
    map_fingerprint: str = "fingerprint-1",
) -> ExecutionPlan:
    variables = (
        {"password": PlanVariable(value=secret, sensitive=True)} if secret else {}
    )
    return ExecutionPlan(
        plan_id=name,
        original_request=f"request-{name}",
        tcode="MM03",
        map_id=map_id,
        map_scope={
            "system_id": system_id,
            "client": client,
            "language": language,
        },
        map_version=map_version,
        map_fingerprint=map_fingerprint,
        start_state_id="start",
        goal_state_id="goal",
        variables=variables,
        steps=[
            PlanStep(
                step_id="read",
                order=1,
                command="get",
                tool="sap_get_field",
                params={"element_id": "wnd[0]/usr/txtFIELD"},
            )
        ],
    )


def test_registry_stores_plan_object_and_exposes_value_free_summary() -> None:
    secret = "TOP-SECRET-4711"
    registry = PlanRegistry(max_size=2, ttl_seconds=60)
    item = plan("p1", secret=secret)

    reference = registry.register(item)

    assert registry.resolve(reference.plan_id, reference.plan_hash) is item
    summary = registry.summaries()
    assert summary[0]["plan_id"] == "p1"
    assert "plan" not in summary[0]
    assert "variables" not in summary[0]
    assert secret not in str(summary)


def test_registry_ttl_expires_without_refreshing_on_read() -> None:
    clock = Clock()
    registry = PlanRegistry(max_size=2, ttl_seconds=10, clock=clock)
    item = plan("ttl")
    registry.register(item)
    clock.advance(9)
    assert registry.resolve(item.plan_id, item.plan_hash) is item
    clock.advance(1)

    with pytest.raises(PlanNotFoundError):
        registry.resolve(item.plan_id, item.plan_hash)


def test_registry_size_limit_is_lru() -> None:
    registry = PlanRegistry(max_size=2, ttl_seconds=60)
    first, second, third = plan("one"), plan("two"), plan("three")
    registry.register(first)
    registry.register(second)
    registry.resolve(first.plan_id, first.plan_hash)
    registry.register(third)

    with pytest.raises(PlanNotFoundError):
        registry.resolve(second.plan_id, second.plan_hash)
    assert registry.resolve(first.plan_id, first.plan_hash) is first
    assert registry.resolve(third.plan_id, third.plan_hash) is third


def test_registry_invalidates_only_the_requested_map_scope() -> None:
    registry = PlanRegistry(max_size=4, ttl_seconds=60)
    old = plan("old", map_version=1, map_fingerprint="fp-old")
    current = plan("current", map_version=2, map_fingerprint="fp-current")
    other = plan(
        "other",
        map_id="other-map",
        system_id="QAS",
        map_version=1,
        map_fingerprint="fp-other",
    )
    for item in (old, current, other):
        registry.register(item)

    removed = registry.invalidate_map_scope(
        map_id="map",
        tcode="mm03",
        system_id="dev",
        client="100",
        language="ko",
        head_version=2,
        head_fingerprint="fp-current",
    )

    assert removed == 1
    with pytest.raises(PlanNotFoundError):
        registry.resolve(old.plan_id, old.plan_hash)
    assert registry.resolve(current.plan_id, current.plan_hash) is current
    assert registry.resolve(other.plan_id, other.plan_hash) is other


@pytest.mark.parametrize(
    ("head_version", "head_fingerprint"),
    [(4, "fp-3"), (3, "fp-replaced")],
)
def test_stale_map_head_rejects_and_evicts_plan(
    head_version: int, head_fingerprint: str
) -> None:
    registry = PlanRegistry(max_size=2, ttl_seconds=60)
    item = plan("stale", map_version=3, map_fingerprint="fp-3")
    registry.register(item)
    head = {
        "version": head_version,
        "scope": {
            "map_id": "map",
            "tcode": "MM03",
            "system_id": "DEV",
            "client": "100",
            "language": "KO",
        },
    }

    with pytest.raises(PlanMapDependencyError, match="stale"):
        registry.validate_map_head(
            item.plan_id, item.plan_hash, head, fingerprint=head_fingerprint
        )
    with pytest.raises(PlanNotFoundError):
        registry.resolve(item.plan_id, item.plan_hash)


def test_exact_map_head_validation_keeps_plan_and_dependency_is_value_free() -> None:
    runtime = PlanRuntime()
    item = plan("current", secret="NEVER-IN-SUMMARY", map_version=7, map_fingerprint="fp-7")
    runtime.register_plan(item)
    head = {
        "version": 7,
        "scope": {
            "map_id": "map",
            "tcode": "mm03",
            "system_id": "dev",
            "client": "100",
            "language": "ko",
        },
    }

    assert runtime.validate_plan_map_head(
        item.plan_id, item.plan_hash, head, fingerprint="fp-7"
    ) is item
    summary = runtime.registry.summaries()[0]
    assert summary["map_dependency"] == {
        "map_id": "map",
        "tcode": "MM03",
        "system_id": "DEV",
        "client": "100",
        "language": "KO",
        "version": 7,
        "fingerprint": "fp-7",
    }
    assert "NEVER-IN-SUMMARY" not in str(summary)


def test_execution_idempotency_returns_running_and_completed_result() -> None:
    runtime = PlanRuntime()
    first = runtime.begin_execution(
        "exec-1", "digest-a", slot_id="ses0", agent_name="agent-a"
    )
    duplicate_running = runtime.begin_execution(
        "exec-1", "digest-a", slot_id="ses0", agent_name="agent-a"
    )
    completed = runtime.complete_execution(
        "exec-1", {"status": "ok"}, slot_id="ses0", agent_name="agent-a"
    )
    duplicate_completed = runtime.begin_execution(
        "exec-1", "digest-a", slot_id="ses0", agent_name="agent-a"
    )

    assert first.status == duplicate_running.status == "running"
    assert not first.replayed and duplicate_running.replayed
    assert completed.status == "completed"
    assert duplicate_completed.status == "completed"
    assert duplicate_completed.result == {"status": "ok"}
    assert duplicate_completed.replayed
    assert runtime.lease_for_slot("ses0") is None


def test_definitive_failure_is_replayable_and_releases_slot_lease() -> None:
    runtime = PlanRuntime()
    runtime.begin_execution(
        "failed-exec", "digest", slot_id="ses0", agent_name="agent"
    )

    failed = runtime.fail_execution(
        "failed-exec",
        {"status": "blocked"},
        error_code="plan_blocked",
        slot_id="ses0",
        agent_name="agent",
    )
    replay = runtime.begin_execution(
        "failed-exec", "digest", slot_id="ses0", agent_name="agent"
    )

    assert failed.status == "failed"
    assert failed.error_code == "plan_blocked"
    assert replay.status == "failed"
    assert replay.replayed is True
    assert replay.result == {"status": "blocked"}
    assert runtime.lease_for_slot("ses0") is None


@pytest.mark.parametrize(
    ("digest", "slot_id", "agent_name"),
    [
        ("different", "ses0", "agent-a"),
        ("digest-a", "ses1", "agent-a"),
        ("digest-a", "ses0", "agent-b"),
    ],
)
def test_execution_id_reuse_with_different_payload_or_owner_conflicts(
    digest: str, slot_id: str, agent_name: str
) -> None:
    runtime = PlanRuntime()
    runtime.begin_execution(
        "exec-1", "digest-a", slot_id="ses0", agent_name="agent-a"
    )

    with pytest.raises(ExecutionConflictError):
        runtime.begin_execution(
            "exec-1", digest, slot_id=slot_id, agent_name=agent_name
        )


def test_slot_lease_blocks_another_execution_and_checks_owner() -> None:
    runtime = PlanRuntime()
    runtime.begin_execution(
        "exec-1", "digest-a", slot_id="ses0", agent_name="agent-a"
    )

    with pytest.raises(SlotLeaseError):
        runtime.begin_execution(
            "exec-2", "digest-b", slot_id="ses0", agent_name="agent-b"
        )
    with pytest.raises(SlotLeaseError, match="another agent"):
        runtime.complete_execution(
            "exec-1", {}, slot_id="ses0", agent_name="agent-b"
        )


def test_outcome_unknown_keeps_slot_blocked_until_reconciled() -> None:
    runtime = PlanRuntime()
    runtime.begin_execution(
        "exec-1", "digest-a", slot_id="ses0", agent_name="agent-a"
    )
    unknown = runtime.mark_outcome_unknown(
        "exec-1", error_code="socket_lost", slot_id="ses0", agent_name="agent-a"
    )

    assert unknown.status == "outcome_unknown"
    assert runtime.lease_for_slot("ses0").execution_id == "exec-1"
    with pytest.raises(SlotLeaseError):
        runtime.begin_execution(
            "exec-2", "digest-b", slot_id="ses0", agent_name="agent-a"
        )

    assert runtime.release_execution_lease(
        "exec-1", slot_id="ses0", agent_name="agent-a"
    )
    assert runtime.lease_for_slot("ses0") is None
    assert runtime.begin_execution(
        "exec-2", "digest-b", slot_id="ses0", agent_name="agent-a"
    ).status == "running"


def test_runtime_rejects_access_from_non_owner_thread() -> None:
    runtime = PlanRuntime()

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(runtime.register_plan, plan("thread"))
        with pytest.raises(OwnerThreadError, match="owner thread"):
            future.result()


def test_terminal_execution_ttl_removes_result_but_not_active_unknown_lease() -> None:
    clock = Clock()
    runtime = PlanRuntime(execution_ttl_seconds=10, clock=clock)
    runtime.begin_execution(
        "done", "digest-done", slot_id="ses0", agent_name="agent-a"
    )
    runtime.complete_execution("done", {"ok": True})
    runtime.begin_execution(
        "unknown", "digest-unknown", slot_id="ses1", agent_name="agent-a"
    )
    runtime.mark_outcome_unknown("unknown")
    clock.advance(11)

    with pytest.raises(KeyError):
        runtime.get_execution("done")
    assert runtime.get_execution("unknown").status == "outcome_unknown"


def test_process_global_runtime_binding() -> None:
    runtime = PlanRuntime()
    set_plan_runtime(runtime)
    try:
        assert get_plan_runtime() is runtime
    finally:
        set_plan_runtime(None)

    with pytest.raises(RuntimeError, match="persistent daemon"):
        get_plan_runtime()
