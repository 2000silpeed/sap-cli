"""Safe, readable projections of a persisted SAP UI-map graph."""

from __future__ import annotations

from typing import Any

from .schema import UiMap


def map_view(ui_map: UiMap) -> dict[str, Any]:
    """Return a value-free graph view for people and MCP clients.

    This is intentionally a projection, not the persisted map payload.  It
    exposes screen identity, structural counts and confirmed routes, while
    excluding component values, observed parameters and transition evidence.
    """

    ordered_ids = sorted(ui_map.states)
    node_ids = {state_id: f"S{index + 1}" for index, state_id in enumerate(ordered_ids)}
    confirmed_semantics: dict[str, list[str]] = {}
    for link in ui_map.semantic_links:
        if link.status == "confirmed":
            confirmed_semantics.setdefault(link.subject_id, []).append(link.object_id)

    states = []
    for state_id in ordered_ids:
        state = ui_map.states[state_id]
        states.append(
            {
                "node_id": node_ids[state_id],
                "state_id": state_id,
                "screen": _screen_label(state),
                "active_tabs": sorted(state.active_tabs.values()),
                "component_count": len(state.components),
                "collections": sorted({item.kind for item in state.collections}),
                "modal": bool(state.modal_signature),
                "semantic_roles": sorted(confirmed_semantics.get(state_id, [])),
            }
        )

    edges = []
    for transition in sorted(ui_map.transitions, key=lambda item: item.transition_id):
        edges.append(
            {
                "transition_id": transition.transition_id,
                "from_node_id": node_ids.get(transition.from_state_id, ""),
                "to_node_id": node_ids.get(transition.to_state_id, ""),
                "from_state_id": transition.from_state_id,
                "to_state_id": transition.to_state_id,
                "action": transition.action,
                "target_logical_id": transition.target_logical_id,
                "reversible": transition.reversible,
                "confidence": transition.confidence,
                "status": "connected" if transition.to_state_id in node_ids else "unresolved",
            }
        )

    outgoing: dict[str, list[dict[str, Any]]] = {state_id: [] for state_id in ordered_ids}
    for edge in edges:
        if edge["from_state_id"] in outgoing:
            outgoing[edge["from_state_id"]].append(edge)
    outline = [
        {
            "node_id": node_ids[state_id],
            "state_id": state_id,
            "screen": _screen_label(ui_map.states[state_id]),
            "next": [
                {
                    "action": edge["action"],
                    "target_logical_id": edge["target_logical_id"],
                    "to_node_id": edge["to_node_id"],
                    "status": edge["status"],
                }
                for edge in outgoing[state_id]
            ],
        }
        for state_id in ordered_ids
    ]
    return {
        "map": {
            "map_id": ui_map.manifest.map_id,
            "map_version": ui_map.manifest.map_version,
            "tcode": ui_map.manifest.tcode,
            "system_id": ui_map.manifest.system_id,
            "client": ui_map.manifest.client,
            "language": ui_map.manifest.language,
        },
        "summary": {
            "states": len(states),
            "connections": len(edges),
            "unresolved_connections": sum(item["status"] == "unresolved" for item in edges),
            "pending_interactions": sum(
                item.status == "pending" for item in ui_map.pending_interactions
            ),
        },
        "states": states,
        "connections": edges,
        "outline": outline,
        "mermaid": _mermaid(states, edges),
    }


def _screen_label(state: Any) -> str:
    """Use stable technical screen identity, not potentially sensitive values."""

    return " · ".join(
        item
        for item in (state.tcode, state.program, str(state.screen_number or ""))
        if item
    ) or "SAP screen"


def _mermaid(states: list[dict[str, Any]], edges: list[dict[str, Any]]) -> str:
    lines = ["flowchart LR"]
    for state in states:
        label = _mermaid_text(f"{state['node_id']} · {state['screen']}")
        lines.append(f"    {state['node_id']}[{label}]")
    for edge in edges:
        if not edge["from_node_id"] or not edge["to_node_id"]:
            continue
        action = _mermaid_text(
            " · ".join(
                item for item in (edge["action"], edge["target_logical_id"]) if item
            )
        )
        lines.append(
            f"    {edge['from_node_id']} -->|{action or 'navigate'}| {edge['to_node_id']}"
        )
    return "\n".join(lines)


def _mermaid_text(value: str) -> str:
    return str(value).replace('"', "'").replace("|", "/").replace("\n", " ")


__all__ = ["map_view"]
