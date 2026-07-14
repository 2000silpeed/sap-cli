"""Dispatcher surface for daemon-owned SAP GUI event subscriptions."""

from __future__ import annotations

from typing import Any, Callable

from sap_cli.events.runtime import get_event_manager


EVENT_COMMANDS = frozenset(
    {"event_subscribe", "event_poll", "event_unsubscribe", "event_list"}
)


def is_event_command(command: str) -> bool:
    return command in EVENT_COMMANDS


def dispatch_event_command(
    command: str,
    args: dict[str, Any],
    ctx: Any,
    *,
    get_sap: Callable[[str], Any],
) -> dict[str, Any]:
    manager = get_event_manager()
    owner = str(ctx.agent_name or "")

    if command == "event_subscribe":
        scope = str(args.get("scope", "session"))
        wrapper = get_sap(ctx.slot_id)
        if scope.lower() == "application":
            target = wrapper._application or wrapper._get_application()
        elif scope.lower() == "session":
            target = wrapper.session
        else:
            raise ValueError("scope must be application or session")
        event_names = args.get("events") or args.get("event_names") or []
        if isinstance(event_names, str):
            event_names = [item.strip() for item in event_names.split(",") if item.strip()]
        return manager.subscribe(
            slot_id=ctx.slot_id,
            scope=scope,
            target=target,
            event_names=event_names,
            owner=owner,
            ttl_seconds=float(args.get("ttl_seconds", 300)),
            queue_limit=int(args.get("queue_limit", 256)),
            redact_sensitive=bool(args.get("redact_sensitive", True)),
        )

    if command == "event_list":
        return {
            "subscriptions": manager.list_subscriptions(
                slot_id=ctx.slot_id, owner=owner
            )
        }

    subscription_id = str(args.get("subscription_id", ""))
    if not subscription_id:
        raise ValueError("subscription_id is required")
    _require_owned_subscription(manager, subscription_id, owner)
    if command == "event_poll":
        return manager.poll(
            subscription_id,
            cursor=int(args.get("cursor", 0)),
            limit=int(args.get("limit", 100)),
        )
    if command == "event_unsubscribe":
        return {
            "subscription_id": subscription_id,
            "unsubscribed": manager.unsubscribe(subscription_id),
        }
    raise ValueError(f"unsupported event command: {command}")


def _require_owned_subscription(manager: Any, subscription_id: str, owner: str) -> None:
    matches = [
        item
        for item in manager.list_subscriptions(owner=owner)
        if item["subscription_id"] == subscription_id
    ]
    if not matches:
        raise ValueError("unknown event subscription or owner mismatch")
