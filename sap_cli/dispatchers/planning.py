"""Read-only execution-plan preview dispatcher."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping
from typing import Any, Callable

from sap_cli.auth.policy import Policy
from sap_cli.auth.preflight import preflight_tool_call
from sap_cli.planning.compiler import PlanCompiler
from sap_cli.planning.executor import PlanExecutor
from sap_cli.planning.approvals import get_plan_approval_store
from sap_cli.planning.preflight import preflight_plan
from sap_cli.planning.runtime import (
    ExecutionConflictError,
    get_plan_runtime,
    try_get_plan_runtime,
)
from sap_cli.planning.schema import NaturalLanguageRequest
from sap_cli.jobs import try_get_job_coordinator, try_get_plan_job_ledger
from sap_cli.recipe import RecipeStore
from sap_cli.ui_map.store import UiMapStore

from .ui_maps import default_map_store


PLANNING_COMMANDS = frozenset(
    {"plan_approve", "plan_execute", "plan_preview", "plan_context", "plan_compile"}
)


def is_planning_command(command: str) -> bool:
    return command in PLANNING_COMMANDS


def dispatch_planning_command(
    command: str,
    args: dict[str, Any],
    *,
    get_map_store: Callable[[], UiMapStore] = default_map_store,
    get_recipe_store: Callable[[], Any] = RecipeStore,
    policy: Policy | None = None,
    agent_name: str | None = None,
    enforce_auth: bool = True,
    ctx: Any = None,
    get_sap: Callable[[str], Any] | None = None,
    authorize_slot: Callable[[str, str], None] | None = None,
) -> dict[str, Any]:
    if command == "plan_approve":
        return _dispatch_plan_approve(args, ctx=ctx)
    if command == "plan_execute":
        return _dispatch_plan_execute(
            args,
            policy=policy,
            agent_name=agent_name,
            enforce_auth=enforce_auth,
            ctx=ctx,
            get_map_store=get_map_store,
            get_sap=get_sap,
            authorize_slot=authorize_slot,
        )
    if command == "plan_context":
        return _dispatch_plan_context(args, get_map_store=get_map_store)
    if command not in {"plan_preview", "plan_compile"}:
        raise ValueError(f"unsupported planning command: {command}")
    text = str(args.get("request") or args.get("text") or "").strip()
    if not text:
        raise ValueError("request is required")
    supplied = args.get("values") or args.get("supplied_values") or {}
    if not isinstance(supplied, dict):
        raise ValueError("values must be an object")
    request = NaturalLanguageRequest(
        text=text,
        request_id=str(args.get("request_id", "")),
        system_id=str(args.get("system_id", "")),
        client=str(args.get("client", "")),
        tcode_hint=str(args.get("tcode", "")),
        supplied_values=supplied,
        success_criteria=list(args.get("success_criteria") or []),
        context={"map_id": str(args.get("map_id", ""))},
    )
    runtime_versions = {
        key: str(args[key])
        for key in ("api_version", "gui_version", "application_release", "schema_version")
        if args.get(key)
    }
    draft = args.get("draft") or {}
    if command == "plan_compile" and not isinstance(draft, dict):
        raise ValueError("draft must be an object")
    effective_goal = (
        str(draft.get("goal_query", "")).strip()
        if command == "plan_compile"
        else str(args.get("goal_query", ""))
    )
    if command == "plan_compile" and not effective_goal:
        raise ValueError("draft.goal_query is required")
    if command == "plan_compile" and not isinstance(
        draft.get("route_transition_ids"), list
    ):
        raise ValueError("draft.route_transition_ids is required and must be an array")
    result = PlanCompiler(get_map_store(), get_recipe_store()).compile(
        request,
        current_state_id=str(args.get("current_state_id", "")),
        goal_query=effective_goal,
        map_id=str(args.get("map_id", "")),
        runtime_versions=runtime_versions,
    )
    validation = result.validation.model_dump(mode="json")
    if command == "plan_compile" and result.plan is not None:
        requested_route = [
            str(item)
            for item in draft.get("route_transition_ids", [])
            if str(item)
        ]
        actual_route = [
            step.map_transition_id
            for step in result.plan.steps
            if step.map_transition_id
        ]
        if requested_route != actual_route:
            validation["valid"] = False
            validation["errors"] = list(
                dict.fromkeys([*validation["errors"], "draft_route_mismatch"])
            )
            validation["warnings"] = [
                *validation["warnings"],
                "LLM draft route does not match the verified shortest saved route.",
            ]
    plan_preflight = None
    transaction_authorization = None
    if result.plan is not None:
        plan_preflight = preflight_plan(
            result.plan,
            authorization_hook=lambda _command, tool, params, _step: preflight_tool_call(
                tool,
                params,
                policy=policy,
                agent_name=agent_name,
                enforce_auth=enforce_auth,
            ),
        )
        validation["valid"] = bool(validation["valid"] and plan_preflight.valid)
        validation["errors"] = list(
            dict.fromkeys([*validation["errors"], *plan_preflight.errors])
        )
        validation["requires_approval"] = bool(
            validation["requires_approval"] or plan_preflight.requires_approval
        )
        validation["required_approvals"] = list(
            dict.fromkeys(
                [
                    *validation["required_approvals"],
                    *plan_preflight.required_approvals,
                ]
            )
        )
        if not any(step.command == "run" for step in result.plan.steps):
            transaction_authorization = preflight_tool_call(
                "sap_run",
                {"tcode": result.plan.tcode},
                policy=policy,
                agent_name=agent_name,
                enforce_auth=enforce_auth,
            )
            if not transaction_authorization.allowed:
                validation["valid"] = False
                validation["errors"] = list(
                    dict.fromkeys(
                        [*validation["errors"], "transaction authorization denied"]
                    )
                )
            if transaction_authorization.approval_required:
                validation["requires_approval"] = True
                validation["required_approvals"] = list(
                    dict.fromkeys(
                        [*validation["required_approvals"], "authorize-tcode"]
                    )
                )
    plan_ref = None
    activation = None
    if result.plan is not None and validation["valid"]:
        runtime = try_get_plan_runtime()
        reference = runtime.register_plan(result.plan) if runtime is not None else None
        if reference is not None:
            plan_ref = {
                "plan_id": reference.plan_id,
                "plan_hash": reference.plan_hash,
                "expires_in_seconds": reference.expires_in_seconds,
                "daemon_memory_only": True,
            }
            # Keep the hand-off deliberately small.  An agent can forward this
            # object directly to ``sap_plan_execute`` after the user confirms
            # the preview; it does not need to copy identifiers from the full
            # plan payload or invent an execution ID.
            activation = {
                "tool": "sap_plan_execute",
                "arguments": {"plan_ref": plan_ref, "verify_mode": "cheap"},
                "requires_user_confirmation": True,
            }
    interaction_request = (
        result.plan.original_request if result.plan is not None else text
    )
    if result.intent is not None:
        for name, value in result.intent.values.items():
            if any(
                marker in name.casefold()
                for marker in (
                    "password", "passwd", "pwd", "secret", "token", "pin", "비밀번호"
                )
            ) and value not in (None, ""):
                interaction_request = interaction_request.replace(
                    str(value), "<redacted>"
                )
    payload: dict[str, Any] = {
        "validation": validation,
        "plan": result.plan.model_dump(mode="json") if result.plan else None,
        "plan_ref": plan_ref,
        "activation": activation,
        "preflight": (
            plan_preflight.model_dump(mode="json") if plan_preflight else None
        ),
        "transaction_authorization": (
            transaction_authorization.as_dict()
            if transaction_authorization is not None
            else None
        ),
        "interactions": _standard_interactions(
            validation.get("questions", []),
            request=interaction_request,
            tcode=(
                result.intent.tcode
                if result.intent is not None
                else str(args.get("tcode", "")).strip().upper()
            ),
            map_id=str(args.get("map_id", "")),
            values=supplied,
        ),
    }
    if result.intent is not None:
        sensitive = {
            name
            for name, variable in (result.plan.variables.items() if result.plan else [])
            if variable.sensitive
        } | {
            name
            for name in result.intent.values
            if any(
                marker in name.casefold()
                for marker in ("password", "passwd", "pwd", "secret", "token", "pin", "비밀번호")
            )
        }
        payload["intent"] = {
            "tcode": result.intent.tcode,
            "operation": result.intent.operation,
            "entities": result.intent.entities,
            "values": {
                name: "<redacted>" if name in sensitive else value
                for name, value in result.intent.values.items()
            },
            "ambiguities": result.intent.ambiguities,
            "confidence": result.intent.confidence,
        }
    if activation is not None:
        payload["interactions"].append(
            {
                "interaction_id": "interaction-" + hashlib.sha256(
                    f"{result.plan.plan_id}:execute-confirmation".encode()
                ).hexdigest()[:16],
                "type": "confirmation",
                "audience": "user",
                "blocking": True,
                "code": "plan_execution_confirmation",
                "prompt": "검증된 SAP 실행 계획을 실행할까요?",
                "choices": [
                    {
                        "id": "execute",
                        "label": "실행",
                        "description": "표시된 계획과 승인 조건에 따라 SAP 작업을 실행합니다.",
                    },
                    {
                        "id": "cancel",
                        "label": "취소",
                        "description": "계획을 실행하지 않습니다.",
                    },
                ],
                "continuation": {
                    "tool": "sap_plan_execute",
                    "choice_argument_mapping": {
                        "execute": activation["arguments"],
                        "cancel": None,
                    },
                },
                "llm_guidance": [
                    "계획의 핵심 작업과 위험·승인 조건을 자연어로 요약해 사용자에게 확인합니다.",
                    "사용자가 명시적으로 실행에 동의하기 전에는 sap_plan_execute를 호출하지 않습니다.",
                ],
            }
        )
    return payload


def _dispatch_plan_context(
    args: dict[str, Any], *, get_map_store: Callable[[], UiMapStore]
) -> dict[str, Any]:
    """Return a bounded, value-free map context for an LLM plan author."""

    text = str(args.get("request") or "").strip()
    tcode = str(args.get("tcode") or "").strip().upper()
    map_id = str(args.get("map_id") or "").strip()
    if not text or not tcode:
        raise ValueError("request and tcode are required")
    candidates = get_map_store().list_latest(
        tcode=tcode,
        system_id=str(args.get("system_id", "")),
        client=str(args.get("client", "")),
        language=str(args.get("language", "")),
    )
    if map_id:
        candidates = [item for item in candidates if item.manifest.map_id == map_id]
    if len(candidates) != 1:
        code = "map_missing" if not candidates else "map_ambiguous"
        prompt = (
            f"{tcode}의 UI 지도가 없습니다."
            if not candidates
            else "계획에 사용할 지도 하나를 선택해 주세요."
        )
        question = {
            "code": code,
            "question": prompt,
            "choices": [
                {
                    "id": item.manifest.map_id,
                    "label": item.manifest.map_id,
                    "description": (
                        f"{item.manifest.system_id}/{item.manifest.client}/"
                        f"{item.manifest.language}"
                    ),
                }
                for item in candidates[:12]
            ],
        }
        return {
            "request": text,
            "context": None,
            "interactions": _standard_interactions(
                [question], request=text, tcode=tcode, map_id=map_id, values={}
            ),
        }
    ui_map = candidates[0]
    states = []
    for state in sorted(ui_map.states.values(), key=lambda item: item.state_id)[:50]:
        components = []
        for component in state.components:
            if component.sensitive or component.visible is False:
                continue
            label = (
                component.name or component.tooltip
                if component.changeable is True
                else component.text or component.name or component.tooltip
            )
            if component.type not in {
                "GuiLabel", "GuiTab", "GuiButton", "GuiMenu", "GuiTextField",
                "GuiCTextField", "GuiComboBox", "GuiCheckBox", "GuiRadioButton",
                "GuiTableControl", "GuiGridView", "GuiTree",
            }:
                continue
            components.append(
                {
                    "logical_id": component.logical_id,
                    "type": component.type,
                    "label": label,
                    "required": component.required,
                    "changeable": component.changeable,
                    "supported_actions": component.supported_actions,
                }
            )
            if len(components) >= 40:
                break
        states.append(
            {
                "state_id": state.state_id,
                "title": state.title,
                "program": state.program,
                "screen_number": state.screen_number,
                "components": components,
            }
        )
    transitions = [
        {
            "transition_id": item.transition_id,
            "from_state_id": item.from_state_id,
            "to_state_id": item.to_state_id,
            "action": item.action,
            "target_logical_id": item.target_logical_id,
            "side_effect": item.side_effect.value,
        }
        for item in ui_map.transitions[:100]
    ]
    gaps = [
        {
            key: item.get(key)
            for key in (
                "gap_id", "state_id", "reason", "action", "target_logical_id", "label"
            )
            if item.get(key) not in (None, "")
        }
        for item in ui_map.manifest.unreachable
        if item.get("resolved") is not True
    ][:50]
    return {
        "request": text,
        "author": "calling_llm",
        "instructions": [
            "사용자 요청의 의미를 해석해 목표 logical_id 또는 state_id를 선택합니다.",
            "저장된 transition_id만 사용해 시작 상태부터 목표까지 route를 작성합니다.",
            "미관측 목적지는 계획에 추측하지 말고 gaps에서 관련 분기를 골라 먼저 지도 보강합니다.",
            "질문이 필요하면 interactions 규약에 따라 자연어로 질문하고 답변 전에는 후속 도구를 호출하지 않습니다.",
            "완성한 draft를 sap_plan_compile로 보내 결정론적 검증을 받습니다.",
        ],
        "draft_schema": {
            "goal_query": "선택한 정확한 state_id 또는 component logical_id",
            "route_transition_ids": ["저장된 transition_id를 실행 순서대로"],
            "reasoning_summary": "민감 업무값을 제외한 짧은 선택 근거",
        },
        "context": {
            "map_id": ui_map.manifest.map_id,
            "tcode": ui_map.manifest.tcode,
            "map_version": ui_map.manifest.map_version,
            "states": states,
            "transitions": transitions,
            "gaps": gaps,
        },
        "interactions": [],
    }


def _standard_interactions(
    questions: list[Any],
    *,
    request: str,
    tcode: str,
    map_id: str,
    values: dict[str, Any],
) -> list[dict[str, Any]]:
    """Convert internal questions into a uniform LLM conversation contract."""

    interactions: list[dict[str, Any]] = []
    for index, raw in enumerate(questions):
        question = raw if isinstance(raw, dict) else {"question": str(raw)}
        code = str(question.get("code") or question.get("field") or "input_required")
        prompt = str(question.get("question") or "추가 확인이 필요합니다.")
        choices = question.get("choices") if isinstance(question.get("choices"), list) else []
        interaction_id = "interaction-" + hashlib.sha256(
            json.dumps(
                [request, code, index, choices],
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            ).encode()
        ).hexdigest()[:16]
        continuation: dict[str, Any] = {
            "tool": "sap_plan_compile",
            "arguments_template": {
                "request": request,
                "tcode": tcode,
                "map_id": map_id,
                "draft": {
                    "goal_query": "<LLM_SELECTED_EXACT_GOAL>",
                    "route_transition_ids": ["<LLM_SELECTED_TRANSITION_IDS>"],
                },
                "values": values,
            },
        }
        if code == "goal_semantic_inference_required":
            continuation = {
                "tool": "sap_map_augment_start",
                "choice_argument_field": "augment_args",
                "after_completion": {
                    "tool": "sap_plan_context",
                    "arguments": {"request": request, "tcode": tcode, "map_id": map_id},
                },
            }
        interactions.append(
            {
                "interaction_id": interaction_id,
                "type": "choice" if choices else "input",
                "audience": "user",
                "blocking": True,
                "code": code,
                "prompt": prompt,
                "choices": choices,
                "continuation": continuation,
                "llm_guidance": [
                    "원시 JSON을 그대로 출력하지 말고 현재 작업 문맥에 맞는 자연스러운 한국어 질문으로 바꿉니다.",
                    "내부 ID는 사용자 구분에 꼭 필요한 경우에만 보여줍니다.",
                    "사용자 답변을 받기 전에는 continuation 도구를 호출하지 않습니다.",
                    "답변이 선택지와 대응하지 않으면 추측하지 말고 다시 확인합니다.",
                ],
            }
        )
    return interactions


def _dispatch_plan_approve(args: dict[str, Any], *, ctx: Any) -> dict[str, Any]:
    """Issue one opaque, daemon-memory approval token for a subject agent."""

    if ctx is None:
        raise RuntimeError("plan approval requires the persistent daemon runtime")
    plan_id = str(args.get("plan_id", "")).strip()
    plan_hash = str(args.get("plan_hash", "")).strip()
    subject_agent = str(args.get("subject_agent", "")).strip()
    raw_steps = args.get("step_ids")
    if not plan_id or not plan_hash or not subject_agent:
        raise ValueError("plan_id, plan_hash, and subject_agent are required")
    if not isinstance(raw_steps, list):
        raise ValueError("step_ids must be an array")
    step_ids = list(dict.fromkeys(str(item).strip() for item in raw_steps))
    if not step_ids or any(not item for item in step_ids):
        raise ValueError("at least one non-empty step_id is required")
    plan = get_plan_runtime().get_plan(plan_id, plan_hash)
    allowed_step_ids = {step.step_id for step in plan.steps} | {"authorize-tcode"}
    unknown = sorted(set(step_ids) - allowed_step_ids)
    if unknown:
        raise ValueError("approval contains a step outside the immutable plan")
    slot_id = str(getattr(ctx, "slot_id", "ses0") or "ses0")
    ttl_raw = args.get("expires_in_seconds")
    ttl = None if ttl_raw in (None, "") else float(ttl_raw)
    receipt = get_plan_approval_store().issue(
        plan_id=plan_id,
        plan_hash=plan_hash,
        step_ids=step_ids,
        subject_agent=subject_agent,
        slot_id=slot_id,
        ttl_seconds=ttl,
    )
    return {
        "approval_token": receipt.token,
        "expires_in_seconds": receipt.expires_in_seconds,
        "daemon_memory_only": True,
        "single_use": True,
    }


def _dispatch_plan_execute(
    args: dict[str, Any],
    *,
    policy: Policy | None,
    agent_name: str | None,
    enforce_auth: bool,
    ctx: Any,
    get_map_store: Callable[[], UiMapStore],
    get_sap: Callable[[str], Any] | None,
    authorize_slot: Callable[[str, str], None] | None,
) -> dict[str, Any]:
    """Execute one daemon-cached immutable plan with idempotent ownership."""

    if ctx is None or get_sap is None:
        raise RuntimeError("plan execution requires the persistent daemon runtime")
    runtime = get_plan_runtime()
    plan_id, plan_hash = _execution_plan_reference(args)
    supplied_execution_id = str(args.get("execution_id", "")).strip()
    # The ID remains caller-controllable for idempotent retries.  For the
    # normal preview -> confirm -> execute flow, generate it here so an agent
    # can activate the supplied plan_ref without manufacturing one itself.
    execution_id = supplied_execution_id or f"plan-{uuid.uuid4().hex}"
    verification = str(args.get("verify_mode", "cheap") or "cheap").lower()
    if verification not in {"cheap", "full"}:
        raise ValueError("verify_mode must be cheap or full")
    if "approval_grants" in args:
        raise ValueError(
            "caller-supplied approval_grants are forbidden; use approval_token"
        )
    approval_token = str(args.get("approval_token", "") or "").strip()
    slot_id = str(getattr(ctx, "slot_id", "ses0") or "ses0")
    effective_agent = str(
        agent_name
        or getattr(ctx, "agent_name", "")
        or (policy.active_agent if policy else "")
        or "anonymous"
    )
    if authorize_slot is not None:
        authorize_slot(slot_id, effective_agent)
    digest = hashlib.sha256(
        json.dumps(
            {
                "plan_id": plan_id,
                "plan_hash": plan_hash,
                "verify_mode": verification,
                "slot_id": slot_id,
                "agent_name": effective_agent,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    try:
        previous = runtime.get_execution(execution_id)
    except KeyError:
        previous = None
    if previous is not None:
        if (
            previous.request_digest != digest
            or previous.slot_id != slot_id
            or previous.agent_name != effective_agent
        ):
            raise ExecutionConflictError(
                "execution_id is already bound to a different request"
            )
        return {
            "execution_id": execution_id,
            "status": previous.status,
            "replayed": True,
            "result": previous.result,
            "error_code": previous.error_code,
        }
    plan = runtime.get_plan(plan_id, plan_hash)
    map_store = get_map_store()
    head = map_store.latest_head(
        plan.map_id,
        tcode=plan.tcode,
        system_id=str(plan.map_scope.get("system_id", "")),
        client=str(plan.map_scope.get("client", "")),
        language=str(plan.map_scope.get("language", "")),
    )
    plan = runtime.validate_plan_map_head(
        plan_id,
        plan_hash,
        head,
        fingerprint=str(head.get("content_fingerprint", "")),
    )
    grants: set[str] = set()
    approval_store = None
    if approval_token:
        approval_store = get_plan_approval_store()
        grants = set(
            approval_store.validate(
                approval_token,
                plan_id=plan_id,
                plan_hash=plan_hash,
                subject_agent=effective_agent,
                slot_id=slot_id,
            )
        )
    coordinator = try_get_job_coordinator()
    ledger = try_get_plan_job_ledger()
    coordinator_id = f"plan:{execution_id}"
    if coordinator is not None:
        lease = coordinator.lease_for_slot(slot_id)
        if lease is not None and lease.job_id != coordinator_id:
            raise RuntimeError("SAP slot is reserved by another daemon job")
    ledger_record = None
    try:
        if coordinator is not None:
            coordinated = coordinator.submit(
                coordinator_id,
                "plan_execute",
                digest,
                slot_id=slot_id,
                agent_name=effective_agent,
            )
            if not coordinated.replayed:
                claimed = coordinator.claim(coordinator_id)
                if claimed is None:
                    coordinator.cancel(coordinator_id)
                    raise RuntimeError("SAP slot is reserved by an earlier daemon job")
        if ledger is not None:
            ledger_record = ledger.create(
                job_id=coordinator_id,
                execution_id=execution_id,
                plan_id=plan_id,
                plan_hash=plan_hash,
                request_digest=digest,
                owner=effective_agent,
                slot=slot_id,
                status="running",
            )
    except Exception:
        if coordinator is not None:
            try:
                current = coordinator.get(coordinator_id)
                if current.status in {"queued", "running"}:
                    coordinator.fail(
                        coordinator_id, error_code="job_coordination_failed"
                    )
            except Exception:
                pass
        if ledger is not None:
            try:
                if ledger_record is None:
                    ledger.create(
                        job_id=coordinator_id,
                        execution_id=execution_id,
                        plan_id=plan_id,
                        plan_hash=plan_hash,
                        request_digest=digest,
                        owner=effective_agent,
                        slot=slot_id,
                        status="failed",
                    )
                elif ledger_record.status in {"queued", "running"}:
                    ledger.update(
                        coordinator_id,
                        status="failed",
                        error_code="job_coordination_failed",
                    )
            except Exception:
                pass
        raise
    try:
        snapshot = runtime.begin_execution(
            execution_id,
            digest,
            slot_id=slot_id,
            agent_name=effective_agent,
        )
    except Exception:
        if coordinator is not None:
            current = coordinator.get(coordinator_id)
            if current.status == "running":
                coordinator.fail(coordinator_id, error_code="plan_runtime_rejected")
        if ledger is not None:
            ledger.update(
                coordinator_id, status="failed", error_code="plan_runtime_rejected"
            )
        raise
    if snapshot.replayed:
        return {
            "execution_id": execution_id,
            "status": snapshot.status,
            "replayed": True,
            "result": snapshot.result,
            "error_code": snapshot.error_code,
        }
    if approval_token:
        assert approval_store is not None
        try:
            grants = set(
                approval_store.consume(
                    approval_token,
                    plan_id=plan_id,
                    plan_hash=plan_hash,
                    subject_agent=effective_agent,
                    slot_id=slot_id,
                )
            )
        except Exception:
            failed = runtime.fail_execution(
                execution_id,
                error_code="approval_token_rejected",
                slot_id=slot_id,
                agent_name=effective_agent,
            )
            if coordinator is not None:
                current = coordinator.get(coordinator_id)
                if current.status == "running":
                    coordinator.fail(
                        coordinator_id, error_code="approval_token_rejected"
                    )
            if ledger is not None:
                ledger.update(
                    coordinator_id,
                    status="failed",
                    error_code="approval_token_rejected",
                )
            raise ValueError(failed.error_code) from None

    def owned_sap() -> Any:
        sap = get_sap(slot_id)
        if authorize_slot is not None:
            authorize_slot(slot_id, effective_agent)
        return sap

    executor = PlanExecutor(
        get_sap=owned_sap,
        map_store=map_store,
        auth_hook=lambda _command, tool, params, _step: preflight_tool_call(
            tool,
            params,
            policy=policy,
            agent_name=effective_agent,
            enforce_auth=enforce_auth,
        ),
    )
    try:
        execution_result = executor.execute(
            plan,
            approval_grants=grants,
            verification=verification,
        )
        result = execution_result.model_dump(mode="json")
    except Exception:
        unknown = runtime.mark_outcome_unknown(
            execution_id,
            error_code="executor_outcome_unknown",
            slot_id=slot_id,
            agent_name=effective_agent,
        )
        if coordinator is not None:
            current = coordinator.get(coordinator_id)
            if current.status == "running":
                coordinator.mark_outcome_unknown(
                    coordinator_id, error_code="executor_outcome_unknown"
                )
        if ledger is not None:
            ledger.update(
                coordinator_id,
                status="outcome_unknown",
                error_code="executor_outcome_unknown",
            )
        return {
            "execution_id": execution_id,
            "status": unknown.status,
            "error_code": unknown.error_code,
            "replayed": False,
        }
    if execution_result.status == "failed" and execution_result.commit_attempted:
        unknown = runtime.mark_outcome_unknown(
            execution_id,
            error_code="unknown_commit_outcome",
            result=result,
            slot_id=slot_id,
            agent_name=effective_agent,
        )
        if coordinator is not None:
            current = coordinator.get(coordinator_id)
            if current.status == "running":
                coordinator.mark_outcome_unknown(
                    coordinator_id, error_code="unknown_commit_outcome"
                )
        if ledger is not None:
            ledger.update(
                coordinator_id,
                status="outcome_unknown",
                error_code="unknown_commit_outcome",
            )
        return {
            "execution_id": execution_id,
            "status": unknown.status,
            "error_code": unknown.error_code,
            "replayed": False,
            "result": unknown.result,
        }
    if execution_result.status in {"blocked", "failed"}:
        error_code = (
            "plan_blocked"
            if execution_result.status == "blocked"
            else "plan_failed"
        )
        failed = runtime.fail_execution(
            execution_id,
            result,
            error_code=error_code,
            slot_id=slot_id,
            agent_name=effective_agent,
        )
        if coordinator is not None:
            current = coordinator.get(coordinator_id)
            if current.status == "running":
                coordinator.fail(coordinator_id, error_code=error_code)
        if ledger is not None:
            ledger.update(
                coordinator_id, status="failed", error_code=error_code
            )
        payload = {
            "execution_id": execution_id,
            "status": failed.status,
            "error_code": failed.error_code,
            "replayed": False,
            "result": failed.result,
        }
        if any(
            "input_value_conflict" in str(error)
            for error in execution_result.errors
        ):
            payload["interactions"] = [
                {
                    "interaction_id": "interaction-" + hashlib.sha256(
                        f"{execution_id}:input-value-conflict".encode()
                    ).hexdigest()[:16],
                    "type": "choice",
                    "audience": "user",
                    "blocking": True,
                    "code": "input_value_conflict",
                    "prompt": (
                        "SAP 입력 필드에 계획과 다른 값이 이미 있습니다. "
                        "기존 값을 유지해 새 계획을 만들까요, 작업을 취소할까요?"
                    ),
                    "choices": [
                        {
                            "id": "replan",
                            "label": "기존 값으로 재계획",
                            "description": "현재 SAP 값을 덮어쓰지 않고 계획을 다시 작성합니다.",
                        },
                        {
                            "id": "cancel",
                            "label": "취소",
                            "description": "SAP 값을 변경하지 않고 종료합니다.",
                        },
                    ],
                    "continuation": {
                        "tool": "sap_plan_context",
                        "choice_argument_mapping": {
                            "replan": {
                                "request": plan.original_request,
                                "tcode": plan.tcode,
                                "map_id": plan.map_id,
                            },
                            "cancel": None,
                        },
                    },
                    "llm_guidance": [
                        "기존 값이나 계획 값을 응답에 노출하지 않고 충돌 사실만 자연어로 설명합니다.",
                        "사용자 답변 전에 필드를 지우거나 덮어쓰거나 후속 도구를 호출하지 않습니다.",
                    ],
                }
            ]
        return payload
    completed = runtime.complete_execution(
        execution_id,
        result,
        slot_id=slot_id,
        agent_name=effective_agent,
    )
    if coordinator is not None:
        current = coordinator.get(coordinator_id)
        if current.status == "running":
            coordinator.complete(coordinator_id)
    if ledger is not None:
        ledger.update(coordinator_id, status="completed")
    return {
        "execution_id": execution_id,
        "status": completed.status,
        "replayed": False,
        "result": completed.result,
    }


def _execution_plan_reference(args: Mapping[str, Any]) -> tuple[str, str]:
    """Resolve a preview ``plan_ref`` without weakening immutable-plan checks.

    Preview responses intentionally nest their opaque reference under
    ``plan_ref``.  Historically execute only accepted flattened ``plan_id`` /
    ``plan_hash`` fields, which made the natural hand-off fail unless a caller
    manually reshaped the response.  Accept both representations and reject a
    conflicting mixture rather than silently choosing one.
    """

    direct_id = str(args.get("plan_id", "") or "").strip()
    direct_hash = str(args.get("plan_hash", "") or "").strip()
    raw_ref = args.get("plan_ref")
    if raw_ref in (None, ""):
        if not direct_id or not direct_hash:
            raise ValueError("plan_ref or both plan_id and plan_hash are required")
        return direct_id, direct_hash
    if not isinstance(raw_ref, Mapping):
        raise ValueError("plan_ref must be an object returned by plan_preview")
    ref_id = str(raw_ref.get("plan_id", "") or "").strip()
    ref_hash = str(raw_ref.get("plan_hash", "") or "").strip()
    if not ref_id or not ref_hash:
        raise ValueError("plan_ref requires plan_id and plan_hash")
    if direct_id and direct_id != ref_id:
        raise ValueError("plan_id conflicts with plan_ref")
    if direct_hash and direct_hash != ref_hash:
        raise ValueError("plan_hash conflicts with plan_ref")
    return ref_id, ref_hash

__all__ = [
    "PLANNING_COMMANDS",
    "dispatch_planning_command",
    "is_planning_command",
]
