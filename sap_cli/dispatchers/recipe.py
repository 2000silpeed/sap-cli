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
SessionStoreFactory = Callable[[], Any]


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


def _execution_plan(recipe: Any) -> list[dict[str, Any]]:
    """Build the client-facing execution plan for a recipe."""
    plan = []
    for i, step in enumerate(recipe.steps):
        plan_step = {
            "order": i + 1,
            "tool": step.tool,
            "args": step.args,
        }
        if step.hint:
            plan_step["hint"] = step.hint
        plan.append(plan_step)
    return plan


def dispatch_recipe_search(
    args: dict[str, Any],
    get_recipe_store: RecipeStoreFactory,
) -> dict[str, Any]:
    """Search recipes and expand a single match into an executable plan."""
    store = get_recipe_store()
    query = str(args["query"])
    results = store.search(query)

    if len(results) == 1:
        r = results[0]
        return {
            "query": query,
            "count": 1,
            "exact_match": True,
            "recipe_id": r.id,
            "description": r.description,
            "context": r.context,
            "success_count": r.success_count,
            "execution_plan": _execution_plan(r),
            "instruction": (
                f"레시피 '{r.id}'가 검색되었습니다. "
                "아래 execution_plan의 단계를 순서대로 정확히 실행하세요. "
                "sap_tree_read, sap_screen 등으로 별도 탐색하지 마세요. "
                "레시피의 tool과 args를 그대로 호출하면 됩니다. "
                "모든 단계 완료 후 sap_recipe_success를 호출하세요."
            ),
        }

    summaries = []
    for r in results:
        summaries.append({
            "id": r.id,
            "description": r.description,
            "context": r.context,
            "keywords": r.keywords,
            "step_count": len(r.steps),
            "success_count": r.success_count,
        })
    return {
        "query": query,
        "count": len(results),
        "exact_match": False,
        "recipes": summaries,
        "hint": "여러 레시피가 검색되었습니다. context를 비교하여 가장 적합한 레시피의 id를 선택한 후, sap_recipe_search에 해당 id를 정확히 입력하여 상세 steps를 조회하세요.",
    }


def dispatch_recipe_show(
    args: dict[str, Any],
    get_recipe_store: RecipeStoreFactory,
) -> dict[str, Any]:
    """Return a recipe as an executable plan."""
    store = get_recipe_store()
    recipe_id = str(args["recipe_id"])
    r = store.load(recipe_id)
    if not r:
        return {"error": True, "message": f"레시피 '{recipe_id}'를 찾을 수 없습니다."}

    return {
        "recipe_id": r.id,
        "description": r.description,
        "context": r.context,
        "success_count": r.success_count,
        "execution_plan": _execution_plan(r),
        "instruction": (
            f"레시피 '{r.id}'의 상세 실행 계획입니다. "
            "execution_plan의 단계를 순서대로 정확히 실행하세요. "
            "sap_tree_read, sap_screen 등으로 별도 탐색하지 마세요. "
            "레시피의 tool과 args를 그대로 호출하면 됩니다. "
            "모든 단계 완료 후 sap_recipe_success를 호출하세요."
        ),
    }


def dispatch_recipe_list(
    get_recipe_store: RecipeStoreFactory,
) -> dict[str, Any]:
    """List all saved recipes."""
    store = get_recipe_store()
    recipes = store.list_all()
    return {"count": len(recipes), "recipes": recipes}


def dispatch_recipe_delete(
    args: dict[str, Any],
    delete_recipe_with_links: Callable[[str], dict[str, Any]],
) -> dict[str, Any]:
    """Delete a recipe and clean ontology links through the supplied facade."""
    recipe_id = str(args["recipe_id"])
    result = delete_recipe_with_links(recipe_id)
    return {
        "deleted": result["deleted"],
        "recipe_id": recipe_id,
        "cleaned_tcodes": result["cleaned_tcodes"],
    }


def dispatch_recipe_success(
    args: dict[str, Any],
    get_recipe_store: RecipeStoreFactory,
) -> dict[str, Any]:
    """Increment a recipe's success counter."""
    store = get_recipe_store()
    recipe_id = str(args["recipe_id"])
    store.increment_success(recipe_id)
    recipe = store.load(recipe_id)
    return {
        "recipe_id": recipe_id,
        "success_count": recipe.success_count if recipe else 0,
    }


def dispatch_recipe_query(
    args: dict[str, Any],
    get_recipe_store: RecipeStoreFactory,
) -> dict[str, Any]:
    """Query recipes by tcode and/or keywords for automatic AI reference."""
    store = get_recipe_store()
    tcode = args.get("tcode")
    keywords = args.get("keywords")

    if not tcode and not keywords:
        raise ValueError("tcode 또는 keywords 중 하나는 필수입니다.")

    scored = store.query_for_ai(tcode=tcode, keywords=keywords)
    if not scored:
        return {
            "tcode": tcode,
            "keywords": keywords,
            "count": 0,
            "recipes": [],
            "hint": "일치하는 레시피가 없습니다. 직접 트랜잭션을 탐색하세요.",
        }

    if len(scored) == 1:
        _score, r = scored[0]
        return {
            "tcode": tcode,
            "keywords": keywords,
            "count": 1,
            "exact_match": True,
            "recipe_id": r.id,
            "description": r.description,
            "context": r.context,
            "success_count": r.success_count,
            "execution_plan": _execution_plan(r),
            "instruction": (
                f"레시피 '{r.id}'가 검색되었습니다. "
                "execution_plan의 단계를 순서대로 정확히 실행하세요. "
                "별도 탐색(sap_tree_read, sap_screen)하지 마세요. "
                "모든 단계 완료 후 sap_recipe_success를 호출하세요."
            ),
        }

    recipes = []
    for score, r in scored:
        recipes.append({
            "id": r.id,
            "description": r.description,
            "context": r.context,
            "keywords": r.keywords,
            "step_count": len(r.steps),
            "success_count": r.success_count,
            "relevance_score": score,
        })
    return {
        "tcode": tcode,
        "keywords": keywords,
        "count": len(recipes),
        "exact_match": False,
        "recipes": recipes,
        "hint": (
            "여러 레시피가 검색되었습니다. description과 context를 비교하여 "
            "가장 적합한 레시피를 선택한 후 sap_recipe_show(recipe_id=...)로 "
            "실행 계획을 조회하세요."
        ),
    }


def dispatch_recipe_pending(
    args: dict[str, Any],
    get_session_store: SessionStoreFactory,
) -> dict[str, Any]:
    """Summarize unapproved candidate sessions suggested as recipes."""
    from ..session_analyzer import analyze

    store = get_session_store()
    limit = int(args.get("limit", 20))
    sessions = store.list_sessions(status="completed", limit=max(limit * 4, limit))

    pending = []
    for s_summary in sessions:
        session = store.load(s_summary["id"])
        if session is None:
            continue
        analyze(session)
        if not session.candidate or session.approved:
            continue
        pending.append({
            "session_id": session.id,
            "user_request": session.user_request[:120],
            "started_at": session.started_at,
            "step_count": len(session.steps),
            "sap_user": session.sap_user,
            "sap_system": session.sap_system,
        })
        if len(pending) >= limit:
            break

    return {
        "count": len(pending),
        "pending_suggestions": pending,
        "instruction": (
            "이 세션들은 자동 평가에서 레시피 저장 후보로 분류되었으나 아직 승인되지 않았습니다. "
            "sap_session_show로 상세 확인 후 sap_session_approve로 승인하면 레시피로 저장됩니다."
        ) if pending else "현재 미승인 레시피 후보가 없습니다.",
    }
