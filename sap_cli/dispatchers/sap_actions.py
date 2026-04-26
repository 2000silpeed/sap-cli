"""SAP GUI action command dispatch helpers.

Small SAP interaction commands live here while :mod:`sap_cli.dispatcher`
keeps facade routing and compatibility wrappers.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

Factory = Callable[..., Any]


SAP_ACTION_COMMANDS: frozenset[str] = frozenset(
    {
        "record_step",
        "table_select",
        "table_doubleclick",
        "tree",
        "tree_select",
        "tree_doubleclick",
        "tree_expand",
        "tree_collapse",
        "tree_press",
    }
)


def is_sap_action_command(command: str) -> bool:
    """Return whether ``command`` belongs to the SAP action dispatcher group."""
    return command in SAP_ACTION_COMMANDS


def dispatch_sap_action_command(
    command: str,
    args: dict[str, Any],
    ctx: Any,
    *,
    get_sap: Factory,
    get_recorder: Factory,
    map_action: Factory,
    execute_sap_action: Factory,
    with_post_action: Factory,
) -> dict[str, Any]:
    """Dispatch an SAP action command with facade-provided compatibility hooks."""
    if command == "record_step":
        return dispatch_record_step(
            args,
            ctx,
            get_sap,
            get_recorder,
            map_action,
            execute_sap_action,
            with_post_action,
        )
    if command == "table_select":
        return dispatch_table_select(args, ctx, get_sap, with_post_action)
    if command == "table_doubleclick":
        return dispatch_table_doubleclick(args, ctx, get_sap, with_post_action)
    if command == "tree":
        return dispatch_tree_read(args, ctx, get_sap)
    if command == "tree_select":
        return dispatch_tree_select(args, ctx, get_sap, with_post_action)
    if command == "tree_doubleclick":
        return dispatch_tree_doubleclick(args, ctx, get_sap, with_post_action)
    if command == "tree_expand":
        return dispatch_tree_expand(args, ctx, get_sap)
    if command == "tree_collapse":
        return dispatch_tree_collapse(args, ctx, get_sap)
    if command == "tree_press":
        return dispatch_tree_press(args, ctx, get_sap, with_post_action)
    raise ValueError(f"미구현 SAP action 명령: {command}")


def dispatch_record_step(
    args: dict[str, Any],
    ctx: Any,
    get_sap: Factory,
    get_recorder: Factory,
    map_action: Factory,
    execute_sap_action: Factory,
    with_post_action: Factory,
) -> dict[str, Any]:
    """Execute and record one macro step."""
    sap = get_sap(ctx.slot_id)
    recorder = get_recorder()
    action = str(args["action"])
    target = str(args["target"])
    value = args.get("value")
    description = str(args.get("description", ""))

    action_type = map_action(action)
    execute_sap_action(sap, action.lower(), target, None if value is None else str(value))

    info = sap.get_session_info()
    record_value = None if value is None else str(value)
    if action.lower() in ("run", "vkey") and not record_value:
        record_value = target

    step = recorder.record_step(
        action=action_type,
        target_id=target,
        value=record_value,
        description=description,
        screen_number=info.screen_number,
    )

    payload = {
        "recorded": True,
        "step_number": step.step_number,
        "action": action,
        "target": target,
        "value": value,
        "screen_number": info.screen_number,
    }
    return with_post_action(payload, sap, "record_step", ctx)


def dispatch_table_select(
    args: dict[str, Any],
    ctx: Any,
    get_sap: Factory,
    with_post_action: Factory,
) -> dict[str, Any]:
    """Select a row in a SAP GUI table."""
    sap = get_sap(ctx.slot_id)
    element_id = str(args["element_id"])
    row = int(args["row"])
    sap.select_table_row(element_id, row)
    payload = {"selected": True, "table": element_id, "row": row}
    return with_post_action(payload, sap, "table_select", ctx)


def dispatch_table_doubleclick(
    args: dict[str, Any],
    ctx: Any,
    get_sap: Factory,
    with_post_action: Factory,
) -> dict[str, Any]:
    """Double-click a SAP GUI table cell."""
    sap = get_sap(ctx.slot_id)
    element_id = str(args["element_id"])
    row = int(args["row"])
    col = str(args.get("column", ""))
    sap.double_click_table_cell(element_id, row, col)
    payload = {"double_clicked": True, "table": element_id, "row": row, "column": col}
    return with_post_action(payload, sap, "table_doubleclick", ctx)


def dispatch_tree_read(args: dict[str, Any], ctx: Any, get_sap: Factory) -> dict[str, Any]:
    """Read a SAP GUI tree, optionally narrowing by node and depth."""
    element_id = str(args["element_id"])
    node_key = str(args.get("node_key", "")).strip()
    depth = int(args.get("depth", -1))
    sap = get_sap(ctx.slot_id)
    nodes = sap.read_tree(element_id)

    if node_key:
        target = find_tree_node(nodes, node_key)
        if target is None:
            raise ValueError(f"노드를 찾을 수 없습니다: {node_key}")
        nodes = target.children

    if depth >= 0:
        nodes = [truncate_tree_depth(n, depth) for n in nodes]

    columns = getattr(sap, "_tree_columns", [])
    return {
        "element_id": element_id,
        **({"columns": columns} if columns else {}),
        **({"parent_node_key": node_key} if node_key else {}),
        **({"depth": depth} if depth >= 0 else {}),
        "node_count": sum(1 for _ in flatten_tree_nodes(nodes)),
        "nodes": [n.model_dump(mode="json", exclude_none=True) for n in nodes],
    }


def flatten_tree_nodes(nodes: list[Any]) -> list[Any]:
    """Flatten tree nodes in pre-order."""
    result: list[Any] = []
    for node in nodes:
        result.append(node)
        if node.children:
            result.extend(flatten_tree_nodes(node.children))
    return result


def find_tree_node(nodes: list[Any], key: str) -> Any | None:
    """Find a tree node recursively by key."""
    for node in nodes:
        if node.key == key:
            return node
        if node.children:
            found = find_tree_node(node.children, key)
            if found:
                return found
    return None


def truncate_tree_depth(node: Any, remaining_depth: int) -> Any:
    """Limit tree depth and preserve child_count for truncated children."""
    from sap_cli.ontology.schema import SapTreeNode

    if remaining_depth <= 0:
        return SapTreeNode(
            key=node.key,
            text=node.text,
            level=node.level,
            items=node.items,
            children=[],
            child_count=len(node.children) if node.children else None,
        )

    truncated_children = [truncate_tree_depth(child, remaining_depth - 1) for child in node.children]
    return SapTreeNode(
        key=node.key,
        text=node.text,
        level=node.level,
        items=node.items,
        children=truncated_children,
        child_count=None,
    )


def dispatch_tree_select(
    args: dict[str, Any],
    ctx: Any,
    get_sap: Factory,
    with_post_action: Factory,
) -> dict[str, Any]:
    """Select a SAP GUI tree node."""
    sap = get_sap(ctx.slot_id)
    sap.tree_select_node(str(args["element_id"]), str(args["node_key"]))
    payload = {"action": "select", "element_id": args["element_id"], "node_key": args["node_key"]}
    return with_post_action(payload, sap, "tree_select", ctx)


def dispatch_tree_doubleclick(
    args: dict[str, Any],
    ctx: Any,
    get_sap: Factory,
    with_post_action: Factory,
) -> dict[str, Any]:
    """Double-click a SAP GUI tree node."""
    sap = get_sap(ctx.slot_id)
    sap.tree_double_click_node(str(args["element_id"]), str(args["node_key"]))
    payload = {
        "action": "doubleclick",
        "element_id": args["element_id"],
        "node_key": args["node_key"],
    }
    return with_post_action(payload, sap, "tree_doubleclick", ctx)


def dispatch_tree_expand(args: dict[str, Any], ctx: Any, get_sap: Factory) -> dict[str, Any]:
    """Expand a SAP GUI tree node."""
    sap = get_sap(ctx.slot_id)
    sap.tree_expand_node(str(args["element_id"]), str(args["node_key"]))
    return {"action": "expand", "element_id": args["element_id"], "node_key": args["node_key"]}


def dispatch_tree_collapse(args: dict[str, Any], ctx: Any, get_sap: Factory) -> dict[str, Any]:
    """Collapse a SAP GUI tree node."""
    sap = get_sap(ctx.slot_id)
    sap.tree_collapse_node(str(args["element_id"]), str(args["node_key"]))
    return {"action": "collapse", "element_id": args["element_id"], "node_key": args["node_key"]}


def dispatch_tree_press(
    args: dict[str, Any],
    ctx: Any,
    get_sap: Factory,
    with_post_action: Factory,
) -> dict[str, Any]:
    """Press a button/action column on a SAP GUI tree node."""
    sap = get_sap(ctx.slot_id)
    used_col = sap.tree_press_button(
        str(args["element_id"]),
        str(args["node_key"]),
        str(args.get("column", "")),
    )
    cached = sap._tree_action_cache.get(str(args["element_id"]), {})
    payload: dict[str, Any] = {
        "action": "press",
        "element_id": args["element_id"],
        "node_key": args["node_key"],
        "used_column": used_col,
    }
    if cached:
        payload["discovered_columns"] = cached
    return with_post_action(payload, sap, "tree_press", ctx)
