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
RecipeStoreFactory = Callable[[], Any]


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


def dispatch_recipe_failure(
    args: dict[str, Any],
    get_recipe_store: RecipeStoreFactory,
) -> dict[str, Any]:
    """Report recipe execution failure and return updated quality counters."""
    store = get_recipe_store()
    recipe_id = str(args["recipe_id"])
    reason = str(args.get("reason", "")).strip()
    recipe = store.report_failure(recipe_id, reason=reason)
    if recipe is None:
        return {"error": True, "message": f"레시피 '{recipe_id}'를 찾을 수 없습니다."}
    return {
        "recipe_id": recipe.id,
        "failure_count": recipe.failure_count,
        "success_count": recipe.success_count,
        "disabled": recipe.disabled,
        "disabled_reason": recipe.disabled_reason,
    }


def dispatch_recipe_enable(
    args: dict[str, Any],
    get_recipe_store: RecipeStoreFactory,
) -> dict[str, Any]:
    """Re-enable a disabled recipe."""
    store = get_recipe_store()
    recipe_id = str(args["recipe_id"])
    reset = bool(args.get("reset_failure_count", True))
    recipe = store.enable(recipe_id, reset_failure_count=reset)
    if recipe is None:
        return {"error": True, "message": f"레시피 '{recipe_id}'를 찾을 수 없습니다."}
    return {
        "recipe_id": recipe.id,
        "disabled": recipe.disabled,
        "failure_count": recipe.failure_count,
        "success_count": recipe.success_count,
    }


def dispatch_recipe_history(
    args: dict[str, Any],
    get_recipe_store: RecipeStoreFactory,
) -> dict[str, Any]:
    """Return archived versions and active-version metadata for a recipe."""
    store = get_recipe_store()
    recipe_id = str(args["recipe_id"])

    current = store.load(recipe_id)
    archives = store.history(recipe_id)
    return {
        "recipe_id": recipe_id,
        "current_version": current.version if current else None,
        "active": current is not None,
        "archive_count": len(archives),
        "archives": archives,
    }


def dispatch_recipe_chain(
    args: dict[str, Any],
    get_recipe_store: RecipeStoreFactory,
) -> dict[str, Any]:
    """Search recipes spanning one or more tcodes."""
    store = get_recipe_store()
    tcodes_raw = args.get("tcodes", [])
    if isinstance(tcodes_raw, str):
        tcodes = [t.strip() for t in tcodes_raw.split(",") if t.strip()]
    elif isinstance(tcodes_raw, (list, tuple)):
        tcodes = [str(t).strip() for t in tcodes_raw if str(t).strip()]
    else:
        return {"error": True, "message": "tcodes는 배열 또는 쉼표 구분 문자열이어야 합니다."}

    if not tcodes:
        return {"error": True, "message": "tcodes는 최소 1개 이상이어야 합니다."}

    match = str(args.get("match", "all")).lower()
    if match not in ("all", "any"):
        return {"error": True, "message": "match는 'all' 또는 'any'여야 합니다."}

    recipes = store.find_chain(tcodes, match=match)

    summaries = []
    for r in recipes:
        seq = list(store._tcode_seq(r))
        summaries.append({
            "id": r.id,
            "description": r.description,
            "context": r.context,
            "tcode_sequence": seq,
            "cross_tcode": len(seq) > 1,
            "step_count": len(r.steps),
            "success_count": r.success_count,
        })

    return {
        "tcodes": tcodes,
        "match": match,
        "count": len(summaries),
        "recipes": summaries,
        "hint": (
            "여러 tcode를 거치는 복합 레시피입니다. "
            "sap_recipe_show(recipe_id=...)로 상세 단계를 조회하여 그대로 따라 실행하세요."
        ) if summaries else "조건에 맞는 복합 레시피가 없습니다.",
    }
