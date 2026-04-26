"""Recipe command routing helpers.

This module is the first small D-2 extraction step.  The recipe handler
implementations still live in :mod:`sap_cli.dispatcher` for backward
compatibility with existing private imports and monkeypatch-based tests, but
recipe command selection is no longer embedded directly in the central
``_execute_command`` chain.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from typing import Any

RecipeHandler = Callable[[dict[str, Any]], dict[str, Any]]
RecipeStoreFactory = Callable[[], Any]
SessionStoreFactory = Callable[[], Any]
OntologyStoreFactory = Callable[[], Any]


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
        "recipe_replay_plan",
        "recipe_replay_execute",
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


def dispatch_recipe_replay_plan(
    args: dict[str, Any],
    get_recipe_store: RecipeStoreFactory,
) -> dict[str, Any]:
    """Return a safe, non-mutating recipe replay validation plan.

    This is the first E-2 surface: it does not execute SAP actions.  It gives
    the caller a structured precheck, dry-run plan, post-execution verification
    checklist, and explicit success/failure reporting instructions.
    """
    from ..recipe import efficiency_score

    store = get_recipe_store()
    recipe_id = str(args["recipe_id"])
    recipe = store.load(recipe_id)
    if recipe is None:
        return {"error": True, "message": f"레시피 '{recipe_id}'를 찾을 수 없습니다."}

    current_tcode = str(args.get("current_tcode", "") or "")
    current_screen = str(args.get("current_screen", "") or "")
    allow_disabled = bool(args.get("allow_disabled", False))
    precheck = _build_replay_precheck(
        recipe,
        current_tcode=current_tcode,
        current_screen=current_screen,
        allow_disabled=allow_disabled,
    )
    dry_run_plan = _execution_plan(recipe)

    return {
        "recipe_id": recipe.id,
        "description": recipe.description,
        "context": recipe.context,
        "version": recipe.version,
        "disabled": recipe.disabled,
        "disabled_reason": recipe.disabled_reason,
        "success_count": recipe.success_count,
        "failure_count": recipe.failure_count,
        "quality_score": efficiency_score(recipe),
        "flow": ["precheck", "dry_run_plan", "execute", "verify", "report"],
        "precheck": precheck,
        "dry_run_plan": dry_run_plan,
        "execute": {
            "mode": "manual_tool_calls",
            "allowed": precheck["status"] != "blocked",
            "instruction": (
                "blocked가 아니면 dry_run_plan의 tool/args를 순서대로 호출하세요. "
                "각 단계 전 precondition이 있으면 현재 화면/상태와 맞는지 확인하세요."
            ),
        },
        "verify": {
            "required_observations": ["sap_status", "sap_modal", "sap_screen_summary"],
            "success_criteria": [
                "마지막 SAP status가 오류/중단 상태가 아님",
                "예상치 못한 modal dialog가 남아있지 않음",
                "screen_summary가 recipe 목적의 완료 화면 또는 다음 정상 화면과 일치함",
            ],
            "instruction": (
                "실행 후 sap_status, sap_modal, 필요 시 sap_screen을 확인해 완료 여부를 판단하세요."
            ),
        },
        "report": {
            "on_success": {
                "tool": "sap_recipe_success",
                "args": {"recipe_id": recipe.id},
            },
            "on_failure": {
                "tool": "sap_recipe_failure",
                "args": {
                    "recipe_id": recipe.id,
                    "reason": "실패 단계/관찰된 status/modal/screen_summary를 요약",
                },
                "auto_call_candidate": True,
                "note": (
                    "향후 execute 래퍼가 도입되면 verify 실패 시 자동 호출 가능. "
                    "현재는 안전상 호출자가 실패를 확인한 뒤 명시적으로 호출합니다."
                ),
            },
        },
        "instruction": (
            "이 응답은 실행하지 않는 replay 검증 계획입니다. precheck를 검토하고, "
            "dry_run_plan 실행 후 verify 관찰값에 따라 sap_recipe_success 또는 "
            "sap_recipe_failure를 반드시 호출하세요."
        ),
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
        if getattr(step, "precondition", None):
            plan_step["precondition"] = step.precondition
        plan.append(plan_step)
    return plan


def _step_tcode(step: Any) -> str:
    """Return a normalized tcode implied by a recipe step, if any."""
    tool = getattr(step, "tool", "")
    args = getattr(step, "args", {}) or {}
    if tool == "sap_run" and args.get("tcode"):
        return str(args["tcode"]).upper().strip()
    if tool == "sap_command" and args.get("cmd"):
        cmd = str(args["cmd"]).strip()
        if cmd.startswith("/n") or cmd.startswith("/o"):
            return cmd[2:].upper().strip()
    return ""


def _normalize_tcode(value: str) -> str:
    normalized = str(value).upper().strip()
    if normalized.startswith("/N") or normalized.startswith("/O"):
        return normalized[2:].strip()
    return normalized


def _starts_with_navigation_to(recipe: Any, tcode: str) -> bool:
    if not recipe.steps or not tcode:
        return False
    return _step_tcode(recipe.steps[0]) == _normalize_tcode(tcode)


def _collect_step_preconditions(recipe: Any) -> list[dict[str, Any]]:
    preconditions = []
    for i, step in enumerate(recipe.steps):
        if getattr(step, "precondition", None):
            preconditions.append({
                "order": i + 1,
                "tool": step.tool,
                "precondition": step.precondition,
            })
    return preconditions


def _build_replay_precheck(
    recipe: Any,
    *,
    current_tcode: str = "",
    current_screen: str = "",
    allow_disabled: bool = False,
) -> dict[str, Any]:
    """Build a non-mutating precheck summary for recipe replay."""
    seq = [_normalize_tcode(t) for t in list(recipe.tcode_sequence or []) if str(t).strip()]
    if not seq:
        # Legacy recipes may not have tcode_sequence populated.
        seq = [_step_tcode(step) for step in recipe.steps if _step_tcode(step)]
    required_start_tcode = seq[0] if seq else ""
    normalized_current_tcode = _normalize_tcode(current_tcode)
    issues: list[dict[str, Any]] = []
    checks: list[dict[str, Any]] = []

    if recipe.disabled and not allow_disabled:
        issues.append({
            "code": "recipe_disabled",
            "severity": "blocker",
            "message": "비활성화된 레시피입니다. sap_recipe_enable 또는 recipe 수정 후 재검증하세요.",
        })

    if required_start_tcode:
        if not normalized_current_tcode:
            checks.append({
                "name": "start_tcode",
                "status": "unknown",
                "expected": required_start_tcode,
                "actual": "",
                "message": "current_tcode가 제공되지 않아 현재 트랜잭션 일치 여부를 확인하지 못했습니다.",
            })
        elif normalized_current_tcode == required_start_tcode:
            checks.append({
                "name": "start_tcode",
                "status": "ok",
                "expected": required_start_tcode,
                "actual": normalized_current_tcode,
                "message": "현재 tcode가 레시피 시작 조건과 일치합니다.",
            })
        elif _starts_with_navigation_to(recipe, required_start_tcode):
            checks.append({
                "name": "start_tcode",
                "status": "will_navigate",
                "expected": required_start_tcode,
                "actual": normalized_current_tcode,
                "message": "현재 tcode는 다르지만 첫 단계가 필요한 tcode로 이동합니다.",
            })
        else:
            issues.append({
                "code": "start_tcode_mismatch",
                "severity": "warning",
                "expected": required_start_tcode,
                "actual": normalized_current_tcode,
                "message": "현재 tcode가 레시피 시작 tcode와 다릅니다. 실행 전 화면 이동이 필요할 수 있습니다.",
            })
    else:
        checks.append({
            "name": "start_tcode",
            "status": "not_declared",
            "message": "레시피에서 시작 tcode를 추론하지 못했습니다.",
        })

    if current_screen:
        checks.append({
            "name": "current_screen",
            "status": "captured",
            "actual": current_screen,
            "message": "호출자가 제공한 현재 화면 식별자를 replay report에 포함합니다.",
        })

    status = "ready"
    if any(i["severity"] == "blocker" for i in issues):
        status = "blocked"
    elif issues or any(c["status"] in {"unknown", "not_declared"} for c in checks):
        status = "needs_review"

    return {
        "status": status,
        "required_start_tcode": required_start_tcode,
        "current_tcode": normalized_current_tcode,
        "current_screen": current_screen,
        "checks": checks,
        "issues": issues,
        "step_preconditions": _collect_step_preconditions(recipe),
    }


def get_recipe_store() -> Any:
    """Create the default recipe store."""
    from ..recipe import RecipeStore

    return RecipeStore()


def collect_recipe_hints(
    tcode: str,
    get_recipe_store: RecipeStoreFactory,
    limit: int = 3,
) -> dict[str, Any] | None:
    """Collect safe recipe candidates to attach to sap_run responses."""
    from ..recipe import efficiency_score

    if not tcode:
        return None
    try:
        store = get_recipe_store()
        recipes = store.find_by_tcode(tcode)
    except (OSError, PermissionError) as exc:
        logging.getLogger(__name__).warning("recipe_hints 수집 실패(%s): %s", tcode, exc)
        return None
    if not recipes:
        return None
    candidates = []
    for r in recipes[:limit]:
        seq = list(store._tcode_seq(r))
        item: dict[str, Any] = {
            "id": r.id,
            "description": r.description,
            "step_count": len(r.steps),
            "success_count": r.success_count,
            "quality_score": efficiency_score(r),
        }
        if len(seq) > 1:
            item["tcode_sequence"] = seq
            item["cross_tcode"] = True
        candidates.append(item)
    return {
        "available": len(recipes),
        "candidates": candidates,
        "instruction": (
            f"'{tcode}' 관련 기존 레시피 {len(recipes)}개가 있습니다. "
            "목적에 맞는 레시피가 있으면 sap_recipe_show로 상세 context와 steps를 확인 후 그대로 따라 실행하고, "
            "완료 후 sap_recipe_success를 호출하세요. "
            "맞는 레시피가 없을 때만 직접 탐색하고 성공 후 sap_recipe_save로 저장하세요."
        ),
    }


def extract_recipe_tcodes(recipe: Any) -> set[str]:
    """Extract referenced tcodes from a recipe's executable steps."""
    tcodes: set[str] = set()
    for step in recipe.steps:
        if step.tool == "sap_run" and "tcode" in step.args:
            tcodes.add(str(step.args["tcode"]).upper())
        elif step.tool == "sap_command" and "cmd" in step.args:
            cmd = str(step.args["cmd"])
            if cmd.startswith("/n") or cmd.startswith("/o"):
                tcodes.add(cmd[2:].upper())
    return tcodes


