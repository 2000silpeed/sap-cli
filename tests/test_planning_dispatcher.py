from sap_cli.auth.policy import (
    AgentPolicy,
    DangerousTransaction,
    Policy,
    Relation,
    RelationBinding,
)
from sap_cli.dispatchers.planning import dispatch_planning_command
from sap_cli.dispatcher import AuthorizationDecision, DispatchContext, dispatch_command
from sap_cli.planning.runtime import PlanRuntime, set_plan_runtime
from types import SimpleNamespace
import pytest
from sap_cli.ui_map.schema import (
    MapManifest,
    SideEffectClass,
    Transition,
    UiComponent,
    UiMap,
    UiState,
)
from sap_cli.ui_map.store import UiMapStore


def store_with_map(tmp_path):
    store = UiMapStore(tmp_path)
    store.save(
        UiMap(
            manifest=MapManifest(map_id="map", tcode="MM03"),
            states={
                "start": UiState(
                    state_id="start",
                    tcode="MM03",
                    components=[
                        UiComponent(
                            logical_id="material",
                            sap_id="wnd[0]/usr/ctxtMATNR",
                            type="GuiCTextField",
                            role="field",
                            name="material",
                            changeable=True,
                        ),
                        UiComponent(
                            logical_id="description",
                            sap_id="wnd[0]/usr/txtMAKTX",
                            type="GuiTextField",
                            role="output",
                            name="description",
                            tooltip="기본 데이터",
                        ),
                    ],
                )
            },
        )
    )
    return store


def test_plan_preview_dispatcher_returns_redacted_hashed_plan(tmp_path):
    store = store_with_map(tmp_path)
    result = dispatch_planning_command(
        "plan_preview",
        {
            "request": "tcode MM03 기본 데이터 조회 password='TOP-SECRET'",
            "map_id": "map",
            "goal_query": "기본 데이터",
            "values": {"material": "MAT-1"},
        },
        get_map_store=lambda: store,
        get_recipe_store=lambda: type("Recipes", (), {"query_for_ai": lambda *a, **k: []})(),
    )

    assert result["plan"]["plan_hash"]
    assert result["plan"]["scan_policy"] == "fast"
    assert "TOP-SECRET" not in str(result)
    assert result["intent"]["values"]["password"] == "<redacted>"
    assert result["preflight"]["resolved_steps"][0]["command"] == "run"
    material_step = next(
        step
        for step in result["preflight"]["resolved_steps"]
        if step["command"] == "ui_set"
    )
    assert material_step["params"]["value"] == "MAT-1"


def test_missing_map_still_redacts_sensitive_intent(tmp_path):
    result = dispatch_planning_command(
        "plan_preview",
        {"request": "tcode SU01 조회 password='TOP-SECRET'"},
        get_map_store=lambda: UiMapStore(tmp_path),
        get_recipe_store=lambda: type("Recipes", (), {"query_for_ai": lambda *a, **k: []})(),
    )
    assert result["plan"] is None
    assert result["validation"]["errors"] == ["map_missing"]
    assert result["validation"]["questions"][0]["code"] == "map_missing"
    assert "TOP-SECRET" not in str(result)


def test_plan_preview_preflights_resolved_tcode_with_central_policy(tmp_path):
    policy = Policy(
        active_agent="planner",
        agents={
            "planner": AgentPolicy(
                relations=[RelationBinding(resource="tx:*", relation=Relation.OPERATOR)]
            )
        },
        dangerous_transactions=[
            DangerousTransaction(tcode="MM03", reason="승인 대상 조회")
        ],
    )
    result = dispatch_planning_command(
        "plan_preview",
        {
            "request": "tcode MM03 기본 데이터 조회",
            "map_id": "map",
            "goal_query": "기본 데이터",
            "values": {"material": "MAT-1"},
        },
        get_map_store=lambda: store_with_map(tmp_path),
        get_recipe_store=lambda: type(
            "Recipes", (), {"query_for_ai": lambda *a, **k: []}
        )(),
        policy=policy,
        agent_name="planner",
    )

    run = result["preflight"]["resolved_steps"][0]
    assert run["authorization_resource"] == "tx:MM03"
    assert run["authorization_requires_approval"] is True
    assert run["authorization_risk"]["preflight_code"] == "dangerous_transaction"
    assert result["validation"]["requires_approval"] is True


