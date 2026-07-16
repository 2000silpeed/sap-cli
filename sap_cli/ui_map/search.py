"""Deterministic, dependency-free search over a :class:`~.schema.UiMap`.

The index intentionally contains only structural UI metadata.  Business values
and values belonging to sensitive controls are never added to searchable text.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
import json
import re
from typing import Any, Iterable, Literal, Sequence

from .schema import Transition, UiComponent, UiMap, UiState, UserInputRequest


EntityKind = Literal["state", "component", "transition"]
_TOKEN_RE = re.compile(r"[\w./\[\]-]+", re.UNICODE)
_VALUE_KEYS = {
    "actual",
    "credential",
    "credentials",
    "current_value",
    "input",
    "password",
    "secret",
    "token",
    "value",
    "values",
}


def _normalize(value: Any) -> str:
    return " ".join(str(value).casefold().split())


def _tokens(value: str) -> tuple[str, ...]:
    return tuple(_TOKEN_RE.findall(_normalize(value)))


def _safe_text(value: Any, *, key: str = "") -> list[str]:
    """Flatten structural evidence while omitting likely business values."""

    normalized_key = key.casefold().replace("-", "_")
    if normalized_key in _VALUE_KEYS or normalized_key.endswith("_value"):
        return []
    if isinstance(value, dict):
        if value.get("sensitive") is True:
            return []
        result: list[str] = []
        for child_key in sorted(value, key=str):
            result.extend(_safe_text(value[child_key], key=str(child_key)))
        return result
    if isinstance(value, (list, tuple, set)):
        result = []
        for item in value:
            result.extend(_safe_text(item, key=key))
        return result
    if value is None or isinstance(value, (bool, int, float)):
        return []
    return [str(value)]


@dataclass(frozen=True)
class SearchResult:
    """One ranked map-search result."""

    kind: EntityKind
    score: int
    entity_id: str
    state_id: str = ""
    matched_fields: tuple[str, ...] = ()
    entity: UiState | UiComponent | Transition | None = field(
        default=None, compare=False, repr=False
    )


@dataclass(frozen=True)
class ComponentPath:
    """A reachable component and the shortest transitions leading to it."""

    state_id: str
    component: UiComponent
    transitions: tuple[Transition, ...] = ()


class UiMapSearch:
    """In-memory structural index and graph queries for a ``UiMap``."""

    def __init__(self, ui_map: UiMap):
        self.ui_map = ui_map
        self._entries: list[
            tuple[EntityKind, str, str, Any, tuple[tuple[str, str, int], ...]]
        ] = []
        self._build_index()

    def _build_index(self) -> None:
        semantic_by_subject: dict[str, list[str]] = defaultdict(list)
        for link in self.ui_map.semantic_links:
            if link.status == "confirmed":
                semantic_by_subject[link.subject_id].append(link.object_id)
        for state_id in sorted(self.ui_map.states):
            state = self.ui_map.states[state_id]
            self._entries.append(
                (
                    "state",
                    state_id,
                    state_id,
                    state,
                    self._fields(
                        ("tcode", state.tcode, 12),
                        ("program", state.program, 10),
                        ("screen_number", str(state.screen_number), 10),
                        ("title", state.title, 8),
                        ("semantic", " ".join(semantic_by_subject[state_id]), 8),
                    ),
                )
            )
            for component in sorted(
                state.components, key=lambda item: (item.logical_id, item.sap_id)
            ):
                # ``text`` is a business value on editable/input controls and
                # a structural caption on labels/buttons.  Index only the
                # latter; sensitivity is not limited to password controls.
                text = component.text if self._indexes_component_text(component) else ""
                fields = self._fields(
                    ("logical_id", component.logical_id, 14),
                    ("sap_id", component.sap_id, 13),
                    ("name", component.name, 10),
                    ("text", text, 9),
                    ("tooltip", component.tooltip, 8),
                    ("role", component.role, 7),
                    ("type", component.type, 7),
                    ("sub_type", component.sub_type, 6),
                    (
                        "semantic",
                        " ".join(
                            semantic_by_subject[
                                f"{state_id}:{component.logical_id}"
                            ]
                        ),
                        8,
                    ),
                )
                self._entries.append(
                    (
                        "component",
                        component.logical_id,
                        state_id,
                        component,
                        fields,
                    )
                )

        for transition in sorted(
            self.ui_map.transitions, key=lambda item: item.transition_id
        ):
            fields = [
                ("action", transition.action, 12),
                ("target", transition.target_logical_id, 11),
            ]
            fields.extend(
                ("guards", value, 6)
                for value in _safe_text(transition.guards, key="guards")
            )
            fields.extend(
                ("evidence", value, 5)
                for value in _safe_text(transition.evidence, key="evidence")
            )
            fields.extend(
                ("target", value, 8)
                for value in _safe_text(transition.target_locator, key="target")
            )
            self._entries.append(
                (
                    "transition",
                    transition.transition_id,
                    transition.from_state_id,
                    transition,
                    self._fields(*fields),
                )
            )

    @staticmethod
    def _indexes_component_text(component: UiComponent) -> bool:
        if component.sensitive:
            return False
        if component.role.casefold() in {"field", "input", "password", "value"}:
            return False
        component_type = component.type.casefold()
        return not any(
            marker in component_type
            for marker in ("textfield", "password", "combobox")
        )

    @staticmethod
    def _fields(*items: tuple[str, Any, int]) -> tuple[tuple[str, str, int], ...]:
        return tuple(
            (name, normalized, weight)
            for name, value, weight in items
            if (normalized := _normalize(value))
        )

    def search(
        self,
        query: str,
        *,
        kinds: Iterable[EntityKind] | None = None,
        limit: int | None = 50,
    ) -> list[SearchResult]:
        """Search all structural fields with stable scoring and tie-breaking."""

        normalized = _normalize(query)
        if not normalized or limit == 0:
            return []
        query_tokens = _tokens(normalized)
        selected = set(kinds or ("state", "component", "transition"))
        results: list[SearchResult] = []
        for kind, entity_id, state_id, entity, fields in self._entries:
            if kind not in selected:
                continue
            score = 0
            matched: list[str] = []
            for field_name, value, weight in fields:
                field_score = self._field_score(normalized, query_tokens, value, weight)
                if field_score:
                    score += field_score
                    matched.append(field_name)
            if score:
                results.append(
                    SearchResult(
                        kind=kind,
                        score=score,
                        entity_id=entity_id,
                        state_id=state_id,
                        matched_fields=tuple(dict.fromkeys(matched)),
                        entity=entity,
                    )
                )
        results.sort(
            key=lambda item: (-item.score, item.kind, item.state_id, item.entity_id)
        )
        return results if limit is None else results[: max(0, limit)]

    @staticmethod
    def _field_score(
        query: str, query_tokens: Sequence[str], value: str, weight: int
    ) -> int:
        if value == query:
            return weight * 100
        if value.startswith(query):
            return weight * 70
        value_tokens = set(_tokens(value))
        if query in value_tokens:
            return weight * 60
        if query in value:
            return weight * 40
        if query_tokens and all(token in value_tokens for token in query_tokens):
            return weight * 35 + len(query_tokens)
        if query_tokens and all(token in value for token in query_tokens):
            return weight * 20 + len(query_tokens)
        return 0

    def search_states(self, query: str, *, limit: int | None = 50) -> list[SearchResult]:
        return self.search(query, kinds=("state",), limit=limit)

    def search_components(
        self, query: str, *, limit: int | None = 50
    ) -> list[SearchResult]:
        return self.search(query, kinds=("component",), limit=limit)

    def search_transitions(
        self, query: str, *, limit: int | None = 50
    ) -> list[SearchResult]:
        return self.search(query, kinds=("transition",), limit=limit)

    def shortest_path(
        self,
        from_state_id: str,
        to_state_id: str,
        *,
        excluded_actions: Iterable[str] = (),
    ) -> list[Transition] | None:
        """Return the deterministic shortest directed path, or ``None``."""

        if from_state_id not in self.ui_map.states or to_state_id not in self.ui_map.states:
            return None
        if from_state_id == to_state_id:
            return []
        excluded = {
            _normalize(item).replace("-", "_").replace(" ", "_")
            for item in excluded_actions
        }
        adjacency: dict[str, list[Transition]] = defaultdict(list)
        for transition in self.ui_map.transitions:
            action = _normalize(transition.action).replace("-", "_").replace(" ", "_")
            if transition.to_state_id and action not in excluded:
                adjacency[transition.from_state_id].append(transition)
        for transitions in adjacency.values():
            transitions.sort(key=lambda item: (item.to_state_id, item.transition_id))

        queue: deque[str] = deque([from_state_id])
        previous: dict[str, tuple[str, Transition]] = {}
        seen = {from_state_id}
        while queue:
            state_id = queue.popleft()
            for transition in adjacency.get(state_id, ()):
                target = transition.to_state_id
                if target in seen or target not in self.ui_map.states:
                    continue
                seen.add(target)
                previous[target] = (state_id, transition)
                if target == to_state_id:
                    return self._reconstruct_path(previous, from_state_id, target)
                queue.append(target)
        return None

    @staticmethod
    def _reconstruct_path(
        previous: dict[str, tuple[str, Transition]], start: str, end: str
    ) -> list[Transition]:
        path: list[Transition] = []
        current = end
        while current != start:
            current, transition = previous[current]
            path.append(transition)
        path.reverse()
        return path

    def paths_to_component(
        self,
        component_query: str,
        *,
        from_state_id: str | None = None,
        limit: int | None = 50,
        excluded_actions: Iterable[str] = (),
    ) -> list[ComponentPath]:
        """Find matching components and shortest paths from a graph root/state."""

        roots = [from_state_id] if from_state_id is not None else self._root_states()
        matches = self.search_components(component_query, limit=None)
        paths: list[ComponentPath] = []
        for match in matches:
            assert isinstance(match.entity, UiComponent)
            best: list[Transition] | None = None
            for root in roots:
                candidate = self.shortest_path(
                    root,
                    match.state_id,
                    excluded_actions=excluded_actions,
                )
                if candidate is not None and (
                    best is None
                    or len(candidate) < len(best)
                    or (
                        len(candidate) == len(best)
                        and tuple(item.transition_id for item in candidate)
                        < tuple(item.transition_id for item in best)
                    )
                ):
                    best = candidate
            if best is not None:
                paths.append(
                    ComponentPath(match.state_id, match.entity, tuple(best))
                )
        paths.sort(
            key=lambda item: (
                len(item.transitions),
                item.state_id,
                item.component.logical_id,
            )
        )
        return paths if limit is None else paths[: max(0, limit)]

    def _root_states(self) -> list[str]:
        incoming = {
            transition.to_state_id
            for transition in self.ui_map.transitions
            if transition.to_state_id in self.ui_map.states
        }
        roots = sorted(set(self.ui_map.states) - incoming)
        return roots or sorted(self.ui_map.states)[:1]

    def unresolved_interactions(self) -> list[UserInputRequest]:
        """Return pending user checkpoints in deterministic order."""

        unresolved = {
            "pending",
            "answered",
        }
        return sorted(
            (
                item
                for item in self.ui_map.pending_interactions
                if item.status in unresolved
            ),
            key=lambda item: (
                item.current_state_id,
                item.resume_token,
                item.question,
            ),
        )

    def unreachable_coverage(self) -> list[dict[str, Any]]:
        """Return a deterministic, duplicate-free view of coverage gaps."""

        unique: dict[str, dict[str, Any]] = {}
        for item in self.ui_map.manifest.unreachable:
            if item.get("resolved") is True:
                continue
            value = dict(item)
            key = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
            unique.setdefault(key, value)
        for state_id in sorted(self.ui_map.states):
            state = self.ui_map.states[state_id]
            for collection in sorted(
                state.collections, key=lambda item: item.component_logical_id
            ):
                coverage = collection.coverage
                if not coverage:
                    continue
                incomplete = coverage.get("complete") is False or any(
                    key in coverage
                    for key in ("unreachable", "missing", "reason", "reasons")
                )
                if incomplete:
                    value = {
                        "state_id": state_id,
                        "component_logical_id": collection.component_logical_id,
                        "coverage": coverage,
                    }
                    key = json.dumps(
                        value, ensure_ascii=False, sort_keys=True, default=str
                    )
                    unique.setdefault(key, value)
        return [unique[key] for key in sorted(unique)]


def search_map(
    ui_map: UiMap,
    query: str,
    *,
    kinds: Iterable[EntityKind] | None = None,
    limit: int | None = 50,
) -> list[SearchResult]:
    """Convenience wrapper for one-off searches."""

    return UiMapSearch(ui_map).search(query, kinds=kinds, limit=limit)


__all__ = ["ComponentPath", "SearchResult", "UiMapSearch", "search_map"]