def unlink_recipe_from_ontology(
    recipe: Any,
    get_store: OntologyStoreFactory,
    extract_tcodes: Callable[[Any], set[str]] = extract_recipe_tcodes,
) -> list[str]:
    """Remove a recipe's ontology links from related transactions."""
    ont_store = get_store()
    path_key = recipe.description or recipe.id
    recipe_id = recipe.id
    cleaned: list[str] = []

    tcodes = extract_tcodes(recipe)
    if tcodes:
        target_tcodes = list(tcodes)
    else:
        try:
            target_tcodes = list(ont_store.list_transactions())
        except (OSError, PermissionError) as exc:
            logging.getLogger(__name__).warning(
                "ontology list_transactions I/O 실패: %s", exc
            )
            target_tcodes = []

    for tcode in target_tcodes:
        ontology = ont_store.load_transaction(tcode)
        if not ontology:
            continue
        changed = False
        if recipe_id in ontology.recipes:
            ontology.recipes.remove(recipe_id)
            changed = True
        if path_key in ontology.action_paths:
            del ontology.action_paths[path_key]
            changed = True
        if changed:
            ont_store.save_transaction(ontology)
            cleaned.append(tcode)
    return cleaned


def delete_recipe_with_links(
    recipe_id: str,
    get_recipe_store: RecipeStoreFactory,
    unlink_recipe: Callable[[Any], list[str]],
) -> dict[str, Any]:
    """Delete a recipe JSON file and clean its ontology links."""
    store = get_recipe_store()
    recipe = store.load(recipe_id)
    deleted = store.delete(recipe_id)

    cleaned_tcodes: list[str] = []
    if recipe is not None:
        cleaned_tcodes = unlink_recipe(recipe)
    return {"deleted": deleted, "cleaned_tcodes": cleaned_tcodes}