def test_preview_exposes_expected_transition_risk_and_approval_contract(tmp_path):
    store = UiMapStore(tmp_path)
    store.save(
        UiMap(
            manifest=MapManifest(map_id="path-map", tcode="MM03"),
            states={
                "start": UiState(
                    state_id="start",
                    tcode="MM03",
                    components=[
                        UiComponent(
                            logical_id="basic-tab",
                            sap_id="wnd[0]/usr/tabs/tabpBASIC",
                            type="GuiTab",
                            text="기본 탭",
                        )
                    ],
                ),
                "detail": UiState(
                    state_id="detail",
                    tcode="MM03",
                    components=[
                        UiComponent(
                            logical_id="detail-output",
                            sap_id="wnd[0]/usr/txtDETAIL",
                            type="GuiTextField",
                            text="상세 설명 결과",
                        )
                    ],
                ),
            },
            transitions=[
                Transition(
                    transition_id="open-basic",
                    from_state_id="start",
                    to_state_id="detail",
                    action="select_tab",
                    target_logical_id="basic-tab",
                    target_locator={"sap_id": "wnd[0]/usr/tabs/tabpBASIC"},
                    side_effect=SideEffectClass.REVERSIBLE,
                    reversible=True,
                )
            ],
        )
    )
    result = dispatch_planning_command(
        "plan_preview",
        {
            "request": "tcode MM03 상세 결과 조회",
            "map_id": "path-map",
            "goal_query": "detail-output",
        },
        get_map_store=lambda: store,
        get_recipe_store=lambda: type(
            "Recipes", (), {"query_for_ai": lambda *a, **k: []}
        )(),
    )

    transition = result["preflight"]["resolved_steps"][-1]
    assert transition["command"] == "ui_select"
    assert transition["map_transition_id"] == "open-basic"
    assert transition["expected_from_fingerprint"]
    assert transition["expected_to_fingerprint"]
    assert transition["risk"] == "low"
    assert transition["approval_required"] is False


def test_current_state_plan_still_preflights_transaction_authorization(tmp_path):
    store = store_with_map(tmp_path)
    policy = Policy(
        active_agent="planner",
        agents={
            "planner": AgentPolicy(
                relations=[RelationBinding(resource="tx:*", relation=Relation.OPERATOR)]
            )
        },
        dangerous_transactions=[DangerousTransaction(tcode="MM03", reason="민감 조회")],
    )
    result = dispatch_planning_command(
        "plan_preview",
        {
            "request": "tcode MM03 기본 데이터 조회",
            "map_id": "map",
            "current_state_id": "start",
            "goal_query": "기본 데이터",
            "values": {"material": "MAT-1"},
        },
        get_map_store=lambda: store,
        get_recipe_store=lambda: type(
            "Recipes", (), {"query_for_ai": lambda *a, **k: []}
        )(),
        policy=policy,
        agent_name="planner",
    )

    assert all(step["command"] != "run" for step in result["plan"]["steps"])
    assert result["transaction_authorization"]["approval_required"] is True
    assert "authorize-tcode" in result["validation"]["required_approvals"]


def test_outer_plan_preview_audit_never_receives_sensitive_request_value(monkeypatch):
    import sap_cli.dispatcher as dispatcher

    seen = []
    monkeypatch.setattr(
        dispatcher,
        "_evaluate_authorization",
        lambda *args: (
            AuthorizationDecision(
                agent="test",
                permission="read",
                resource="tool:sap_plan_preview",
                allowed=True,
            ),
            None,
        ),
    )
    monkeypatch.setattr(
        dispatcher,
        "_audit_authorization",
        lambda policy, authz, tool, arguments: seen.append(arguments),
    )
    monkeypatch.setattr(dispatcher, "_execute_command", lambda *args: {"ok": True})

    result = dispatch_command(
        "plan_preview",
        {
            "request": "tcode SU01 조회 password='TOP-SECRET'",
            "values": {"api_key": "KEY-SECRET"},
        },
        DispatchContext(enforce_auth=False),
    )

    assert result == {"ok": True}
    assert "TOP-SECRET" not in str(seen)
    assert "KEY-SECRET" not in str(seen)


