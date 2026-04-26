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
from .process import (
    PROCESS_COMMANDS,
    dispatch_process_command,
    is_process_command,
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
    "PROCESS_COMMANDS",
    "dispatch_process_command",
    "is_process_command",
    "SESSION_COMMANDS",
    "SESSION_SKIP_COMMANDS",
    "dispatch_session_command",
    "is_session_command",
]
