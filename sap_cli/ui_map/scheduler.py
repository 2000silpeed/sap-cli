"""Cooperative owner-thread scheduler for durable UI-map traversal jobs.

``start`` only persists work.  SAP GUI access happens in ``tick``, allowing the
daemon's COM/STA owner thread to advance one transition between requests.
"""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
import hashlib
import json
import re
import uuid
from typing import Any

from .jobs import BUDGET_STOP_REASONS, MapJobRecord, MapJobStore
from .sap_traversal import SapTraversalAdapter
from .scanner import UiMapScanner
from .schema import (
    MapManifest,
    SideEffectClass,
    TraversalBudget,
    TraversalBranchRule,
    TraversalPolicy,
    UiMap,
    UserInputRequest,
)
from .traversal import TraversalCheckpoint, TraversalEngine, TraversalResult
from .store import UiMapStore


WrapperFactory = Callable[[str], Any]
AdapterFactory = Callable[[Any], Any]


# Route teaching deliberately records only structural selectors.  These
# markers close the same high-risk verbs that are rejected for automatic
# allowlisted branches, so an augmentation answer cannot smuggle a Save/Post
# route into a later plan merely by selecting it from a scanned screen.
_DANGEROUS_BRANCH_MARKER = re.compile(
    r"(?<![a-z])(save|post|delete|release|send|print)(?![a-z])"
    r"|저장|전기|삭제|릴리스|릴리즈|승인|전송|발송|인쇄|출력",
    re.I,
)
_COLLECTION_BRANCH_ACTIONS = {
    "press_toolbar_button": "toolbar_actions",
    "select_context_menu_item": "context_menu_actions",
}