def test_plan_approval_commands_are_omitted_from_transcript_and_session_capture():
    from sap_cli.dispatcher import TRANSCRIPT_OMIT_COMMANDS
    from sap_cli.dispatchers.session import SESSION_SKIP_COMMANDS

    assert {"plan_approve", "plan_execute"} <= TRANSCRIPT_OMIT_COMMANDS
    assert {"plan_approve", "plan_execute"} <= SESSION_SKIP_COMMANDS


def test_daemon_runtime_keeps_full_plan_and_returns_only_reference(tmp_path):
    runtime = PlanRuntime()
    set_plan_runtime(runtime)
    try:
        result = dispatch_planning_command(
            "plan_preview",
            {
                "request": "tcode MM03 기본 데이터 조회",
                "map_id": "map",
                "goal_query": "기본 데이터",
                "values": {"material": "MAT-1"},
            },
            get_map_store=lambda: store_with_map(tmp_path),
            get_recipe_store=lambda: type(
                "Recipes", (), {"query_for_ai": lambda *a, **k: []}
            )(),
        )
        reference = result["plan_ref"]
        cached = runtime.get_plan(reference["plan_id"], reference["plan_hash"])
        assert cached.plan_hash == result["plan"]["plan_hash"]
        assert reference["daemon_memory_only"] is True
    finally:
        set_plan_runtime(None)


def test_invalid_preview_never_receives_a_daemon_execution_reference(tmp_path):
    runtime = PlanRuntime()
    set_plan_runtime(runtime)
    try:
        result = dispatch_planning_command(
            "plan_preview",
            {
                "request": "tcode MM03 기본 데이터 생성",
                "map_id": "map",
                "goal_query": "기본 데이터",
            },
            get_map_store=lambda: store_with_map(tmp_path),
            get_recipe_store=lambda: type(
                "Recipes", (), {"query_for_ai": lambda *a, **k: []}
            )(),
        )
        assert result["validation"]["valid"] is False
        assert result["validation"]["errors"] == ["operation_action_missing:create"]
        assert result["plan_ref"] is None
    finally:
        set_plan_runtime(None)


