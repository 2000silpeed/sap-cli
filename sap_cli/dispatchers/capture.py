"""Screenshot capture command dispatch helpers.

Capture command implementations live here while :mod:`sap_cli.dispatcher`
keeps thin private wrappers for backward-compatible imports and monkeypatch-
based tests.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

Factory = Callable[..., Any]


CAPTURE_COMMANDS: frozenset[str] = frozenset(
    {
        "capture",
        "capture_list",
    }
)


def is_capture_command(command: str) -> bool:
    """Return whether ``command`` belongs to the capture dispatcher group."""
    return command in CAPTURE_COMMANDS


def dispatch_capture_command(
    command: str,
    args: dict[str, Any],
    ctx: Any,
    *,
    get_sap: Factory,
) -> dict[str, Any]:
    """Dispatch a capture command with facade-provided compatibility hooks."""
    if command == "capture":
        return dispatch_capture(args, ctx, get_sap)
    if command == "capture_list":
        return dispatch_capture_list(args)
    raise ValueError(f"미구현 capture 명령: {command}")


def dispatch_capture(args: dict[str, Any], ctx: Any | None, get_sap: Factory) -> dict[str, Any]:
    """Capture the current SAP GUI window screenshot."""
    from sap_cli.screenshot import capture_for_step, capture_sap_window

    slot_id = ctx.slot_id if ctx else "ses0"
    sap = get_sap(slot_id)
    info = sap.get_session_info()
    label = args.get("label") or ""
    output = args.get("output")

    session = sap._session  # COM 세션 → 정확한 윈도우 핸들 획득용

    if output:
        path = capture_sap_window(output, session=session)
    else:
        path = capture_for_step(
            tcode=info.transaction,
            step_number=0,
            screen_number=info.screen_number,
            label=label or "manual",
            session=session,
        )

    if path is None:
        raise RuntimeError("스크린샷 캡처에 실패했습니다.")

    return {
        "captured": True,
        "path": str(path),
        "transaction": info.transaction,
        "screen_number": info.screen_number,
        "label": label,
    }


def dispatch_capture_list(args: dict[str, Any]) -> dict[str, Any]:
    """List saved screenshots."""
    from sap_cli.screenshot import list_screenshots

    tcode = args.get("tcode")
    shots = list_screenshots(tcode)
    return {"screenshots": shots, "count": len(shots)}
