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
from .macro import (
    MACRO_COMMANDS,
    dispatch_macro_command,
    is_macro_command,
)
from .ontology import (
    ONTOLOGY_COMMANDS,
    dispatch_ontology_command,
    is_ontology_command,
)
from .session import (
    SESSION_COMMANDS,
    SESSION_SKIP_COMMANDS,
    dispatch_session_command,
    is_session_command,
)
from .slot import (
    SLOT_COMMANDS,
    dispatch_slot_command,
    is_slot_command,
)
from .transcript import (
    TRANSCRIPT_COMMANDS,
    dispatch_transcript_command,
    is_transcript_command,
)
from .capture import (
    CAPTURE_COMMANDS,
    dispatch_capture_command,
    is_capture_command,
)
from .form import (
    FORM_COMMANDS,
    dispatch_form_command,
    is_form_command,
)
from .sap_actions import (
    SAP_ACTION_COMMANDS,
    dispatch_sap_action_command,
    is_sap_action_command,
)

__all__ = [
    "COMMAND_REGISTRY",
    "COMMAND_TOOL_MAP",
    "MCP_TOOL_TO_COMMAND",
    "CommandDef",
    "PROCESS_COMMANDS",
    "dispatch_process_command",
    "is_process_command",
    "MACRO_COMMANDS",
    "dispatch_macro_command",
    "is_macro_command",
    "ONTOLOGY_COMMANDS",
    "dispatch_ontology_command",
    "is_ontology_command",
    "SESSION_COMMANDS",
    "SESSION_SKIP_COMMANDS",
    "dispatch_session_command",
    "is_session_command",
    "SLOT_COMMANDS",
    "dispatch_slot_command",
    "is_slot_command",
    "TRANSCRIPT_COMMANDS",
    "dispatch_transcript_command",
    "is_transcript_command",
    "CAPTURE_COMMANDS",
    "dispatch_capture_command",
    "is_capture_command",
    "FORM_COMMANDS",
    "dispatch_form_command",
    "is_form_command",
    "SAP_ACTION_COMMANDS",
    "dispatch_sap_action_command",
    "is_sap_action_command",
]
