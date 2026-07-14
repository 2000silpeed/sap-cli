"""Executable contracts layered on top of the official scripting graph.

The official PL08 pages expose four COM ``NewEnum`` properties as ``Unknown``
and two ``GuiEnum`` methods with ByRef/out parameters.  Keep these narrow,
auditable exceptions separate from the generated source graph so regenerating
the graph remains byte-for-byte reproducible.
"""

from __future__ import annotations

from typing import Any


NEW_ENUM_OWNERS = frozenset(
    {"GuiCollection", "GuiComponentCollection", "GuiTableColumn", "GuiTableRow"}
)
SUPPORTED_BYREF_TYPES = frozenset({"GuiEnum", "Variant", "ULong"})
TESTED_BYREF_MEMBERS = frozenset({("GuiEnum", "Clone"), ("GuiEnum", "Next")})


def effective_result_type(
    object_name: str,
    member_kind: str,
    member_name: str,
    member_node: dict[str, Any],
) -> str:
    """Return the runtime result type supported by the adapter contract."""

    declared = str(
        member_node.get("return_type")
        if member_kind == "method"
        else member_node.get("property_type")
        or ""
    ).strip()
    if (
        member_kind == "property"
        and member_name == "NewEnum"
        and object_name in NEW_ENUM_OWNERS
        and declared.lower() == "unknown"
    ):
        # These are the standard COM enumeration entry points for the four
        # collection interfaces documented by SAP.  Their returned interface
        # is GuiEnum even though the Help page renders the type as Unknown.
        return "GuiEnum"
    return declared


def unsupported_adapter_reasons(
    object_name: str,
    member_kind: str,
    member_name: str,
    member_node: dict[str, Any],
) -> list[str]:
    """Report signature features not covered by the executable adapter."""

    reasons: list[str] = []
    for parameter in member_node.get("parameters", []):
        if str(parameter.get("passing", "")).lower() != "byref":
            continue
        parameter_type = str(parameter.get("type", ""))
        if parameter_type not in SUPPORTED_BYREF_TYPES:
            reasons.append(
                f"ByRef/out type is not implemented: {parameter_type or 'missing'}"
            )
    result_type = effective_result_type(
        object_name, member_kind, member_name, member_node
    )
    declared = str(
        member_node.get("return_type")
        if member_kind == "method"
        else member_node.get("property_type")
        or ""
    ).strip()
    if declared.lower() == "unknown" and result_type.lower() == "unknown":
        reasons.append("documented return/property type is Unknown")
    return reasons


def has_tested_runtime_contract(
    object_name: str,
    member_kind: str,
    member_name: str,
    member_node: dict[str, Any],
) -> bool:
    if (
        member_kind == "property"
        and member_name == "NewEnum"
        and object_name in NEW_ENUM_OWNERS
    ):
        return True
    return (
        member_kind == "method"
        and (object_name, member_name) in TESTED_BYREF_MEMBERS
        and not unsupported_adapter_reasons(
            object_name, member_kind, member_name, member_node
        )
    )


__all__ = [
    "NEW_ENUM_OWNERS",
    "SUPPORTED_BYREF_TYPES",
    "effective_result_type",
    "has_tested_runtime_contract",
    "unsupported_adapter_reasons",
]