def link_recipe_to_ontology(
    recipe_id: str,
    steps: list[Any],
    get_store: OntologyStoreFactory,
    description: str = "",
    context: str = "",
    keywords: list[str] | None = None,
) -> list[str]:
    """Link recipe steps to transaction ontology action paths."""
    import time as _time
    from ..ontology.schema import ActionPath, ActionStep

    tcodes: set[str] = set()
    for step in steps:
        tool = step.tool if hasattr(step, "tool") else step.get("tool", "")
        args = step.args if hasattr(step, "args") else step.get("args", {})
        if tool == "sap_run" and "tcode" in args:
            tcodes.add(str(args["tcode"]).upper())
        elif tool == "sap_command" and "cmd" in args:
            cmd = str(args["cmd"])
            if cmd.startswith("/n") or cmd.startswith("/o"):
                tcodes.add(cmd[2:].upper())

    linked = []
    if tcodes:
        ont_store = get_store()
        for tcode in tcodes:
            ontology = ont_store.load_transaction(tcode)
            if ontology:
                if recipe_id not in ontology.recipes:
                    ontology.recipes.append(recipe_id)

                action_steps = []
                for s in steps:
                    if isinstance(s, dict):
                        action_steps.append(ActionStep(**s))
                    else:
                        action_steps.append(ActionStep(tool=s.tool, args=s.args, hint=s.hint))

                path_key = description or recipe_id
                ontology.action_paths[path_key] = ActionPath(
                    context=context,
                    keywords=keywords or [],
                    steps=action_steps,
                    success_count=0,
                    updated_at=_time.time(),
                )
                ont_store.save_transaction(ontology)
                linked.append(tcode)
    return linked


