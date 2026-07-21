"""Schemas for versioned SAP GUI state and transition maps.

The legacy ontology stores a convenient summary keyed by dynpro number.  These
models deliberately keep a separate, lossless map whose identity also reflects
window, tab, mode, modal, and collection state.
"""

from __future__ import annotations

import hashlib
import json
import re
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


SCHEMA_VERSION = "1.0"


class SideEffectClass(str, Enum):
    """Safety class used by traversal and compiled execution plans."""

    READ_ONLY = "read_only"
    REVERSIBLE = "reversible"
    NAVIGATION = "navigation"
    COMMIT = "commit"
    DESTRUCTIVE = "destructive"
    EXTERNAL = "external"


class ApiMemberCoverage(BaseModel):
    """Coverage status for one documented SAP GUI Scripting member."""

    object_name: str
    member_name: str
    member_kind: Literal[
        "method", "property", "event", "object", "enum", "enum_value"
    ]
    object_category: Literal[
        "visual", "administrative", "collection", "value", "unknown"
    ] = "unknown"
    discovered: bool = True
    modeled: bool = False
    adapter_supported: bool = False
    policy_classified: bool = False
    unit_tested: bool = False
    live_tested_versions: list[str] = Field(default_factory=list)
    documented_access: str = ""
    permission: Literal["read", "execute", "write", "admin"] | None = None
    risk_level: Literal["low", "medium", "high", "critical"] | None = None
    access_path: str = ""
    notes: list[str] = Field(default_factory=list)

    @property
    def live_tested(self) -> bool:
        return bool(self.live_tested_versions)


class ApiCoverageManifest(BaseModel):
    """Versioned coverage ledger derived from the official API manifest."""

    api_version: str
    source: str = ""
    generated_at: str = ""
    members: list[ApiMemberCoverage] = Field(default_factory=list)

    def counts(self) -> dict[str, int]:
        members = self.members
        return {
            "total": len(members),
            "discovered": sum(item.discovered for item in members),
            "modeled": sum(item.modeled for item in members),
            "adapter_supported": sum(item.adapter_supported for item in members),
            "policy_classified": sum(item.policy_classified for item in members),
            "unit_tested": sum(item.unit_tested for item in members),
            "live_tested": sum(item.live_tested for item in members),
        }


class MapManifest(BaseModel):
    """Identity, environment scope, and provenance for a transaction map."""

    map_id: str
    tcode: str
    schema_version: str = SCHEMA_VERSION
    map_version: int = Field(default=1, ge=1)
    api_version: str = ""
    system_id: str = ""
    client: str = ""
    language: str = ""
    gui_version: str = ""
    application_release: str = ""
    capture_mode: Literal[
        "passive", "guided", "reversible", "allowlisted", "record"
    ] = "passive"
    fixture_ids: list[str] = Field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    completeness: float = Field(default=0.0, ge=0.0, le=1.0)
    unreachable: list[dict[str, Any]] = Field(default_factory=list)


class WindowDescriptor(BaseModel):
    """Stable subset of an open SAP window used to identify UI state."""

    sap_id: str
    type: str = ""
    title: str = ""
    modal: bool = False
    active: bool = False
    signature: str = ""


class UiComponent(BaseModel):
    """One component in a lossless, hierarchical SAP GUI snapshot."""

    logical_id: str
    sap_id: str
    type: str
    sub_type: str = ""
    role: str = ""
    parent_logical_id: str = ""
    children: list[str] = Field(default_factory=list)
    alternate_locators: list[dict[str, Any]] = Field(default_factory=list)
    name: str = ""
    text: str = ""
    tooltip: str = ""
    icon_name: str = ""
    visible: bool | None = None
    enabled: bool | None = None
    changeable: bool | None = None
    required: bool | None = None
    selected: bool | None = None
    bounds: dict[str, int] = Field(default_factory=dict)
    static_properties: dict[str, Any] = Field(default_factory=dict)
    volatile_properties: dict[str, Any] = Field(default_factory=dict)
    supported_actions: list[str] = Field(default_factory=list)
    sensitive: bool = False
    provenance: dict[str, Any] = Field(default_factory=dict)