def test_plan_execute_is_idempotent_and_cheap_path_never_scans(tmp_path):
    from sap_cli.jobs import PlanJobLedger, set_plan_job_ledger
    class Dump:
        def __init__(self, value):
            self.value = value

        def model_dump(self, mode="json"):
            return self.value

    class Sap:
        def __init__(self):
            self.actions = []
            self.get_screen_calls = 0

        def wait_until_idle(self):
            return True

        def get_session_info(self):
            return Dump({"transaction": "MM03", "screen_number": 0})

        def get_status(self):
            return Dump({"type": "S", "message": "ok"})

        def has_modal(self):
            return False

        def run_transaction(self, tcode):
            self.actions.append(("run", tcode))

        def perform_ui_action(self, element_id, action, value=None, **kwargs):
            self.actions.append((action, element_id, value))
            return {"ok": True}

        def get_screen(self):
            self.get_screen_calls += 1
            return Dump({"screen_number": 0})

        def inspect_element(self, element_id):
            return {"id": element_id, "visible": True}

    runtime = PlanRuntime()
    ledger = PlanJobLedger(tmp_path / "plan-jobs")
    store = store_with_map(tmp_path)
    sap = Sap()
    set_plan_runtime(runtime)
    set_plan_job_ledger(ledger)
    try:
        preview = dispatch_planning_command(
            "plan_preview",
            {
                "request": "tcode MM03 기본 데이터 조회",
                "map_id": "map",
                "goal_query": "기본 데이터",
                "values": {"material": "MAT-1"},
            },
            get_map_store=lambda: store,
            get_recipe_store=lambda: type(
                "Recipes", (), {"query_for_ai": lambda *a, **k: []}
            )(),
        )
        args = {
            **preview["plan_ref"],
            "execution_id": "exec-1",
            "verify_mode": "cheap",
        }
        args.pop("expires_in_seconds")
        args.pop("daemon_memory_only")
        ctx = SimpleNamespace(slot_id="ses0", agent_name="agent-a")
        try:
            dispatch_planning_command(
                "plan_execute",
                {
                    **args,
                    "approval_grants": [
                        {
                            "step_id": "input-material",
                            "plan_hash": "different",
                            "approved": True,
                        }
                    ],
                },
                get_map_store=lambda: store,
                policy=None,
                agent_name="agent-a",
                ctx=ctx,
                get_sap=lambda slot: sap,
            )
        except ValueError as exc:
            assert "approval_grants are forbidden" in str(exc)
        else:
            raise AssertionError("mismatched approval grant was accepted")
        assert sap.actions == []
        first = dispatch_planning_command(
            "plan_execute",
            args,
            get_map_store=lambda: store,
            policy=None,
            agent_name="agent-a",
            ctx=ctx,
            get_sap=lambda slot: sap,
        )
        action_count = len(sap.actions)
        replay = dispatch_planning_command(
            "plan_execute",
            args,
            get_map_store=lambda: store,
            policy=None,
            agent_name="agent-a",
            ctx=ctx,
            get_sap=lambda slot: sap,
        )

        assert first["status"] == "completed"
        assert first["result"]["status"] == "success"
        assert sap.get_screen_calls == 0
        assert replay["replayed"] is True
        assert len(sap.actions) == action_count
        assert ledger.get("plan:exec-1").status == "completed"
        assert "MAT-1" not in next((tmp_path / "plan-jobs").glob("*.json")).read_text()
    finally:
        set_plan_job_ledger(None)
        set_plan_runtime(None)


@pytest.mark.parametrize("execution_status", ["blocked", "failed"])
def test_definitive_plan_failure_is_not_recorded_as_completed(
    tmp_path, monkeypatch, execution_status
):
    import sap_cli.dispatchers.planning as planning_dispatcher
    from sap_cli.jobs import (
        DaemonJobCoordinator,
        PlanJobLedger,
        set_job_coordinator,
        set_plan_job_ledger,
    )
    from sap_cli.planning.executor import PlanExecutionResult

    runtime = PlanRuntime()
    coordinator = DaemonJobCoordinator()
    ledger = PlanJobLedger(tmp_path / "failure-ledger")
    store = store_with_map(tmp_path / "maps")
    set_plan_runtime(runtime)
    set_job_coordinator(coordinator)
    set_plan_job_ledger(ledger)
    try:
        preview = dispatch_planning_command(
            "plan_preview",
            {
                "request": "tcode MM03 기본 데이터 조회",
                "map_id": "map",
                "goal_query": "기본 데이터",
                "values": {"material": "MAT-1"},
            },
            get_map_store=lambda: store,
            get_recipe_store=lambda: type(
                "Recipes", (), {"query_for_ai": lambda *a, **k: []}
            )(),
        )
        reference = dict(preview["plan_ref"])
        reference.pop("expires_in_seconds")
        reference.pop("daemon_memory_only")
        plan = runtime.get_plan(reference["plan_id"], reference["plan_hash"])
        monkeypatch.setattr(
            planning_dispatcher.PlanExecutor,
            "execute",
            lambda self, *args, **kwargs: PlanExecutionResult(
                status=execution_status,
                plan_id=plan.plan_id,
                plan_hash=plan.plan_hash,
                map_id=plan.map_id,
                map_version=plan.map_version,
                errors=["safe failure"],
                commit_attempted=False,
            ),
        )

        result = dispatch_planning_command(
            "plan_execute",
            {**reference, "execution_id": f"exec-{execution_status}"},
            get_map_store=lambda: store,
            agent_name="agent-a",
            ctx=SimpleNamespace(slot_id="ses0", agent_name="agent-a"),
            get_sap=lambda _slot: (_ for _ in ()).throw(AssertionError("SAP access")),
        )

        assert result["status"] == "failed"
        assert result["result"]["status"] == execution_status
        execution = runtime.get_execution(f"exec-{execution_status}")
        assert execution.status == "failed"
        assert coordinator.get(f"plan:exec-{execution_status}").status == "failed"
        assert ledger.get(f"plan:exec-{execution_status}").status == "failed"
        assert runtime.lease_for_slot("ses0") is None
        assert coordinator.lease_for_slot("ses0") is None
    finally:
        set_plan_job_ledger(None)
        set_job_coordinator(None)
        set_plan_runtime(None)