def dispatch_recipe_save(
    args: dict[str, Any],
    get_recipe_store: RecipeStoreFactory,
    unlink_recipe_from_ontology: Callable[[Any], list[str]],
    link_recipe_to_ontology: Callable[..., list[str]],
    delete_recipe_with_links: Callable[[str], dict[str, Any]],
) -> dict[str, Any]:
    """Save or update a recipe, including duplicate handling and ontology links."""
    from ..recipe import Recipe, RecipeStep, efficiency_score

    store = get_recipe_store()
    recipe_id = str(args["recipe_id"])
    description = str(args["description"])
    context = str(args.get("context", ""))
    keywords = args.get("keywords", [])
    if isinstance(keywords, str):
        keywords = [k.strip() for k in keywords.split(",")]
    steps_raw = args.get("steps", [])
    steps = [RecipeStep(**s) if isinstance(s, dict) else s for s in steps_raw]

    on_duplicate = args.get("on_duplicate")
    if on_duplicate is not None:
        on_duplicate = str(on_duplicate).lower().strip()
        if on_duplicate not in ("keep_existing", "replace", "keep_both"):
            return {
                "status": "error",
                "message": (
                    "on_duplicate는 keep_existing|replace|keep_both 중 하나여야 합니다. "
                    f"받은 값: {on_duplicate!r}"
                ),
            }

    approved_ids_raw = args.get("approved_ids")
    if approved_ids_raw is None:
        approved_ids: set[str] | None = None
    elif isinstance(approved_ids_raw, str):
        approved_ids = {s.strip() for s in approved_ids_raw.split(",") if s.strip()}
    elif isinstance(approved_ids_raw, (list, tuple)):
        approved_ids = {str(s).strip() for s in approved_ids_raw if str(s).strip()}
    else:
        return {
            "status": "error",
            "message": "approved_ids는 문자열 배열 또는 쉼표 구분 문자열이어야 합니다.",
        }

    existing = store.load(recipe_id)
    if existing:
        unlink_recipe_from_ontology(existing)
        existing.description = description
        existing.context = context
        existing.keywords = keywords
        existing.steps = steps
        store.save(existing)
        linked_tcodes = link_recipe_to_ontology(
            recipe_id, steps, description=description, context=context, keywords=keywords,
        )
        return {
            "status": "saved",
            "saved": recipe_id,
            "updated": True,
            "step_count": len(steps),
            "linked_tcodes": linked_tcodes,
        }

    if on_duplicate is None:
        similar = store.find_similar(
            description=description,
            context=context,
            keywords=keywords,
            steps=steps,
            exclude_id=recipe_id,
            threshold=0.6,
        )
        if similar:
            new_recipe_preview = Recipe(
                id=recipe_id,
                description=description,
                context=context,
                keywords=keywords,
                steps=steps,
            )
            new_eff = efficiency_score(new_recipe_preview)
            candidates = []
            best_existing: Recipe | None = None
            for sim, r in similar:
                eff = efficiency_score(r)
                candidates.append({
                    "id": r.id,
                    "description": r.description,
                    "context": r.context,
                    "keywords": r.keywords,
                    "step_count": len(r.steps),
                    "success_count": r.success_count,
                    "similarity": sim,
                    "efficiency": eff,
                })
                if best_existing is None or eff > efficiency_score(best_existing):
                    best_existing = r

            if best_existing is None:
                recommended = "keep_both"
                reason = "비교 대상을 찾지 못해 안전하게 둘 다 유지 권장."
            else:
                best_eff = efficiency_score(best_existing)
                if new_eff > best_eff + 0.05:
                    recommended = "replace"
                    reason = (
                        f"새 레시피가 더 효율적(new_eff={new_eff} > existing_eff={best_eff}). "
                        f"기존 '{best_existing.id}'를 대체 권장."
                    )
                elif best_eff > new_eff + 0.05:
                    recommended = "keep_existing"
                    reason = (
                        f"기존 '{best_existing.id}'가 더 효율적"
                        f"(existing_eff={best_eff} > new_eff={new_eff}). 저장 생략 권장."
                    )
                else:
                    recommended = "keep_existing"
                    reason = (
                        f"효율성이 비슷함(new_eff={new_eff}, existing_eff={best_eff}). "
                        "중복 방지 위해 기존 유지 권장."
                    )

            candidate_ids = [c["id"] for c in candidates]
            return {
                "status": "duplicate_candidates",
                "recipe_id": recipe_id,
                "new_efficiency": new_eff,
                "candidates": candidates,
                "candidate_ids": candidate_ids,
                "recommended": recommended,
                "reason": reason,
                "instruction": (
                    "유사한 레시피가 이미 있습니다. 사용자에게 후보를 보여주고 확인받은 뒤 "
                    "on_duplicate 파라미터와 함께 sap_recipe_save를 다시 호출하세요. "
                    "값: 'keep_existing'(저장 취소) | 'replace'(기존 삭제 후 저장, success_count 승계) | "
                    "'keep_both'(강제 저장). "
                    "on_duplicate='replace'일 때는 candidate_ids를 approved_ids 파라미터로 그대로 전달해야 합니다 "
                    "(사용자가 확인한 후보 집합이 변하지 않았음을 서버가 검증). "
                    "recommended는 참고용이며 최종 결정은 사용자가 합니다."
                ),
            }

    if on_duplicate == "keep_existing":
        return {
            "status": "skipped",
            "recipe_id": recipe_id,
            "reason": "사용자가 기존 레시피 유지를 선택했습니다.",
        }

    inherited_success = 0
    replaced_ids: list[str] = []
    cleaned_tcodes_all: list[str] = []
    if on_duplicate == "replace":
        if approved_ids is None:
            similar_now = store.find_similar(
                description=description, context=context, keywords=keywords,
                steps=steps, exclude_id=recipe_id, threshold=0.6,
            )
            return {
                "status": "error",
                "message": (
                    "on_duplicate='replace'는 approved_ids 파라미터가 필수입니다. "
                    "1차 응답의 candidate_ids를 그대로 전달하세요."
                ),
                "current_candidate_ids": sorted({r.id for _, r in similar_now}),
            }

        # 삭제 직전 2단계 재검증: 승인 시점 → 실행 직전 → 실제 삭제 루프
        # (동일 프로세스 내 race에도 안전; 다른 프로세스의 변경은 pre-loop 검증으로 차단)
        similar = store.find_similar(
            description=description, context=context, keywords=keywords,
            steps=steps, exclude_id=recipe_id, threshold=0.6,
        )
        current_ids = {r.id for _, r in similar}
        drift = current_ids.symmetric_difference(approved_ids)
        if drift:
            return {
                "status": "candidate_drift",
                "message": (
                    "승인한 후보 집합과 현재 유사 후보가 다릅니다. "
                    "(다른 세션이 레시피를 수정/추가/삭제했을 수 있음) "
                    "sap_recipe_save를 재호출하여 최신 후보를 다시 확인하세요."
                ),
                "approved_ids": sorted(approved_ids),
                "current_candidate_ids": sorted(current_ids),
                "drift_ids": sorted(drift),
            }

        # 최종 재검증 직전에 다시 확인 (find_similar ↔ delete 루프 사이 race 최소화)
        verify = store.find_similar(
            description=description, context=context, keywords=keywords,
            steps=steps, exclude_id=recipe_id, threshold=0.6,
        )
        verify_ids = {r.id for _, r in verify}
        if verify_ids != approved_ids:
            return {
                "status": "candidate_drift",
                "message": "삭제 직전 후보 집합이 변경되었습니다. 재호출하세요.",
                "approved_ids": sorted(approved_ids),
                "current_candidate_ids": sorted(verify_ids),
                "drift_ids": sorted(verify_ids.symmetric_difference(approved_ids)),
            }

        for _, r in verify:
            if r.id not in approved_ids:
                continue
            inherited_success += r.success_count
            result = delete_recipe_with_links(r.id)
            if result["deleted"]:
                replaced_ids.append(r.id)
            cleaned_tcodes_all.extend(result["cleaned_tcodes"])

    recipe = Recipe(
        id=recipe_id,
        description=description,
        context=context,
        keywords=keywords,
        steps=steps,
        success_count=inherited_success,
    )
    store.save(recipe)

    linked_tcodes = link_recipe_to_ontology(
        recipe_id, steps, description=description, context=context, keywords=keywords,
    )

    return {
        "status": "saved",
        "saved": recipe_id,
        "updated": False,
        "step_count": len(steps),
        "linked_tcodes": linked_tcodes,
        "on_duplicate": on_duplicate,
        "replaced_ids": replaced_ids,
        "cleaned_tcodes": cleaned_tcodes_all,
        "inherited_success_count": inherited_success,
    }


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