class CollectionColumn(BaseModel):
    """Stable definition of a table, ALV, grid, or tree column."""

    id: str
    title: str = ""
    type: str = ""
    position: int = 0
    editable: bool | None = None
    visible: bool | None = None


class CollectionNode(BaseModel):
    """One discovered tree node without assuming its key is business-stable."""

    key: str
    text: str = ""
    parent_key: str = ""
    path: str = ""
    depth: int | None = None
    expandable: bool | None = None
    expanded: bool | None = None
    selected: bool | None = None
    values: dict[str, Any] = Field(default_factory=dict)
    alternate_locators: list[dict[str, Any]] = Field(default_factory=list)
    volatile_properties: dict[str, Any] = Field(default_factory=dict)


class CollectionCell(BaseModel):
    """Visible collection-cell affordance, deliberately without its value."""

    absolute_row: int | None = None
    visible_row: int | None = None
    column_id: str
    column_position: int = 0
    cell_type: str = ""
    changeable: bool | None = None
    selected: bool | None = None
    affordances: list[str] = Field(default_factory=list)
    value_redacted: bool = True
    icon_name: str = ""


class CollectionDescriptor(BaseModel):
    """Structure and interaction metadata for collection-like controls."""

    component_logical_id: str
    kind: Literal["table", "grid", "alv", "tree", "apo_grid", "calendar", "other"] = "other"
    columns: list[CollectionColumn] = Field(default_factory=list)
    nodes: list[CollectionNode] = Field(default_factory=list)
    visible_cells: list[CollectionCell] = Field(default_factory=list)
    row_count: int | None = None
    total_column_count: int | None = None
    total_node_count: int | None = None
    visible_range: tuple[int, int] | None = None
    horizontal_position: int | None = None
    vertical_position: int | None = None
    selection_mode: str = ""
    selected_rows: list[int] = Field(default_factory=list)
    selected_node_keys: list[str] = Field(default_factory=list)
    expanded_node_keys: list[str] = Field(default_factory=list)
    toolbar_actions: list[dict[str, Any]] = Field(default_factory=list)
    context_menu_actions: list[dict[str, Any]] = Field(default_factory=list)
    filtered_columns: list[str] = Field(default_factory=list)
    sorted_columns: list[str] = Field(default_factory=list)
    sample_policy: str = "none"
    coverage: dict[str, Any] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=dict)
    volatile_properties: dict[str, Any] = Field(default_factory=dict)


class UiState(BaseModel):
    """A behaviorally distinct SAP GUI state."""

    state_id: str = ""
    tcode: str = ""
    program: str = ""
    screen_number: int = 0
    title: str = ""
    mode: str = ""
    active_tabs: dict[str, str] = Field(default_factory=dict)
    active_subscreens: list[str] = Field(default_factory=list)
    focused_component: str = ""
    window_stack: list[WindowDescriptor] = Field(default_factory=list)
    modal_signature: str = ""
    components: list[UiComponent] = Field(default_factory=list)
    collections: list[CollectionDescriptor] = Field(default_factory=list)
    normalized_status: dict[str, Any] = Field(default_factory=dict)
    captured_at: str = ""

    def compute_fingerprint(self) -> str:
        return structural_fingerprint(self)

    def ensure_state_id(self) -> "UiState":
        if self.state_id:
            return self
        return self.model_copy(update={"state_id": self.compute_fingerprint()})


class Transition(BaseModel):
    """An observed or approved action-induced state transition."""

    transition_id: str
    from_state_id: str
    to_state_id: str = ""
    action: str
    target_logical_id: str = ""
    target_locator: dict[str, Any] = Field(default_factory=dict)
    parameter_schema: dict[str, Any] = Field(default_factory=dict)
    observed_params: dict[str, Any] = Field(default_factory=dict)
    guards: list[dict[str, Any]] = Field(default_factory=list)
    expected_effects: list[dict[str, Any]] = Field(default_factory=list)
    expected_status: dict[str, Any] = Field(default_factory=dict)
    expected_modal_signature: str = ""
    side_effect: SideEffectClass = SideEffectClass.NAVIGATION
    reversible: bool = False
    idempotent: bool = False
    approval_required: bool = False
    timeout_seconds: float = Field(default=30.0, gt=0)
    retry: dict[str, Any] = Field(default_factory=dict)
    recovery: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    timing_ms: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _commit_safety(self) -> "Transition":
        if self.side_effect in {
            SideEffectClass.COMMIT,
            SideEffectClass.DESTRUCTIVE,
            SideEffectClass.EXTERNAL,
        } and not self.approval_required:
            raise ValueError("commit/destructive/external transition requires approval")
        return self


