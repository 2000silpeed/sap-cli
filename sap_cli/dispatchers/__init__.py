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

__all__ = [
    "COMMAND_REGISTRY",
    "COMMAND_TOOL_MAP",
    "MCP_TOOL_TO_COMMAND",
    "CommandDef",
]