def test_coordinator_rejection_never_leaves_running_ledger_record(tmp_path):
    from sap_cli.jobs import (
        DaemonJobCoordinator,
        JobCapacityError,
        PlanJobLedger,
        set_job_coordinator,
        set_plan_job_ledger,
    )

    runtime = PlanRuntime()
    coordinator = DaemonJobCoordinator(max_jobs=1, max_queued=1)
    coordinator.submit(
        "other-job",
        "map_traverse",
        "other-digest",
        slot_id="ses9",
        agent_name="agent-a",
    )
    ledger = PlanJobLedger(tmp_path / "coordination-ledger")
    store = store_with_map(tmp_path / "maps")
    set_plan_runtime(runtime)
    set_job_coordinator(coordinator)
    set_plan_job_ledger(ledger)
    try:
        preview = dispatch_planning_command(
            "plan_preview",
            {
                "request": "tcode MM03 기본 데이터 조회",
                "map_id": "map",
                "goal_query": "기본 데이터",
                "values": {"material": "MAT-1"},
            },
            get_map_store=lambda: store,
            get_recipe_store=lambda: type(
                "Recipes", (), {"query_for_ai": lambda *a, **k: []}
            )(),
        )
        reference = dict(preview["plan_ref"])
        reference.pop("expires_in_seconds")
        reference.pop("daemon_memory_only")

        with pytest.raises(JobCapacityError):
            dispatch_planning_command(
                "plan_execute",
                {**reference, "execution_id": "coord-rejected"},
                get_map_store=lambda: store,
                agent_name="agent-a",
                ctx=SimpleNamespace(slot_id="ses0", agent_name="agent-a"),
                get_sap=lambda _slot: (_ for _ in ()).throw(AssertionError("SAP access")),
            )

        assert ledger.get("plan:coord-rejected").status == "failed"
        assert runtime.lease_for_slot("ses0") is None
    finally:
        set_plan_job_ledger(None)
        set_job_coordinator(None)
        set_plan_runtime(None)


