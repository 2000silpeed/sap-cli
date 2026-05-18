"""Connection, session, screen, and modal command dispatch helpers.

The top-level :mod:`sap_cli.dispatcher` module keeps thin private wrappers for
backward-compatible imports and monkeypatch-based tests while connection and
screen command implementations live here.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

Factory = Callable[..., Any]


CONNECTION_COMMANDS: frozenset[str] = frozenset(
    {
        "connect",
        "connections",
        "modal",
        "screen",
        "session",
    }
)


def is_connection_command(command: str) -> bool:
    """Return whether ``command`` belongs to the connection/screen dispatcher group."""
    return command in CONNECTION_COMMANDS


def dispatch_connection_command(
    command: str,
    args: dict[str, Any],
    ctx: Any,
    *,
    get_pool: Factory,
    get_sap: Factory,
    sap_wrapper_factory: Factory,
    with_post_action: Factory,
) -> dict[str, Any]:
    """Dispatch a connection/screen command with facade-provided hooks."""
    if command == "connect":
        return dispatch_connect(args, ctx, get_pool, with_post_action)
    if command == "connections":
        return dispatch_connections(sap_wrapper_factory)
    if command == "session":
        return dispatch_session_info(ctx, get_sap)
    if command == "screen":
        return dispatch_screen(args, ctx, get_sap)
    if command == "modal":
        return dispatch_modal(ctx, get_sap)
    raise ValueError(f"미구현 connection 명령: {command}")


def dispatch_connect(
    args: dict[str, Any],
    ctx: Any,
    get_pool: Factory,
    with_post_action: Factory,
) -> dict[str, Any]:
    """Connect a session pool slot to a SAP GUI session."""
    pool = get_pool()
    sap = pool.get_or_create(ctx.slot_id, agent_name=ctx.agent_name)
    conn_idx = int(args.get("connection", args.get("connection_index", 0)))
    sess_idx = int(args.get("session", args.get("session_index", 0)))
    system = args.get("system")
    info = sap.connect(
        connection_index=conn_idx,
        session_index=sess_idx,
        system=system,
    )
    pool.update_slot_connection(ctx.slot_id, conn_idx, sess_idx, system_id=system)
    payload = {
        "connected": True,
        "slot_id": ctx.slot_id,
        "session": info.model_dump(mode="json"),
    }
    return with_post_action(payload, sap, "connect", ctx)


def dispatch_connections(sap_wrapper_factory: Factory) -> dict[str, Any]:
    """List available SAP GUI connections."""
    sap = sap_wrapper_factory()
    return sap.list_connections()


def dispatch_session_info(ctx: Any, get_sap: Factory) -> dict[str, Any]:
    """Read current SAP GUI session metadata."""
    return get_sap(ctx.slot_id).get_session_info().model_dump(mode="json")


def dispatch_screen(
    args: dict[str, Any],
    ctx: Any | None,
    get_sap: Factory,
) -> dict[str, Any]:
    """Read the current screen, optionally filtering fields/actions/search results."""
    slot_id = ctx.slot_id if ctx else "ses0"
    sap = get_sap(slot_id)
    screen = sap.get_screen()
    search = args.get("search")
    if search:
        return search_screen(screen, search)
    if args.get("fields_only"):
        result: dict[str, Any] = {
            "transaction": screen.transaction,
            "screen_number": screen.screen_number,
            "fields": [field.model_dump(mode="json") for field in screen.fields],
        }
        if screen.tables:
            result["tables"] = [table.model_dump(mode="json") for table in screen.tables]
        return result
    if args.get("actions_only"):
        actions = screen.get_available_actions()
        return {
            "transaction": screen.transaction,
            "screen_number": screen.screen_number,
            "available_actions": [action.model_dump(mode="json") for action in actions],
        }
    data = screen.model_dump(mode="json")
    data["hint"] = "온톨로지 조회 가능: sap_field_dependencies(필드 종속성), sap_document_flow(문서 흐름), sap_ontology_relationships(필드 위치)"
    return data


def search_screen(screen: Any, pattern: str) -> dict[str, Any]:
    """Search screen elements by ID, name, label, text, and semantic metadata."""
    p = pattern.lower()

    def _match(text: str) -> bool:
        return p in text.lower()

    matched_fields = []
    for field in screen.fields:
        field_values = [
            field.id,
            field.name,
            field.label,
            getattr(field, "value", ""),
            getattr(field.semantic, "sap_field_name", ""),
            getattr(field.semantic, "description", ""),
        ] + getattr(field.semantic, "synonyms", [])
        if any(_match(value) for value in field_values):
            matched_fields.append(field.model_dump(mode="json"))

    matched_buttons = []
    for button in screen.buttons:
        if any(_match(value) for value in [button.id, button.name, button.text, button.tooltip]):
            matched_buttons.append(button.model_dump(mode="json"))

    matched_tabs = []
    for tab in screen.tabs:
        if any(_match(value) for value in [tab.id, tab.name, tab.text]):
            matched_tabs.append(tab.model_dump(mode="json"))

    matched_tables = []
    for table in screen.tables:
        matching_columns = []
        for column in getattr(table, "columns", []) or []:
            column_values = [
                str(getattr(column, "name", "")),
                str(getattr(column, "title", "")),
            ]
            if any(_match(value) for value in column_values):
                if hasattr(column, "model_dump"):
                    matching_columns.append(column.model_dump(mode="json"))
                else:
                    matching_columns.append({
                        "name": str(getattr(column, "name", "")),
                        "title": str(getattr(column, "title", "")),
                        "position": getattr(column, "position", 0),
                    })
        if any(_match(value) for value in [table.id, table.name]) or matching_columns:
            table_payload = table.model_dump(mode="json")
            if matching_columns:
                table_payload["matching_columns"] = matching_columns
            matched_tables.append(table_payload)

    total = len(matched_fields) + len(matched_buttons) + len(matched_tabs) + len(matched_tables)
    result: dict[str, Any] = {
        "transaction": screen.transaction,
        "screen_number": screen.screen_number,
        "search": pattern,
        "total_matches": total,
    }
    if matched_fields:
        result["fields"] = matched_fields
    if matched_buttons:
        result["buttons"] = matched_buttons
    if matched_tabs:
        result["tabs"] = matched_tabs
    if matched_tables:
        result["tables"] = matched_tables
    return result


def dispatch_modal(ctx: Any, get_sap: Factory) -> dict[str, Any]:
    """Read current modal state."""
    return collect_modal(get_sap(ctx.slot_id))


def collect_modal(sap: Any) -> dict[str, Any]:
    """Collect modal presence and text from a SAP GUI wrapper."""
    wait_until_idle = getattr(sap, "wait_until_idle", None)
    if callable(wait_until_idle):
        wait_until_idle()
    has_modal = sap.has_modal()
    payload: dict[str, Any] = {"has_modal": has_modal}
    if has_modal:
        payload["text"] = sap.get_modal_text()
    return payload