class UiMapScheduler:
    """Advance durable traversal jobs one transition per owner-thread tick."""

    def __init__(
        self,
        job_store: MapJobStore,
        get_wrapper: WrapperFactory,
        *,
        scanner: UiMapScanner | None = None,
        adapter_factory: AdapterFactory = SapTraversalAdapter,
        map_store: UiMapStore | None = None,
        coordinator: Any = None,
        semantic_reviewer: Any = None,
    ) -> None:
        self.job_store = job_store
        self.get_wrapper = get_wrapper
        self.scanner = scanner or UiMapScanner()
        self.adapter_factory = adapter_factory
        self.map_store = map_store
        self.coordinator = coordinator
        self.semantic_reviewer = semantic_reviewer
        # Answer values are intentionally process-local and are removed before
        # the traversal attempt, including when that attempt raises.
        self._answer_vault: dict[str, dict[str, Any]] = {}

    def start(
        self,
        *,
        tcode: str,
        map_id: str,
        mode: str = "passive",
        budget: TraversalBudget | dict[str, Any] | None = None,
        allowed_actions: set[str] | list[str] | tuple[str, ...] | None = None,
        allowed_branches: list[TraversalBranchRule | dict[str, Any]] | None = None,
        owner_agent: str = "",
        slot_id: str = "ses0",
        navigate_pending: bool | None = None,
        navigate: bool | None = None,
        test_client_confirmed: bool = False,
        semantic_review: bool = False,
        job_id: str | None = None,
    ) -> MapJobRecord:
        """Persist a queued job without resolving a wrapper or touching COM."""

        resolved_budget = (
            budget
            if isinstance(budget, TraversalBudget)
            else TraversalBudget.model_validate(budget or {})
        )
        resolved_mode = self._validate_mode(mode)
        branch_rules = [
            item
            if isinstance(item, TraversalBranchRule)
            else TraversalBranchRule.model_validate(item)
            for item in (allowed_branches or ())
        ]
        if branch_rules and resolved_mode != "allowlisted":
            raise ValueError("allowed_branches require allowlisted mode")
        if resolved_mode != "passive" and not test_client_confirmed:
            raise ValueError(
                "active traversal requires explicit test_client_confirmed=true"
            )
        should_navigate = (
            bool(navigate)
            if navigate is not None
            else True if navigate_pending is None else bool(navigate_pending)
        )
        resolved_map_id = map_id.strip() or f"auto-{tcode.strip().upper()}"
        config = {
            "mode": resolved_mode,
            "budget": resolved_budget.model_dump(mode="json"),
            "allowed_actions": sorted(str(item) for item in (allowed_actions or ())),
            "allowed_branches": [
                item.model_dump(mode="json")
                for item in sorted(branch_rules, key=lambda rule: rule.rule_id)
            ],
            "navigate_pending": should_navigate,
            "test_client_confirmed": bool(test_client_confirmed),
            "semantic_review": bool(semantic_review),
        }
        record = self.job_store.create(
            tcode=tcode,
            map_id=resolved_map_id,
            owner_agent=owner_agent,
            slot_id=slot_id,
            traversal_config=config,
            job_id=job_id,
        )
        if self.coordinator is not None:
            try:
                self._submit_to_coordinator(record)
                self.coordinator.claim(self._coordinator_id(record.job_id))
            except Exception:
                self.job_store.fail(record.job_id, "map_job_coordination_failed")
                raise
        return record

    def augment_start(
        self,
        *,
        ui_map: UiMap,
        from_state_id: str,
        request: str,
        scope: dict[str, Any] | None = None,
        owner_agent: str = "",
        slot_id: str = "ses0",
        job_id: str | None = None,
    ) -> MapJobRecord:
        """Create a durable user-guided checkpoint for an existing map.

        This operation never touches SAP.  The user moves the live GUI to the
        requested missing screen and resumes the newly-created job; the normal
        traversal path then scans that screen and saves a non-destructive delta.
        """

        source_id = str(from_state_id or "").strip()
        question = str(request or "").strip()
        if not source_id or source_id not in ui_map.states:
            raise ValueError("from_state_id does not exist in the selected map")
        if not question:
            raise ValueError("request is required")
        requested_scope = dict(scope or {})
        token = f"augment-{uuid.uuid4().hex}"
        replay_choices = self._augmentation_transition_choices(ui_map.states[source_id])
        # A user may still document a route that cannot be replayed.  But when
        # the source screen exposes a safe, stable structural control, the
        # normal augmentation path must capture that control.  Otherwise a
        # seemingly successful map extension becomes a user-guided edge that
        # the planner correctly cannot execute later.
        replay_required = bool(replay_choices)
        requested_scope["replayable_transition_required"] = replay_required
        interaction = UserInputRequest(
            question=(
                question
                + (
                    " 자동 실행 계획에도 사용할 경로이므로, 이동에 사용한 버튼·메뉴·탭을 "
                    "목록에서 반드시 선택해 주세요."
                    if replay_required
                    else ""
                )
            ),
            reason="user_requested_missing_screen",
            current_state_id=source_id,
            required_fields=(
                ["continue", "transition"] if replay_required else ["continue"]
            ),
            choices=[{"continue": True}, *replay_choices],
            risk="medium",
            resume_token=token,
            scope=requested_scope,
            provenance={
                "source": "user_augmentation_request",
                "replay_annotation_optional": bool(replay_choices),
                "replayable_transition_required": replay_required,
                "answer_value_stored": False,
            },
        )
        graph = ui_map.model_copy(deep=True)
        graph.pending_interactions.append(interaction.model_copy(deep=True))
        checkpoint = TraversalCheckpoint(
            ui_map=graph,
            frontier=[source_id],
            depths={source_id: 0},
            needs_user_input=[interaction],
            traversal_scope=requested_scope,
        )
        config = {
            "job_kind": "augmentation",
            "mode": "passive",
            "budget": TraversalBudget().model_dump(mode="json"),
            "allowed_actions": [],
            "navigate_pending": False,
            "test_client_confirmed": False,
            "from_state_id": source_id,
            "map_scope": {
                "system_id": ui_map.manifest.system_id,
                "client": ui_map.manifest.client,
                "language": ui_map.manifest.language,
            },
        }
        record = self.job_store.create(
            tcode=ui_map.manifest.tcode,
            map_id=ui_map.manifest.map_id,
            owner_agent=owner_agent,
            slot_id=slot_id,
            traversal_config=config,
            job_id=job_id,
        )
        try:
            record = self.job_store.save_checkpoint(record.job_id, checkpoint)
            if self.coordinator is not None:
                self._submit_to_coordinator(record)
                claimed = self.coordinator.claim(self._coordinator_id(record.job_id))
                if claimed is None:
                    raise RuntimeError("augmentation job could not acquire its SAP slot")
                self.coordinator.needs_user_input(self._coordinator_id(record.job_id))
        except Exception:
            self.job_store.fail(record.job_id, "map_augmentation_start_failed")
            raise
        return record

    @staticmethod
    def _augmentation_transition_choices(state: Any) -> list[dict[str, Any]]:
        """Expose only value-free, typed controls usable for route teaching."""

        choices: list[dict[str, Any]] = []
        action_by_type = {
            "GuiButton": "press",
            "GuiMenu": "select_menu",
            "GuiTab": "select_tab",
        }
        for component in state.components:
            action = action_by_type.get(component.type)
            if (
                not action
                or not component.logical_id
                or not component.sap_id
                or component.visible is False
                or component.enabled is False
                or UiMapScheduler._dangerous_structural_marker(
                    action, component.logical_id, component.sap_id
                )
            ):
                continue
            supported = {
                value.strip().lower().replace("-", "_")
                for value in component.supported_actions
            }
            expected = "select" if action != "press" else "press"
            if supported and expected not in supported:
                continue
            choices.append(
                {
                    "transition": {
                        "action": action,
                        "target_logical_id": component.logical_id,
                    }
                }
            )
        components = {component.logical_id: component for component in state.components}
        for collection in state.collections:
            component = components.get(collection.component_logical_id)
            if (
                component is None
                or not component.logical_id
                or not component.sap_id
                or component.visible is False
                or component.enabled is False
            ):
                continue
            for action, attribute in _COLLECTION_BRANCH_ACTIONS.items():
                for position, item in enumerate(getattr(collection, attribute, ())):
                    if not isinstance(item, dict) or (
                        item.get("enabled") is False or item.get("visible") is False
                    ):
                        continue
                    item_id = UiMapScheduler._collection_item_id(item, position)
                    if not item_id or UiMapScheduler._dangerous_structural_marker(
                        action, component.logical_id, component.sap_id, item_id
                    ):
                        continue
                    supported = {
                        value.strip().lower().replace("-", "_")
                        for value in component.supported_actions
                    }
                    if supported and action not in supported:
                        continue
                    choices.append(
                        {
                            "transition": {
                                "action": action,
                                "target_logical_id": component.logical_id,
                                "item_id": item_id,
                            }
                        }
                    )
        return sorted(
            choices,
            key=lambda item: (
                item["transition"]["action"],
                item["transition"]["target_logical_id"],
                item["transition"].get("item_id", ""),
            ),
        )

    @staticmethod
    def _collection_item_id(item: dict[str, Any], position: int) -> str:
        for key in ("id", "key", "position"):
            value = item.get(key)
            if value is not None and str(value):
                return str(value)
        return str(position)

    @staticmethod
    def _dangerous_structural_marker(*values: str) -> bool:
        return bool(_DANGEROUS_BRANCH_MARKER.search(" ".join(values)))

    def hydrate_persisted_jobs(self) -> dict[str, int]:
        """Rebuild value-free coordinator state after a daemon restart."""

        counts = {
            "queued": 0,
            "running": 0,
            "needs_user_input": 0,
            "paused": 0,
            "interrupted": 0,
        }
        if self.coordinator is None:
            return counts
        self.job_store.recover_interrupted()
        records = [
            item
            for item in self.job_store.list()
            if item.status in counts
        ]
        priority = {
            "needs_user_input": 0,
            "interrupted": 1,
            "running": 2,
            "paused": 3,
            "queued": 4,
        }
        records.sort(key=lambda item: (priority[item.status], item.created_at, item.job_id))
        for record in records:
            self._submit_to_coordinator(record)
            coordinator_id = self._coordinator_id(record.job_id)
            if record.status == "queued":
                counts[record.status] += 1
                continue
            if record.status == "paused":
                self.coordinator.pause(coordinator_id)
                counts[record.status] += 1
                continue
            claimed = self.coordinator.claim(coordinator_id)
            if claimed is None:
                raise RuntimeError("persisted map job could not reacquire its SAP slot")
            if record.status == "needs_user_input":
                self.coordinator.needs_user_input(coordinator_id)
            elif record.status == "interrupted":
                self.coordinator.mark_outcome_unknown(
                    coordinator_id,
                    error_code="dirty_checkpoint_reconciliation_required",
                )
            counts[record.status] += 1
        return counts

    def resume(
        self,
        job_id: str,
        answers: dict[str, Any] | None = None,
        *,
        budget: dict[str, Any] | None = None,
        owner_agent: str = "",
        slot_id: str = "ses0",
    ) -> MapJobRecord:
        """Resume user input or extend a clean budget-paused checkpoint."""

        current = self.job_store.authorize(
            job_id, owner_agent=owner_agent, slot_id=slot_id
        )
        provided_answers = dict(answers or {})
        provided_budget = dict(budget or {})
        if current.status == "paused":
            if provided_answers:
                raise ValueError("budget-paused jobs resume with budget, not answers")
            expanded = self._expanded_budget(current, provided_budget)
            record = self.job_store.resume_paused(
                job_id, budget=expanded.model_dump(mode="json")
            )
        else:
            if provided_budget:
                raise ValueError("budget may only be changed for a paused map job")
            if not provided_answers:
                raise ValueError("answers must be a non-empty object")
            record = self.job_store.record_resume_answers(job_id, provided_answers)
            self._answer_vault[job_id] = deepcopy(provided_answers)
        if self.coordinator is not None:
            self.coordinator.resume(self._coordinator_id(job_id))
            self.coordinator.claim(self._coordinator_id(job_id))
        return record

    def tick(self, job_id: str | None = None) -> MapJobRecord | None:
        """Run at most one transition for one runnable job."""

        record = self._select_job(job_id)
        if record is None:
            return record
        if record.status in {"completed", "failed", "cancelled"}:
            self._answer_vault.pop(record.job_id, None)
            return record
        if self.coordinator is not None:
            coord_id = self._coordinator_id(record.job_id)
            snapshot = self.coordinator.get(coord_id)
            if snapshot.status == "queued":
                snapshot = self.coordinator.claim(coord_id)
                if snapshot is None:
                    return record
            if snapshot.status == "needs_user_input":
                return record
            if snapshot.status != "running":
                return record
        # Consume before any COM/store work.  A failed tick must not retain
        # business values for an implicit retry.
        answers = self._answer_vault.pop(record.job_id, {})
        checkpoint = record.checkpoint
        if checkpoint is not None and checkpoint.in_flight.get("dirty"):
            # Re-persist as running so the store's crash recovery can mark the
            # job interrupted.  Never infer whether the SAP action happened.
            self.job_store.save_checkpoint(record.job_id, checkpoint)
            self.job_store.recover_interrupted()
            if self.coordinator is not None:
                self.coordinator.mark_outcome_unknown(
                    self._coordinator_id(record.job_id),
                    error_code="dirty_checkpoint_reconciliation_required",
                )
            return self.job_store.get(record.job_id)

        config = record.traversal_config
        full_budget = TraversalBudget.model_validate(config.get("budget") or {})
        transition_count = (
            len(checkpoint.ui_map.transitions) if checkpoint is not None else 0
        )
        if transition_count >= full_budget.max_transitions:
            if checkpoint is None:
                raise RuntimeError("transition budget exhausted without checkpoint")
            paused = self.job_store.finish(
                record.job_id,
                self._persist_result(TraversalResult(
                    ui_map=checkpoint.ui_map,
                    checkpoint=checkpoint,
                    stop_reason="max_transitions",
                )),
            )
            if self.coordinator is not None:
                self.coordinator.pause(self._coordinator_id(record.job_id))
            return paused

        try:
            wrapper = self.get_wrapper(record.slot_id)
            if checkpoint is None and bool(config.get("navigate_pending", True)):
                self._navigate(wrapper, record.tcode)
            self._assert_live_scope(wrapper, record, checkpoint)
            # Record the live system/client/user so the test-client assertion can
            # be confirmed against reality. Purely informational — never used to
            # decide production from the client number.
            self._record_observed_scope(wrapper, record)

            session = getattr(wrapper, "session", wrapper)
            adapter = self.adapter_factory(session)
            branch_rules = [
                TraversalBranchRule.model_validate(item)
                for item in (config.get("allowed_branches") or ())
            ]
            if branch_rules:
                configure = getattr(adapter, "configure_branch_rules", None)
                if not callable(configure):
                    raise TypeError(
                        "traversal adapter does not support exact branch rules"
                    )
                configure(branch_rules)
            sliced_budget = full_budget.model_copy(
                update={"max_transitions": transition_count + 1}
            )
            policy = TraversalPolicy(
                mode=self._validate_mode(str(config.get("mode", "passive"))),
                budget=sliced_budget,
                allowed_side_effects=self._allowed_effects(
                    str(config.get("mode", "passive"))
                ),
                allowed_actions=(
                    set(config.get("allowed_actions") or ())
                    | {item.action for item in branch_rules}
                ),
                allowed_branches=branch_rules,
            )
            engine = TraversalEngine(
                policy,
                scanner=self.scanner,
                checkpoint_callback=self.job_store.checkpoint_callback(record.job_id),
                semantic_reviewer=(
                    self.semantic_reviewer
                    if bool(config.get("semantic_review", False))
                    else None
                ),
            )
            result = engine.run(
                adapter,
                checkpoint=checkpoint,
                manifest=(
                    None
                    if checkpoint is not None
                    else self._manifest(record, wrapper, policy)
                ),
                answers=answers,
            )
        except Exception as exc:
            failed = self.job_store.fail(record.job_id, "map_traversal_failed")
            if self.coordinator is not None:
                self.coordinator.fail(
                    self._coordinator_id(record.job_id),
                    error_code="map_traversal_failed",
                )
            return failed

        # ``max_transitions`` is normally the cooperative slice boundary, not
        # failure of the user's full budget.
        if (
            result.stop_reason == "max_transitions"
            and len(result.ui_map.transitions) < full_budget.max_transitions
        ):
            # A plan may be requested while this cooperative job is paused or
            # waiting for another daemon tick.  Persist the graph delta before
            # retaining the checkpoint so map lookup never sees an old route.
            persisted = self._persist_result(result)
            saved = self.job_store.save_checkpoint(record.job_id, persisted.checkpoint)
            if saved.status == "needs_user_input" and self.coordinator is not None:
                self.coordinator.needs_user_input(self._coordinator_id(record.job_id))
            return saved
        finished = self.job_store.finish(record.job_id, self._persist_result(result))
        if self.coordinator is not None:
            coord_id = self._coordinator_id(record.job_id)
            if finished.status == "needs_user_input":
                self.coordinator.needs_user_input(coord_id)
            elif finished.status == "completed":
                self.coordinator.complete(coord_id)
            elif finished.status == "paused":
                self.coordinator.pause(coord_id)
            elif finished.status == "failed":
                self.coordinator.fail(coord_id, error_code="map_traversal_failed")
        return finished

    def cancel(self, job_id: str) -> MapJobRecord:
        record = self.job_store.request_cancel(job_id)
        self._answer_vault.pop(job_id, None)
        if self.coordinator is not None:
            snapshot = self.coordinator.get(self._coordinator_id(job_id))
            if snapshot.status in {"queued", "running", "needs_user_input", "paused"}:
                self.coordinator.cancel(self._coordinator_id(job_id))
        return record

    def has_pending_answers(self, job_id: str) -> bool:
        """Diagnostic used by daemon tests; values themselves are never exposed."""

        return job_id in self._answer_vault

    def _persist_result(self, result: TraversalResult) -> TraversalResult:
        if self.map_store is None:
            return result
        saved = self.map_store.save_delta(result.ui_map)
        checkpoint = result.checkpoint.model_copy(update={"ui_map": saved}, deep=True)
        return result.model_copy(
            update={"ui_map": saved, "checkpoint": checkpoint}, deep=True
        )

    @staticmethod
    def _manifest(
        record: MapJobRecord, wrapper: Any, policy: TraversalPolicy
    ) -> MapManifest:
        values: dict[str, str] = {}
        getter = getattr(wrapper, "get_session_info", None)
        if callable(getter):
            info = getter()
            values = {
                "system_id": str(getattr(info, "system_name", "") or ""),
                "client": str(getattr(info, "client", "") or ""),
                "language": str(getattr(info, "language", "") or ""),
            }
        return MapManifest(
            map_id=record.map_id,
            tcode=record.tcode,
            capture_mode=policy.mode,
            **values,
        )

    def _select_job(self, job_id: str | None) -> MapJobRecord | None:
        if job_id is not None:
            return self.job_store.get(job_id)
        runnable = [
            *self.job_store.list(status="queued"),
            *self.job_store.list(status="running"),
        ]
        if self.coordinator is not None:
            eligible = []
            for record in runnable:
                snapshot = self.coordinator.get(self._coordinator_id(record.job_id))
                lease = self.coordinator.lease_for_slot(record.slot_id)
                if (
                    snapshot.status == "running"
                    or lease is None
                    or lease.job_id == snapshot.job_id
                ):
                    eligible.append(record)
            runnable = eligible
        if not runnable:
            return None
        # Oldest update first prevents one long traversal from starving others.
        return min(runnable, key=lambda item: (item.updated_at, item.job_id))

    @staticmethod
    def _coordinator_id(job_id: str) -> str:
        return f"map:{job_id}"

    @staticmethod
    def _request_digest(record: MapJobRecord) -> str:
        return hashlib.sha256(
            json.dumps(
                {
                    "tcode": record.tcode,
                    "map_id": record.map_id,
                    "config": record.traversal_config,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()

    def _submit_to_coordinator(self, record: MapJobRecord) -> Any:
        return self.coordinator.submit(
            self._coordinator_id(record.job_id),
            "map_traverse",
            self._request_digest(record),
            slot_id=record.slot_id,
            agent_name=record.owner_agent or "unknown",
        )

    @staticmethod
    def _expanded_budget(
        record: MapJobRecord, updates: dict[str, Any]
    ) -> TraversalBudget:
        if not updates:
            raise ValueError("paused map job requires a larger budget")
        unknown = set(updates) - set(TraversalBudget.model_fields)
        if unknown:
            raise ValueError(f"unknown budget fields: {', '.join(sorted(unknown))}")
        checkpoint = record.checkpoint
        if checkpoint is None:
            raise ValueError("paused map job has no checkpoint")
        if record.stop_reason not in BUDGET_STOP_REASONS:
            raise ValueError("map job is not paused at a resumable budget boundary")

        current = TraversalBudget.model_validate(
            record.traversal_config.get("budget") or {}
        )
        candidate_data = current.model_dump(mode="json")
        candidate_data.update(updates)
        candidate = TraversalBudget.model_validate(candidate_data)
        for field_name in TraversalBudget.model_fields:
            if getattr(candidate, field_name) < getattr(current, field_name):
                raise ValueError(f"budget cannot decrease: {field_name}")

        stopped_field = record.stop_reason
        if getattr(candidate, stopped_field) <= getattr(current, stopped_field):
            raise ValueError(f"paused budget must increase: {stopped_field}")
        usage = {
            "max_seconds": checkpoint.elapsed_seconds,
            "max_states": len(checkpoint.ui_map.states),
            "max_transitions": len(checkpoint.ui_map.transitions),
            "max_pages_per_collection": checkpoint.pages_visited,
            "max_nodes_per_tree": checkpoint.nodes_visited,
        }
        for field_name, consumed in usage.items():
            if getattr(candidate, field_name) <= consumed:
                raise ValueError(
                    f"budget must exceed current usage: {field_name}={consumed}"
                )
        return candidate

    @staticmethod
    def _navigate(wrapper: Any, tcode: str) -> None:
        for method_name in ("run_transaction", "RunTransaction"):
            method = getattr(wrapper, method_name, None)
            if callable(method):
                method(tcode)
                return
        raise AttributeError("SAP wrapper does not expose run_transaction")

    @staticmethod
    def _assert_live_scope(
        wrapper: Any, record: MapJobRecord, checkpoint: TraversalCheckpoint | None
    ) -> None:
        """Fail closed before scanning when a saved augmentation map is out of scope.

        The scanner accepts a TCode hint, so it must not be allowed to mask a
        session attached to another system/client/language or transaction.
        Fresh traversal jobs have no saved scope yet; completed-map augmentation
        jobs always carry one in their durable configuration.
        """

        expected_scope = dict(record.traversal_config.get("map_scope") or {})
        if not expected_scope and checkpoint is not None:
            manifest = checkpoint.ui_map.manifest
            expected_scope = {
                "system_id": manifest.system_id,
                "client": manifest.client,
                "language": manifest.language,
            }
        if not expected_scope:
            return
        getter = getattr(wrapper, "get_session_info", None)
        if not callable(getter):
            raise RuntimeError("map_scope_unverifiable")
        info = getter()
        dump = getattr(info, "model_dump", None)
        values = dump(mode="json") if callable(dump) else {
            name: getattr(info, name, "")
            for name in ("system_name", "client", "language", "transaction")
        }
        if not isinstance(values, dict):
            raise RuntimeError("map_scope_unverifiable")
        expected_tcode = str(record.tcode or "").strip().upper()
        actual_tcode = str(values.get("transaction", "")).strip().upper()
        expected = {
            "system_id": str(expected_scope.get("system_id", "")).strip().upper(),
            "client": str(expected_scope.get("client", "")).strip(),
            "language": str(expected_scope.get("language", "")).strip().upper(),
        }
        actual = {
            "system_id": str(values.get("system_name", "")).strip().upper(),
            "client": str(values.get("client", "")).strip(),
            "language": str(values.get("language", "")).strip().upper(),
        }
        if (
            not actual_tcode
            or actual_tcode != expected_tcode
            or any(expected[name] and actual[name] != expected[name] for name in expected)
        ):
            raise RuntimeError("map_scope_mismatch")

    def _record_observed_scope(self, wrapper: Any, record: MapJobRecord) -> None:
        """Read live connection identity and store it on the job (best effort)."""

        getter = getattr(wrapper, "get_session_info", None)
        if not callable(getter):
            return
        try:
            info = getter()
        except Exception:
            return
        dump = getattr(info, "model_dump", None)
        raw = dump(mode="json") if callable(dump) else {
            name: getattr(info, name, "")
            for name in ("system_name", "client", "user", "language", "transaction")
        }
        if not isinstance(raw, dict):
            return
        scope = {
            "system_id": str(raw.get("system_name", "") or ""),
            "client": str(raw.get("client", "") or ""),
            "user": str(raw.get("user", "") or ""),
            "language": str(raw.get("language", "") or ""),
            "transaction": str(raw.get("transaction", "") or ""),
        }
        if any(scope.values()):
            try:
                self.job_store.record_observed_scope(record.job_id, scope)
            except Exception:
                # Surfacing identity must never break the traversal itself.
                pass

    @staticmethod
    def _validate_mode(mode: str) -> str:
        normalized = mode.strip().lower()
        if normalized not in {"passive", "guided", "reversible", "allowlisted"}:
            raise ValueError(
                "mode must be passive, guided, reversible, or allowlisted"
            )
        return normalized

    @staticmethod
    def _allowed_effects(mode: str) -> set[SideEffectClass]:
        if mode.strip().lower() == "passive":
            return {SideEffectClass.READ_ONLY}
        return {
            SideEffectClass.READ_ONLY,
            SideEffectClass.REVERSIBLE,
            SideEffectClass.NAVIGATION,
        }


__all__ = ["UiMapScheduler"]
