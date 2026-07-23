"""Policy-bound, platform-independent SAP UI map traversal engine.

Concrete SAP actions intentionally live behind :class:`TraversalAdapter`.
This module only decides *whether* a proposed action may run, records the
resulting graph, restores the baseline, and creates resumable checkpoints.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any, Callable, Iterable, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from .scanner import UiMapScanner
from .schema import (
    MapManifest,
    SideEffectClass,
    Transition,
    TraversalPolicy,
    UiMap,
    UiState,
    UserInputRequest,
)


_FORBIDDEN = {
    SideEffectClass.COMMIT,
    SideEffectClass.DESTRUCTIVE,
    SideEffectClass.EXTERNAL,
}
_SAFE_TRAVERSAL_ACTIONS = {
    "inspect",
    "read",
    "select_tab",
    "scroll_to",
    "expand_node",
    "collapse_node",
    "press",
    "select_menu",
    "press_toolbar_button",
    "select_context_menu_item",
}
_DANGEROUS_MARKER = re.compile(
    r"(?<![a-z])(save|post|delete|release|send|print)(?![a-z])"
    r"|저장|전기|삭제|릴리스|릴리즈|승인|전송|발송|인쇄|출력",
    re.I,
)


# Compatibility name retained for the first traversal API draft.  The durable
# schema model is now the single source of truth for user interaction records.
NeedsUserInput = UserInputRequest


class TraversalCandidate(BaseModel):
    """One adapter-proposed action; it contains no platform implementation."""

    action: str
    target_logical_id: str = ""
    target_locator: dict[str, Any] = Field(default_factory=dict)
    params: dict[str, Any] = Field(default_factory=dict)
    side_effect: SideEffectClass = SideEffectClass.NAVIGATION
    reversible: bool = True
    idempotent: bool = False
    page_cost: int = Field(default=0, ge=0)
    node_cost: int = Field(default=0, ge=0)
    expected_modal_signature: str = ""
    expected_program: str = ""
    expected_screen_number: int | None = Field(default=None, ge=0)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    needs_user_input: NeedsUserInput | None = None


class TraversalCheckpoint(BaseModel):
    """Complete restart boundary, including unresolved human questions."""

    ui_map: UiMap
    frontier: list[str] = Field(default_factory=list)
    processed: list[str] = Field(default_factory=list)
    elapsed_seconds: float = Field(default=0.0, ge=0)
    pages_visited: int = Field(default=0, ge=0)
    nodes_visited: int = Field(default=0, ge=0)
    depths: dict[str, int] = Field(default_factory=dict)
    needs_user_input: list[NeedsUserInput] = Field(default_factory=list)
    resolved_resume_tokens: list[str] = Field(default_factory=list)
    in_flight: dict[str, Any] = Field(default_factory=dict)
    traversal_scope: dict[str, Any] = Field(default_factory=dict)

    def to_json(self) -> str:
        return self.model_dump_json()

    @classmethod
    def from_json(cls, payload: str | bytes) -> "TraversalCheckpoint":
        return cls.model_validate_json(payload)


class TraversalResult(BaseModel):
    ui_map: UiMap
    checkpoint: TraversalCheckpoint
    stop_reason: str = "complete"


@runtime_checkable
class TraversalAdapter(Protocol):
    """Platform boundary implemented by a real or fake SAP action adapter."""

    @property
    def session(self) -> Any: ...

    def discover(self, state: UiState) -> Iterable[TraversalCandidate]: ...

    def execute(self, candidate: TraversalCandidate) -> dict[str, Any] | None: ...

    def restore(self, baseline: UiState) -> None: ...

    def reach(self, state: UiState) -> bool: ...

    def apply_user_input(self, item: UserInputRequest, answer: Any) -> bool: ...


class TraversalEngine:
    """Build an incremental UI graph under a hard policy and budget envelope."""

    def __init__(
        self,
        policy: TraversalPolicy,
        *,
        scanner: UiMapScanner | None = None,
        clock: Callable[[], float] = time.monotonic,
        checkpoint_callback: Callable[[TraversalCheckpoint], None] | None = None,
        semantic_reviewer: Any | None = None,
    ) -> None:
        self.policy = policy
        self.scanner = scanner or UiMapScanner()
        self.clock = clock
        self.checkpoint_callback = checkpoint_callback
        self.semantic_reviewer = semantic_reviewer

    def run(
        self,
        adapter: TraversalAdapter,
        *,
        ui_map: UiMap | None = None,
        manifest: MapManifest | None = None,
        checkpoint: TraversalCheckpoint | None = None,
        answers: dict[str, Any] | None = None,
    ) -> TraversalResult:
        """Traverse or resume, preserving all prior states and transitions."""

        if checkpoint is not None:
            graph = checkpoint.ui_map.model_copy(deep=True)
            frontier = list(checkpoint.frontier)
            processed = set(checkpoint.processed)
            elapsed_before = checkpoint.elapsed_seconds
            pages = checkpoint.pages_visited
            nodes = checkpoint.nodes_visited
            depths = dict(checkpoint.depths)
            pending = list(checkpoint.needs_user_input)
            resolved = set(checkpoint.resolved_resume_tokens)
            traversal_scope = dict(checkpoint.traversal_scope)
        else:
            graph = ui_map.model_copy(deep=True) if ui_map is not None else UiMap(
                manifest=manifest
                or MapManifest(map_id="traversal", tcode="", capture_mode=self.policy.mode)
            )
            frontier, processed, pending, resolved = [], set(), [], set()
            elapsed_before, pages, nodes = 0.0, 0, 0
            depths = {}
            traversal_scope = {}

        started = self.clock()
        answers = answers or {}
        pending = self._apply_answers(
            adapter,
            graph,
            frontier,
            pending,
            answers,
            resolved,
            depths,
            traversal_scope,
        )
        if pending:
            return self._result(
                graph, frontier, processed, elapsed_before, pages, nodes, pending, resolved,
                depths, "needs_user_input", traversal_scope,
            )

        if not frontier:
            initial = self.scanner.scan(adapter.session, tcode=graph.manifest.tcode)
            initial = graph.add_state(initial)
            if not graph.manifest.tcode:
                graph.manifest.tcode = initial.tcode
            frontier.append(initial.state_id)
            depths.setdefault(initial.state_id, 0)

        if self.policy.mode == "passive":
            return self._result(
                graph, [], {frontier[0]}, elapsed_before + self._elapsed(started),
                pages, nodes, [], resolved, depths, "passive_complete",
                traversal_scope,
            )

        stop_reason = "complete"
        while frontier:
            elapsed = elapsed_before + self._elapsed(started)
            if elapsed >= self.policy.budget.max_seconds:
                stop_reason = "max_seconds"
                break
            state_id = frontier.pop(0)
            if state_id in processed:
                continue
            baseline = graph.states.get(state_id)
            if baseline is None:
                self._unreachable(graph, state_id, "checkpoint_state_missing")
                continue
            configure_reach = getattr(adapter, "configure_reach_graph", None)
            if callable(configure_reach):
                configure_reach(graph)
            if not adapter.reach(baseline):
                self._unreachable(graph, state_id, "adapter_could_not_reach_state")
                processed.add(state_id)
                continue
            current = self.scanner.scan(adapter.session, tcode=baseline.tcode)
            if (
                baseline.tcode
                and current.tcode
                and current.tcode.upper() != baseline.tcode.upper()
            ):
                self._unreachable(graph, state_id, "reached_transaction_mismatch")
                processed.add(state_id)
                continue
            if current.compute_fingerprint() != baseline.compute_fingerprint():
                self._unreachable(graph, state_id, "reached_state_fingerprint_mismatch")
                processed.add(state_id)
                continue
            if depths.get(state_id, 0) >= self.policy.budget.max_depth:
                processed.add(state_id)
                continue

            if self._queue_semantic_review(graph, baseline, pending):
                frontier.insert(0, state_id)
                return self._result(
                    graph,
                    frontier,
                    processed,
                    elapsed_before + self._elapsed(started),
                    pages,
                    nodes,
                    pending,
                    resolved,
                    depths,
                    "needs_user_input",
                    traversal_scope,
                )

            blocked_here = False
            candidates: list[TraversalCandidate] = []
            for candidate in adapter.discover(baseline):
                if candidate.needs_user_input is not None:
                    item = candidate.needs_user_input.model_copy(
                        update={"current_state_id": state_id}
                    )
                    if item.resume_token not in resolved:
                        self._add_pending_interaction(graph, pending, item)
                        if isinstance(item.scope, dict):
                            traversal_scope.update(item.scope)
                        blocked_here = True
                    continue
                if not self._scope_permits(traversal_scope, state_id, candidate):
                    continue
                if not self._permitted(candidate, baseline):
                    continue
                # Selecting a tab only changes the active member of an
                # already-known tab strip.  Once that target tab has been
                # scanned as active, executing another sibling -> target
                # switch adds a dense reverse/cross-tab edge but no new UI
                # structure.  It is particularly expensive in SAP because
                # every such switch also requires a target scan and a
                # baseline restore.  Retain the first observed route to each
                # concrete tab and let the alternate tab state be explored
                # for its own collections/branches instead.
                if self._tab_target_is_already_mapped(graph, candidate):
                    continue
                if self._automatic_candidate_already_attempted(
                    traversal_scope, state_id, candidate
                ):
                    continue
                transition_id = self._transition_id(state_id, candidate)
                if any(item.transition_id == transition_id for item in graph.transitions):
                    continue
                candidates.append(candidate)

            # Guided mapping is the deliberately small, inspectable primitive
            # for later automatic traversal: discover the exact same safe
            # candidates, but execute only the one the user selected.
            guided_revisit = False
            if self.policy.mode == "guided" and not blocked_here:
                selected = self._guided_selected_candidate(
                    traversal_scope, state_id, candidates
                )
                if selected is None:
                    if candidates:
                        self._add_pending_interaction(
                            graph,
                            pending,
                            self._guided_candidate_request(baseline, candidates),
                        )
                        blocked_here = True
                    candidates = []
                else:
                    candidates = [selected]
                    guided_revisit = True

            for candidate in candidates:
                transition_id = self._transition_id(state_id, candidate)
                # Stop before touching SAP when no new state can be retained.
                # This keeps the candidate replayable after a larger budget is
                # supplied instead of persisting an edge to a discarded state.
                if len(graph.states) >= self.policy.budget.max_states:
                    stop_reason = "max_states"
                    break
                if len(graph.transitions) >= self.policy.budget.max_transitions:
                    stop_reason = "max_transitions"
                    break
                if pages + candidate.page_cost > self.policy.budget.max_pages_per_collection:
                    stop_reason = "max_pages_per_collection"
                    break
                if nodes + candidate.node_cost > self.policy.budget.max_nodes_per_tree:
                    stop_reason = "max_nodes_per_tree"
                    break
                if elapsed_before + self._elapsed(started) >= self.policy.budget.max_seconds:
                    stop_reason = "max_seconds"
                    break

                self._remember_automatic_candidate(
                    traversal_scope, state_id, candidate
                )

                try:
                    self._emit_checkpoint(
                        graph,
                        [state_id, *frontier],
                        processed,
                        elapsed_before + self._elapsed(started),
                        pages,
                        nodes,
                        pending,
                        resolved,
                        depths,
                        traversal_scope=traversal_scope,
                        in_flight={
                            "transition_id": transition_id,
                            "state_id": state_id,
                            "candidate": candidate.model_dump(mode="json"),
                            "dirty": True,
                        },
                    )
                except Exception:
                    self._unreachable(
                        graph, state_id, "checkpoint_write_failed", candidate.action
                    )
                    stop_reason = "checkpoint_write_failed"
                    break
                transition, discovered, restore_fatal, attempt_stop = self._attempt(
                    adapter, baseline, candidate, graph
                )
                graph.transitions.append(transition)
                pages += candidate.page_cost
                nodes += candidate.node_cost
                if discovered is not None and discovered.state_id not in graph.states:
                    if len(graph.states) >= self.policy.budget.max_states:
                        self._unreachable(graph, state_id, "max_states", candidate.action)
                        stop_reason = "max_states"
                        break
                    graph.add_state(discovered)
                    frontier.append(discovered.state_id)
                    depths[discovered.state_id] = depths.get(state_id, 0) + 1
                if restore_fatal:
                    stop_reason = "baseline_restore_failed"
                    break
                if attempt_stop:
                    stop_reason = attempt_stop
                    break
                try:
                    self._emit_checkpoint(
                        graph, frontier, processed, elapsed_before + self._elapsed(started),
                        pages, nodes, pending, resolved,
                        depths,
                        traversal_scope=traversal_scope,
                    )
                except Exception:
                    self._unreachable(
                        graph, state_id, "checkpoint_write_failed_after_action", candidate.action
                    )
                    stop_reason = "checkpoint_write_failed_after_action"
                    break
            if stop_reason != "complete":
                if state_id not in processed:
                    frontier.insert(0, state_id)
                break
            if blocked_here:
                frontier.insert(0, state_id)
                stop_reason = "needs_user_input"
                break
            if guided_revisit:
                # The selected branch is now recorded, but sibling candidates
                # must remain available for the next user choice.
                if state_id not in frontier:
                    frontier.append(state_id)
            else:
                processed.add(state_id)

        elapsed_total = elapsed_before + self._elapsed(started)
        return self._result(
            graph, frontier, processed, elapsed_total, pages, nodes, pending, resolved,
            depths, stop_reason, traversal_scope,
        )

    def _attempt(
        self,
        adapter: TraversalAdapter,
        baseline: UiState,
        candidate: TraversalCandidate,
        graph: UiMap,
    ) -> tuple[Transition, UiState | None, bool, str]:
        evidence = list(candidate.evidence)
        discovered: UiState | None = None
        failure = ""
        restore_failure = ""
        restored = False
        started = self.clock()
        try:
            result = adapter.execute(candidate)
            if result:
                evidence.append({"adapter_result": result})
            discovered = self.scanner.scan(adapter.session, tcode=baseline.tcode)
            if (
                baseline.tcode
                and discovered.tcode
                and discovered.tcode.upper() != baseline.tcode.upper()
            ):
                failure = "cross_transaction_transition"
                self._unreachable(graph, baseline.state_id, failure, candidate.action)
            modal_changed = discovered.modal_signature != baseline.modal_signature
            modal_unexpected = (
                modal_changed
                and bool(discovered.modal_signature)
                and discovered.modal_signature != candidate.expected_modal_signature
            )
            if not failure and modal_unexpected:
                failure = "unexpected_modal"
                self._unreachable(graph, baseline.state_id, failure, candidate.action)
            elif not failure and (
                candidate.expected_modal_signature
                and discovered.modal_signature != candidate.expected_modal_signature
            ):
                failure = "expected_modal_missing_or_mismatched"
                self._unreachable(graph, baseline.state_id, failure, candidate.action)
            elif (
                candidate.expected_program
                and discovered.program != candidate.expected_program
            ) or (
                candidate.expected_screen_number is not None
                and discovered.screen_number != candidate.expected_screen_number
            ):
                failure = "unexpected_target_state"
                self._unreachable(graph, baseline.state_id, failure, candidate.action)
        except Exception:
            failure = "action_failed"
            evidence.append(
                {"failure_code": "action_failed", "phase": "execute"}
            )
            self._unreachable(graph, baseline.state_id, failure, candidate.action)
        finally:
            try:
                adapter.restore(baseline)
                restored_state = self.scanner.scan(adapter.session, tcode=baseline.tcode)
                restored = restored_state.compute_fingerprint() == baseline.compute_fingerprint()
                if not restored:
                    restore_failure = "baseline_fingerprint_mismatch"
            except Exception:
                restore_failure = "restore_failed"
                evidence.append(
                    {"failure_code": "restore_failed", "phase": "restore"}
                )
            if restore_failure:
                self._unreachable(
                    graph, baseline.state_id, restore_failure, candidate.action
                )
        # An unexpected modal is still valuable map evidence. Preserve the
        # read-only snapshot and transition, then stop before exploring or
        # interacting with it. Other action failures have no trusted target.
        preserved_failures = {
            "unexpected_modal",
            "expected_modal_missing_or_mismatched",
            "unexpected_target_state",
        }
        if failure and failure not in preserved_failures:
            discovered = None
        restore_fatal = bool(restore_failure)
        if restore_fatal:
            discovered = None
        target_state_id = discovered.ensure_state_id().state_id if discovered is not None else ""
        evidence.append({"baseline_restored": restored})
        transition = Transition(
            transition_id=self._transition_id(baseline.state_id, candidate),
            from_state_id=baseline.state_id,
            to_state_id=target_state_id,
            action=candidate.action,
            target_logical_id=candidate.target_logical_id,
            target_locator=candidate.target_locator,
            observed_params=candidate.params,
            expected_modal_signature=candidate.expected_modal_signature,
            side_effect=candidate.side_effect,
            reversible=candidate.reversible,
            idempotent=candidate.idempotent,
            evidence=evidence,
            timing_ms=max(0.0, self._elapsed(started) * 1000.0),
            confidence=1.0 if discovered is not None and restored else 0.0,
        )
        return (
            transition,
            discovered,
            restore_fatal,
            failure if failure in {*(preserved_failures), "cross_transaction_transition"} else "",
        )

    def _permitted(self, candidate: TraversalCandidate, source: UiState) -> bool:
        normalized_action = candidate.action.strip().lower().replace("-", "_")
        if normalized_action not in _SAFE_TRAVERSAL_ACTIONS:
            return False
        if candidate.side_effect in _FORBIDDEN:
            return False
        if candidate.side_effect not in {
            SideEffectClass.READ_ONLY,
            SideEffectClass.REVERSIBLE,
            SideEffectClass.NAVIGATION,
        }:
            return False
        if self.policy.mode in {"guided", "reversible"} and not candidate.reversible:
            return False
        marker_payload = json.dumps(
            {
                "action": candidate.action,
                "target": candidate.target_locator,
                "params": candidate.params,
                "target_logical_id": candidate.target_logical_id,
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        if _DANGEROUS_MARKER.search(marker_payload):
            return False
        branch_actions = {
            "press",
            "select_menu",
            "press_toolbar_button",
            "select_context_menu_item",
        }
        if normalized_action in branch_actions:
            rule_id = str(candidate.params.get("branch_rule_id", ""))
            rule = next(
                (
                    item
                    for item in self.policy.allowed_branches
                    if item.rule_id == rule_id
                ),
                None,
            )
            if rule is None:
                return False
            if (
                rule.action != normalized_action
                or rule.target_logical_id != candidate.target_logical_id
                or rule.target_sap_id
                != str(candidate.target_locator.get("sap_id", ""))
                or rule.item_id != str(candidate.target_locator.get("item_id", ""))
                or rule.restore_vkey != candidate.params.get("restore_vkey")
                or (rule.source_state_id and rule.source_state_id != source.state_id)
                or (
                    rule.source_fingerprint
                    and rule.source_fingerprint != source.compute_fingerprint()
                )
            ):
                return False
        if self.policy.mode == "allowlisted":
            if not self.policy.allowed_actions:
                return False
            normalized = candidate.action.strip().lower().replace("_", "-")
            allowlist = {
                item.strip().lower().replace("_", "-")
                for item in self.policy.allowed_actions
            }
            if normalized not in allowlist:
                return False
        return self.policy.permits(candidate.action, candidate.side_effect)

    @staticmethod
    def _tab_target_is_already_mapped(
        graph: UiMap, candidate: TraversalCandidate
    ) -> bool:
        """Return whether a concrete tab has already been scanned active.

        The check deliberately requires both the stable logical id and SAP id.
        Candidates without either identifier (including generic test adapters)
        retain the old per-transition behaviour because their destination is
        not safely knowable before executing SAP GUI scripting.
        """

        action = candidate.action.strip().lower().replace("-", "_")
        logical_id = candidate.target_logical_id.strip()
        sap_id = str(candidate.target_locator.get("sap_id", "")).strip()
        if action != "select_tab" or not logical_id or not sap_id:
            return False
        for state in graph.states.values():
            if logical_id not in state.active_tabs.values():
                continue
            component = next(
                (
                    item
                    for item in state.components
                    if item.logical_id == logical_id and item.sap_id == sap_id
                ),
                None,
            )
            if component is not None and component.type == "GuiTab":
                return True
        return False

    def _automatic_candidate_already_attempted(
        self,
        traversal_scope: dict[str, Any],
        state_id: str,
        candidate: TraversalCandidate,
    ) -> bool:
        """Avoid volatile tab/scroll candidates creating an endless graph.

        SAP controls can emit a fresh scroll position or tab candidate after
        every scan.  Those values change the transition id even when they do
        not reveal another UI structure.  Keep one structural attempt per
        control across an automatic run; guided mode deliberately remains
        user-directed and is not deduplicated here.
        """

        if self.policy.mode != "reversible":
            return False
        key = self._automatic_candidate_key(state_id, candidate)
        if not key:
            return False
        attempted = traversal_scope.get("automatic_candidate_keys", [])
        return isinstance(attempted, list) and key in attempted

    def _remember_automatic_candidate(
        self,
        traversal_scope: dict[str, Any],
        state_id: str,
        candidate: TraversalCandidate,
    ) -> None:
        if self.policy.mode != "reversible":
            return
        key = self._automatic_candidate_key(state_id, candidate)
        if not key:
            return
        attempted = traversal_scope.setdefault("automatic_candidate_keys", [])
        if isinstance(attempted, list) and key not in attempted:
            attempted.append(key)

    @staticmethod
    def _automatic_candidate_key(
        state_id: str, candidate: TraversalCandidate
    ) -> str:
        action = candidate.action.strip().lower().replace("-", "_")
        if action not in {"select_tab", "scroll_to"}:
            return ""
        locator = dict(candidate.target_locator)
        # Position and viewport fields are observations, not distinct UI
        # destinations.  Including them was the source of repeated scans.
        for field in ("position", "offset", "row", "column", "page", "index"):
            locator.pop(field, None)
        payload: dict[str, Any] = {
            "action": action,
            "target_logical_id": candidate.target_logical_id,
            "target_locator": locator,
        }
        if action == "select_tab":
            # A tab destination is global to a tab strip.  When the adapter
            # cannot expose a stable target id, keep source state in the key
            # so unrelated generic candidates are not collapsed together.
            if not candidate.target_logical_id and not locator:
                payload["state_id"] = state_id
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()[:24]

    def _guided_candidate_request(
        self, state: UiState, candidates: list[TraversalCandidate]
    ) -> UserInputRequest:
        """Create a value-free one-action selection checkpoint."""

        choices = [
            self._guided_choice(state, candidate)
            for candidate in candidates
        ]
        identity = json.dumps(
            {"state_id": state.state_id, "candidate_ids": [item["candidate_id"] for item in choices]},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return UserInputRequest(
            question="이 화면에서 지도에 추가할 안전한 확장 후보를 하나 선택해 주세요.",
            reason="guided_candidate_selection",
            current_state_id=state.state_id,
            required_fields=["candidate_id"],
            choices=choices,
            risk="low",
            resume_token="guided-" + hashlib.sha256(identity.encode()).hexdigest()[:24],
            provenance={
                "source": "guided_candidate_selection",
                "answer_value_stored": False,
            },
        )

    def _queue_semantic_review(
        self, graph: UiMap, state: UiState, pending: list[UserInputRequest]
    ) -> bool:
        """Pause for a closed-set MCP-AI semantic review once per state."""

        if self.semantic_reviewer is None:
            return False
        subject_prefixes = (state.state_id, f"{state.state_id}:")
        if any(
            item.subject_id == subject_prefixes[0]
            or item.subject_id.startswith(subject_prefixes[1])
            for item in graph.semantic_links
        ):
            return False
        propose = getattr(self.semantic_reviewer, "propose", None)
        if not callable(propose):
            raise TypeError("semantic reviewer does not expose propose(state)")
        candidates = list(propose(state))
        if not candidates:
            return False
        existing = {item.link_id for item in graph.semantic_links}
        candidates = [item for item in candidates if item.link_id not in existing]
        if not candidates:
            return False
        graph.semantic_links.extend(candidates)
        choices = [
            {
                "candidate_id": item.link_id,
                "subject_kind": item.subject_kind,
                "predicate": item.predicate,
                "object_id": item.object_id,
                "confidence": item.confidence,
            }
            for item in candidates[:12]
        ]
        token_source = json.dumps(
            [state.state_id, [item["candidate_id"] for item in choices]],
            separators=(",", ":"),
        )
        self._add_pending_interaction(
            graph,
            pending,
            UserInputRequest(
                question=(
                    "MCP AI가 로컬 온톨로지와 화면 구조를 비교했습니다. "
                    "확정할 의미 연결 candidate_id 목록을 선택해 주세요."
                ),
                reason="semantic_review",
                current_state_id=state.state_id,
                required_fields=["accepted_candidate_ids"],
                choices=choices,
                risk="low",
                resume_token="semantic-"
                + hashlib.sha256(token_source.encode()).hexdigest()[:24],
                provenance={
                    "source": "mcp_ai_semantic_review",
                    "answer_value_stored": False,
                    "closed_candidate_set": True,
                },
            ),
        )
        return True

    def _guided_selected_candidate(
        self,
        traversal_scope: dict[str, Any],
        state_id: str,
        candidates: list[TraversalCandidate],
    ) -> TraversalCandidate | None:
        selections = traversal_scope.get("guided_selection")
        if not isinstance(selections, dict):
            return None
        candidate_id = str(selections.pop(state_id, "")).strip()
        if not selections:
            traversal_scope.pop("guided_selection", None)
        if not candidate_id:
            return None
        return next(
            (
                candidate
                for candidate in candidates
                if self._transition_id(state_id, candidate) == candidate_id
            ),
            None,
        )

    def _guided_choice(
        self, state: UiState, candidate: TraversalCandidate
    ) -> dict[str, Any]:
        candidate_id = self._transition_id(state.state_id, candidate)
        component = next(
            (
                item
                for item in state.components
                if item.logical_id == candidate.target_logical_id
            ),
            None,
        )
        target = (
            ""
            if component is not None and component.sensitive
            else (
                (component.text or component.name or component.tooltip)
                if component is not None
                else ""
            )
        ) or candidate.target_logical_id or str(
            candidate.target_locator.get("sap_id", "")
        )
        return {
            "candidate_id": candidate_id,
            "action": candidate.action,
            "target_logical_id": candidate.target_logical_id,
            "label": f"{candidate.action}: {target or '구조 탐색'}",
            "side_effect": candidate.side_effect.value,
            "reversible": candidate.reversible,
        }

    def _apply_answers(
        self,
        adapter: TraversalAdapter,
        graph: UiMap,
        frontier: list[str],
        pending: list[UserInputRequest],
        answers: dict[str, Any],
        resolved: set[str],
        depths: dict[str, int],
        traversal_scope: dict[str, Any],
    ) -> list[UserInputRequest]:
        remaining: list[UserInputRequest] = []
        for item in pending:
            if item.resume_token not in answers:
                remaining.append(item)
                continue
            if (
                item.provenance.get("source") == "user_augmentation_request"
                and isinstance(item.scope, dict)
            ):
                baseline = graph.states.get(item.current_state_id)
                answer = answers[item.resume_token]
                requires_replay = item.scope.get("replayable_transition_required") is True
                supplied_transition = isinstance(answer, dict) and "transition" in answer
                if (requires_replay or supplied_transition) and (
                    baseline is None
                    or self._taught_transition(baseline, item, answer) is None
                ):
                    # Do not convert a user-confirmed structural route into a
                    # non-executable ``user_guided_navigation`` edge merely
                    # because the MCP client omitted the selected choice or
                    # supplied a forged/dangerous structural selector.
                    remaining.append(item)
                    continue
            if item.provenance.get("source") == "guided_candidate_selection":
                answer = answers[item.resume_token]
                candidate_id = str(answer.get("candidate_id", "")).strip()
                selections = traversal_scope.setdefault("guided_selection", {})
                if not isinstance(selections, dict):
                    raise ValueError("guided selection checkpoint is malformed")
                selections[item.current_state_id] = candidate_id
                resolved.add(item.resume_token)
                self._mark_interaction_resolved(graph, item, answer)
                continue
            if item.provenance.get("source") == "mcp_ai_semantic_review":
                answer = answers[item.resume_token]
                accepted = {
                    str(value)
                    for value in answer.get("accepted_candidate_ids", [])
                }
                offered = {
                    str(choice.get("candidate_id", ""))
                    for choice in item.choices
                    if isinstance(choice, dict)
                }
                for index, link in enumerate(graph.semantic_links):
                    if link.link_id not in offered:
                        continue
                    graph.semantic_links[index] = link.model_copy(
                        update={
                            "status": "confirmed" if link.link_id in accepted else "rejected",
                            "provenance": {
                                **link.provenance,
                                "reviewed_by": "mcp_ai",
                            },
                        }
                    )
                resolved.add(item.resume_token)
                self._mark_interaction_resolved(graph, item, answer)
                continue
            if adapter.apply_user_input(item, answers[item.resume_token]):
                captured = self._capture_user_guided_state(
                    adapter, graph, frontier, depths, item, answers[item.resume_token]
                )
                if (
                    (
                        item.provenance.get("source") == "user_augmentation_request"
                        or item.provenance.get("requires_distinct_screen") is True
                    )
                    and not captured
                ):
                    remaining.append(item)
                    continue
                resolved.add(item.resume_token)
                self._mark_interaction_resolved(graph, item, answers[item.resume_token])
            else:
                remaining.append(item)
        return remaining

    def _result(
        self,
        graph: UiMap,
        frontier: list[str],
        processed: set[str],
        elapsed: float,
        pages: int,
        nodes: int,
        pending: list[UserInputRequest],
        resolved: set[str],
        depths: dict[str, int],
        stop_reason: str,
        traversal_scope: dict[str, Any],
    ) -> TraversalResult:
        checkpoint = TraversalCheckpoint(
            ui_map=graph,
            frontier=list(frontier),
            processed=sorted(processed),
            elapsed_seconds=elapsed,
            pages_visited=pages,
            nodes_visited=nodes,
            depths=dict(depths),
            needs_user_input=pending,
            resolved_resume_tokens=sorted(resolved),
            traversal_scope=dict(traversal_scope),
        )
        if self.checkpoint_callback:
            try:
                self.checkpoint_callback(checkpoint)
            except Exception:
                if stop_reason != "checkpoint_write_failed":
                    raise
        return TraversalResult(ui_map=graph, checkpoint=checkpoint, stop_reason=stop_reason)

    def _emit_checkpoint(
        self,
        graph: UiMap,
        frontier: list[str],
        processed: set[str],
        elapsed: float,
        pages: int,
        nodes: int,
        pending: list[UserInputRequest],
        resolved: set[str],
        depths: dict[str, int],
        in_flight: dict[str, Any] | None = None,
        traversal_scope: dict[str, Any] | None = None,
    ) -> None:
        if self.checkpoint_callback is None:
            return
        self.checkpoint_callback(
            TraversalCheckpoint(
                ui_map=graph.model_copy(deep=True),
                frontier=list(frontier),
                processed=sorted(processed),
                elapsed_seconds=elapsed,
                pages_visited=pages,
                nodes_visited=nodes,
                depths=dict(depths),
                needs_user_input=list(pending),
                resolved_resume_tokens=sorted(resolved),
                in_flight=in_flight or {},
                traversal_scope=dict(traversal_scope or {}),
            )
        )

    @staticmethod
    def merge(base: UiMap, incremental: UiMap) -> UiMap:
        """Merge a resumed/incremental graph without discarding prior evidence."""

        merged = base.model_copy(deep=True)
        merged.merge_delta(incremental.model_copy(deep=True))
        existing = {json.dumps(item, sort_keys=True, default=str) for item in merged.manifest.unreachable}
        for item in incremental.manifest.unreachable:
            marker = json.dumps(item, sort_keys=True, default=str)
            if marker not in existing:
                merged.manifest.unreachable.append(dict(item))
                existing.add(marker)
        return merged

    @staticmethod
    def _add_pending_interaction(
        graph: UiMap,
        pending: list[UserInputRequest],
        item: UserInputRequest,
    ) -> None:
        key = (item.resume_token, item.current_state_id, item.question)
        if not any(
            (existing.resume_token, existing.current_state_id, existing.question) == key
            for existing in pending
        ):
            pending.append(item)
        if not any(
            (existing.resume_token, existing.current_state_id, existing.question) == key
            for existing in graph.pending_interactions
        ):
            graph.pending_interactions.append(item.model_copy(deep=True))

    @staticmethod
    def _mark_interaction_resolved(
        graph: UiMap, item: UserInputRequest, answer: Any
    ) -> None:
        answered_fields = (
            sorted(str(key) for key in answer)
            if isinstance(answer, dict)
            else list(item.required_fields)
        )
        for index, existing in enumerate(graph.pending_interactions):
            if existing.resume_token != item.resume_token:
                continue
            provenance = dict(existing.provenance)
            provenance.update(
                {
                    "answer_source": "user",
                    "answered_fields": answered_fields,
                    "answer_value_stored": False,
                }
            )
            graph.pending_interactions[index] = existing.model_copy(
                update={"status": "resolved", "provenance": provenance}
            )

    def _capture_user_guided_state(
        self,
        adapter: TraversalAdapter,
        graph: UiMap,
        frontier: list[str],
        depths: dict[str, int],
        item: UserInputRequest,
        answer: Any,
    ) -> bool:
        """Record a screen reached manually while traversal was paused."""

        baseline = graph.states.get(item.current_state_id)
        if baseline is None:
            return False
        observed = self.scanner.scan(adapter.session, tcode=baseline.tcode)
        if observed.compute_fingerprint() == baseline.compute_fingerprint():
            return False
        observed.ensure_state_id()
        if observed.state_id not in graph.states:
            graph.add_state(observed)
        taught = self._taught_transition(baseline, item, answer)
        action = taught["action"] if taught is not None else "user_guided_navigation"
        target_logical_id = taught["target_logical_id"] if taught is not None else ""
        target_locator = taught["target_locator"] if taught is not None else {}
        observed_params = taught["observed_params"] if taught is not None else {}
        transition_id = hashlib.sha256(
            json.dumps(
                [
                    item.current_state_id,
                    action,
                    target_logical_id,
                    target_locator,
                    observed.state_id,
                ],
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()[:24]
        if not any(t.transition_id == transition_id for t in graph.transitions):
            graph.transitions.append(
                Transition(
                    transition_id=transition_id,
                    from_state_id=item.current_state_id,
                    to_state_id=observed.state_id,
                    action=action,
                    target_logical_id=target_logical_id,
                    target_locator=target_locator,
                    observed_params=observed_params,
                    side_effect=SideEffectClass.NAVIGATION,
                    reversible=False,
                    evidence=[
                        {
                            "source": "user_taught_transition"
                            if taught is not None
                            else "user",
                            "resume_token": item.resume_token,
                            "scope": item.scope,
                            **({"annotation": "structural_only"} if taught is not None else {}),
                        }
                    ],
                    confidence=0.8 if taught is not None else 1.0,
                )
            )
        if observed.state_id not in frontier:
            frontier.insert(0, observed.state_id)
        depths.setdefault(observed.state_id, depths.get(item.current_state_id, 0) + 1)
        return True

    @staticmethod
    def _taught_transition(
        baseline: UiState, item: UserInputRequest, answer: Any
    ) -> dict[str, Any] | None:
        """Accept only a structural, value-free replay annotation from the user.

        A manual move without this annotation stays as coverage evidence.  The
        annotation can teach a replayable route, but never stores a business
        value, arbitrary SAP locator, or high-risk action supplied by a client.
        """

        if not isinstance(answer, dict) or not isinstance(answer.get("transition"), dict):
            return None
        spec = answer["transition"]
        action = str(spec.get("action", "")).strip().lower().replace("-", "_")
        logical_id = str(spec.get("target_logical_id", "")).strip()
        component = next(
            (candidate for candidate in baseline.components if candidate.logical_id == logical_id),
            None,
        )
        if component is None or not component.sap_id or component.visible is False or component.enabled is False:
            return None
        if _DANGEROUS_MARKER.search(
            json.dumps(
                {
                    "action": action,
                    "logical_id": component.logical_id,
                    "sap_id": component.sap_id,
                    "item_id": spec.get("item_id", ""),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        ):
            return None
        collection_attribute = {
            "press_toolbar_button": "toolbar_actions",
            "select_context_menu_item": "context_menu_actions",
        }.get(action)
        if collection_attribute is not None:
            if set(spec) != {"action", "target_logical_id", "item_id"}:
                return None
            if component.type not in {"GuiToolbar", "GuiShell", "GuiGridView", "GuiTableControl"}:
                return None
            item_id = str(spec.get("item_id", "")).strip()
            if not item_id:
                return None
            collection = next(
                (
                    candidate
                    for candidate in baseline.collections
                    if candidate.component_logical_id == component.logical_id
                ),
                None,
            )
            if collection is None:
                return None
            items = getattr(collection, collection_attribute, ())
            if not any(
                isinstance(item, dict)
                and item.get("enabled") is not False
                and item.get("visible") is not False
                and TraversalEngine._collection_item_id(item, position) == item_id
                for position, item in enumerate(items)
            ):
                return None
            supported = {
                value.strip().lower().replace("-", "_")
                for value in component.supported_actions
            }
            if supported and action not in supported:
                return None
            return {
                "action": action,
                "target_logical_id": component.logical_id,
                "target_locator": {
                    "sap_id": component.sap_id,
                    "component_type": component.type,
                    "item_id": item_id,
                },
                "observed_params": {"item_id": item_id},
            }
        if set(spec) != {"action", "target_logical_id"}:
            return None
        expected_types = {
            "press": {"GuiButton"},
            "select_menu": {"GuiMenu"},
            "select_tab": {"GuiTab"},
        }
        if (
            action not in expected_types
            or component.type not in expected_types[action]
        ):
            return None
        supported = {value.strip().lower().replace("-", "_") for value in component.supported_actions}
        expected_capability = "select" if action in {"select_menu", "select_tab"} else action
        if supported and expected_capability not in supported:
            return None
        return {
            "action": action,
            "target_logical_id": component.logical_id,
            "target_locator": {"sap_id": component.sap_id, "component_type": component.type},
            "observed_params": {},
        }

    @staticmethod
    def _collection_item_id(item: dict[str, Any], position: int) -> str:
        """Return the scanner's stable, value-free ID for an ALV action."""

        for key in ("id", "key", "position"):
            value = item.get(key)
            if value is not None and str(value):
                return str(value)
        return str(position)

    @staticmethod
    def _scope_permits(
        scope: dict[str, Any], state_id: str, candidate: TraversalCandidate
    ) -> bool:
        """Constrain a resumed run to the branch explicitly requested by a user."""

        state_ids = set(scope.get("state_ids") or ())
        actions = set(scope.get("actions") or ())
        targets = set(scope.get("target_logical_ids") or ())
        if state_ids and state_id not in state_ids:
            return False
        if actions and candidate.action not in actions:
            return False
        if targets and candidate.target_logical_id not in targets:
            return False
        return True

    @staticmethod
    def _transition_id(state_id: str, candidate: TraversalCandidate) -> str:
        payload = json.dumps(
            [
                state_id,
                candidate.action,
                candidate.target_logical_id,
                candidate.target_locator,
                candidate.params,
                candidate.side_effect.value,
            ],
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]

    @staticmethod
    def _unreachable(graph: UiMap, state_id: str, reason: str, action: str = "") -> None:
        graph.manifest.unreachable.append(
            {"state_id": state_id, "reason": reason, **({"action": action} if action else {})}
        )

    def _elapsed(self, started: float) -> float:
        return max(0.0, self.clock() - started)


__all__ = [
    "NeedsUserInput",
    "TraversalAdapter",
    "TraversalCandidate",
    "TraversalCheckpoint",
    "TraversalEngine",
    "TraversalResult",
]
