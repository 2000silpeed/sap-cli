"""Compile natural-language SAP requests from saved UiMap graphs only."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

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
        candidates = self.map_store.list_latest(
            tcode=tcode,
            system_id=req.system_id,
            client=req.client,
        )
        requested_map_id = map_id or str(req.context.get("map_id", ""))
        if requested_map_id:
            candidates = [
                item for item in candidates if item.manifest.map_id == requested_map_id
            ]
        if not candidates:
            return self._needs(
                "map_missing", f"{tcode}의 저장된 UI 지도가 필요합니다.", intent
            )
        if len(candidates) > 1:
            return self._needs(
                "map_ambiguous",
                "환경 또는 map_id를 지정해 사용할 지도를 선택해야 합니다.",
                intent,
            )
        ui_map = candidates[0]
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

        start = self._start_state(ui_map, current_state_id)
        if start is None:
            return self._needs(
                "start_state_missing", "지도에서 시작 상태를 결정할 수 없습니다.", intent
            )
        target_query = goal_query.strip() or " ".join(parsed.goal_terms).strip()
        collection_goal, collection_error = self._collection_goal(
            ui_map, start.state_id, target_query
        )
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
        if collection_goal is not None:
            goal_state = collection_goal.state
            goal_component = collection_goal.component_id
            transitions = list(collection_goal.transitions)
        else:
            goal_state, goal_component, transitions, goal_error = self._goal_path(
                ui_map, start.state_id, target_query, parsed.operation
            )
            if goal_error:
                return self._needs(
                    goal_error,
                    "목표 화면으로 가는 저장 경로에는 사용자 수동 이동이 필요합니다.",
                    intent,
                )
        if goal_state is None:
            return self._needs(
                "goal_ambiguous",
                "목표 화면이나 UI 요소를 더 구체적으로 알려 주세요.",
                intent,
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
                    map_fingerprint=self._map_fingerprint(ui_map),
                )
            )
            order += 1

        input_steps = self._input_steps_for_state(
            ui_map, start, bindings.get(start.state_id, []), order
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
                    map_fingerprint=self._map_fingerprint(ui_map),
                )
            )
            order += 1

            input_steps = self._input_steps_for_state(
                ui_map, target, bindings.get(target.state_id, []), order
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
                    map_fingerprint=self._map_fingerprint(ui_map),
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
            map_fingerprint=self._map_fingerprint(ui_map),
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
                item = next(
                    (candidate for candidate in paths if candidate.transitions),
                    paths[0],
                )
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
            for match in states:
                path = index.shortest_path(
                    start_state_id,
                    match.state_id,
                    excluded_actions={"user_guided_navigation"},
                )
                if path is not None:
                    return ui_map.states[match.state_id], "", path, ""
                if index.shortest_path(start_state_id, match.state_id) is not None:
                    manual_state_match = True
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
                    map_fingerprint=PlanCompiler._map_fingerprint(ui_map),
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
        code: str, message: str, intent: ResolvedIntent | None = None
    ) -> CompilationResult:
        return CompilationResult(
            None,
            PlanValidation(
                valid=False,
                errors=[code],
                warnings=[message],
                questions=[{"code": code, "question": message}],
            ),
            intent,
        )


__all__ = ["CompilationResult", "PlanCompiler"]
