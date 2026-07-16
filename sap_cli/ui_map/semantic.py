"""Value-free ontology candidates for MCP-AI-guided UI map construction.

This module never calls an LLM.  It turns a scanned UI state and the local SAP
ontology into a closed set of auditable candidates.  The MCP AI may review and
confirm candidate IDs, while the map keeps the final semantic edges separate
from structural navigation evidence.
"""

from __future__ import annotations

import hashlib
import json
from typing import Iterable

from sap_cli.ontology.store import OntologyStore

from .schema import SemanticLink, UiComponent, UiState


class MapSemanticReviewer:
    """Propose semantic links from local ontology and structural UI metadata."""

    def __init__(self, store: OntologyStore | None = None) -> None:
        self.store = store or OntologyStore()

    def propose(self, state: UiState) -> list[SemanticLink]:
        """Return deterministic, business-value-free candidate links."""

        candidates: list[SemanticLink] = []
        ontology = self.store.load_transaction(state.tcode) if state.tcode else None
        if ontology is not None:
            screen = ontology.get_screen(state.screen_number)
            if screen is not None:
                candidates.append(
                    self._link(
                        subject_kind="state",
                        subject_id=state.state_id,
                        predicate="maps_to_ontology_screen",
                        object_id=f"ontology_screen:{state.tcode}:{screen.screen_number}",
                        confidence=1.0,
                        evidence=[{"match": "tcode_and_screen_number"}],
                    )
                )
                for component in state.components:
                    if component.sensitive:
                        continue
                    field = self._matching_field(component, screen.fields)
                    if field is None:
                        continue
                    candidates.append(
                        self._link(
                            subject_kind="component",
                            subject_id=self._component_subject(state, component),
                            predicate="maps_to_ontology_field",
                            object_id=(
                                f"ontology_field:{state.tcode}:{screen.screen_number}:"
                                f"{field.id}"
                            ),
                            confidence=0.95,
                            evidence=[{"match": "stable_component_identifier"}],
                        )
                    )

        # A small closed vocabulary lets a reviewing AI add useful meaning to
        # a new transaction before a dedicated ontology screen exists.
        for role in self._state_roles(state):
            candidates.append(
                self._link(
                    subject_kind="state",
                    subject_id=state.state_id,
                    predicate="has_ui_role",
                    object_id=f"ui_role:{role}",
                    confidence=0.55,
                    evidence=[{"match": "structural_ui_role"}],
                )
            )
        return sorted(candidates, key=lambda item: item.link_id)

    @staticmethod
    def _matching_field(component: UiComponent, fields: Iterable[object]):
        component_ids = {
            value.casefold()
            for value in (component.sap_id, component.name, component.logical_id)
            if value
        }
        for field in fields:
            identifiers = {
                str(getattr(field, name, "") or "").casefold()
                for name in ("id", "name")
            }
            semantic = getattr(field, "semantic", None)
            identifiers.add(str(getattr(semantic, "sap_field_name", "") or "").casefold())
            identifiers.discard("")
            if component_ids & identifiers:
                return field
        return None

    @staticmethod
    def _state_roles(state: UiState) -> set[str]:
        roles: set[str] = set()
        if state.modal_signature:
            roles.add("modal")
        if state.collections:
            roles.add("overview")
        if any(component.changeable is True for component in state.components):
            roles.add("selection")
        if not roles:
            roles.add("detail")
        return roles

    @staticmethod
    def _component_subject(state: UiState, component: UiComponent) -> str:
        return f"{state.state_id}:{component.logical_id}"

    @staticmethod
    def _link(
        *,
        subject_kind: str,
        subject_id: str,
        predicate: str,
        object_id: str,
        confidence: float,
        evidence: list[dict[str, str]],
    ) -> SemanticLink:
        identity = [subject_kind, subject_id, predicate, object_id]
        link_id = "semantic-" + hashlib.sha256(
            json.dumps(identity, ensure_ascii=False, separators=(",", ":")).encode()
        ).hexdigest()[:24]
        return SemanticLink(
            link_id=link_id,
            subject_kind=subject_kind,  # type: ignore[arg-type]
            subject_id=subject_id,
            predicate=predicate,
            object_id=object_id,
            confidence=confidence,
            evidence=evidence,
            provenance={"source": "local_ontology_bridge", "business_values_included": False},
        )


__all__ = ["MapSemanticReviewer"]
