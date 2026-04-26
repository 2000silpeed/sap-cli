"""Session slot pool command dispatch helpers.

Slot command implementations live here while :mod:`sap_cli.dispatcher` keeps
thin private wrappers for backward-compatible imports and monkeypatch-based
tests.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

Factory = Callable[..., Any]


SLOT_COMMANDS: frozenset[str] = frozenset(
    {
        "slots",
        "slot_allocate",
        "slot_release",
    }
)


def is_slot_command(command: str) -> bool:
    """Return whether ``command`` belongs to the slot dispatcher group."""
    return command in SLOT_COMMANDS


def dispatch_slot_command(
    command: str,
    args: dict[str, Any],
    ctx: Any,
    *,
    get_pool: Factory,
    get_sap: Factory,
) -> dict[str, Any]:
    """Dispatch a slot command with facade-provided compatibility hooks."""
    if command == "slots":
        return dispatch_slots(get_pool)
    if command == "slot_allocate":
        return dispatch_slot_allocate(args, ctx, get_pool, get_sap)
    if command == "slot_release":
        return dispatch_slot_release(args, get_pool)
    raise ValueError(f"미구현 slot 명령: {command}")


def dispatch_slots(get_pool: Factory) -> dict[str, Any]:
    """List session pool slots."""
    pool = get_pool()
    return {
        "slot_count": pool.slot_count(),
        "max_sessions": pool.max_sessions,
        "slots": pool.list_slots(),
    }


def dispatch_slot_allocate(
    args: dict[str, Any],
    ctx: Any,
    get_pool: Factory,
    get_sap: Factory,
) -> dict[str, Any]:
    """Allocate a session slot and optionally connect it."""
    slot_id = str(args.get("slot_id", ""))
    if not slot_id:
        raise ValueError("slot_id가 필요합니다.")
    agent_name = args.get("agent_name") or ctx.agent_name
    system = args.get("system")
    session_index = int(args.get("session_index", -1))
    create_new = bool(args.get("create_new_session", False))

    pool = get_pool()
    wrapper = pool.get_or_create(slot_id, agent_name=agent_name)

    result: dict[str, Any] = {
        "allocated": True,
        "slot_id": slot_id,
        "agent_name": agent_name,
    }

    # create_new_session: 기존 연결에서 새 SAP 세션을 생성하여 할당
    if create_new:
        base_wrapper = get_sap("ses0")
        new_idx = base_wrapper.create_session()
        session_index = new_idx
        result["created_session_index"] = new_idx

    # 시스템이나 세션 인덱스가 지정되면 바로 연결
    if system or session_index >= 0:
        info = wrapper.connect(
            session_index=max(session_index, 0),
            system=system,
        )
        pool.update_slot_connection(
            slot_id,
            wrapper._last_connection_index,
            wrapper._last_session_index,
            system_id=system,
        )
        result["connected"] = True
        result["session"] = info.model_dump(mode="json")

    return result


def dispatch_slot_release(args: dict[str, Any], get_pool: Factory) -> dict[str, Any]:
    """Release or close a session slot."""
    slot_id = str(args.get("slot_id", ""))
    if not slot_id:
        raise ValueError("slot_id가 필요합니다.")
    close = args.get("close", False)

    pool = get_pool()
    if close:
        ok = pool.close(slot_id)
    else:
        ok = pool.release(slot_id)
    return {"released": ok, "slot_id": slot_id, "closed": close}
