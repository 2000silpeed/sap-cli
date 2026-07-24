"""Compile natural-language SAP requests from saved UiMap graphs only."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping

from sap_cli.dispatchers.registry import COMMAND_REGISTRY
from sap_cli.ui_map.fingerprint import compute_map_fingerprint
from sap_cli.ui_map.schema import (
    CollectionDescriptor,
    SideEffectClass,
    UiComponent,
    UiMap,
    UiState,
)
from sap_cli.ui_map.search import UiMapSearch
from sap_cli.ui_map.store import UiMapStore

from .action_adapter import UnsupportedTransitionError, compile_transition
from .intent import ParsedIntent, parse_intent
from .schema import (
    ExecutionPlan,
    NaturalLanguageRequest,
    PlanStep,
    PlanValidation,
    PlanVariable,
    ResolvedIntent,
)


@dataclass(frozen=True)
class CompilationResult:
    plan: ExecutionPlan | None
    validation: PlanValidation
    intent: ResolvedIntent | None = None


@dataclass(frozen=True)
class _CollectionGoal:
    state: UiState
    component_id: str
    component_sap_id: str
    component_type: str
    collection_kind: str
    command: str
    params: dict[str, Any]
    transitions: tuple[Any, ...]
    score: int


@dataclass(frozen=True)
class _InputBinding:
    variable_name: str
    state: UiState
    component: UiComponent
    score: int


class PlanCompiler:
    """Pure compiler with no SAP session, scanner, or COM dependency."""

    def __init__(self, map_store: UiMapStore, recipe_store: Any | None = None) -> None:
        self.map_store = map_store
        self.recipe_store = recipe_store

    def compile(
        self,
        request: NaturalLanguageRequest | str,
        *,
        current_state_id: str = "",
        goal_query: str = "",
        map_id: str = "",
        runtime_versions: dict[str, str] | None = None,
    ) -> CompilationResult:
        req = (
            request
            if isinstance(request, NaturalLanguageRequest)
            else NaturalLanguageRequest(text=str(request))
        )
        parsed = parse_intent(req.text)
        tcode = (req.tcode_hint or parsed.tcode).strip().upper()
        if not tcode:
            return self._needs("tcode_missing", "실행할 TCode가 필요합니다.")
        intent = self._resolved_intent(req, parsed, tcode)
        if parsed.operation == "unknown":
            return self._needs(
                "operation_ambiguous",
                "조회, 변경, 생성, 저장 등 수행할 작업을 구체적으로 알려 주세요.",
                intent,
            )
        target_query = goal_query.strip() or " ".join(parsed.goal_terms).strip()
        requested_map_id = map_id or str(req.context.get("map_id", ""))
        loaded = self._load_planning_map(
            tcode=tcode,
            system_id=req.system_id,
            client=req.client,
            requested_map_id=requested_map_id,
            current_state_id=current_state_id,
            goal_query=target_query,
            operation=parsed.operation,
        )
        if loaded is None:
            return self._needs(
                "map_missing", f"{tcode}의 저장된 UI 지도가 필요합니다.", intent
            )
        if loaded == "ambiguous":
            return self._needs(
                "map_ambiguous",
                "환경 또는 map_id를 지정해 사용할 지도를 선택해야 합니다.",
                intent,
            )
        ui_map, map_fingerprint = loaded
        stale = self.map_store.stale_reasons(ui_map, **(runtime_versions or {}))
        if stale:
            return CompilationResult(
                None,
                PlanValidation(
                    valid=False,
                    errors=["saved_map_is_stale"],
                    warnings=[json.dumps(stale, ensure_ascii=False, sort_keys=True)],
                ),
                intent,
            )
        if self._map_has_unresolved_interactions(ui_map):
            return self._needs(
                "map_build_incomplete",
                "지도 작성이 사용자 입력을 기다리고 있습니다. 지도 작업을 먼저 이어서 완료해 주세요.",
                intent,
            )

        start = self._start_state(ui_map, current_state_id)
        if start is None:
            return self._needs(
                "start_state_missing", "지도에서 시작 상태를 결정할 수 없습니다.", intent
            )
        (
            collection_goal,
            goal_state,
            goal_component,
            transitions,
            collection_error,
            goal_error,
        ) = self._resolve_goal(ui_map, start, target_query, parsed.operation)
        if collection_error:
            messages = {
                "collection_target_ambiguous": (
                    "같은 이름으로 일치하는 열이나 트리 노드가 여러 개입니다. "
                    "대상 control, 열 ID 또는 노드 경로를 지정해 주세요."
                ),
                "collection_target_requires_user_guidance": (
                    "대상 열이나 트리 노드로 가는 저장 경로에 사용자 수동 이동이 필요합니다."
                ),
            }
            return self._needs(collection_error, messages[collection_error], intent)
        if goal_error:
            if goal_error == "goal_selection_required":
                return self._needs(
                    goal_error,
                    "저장된 지도에서 같은 목표 후보가 여러 개입니다. 실행 전에 하나를 선택해 주세요.",
                    intent,
                    question=self._goal_question(ui_map, start, target_query),
                )
            return self._needs(
                goal_error,
                "목표 화면으로 가는 저장 경로에는 사용자 수동 이동이 필요합니다.",
                intent,
            )
        if goal_state is None:
            for expanded_map in self._widen_graph_slices(
                ui_map,
                current_state_id=current_state_id,
                goal_query=target_query,
            ):
                expanded_start = self._start_state(expanded_map, current_state_id)
                if expanded_start is None:
                    continue
                (
                    expanded_collection_goal,
                    expanded_goal,
                    expanded_component,
                    expanded_transitions,
                    expanded_collection_error,
                    expanded_goal_error,
                ) = self._resolve_goal(
                    expanded_map, expanded_start, target_query, parsed.operation
                )
                if expanded_collection_error:
                    return self._needs(
                        expanded_collection_error,
                        {
                            "collection_target_ambiguous": (
                                "같은 이름으로 일치하는 열이나 트리 노드가 여러 개입니다. "
                                "대상 control, 열 ID 또는 노드 경로를 지정해 주세요."
                            ),
                            "collection_target_requires_user_guidance": (
                                "대상 열이나 트리 노드로 가는 저장 경로에 사용자 수동 이동이 필요합니다."
                            ),
                        }[expanded_collection_error],
                        intent,
                    )
                if expanded_goal_error:
                    if expanded_goal_error == "goal_selection_required":
                        return self._needs(
                            expanded_goal_error,
                            "저장된 지도에서 같은 목표 후보가 여러 개입니다. 실행 전에 하나를 선택해 주세요.",
                            intent,
                            question=self._goal_question(
                                expanded_map, expanded_start, target_query
                            ),
                        )
                    return self._needs(
                        expanded_goal_error,
                        "목표 화면으로 가는 저장 경로에는 사용자 수동 이동이 필요합니다.",
                        intent,
                    )
                if expanded_goal is not None:
                    ui_map = expanded_map
                    start = expanded_start
                    collection_goal = expanded_collection_goal
                    goal_state = expanded_goal
                    goal_component = expanded_component
                    transitions = expanded_transitions
                    break
        if goal_state is None:
            # The fallback parser deliberately retains every meaningful word
            # from a natural-language request.  A saved control label often
            # contains only a contiguous part of that phrase (for example,
            # ``자재 기본 데이터`` -> ``기본 데이터``).  Use a shorter phrase
            # only when it identifies one reachable structural target; never
            # let a vague partial match choose an arbitrary screen.
            if not goal_query.strip():
                alternative = self._unique_goal_phrase(
                    ui_map, start, parsed.goal_terms, parsed.operation
                )
                if alternative is not None:
                    (
                        collection_goal,
                        goal_state,
                        goal_component,
                        transitions,
                        _collection_error,
                        _goal_error,
                    ) = alternative
        if goal_state is None:
            coverage_question = self._coverage_guidance_question(
                ui_map, start, target_query
            )
            if coverage_question is not None:
                return self._needs(
                    "goal_semantic_inference_required",
                    "부분 지도만으로 목표를 확정할 수 없습니다. AI가 요청과 구조 후보의 "
                    "의미를 비교해 관련 분기 하나를 선택하고 그 분기만 보강해야 합니다.",
                    intent,
                    question=coverage_question,
                )
            return self._needs(
                "goal_ambiguous",
                "목표 화면이나 UI 요소를 더 구체적으로 알려 주세요.",
                intent,
                question=self._goal_question(ui_map, start),
            )

        variables = self._variables(req, parsed)
        path_states = self._path_states(ui_map, start, transitions)
        bindings, input_errors, input_questions = self._resolve_input_bindings(
            path_states, variables
        )
        for state in path_states:
            input_errors.extend(self._missing_required_inputs(state, variables))

        steps: list[PlanStep] = []
        errors: list[str] = []
        order = 1
        if not current_state_id:
            definition = COMMAND_REGISTRY["run"]
            steps.append(
                PlanStep(
                    step_id="navigate-tcode",
                    order=order,
                    command="run",
                    tool=definition.tool_name,
                    params={"tcode": tcode},
                    expected_to_fingerprint=start.compute_fingerprint(),
                    side_effect=SideEffectClass.NAVIGATION,
                    risk="medium",
                    retry={"max_attempts": 1},
                    map_id=ui_map.manifest.map_id,
                    map_version=ui_map.manifest.map_version,
                    map_fingerprint=map_fingerprint,
                )
            )
            order += 1

        input_steps = self._input_steps_for_state(
            ui_map, start, bindings.get(start.state_id, []), order, map_fingerprint
        )
        steps.extend(input_steps)
        order += len(input_steps)

        for transition in transitions:
            source = ui_map.states.get(transition.from_state_id)
            target = ui_map.states.get(transition.to_state_id)
            if source is None or target is None:
                errors.append(f"transition_state_missing:{transition.transition_id}")
                continue
            try:
                action = compile_transition(transition, source)
            except UnsupportedTransitionError as exc:
                errors.append(f"{transition.transition_id}:{exc}")
                continue
            steps.append(
                PlanStep(
                    step_id=f"map-{transition.transition_id}",
                    order=order,
                    command=action.command,
                    tool=action.tool,
                    target_logical_id=transition.target_logical_id,
                    target_locator=transition.target_locator,
                    params=action.params,
                    expected_from_fingerprint=source.compute_fingerprint(),
                    expected_to_fingerprint=target.compute_fingerprint(),
                    guards=transition.guards,
                    effects=transition.expected_effects,
                    status=transition.expected_status,
                    modal={"signature": transition.expected_modal_signature}
                    if transition.expected_modal_signature
                    else {},
                    side_effect=action.side_effect,
                    risk=action.risk,
                    approval_required=action.approval_required,
                    timeout_seconds=transition.timeout_seconds,
                    idempotent=transition.idempotent,
                    retry=transition.retry,
                    recovery=transition.recovery,
                    commit_boundary=action.side_effect
                    in {
                        SideEffectClass.COMMIT,
                        SideEffectClass.DESTRUCTIVE,
                        SideEffectClass.EXTERNAL,
                    },
                    map_transition_id=transition.transition_id,
                    map_id=ui_map.manifest.map_id,
                    map_version=ui_map.manifest.map_version,
                    map_fingerprint=map_fingerprint,
                )
            )
            order += 1

            input_steps = self._input_steps_for_state(
                ui_map, target, bindings.get(target.state_id, []), order, map_fingerprint
            )
            steps.extend(input_steps)
            order += len(input_steps)

        if collection_goal is not None and not errors:
            definition = COMMAND_REGISTRY[collection_goal.command]
            side_effect = (
                SideEffectClass.READ_ONLY
                if collection_goal.command == "ui_read_table"
                else SideEffectClass.REVERSIBLE
            )
            steps.append(
                PlanStep(
                    step_id="collection-target",
                    order=order,
                    command=collection_goal.command,
                    tool=definition.tool_name,
                    target_logical_id=collection_goal.component_id,
                    target_locator={
                        "sap_id": collection_goal.component_sap_id,
                        "component_type": collection_goal.component_type,
                        "collection_kind": collection_goal.collection_kind,
                    },
                    params=collection_goal.params,
                    expected_from_fingerprint=goal_state.compute_fingerprint(),
                    side_effect=side_effect,
                    idempotent=True,
                    risk=definition.risk_level,
                    retry={
                        "max_attempts": 3
                        if side_effect == SideEffectClass.READ_ONLY
                        else 2
                    },
                    map_id=ui_map.manifest.map_id,
                    map_version=ui_map.manifest.map_version,
                    map_fingerprint=map_fingerprint,
                )
            )

        operation_error = self._operation_validation_error(
            parsed.operation, transitions, collection_goal, ui_map
        )
        if operation_error:
            errors.append(operation_error)

        unresolved = sorted(set(input_errors))
        questions = [
            input_questions.get(item, self._question(item)) for item in unresolved
        ]
        plan = ExecutionPlan(
            plan_id=self._plan_id(req.text, ui_map, start.state_id, goal_state.state_id),
            original_request=self._redacted_request(req.text, parsed),
            tcode=tcode,
            map_id=ui_map.manifest.map_id,
            map_scope=self._scope(ui_map),
            map_version=ui_map.manifest.map_version,
            map_fingerprint=map_fingerprint,
            recipe_ids=self._verified_recipe_ids(tcode, req.text),
            start_state_id=start.state_id,
            goal_state_id=goal_state.state_id,
            goal_component_id=goal_component,
            variables=variables,
            unresolved_inputs=unresolved,
            questions=questions,
            preconditions=[
                {"kind": "map_version", "expected": ui_map.manifest.map_version},
                {"kind": "start_fingerprint", "expected": start.compute_fingerprint()},
            ],
            scan_policy="fast",
            steps=steps,
            success_criteria=req.success_criteria or intent.success_criteria,
            # The normal daemon path is deliberately scan-free.  A changed UI
            # state fails closed and requires a new preview; an operator may
            # explicitly request adaptive/full verification in a separate run.
            recovery={"on_drift": "fail_closed_repreview_required", "full_rescan": False},
        )
        requires_approval = any(step.approval_required for step in plan.steps)
        validation = PlanValidation(
            valid=not errors and not unresolved,
            plan_hash=plan.plan_hash,
            errors=errors,
            unresolved_inputs=unresolved,
            questions=questions,
            requires_approval=requires_approval,
            required_approvals=[
                step.step_id for step in plan.steps if step.approval_required
            ],
        )
        return CompilationResult(plan, validation, intent)

    def _verified_recipe_ids(self, tcode: str, query: str) -> list[str]:
        if self.recipe_store is None:
            return []
        finder = getattr(self.recipe_store, "query_for_ai", None)
        if not callable(finder):
            return []
        results = finder(tcode=tcode, keywords=query, include_disabled=False)
        verified: list[tuple[float, str]] = []
        for score, recipe in results:
            successes = int(getattr(recipe, "success_count", 0) or 0)
            failures = int(getattr(recipe, "failure_count", 0) or 0)
            total = successes + failures
            if successes < 1 or (total and failures / total >= 0.5):
                continue
            verified.append((float(score), str(recipe.id)))
        return [recipe_id for _, recipe_id in sorted(verified, key=lambda item: (-item[0], item[1]))]

    def _load_planning_map(
        self,
        *,
        tcode: str,
        system_id: str,
        client: str,
        requested_map_id: str,
        current_state_id: str,
        goal_query: str,
        operation: str,
    ) -> tuple[UiMap, str] | str | None:
        """Load only the graph needed for planning when the store supports it.

        A graph slice is deliberately not a new map version.  Its content is
        sufficient for route search and step construction, but its fingerprint
        must *always* remain the full immutable snapshot fingerprint from the
        small latest-head pointer.  The executor therefore continues to reject
        a plan after any change anywhere in the saved map, even when that
        changed area was not loaded for this plan.

        Older stores retain the full-map path until they are upgraded.
        """

        list_heads = getattr(self.map_store, "list_latest_heads", None)
        load_slice = getattr(self.map_store, "load_graph_slice", None)
        if callable(list_heads) and callable(load_slice):
            heads = list(
                list_heads(
                    tcode=tcode,
                    system_id=system_id,
                    client=client,
                )
            )
            if requested_map_id:
                heads = [
                    item
                    for item in heads
                    if str(
                        (item.get("scope", {}) if isinstance(item, Mapping) else {}).get(
                            "map_id", ""
                        )
                    )
                    == requested_map_id
                ]
            if not heads:
                return None
            if len(heads) > 1:
                return "ambiguous"
            head = heads[0]
            if not isinstance(head, Mapping) or not isinstance(head.get("scope"), Mapping):
                raise ValueError("UI map latest head is invalid")
            scope = head["scope"]
            map_id = str(scope.get("map_id", ""))
            version = head.get("version")
            fingerprint = str(head.get("content_fingerprint", ""))
            if (
                not map_id
                or isinstance(version, bool)
                or not isinstance(version, int)
                or version < 1
                or not re.fullmatch(r"[0-9a-f]{64}", fingerprint)
            ):
                raise ValueError("UI map latest head is incomplete")
            exact_scope = {
                "tcode": str(scope.get("tcode", "")),
                "system_id": str(scope.get("system_id", "")),
                "client": str(scope.get("client", "")),
                "language": str(scope.get("language", "")),
            }
            # A direct graph-index hit gives the store a precise stopping
            # state.  Passing the raw query can otherwise stop at the root
            # when, for example, a tab caption repeats the target text.
            target_state_ids: list[str] = []
            search_graph = getattr(self.map_store, "search_graph", None)
            if callable(search_graph) and goal_query.strip():
                for hit in search_graph(
                    map_id,
                    goal_query,
                    version=version,
                    **exact_scope,
                    limit=16,
                ):
                    state_id = str(getattr(hit, "state_id", "")).strip()
                    if (
                        state_id
                        and state_id != current_state_id
                        and state_id not in target_state_ids
                    ):
                        target_state_ids.append(state_id)
            slice_hints: dict[str, Any] = {
                "start_state_id": current_state_id,
                "goal_query": goal_query,
                "operation": operation,
            }
            if target_state_ids:
                slice_hints.update(
                    goal_query="", operation="", goal_state_ids=target_state_ids[:1]
                )
            ui_map = load_slice(map_id, version, **exact_scope, **slice_hints)
            if not isinstance(ui_map, UiMap):
                raise TypeError("UI map graph slice must return UiMap")
            manifest = ui_map.manifest
            expected_identity = (
                map_id,
                exact_scope["tcode"].upper(),
                exact_scope["system_id"].upper(),
                exact_scope["client"],
                exact_scope["language"].upper(),
                version,
            )
            actual_identity = (
                manifest.map_id,
                manifest.tcode.upper(),
                manifest.system_id.upper(),
                manifest.client,
                manifest.language.upper(),
                manifest.map_version,
            )
            if actual_identity != expected_identity:
                raise ValueError("UI map graph slice identity mismatch")
            return ui_map, fingerprint

        candidates = self.map_store.list_latest(
            tcode=tcode,
            system_id=system_id,
            client=client,
        )
        if requested_map_id:
            candidates = [
                item for item in candidates if item.manifest.map_id == requested_map_id
            ]
        if not candidates:
            return None
        if len(candidates) > 1:
            return "ambiguous"
        ui_map = candidates[0]
        return ui_map, self._map_fingerprint(ui_map)

    def _map_has_unresolved_interactions(self, ui_map: UiMap) -> bool:
        """Check map readiness without expanding a graph slice when possible."""

        checker = getattr(self.map_store, "has_unresolved_interactions", None)
        if callable(checker):
            manifest = ui_map.manifest
            try:
                return bool(
                    checker(
                        manifest.map_id,
                        manifest.map_version,
                        tcode=manifest.tcode,
                        system_id=manifest.system_id,
                        client=manifest.client,
                        language=manifest.language,
                    )
                )
            except Exception:
                # Fail closed below when the selected slice itself carries an
                # unresolved prompt.  Stores predating the readiness index
                # remain compatible with the original slice API.
                pass
        return any(item.status == "pending" for item in ui_map.pending_interactions)

    @staticmethod
    def _coverage_guidance_question(
        ui_map: UiMap, start: UiState, goal_query: str = ""
    ) -> dict[str, Any] | None:
        """Expose closed, value-free gap candidates for semantic AI routing.

        The compiler cannot safely invent an unobserved destination.  It can,
        however, give the calling LLM enough structural context to decide which
        single branch is relevant and start a narrowly scoped augmentation.
        """

        incomplete_markers = {
            "partial",
            "metadata_only",
            "not_observed",
            "unobserved",
            "unknown",
            "not_sampled",
        }
        gaps: list[dict[str, str]] = []
        for gap in ui_map.manifest.unreachable:
            if gap.get("resolved") is True:
                continue
            state_id = str(gap.get("state_id", ""))
            if state_id and state_id not in ui_map.states:
                continue
            gaps.append(
                {
                    "kind": "transition",
                    "state_id": state_id or start.state_id,
                    "reason": str(gap.get("reason", "unobserved_transition")),
                }
            )
        for state in ui_map.states.values():
            for collection in state.collections:
                for area, status in collection.coverage.items():
                    normalized = str(status).casefold()
                    if not any(marker in normalized for marker in incomplete_markers):
                        continue
                    gaps.append(
                        {
                            "kind": "collection",
                            "state_id": state.state_id,
                            "reason": (
                                f"{collection.component_logical_id}:{area}:{status}"
                            ),
                        }
                    )
        if not gaps:
            return None
        # Keep prompts bounded and value-free.  The MCP client can use a
        # returned state_id directly with sap_map_augment_start.
        raw_choices = sorted(
            {
                (item["kind"], item["state_id"], item["reason"])
                for item in gaps
            }
        )[:12]
        choices: list[dict[str, Any]] = []
        for kind, state_id, reason in raw_choices:
            state = ui_map.states.get(state_id)
            labels: list[str] = []
            controls: list[dict[str, str]] = []
            if state is not None:
                labels.extend(
                    str(value)
                    for value in (state.title, state.program, state.screen_number)
                    if value
                )
                for component in state.components:
                    if component.sensitive or component.visible is False:
                        continue
                    label = component.text or component.name or component.tooltip
                    if (
                        component.type
                        in {
                            "GuiLabel", "GuiTab", "GuiButton", "GuiMenu",
                            "GuiTitlebar", "GuiStatusbar",
                        }
                        and label
                        and label not in labels
                    ):
                        labels.append(label)
                    if (
                        component.type in {"GuiTab", "GuiButton", "GuiMenu"}
                        and component.enabled is not False
                        and label
                    ):
                        controls.append(
                            {
                                "logical_id": component.logical_id,
                                "label": label,
                                "type": component.type,
                            }
                        )
                    if len(labels) >= 20 and len(controls) >= 8:
                        break
            identity = json.dumps(
                [ui_map.manifest.map_id, kind, state_id, reason],
                ensure_ascii=False,
                separators=(",", ":"),
            )
            candidate_id = "gap-" + hashlib.sha256(identity.encode()).hexdigest()[:16]
            augmentation_request = (
                f"계획 목표 '{goal_query}'와 관련된 미관측 분기 확인"
                if goal_query
                else "계획 목표와 관련된 미관측 분기 확인"
            )
            choices.append(
                {
                    "candidate_id": candidate_id,
                    "kind": kind,
                    "state_id": state_id,
                    "reason": reason,
                    "screen_context": labels[:20],
                    "actionable_controls": controls[:8],
                    "augment_args": {
                        "map_id": ui_map.manifest.map_id,
                        "tcode": ui_map.manifest.tcode,
                        "from_state_id": state_id,
                        "request": augmentation_request,
                    },
                }
            )
        return {
            "code": "goal_semantic_inference_required",
            "question": (
                "자연어 목표와 각 후보의 screen_context/actionable_controls를 의미적으로 "
                "비교해 가장 관련 있는 candidate_id 하나를 선택하세요."
            ),
            "map_id": ui_map.manifest.map_id,
            "tcode": ui_map.manifest.tcode,
            "from_state_id": start.state_id,
            "goal_query": goal_query,
            "decision_owner": "calling_llm",
            "instructions": [
                "규칙 기반 문자열 일치만 사용하지 말고 화면 문맥과 사용자 요청의 의미를 판단합니다.",
                "후보 하나가 충분히 관련 있으면 사용자에게 다시 묻지 말고 해당 augment_args로 sap_map_augment_start를 호출합니다.",
                "후보가 없거나 둘 이상이 비슷하면 candidate_id와 화면 설명을 사용자에게 보여주고 선택을 요청합니다.",
                "미관측 목적지를 실행 계획에 추측해 넣지 않습니다. 지도 보강 완료 후 동일 요청으로 sap_plan_preview를 다시 호출합니다.",
            ],
            "choices": choices,
        }

    @staticmethod
    def _resolved_intent(
        request: NaturalLanguageRequest, parsed: ParsedIntent, tcode: str
    ) -> ResolvedIntent:
        values = dict(parsed.inputs)
        values.update(request.supplied_values)
        return ResolvedIntent(
            request_id=request.request_id,
            tcode=tcode,
            operation=parsed.operation,
            entities=list(parsed.goal_terms),
            values=values,
            success_criteria=request.success_criteria or list(parsed.success_criteria),
            ambiguities=[{"code": item} for item in parsed.ambiguities if item != "tcode_missing"],
            confidence=1.0 if not parsed.ambiguities else 0.6,
            provenance={"parser": "deterministic_fallback"},
        )

    @staticmethod
    def _start_state(ui_map: UiMap, requested: str) -> UiState | None:
        if requested:
            return ui_map.states.get(requested)
        incoming = {
            transition.to_state_id
            for transition in ui_map.transitions
            if transition.to_state_id
        }
        roots = sorted(set(ui_map.states) - incoming)
        state_id = roots[0] if roots else next(iter(sorted(ui_map.states)), "")
        return ui_map.states.get(state_id)

    def _resolve_goal(
        self,
        ui_map: UiMap,
        start: UiState,
        query: str,
        operation: str,
    ) -> tuple[_CollectionGoal | None, UiState | None, str, list[Any], str, str]:
        """Resolve a goal from the currently loaded graph slice.

        Keeping this small operation separate lets :meth:`compile` retry only
        a bounded, wider graph slice when a partial slice has no answer.  It
        must not turn an incomplete slice into a user-facing ambiguity.
        """

        collection_goal, collection_error = self._collection_goal(
            ui_map, start.state_id, query
        )
        if collection_error:
            return None, None, "", [], collection_error, ""
        if collection_goal is not None:
            return (
                collection_goal,
                collection_goal.state,
                collection_goal.component_id,
                list(collection_goal.transitions),
                "",
                "",
            )
        goal_state, goal_component, transitions, goal_error = self._goal_path(
            ui_map, start.state_id, query, operation
        )
        return None, goal_state, goal_component, transitions, "", goal_error

    def _unique_goal_phrase(
        self,
        ui_map: UiMap,
        start: UiState,
        terms: tuple[str, ...],
        operation: str,
    ) -> tuple[_CollectionGoal | None, UiState | None, str, list[Any], str, str] | None:
        """Resolve one unique, shorter contiguous phrase from user intent.

        This is intentionally a narrow recovery path for the deterministic
        parser, not semantic guessing.  Single-word fallbacks are excluded:
        they are usually too broad in SAP transactions and should be resolved
        through the interactive choices instead.
        """

        phrases = self._goal_phrases(terms)
        index = UiMapSearch(ui_map)
        for phrase in phrases:
            component_paths = index.paths_to_component(
                phrase,
                from_state_id=start.state_id,
                limit=None,
                excluded_actions={"user_guided_navigation"},
            )
            component_targets = {
                (item.state_id, item.component.logical_id) for item in component_paths
            }
            state_targets = {
                item.state_id
                for item in index.search_states(phrase, limit=None)
                if index.shortest_path(
                    start.state_id,
                    item.state_id,
                    excluded_actions={"user_guided_navigation"},
                )
                is not None
            }
            # Component matches are more precise than a matching screen title.
            # Do not count that title as a second candidate for the same
            # structural destination.
            targets = (
                set(component_targets)
                if component_targets
                else {(state_id, "") for state_id in state_targets}
            )
            if len(targets) != 1:
                continue
            resolved = self._resolve_goal(ui_map, start, phrase, operation)
            if resolved[1] is not None and not resolved[4] and not resolved[5]:
                return resolved
        return None

    @staticmethod
    def _goal_phrases(terms: tuple[str, ...]) -> tuple[str, ...]:
        """Return meaningful contiguous fallback phrases, most specific first."""

        values = [item.strip() for item in terms if item.strip()]
        phrases: list[str] = []
        for size in range(len(values) - 1, 1, -1):
            for start in range(len(values) - size + 1):
                phrase = " ".join(values[start : start + size])
                if phrase not in phrases:
                    phrases.append(phrase)
        return tuple(phrases)

    @staticmethod
    def _goal_question(
        ui_map: UiMap, start: UiState, query: str = ""
    ) -> dict[str, Any]:
        """Offer replayable saved destinations, preferring the tied goal matches."""

        index = UiMapSearch(ui_map)
        target_ids: set[tuple[str, str]] = set()
        if query.strip():
            matches = index.search_components(query, limit=None)
            if matches:
                best_score = max(item.score for item in matches)
                target_ids = {
                    (item.state_id, item.entity_id)
                    for item in matches
                    if item.score == best_score
                }
        choices: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for state_id in sorted(ui_map.states):
            path = (
                []
                if state_id == start.state_id
                else index.shortest_path(
                    start.state_id,
                    state_id,
                    excluded_actions={"user_guided_navigation"},
                )
            )
            if path is None:
                continue
            state = ui_map.states[state_id]
            for component in sorted(
                state.components, key=lambda item: (item.logical_id, item.sap_id)
            ):
                if component.sensitive or component.visible is False:
                    continue
                if target_ids and (state_id, component.logical_id) not in target_ids:
                    continue
                label = component.text or component.name or component.tooltip
                if not label or not component.logical_id:
                    continue
                key = (state_id, component.logical_id)
                if key in seen:
                    continue
                seen.add(key)
                choices.append(
                    {
                        "state_id": state_id,
                        "logical_id": component.logical_id,
                        "label": label,
                        "type": component.type,
                        "use_goal_query": component.logical_id,
                        "path_transition_ids": [
                            item.transition_id for item in path
                        ],
                    }
                )
                if len(choices) >= 12:
                    break
            if len(choices) >= 12:
                break
        selection_required = bool(target_ids)
        return {
            "code": "goal_selection_required" if selection_required else "goal_ambiguous",
            "question": (
                "저장된 지도에서 일치하는 목표가 여러 개입니다. 하나를 선택해 주세요. "
                if selection_required
                else "저장된 지도에서 목표를 하나 선택해 주세요. "
            ) + (
                "선택지의 use_goal_query 값을 다음 계획 요청의 목표로 지정하면 됩니다."
            ),
            "choices": choices,
        }

    def _widen_graph_slices(
        self,
        ui_map: UiMap,
        *,
        current_state_id: str,
        goal_query: str,
    ) -> tuple[UiMap, ...]:
        """Return a few wider graph slices after an unresolved partial read.

        A graph index has intentionally no business values and does not index
        collection column/node metadata.  Therefore a valid goal can be absent
        from the first small slice.  Retry progressively and remain bounded;
        this is still a graph read, never a fallback to a complete snapshot.

        When index hits are available, request one target state at a time.
        Passing the original text back as ``goal_query`` would let a matching
        root tab stop the store BFS immediately, even if the real target is
        deeper in the route.
        """

        if not goal_query.strip():
            return ()
        load_slice = getattr(self.map_store, "load_graph_slice", None)
        if not callable(load_slice):
            return ()
        manifest = ui_map.manifest
        scope = {
            "tcode": manifest.tcode,
            "system_id": manifest.system_id,
            "client": manifest.client,
            "language": manifest.language,
        }
        start_state_id = current_state_id or self._start_state(ui_map, "").state_id

        candidates: list[str] = []
        search_graph = getattr(self.map_store, "search_graph", None)
        if callable(search_graph):
            hits = search_graph(
                manifest.map_id,
                goal_query,
                version=manifest.map_version,
                **scope,
                limit=16,
            )
            for hit in hits:
                state_id = str(getattr(hit, "state_id", "")).strip()
                if state_id and state_id != start_state_id and state_id not in candidates:
                    candidates.append(state_id)

        target_sets = [(state_id,) for state_id in candidates[:4]] or [()]
        slices: list[UiMap] = []
        for max_states, max_depth in ((160, 24), (384, 48)):
            for goal_state_ids in target_sets:
                candidate = load_slice(
                    manifest.map_id,
                    manifest.map_version,
                    **scope,
                    start_state_id=start_state_id,
                    # Do not let a self-matching root stop the forward search.
                    goal_query="",
                    operation="",
                    goal_state_ids=goal_state_ids,
                    max_states=max_states,
                    max_depth=max_depth,
                    neighborhood=1,
                )
                if not isinstance(candidate, UiMap):
                    raise TypeError("UI map graph slice must return UiMap")
                slices.append(candidate)
        return tuple(slices)

    @staticmethod
    def _goal_path(
        ui_map: UiMap, start_state_id: str, query: str, operation: str
    ) -> tuple[UiState | None, str, list[Any], str]:
        index = UiMapSearch(ui_map)
        if query:
            paths = index.paths_to_component(
                query,
                from_state_id=start_state_id,
                limit=50,
                excluded_actions={"user_guided_navigation"},
            )
            if paths:
                replayable_paths = [item for item in paths if item.transitions]
                candidates = replayable_paths or paths
                scores = {
                    (item.state_id, item.entity_id): item.score
                    for item in index.search_components(query, limit=None)
                }
                best_score = max(
                    scores.get((item.state_id, item.component.logical_id), 0)
                    for item in candidates
                )
                best_paths = [
                    item
                    for item in candidates
                    if scores.get((item.state_id, item.component.logical_id), 0)
                    == best_score
                ]
                if len(
                    {
                        (item.state_id, item.component.logical_id)
                        for item in best_paths
                    }
                ) > 1:
                    return None, "", [], "goal_selection_required"
                item = best_paths[0]
                transitions = list(item.transitions)
                if operation != "display" and not any(
                    PlanCompiler._transition_fulfills_operation(
                        transition,
                        ui_map.states.get(transition.from_state_id),
                        operation,
                    )
                    for transition in transitions
                ):
                    terminal = PlanCompiler._outgoing_terminal_transition(
                        ui_map, item.state_id, item.component, operation
                    )
                    if terminal is not None:
                        transitions.append(terminal)
                        return (
                            ui_map.states[terminal.to_state_id],
                            item.component.logical_id,
                            transitions,
                            "",
                        )
                if any(
                    transition.from_state_id == item.state_id
                    and transition.target_logical_id == item.component.logical_id
                    and PlanCompiler._normalize_query(transition.action)
                    .replace("-", "_")
                    .replace(" ", "_")
                    == "user_guided_navigation"
                    for transition in ui_map.transitions
                ):
                    return None, "", [], "goal_requires_user_guidance"
                return ui_map.states[item.state_id], item.component.logical_id, transitions, ""
            states = index.search_states(query, limit=20)
            manual_state_match = False
            reachable_states: list[tuple[Any, list[Any]]] = []
            for match in states:
                path = index.shortest_path(
                    start_state_id,
                    match.state_id,
                    excluded_actions={"user_guided_navigation"},
                )
                if path is not None:
                    reachable_states.append((match, path))
                    continue
                if index.shortest_path(start_state_id, match.state_id) is not None:
                    manual_state_match = True
            if reachable_states:
                best_score = max(match.score for match, _ in reachable_states)
                best_states = [
                    item for item in reachable_states if item[0].score == best_score
                ]
                if len({match.state_id for match, _ in best_states}) > 1:
                    return None, "", [], "goal_selection_required"
                match, path = best_states[0]
                return ui_map.states[match.state_id], "", path, ""
            manual_paths = index.paths_to_component(
                query, from_state_id=start_state_id, limit=1
            )
            if manual_paths or manual_state_match:
                return None, "", [], "goal_requires_user_guidance"
            return None, "", [], ""
        return None, "", [], ""

    @staticmethod
    def _outgoing_terminal_transition(
        ui_map: UiMap,
        state_id: str,
        component: UiComponent,
        operation: str,
    ) -> Any | None:
        candidates = []
        source = ui_map.states.get(state_id)
        for transition in sorted(ui_map.transitions, key=lambda item: item.transition_id):
            if (
                transition.from_state_id != state_id
                or transition.target_logical_id != component.logical_id
                or not transition.to_state_id
                or transition.to_state_id not in ui_map.states
                or not PlanCompiler._transition_fulfills_operation(
                    transition, source, operation
                )
            ):
                continue
            try:
                compile_transition(transition, source)  # type: ignore[arg-type]
            except UnsupportedTransitionError:
                continue
            candidates.append(transition)
        return candidates[0] if len(candidates) == 1 else None

    @staticmethod
    def _collection_goal(
        ui_map: UiMap, start_state_id: str, query: str
    ) -> tuple[_CollectionGoal | None, str]:
        """Resolve a saved collection column/node to one closed final action."""

        normalized = PlanCompiler._normalize_query(query)
        if not normalized:
            return None, ""
        index = UiMapSearch(ui_map)
        matches: list[_CollectionGoal] = []
        guided_matches = False
        for state_id in sorted(ui_map.states):
            state = ui_map.states[state_id]
            components = {item.logical_id: item for item in state.components}
            path = index.shortest_path(
                start_state_id,
                state_id,
                excluded_actions={"user_guided_navigation"},
            )
            manual_path = (
                index.shortest_path(start_state_id, state_id)
                if path is None
                else None
            )
            if path is None and manual_path is None:
                continue
            user_guided = path is None
            selected_path = manual_path if user_guided else path
            assert selected_path is not None
            for collection in sorted(
                state.collections, key=lambda item: item.component_logical_id
            ):
                component = components.get(collection.component_logical_id)
                if component is None or not component.sap_id:
                    continue
                for score, command, params in PlanCompiler._collection_items(
                    collection, normalized, component.sap_id
                ):
                    if user_guided:
                        guided_matches = True
                        continue
                    matches.append(
                        _CollectionGoal(
                            state=state,
                            component_id=component.logical_id,
                            component_sap_id=component.sap_id,
                            component_type=component.type,
                            collection_kind=collection.kind,
                            command=command,
                            params=params,
                            transitions=tuple(selected_path),
                            score=score,
                        )
                    )
        if not matches:
            return (
                None,
                "collection_target_requires_user_guidance" if guided_matches else "",
            )
        matches.sort(
            key=lambda item: (
                -item.score,
                len(item.transitions),
                item.state.state_id,
                item.component_id,
                json.dumps(item.params, ensure_ascii=False, sort_keys=True),
            )
        )
        best = matches[0]
        tied = [item for item in matches if item.score == best.score]
        identities = {
            (
                item.state.state_id,
                item.component_id,
                item.command,
                json.dumps(item.params, ensure_ascii=False, sort_keys=True),
            )
            for item in tied
        }
        if len(identities) > 1:
            return None, "collection_target_ambiguous"
        return best, ""

    @staticmethod
    def _collection_items(
        collection: CollectionDescriptor, query: str, sap_id: str
    ) -> list[tuple[int, str, dict[str, Any]]]:
        matches: list[tuple[int, str, dict[str, Any]]] = []
        if collection.kind in {"table", "grid", "alv", "apo_grid"}:
            for column in collection.columns:
                if not column.id:
                    continue
                score = PlanCompiler._collection_match_score(
                    query,
                    (
                        column.id,
                        column.title,
                        str(getattr(column, "name", "") or ""),
                    ),
                )
                if score:
                    matches.append(
                        (
                            score,
                            "ui_read_table",
                            {
                                "element_id": sap_id,
                                "column": column.id,
                                "columns": [column.id],
                            },
                        )
                    )
        if collection.kind == "tree":
            for node in collection.nodes:
                if not node.key:
                    continue
                score = PlanCompiler._collection_match_score(
                    query, (node.key, node.text, node.path)
                )
                if score:
                    matches.append(
                        (
                            score,
                            "ui_select_node",
                            {"element_id": sap_id, "node_key": node.key},
                        )
                    )
        return matches

    @staticmethod
    def _collection_match_score(query: str, values: tuple[str, ...]) -> int:
        score = 0
        query_tokens = set(query.split())
        for position, value in enumerate(values):
            normalized = PlanCompiler._normalize_query(value)
            if not normalized:
                continue
            weight = max(1, len(values) - position)
            if normalized == query:
                score = max(score, 1000 + weight)
            elif normalized.startswith(query) or query.startswith(normalized):
                score = max(score, 700 + weight)
            elif query in normalized:
                score = max(score, 500 + weight)
            elif query_tokens and query_tokens <= set(normalized.split()):
                score = max(score, 300 + weight)
        return score

    @staticmethod
    def _normalize_query(value: Any) -> str:
        return " ".join(str(value or "").casefold().split())

    @staticmethod
    def _variables(
        request: NaturalLanguageRequest, parsed: ParsedIntent
    ) -> dict[str, PlanVariable]:
        values = dict(parsed.inputs)
        values.update(request.supplied_values)
        sensitive = set(parsed.sensitive_inputs) | {
            key
            for key in values
            if any(marker in key.casefold() for marker in ("password", "secret", "token", "pin"))
        }
        return {
            key: PlanVariable(
                value=value,
                source="user",
                sensitive=key in sensitive,
            )
            for key, value in sorted(values.items())
        }

    @staticmethod
    def _path_states(
        ui_map: UiMap, start: UiState, transitions: list[Any]
    ) -> list[UiState]:
        """Return states in the exact order in which the saved path reaches them."""

        states = [start]
        for transition in transitions:
            target = ui_map.states.get(transition.to_state_id)
            if target is not None:
                states.append(target)
        return states

    @staticmethod
    def _resolve_input_bindings(
        states: list[UiState], variables: dict[str, PlanVariable]
    ) -> tuple[dict[str, list[_InputBinding]], list[str], dict[str, dict[str, Any]]]:
        """Resolve each variable against every state on the selected map path.

        A unique matching component is required. Stable scoring and sorting are
        used only to order reproducible questions; they never choose one of
        several plausible controls.
        """

        by_state: dict[str, list[_InputBinding]] = {}
        unresolved: list[str] = []
        questions: dict[str, dict[str, Any]] = {}
        for name in sorted(variables):
            candidates: list[_InputBinding] = []
            for state in states:
                for component in state.components:
                    if component.changeable is False or not component.sap_id:
                        continue
                    score = PlanCompiler._input_match_score(name, component)
                    if score:
                        candidates.append(
                            _InputBinding(name, state, component, score)
                        )
            if not candidates:
                unresolved.append(name)
                questions[name] = {
                    "code": "input_target_missing",
                    "field": name,
                    "question": (
                        f"{name} 값을 입력할 필드를 저장 지도 경로에서 찾지 못했습니다. "
                        "대상 화면과 필드를 지정해 주세요."
                    ),
                }
                continue

            best_score = max(item.score for item in candidates)
            best = [item for item in candidates if item.score == best_score]
            identities = {
                (item.state.state_id, item.component.logical_id, item.component.sap_id)
                for item in candidates
            }
            if len(identities) != 1:
                unresolved.append(name)
                ordered = sorted(
                    candidates,
                    key=lambda item: (
                        -item.score,
                        item.state.state_id,
                        item.component.logical_id,
                        item.component.sap_id,
                    ),
                )
                questions[name] = {
                    "code": "input_target_ambiguous",
                    "field": name,
                    "question": (
                        f"{name} 값을 입력할 필드 후보가 여러 개입니다. "
                        "대상 화면과 필드를 선택해 주세요."
                    ),
                    "choices": [
                        {
                            "state_id": item.state.state_id,
                            "logical_id": item.component.logical_id,
                            "sap_id": item.component.sap_id,
                            "name": item.component.name,
                        }
                        for item in ordered
                    ],
                }
                continue

            binding = best[0]
            by_state.setdefault(binding.state.state_id, []).append(binding)

        for bindings in by_state.values():
            bindings.sort(key=lambda item: item.variable_name)
        return by_state, unresolved, questions

    @staticmethod
    def _input_match_score(name: str, component: UiComponent) -> int:
        query = PlanCompiler._normalize_query(name).replace("_", " ")
        if not query:
            return 0
        query_tokens = set(query.split())
        score = 0
        fields = (
            (component.logical_id, 14),
            (component.name, 12),
            (component.tooltip, 10),
            (component.text, 9),
            (component.sap_id, 8),
        )
        for value, weight in fields:
            normalized = PlanCompiler._normalize_query(value).replace("_", " ")
            if not normalized:
                continue
            tokens = set(normalized.split())
            if normalized == query:
                field_score = 1000 + weight
            elif query_tokens and tokens == query_tokens:
                field_score = 900 + weight
            elif normalized.startswith(query) or query.startswith(normalized):
                field_score = 700 + weight
            elif query in normalized:
                field_score = 500 + weight
            elif query_tokens and query_tokens <= tokens:
                field_score = 300 + weight
            else:
                field_score = 0
            score = max(score, field_score)
        return score

    @staticmethod
    def _input_steps_for_state(
        ui_map: UiMap,
        state: UiState,
        bindings: list[_InputBinding],
        first_order: int,
        map_fingerprint: str,
    ) -> list[PlanStep]:
        definition = COMMAND_REGISTRY["ui_set"]
        steps: list[PlanStep] = []
        for binding in bindings:
            component = binding.component
            name = binding.variable_name
            steps.append(
                PlanStep(
                    step_id=f"input-{name}",
                    order=first_order + len(steps),
                    command="ui_set",
                    tool=definition.tool_name,
                    target_logical_id=component.logical_id,
                    target_locator={"sap_id": component.sap_id, "type": component.type},
                    params={"element_id": component.sap_id, "value": f"${{{name}}}"},
                    expected_from_fingerprint=state.compute_fingerprint(),
                    side_effect=SideEffectClass.NAVIGATION,
                    idempotent=True,
                    risk="medium",
                    retry={"max_attempts": 2},
                    map_id=ui_map.manifest.map_id,
                    map_version=ui_map.manifest.map_version,
                    map_fingerprint=map_fingerprint,
                )
            )
        return steps

    @staticmethod
    def _operation_validation_error(
        operation: str,
        transitions: list[Any],
        collection_goal: _CollectionGoal | None,
        ui_map: UiMap,
    ) -> str:
        """Reject plans that claim a business operation after navigation only."""

        if operation == "display":
            return ""
        if collection_goal is not None and operation == "export":
            if collection_goal.command == "ui_export":
                return ""

        for transition in transitions:
            if PlanCompiler._transition_fulfills_operation(
                transition,
                ui_map.states.get(transition.from_state_id),
                operation,
            ):
                return ""
        return f"operation_action_missing:{operation}"

    @staticmethod
    def _operation_aliases(operation: str) -> tuple[str, ...]:
        return {
            "change": ("change", "edit", "modify", "update", "save", "변경", "수정", "저장"),
            "create": ("create", "new", "save", "생성", "등록", "신규", "저장"),
            "delete": ("delete", "remove", "삭제"),
            "export": ("export", "download", "내보내", "다운로드"),
            "post": ("post", "posting", "commit", "전기"),
            "release": ("release", "approve", "승인", "릴리즈"),
            "save": ("save", "commit", "저장"),
        }.get(operation, (operation,))

    @staticmethod
    def _transition_fulfills_operation(
        transition: Any,
        source: UiState | None,
        operation: str,
    ) -> bool:
        action = PlanCompiler._normalize_query(transition.action).replace("-", "_")
        if action in {
            "select_tab",
            "select",
            "scroll_to",
            "expand_node",
            "collapse_node",
            "read",
            "inspect",
            "user_guided_navigation",
        }:
            return False
        component = None
        if source is not None:
            component = next(
                (
                    item
                    for item in source.components
                    if item.logical_id == transition.target_logical_id
                ),
                None,
            )
        payload = {
            "action": transition.action,
            "target": transition.target_logical_id,
            "locator": transition.target_locator,
            "params": transition.observed_params,
            "effects": transition.expected_effects,
            "status": transition.expected_status,
            "component": (
                {
                    "logical_id": component.logical_id,
                    "name": component.name,
                    "text": component.text,
                    "tooltip": component.tooltip,
                    "icon_name": component.icon_name,
                }
                if component is not None
                else {}
            ),
        }
        evidence = PlanCompiler._normalize_query(
            json.dumps(payload, ensure_ascii=False, sort_keys=True)
        )
        # SAP's standard save key has stable semantics even when its window has
        # no business caption.  Other keys need explicit map evidence.
        if action in {"vkey", "send_vkey"} and str(
            transition.observed_params.get(
                "key", transition.observed_params.get("vkey", "")
            )
        ) == "11":
            evidence += " save commit 저장"
        tokens = set(re.findall(r"[a-z0-9가-힣]+", evidence.casefold()))
        for alias in PlanCompiler._operation_aliases(operation):
            normalized = PlanCompiler._normalize_query(alias)
            if normalized in tokens or (
                any("가" <= char <= "힣" for char in normalized)
                and normalized in evidence
            ):
                return True
        return False

    @staticmethod
    def _missing_required_inputs(
        state: UiState, variables: dict[str, PlanVariable]
    ) -> list[str]:
        missing: list[str] = []
        supplied = {name.casefold() for name in variables}
        for component in sorted(
            state.components, key=lambda item: (item.logical_id, item.sap_id)
        ):
            if component.required is not True or component.changeable is False:
                continue
            searchable = {
                str(value).casefold()
                for value in (
                    component.logical_id,
                    component.name,
                    component.text,
                    component.tooltip,
                    component.sap_id,
                )
                if value
            }
            if any(
                name in value or value in name
                for name in supplied
                for value in searchable
            ):
                continue
            missing.append(component.name or component.logical_id or component.sap_id)
        return missing

    @staticmethod
    def _map_fingerprint(ui_map: UiMap) -> str:
        return compute_map_fingerprint(ui_map)

    @staticmethod
    def _scope(ui_map: UiMap) -> dict[str, str]:
        manifest = ui_map.manifest
        return {
            "system_id": manifest.system_id,
            "client": manifest.client,
            "language": manifest.language,
        }

    @staticmethod
    def _plan_id(text: str, ui_map: UiMap, start: str, goal: str) -> str:
        payload = [text, ui_map.manifest.map_id, ui_map.manifest.map_version, start, goal]
        return "plan-" + hashlib.sha256(
            json.dumps(payload, ensure_ascii=False).encode()
        ).hexdigest()[:20]

    @staticmethod
    def _question(name: str) -> dict[str, Any]:
        return {
            "field": name,
            "question": f"{name}의 필수 입력값 또는 대상 필드를 확인해 주세요.",
        }

    @staticmethod
    def _redacted_request(text: str, parsed: ParsedIntent) -> str:
        redacted = text
        for name in parsed.sensitive_inputs:
            value = parsed.inputs.get(name)
            if value not in (None, ""):
                redacted = redacted.replace(str(value), "<redacted>")
        return redacted

    @staticmethod
    def _needs(
        code: str,
        message: str,
        intent: ResolvedIntent | None = None,
        *,
        question: dict[str, Any] | None = None,
    ) -> CompilationResult:
        return CompilationResult(
            None,
            PlanValidation(
                valid=False,
                errors=[code],
                warnings=[message],
                questions=[question or {"code": code, "question": message}],
            ),
            intent,
        )


__all__ = ["CompilationResult", "PlanCompiler"]
