"""SAP 커맨드 필드 파서."""

from __future__ import annotations

import re

_SAP_COMMAND_TCODE_RE = re.compile(
    r"^(?:/[NO])?=?([A-Z][A-Z0-9]{1,19})$",
    re.IGNORECASE,
)


def extract_tcode_from_command(command_text: str) -> str | None:
    normalized = command_text.strip().upper()
    if not normalized:
        return None
    match = _SAP_COMMAND_TCODE_RE.match(normalized)
    if not match:
        return None
    return match.group(1)