def test_daemon_issued_approval_token_is_scope_bound_single_use_and_replay_safe(
    tmp_path,
):
    from sap_cli.jobs import PlanJobLedger, set_plan_job_ledger
    from sap_cli.planning.approvals import (
        ApprovalTokenConsumedError,
        ApprovalTokenInvalidError,
        PlanApprovalStore,
        set_plan_approval_store,
    )

    class Dump:
        def __init__(self, value):
            self.value = value

        def model_dump(self, mode="json"):
            return self.value

    class Sap:
        def __init__(self):
            self.actions = []

        def wait_until_idle(self):
            return True

        def get_session_info(self):
            return Dump({"transaction": "MM03", "screen_number": 0})

        def get_status(self):
            return Dump({"type": "S"})

        def has_modal(self):
            return False

        def run_transaction(self, tcode):
            self.actions.append(("run", tcode))

        def set_field(self, element_id, value):
            self.actions.append(("set", element_id, value))

        def perform_ui_action(self, element_id, action, value=None, **kwargs):
            self.actions.append((action, element_id, value))
            return {"ok": True}

        def inspect_element(self, element_id):
            return {"id": element_id, "visible": True}

    policy = Policy(
        active_agent="operator",
        agents={
            "operator": AgentPolicy(
                relations=[RelationBinding(resource="tx:*", relation=Relation.OPERATOR)]
            )
        },
        dangerous_transactions=[
            DangerousTransaction(tcode="MM03", reason="approval required")
        ],
    )
    runtime = PlanRuntime()
    approvals = PlanApprovalStore(token_factory=lambda: "one-time-secret-token")
    ledger = PlanJobLedger(tmp_path / "approval-ledger")
    store = store_with_map(tmp_path / "maps")
    sap = Sap()
    set_plan_runtime(runtime)
    set_plan_approval_store(approvals)
    set_plan_job_ledger(ledger)
    try:
        preview = dispatch_planning_command(
            "plan_preview",
            {
                "request": "tcode MM03 기본 데이터 조회",
                "map_id": "map",
                "goal_query": "기본 데이터",
                "values": {"material": "MAT-1"},
            },
            get_map_store=lambda: store,
            get_recipe_store=lambda: type(
                "Recipes", (), {"query_for_ai": lambda *a, **k: []}
            )(),
            policy=policy,
            agent_name="operator",
        )
        reference = dict(preview["plan_ref"])
        reference.pop("expires_in_seconds")
        reference.pop("daemon_memory_only")
        assert preview["validation"]["required_approvals"] == ["navigate-tcode"]
        issued = dispatch_planning_command(
            "plan_approve",
            {
                **reference,
                "subject_agent": "operator",
                "step_ids": ["navigate-tcode"],
                "expires_in_seconds": 60,
            },
            ctx=SimpleNamespace(slot_id="ses0", agent_name="admin"),
        )
        token = issued["approval_token"]
        assert token == "one-time-secret-token"
        with pytest.raises(ApprovalTokenInvalidError):
            approvals.validate(
                token,
                plan_id=reference["plan_id"],
                plan_hash=reference["plan_hash"],
                subject_agent="another-agent",
                slot_id="ses0",
            )

        result = dispatch_planning_command(
            "plan_execute",
            {
                **reference,
                "execution_id": "approved-execution",
                "approval_token": token,
            },
            get_map_store=lambda: store,
            policy=policy,
            agent_name="operator",
            ctx=SimpleNamespace(slot_id="ses0", agent_name="operator"),
            get_sap=lambda _slot: sap,
        )

        assert result["status"] == "completed"
        assert result["result"]["status"] == "success"
        assert sap.actions
        ledger_text = next((tmp_path / "approval-ledger").glob("*.json")).read_text()
        assert token not in ledger_text
        assert "navigate-tcode" not in ledger_text

        with pytest.raises(ApprovalTokenConsumedError):
            dispatch_planning_command(
                "plan_execute",
                {
                    **reference,
                    "execution_id": "second-execution",
                    "approval_token": token,
                },
                get_map_store=lambda: store,
                policy=policy,
                agent_name="operator",
                ctx=SimpleNamespace(slot_id="ses0", agent_name="operator"),
                get_sap=lambda _slot: sap,
            )

        runtime.registry.remove(reference["plan_id"], reference["plan_hash"])
        replay = dispatch_planning_command(
            "plan_execute",
            {**reference, "execution_id": "approved-execution"},
            get_map_store=lambda: store,
            policy=policy,
            agent_name="operator",
            ctx=SimpleNamespace(slot_id="ses0", agent_name="operator"),
            get_sap=lambda _slot: (_ for _ in ()).throw(AssertionError("SAP replay")),
        )
        assert replay["status"] == "completed"
        assert replay["replayed"] is True

    finally:
        set_plan_job_ledger(None)
        set_plan_approval_store(None)
        set_plan_runtime(None)


