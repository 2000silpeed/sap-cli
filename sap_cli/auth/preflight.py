"""Side-effect-free authorization and risk preflight.

This module deliberately does not write audit records or dispatch commands.  It
is shared by plan compilation/preview and the runtime dispatcher so both paths
evaluate the same resolved tool call.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .checker import PermissionChecker
from .policy import Policy, TOOL_PERMISSION_MAP


@dataclass(frozen=True, slots=True)
class AuthorizationPreflight:
    tool_name: str
    agent: str
    permission: str
    resource: str
    allowed: bool
    reason: str = ""
    approval_required: bool = False
    approval_reason: str = ""
    risk_metadata: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def preflight_tool_call(
    tool_name: str,
    arguments: dict[str, Any] | None = None,
    *,
    policy: Policy | None = None,
    agent_name: str | None = None,
    enforce_auth: bool = True,
) -> AuthorizationPreflight:
    """Evaluate one fully resolved tool call without executing or logging it."""

    args = dict(arguments or {})
    checker = PermissionChecker(policy)
    if agent_name:
        checker.set_agent(agent_name)
    check = checker.check_tool(tool_name, args)
    permission = TOOL_PERMISSION_MAP.get(tool_name)
    resource = checker.resolve_resource(tool_name, args)
    risk_metadata = checker.risk_metadata(tool_name, args)
    effective_agent = checker.agent_name or agent_name or "anonymous"

    if not enforce_auth:
        return AuthorizationPreflight(
            tool_name=tool_name,
            agent=effective_agent,
            permission=permission.value if permission else "",
            resource=resource,
            allowed=True,
            reason="auth_disabled",
            risk_metadata=risk_metadata,
        )

    if not check:
        return AuthorizationPreflight(
            tool_name=tool_name,
            agent=effective_agent,
            permission=permission.value if permission else "",
            resource=resource,
            allowed=False,
            reason=check.reason,
            risk_metadata=check.risk_metadata or risk_metadata,
        )

    approval_reason = checker.requires_approval(tool_name, args)
    return AuthorizationPreflight(
        tool_name=tool_name,
        agent=effective_agent,
        permission=permission.value if permission else "",
        resource=resource,
        allowed=True,
        reason=check.reason,
        approval_required=bool(approval_reason),
        approval_reason=approval_reason or "",
        risk_metadata=risk_metadata,
    )


__all__ = ["AuthorizationPreflight", "preflight_tool_call"]
