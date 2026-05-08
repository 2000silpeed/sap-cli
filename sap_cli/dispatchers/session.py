"""Session command dispatch helpers.

Session implementations live here while :mod:`sap_cli.dispatcher` keeps thin
private wrappers for backward-compatible imports and monkeypatch-based tests.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from ..auth.policy import load_policy

SessionStoreFactory = Callable[..., Any]
RecipeStoreFactory = Callable[[], Any]
SapFactory = Callable[[str], Any]
OntologyLinker = Callable[[str, list[Any], str, str, list[str] | None], list[str]]


SESSION_COMMANDS: frozenset[str] = frozenset(
    {
        "session_start",
        "session_end",
        "session_list",
        "session_show",
        "session_candidates",
        "session_approve",
        "session_mark",
        "session_excel",
    }
)

SESSION_SKIP_COMMANDS: frozenset[str] = frozenset(
    {
        "session_start",
        "session_end",
        "session_list",
        "session_show",
        "session_candidates",
        "session_approve",
        "session_mark",
        "session_excel",
        "transcript_query",
        "transcript_plan",
        "ontology_query",
        "ontology_ask",
        "macro_list",
        "macro_show",
        "macro_delete",
        "capture",
        "capture_list",
        "recipe_query",
        "recipe_pending",
    }
)


def is_session_command(command: str) -> bool:
    """Return whether ``command`` belongs to the session dispatcher group."""
    return command in SESSION_COMMANDS


def dispatch_session_command(
    command: str,
    args: dict[str, Any],
    ctx: Any,
    *,
    get_session_store: SessionStoreFactory,
    get_recipe_store: RecipeStoreFactory,
    get_existing_sap: SapFactory,
    link_recipe_to_ontology: OntologyLinker,
) -> dict[str, Any]:
    """Dispatch a session command with facade-provided compatibility hooks."""
    if command == "session_start":
        return dispatch_session_start(args, ctx, get_session_store, get_existing_sap)
    if command == "session_end":
        return dispatch_session_end(args, get_session_store)
    if command == "session_list":
        return dispatch_session_list(args, get_session_store)
    if command == "session_show":
        return dispatch_session_show(args, get_session_store)
    if command == "session_candidates":
        return dispatch_session_candidates(args, get_session_store)
    if command == "session_approve":
        return dispatch_session_approve(
            args,
            get_session_store,
            get_recipe_store,
            link_recipe_to_ontology,
        )
    if command == "session_mark":
        return dispatch_session_mark(args, ctx, get_session_store)
    if command == "session_excel":
        return dispatch_session_excel(args, ctx, get_session_store)
    raise ValueError(f"미구현 session 명령: {command}")


def dispatch_session_start(
    args: dict[str, Any],
    ctx: Any,
    get_session_store: SessionStoreFactory,
    get_existing_sap: SapFactory,
) -> dict[str, Any]:
    """Start a session log."""
    policy = load_policy()
    config = policy.session if policy else None
    if config and not config.enabled:
        return {"error": "세션 로깅이 비활성화되어 있습니다"}
    store = get_session_store(config)

    sap_user, sap_system = "", ""
    sap = get_existing_sap(ctx.slot_id)
    if sap is not None:
        try:
            info = sap.get_session_info()
            sap_user = info.user
            sap_system = info.system_name
        except Exception:
            pass

    session = store.create_session(
        user_request=str(args.get("user_request", "")),
        ai_reasoning=str(args.get("ai_reasoning", "")),
        agent=ctx.agent_name or "",
        sap_user=sap_user,
        sap_system=sap_system,
        source=ctx.source,
    )
    return {
        "session_id": session.id,
        "status": session.status,
        "started_at": session.started_at,
    }


def _append_marker_step(
    args: dict[str, Any],
    ctx: Any,
    get_session_store: SessionStoreFactory,
    *,
    command: str,
    tool_name: str,
    default_kind: str,
    extra_arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    store = get_session_store()
    session_id = str(args.get("session_id") or getattr(ctx, "session_id", "") or "")
    if not session_id:
        return {"error": "session_id가 필요합니다"}

    text = str(args.get("text") or args.get("message") or "").strip()
    step_kind = str(args.get("step_kind") or args.get("kind") or default_kind).strip() or default_kind
    followable = bool(args.get("followable", True))
    requires_sap = bool(args.get("requires_sap", False))
    cli_invocation = str(args.get("cli_invocation") or "").strip()
    raw_argv = args.get("raw_argv") if isinstance(args.get("raw_argv"), list) else []
    raw_argv = [str(item) for item in raw_argv]
    source = str(args.get("source") or getattr(ctx, "source", "") or "manual")

    marker_args: dict[str, Any] = {
        "text": text,
        "step_kind": step_kind,
        "followable": followable,
        "requires_sap": requires_sap,
    }
    if extra_arguments:
        marker_args.update({k: v for k, v in extra_arguments.items() if v not in (None, "")})
    if cli_invocation:
        marker_args["cli_invocation"] = cli_invocation
    if raw_argv:
        marker_args["raw_argv"] = raw_argv

    step = store.add_step(
        session_id,
        command=command,
        tool_name=tool_name,
        arguments=marker_args,
        source=source,
        cli_invocation=cli_invocation,
        raw_argv=raw_argv,
        step_kind=step_kind,
        followable=followable,
        requires_sap=requires_sap,
        intent=text,
        outcome="marked",
    )
    if step is None:
        return {"error": f"실행 중인 세션을 찾을 수 없습니다: {session_id}"}
    return {
        "session_id": session_id,
        "marked": True,
        "step": step.model_dump(mode="json", exclude_none=True),
    }


def dispatch_session_mark(
    args: dict[str, Any],
    ctx: Any,
    get_session_store: SessionStoreFactory,
) -> dict[str, Any]:
    """Append an explicit followable/non-SAP marker step to a running session."""
    return _append_marker_step(
        args,
        ctx,
        get_session_store,
        command="session_mark",
        tool_name="sap_session_mark",
        default_kind="note",
    )


def dispatch_session_excel(
    args: dict[str, Any],
    ctx: Any,
    get_session_store: SessionStoreFactory,
) -> dict[str, Any]:
    """Append an Excel-related non-SAP step marker to a running session."""
    extra = {
        "excel_action": args.get("excel_action") or args.get("action"),
        "workbook": args.get("workbook"),
        "sheet": args.get("sheet"),
        "range": args.get("range") or args.get("range_ref"),
        "value": args.get("value"),
    }
    normalized = dict(args)
    normalized.setdefault("step_kind", "excel")
    normalized.setdefault("requires_sap", False)
    return _append_marker_step(
        normalized,
        ctx,
        get_session_store,
        command="session_excel",
        tool_name="sap_session_excel",
        default_kind="excel",
        extra_arguments=extra,
    )


def dispatch_session_end(
    args: dict[str, Any],
    get_session_store: SessionStoreFactory,
) -> dict[str, Any]:
    """Finish a session and attach recipe suggestion metadata when eligible."""
    from ..session_analyzer import analyze, session_to_recipe

    store = get_session_store()
    session_id = str(args["session_id"])
    outcome = str(args.get("outcome", "success"))
    summary = str(args.get("outcome_summary", ""))
    session = store.finish_session(session_id, outcome=outcome, outcome_summary=summary)
    if session is None:
        return {"error": f"세션을 찾을 수 없습니다: {session_id}"}

    payload: dict[str, Any] = {
        "session_id": session.id,
        "status": session.status,
        "outcome": session.outcome,
        "step_count": len(session.steps),
        "finished_at": session.finished_at,
    }

    try:
        analysis = analyze(session)
        if session.candidate and not session.approved:
            store._save(session)
            preview = session_to_recipe(session)
            steps_preview = preview.get("steps", [])
            payload["recipe_suggestion"] = {
                "candidate": True,
                "suggested_recipe_id": preview.get("id", ""),
                "description": preview.get("description", ""),
                "core_step_count": len(steps_preview),
                "exploration_count": analysis.get("exploration_count", 0),
                "recovery_count": analysis.get("recovery_count", 0),
                "keywords": preview.get("keywords", []),
                "instruction": (
                    "이 세션은 재사용 가치가 있는 성공 경로로 평가되었습니다. "
                    "사용자에게 레시피 저장 의사를 확인한 후 sap_session_approve로 승인하세요. "
                    "approve 시 자동으로 sap_recipe_save가 실행됩니다."
                ),
            }
    except Exception as exc:
        logging.getLogger(__name__).warning(
            "recipe_suggestion 생성 실패(session=%s): %s", session_id, exc
        )

    return payload


def dispatch_session_list(
    args: dict[str, Any],
    get_session_store: SessionStoreFactory,
) -> dict[str, Any]:
    """List stored sessions."""
    store = get_session_store()
    sessions = store.list_sessions(
        date=args.get("date"),
        status=args.get("status"),
        limit=int(args.get("limit", 20)),
    )
    return {"sessions": sessions, "count": len(sessions)}


def dispatch_session_show(
    args: dict[str, Any],
    get_session_store: SessionStoreFactory,
) -> dict[str, Any]:
    """Return a stored session payload."""
    store = get_session_store()
    session_id = str(args["session_id"])
    session = store.load(session_id)
    if session is None:
        return {"error": f"세션을 찾을 수 없습니다: {session_id}"}
    return session.model_dump(mode="json", exclude_none=True)


def dispatch_session_candidates(
    args: dict[str, Any],
    get_session_store: SessionStoreFactory,
) -> dict[str, Any]:
    """Return completed sessions that are recipe candidates and unapproved."""
    from ..session_analyzer import analyze

    store = get_session_store()
    sessions = store.list_sessions(
        status="completed",
        limit=int(args.get("limit", 50)),
    )
    candidates = []
    for s_summary in sessions:
        session = store.load(s_summary["id"])
        if session is None:
            continue
        analyze(session)
        if not session.candidate or session.approved:
            continue
        candidates.append(
            {
                "id": session.id,
                "user_request": session.user_request,
                "outcome": session.outcome,
                "step_count": len(session.steps),
                "started_at": session.started_at,
                "sap_user": session.sap_user,
            }
        )
    return {"candidates": candidates, "count": len(candidates)}


def dispatch_session_approve(
    args: dict[str, Any],
    get_session_store: SessionStoreFactory,
    get_recipe_store: RecipeStoreFactory,
    link_recipe_to_ontology: OntologyLinker,
) -> dict[str, Any]:
    """Approve a session candidate and persist it as a recipe."""
    from ..recipe import Recipe, RecipeStep as RStep
    from ..recipe import normalize_replay_success_criteria
    from ..session_analyzer import analyze, approve_session

    store = get_session_store()
    session_id = str(args["session_id"])
    approved_by = str(args.get("approved_by", "admin"))
    criteria_supplied = "replay_success_criteria" in args
    replay_success_criteria = (
        normalize_replay_success_criteria(args.get("replay_success_criteria", {}))
        if criteria_supplied
        else None
    )
    derive_success_criteria = bool(args.get("derive_replay_success_criteria", True))

    session = store.load(session_id)
    if session is None:
        return {"error": f"세션을 찾을 수 없습니다: {session_id}"}

    analyze(session)
    if not session.candidate:
        return {"error": "이 세션은 승인 후보(candidate)가 아닙니다"}
    if session.approved:
        return {"error": f"이미 승인된 세션입니다 (recipe_id: {session.recipe_id})"}

    recipe_data = approve_session(
        session,
        approved_by=approved_by,
        replay_success_criteria=replay_success_criteria,
        derive_replay_success_criteria=derive_success_criteria,
    )
    if "error" in recipe_data:
        return recipe_data

    recipe_store = get_recipe_store()
    steps = [RStep(**s) if isinstance(s, dict) else s for s in recipe_data.pop("steps", [])]
    recipe = Recipe(**recipe_data, steps=steps)
    recipe_store.save(recipe)

    store._save(session)

    linked_tcodes = link_recipe_to_ontology(
        recipe.id,
        recipe.steps,
        recipe.description,
        recipe.context,
        recipe.keywords,
    )

    return {
        "approved": True,
        "session_id": session_id,
        "recipe_id": recipe.id,
        "step_count": len(recipe.steps),
        "linked_tcodes": linked_tcodes,
        "replay_success_criteria": recipe.replay_success_criteria,
        "replay_success_criteria_source": (
            "request" if criteria_supplied
            else "session" if recipe.replay_success_criteria
            else "none"
        ),
    }


def log_session_step(
    *,
    ctx: Any,
    command: str,
    tool_name: str,
    arguments: dict[str, Any],
    outcome: str,
    get_session_store: SessionStoreFactory,
    logger: logging.Logger,
    error: str = "",
    before_state: dict[str, Any] | None = None,
    after_state: dict[str, Any] | None = None,
) -> None:
    """Append one command execution step to an active session."""
    if not ctx.session_id or command in SESSION_SKIP_COMMANDS:
        return
    try:
        store = get_session_store()
        if not store.auto_log_steps:
            return
        status_msg = ""
        if after_state and isinstance(after_state.get("status"), dict):
            status_msg = after_state["status"].get("message", "")
        store.add_step(
            ctx.session_id,
            command=command,
            tool_name=tool_name,
            arguments=arguments,
            source=getattr(ctx, "source", ""),
            cli_invocation=getattr(ctx, "cli_invocation", ""),
            raw_argv=getattr(ctx, "raw_argv", []) or [],
            step_kind="sap",
            followable=True,
            requires_sap=True,
            outcome=outcome,
            error=error,
            before_state=before_state,
            after_state=after_state,
            status_message=status_msg,
        )
    except Exception:
        logger.warning(
            "세션 단계 기록 실패: session_id=%s, command=%s",
            ctx.session_id,
            command,
            exc_info=True,
        )
