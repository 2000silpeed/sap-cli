"""Strict recursive variable binding for frozen execution plans."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping


_TOKEN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_.-]*)\}")


@dataclass(frozen=True)
class BindingResult:
    value: Any
    used: tuple[str, ...]
    missing: tuple[str, ...]

    def require_complete(self) -> Any:
        if self.missing:
            raise ValueError(
                "missing plan variables: " + ", ".join(self.missing)
            )
        return self.value


def bind_variables(value: Any, variables: Mapping[str, Any]) -> BindingResult:
    """Resolve every ``${name}``; exact tokens retain the value's native type."""

    used: set[str] = set()
    missing: set[str] = set()

    def resolve(item: Any) -> Any:
        if isinstance(item, str):
            exact = _TOKEN.fullmatch(item)
            if exact:
                name = exact.group(1)
                used.add(name)
                if name not in variables:
                    missing.add(name)
                    return item
                return variables[name]

            def replace(match: re.Match[str]) -> str:
                name = match.group(1)
                used.add(name)
                if name not in variables:
                    missing.add(name)
                    return match.group(0)
                return str(variables[name])

            return _TOKEN.sub(replace, item)
        if isinstance(item, list):
            return [resolve(child) for child in item]
        if isinstance(item, tuple):
            return tuple(resolve(child) for child in item)
        if isinstance(item, dict):
            return {resolve(key): resolve(child) for key, child in item.items()}
        return item

    resolved = resolve(value)
    return BindingResult(
        value=resolved,
        used=tuple(sorted(used)),
        missing=tuple(sorted(missing)),
    )


def contains_variable_tokens(value: Any) -> bool:
    if isinstance(value, str):
        return bool(_TOKEN.search(value))
    if isinstance(value, (list, tuple, set)):
        return any(contains_variable_tokens(item) for item in value)
    if isinstance(value, dict):
        return any(
            contains_variable_tokens(key) or contains_variable_tokens(item)
            for key, item in value.items()
        )
    return False


def redact_variables(
    variables: Mapping[str, Any], sensitive_names: set[str] | frozenset[str]
) -> dict[str, Any]:
    normalized = {name.casefold() for name in sensitive_names}
    return {
        name: "<redacted>" if name.casefold() in normalized else value
        for name, value in variables.items()
    }


__all__ = [
    "BindingResult",
    "bind_variables",
    "contains_variable_tokens",
    "redact_variables",
]
