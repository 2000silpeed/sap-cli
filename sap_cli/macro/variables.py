"""Macro variable normalization helpers."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from sap_cli.macro.schema import Macro

_PLACEHOLDER_NAME_PATTERNS = (
    re.compile(r"^\s*\$\{(\w+)\}\s*$"),
    re.compile(r"^\s*\{\{\s*([\w.-]+)\s*\}\}\s*$"),
    re.compile(r"^\s*\{([\w.-]+)\}\s*$"),
)


def normalize_variable_name(name: str) -> str:
    """Normalize user-facing variable names to the ``${name}`` format."""
    stripped = str(name).strip()
    for pattern in _PLACEHOLDER_NAME_PATTERNS:
        match = pattern.match(stripped)
        if match:
            stripped = match.group(1)
            break
    return re.sub(r"\W+", "_", stripped).strip("_") or "var"


def normalize_runtime_variables(
    raw: Any,
    macro: Macro | None = None,
) -> dict[str, str]:
    """Normalize macro-play variable payloads into ``{name: value}``.

    Runtime callers in practice send several shapes:
    - ``{"matnr": "200-200"}``
    - ``{"matnr": {"value": "200-200"}}``
    - ``[{"name": "matnr", "value": "200-200"}]``
    - ``{"variables": [...]}``
    - ``"matnr=200-200,werks=1000"``
    """
    variables: dict[str, str] = {}
    for name, value in _iter_runtime_pairs(raw):
        if name is None:
            continue
        normalized_name = normalize_variable_name(str(name))
        variables[normalized_name] = "" if value is None else str(value)

    if not macro:
        return variables

    canonical_names = {
        normalize_variable_name(var.name).lower(): var.name
        for var in macro.variables
    }
    return {
        canonical_names.get(name.lower(), name): value
        for name, value in variables.items()
    }


def resolve_variables_in_data(value: Any, variables: dict[str, str]) -> Any:
    """Resolve ``${name}`` placeholders recursively in macro step data."""
    if isinstance(value, str):
        return _resolve_variables_in_string(value, variables)
    if isinstance(value, list):
        return [resolve_variables_in_data(item, variables) for item in value]
    if isinstance(value, dict):
        return {
            key: resolve_variables_in_data(item, variables)
            for key, item in value.items()
        }
    return value


def replace_literal_in_data(value: Any, actual: str, placeholder: str) -> Any:
    """Replace a literal string recursively in macro step data."""
    if isinstance(value, str):
        return value.replace(actual, placeholder)
    if isinstance(value, list):
        return [replace_literal_in_data(item, actual, placeholder) for item in value]
    if isinstance(value, dict):
        return {
            key: replace_literal_in_data(item, actual, placeholder)
            for key, item in value.items()
        }
    return value


def value_from_mapping(mapping: Mapping[str, Any], *keys: str) -> Any:
    """Return the first present, non-None value for any of ``keys``."""
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return None


def _resolve_variables_in_string(value: str, variables: dict[str, str]) -> str:
    pattern = re.compile(r"\$\{(\w+)\}")

    def _replace(match: re.Match[str]) -> str:
        var_name = match.group(1)
        return variables.get(var_name, match.group(0))

    return pattern.sub(_replace, value)


def _iter_runtime_pairs(raw: Any) -> list[tuple[str | None, Any]]:
    if raw in (None, ""):
        return []

    if isinstance(raw, str):
        parsed = _parse_runtime_string(raw)
        if parsed is not raw:
            return _iter_runtime_pairs(parsed)
        return _parse_assignment_pairs(raw)

    if isinstance(raw, Mapping):
        nested = value_from_mapping(raw, "variables", "vars", "values")
        if nested is not None and len(raw) == 1:
            return _iter_runtime_pairs(nested)
        if _looks_like_single_variable(raw):
            return [(
                value_from_mapping(raw, "name", "key", "id", "variable"),
                value_from_mapping(raw, "value", "actual", "default"),
            )]
        pairs: list[tuple[str | None, Any]] = []
        for key, value in raw.items():
            if key in {"variables", "vars", "values"}:
                pairs.extend(_iter_runtime_pairs(value))
                continue
            if isinstance(value, Mapping):
                pairs.append((
                    str(key),
                    value_from_mapping(value, "value", "actual", "default"),
                ))
            else:
                pairs.append((str(key), value))
        return pairs

    if isinstance(raw, Sequence) and not isinstance(raw, (bytes, bytearray)):
        pairs = []
        for item in raw:
            pairs.extend(_iter_runtime_pairs(item))
        return pairs

    raise ValueError(f"지원하지 않는 변수 구조: {type(raw).__name__}")


def _parse_runtime_string(raw: str) -> Any:
    stripped = raw.strip()
    if not stripped:
        return {}
    if stripped[0] in "[{":
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            try:
                return json.loads(stripped.replace("'", '"'))
            except json.JSONDecodeError:
                return raw
    return raw


def _parse_assignment_pairs(raw: str) -> list[tuple[str | None, str]]:
    stripped = raw.strip().strip("{}")
    pairs: list[tuple[str | None, str]] = []
    for pair in re.split(r"\s*,\s*", stripped):
        if not pair:
            continue
        sep = "=" if "=" in pair else ":" if ":" in pair else ""
        if not sep:
            raise ValueError(f"변수 파싱 실패: {raw}")
        key, value = pair.split(sep, 1)
        pairs.append((key.strip().strip("'\""), value.strip().strip("'\"")))
    return pairs


def _looks_like_single_variable(raw: Mapping[str, Any]) -> bool:
    return any(key in raw for key in ("name", "key", "id", "variable")) and any(
        key in raw for key in ("value", "actual", "default")
    )