def dispatch_recipe_version_up(
    args: dict[str, Any],
    get_recipe_store: RecipeStoreFactory,
    unlink_recipe_from_ontology: Callable[[Any], list[str]],
    link_recipe_to_ontology: Callable[..., list[str]],
) -> dict[str, Any]:
    """Save a new recipe version and refresh ontology links."""
    from ..recipe import RecipeStep

    store = get_recipe_store()
    recipe_id = str(args["recipe_id"])
    steps_raw = args.get("new_steps", [])
    new_steps = [RecipeStep(**s) if isinstance(s, dict) else s for s in steps_raw]
    new_context = str(args.get("new_context", ""))
    change_note = str(args.get("change_note", ""))

    if not new_steps:
        return {"error": True, "message": "new_steps는 비어있을 수 없습니다."}

    existing = store.load(recipe_id)
    if existing is None:
        return {"error": True, "message": f"레시피 '{recipe_id}'를 찾을 수 없습니다."}

    unlink_recipe_from_ontology(existing)
    new_recipe = store.version_up(
        recipe_id, new_steps=new_steps, new_context=new_context, change_note=change_note,
    )
    if new_recipe is None:
        return {"error": True, "message": f"version_up 실패: {recipe_id}"}

    linked_tcodes = link_recipe_to_ontology(
        recipe_id, new_recipe.steps,
        description=new_recipe.description,
        context=new_recipe.context,
        keywords=new_recipe.keywords,
    )

    return {
        "recipe_id": recipe_id,
        "new_version": new_recipe.version,
        "previous_version_archive": new_recipe.previous_version_archive,
        "step_count": len(new_recipe.steps),
        "linked_tcodes": linked_tcodes,
    }


def dispatch_recipe_query(
    args: dict[str, Any],
    get_recipe_store: RecipeStoreFactory,
) -> dict[str, Any]:
    """Query recipes by tcode and/or keywords for automatic AI reference."""
    from ..recipe import efficiency_score

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
            "quality_score": efficiency_score(r),
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
            "quality_score": efficiency_score(r),
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
