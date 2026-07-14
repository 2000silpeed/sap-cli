"""Conservative SAP GUI adapter for reversible UI-map traversal.

Only three typed actions are accepted: selecting a tab, moving either axis of
a collection scrollbar by one page, and expanding a tree node. Unsafe buttons,
menus, and collection actions are reported as one bounded user-guidance gap per
state; the adapter deliberately has no generic press/menu/toolbar escape hatch.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any, Iterable

from .schema import (
    SideEffectClass,
    TraversalBranchRule,
    Transition,
    UiComponent,
    UiMap,
    UiState,
    UserInputRequest,
)
from .traversal import TraversalCandidate
from .search import UiMapSearch


_COLLECTION_KINDS = {"table", "grid", "alv", "apo_grid", "tree"}
_MAX_MANUAL_BRANCH_DESCRIPTORS = 32


class SapTraversalAdapter:
    """Translate a saved :class:`UiState` into strictly reversible SAP calls."""

    def __init__(
        self,
        session: Any,
        *,
        branch_rules: Iterable[TraversalBranchRule | dict[str, Any]] = (),
    ) -> None:
        self._session = session
        self._restore_tokens: list[dict[str, Any]] = []
        self._reach_graph: UiMap | None = None
        self.configure_branch_rules(branch_rules)

    def configure_branch_rules(
        self, rules: Iterable[TraversalBranchRule | dict[str, Any]]
    ) -> None:
        parsed = [
            item
            if isinstance(item, TraversalBranchRule)
            else TraversalBranchRule.model_validate(item)
            for item in rules
        ]
        self._branch_rules = {item.rule_id: item for item in parsed}

    def configure_reach_graph(self, ui_map: UiMap) -> None:
        self._reach_graph = ui_map

    @property
    def session(self) -> Any:
        return self._session

    @property
    def pending_restore_token(self) -> dict[str, Any] | None:
        """Return a copy suitable for a durable diagnostic/checkpoint record."""

        return deepcopy(self._restore_tokens[-1]) if self._restore_tokens else None

    def discover(self, state: UiState) -> Iterable[TraversalCandidate]:
        """Yield only typed actions whose inverse is known before execution."""

        components = {item.logical_id: item for item in state.components}
        yield from self._tab_candidates(state, components)
        yield from self._scroll_candidates(state, components)
        yield from self._tree_candidates(state, components)
        yield from self._branch_candidates(state, components)
        manual_gap = self._manual_branch_gap(state)
        if manual_gap is not None:
            yield manual_gap

    def execute(self, candidate: TraversalCandidate) -> dict[str, Any] | None:
        """Execute one allowlisted action after recording its exact inverse."""

        action = candidate.action.strip().lower().replace("-", "_")
        if action == "select_tab":
            return self._execute_select_tab(candidate)
        if action == "scroll_to":
            return self._execute_scroll(candidate)
        if action == "expand_node":
            return self._execute_expand(candidate)
        if action in {
            "press",
            "select_menu",
            "press_toolbar_button",
            "select_context_menu_item",
        }:
            return self._execute_branch(candidate)
        raise ValueError(f"unsupported traversal action: {candidate.action}")

    def restore(self, baseline: UiState) -> None:
        """Apply the action-specific inverse captured immediately before execute."""

        del baseline  # The inverse token is more exact than a reconstructed guess.
        if not self._restore_tokens:
            return
        token = self._restore_tokens.pop()
        action = token["action"]
        if action == "select_tab":
            tab = self._find(token["previous_tab_sap_id"])
            self._require_type(tab, {"GuiTab"}, "tab restore")
            tab.Select()
            return
        if action == "scroll_to":
            control = self._find(token["sap_id"])
            self._scrollbar(control, token.get("axis", "vertical")).Position = int(
                token["position"]
            )
            return
        if action == "expand_node":
            if not token["was_expanded"]:
                tree = self._find(token["sap_id"])
                self._require_tree(tree)
                tree.CollapseNode(token["node_key"])
            return
        if action == "allowlisted_branch":
            window = getattr(self._session, "ActiveWindow", None)
            if window is None:
                window = self._find("wnd[0]")
            sender = getattr(window, "SendVKey", None)
            if not callable(sender):
                sender = getattr(window, "sendVKey", None)
            if not callable(sender):
                raise AttributeError("active window does not expose SendVKey")
            sender(int(token["restore_vkey"]))
            return
        raise ValueError(f"unknown restore token action: {action}")

    def reach(self, state: UiState) -> bool:
        """Safely reproduce saved tab, scroll, and expanded-tree state."""

        components = {item.logical_id: item for item in state.components}
        try:
            if not self._reach_navigation_state(state):
                return False
            for logical_id in state.active_tabs.values():
                component = components.get(logical_id)
                if component is None or component.type != "GuiTab":
                    return False
                tab = self._find(component.sap_id)
                self._require_type(tab, {"GuiTab"}, "tab reach")
                tab.Select()

            for collection in state.collections:
                component = components.get(collection.component_logical_id)
                if component is None:
                    return False
                control = None
                if collection.horizontal_position is not None:
                    control = self._find(component.sap_id)
                    self._scrollbar(control, "horizontal").Position = int(
                        collection.horizontal_position
                    )
                if collection.vertical_position is not None:
                    control = control or self._find(component.sap_id)
                    self._scrollbar(control, "vertical").Position = int(
                        collection.vertical_position
                    )

                if collection.kind != "tree":
                    continue
                tree = self._find(component.sap_id)
                self._require_tree(tree)
                desired = set(collection.expanded_node_keys)
                nodes = sorted(
                    collection.nodes,
                    key=lambda node: node.depth if node.depth is not None else 0,
                )
                # Collapse deepest-first, then expand parents before descendants.
                for node in reversed(nodes):
                    if node.key not in desired and self._is_expanded(tree, node.key):
                        tree.CollapseNode(node.key)
                for node in nodes:
                    if node.key in desired and not self._is_expanded(tree, node.key):
                        tree.ExpandNode(node.key)
            return True
        except Exception:
            return False

    def _reach_navigation_state(self, target: UiState) -> bool:
        """Replay an exact saved path when the live dynpro is not the target."""

        info = getattr(self._session, "Info", None)
        live_program = str(getattr(info, "Program", "") or "")
        try:
            live_screen = int(getattr(info, "ScreenNumber", -1))
        except (TypeError, ValueError):
            live_screen = -1
        if (
            (not target.program or target.program == live_program)
            and (not target.screen_number or target.screen_number == live_screen)
        ):
            return True
        graph = self._reach_graph
        if graph is None:
            return False
        current_ids = [
            state_id
            for state_id, item in sorted(graph.states.items())
            if (not item.program or item.program == live_program)
            and (not item.screen_number or item.screen_number == live_screen)
        ]
        index = UiMapSearch(graph)
        paths = [
            path
            for state_id in current_ids
            if (path := index.shortest_path(state_id, target.state_id)) is not None
            and all(item.action != "user_guided_navigation" for item in path)
        ]
        if not paths:
            return False
        path = min(
            paths,
            key=lambda items: (len(items), tuple(item.transition_id for item in items)),
        )
        for transition in path:
            if not self._replay_transition(transition):
                return False
        info = getattr(self._session, "Info", None)
        reached_program = str(getattr(info, "Program", "") or "")
        try:
            reached_screen = int(getattr(info, "ScreenNumber", -1))
        except (TypeError, ValueError):
            reached_screen = -1
        return (
            (not target.program or target.program == reached_program)
            and (not target.screen_number or target.screen_number == reached_screen)
        )

    def _replay_transition(self, transition: Transition) -> bool:
        action = transition.action.strip().lower().replace("-", "_")
        if action == "select_tab":
            sap_id = str(transition.target_locator.get("sap_id", ""))
            if not sap_id:
                return False
            tab = self._find(sap_id)
            self._require_type(tab, {"GuiTab"}, "reach select_tab")
            tab.Select()
            return True
        if action not in {
            "press",
            "select_menu",
            "press_toolbar_button",
            "select_context_menu_item",
        }:
            return False
        rule = next(
            (
                item
                for item in self._branch_rules.values()
                if item.action == action
                and item.target_logical_id == transition.target_logical_id
                and item.target_sap_id
                == str(transition.target_locator.get("sap_id", ""))
            ),
            None,
        )
        if rule is None:
            return False
        candidate = TraversalCandidate(
            action=rule.action,
            target_logical_id=rule.target_logical_id,
            target_locator={
                "sap_id": rule.target_sap_id,
                "component_type": rule.component_type,
                "item_id": rule.item_id,
                "branch_rule_id": rule.rule_id,
            },
            params={
                "branch_rule_id": rule.rule_id,
                "restore_vkey": rule.restore_vkey,
                **({"item_id": rule.item_id} if rule.item_id else {}),
            },
        )
        self._execute_branch(candidate, record_restore=False)
        return True

    def apply_user_input(self, item: UserInputRequest, answer: Any) -> bool:
        """Acknowledge an answer without ever writing it into a COM component."""

        del item
        return answer is not None

    def _tab_candidates(
        self, state: UiState, components: dict[str, UiComponent]
    ) -> Iterable[TraversalCandidate]:
        for tab in state.components:
            if tab.type != "GuiTab" or tab.visible is False or tab.enabled is False:
                continue
            active_logical_id = state.active_tabs.get(tab.parent_logical_id)
            active = components.get(active_logical_id or "")
            if (
                active is None
                or active.type != "GuiTab"
                or active.logical_id == tab.logical_id
                or tab.selected is True
            ):
                continue
            yield TraversalCandidate(
                action="select_tab",
                target_logical_id=tab.logical_id,
                target_locator={"sap_id": tab.sap_id, "component_type": "GuiTab"},
                params={"previous_tab_sap_id": active.sap_id},
                side_effect=SideEffectClass.REVERSIBLE,
                reversible=True,
                evidence=[{"source": "ui_state", "typed_action": "GuiTab.Select"}],
            )

    def _scroll_candidates(
        self, state: UiState, components: dict[str, UiComponent]
    ) -> Iterable[TraversalCandidate]:
        for collection in state.collections:
            if collection.kind not in _COLLECTION_KINDS:
                continue
            component = components.get(collection.component_logical_id)
            if component is None:
                continue
            for axis, saved_position in (
                ("horizontal", collection.horizontal_position),
                ("vertical", collection.vertical_position),
            ):
                if saved_position is None:
                    continue
                try:
                    scrollbar = self._scrollbar(self._find(component.sap_id), axis)
                    current = int(saved_position)
                    maximum = int(scrollbar.Maximum)
                    page_size = max(1, int(scrollbar.PageSize))
                except Exception:
                    continue
                target = min(maximum, current + page_size)
                if target <= current:
                    continue
                yield TraversalCandidate(
                    action="scroll_to",
                    target_logical_id=component.logical_id,
                    target_locator={
                        "sap_id": component.sap_id,
                        "component_type": component.type,
                        "collection_kind": collection.kind,
                        "axis": axis,
                    },
                    params={
                        "axis": axis,
                        "position": target,
                        "previous_position": current,
                        "page_size": page_size,
                    },
                    side_effect=SideEffectClass.REVERSIBLE,
                    reversible=True,
                    page_cost=1,
                    evidence=[
                        {"source": "ui_state", "typed_action": f"{axis}_scroll"}
                    ],
                )

    def _manual_branch_gap(self, state: UiState) -> TraversalCandidate | None:
        descriptors: list[dict[str, Any]] = []
        for component in state.components:
            if (
                component.type in {"GuiButton", "GuiMenu"}
                and component.visible is not False
                and component.enabled is not False
                and not self._component_is_allowlisted(state, component)
            ):
                descriptors.append(
                    {"kind": component.type, "structural_id": component.logical_id}
                )
        for collection in state.collections:
            collection_id = collection.component_logical_id
            for position, action in enumerate(collection.toolbar_actions):
                if isinstance(action, dict) and (
                    action.get("enabled") is False or action.get("visible") is False
                ):
                    continue
                if self._collection_action_is_allowlisted(
                    state, collection_id, action, position, "press_toolbar_button"
                ):
                    continue
                descriptors.append(
                    {
                        "kind": "collection_toolbar_action",
                        "structural_id": self._action_structural_id(
                            collection_id, "toolbar", action, position
                        ),
                    }
                )
            for position, action in enumerate(collection.context_menu_actions):
                if isinstance(action, dict) and (
                    action.get("enabled") is False or action.get("visible") is False
                ):
                    continue
                if self._collection_action_is_allowlisted(
                    state, collection_id, action, position, "select_context_menu_item"
                ):
                    continue
                descriptors.append(
                    {
                        "kind": "collection_context_menu_action",
                        "structural_id": self._action_structural_id(
                            collection_id, "context", action, position
                        ),
                    }
                )
            context_status = str(collection.coverage.get("context_menu_actions", ""))
            if any(
                marker in context_status.casefold()
                for marker in ("partial", "not_observed", "unobserved", "unknown")
            ):
                descriptors.append(
                    {
                        "kind": "collection_context_menu_unobserved",
                        "structural_id": f"{collection_id}:context_menu",
                    }
                )
        if not descriptors:
            return None
        descriptors.sort(key=lambda item: (item["kind"], item["structural_id"]))
        total = len(descriptors)
        bounded = descriptors[:_MAX_MANUAL_BRANCH_DESCRIPTORS]
        identity = json.dumps(
            {"state_id": state.state_id, "branches": bounded, "total": total},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        resume_token = "manual-gap-" + hashlib.sha256(identity.encode()).hexdigest()[:24]
        request = UserInputRequest(
            question=(
                f"자동 실행하지 않은 구조적 UI 분기 {total}개가 있습니다. "
                "필요한 화면으로 직접 이동한 뒤 계속해 주세요."
            ),
            reason="unsafe_actionable_branches_require_user_guidance",
            required_fields=["continue"],
            risk="medium",
            resume_token=resume_token,
            scope={
                "coverage_branches": bounded,
                "branch_count": total,
                "truncated": total > len(bounded),
            },
            provenance={
                "source": "sap_traversal_adapter",
                "requires_distinct_screen": True,
                "answer_value_stored": False,
            },
        )
        return TraversalCandidate(
            action="manual_branch_gap",
            side_effect=SideEffectClass.NAVIGATION,
            reversible=False,
            needs_user_input=request,
            evidence=[{"source": "ui_state", "typed_action": "manual_guidance"}],
        )

    def _branch_candidates(
        self, state: UiState, components: dict[str, UiComponent]
    ) -> Iterable[TraversalCandidate]:
        for rule in sorted(self._branch_rules.values(), key=lambda item: item.rule_id):
            if not self._rule_matches_state(rule, state):
                continue
            component = components.get(rule.target_logical_id)
            if (
                component is None
                or component.sap_id != rule.target_sap_id
                or component.visible is False
                or component.enabled is False
            ):
                continue
            if rule.action == "press" and component.type != "GuiButton":
                continue
            if rule.action == "select_menu" and component.type != "GuiMenu":
                continue
            if rule.action in {"press_toolbar_button", "select_context_menu_item"}:
                collection = next(
                    (
                        item
                        for item in state.collections
                        if item.component_logical_id == component.logical_id
                    ),
                    None,
                )
                if collection is None:
                    continue
                actions = (
                    collection.toolbar_actions
                    if rule.action == "press_toolbar_button"
                    else collection.context_menu_actions
                )
                if not any(self._collection_item_id(item, pos) == rule.item_id for pos, item in enumerate(actions)):
                    continue
            yield TraversalCandidate(
                action=rule.action,
                target_logical_id=rule.target_logical_id,
                target_locator={
                    "sap_id": rule.target_sap_id,
                    "component_type": rule.component_type,
                    "item_id": rule.item_id,
                    "branch_rule_id": rule.rule_id,
                },
                params={
                    "branch_rule_id": rule.rule_id,
                    "restore_vkey": rule.restore_vkey,
                    **({"item_id": rule.item_id} if rule.item_id else {}),
                },
                side_effect=SideEffectClass.NAVIGATION,
                reversible=False,
                expected_modal_signature=rule.expected_modal_signature,
                expected_program=rule.expected_program,
                expected_screen_number=rule.expected_screen_number,
                evidence=[
                    {
                        "source": "exact_allowlisted_branch",
                        "branch_rule_id": rule.rule_id,
                    }
                ],
            )

    @staticmethod
    def _rule_matches_state(rule: TraversalBranchRule, state: UiState) -> bool:
        if rule.source_state_id and rule.source_state_id != state.state_id:
            return False
        if rule.source_fingerprint and rule.source_fingerprint != state.compute_fingerprint():
            return False
        return True

    def _component_is_allowlisted(self, state: UiState, component: UiComponent) -> bool:
        expected_action = "press" if component.type == "GuiButton" else "select_menu"
        return any(
            rule.action == expected_action
            and self._rule_matches_state(rule, state)
            and rule.target_logical_id == component.logical_id
            and rule.target_sap_id == component.sap_id
            for rule in self._branch_rules.values()
        )

    def _collection_action_is_allowlisted(
        self,
        state: UiState,
        collection_id: str,
        action: Any,
        position: int,
        expected_action: str,
    ) -> bool:
        item_id = self._collection_item_id(action, position)
        return any(
            rule.action == expected_action
            and self._rule_matches_state(rule, state)
            and rule.target_logical_id == collection_id
            and rule.item_id == item_id
            for rule in self._branch_rules.values()
        )

    @staticmethod
    def _collection_item_id(action: Any, position: int) -> str:
        if isinstance(action, dict):
            for key in ("id", "key", "position"):
                value = action.get(key)
                if value is not None and str(value):
                    return str(value)
        return str(position)

    @staticmethod
    def _action_structural_id(
        collection_id: str, area: str, action: Any, position: int
    ) -> str:
        action_id = ""
        if isinstance(action, dict):
            for key in ("id", "key", "position"):
                value = action.get(key)
                if value is not None and str(value):
                    action_id = str(value)
                    break
        return f"{collection_id}:{area}:{action_id or position}"

    def _tree_candidates(
        self, state: UiState, components: dict[str, UiComponent]
    ) -> Iterable[TraversalCandidate]:
        for collection in state.collections:
            if collection.kind != "tree":
                continue
            component = components.get(collection.component_logical_id)
            if component is None:
                continue
            for node in collection.nodes:
                if node.expandable is not True or node.expanded is not False:
                    continue
                yield TraversalCandidate(
                    action="expand_node",
                    target_logical_id=component.logical_id,
                    target_locator={
                        "sap_id": component.sap_id,
                        "component_type": component.type,
                        "collection_kind": "tree",
                    },
                    params={"node_key": node.key, "was_expanded": False},
                    side_effect=SideEffectClass.REVERSIBLE,
                    reversible=True,
                    node_cost=1,
                    evidence=[{"source": "ui_state", "typed_action": "GuiTree.ExpandNode"}],
                )

    def _execute_select_tab(self, candidate: TraversalCandidate) -> dict[str, Any]:
        sap_id = self._candidate_sap_id(candidate)
        previous = str(candidate.params.get("previous_tab_sap_id", ""))
        if not previous:
            raise ValueError("select_tab requires previous_tab_sap_id")
        tab = self._find(sap_id)
        prior_tab = self._find(previous)
        self._require_type(tab, {"GuiTab"}, "select_tab target")
        self._require_type(prior_tab, {"GuiTab"}, "select_tab inverse")
        self._push_restore(
            {"action": "select_tab", "previous_tab_sap_id": previous}
        )
        tab.Select()
        return {"action": "select_tab", "sap_id": sap_id}

    def _execute_scroll(self, candidate: TraversalCandidate) -> dict[str, Any]:
        sap_id = self._candidate_sap_id(candidate)
        if candidate.target_locator.get("collection_kind") not in _COLLECTION_KINDS:
            raise ValueError("scroll target is not a typed collection")
        control = self._find(sap_id)
        axis = str(candidate.params.get("axis", "vertical")).casefold()
        if axis not in {"horizontal", "vertical"}:
            raise ValueError("scroll axis must be horizontal or vertical")
        scrollbar = self._scrollbar(control, axis)
        previous = int(scrollbar.Position)
        expected_previous = int(candidate.params["previous_position"])
        if previous != expected_previous:
            raise ValueError(
                "scroll state drift: live position differs from previous_position"
            )
        target = int(candidate.params["position"])
        maximum = int(scrollbar.Maximum)
        live_page_size = max(1, int(scrollbar.PageSize))
        expected_page_size = int(candidate.params.get("page_size", live_page_size))
        if expected_page_size != live_page_size:
            raise ValueError("scroll state drift: live page size differs from candidate")
        if target != min(maximum, previous + live_page_size) or target <= previous:
            raise ValueError("scroll target must be a bounded forward page")
        self._push_restore(
            {
                "action": "scroll_to",
                "sap_id": sap_id,
                "axis": axis,
                "position": previous,
            }
        )
        scrollbar.Position = target
        return {"action": "scroll_to", "axis": axis, "position": target}

    def _execute_expand(self, candidate: TraversalCandidate) -> dict[str, Any]:
        sap_id = self._candidate_sap_id(candidate)
        if candidate.target_locator.get("collection_kind") != "tree":
            raise ValueError("expand target is not a typed tree")
        node_key = str(candidate.params.get("node_key", ""))
        if not node_key:
            raise ValueError("expand_node requires node_key")
        tree = self._find(sap_id)
        self._require_tree(tree)
        was_expanded = self._is_expanded(tree, node_key)
        if "was_expanded" not in candidate.params:
            raise ValueError("expand_node requires was_expanded")
        expected_expanded = bool(candidate.params["was_expanded"])
        if expected_expanded:
            raise ValueError("automatic expand_node requires was_expanded=false")
        if was_expanded != expected_expanded:
            raise ValueError(
                "tree state drift: live expansion differs from was_expanded"
            )
        self._push_restore(
            {
                "action": "expand_node",
                "sap_id": sap_id,
                "node_key": node_key,
                "was_expanded": expected_expanded,
            }
        )
        if not was_expanded:
            tree.ExpandNode(node_key)
        return {"action": "expand_node", "node_key": node_key}

    def _execute_branch(
        self, candidate: TraversalCandidate, *, record_restore: bool = True
    ) -> dict[str, Any]:
        rule_id = str(candidate.params.get("branch_rule_id", ""))
        rule = self._branch_rules.get(rule_id)
        if rule is None:
            raise ValueError(
                "unsupported traversal action: allowlisted branch has no exact rule"
            )
        if (
            candidate.action != rule.action
            or candidate.target_logical_id != rule.target_logical_id
            or self._candidate_sap_id(candidate) != rule.target_sap_id
            or str(candidate.target_locator.get("item_id", "")) != rule.item_id
        ):
            raise ValueError("allowlisted branch candidate does not match its exact rule")
        target = self._find(rule.target_sap_id)
        live_type = str(getattr(target, "Type", ""))
        if live_type != rule.component_type:
            raise TypeError(
                f"allowlisted branch live type mismatch: {live_type or '<unknown>'}"
            )
        if record_restore:
            self._push_restore(
                {
                    "action": "allowlisted_branch",
                    "branch_rule_id": rule.rule_id,
                    "restore_vkey": rule.restore_vkey,
                }
            )
        if rule.action == "press":
            target.Press()
        elif rule.action == "select_menu":
            target.Select()
        elif rule.action == "press_toolbar_button":
            target.PressToolbarButton(rule.item_id)
        elif rule.action == "select_context_menu_item":
            target.SelectContextMenuItem(rule.item_id)
        else:  # pragma: no cover - schema closes this set
            raise ValueError(f"unsupported exact branch action: {rule.action}")
        return {"action": rule.action, "branch_rule_id": rule.rule_id}

    def _push_restore(self, token: dict[str, Any]) -> None:
        # Fail before touching SAP if an inverse cannot be checkpointed as JSON.
        json.dumps(token, ensure_ascii=False, sort_keys=True)
        self._restore_tokens.append(token)

    def _find(self, sap_id: str) -> Any:
        for name in ("FindById", "findById"):
            method = getattr(self._session, name, None)
            if callable(method):
                return method(sap_id)
        raise AttributeError("session does not expose FindById/findById")

    @staticmethod
    def _candidate_sap_id(candidate: TraversalCandidate) -> str:
        sap_id = str(candidate.target_locator.get("sap_id", ""))
        if not sap_id:
            raise ValueError("typed traversal candidate requires sap_id")
        return sap_id

    @staticmethod
    def _scrollbar(control: Any, axis: str) -> Any:
        prefix = "Horizontal" if axis == "horizontal" else "Vertical"
        for name in (f"{prefix}Scrollbar", f"{prefix.lower()}Scrollbar"):
            try:
                scrollbar = getattr(control, name)
            except Exception:
                continue
            if scrollbar is not None:
                return scrollbar
        raise AttributeError(f"control has no {axis} scrollbar")

    @staticmethod
    def _require_type(obj: Any, allowed: set[str], context: str) -> None:
        if str(getattr(obj, "Type", "")) not in allowed:
            raise TypeError(f"{context} has an unsafe component type")

    @staticmethod
    def _require_tree(tree: Any) -> None:
        component_type = str(getattr(tree, "Type", ""))
        subtype = str(getattr(tree, "SubType", "")).lower()
        if component_type != "GuiTree" and not (
            component_type == "GuiShell" and "tree" in subtype
        ):
            raise TypeError("tree action target has an unsafe component type")

    @staticmethod
    def _is_expanded(tree: Any, node_key: str) -> bool:
        return bool(tree.IsFolderExpanded(node_key))