def test_user_guided_map_lease_blocks_plan_before_sap_access(tmp_path):
    from sap_cli.jobs import DaemonJobCoordinator, set_job_coordinator

    runtime = PlanRuntime()
    coordinator = DaemonJobCoordinator()
    coordinator.submit(
        "map:guided",
        "map_traverse",
        "digest",
        slot_id="ses0",
        agent_name="agent-a",
    )
    coordinator.claim("map:guided")
    coordinator.needs_user_input("map:guided")
    store = store_with_map(tmp_path)
    sap_calls = []
    set_plan_runtime(runtime)
    set_job_coordinator(coordinator)
    try:
        preview = dispatch_planning_command(
            "plan_preview",
            {
                "request": "tcode MM03 기본 데이터 조회",
                "map_id": "map",
                "goal_query": "기본 데이터",
                "values": {"material": "MAT-1"},
            },
            get_map_store=lambda: store,
            get_recipe_store=lambda: type(
                "Recipes", (), {"query_for_ai": lambda *a, **k: []}
            )(),
        )
        reference = dict(preview["plan_ref"])
        reference.pop("expires_in_seconds")
        reference.pop("daemon_memory_only")
        with pytest.raises(RuntimeError, match="reserved by another daemon job"):
            dispatch_planning_command(
                "plan_execute",
                {**reference, "execution_id": "blocked"},
                get_map_store=lambda: store,
                agent_name="agent-a",
                ctx=SimpleNamespace(slot_id="ses0", agent_name="agent-a"),
                get_sap=lambda _slot: sap_calls.append(True),
            )
        assert sap_calls == []
    finally:
        set_job_coordinator(None)
        set_plan_runtime(None)


def test_new_map_head_evicts_cached_plan_before_sap_access(tmp_path):
    from sap_cli.planning.runtime import PlanMapDependencyError

    runtime = PlanRuntime()
    store = store_with_map(tmp_path)
    set_plan_runtime(runtime)
    sap_calls = []
    try:
        preview = dispatch_planning_command(
            "plan_preview",
            {
                "request": "tcode MM03 기본 데이터 조회",
                "map_id": "map",
                "goal_query": "기본 데이터",
                "values": {"material": "MAT-1"},
            },
            get_map_store=lambda: store,
            get_recipe_store=lambda: type(
                "Recipes", (), {"query_for_ai": lambda *a, **k: []}
            )(),
        )
        current = store.latest("map", tcode="MM03")
        store.save(current)
        reference = dict(preview["plan_ref"])
        reference.pop("expires_in_seconds")
        reference.pop("daemon_memory_only")
        with pytest.raises(PlanMapDependencyError):
            dispatch_planning_command(
                "plan_execute",
                {**reference, "execution_id": "stale"},
                get_map_store=lambda: store,
                agent_name="agent-a",
                ctx=SimpleNamespace(slot_id="ses0", agent_name="agent-a"),
                get_sap=lambda _slot: sap_calls.append(True),
            )
        assert sap_calls == []
        assert len(runtime.registry) == 0
    finally:
        set_plan_runtime(None)


def test_plan_execute_outer_boundary_audits_once_without_state_capture(monkeypatch):
    import sap_cli.dispatcher as dispatcher

    audits = []
    monkeypatch.setattr(
        dispatcher,
        "_evaluate_authorization",
        lambda *args: (
            AuthorizationDecision(
                agent="agent",
                permission="write",
                resource="tool:sap_plan_execute",
                allowed=True,
            ),
            None,
        ),
    )
    monkeypatch.setattr(
        dispatcher,
        "_audit_authorization",
        lambda *args: audits.append(args),
    )
    monkeypatch.setattr(
        dispatcher,
        "_capture_transcript_state",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("state capture")),
    )
    monkeypatch.setattr(
        dispatcher,
        "_execute_command",
        lambda *args: {"status": "completed"},
    )

    result = dispatch_command(
        "plan_execute",
        {
            "plan_id": "p",
            "plan_hash": "h",
            "execution_id": "e",
            "approval_token": "NEVER-LOG-THIS-TOKEN",
        },
        DispatchContext(agent_name="agent", session_id="session"),
    )

    assert result == {"status": "completed"}
    assert len(audits) == 1
    assert "NEVER-LOG-THIS-TOKEN" not in str(audits)