class UserInputRequest(BaseModel):
    """A durable checkpoint that requires user input before traversal resumes."""

    question: str
    reason: str = ""
    current_state_id: str = ""
    required_fields: list[str | dict[str, Any]] = Field(default_factory=list)
    choices: list[str | dict[str, Any]] = Field(default_factory=list)
    risk: Literal["low", "medium", "high", "critical"] = "medium"
    resume_token: str = ""
    scope: str | dict[str, Any] = ""
    status: Literal["pending", "answered", "resolved", "cancelled", "expired"] = "pending"
    provenance: dict[str, Any] = Field(default_factory=dict)


class SemanticLink(BaseModel):
    """A value-free semantic connection proposed or confirmed during mapping.

    The link is deliberately closed-world: an MCP AI may only confirm one of
    the candidate IDs emitted by the local ontology bridge.  It cannot write a
    free-form business interpretation into a map.
    """

    link_id: str
    subject_kind: Literal["state", "component", "transition"]
    subject_id: str
    predicate: str
    object_id: str
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    status: Literal["proposed", "confirmed", "rejected"] = "proposed"
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)


class UiMap(BaseModel):
    """Versioned transaction state graph."""

    manifest: MapManifest
    states: dict[str, UiState] = Field(default_factory=dict)
    transitions: list[Transition] = Field(default_factory=list)
    pending_interactions: list[UserInputRequest] = Field(default_factory=list)
    semantic_links: list[SemanticLink] = Field(default_factory=list)
    merge_conflicts: list[dict[str, Any]] = Field(default_factory=list)

    def add_state(self, state: UiState) -> UiState:
        resolved = state.ensure_state_id()
        self.states[resolved.state_id] = resolved
        return resolved

    def merge_delta(self, delta: "UiMap") -> dict[str, int]:
        """Merge only new map records while preserving all existing records.

        Identity and environment conflicts reject the complete delta before any
        state, transition, or pending interaction is added.  Reused identifiers
        never overwrite an existing record; differing payloads are retained as
        conflict evidence for later review.
        """

        identity_conflicts = self._identity_conflicts(delta)
        if identity_conflicts:
            self.merge_conflicts.append(
                {
                    "kind": "map_identity",
                    "reason": "delta map identity/environment does not match",
                    "fields": identity_conflicts,
                    "incoming_map_id": delta.manifest.map_id,
                }
            )
            fields = ", ".join(sorted(identity_conflicts))
            raise ValueError(f"cannot merge UiMap delta with conflicting {fields}")

        added_states = 0
        added_transitions = 0
        added_interactions = 0
        conflicts = 0

        for map_key, incoming_state in delta.states.items():
            resolved = incoming_state.ensure_state_id()
            state_id = resolved.state_id or map_key
            existing = self.states.get(state_id)
            if existing is None:
                self.states[state_id] = resolved
                added_states += 1
            elif existing != resolved:
                self.merge_conflicts.append(
                    {
                        "kind": "state",
                        "id": state_id,
                        "reason": "state_id already exists with a different payload",
                        "incoming_map_key": map_key,
                    }
                )
                conflicts += 1

        transitions_by_id = {
            transition.transition_id: transition for transition in self.transitions
        }
        for incoming_transition in delta.transitions:
            transition_id = incoming_transition.transition_id
            existing = transitions_by_id.get(transition_id)
            if existing is None:
                self.transitions.append(incoming_transition)
                transitions_by_id[transition_id] = incoming_transition
                added_transitions += 1
            elif existing != incoming_transition:
                self.merge_conflicts.append(
                    {
                        "kind": "transition",
                        "id": transition_id,
                        "reason": "transition_id already exists with a different payload",
                    }
                )
                conflicts += 1

        interaction_indices = {
            self._interaction_key(interaction): index
            for index, interaction in enumerate(self.pending_interactions)
        }
        for interaction in delta.pending_interactions:
            key = self._interaction_key(interaction)
            if key not in interaction_indices:
                self.pending_interactions.append(interaction)
                interaction_indices[key] = len(self.pending_interactions) - 1
                added_interactions += 1
                continue
            index = interaction_indices[key]
            existing = self.pending_interactions[index]
            if existing == interaction:
                continue
            existing_payload = existing.model_dump(exclude={"status", "provenance"})
            incoming_payload = interaction.model_dump(exclude={"status", "provenance"})
            if existing_payload != incoming_payload:
                self.merge_conflicts.append(
                    {
                        "kind": "interaction",
                        "id": interaction.resume_token,
                        "reason": "interaction identity exists with a different payload",
                    }
                )
                conflicts += 1
                continue
            status_order = {
                "pending": 0,
                "answered": 1,
                "resolved": 2,
                "cancelled": 2,
                "expired": 2,
            }
            if status_order[interaction.status] < status_order[existing.status]:
                continue
            provenance = dict(existing.provenance)
            provenance.update(interaction.provenance)
            self.pending_interactions[index] = existing.model_copy(
                update={"status": interaction.status, "provenance": provenance}
            )

        semantic_by_id = {item.link_id: index for index, item in enumerate(self.semantic_links)}
        status_order = {"proposed": 0, "rejected": 1, "confirmed": 2}
        for incoming in delta.semantic_links:
            index = semantic_by_id.get(incoming.link_id)
            if index is None:
                self.semantic_links.append(incoming)
                semantic_by_id[incoming.link_id] = len(self.semantic_links) - 1
                continue
            existing = self.semantic_links[index]
            if existing.model_dump(exclude={"status", "provenance"}) != incoming.model_dump(
                exclude={"status", "provenance"}
            ):
                self.merge_conflicts.append(
                    {
                        "kind": "semantic_link",
                        "id": incoming.link_id,
                        "reason": "semantic link identity exists with a different payload",
                    }
                )
                conflicts += 1
                continue
            if status_order[incoming.status] < status_order[existing.status]:
                continue
            provenance = dict(existing.provenance)
            provenance.update(incoming.provenance)
            self.semantic_links[index] = existing.model_copy(
                update={"status": incoming.status, "provenance": provenance}
            )

        # Coverage gaps are append-only evidence with an explicit lifecycle.
        # A state's presence cannot prove that an action/branch which failed at
        # that state is now reachable.  In particular, resumed traversal deltas
        # contain the complete graph, so treating every delta state as newly
        # observed would incorrectly resolve unrelated historical failures.
        gaps_by_id: dict[str, dict[str, Any]] = {}
        for index, existing_gap in enumerate(self.manifest.unreachable):
            normalized = self._normalized_coverage_gap(existing_gap)
            self.manifest.unreachable[index] = normalized
            gaps_by_id.setdefault(normalized["gap_id"], normalized)

        for incoming_gap in delta.manifest.unreachable:
            normalized = self._normalized_coverage_gap(incoming_gap)
            gap_id = normalized["gap_id"]
            existing = gaps_by_id.get(gap_id)
            if existing is None:
                self.manifest.unreachable.append(normalized)
                gaps_by_id[gap_id] = normalized
                continue
            if self._coverage_gap_identity(existing) != self._coverage_gap_identity(
                normalized
            ):
                self.merge_conflicts.append(
                    {
                        "kind": "coverage_gap",
                        "id": gap_id,
                        "reason": "gap_id already exists with a different identity",
                    }
                )
                conflicts += 1
                continue
            # Only an explicitly resolved incoming lifecycle may advance an
            # unresolved gap.  Full-copy and unrelated-state deltas retain the
            # unresolved record unchanged, and resolved evidence never regresses.
            if normalized.get("resolved") is True:
                existing["resolved"] = True
                for key in self._coverage_gap_lifecycle_keys():
                    if key in normalized and key != "resolved":
                        existing[key] = normalized[key]

        return {
            "states_added": added_states,
            "transitions_added": added_transitions,
            "interactions_added": added_interactions,
            "conflicts_recorded": conflicts,
        }

    def _identity_conflicts(self, delta: "UiMap") -> dict[str, dict[str, str]]:
        conflicts: dict[str, dict[str, str]] = {}
        if self.manifest.map_id != delta.manifest.map_id:
            conflicts["map_id"] = {
                "existing": self.manifest.map_id,
                "incoming": delta.manifest.map_id,
            }
        if self.manifest.tcode.upper() != delta.manifest.tcode.upper():
            conflicts["tcode"] = {
                "existing": self.manifest.tcode,
                "incoming": delta.manifest.tcode,
            }

        for field_name in (
            "system_id",
            "client",
            "language",
            "api_version",
            "gui_version",
            "application_release",
        ):
            existing = str(getattr(self.manifest, field_name) or "")
            incoming = str(getattr(delta.manifest, field_name) or "")
            if existing and incoming and existing != incoming:
                conflicts[field_name] = {"existing": existing, "incoming": incoming}
        return conflicts

    @staticmethod
    def _interaction_key(interaction: UserInputRequest) -> tuple[str, str, str]:
        return (
            interaction.resume_token,
            interaction.current_state_id,
            interaction.question,
        )

    @staticmethod
    def _coverage_gap_lifecycle_keys() -> set[str]:
        return {
            "resolved",
            "resolved_at",
            "resolved_by_state_id",
            "resolution",
            "resolution_resume_token",
        }

    @classmethod
    def _coverage_gap_identity(cls, item: dict[str, Any]) -> dict[str, Any]:
        lifecycle = cls._coverage_gap_lifecycle_keys()
        return {
            key: value
            for key, value in item.items()
            if key != "gap_id" and key not in lifecycle
        }

    @classmethod
    def _normalized_coverage_gap(cls, item: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(item)
        gap_id = str(normalized.get("gap_id", "")).strip()
        if not gap_id:
            encoded = json.dumps(
                cls._coverage_gap_identity(normalized),
                ensure_ascii=False,
                sort_keys=True,
                default=str,
                separators=(",", ":"),
            )
            gap_id = "gap-" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:20]
        normalized["gap_id"] = gap_id
        return normalized


