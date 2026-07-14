from types import SimpleNamespace

import pytest

from sap_cli.dispatchers.jobs import dispatch_job_command
from sap_cli.jobs import DaemonJobCoordinator, set_job_coordinator
from sap_cli.jobs import PlanJobLedger, set_plan_job_ledger
from sap_cli.planning.runtime import PlanRuntime, set_plan_runtime
from sap_cli.ui_map.jobs import MapJobStore
from sap_cli.ui_map.schema import MapManifest, UiMap, UiState
from sap_cli.ui_map.traversal import TraversalCheckpoint


def ctx(agent="agent-a", slot="ses0"):
    return SimpleNamespace(agent_name=agent, source="test", slot_id=slot)


def test_job_status_is_value_safe_and_owner_slot_scoped(tmp_path):
    coordinator = DaemonJobCoordinator()
    coordinator.submit(
        "plan:exec", "plan_execute", "digest-secret", slot_id="ses0", agent_name="agent-a"
    )
    set_job_coordinator(coordinator)
    try:
        result = dispatch_job_command(
            "job_status", {"job_id": "plan:exec"}, ctx(),
            get_map_jobs=lambda: MapJobStore(tmp_path),
        )
        assert result["jobs"][0]["status"] == "queued"
        assert "digest" not in str(result)
        with pytest.raises(PermissionError):
            dispatch_job_command(
                "job_status", {"job_id": "plan:exec"}, ctx(agent="agent-b"),
                get_map_jobs=lambda: MapJobStore(tmp_path),
            )
    finally:
        set_job_coordinator(None)


def test_plan_unknown_reconcile_releases_both_leases_without_reexecution(tmp_path):
    coordinator = DaemonJobCoordinator()
    runtime = PlanRuntime()
    coordinator.submit(
        "plan:exec", "plan_execute", "digest", slot_id="ses0", agent_name="agent-a"
    )
    coordinator.claim("plan:exec")
    coordinator.mark_outcome_unknown("plan:exec")
    runtime.begin_execution("exec", "digest", slot_id="ses0", agent_name="agent-a")
    runtime.mark_outcome_unknown("exec")
    set_job_coordinator(coordinator)
    set_plan_runtime(runtime)
    try:
        result = dispatch_job_command(
            "job_reconcile",
            {"job_id": "plan:exec", "resolution": "release_after_manual_verification"},
            ctx(),
            get_map_jobs=lambda: MapJobStore(tmp_path),
        )
        assert result["job"]["status"] == "failed"
        assert coordinator.lease_for_slot("ses0") is None
        assert runtime.lease_for_slot("ses0") is None
        assert runtime.get_execution("exec").status == "failed"
        assert runtime.get_execution("exec").error_code == "manually_verified_failed"
    finally:
        set_plan_runtime(None)
        set_job_coordinator(None)


def test_plan_unknown_reconcile_records_verified_applied_as_completed(tmp_path):
    coordinator = DaemonJobCoordinator()
    runtime = PlanRuntime()
    coordinator.submit(
        "plan:exec-applied",
        "plan_execute",
        "digest",
        slot_id="ses0",
        agent_name="agent-a",
    )
    coordinator.claim("plan:exec-applied")
    coordinator.mark_outcome_unknown("plan:exec-applied")
    runtime.begin_execution(
        "exec-applied", "digest", slot_id="ses0", agent_name="agent-a"
    )
    runtime.mark_outcome_unknown("exec-applied")
    set_job_coordinator(coordinator)
    set_plan_runtime(runtime)
    try:
        result = dispatch_job_command(
            "job_reconcile",
            {"job_id": "plan:exec-applied", "resolution": "verified_applied"},
            ctx(),
            get_map_jobs=lambda: MapJobStore(tmp_path),
        )
        assert result["job"]["status"] == "completed"
        assert runtime.get_execution("exec-applied").status == "completed"
        assert coordinator.lease_for_slot("ses0") is None
        assert runtime.lease_for_slot("ses0") is None
    finally:
        set_plan_runtime(None)
        set_job_coordinator(None)


def test_dirty_map_reconcile_clears_latch_and_requeues_without_values(tmp_path):
    store = MapJobStore(tmp_path)
    store.create(
        tcode="ZMAP", map_id="map", job_id="job", owner_agent="agent-a", slot_id="ses0"
    )
    checkpoint = TraversalCheckpoint(
        ui_map=UiMap(
            manifest=MapManifest(map_id="map", tcode="ZMAP"),
            states={"s": UiState(state_id="s", tcode="ZMAP")},
        ),
        in_flight={"dirty": True, "candidate": {"action": "press"}},
    )
    store.save_checkpoint("job", checkpoint)
    store.recover_interrupted()
    coordinator = DaemonJobCoordinator()
    coordinator.submit(
        "map:job", "map_traverse", "digest", slot_id="ses0", agent_name="agent-a"
    )
    coordinator.claim("map:job")
    coordinator.mark_outcome_unknown("map:job")
    set_job_coordinator(coordinator)
    try:
        result = dispatch_job_command(
            "job_reconcile",
            {"job_id": "map:job", "resolution": "verified_not_applied"},
            ctx(),
            get_map_jobs=lambda: store,
        )
        assert result["job"]["status"] == "queued"
        assert result["map_status"] == "queued"
        assert store.get("job").checkpoint.in_flight == {}
        assert "verified_not_applied" in (tmp_path / "job.json").read_text()
    finally:
        set_job_coordinator(None)


def test_restart_recovered_plan_unknown_can_be_reconciled_without_plan_values(tmp_path):
    root = tmp_path / "ledger"
    original = PlanJobLedger(root)
    original.create(
        job_id="plan:restart", execution_id="restart", plan_id="plan",
        plan_hash="hash", request_digest="digest", owner="agent-a", slot="ses0"
    )
    ledger = PlanJobLedger(root)
    recovered = ledger.startup_recovered[0]
    coordinator = DaemonJobCoordinator()
    coordinator.submit(
        recovered.job_id, "plan_execute", recovered.request_digest,
        slot_id=recovered.slot, agent_name=recovered.owner
    )
    coordinator.claim(recovered.job_id)
    coordinator.mark_outcome_unknown(recovered.job_id, error_code=recovered.error_code)
    set_job_coordinator(coordinator)
    set_plan_job_ledger(ledger)
    set_plan_runtime(PlanRuntime())
    try:
        result = dispatch_job_command(
            "job_reconcile",
            {"job_id": "plan:restart", "resolution": "release_after_manual_verification"},
            ctx(),
            get_map_jobs=lambda: MapJobStore(tmp_path / "maps"),
        )
        assert result["job"]["status"] == "failed"
        assert ledger.get("plan:restart").status == "failed"
        assert coordinator.lease_for_slot("ses0") is None
    finally:
        set_plan_runtime(None)
        set_plan_job_ledger(None)
        set_job_coordinator(None)
