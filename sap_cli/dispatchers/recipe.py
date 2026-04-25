"""Recipe command routing helpers.

This module is the first small D-2 extraction step.  The recipe handler
implementations still live in :mod:`sap_cli.dispatcher` for backward
compatibility with existing private imports and monkeypatch-based tests, but
recipe command selection is no longer embedded directly in the central
``_execute_command`` chain.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

RecipeHandler = Callable[[dict[str, Any]], dict[str, Any]]


RECIPE_COMMANDS: frozenset[str] = frozenset(
    {
        "recipe_save",
        "recipe_search",
        "recipe_show",
        "recipe_list",
        "recipe_delete",
        "recipe_success",
        "recipe_query",
        "recipe_pending",
        "recipe_failure",
        "recipe_enable",
        "recipe_version_up",
        "recipe_history",
        "recipe_chain",
    }
)


def is_recipe_command(command: str) -> bool:
    """Return whether ``command`` belongs to the recipe dispatcher group."""
    return command in RECIPE_COMMANDS


def dispatch_recipe_command(
    command: str,
    args: dict[str, Any],
    handlers: Mapping[str, RecipeHandler],
) -> dict[str, Any]:
    """Dispatch a recipe command using the supplied compatibility handlers."""
    try:
        handler = handlers[command]
    except KeyError as exc:
        raise ValueError(f"미구현 recipe 명령: {command}") from exc
    return handler(args)
