"""Feature-specific dispatcher modules.

The top-level :mod:`sap_cli.dispatcher` module remains the public facade while
large command groups are gradually moved under this package.
"""

from .registry import (
    COMMAND_REGISTRY,
    COMMAND_TOOL_MAP,
    MCP_TOOL_TO_COMMAND,
    CommandDef,
)
from .session import (
    SESSION_COMMANDS,
    SESSION_SKIP_COMMANDS,
    dispatch_session_command,
    is_session_command,
)

__all__ = [
    "COMMAND_REGISTRY",
    "COMMAND_TOOL_MAP",
    "MCP_TOOL_TO_COMMAND",
    "CommandDef",
    "SESSION_COMMANDS",
    "SESSION_SKIP_COMMANDS",
    "dispatch_session_command",
    "is_session_command",
]