class TraversalBudget(BaseModel):
    """Hard limits that prevent unbounded automatic exploration."""

    max_depth: int = Field(default=8, ge=0)
    max_states: int = Field(default=250, ge=1)
    max_transitions: int = Field(default=1000, ge=1)
    max_nodes_per_tree: int = Field(default=1000, ge=1)
    max_pages_per_collection: int = Field(default=100, ge=1)
    max_seconds: float = Field(default=900.0, gt=0)
    max_rescans_per_checkpoint: int = Field(default=1, ge=0, le=1)


class TraversalBranchRule(BaseModel):
    """One exact, test-client-only navigation branch allowed during mapping.

    A verb such as ``press`` is deliberately insufficient.  The source state,
    structural component and live SAP id must all match before the production
    adapter emits a candidate.  A bounded VKey inverse is mandatory so the
    traversal engine can prove that the baseline was restored.
    """

    rule_id: str = ""
    action: Literal[
        "press",
        "select_menu",
        "press_toolbar_button",
        "select_context_menu_item",
    ]
    source_state_id: str = ""
    source_fingerprint: str = ""
    target_logical_id: str
    target_sap_id: str
    component_type: Literal[
        "GuiButton",
        "GuiMenu",
        "GuiToolbar",
        "GuiShell",
        "GuiGridView",
        "GuiTableControl",
    ]
    item_id: str = ""
    restore_vkey: Literal[3, 12]
    expected_program: str = ""
    expected_screen_number: int | None = Field(default=None, ge=0)
    expected_modal_signature: str = ""

    @model_validator(mode="after")
    def _closed_contract(self) -> "TraversalBranchRule":
        self.target_logical_id = self.target_logical_id.strip()
        self.target_sap_id = self.target_sap_id.strip()
        self.item_id = self.item_id.strip()
        self.source_state_id = self.source_state_id.strip()
        self.source_fingerprint = self.source_fingerprint.strip()
        self.expected_program = self.expected_program.strip()
        if not self.target_logical_id or not self.target_sap_id:
            raise ValueError("allowlisted branch requires exact logical and SAP ids")
        if not self.source_state_id and not self.source_fingerprint:
            raise ValueError(
                "allowlisted branch requires source_state_id or source_fingerprint"
            )
        if self.action == "press" and self.component_type != "GuiButton":
            raise ValueError("press branch requires GuiButton")
        if self.action == "select_menu" and self.component_type != "GuiMenu":
            raise ValueError("select_menu branch requires GuiMenu")
        if self.action in {"press_toolbar_button", "select_context_menu_item"}:
            if self.component_type not in {
                "GuiToolbar",
                "GuiShell",
                "GuiGridView",
                "GuiTableControl",
            } or not self.item_id:
                raise ValueError("collection branch requires a typed collection control and item_id")
        marker = json.dumps(
            {
                "action": self.action,
                "source_state_id": self.source_state_id,
                "source_fingerprint": self.source_fingerprint,
                "logical_id": self.target_logical_id,
                "sap_id": self.target_sap_id,
                "item_id": self.item_id,
                "restore_vkey": self.restore_vkey,
                "expected_program": self.expected_program,
                "expected_screen_number": self.expected_screen_number,
                "expected_modal_signature": self.expected_modal_signature,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        if re.search(
            r"(?<![a-z])(save|post|delete|release|send|print)(?![a-z])"
            r"|저장|전기|삭제|릴리스|릴리즈|승인|전송|발송|인쇄|출력",
            marker,
            flags=re.I,
        ):
            raise ValueError("dangerous action cannot be allowlisted for traversal")
        if not self.rule_id:
            self.rule_id = "branch-" + hashlib.sha256(
                marker.encode("utf-8")
            ).hexdigest()[:20]
        return self


class TraversalPolicy(BaseModel):
    """Safety policy for passive, guided, and active map discovery."""

    mode: Literal["passive", "guided", "reversible", "allowlisted"] = "passive"
    budget: TraversalBudget = Field(default_factory=TraversalBudget)
    allowed_side_effects: set[SideEffectClass] = Field(
        default_factory=lambda: {SideEffectClass.READ_ONLY}
    )
    allowed_actions: set[str] = Field(default_factory=set)
    allowed_branches: list[TraversalBranchRule] = Field(default_factory=list)
    blocked_actions: set[str] = Field(
        default_factory=lambda: {
            "save",
            "post",
            "delete",
            "release",
            "send",
            "print",
        }
    )
    require_test_client_for_active: bool = True

    @model_validator(mode="after")
    def _mode_safety(self) -> "TraversalPolicy":
        forbidden = {
            SideEffectClass.COMMIT,
            SideEffectClass.DESTRUCTIVE,
            SideEffectClass.EXTERNAL,
        }
        if self.allowed_side_effects & forbidden:
            raise ValueError("automatic traversal cannot allow commit/destructive/external effects")
        if self.mode == "passive" and self.allowed_side_effects != {SideEffectClass.READ_ONLY}:
            raise ValueError("passive traversal may only allow read_only effects")
        if self.allowed_branches and self.mode != "allowlisted":
            raise ValueError("allowed_branches require allowlisted mode")
        return self

    def permits(self, action: str, side_effect: SideEffectClass) -> bool:
        normalized = action.strip().lower().replace("_", "-")
        if normalized in {item.replace("_", "-") for item in self.blocked_actions}:
            return False
        if side_effect not in self.allowed_side_effects:
            return False
        return not self.allowed_actions or normalized in {
            item.strip().lower().replace("_", "-") for item in self.allowed_actions
        }


def structural_fingerprint(state: UiState | dict[str, Any]) -> str:
    """Return a deterministic hash excluding business values and timestamps."""

    value = state if isinstance(state, UiState) else UiState.model_validate(state)
    modal_window_ids = {
        window.sap_id for window in value.window_stack if window.modal
    }
    components = sorted(
        (
            {
                "logical_id": component.logical_id,
                "sap_id": component.sap_id,
                "type": component.type,
                "sub_type": component.sub_type,
                "role": component.role,
                "parent": component.parent_logical_id,
                "children": sorted(component.children),
                "name": component.name,
                "visible": component.visible,
                "enabled": component.enabled,
                "changeable": component.changeable,
                "required": component.required,
                "text": (
                    ""
                    if component.sensitive
                    or not any(
                        component.sap_id == window_id
                        or component.sap_id.startswith(f"{window_id}/")
                        for window_id in modal_window_ids
                    )
                    or component.type in {
                        "GuiTextField",
                        "GuiCTextField",
                        "GuiPasswordField",
                        "GuiOkCodeField",
                        "GuiComboBox",
                        "GuiTextedit",
                    }
                    else normalize_structural_text(component.text)
                ),
                "tooltip": (
                    "" if component.sensitive else normalize_structural_text(component.tooltip)
                ),
                "icon_name": component.icon_name,
                "static_properties": {
                    key: value
                    for key, value in component.static_properties.items()
                    # ComboBox option values can be business-specific. Their
                    # count remains structural; the stored options do not.
                    if key != "entries"
                },
                "supported_actions": sorted(component.supported_actions),
                "sensitive": component.sensitive,
            }
            for component in value.components
        ),
        key=lambda item: (item["logical_id"], item["sap_id"]),
    )
    collections = sorted(
        (
            {
                "component": collection.component_logical_id,
                "kind": collection.kind,
                "columns": [
                    column.model_dump(mode="json")
                    for column in sorted(collection.columns, key=lambda item: (item.position, item.id))
                ],
                "selection_mode": collection.selection_mode,
                "filtered_columns": sorted(collection.filtered_columns),
                "sorted_columns": sorted(collection.sorted_columns),
                "selected_nodes": sorted(collection.selected_node_keys),
                "expanded_nodes": sorted(collection.expanded_node_keys),
                "visible_range": collection.visible_range,
                "horizontal_position": collection.horizontal_position,
                "vertical_position": collection.vertical_position,
                "toolbar_actions": collection.toolbar_actions,
                "context_menu_actions": collection.context_menu_actions,
                "visible_cells": [
                    {
                        "row": cell.absolute_row,
                        "column": cell.column_id,
                        "position": cell.column_position,
                        "type": cell.cell_type,
                        "changeable": cell.changeable,
                        "affordances": sorted(cell.affordances),
                    }
                    for cell in collection.visible_cells
                ],
            }
            for collection in value.collections
        ),
        key=lambda item: item["component"],
    )
    payload = {
        "tcode": value.tcode.upper(),
        "program": value.program,
        "screen_number": value.screen_number,
        "mode": value.mode,
        "active_tabs": dict(sorted(value.active_tabs.items())),
        "active_subscreens": sorted(value.active_subscreens),
        "window_stack": [
            {
                "sap_id": window.sap_id,
                "type": window.type,
                "title": normalize_structural_text(window.title) if window.modal else "",
                "modal": window.modal,
                "active": window.active,
                "signature": (
                    hashlib.sha256(
                        normalize_structural_text(window.title).encode("utf-8")
                    ).hexdigest()[:16]
                    if window.modal
                    else ""
                ),
            }
            for window in value.window_stack
        ],
        "modal_signature": "|".join(
            hashlib.sha256(
                normalize_structural_text(window.title).encode("utf-8")
            ).hexdigest()[:16]
            for window in value.window_stack
            if window.modal
        ),
        "components": components,
        "collections": collections,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


_STRUCTURAL_VALUE_TOKEN = re.compile(
    r"\b(?:[0-9][0-9.,:/_-]*|[a-f0-9]{8}-[a-f0-9-]{27,}|(?=[a-z0-9_-]*\d)[a-z0-9_-]{3,})\b",
    re.IGNORECASE,
)


def normalize_structural_text(value: str) -> str:
    """Keep UI meaning while removing document IDs and other business tokens."""

    normalized = " ".join(str(value or "").split()).casefold()
    return _STRUCTURAL_VALUE_TOKEN.sub("<value>", normalized)


def modal_window_signature(component_type: str, sap_id: str, title: str) -> str:
    """Canonical modal identity shared by scan-time and cheap run-time probes."""

    title_hash = hashlib.sha256(
        normalize_structural_text(title).encode("utf-8")
    ).hexdigest()[:16]
    return f"{component_type}:{sap_id}:{title_hash}"


def legacy_screen_to_state(screen: Any) -> UiState:
    """Convert a legacy ScreenOntology without discarding its known elements."""

    components: list[UiComponent] = []
    collections: list[CollectionDescriptor] = []

    def add_component(
        *,
        sap_id: str,
        component_type: str,
        role: str,
        name: str = "",
        text: str = "",
        **kwargs: Any,
    ) -> str:
        logical_id = f"legacy:{len(components)}:{sap_id}"
        components.append(
            UiComponent(
                logical_id=logical_id,
                sap_id=sap_id,
                type=component_type,
                role=role,
                name=name,
                text=text,
                **kwargs,
            )
        )
        return logical_id

    for field in getattr(screen, "fields", []):
        add_component(
            sap_id=field.id,
            component_type=_enum_value(field.type),
            role="field",
            name=field.name,
            text=field.label,
            changeable=field.changeable,
            required=field.required,
            volatile_properties={"value": field.value},
            static_properties={"max_length": field.max_length},
            supported_actions=["set"] if field.changeable else ["read"],
            sensitive=_enum_value(field.type) == "GuiPasswordField",
        )

    for button in getattr(screen, "buttons", []):
        add_component(
            sap_id=button.id,
            component_type="GuiButton",
            role="button",
            name=button.name,
            text=button.text,
            tooltip=button.tooltip,
            icon_name=button.icon_name,
            supported_actions=["press"],
        )

    for tab in getattr(screen, "tabs", []):
        add_component(
            sap_id=tab.id,
            component_type="GuiTab",
            role="tab",
            name=tab.name,
            text=tab.text,
            tooltip=tab.tooltip,
            supported_actions=["select"],
        )

    for menu in getattr(screen, "menus", []):
        _append_legacy_menu(menu, components, parent="")

    for role, items in (
        ("table", getattr(screen, "tables", [])),
        ("tree", getattr(screen, "trees", [])),
    ):
        for table in items:
            logical_id = add_component(
                sap_id=table.id,
                component_type=_enum_value(table.type),
                role=role,
                name=table.name,
                supported_actions=["read", "select"],
            )
            columns = [
                CollectionColumn(
                    id=column.name,
                    title=column.title,
                    position=column.position,
                )
                for column in table.columns
            ]
            kind = "tree" if role == "tree" else (
                "alv" if getattr(table, "sub_type", "") == "GridView" else "table"
            )
            collections.append(
                CollectionDescriptor(
                    component_logical_id=logical_id,
                    kind=kind,
                    columns=columns,
                    row_count=table.row_count,
                    filtered_columns=list(table.filtered_columns),
                    sorted_columns=list(table.sorted_columns),
                )
            )

    status = getattr(screen, "status", None)
    normalized_status = {}
    if status is not None:
        normalized_status = {
            "type": _enum_value(status.type),
            "message": status.message,
        }

    state = UiState(
        tcode=getattr(screen, "transaction", ""),
        program=getattr(screen, "program", ""),
        screen_number=getattr(screen, "screen_number", 0),
        title=getattr(screen, "title", ""),
        components=components,
        collections=collections,
        normalized_status=normalized_status,
    )
    return state.ensure_state_id()


def _append_legacy_menu(menu: Any, components: list[UiComponent], parent: str) -> str:
    logical_id = f"legacy:{len(components)}:{menu.id}"
    component = UiComponent(
        logical_id=logical_id,
        sap_id=menu.id,
        type="GuiMenu",
        role="menu",
        parent_logical_id=parent,
        text=menu.text,
        supported_actions=["select"],
    )
    components.append(component)
    child_ids = [
        _append_legacy_menu(child, components, logical_id)
        for child in getattr(menu, "children", [])
    ]
    component.children.extend(child_ids)
    return logical_id


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value))
