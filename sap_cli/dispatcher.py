"""중앙 명령 dispatcher.

CLI, daemon, MCP 서버가 모두 이 경로를 타도록 만들어
권한, 감사, 실행, 후처리 문맥을 한곳에 모은다.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

_logger = logging.getLogger(__name__)

from .auth.audit import AuditLogger
from .auth.checker import PermissionChecker
from .auth.policy import TOOL_PERMISSION_MAP, Permission, Policy, load_policy
from .auth.transcript import TranscriptLogger
from .command_parser import extract_tcode_from_command
from .com_wrapper import SapGuiWrapper
from .dispatchers.registry import (
    COMMAND_REGISTRY,
    COMMAND_TOOL_MAP,
    MCP_TOOL_TO_COMMAND,
    CommandDef,
)
from .dispatchers.process import (
    dispatch_process_command,
    dispatch_process_create as _dispatch_process_create_impl,
    dispatch_process_delete as _dispatch_process_delete_impl,
    dispatch_process_list as _dispatch_process_list_impl,
    dispatch_process_path as _dispatch_process_path_impl,
    dispatch_process_show as _dispatch_process_show_impl,
    dispatch_process_start as _dispatch_process_start_impl,
    dispatch_process_status as _dispatch_process_status_impl,
    dispatch_process_step as _dispatch_process_step_impl,
    is_process_command,
)
from .dispatchers.ontology import (
    build_chain as _build_chain_impl,
    dispatch_annotate as _dispatch_annotate_impl,
    dispatch_annotate_dependency as _dispatch_annotate_dependency_impl,
    dispatch_document_flow as _dispatch_document_flow_impl,
    dispatch_field_dependencies as _dispatch_field_dependencies_impl,
    dispatch_ontology_ask as _dispatch_ontology_ask_impl,
    dispatch_ontology_command,
    dispatch_ontology_query as _dispatch_ontology_query_impl,
    dispatch_ontology_query_extended as _dispatch_ontology_query_extended_impl,
    dispatch_ontology_rebuild as _dispatch_ontology_rebuild_impl,
    dispatch_ontology_relationships as _dispatch_ontology_relationships_impl,
    is_ontology_command,
)
from .dispatchers.macro import (
    dispatch_macro_command,
    dispatch_macro_control as _dispatch_macro_control_impl,
    dispatch_macro_delete as _dispatch_macro_delete_impl,
    dispatch_macro_list as _dispatch_macro_list_impl,
    dispatch_macro_play as _dispatch_macro_play_impl,
    dispatch_macro_show as _dispatch_macro_show_impl,
    is_macro_command,
    utc_to_local as _utc_to_local_impl,
)
from .dispatchers.recipe import (
    collect_recipe_hints as _collect_recipe_hints_impl,
    dispatch_recipe_chain as _dispatch_recipe_chain_impl,
    dispatch_recipe_command,
    dispatch_recipe_delete as _dispatch_recipe_delete_impl,
    dispatch_recipe_enable as _dispatch_recipe_enable_impl,
    dispatch_recipe_failure as _dispatch_recipe_failure_impl,
    dispatch_recipe_history as _dispatch_recipe_history_impl,
    dispatch_recipe_list as _dispatch_recipe_list_impl,
    dispatch_recipe_pending as _dispatch_recipe_pending_impl,
    dispatch_recipe_query as _dispatch_recipe_query_impl,
    dispatch_recipe_replay_plan as _dispatch_recipe_replay_plan_impl,
    dispatch_recipe_save as _dispatch_recipe_save_impl,
    dispatch_recipe_search as _dispatch_recipe_search_impl,
    dispatch_recipe_show as _dispatch_recipe_show_impl,
    dispatch_recipe_success as _dispatch_recipe_success_impl,
    dispatch_recipe_version_up as _dispatch_recipe_version_up_impl,
    delete_recipe_with_links as _delete_recipe_with_links_impl,
    extract_recipe_tcodes as _extract_recipe_tcodes_impl,
    get_recipe_store as _get_recipe_store_impl,
    is_recipe_command,
    link_recipe_to_ontology as _link_recipe_to_ontology_impl,
    unlink_recipe_from_ontology as _unlink_recipe_from_ontology_impl,
)
from .dispatchers.session import (
    SESSION_SKIP_COMMANDS,
    dispatch_session_approve as _dispatch_session_approve_impl,
    dispatch_session_candidates as _dispatch_session_candidates_impl,
    dispatch_session_command,
    dispatch_session_end as _dispatch_session_end_impl,
    dispatch_session_list as _dispatch_session_list_impl,
    dispatch_session_show as _dispatch_session_show_impl,
    dispatch_session_start as _dispatch_session_start_impl,
    is_session_command,
    log_session_step as _log_session_step_impl,
)
from .dispatchers.slot import (
    dispatch_slot_allocate as _dispatch_slot_allocate_impl,
    dispatch_slot_command,
    dispatch_slot_release as _dispatch_slot_release_impl,
    dispatch_slots as _dispatch_slots_impl,
    is_slot_command,
)
from .dispatchers.transcript import (
    dispatch_transcript_command,
    dispatch_transcript_plan as _dispatch_transcript_plan_impl,
    dispatch_transcript_query as _dispatch_transcript_query_impl,
    make_ontology_store as _make_ontology_store_impl,
    is_transcript_command,
)
from .dispatchers.capture import (
    dispatch_capture as _dispatch_capture_impl,
    dispatch_capture_command,
    dispatch_capture_list as _dispatch_capture_list_impl,
    is_capture_command,
)
from .dispatchers.form import (
    dispatch_fill as _dispatch_fill_impl,
    dispatch_form_command,
    dispatch_learn as _dispatch_learn_impl,
    is_form_command,
)
from .dispatchers.connection import (
    collect_modal as _collect_modal_impl,
    dispatch_connect as _dispatch_connect_impl,
    dispatch_connection_command,
    dispatch_connections as _dispatch_connections_impl,
    dispatch_modal as _dispatch_modal_impl,
    dispatch_screen as _dispatch_screen_impl,
    dispatch_session_info as _dispatch_session_info_impl,
    is_connection_command,
    search_screen as _search_screen_impl,
)
from .dispatchers.post_action import (
    POST_ACTION_COMMANDS,
    collect_post_action as _collect_post_action_impl,
    collect_screen_summary as _collect_screen_summary_impl,
    enum_value as _enum_value_impl,
    summarize_action as _summarize_action_impl,
    summarize_field as _summarize_field_impl,
    with_post_action as _with_post_action_impl,
)
from .dispatchers.sap_actions import (
    dispatch_command_text as _dispatch_command_text_impl,
    dispatch_get as _dispatch_get_impl,
    dispatch_press as _dispatch_press_impl,
    dispatch_record_step as _dispatch_record_step_impl,
    dispatch_run as _dispatch_run_impl,
    dispatch_sap_action_command,
    dispatch_set as _dispatch_set_impl,
    dispatch_status as _dispatch_status_impl,
    dispatch_table_read as _dispatch_table_read_impl,
    dispatch_table_doubleclick as _dispatch_table_doubleclick_impl,
    dispatch_table_select as _dispatch_table_select_impl,
    dispatch_tree_collapse as _dispatch_tree_collapse_impl,
    dispatch_tree_doubleclick as _dispatch_tree_doubleclick_impl,
    dispatch_tree_expand as _dispatch_tree_expand_impl,
    dispatch_tree_press as _dispatch_tree_press_impl,
    dispatch_tree_read as _dispatch_tree_read_impl,
    dispatch_tree_select as _dispatch_tree_select_impl,
    dispatch_vkey as _dispatch_vkey_impl,
    is_sap_action_command,
)
from .ontology.store import OntologyStore
from .service import (
    get_learner as _get_learner,
    get_macro_store as _get_macro_store,
    get_recorder as _get_recorder,
    get_sap as _get_sap,
    get_pool as _get_pool,
    get_session_store as _get_session_store,
    get_store as _get_store,
    execute_sap_action as _execute_sap_action,
    map_action as _map_action,
)


TRANSCRIPT_OMIT_COMMANDS = {
    "transcript_query",
    "transcript_plan",
    "session_start",
    "session_end",
    "session_list",
    "session_show",
    "session_candidates",
    "session_approve",
}


class PermissionDeniedError(Exception):
    """정책상 허용되지 않은 호출."""


class ApprovalRequiredError(Exception):
    """정책상 추가 승인이 필요한 호출."""

    def __init__(self, tool_name: str, reason: str) -> None:
        super().__init__(reason)
        self.tool_name = tool_name
        self.reason = reason

    def to_payload(self) -> dict[str, Any]:
        return {
            "approval_required": True,
            "tool": self.tool_name,
            "reason": self.reason,
        }


@dataclass(slots=True)
class AuthorizationDecision:
    agent: str
    permission: str
    resource: str
    allowed: bool
    reason: str = ""
    approval_required: bool = False
    approval_reason: str = ""


@dataclass(slots=True)
class DispatchContext:
    source: str = "cli"
    tool_name: str | None = None
    agent_name: str | None = None
    enforce_auth: bool = True
    include_post_action_context: bool = True
    include_screen_summary: bool = False
    session_id: str | None = None
    slot_id: str = "ses0"


def dispatch_command(
    command: str,
    arguments: dict[str, Any] | None = None,
    context: DispatchContext | None = None,
) -> dict[str, Any]:
    args = arguments or {}
    ctx = context or DispatchContext()

    if command not in COMMAND_REGISTRY:
        raise ValueError(f"알 수 없는 명령: {command}")

    cmd_def = COMMAND_REGISTRY[command]
    tool_name = ctx.tool_name or cmd_def.tool_name
    authz, policy = _evaluate_authorization(tool_name, args, ctx)
    _audit_authorization(policy, authz, tool_name, args)

    transcript = TranscriptLogger(policy.transcript if policy else None)
    should_log_transcript = transcript.should_log(authz.permission) and command not in TRANSCRIPT_OMIT_COMMANDS
    should_log_session = bool(ctx.session_id) and command not in SESSION_SKIP_COMMANDS
    need_state = should_log_transcript or should_log_session
    transcript_with_screen_summary = transcript.include_screen_summary or ctx.include_screen_summary
    before_state = None
    if need_state:
        before_state = _capture_transcript_state(
            command,
            include_screen_summary=transcript_with_screen_summary,
            slot_id=ctx.slot_id,
        )

    if not authz.allowed:
        _log_transcript(
            transcript,
            should_log_transcript=should_log_transcript,
            ctx=ctx,
            command=command,
            tool_name=tool_name,
            authz=authz,
            arguments=args,
            outcome="denied",
            before_state=before_state,
        )
        raise PermissionDeniedError(f"권한 없음: {authz.reason}")

    if authz.approval_required:
        _log_transcript(
            transcript,
            should_log_transcript=should_log_transcript,
            ctx=ctx,
            command=command,
            tool_name=tool_name,
            authz=authz,
            arguments=args,
            outcome="approval_required",
            before_state=before_state,
        )
        raise ApprovalRequiredError(tool_name, authz.approval_reason)

    try:
        result = _execute_command(command, args, ctx, policy)
    except Exception as exc:
        after_state = None
        if need_state:
            after_state = _capture_transcript_state(
                command,
                include_screen_summary=transcript_with_screen_summary,
                slot_id=ctx.slot_id,
            )
        _log_transcript(
            transcript,
            should_log_transcript=should_log_transcript,
            ctx=ctx,
            command=command,
            tool_name=tool_name,
            authz=authz,
            arguments=args,
            outcome="error",
            before_state=before_state,
            after_state=after_state,
            error=str(exc),
        )
        _log_session_step(
            ctx=ctx,
            command=command,
            tool_name=tool_name,
            arguments=args,
            outcome="error",
            error=str(exc),
            before_state=before_state,
            after_state=after_state,
        )
        raise

    after_state = _extract_transcript_state_from_result(result)
    if need_state and after_state is None:
        after_state = _capture_transcript_state(
            command,
            include_screen_summary=transcript_with_screen_summary,
            slot_id=ctx.slot_id,
        )
    _log_transcript(
        transcript,
        should_log_transcript=should_log_transcript,
        ctx=ctx,
        command=command,
        tool_name=tool_name,
        authz=authz,
        arguments=args,
        outcome="success",
        before_state=before_state,
        after_state=after_state,
        result=result,
    )
    _log_session_step(
        ctx=ctx,
        command=command,
        tool_name=tool_name,
        arguments=args,
        outcome="success",
        before_state=before_state,
        after_state=after_state,
    )
    return result


def _execute_command(
    command: str,
    args: dict[str, Any],
    ctx: DispatchContext,
    policy: Policy | None = None,
) -> dict[str, Any]:
    if command == "recipe_replay_execute":
        return _dispatch_recipe_replay_execute(args, ctx, policy)
    if is_connection_command(command):
        return dispatch_connection_command(
            command,
            args,
            ctx,
            get_pool=_get_connection_pool,
            get_sap=_get_sap,
            sap_wrapper_factory=SapGuiWrapper,
            with_post_action=_with_post_action,
        )
    if is_sap_action_command(command):
        return dispatch_sap_action_command(
            command,
            args,
            ctx,
            get_sap=_get_sap,
            get_recorder=_get_recorder,
            map_action=_map_action,
            execute_sap_action=_execute_sap_action,
            collect_recipe_hints=_collect_recipe_hints,
            with_post_action=_with_post_action,
        )
    if is_macro_command(command):
        return dispatch_macro_command(
            command,
            args,
            ctx,
            get_recorder=_get_recorder,
            get_macro_store=_get_macro_store,
            get_sap=_get_sap,
            with_post_action=_with_post_action,
        )
    if is_ontology_command(command):
        return dispatch_ontology_command(
            command,
            args,
            get_store=_get_store,
            get_recipe_store=_get_recipe_store,
        )
    if is_transcript_command(command):
        return dispatch_transcript_command(command, args, policy)
    if is_process_command(command):
        return dispatch_process_command(command, args, get_store=_get_store)
    if is_form_command(command):
        return dispatch_form_command(
            command,
            args,
            ctx,
            get_sap=_get_sap,
            get_learner=_get_learner,
            with_post_action=_with_post_action,
        )
    if is_capture_command(command):
        return dispatch_capture_command(command, args, ctx, get_sap=_get_sap)
    if is_recipe_command(command):
        return dispatch_recipe_command(command, args, _RECIPE_HANDLERS)
    if is_session_command(command):
        return dispatch_session_command(
            command,
            args,
            ctx,
            get_session_store=_get_session_store,
            get_recipe_store=_get_recipe_store,
            get_existing_sap=_get_existing_sap,
            link_recipe_to_ontology=_link_recipe_to_ontology,
        )
    if is_slot_command(command):
        return dispatch_slot_command(
            command,
            args,
            ctx,
            get_pool=_get_pool,
            get_sap=_get_sap,
        )
    raise ValueError(f"미구현 명령: {command}")


def resolve_mcp_call(
    tool_name: str,
    arguments: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    command = MCP_TOOL_TO_COMMAND.get(tool_name)
    if command is None:
        raise ValueError(f"Unknown tool: {tool_name}")
    args = dict(arguments or {})
    arg_map = COMMAND_REGISTRY[command].mcp_arg_map
    if arg_map:
        for mcp_key, dispatch_key in arg_map.items():
            if mcp_key in args and dispatch_key not in args:
                args[dispatch_key] = args.pop(mcp_key)
    return command, args


def _dispatch_connect(args: dict[str, Any], ctx: DispatchContext) -> dict[str, Any]:
    return _dispatch_connect_impl(args, ctx, _get_connection_pool, _with_post_action)


def _dispatch_connections(_args: dict[str, Any], _ctx: DispatchContext) -> dict[str, Any]:
    return _dispatch_connections_impl(SapGuiWrapper)


def _dispatch_session_info(_args: dict[str, Any], ctx: DispatchContext) -> dict[str, Any]:
    return _dispatch_session_info_impl(ctx, _get_sap)


def _dispatch_screen(args: dict[str, Any], ctx: DispatchContext | None = None) -> dict[str, Any]:
    return _dispatch_screen_impl(args, ctx, _get_sap)


def _dispatch_modal(_args: dict[str, Any], ctx: DispatchContext) -> dict[str, Any]:
    return _dispatch_modal_impl(ctx, _get_sap)


def _search_screen(screen: Any, pattern: str) -> dict[str, Any]:
    return _search_screen_impl(screen, pattern)


def _dispatch_record_step(
    args: dict[str, Any],
    ctx: DispatchContext,
) -> dict[str, Any]:
    return _dispatch_record_step_impl(
        args,
        ctx,
        _get_sap,
        _get_recorder,
        _map_action,
        _execute_sap_action,
        _with_post_action,
    )


def _dispatch_run(args: dict[str, Any], ctx: DispatchContext) -> dict[str, Any]:
    return _dispatch_run_impl(args, ctx, _get_sap, _collect_recipe_hints, _with_post_action)


def _dispatch_set(args: dict[str, Any], ctx: DispatchContext) -> dict[str, Any]:
    return _dispatch_set_impl(args, ctx, _get_sap, _with_post_action)


def _dispatch_get(args: dict[str, Any], ctx: DispatchContext) -> Any:
    return _dispatch_get_impl(args, ctx, _get_sap)


def _dispatch_press(args: dict[str, Any], ctx: DispatchContext) -> dict[str, Any]:
    return _dispatch_press_impl(args, ctx, _get_sap, _with_post_action)


def _dispatch_vkey(args: dict[str, Any], ctx: DispatchContext) -> dict[str, Any]:
    return _dispatch_vkey_impl(args, ctx, _get_sap, _with_post_action)


def _dispatch_status(_args: dict[str, Any], ctx: DispatchContext) -> dict[str, Any]:
    return _dispatch_status_impl(ctx, _get_sap)


def _dispatch_table_read(args: dict[str, Any], ctx: DispatchContext) -> dict[str, Any]:
    return _dispatch_table_read_impl(args, ctx, _get_sap)


def _dispatch_command_text(args: dict[str, Any], ctx: DispatchContext) -> dict[str, Any]:
    return _dispatch_command_text_impl(args, ctx, _get_sap, _with_post_action)


def _get_connection_pool() -> Any:
    from .service import get_pool

    return get_pool()


def _dispatch_table_select(args: dict[str, Any], ctx: DispatchContext) -> dict[str, Any]:
    return _dispatch_table_select_impl(args, ctx, _get_sap, _with_post_action)


def _dispatch_table_doubleclick(args: dict[str, Any], ctx: DispatchContext) -> dict[str, Any]:
    return _dispatch_table_doubleclick_impl(args, ctx, _get_sap, _with_post_action)


def _dispatch_tree_read(args: dict[str, Any], ctx: DispatchContext) -> dict[str, Any]:
    return _dispatch_tree_read_impl(args, ctx, _get_sap)


def _dispatch_tree_select(args: dict[str, Any], ctx: DispatchContext) -> dict[str, Any]:
    return _dispatch_tree_select_impl(args, ctx, _get_sap, _with_post_action)


def _dispatch_tree_doubleclick(args: dict[str, Any], ctx: DispatchContext) -> dict[str, Any]:
    return _dispatch_tree_doubleclick_impl(args, ctx, _get_sap, _with_post_action)


def _dispatch_tree_expand(args: dict[str, Any], ctx: DispatchContext) -> dict[str, Any]:
    return _dispatch_tree_expand_impl(args, ctx, _get_sap)


def _dispatch_tree_collapse(args: dict[str, Any], ctx: DispatchContext) -> dict[str, Any]:
    return _dispatch_tree_collapse_impl(args, ctx, _get_sap)


def _dispatch_tree_press(args: dict[str, Any], ctx: DispatchContext) -> dict[str, Any]:
    return _dispatch_tree_press_impl(args, ctx, _get_sap, _with_post_action)


def _dispatch_macro_control(args: dict[str, Any]) -> dict[str, Any]:
    return _dispatch_macro_control_impl(args, _get_recorder, _get_macro_store)


def _dispatch_macro_play(
    args: dict[str, Any],
    ctx: DispatchContext,
) -> dict[str, Any]:
    return _dispatch_macro_play_impl(args, ctx, _get_macro_store, _get_sap, _with_post_action)


def _dispatch_macro_list(args: dict[str, Any]) -> dict[str, Any]:
    return _dispatch_macro_list_impl(args, _get_macro_store)


def _utc_to_local(iso_str: str) -> str:
    return _utc_to_local_impl(iso_str)


def _dispatch_macro_show(args: dict[str, Any]) -> dict[str, Any]:
    return _dispatch_macro_show_impl(args, _get_macro_store)


def _dispatch_macro_delete(args: dict[str, Any]) -> dict[str, Any]:
    return _dispatch_macro_delete_impl(args, _get_macro_store)


def _dispatch_ontology_ask(args: dict[str, Any]) -> dict[str, Any]:
    return _dispatch_ontology_ask_impl(args, _get_store)


def _dispatch_ontology_query_extended(
    store: OntologyStore, query_type: str, args: dict[str, Any]
) -> dict[str, Any]:
    return _dispatch_ontology_query_extended_impl(store, query_type, args)


def _build_chain(
    dep_graph: Any, field_name: str, chain: list[str], visited: set[str],
    depth: int, max_depth: int,
) -> None:
    return _build_chain_impl(dep_graph, field_name, chain, visited, depth, max_depth)


def _dispatch_ontology_query(args: dict[str, Any]) -> dict[str, Any]:
    return _dispatch_ontology_query_impl(args, _get_store, _get_recipe_store)


def _dispatch_transcript_query(
    args: dict[str, Any],
    policy: Policy | None,
) -> dict[str, Any]:
    return _dispatch_transcript_query_impl(args, policy)


def _dispatch_transcript_plan(
    args: dict[str, Any],
    policy: Policy | None,
) -> dict[str, Any]:
    return _dispatch_transcript_plan_impl(args, policy)


def _make_ontology_store(policy: Policy | None) -> OntologyStore:
    return _make_ontology_store_impl(policy)


def _dispatch_learn(ctx: DispatchContext | None = None) -> dict[str, Any]:
    return _dispatch_learn_impl(ctx, _get_learner)


def _dispatch_annotate(args: dict[str, Any]) -> dict[str, Any]:
    return _dispatch_annotate_impl(args, _get_store)


def _dispatch_annotate_dependency(args: dict[str, Any]) -> dict[str, Any]:
    return _dispatch_annotate_dependency_impl(args, _get_store)


def _dispatch_process_list() -> dict[str, Any]:
    return _dispatch_process_list_impl(_get_store)


def _dispatch_process_show(args: dict[str, Any]) -> dict[str, Any]:
    return _dispatch_process_show_impl(args, _get_store)


def _dispatch_process_create(args: dict[str, Any]) -> dict[str, Any]:
    return _dispatch_process_create_impl(args, _get_store)


def _dispatch_process_delete(args: dict[str, Any]) -> dict[str, Any]:
    return _dispatch_process_delete_impl(args, _get_store)


def _dispatch_process_start(args: dict[str, Any]) -> dict[str, Any]:
    return _dispatch_process_start_impl(args, _get_store)


def _dispatch_process_step(args: dict[str, Any]) -> dict[str, Any]:
    return _dispatch_process_step_impl(args)


def _dispatch_process_status() -> dict[str, Any]:
    return _dispatch_process_status_impl()


def _dispatch_fill(args: dict[str, Any], ctx: DispatchContext) -> dict[str, Any]:
    return _dispatch_fill_impl(args, ctx, _get_sap, _with_post_action)


def _dispatch_ontology_relationships(args: dict[str, Any]) -> dict[str, Any]:
    return _dispatch_ontology_relationships_impl(args, _get_store)


def _dispatch_field_dependencies(args: dict[str, Any]) -> dict[str, Any]:
    return _dispatch_field_dependencies_impl(args, _get_store)


def _dispatch_document_flow(args: dict[str, Any]) -> dict[str, Any]:
    return _dispatch_document_flow_impl(args, _get_store)


def _dispatch_ontology_rebuild() -> dict[str, Any]:
    return _dispatch_ontology_rebuild_impl(_get_store)


def _dispatch_process_path(args: dict[str, Any]) -> dict[str, Any]:
    return _dispatch_process_path_impl(args, _get_store)


def _dispatch_capture(args: dict[str, Any], ctx: DispatchContext | None = None) -> dict[str, Any]:
    return _dispatch_capture_impl(args, ctx, _get_sap)


def _dispatch_capture_list(args: dict[str, Any]) -> dict[str, Any]:
    return _dispatch_capture_list_impl(args)


def _get_recipe_store():
    return _get_recipe_store_impl()


def _collect_recipe_hints(tcode: str, limit: int = 3) -> dict[str, Any] | None:
    return _collect_recipe_hints_impl(tcode, _get_recipe_store, limit)


def _extract_recipe_tcodes(recipe) -> set[str]:
    return _extract_recipe_tcodes_impl(recipe)


def _unlink_recipe_from_ontology(recipe) -> list[str]:
    return _unlink_recipe_from_ontology_impl(recipe, _get_store, _extract_recipe_tcodes)


def _delete_recipe_with_links(recipe_id: str) -> dict[str, Any]:
    return _delete_recipe_with_links_impl(
        recipe_id,
        _get_recipe_store,
        _unlink_recipe_from_ontology,
    )


def _link_recipe_to_ontology(
    recipe_id: str,
    steps: list,
    description: str = "",
    context: str = "",
    keywords: list[str] | None = None,
) -> list[str]:
    return _link_recipe_to_ontology_impl(
        recipe_id,
        steps,
        _get_store,
        description=description,
        context=context,
        keywords=keywords,
    )


def _dispatch_recipe_save(args: dict[str, Any]) -> dict[str, Any]:
    return _dispatch_recipe_save_impl(
        args,
        _get_recipe_store,
        _unlink_recipe_from_ontology,
        _link_recipe_to_ontology,
        _delete_recipe_with_links,
    )


def _dispatch_recipe_search(args: dict[str, Any]) -> dict[str, Any]:
    return _dispatch_recipe_search_impl(args, _get_recipe_store)


def _dispatch_recipe_show(args: dict[str, Any]) -> dict[str, Any]:
    return _dispatch_recipe_show_impl(args, _get_recipe_store)


def _dispatch_recipe_list() -> dict[str, Any]:
    return _dispatch_recipe_list_impl(_get_recipe_store)


def _dispatch_recipe_delete(args: dict[str, Any]) -> dict[str, Any]:
    return _dispatch_recipe_delete_impl(args, _delete_recipe_with_links)


def _dispatch_recipe_success(args: dict[str, Any]) -> dict[str, Any]:
    return _dispatch_recipe_success_impl(args, _get_recipe_store)


def _dispatch_recipe_failure(args: dict[str, Any]) -> dict[str, Any]:
    """레시피 실행 실패 보고. 임계 도달 시 자동 비활성화."""
    return _dispatch_recipe_failure_impl(args, _get_recipe_store)


def _dispatch_recipe_enable(args: dict[str, Any]) -> dict[str, Any]:
    """비활성화된 레시피를 수동 재활성화."""
    return _dispatch_recipe_enable_impl(args, _get_recipe_store)


def _dispatch_recipe_version_up(args: dict[str, Any]) -> dict[str, Any]:
    """기존 레시피의 새 버전을 저장. 기존 버전은 _archive로 이동."""
    return _dispatch_recipe_version_up_impl(
        args,
        _get_recipe_store,
        _unlink_recipe_from_ontology,
        _link_recipe_to_ontology,
    )


def _dispatch_recipe_history(args: dict[str, Any]) -> dict[str, Any]:
    """레시피의 과거 버전 목록 + 현재 활성 버전 메타."""
    return _dispatch_recipe_history_impl(args, _get_recipe_store)


def _dispatch_recipe_chain(args: dict[str, Any]) -> dict[str, Any]:
    """여러 tcode를 걸치는 복합 레시피 검색."""
    return _dispatch_recipe_chain_impl(args, _get_recipe_store)


def _dispatch_recipe_query(args: dict[str, Any]) -> dict[str, Any]:
    """tcode + 키워드 통합 레시피 조회. AI 자동 참조용."""
    return _dispatch_recipe_query_impl(args, _get_recipe_store)


def _dispatch_recipe_replay_plan(args: dict[str, Any]) -> dict[str, Any]:
    """레시피 replay 전 precheck/dry-run/verify/report 계획을 생성."""
    return _dispatch_recipe_replay_plan_impl(args, _get_recipe_store)


_ALLOWED_REPLAY_STEP_COMMANDS: frozenset[str] = frozenset(
    {
        "command",
        "fill",
        "get",
        "modal",
        "press",
        "run",
        "screen",
        "set",
        "status",
        "table",
        "table_doubleclick",
        "table_select",
        "tree",
        "tree_collapse",
        "tree_doubleclick",
        "tree_expand",
        "tree_press",
        "tree_select",
        "vkey",
    }
)


def _dispatch_recipe_replay_execute(
    args: dict[str, Any],
    ctx: DispatchContext,
    _policy: Policy | None,
) -> dict[str, Any]:
    """Execute a recipe replay plan with automatic verification and reporting."""
    recipe_id = str(args["recipe_id"])
    plan = _dispatch_recipe_replay_plan({
        "recipe_id": recipe_id,
        "current_tcode": args.get("current_tcode", ""),
        "current_screen": args.get("current_screen", ""),
        "allow_disabled": bool(args.get("allow_disabled", False)),
    })
    if plan.get("error"):
        return plan
    if not plan.get("execute", {}).get("allowed", False):
        return {
            "recipe_id": recipe_id,
            "status": "blocked",
            "stage": "precheck",
            "plan": plan,
            "executed_steps": [],
            "report_called": False,
            "message": "precheck가 blocked 상태라 recipe replay를 실행하지 않았습니다.",
        }

    preflight = _preflight_recipe_replay_steps(plan.get("dry_run_plan", []), ctx)
    if preflight["status"] != "ok":
        return {
            "recipe_id": recipe_id,
            "status": "blocked",
            "stage": "preflight",
            "plan": plan,
            "preflight": preflight,
            "executed_steps": [],
            "report_called": False,
            "message": "step 권한/approval/risk 검사를 통과하지 못해 실행하지 않았습니다.",
        }

    executed_steps: list[dict[str, Any]] = []
    step_ctx = DispatchContext(
        source=ctx.source,
        agent_name=ctx.agent_name,
        enforce_auth=ctx.enforce_auth,
        include_post_action_context=ctx.include_post_action_context,
        include_screen_summary=False,
        session_id=ctx.session_id,
        slot_id=ctx.slot_id,
    )
    for step in preflight["steps"]:
        order = step["order"]
        command = step["command"]
        step_args = step["args"]
        try:
            result = dispatch_command(command, step_args, step_ctx)
        except ApprovalRequiredError as exc:
            reason = f"step {order} approval required: {exc.reason}"
            return _recipe_replay_failed(
                recipe_id,
                "approval_required",
                plan,
                executed_steps,
                reason,
                ctx,
                auto_report=bool(args.get("auto_report", True)),
            )
        except PermissionDeniedError as exc:
            reason = f"step {order} permission denied: {exc}"
            return _recipe_replay_failed(
                recipe_id,
                "permission_denied",
                plan,
                executed_steps,
                reason,
                ctx,
                auto_report=bool(args.get("auto_report", True)),
            )
        except Exception as exc:
            reason = f"step {order} execution error: {exc}"
            return _recipe_replay_failed(
                recipe_id,
                "execution_error",
                plan,
                executed_steps,
                reason,
                ctx,
                auto_report=bool(args.get("auto_report", True)),
            )
        executed_steps.append({
            "order": order,
            "tool": step["tool"],
            "command": command,
            "args": step_args,
            "result": result,
        })

    observations = _collect_recipe_replay_observations(ctx)
    verify = _verify_recipe_replay_observations(
        observations,
        allow_modal=bool(args.get("allow_modal", False)),
    )
    auto_report = bool(args.get("auto_report", True))
    if verify["status"] != "ok":
        reason = "verify failed: " + "; ".join(issue["message"] for issue in verify["issues"])
        failure_report = (
            _dispatch_recipe_failure({"recipe_id": recipe_id, "reason": reason})
            if auto_report
            else None
        )
        return {
            "recipe_id": recipe_id,
            "status": "failed",
            "stage": "verify",
            "plan": plan,
            "executed_steps": executed_steps,
            "observations": observations,
            "verify": verify,
            "report_called": auto_report,
            "report": failure_report,
        }

    success_report = _dispatch_recipe_success({"recipe_id": recipe_id}) if auto_report else None
    return {
        "recipe_id": recipe_id,
        "status": "success",
        "stage": "report" if auto_report else "verify",
        "plan": plan,
        "executed_steps": executed_steps,
        "observations": observations,
        "verify": verify,
        "report_called": auto_report,
        "report": success_report,
    }


def _preflight_recipe_replay_steps(
    dry_run_plan: list[dict[str, Any]],
    ctx: DispatchContext,
) -> dict[str, Any]:
    steps: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    for raw_step in dry_run_plan:
        tool = str(raw_step.get("tool", ""))
        command = MCP_TOOL_TO_COMMAND.get(tool, tool if tool in COMMAND_REGISTRY else "")
        order = int(raw_step.get("order", len(steps) + 1))
        if not command:
            issues.append({
                "order": order,
                "tool": tool,
                "code": "unknown_tool",
                "message": f"알 수 없는 recipe step tool입니다: {tool}",
            })
            continue
        if command not in _ALLOWED_REPLAY_STEP_COMMANDS:
            issues.append({
                "order": order,
                "tool": tool,
                "command": command,
                "code": "disallowed_tool",
                "message": "recipe replay execute에서 허용되지 않은 step tool입니다.",
            })
            continue
        step_args = dict(raw_step.get("args", {}) or {})
        tool_name = COMMAND_REGISTRY[command].tool_name
        authz, _ = _evaluate_authorization(tool_name, step_args, ctx)
        if not authz.allowed:
            issues.append({
                "order": order,
                "tool": tool,
                "command": command,
                "code": "permission_denied",
                "message": authz.reason,
            })
            continue
        if authz.approval_required:
            issues.append({
                "order": order,
                "tool": tool,
                "command": command,
                "code": "approval_required",
                "message": authz.approval_reason,
            })
            continue
        steps.append({
            "order": order,
            "tool": tool,
            "command": command,
            "args": step_args,
        })
    return {
        "status": "ok" if not issues else "blocked",
        "step_count": len(dry_run_plan),
        "approved_step_count": len(steps),
        "steps": steps,
        "issues": issues,
    }


def _recipe_replay_failed(
    recipe_id: str,
    stage: str,
    plan: dict[str, Any],
    executed_steps: list[dict[str, Any]],
    reason: str,
    ctx: DispatchContext,
    *,
    auto_report: bool,
) -> dict[str, Any]:
    observations = _collect_recipe_replay_observations(ctx)
    failure_report = (
        _dispatch_recipe_failure({"recipe_id": recipe_id, "reason": reason})
        if auto_report
        else None
    )
    return {
        "recipe_id": recipe_id,
        "status": "failed",
        "stage": stage,
        "plan": plan,
        "executed_steps": executed_steps,
        "observations": observations,
        "verify": {"status": "failed", "issues": [{"code": stage, "message": reason}]},
        "report_called": auto_report,
        "report": failure_report,
    }


def _collect_recipe_replay_observations(ctx: DispatchContext) -> dict[str, Any]:
    sap = _get_sap(ctx.slot_id)
    observations: dict[str, Any] = {
        "sap_status": sap.get_status().model_dump(mode="json"),
        "sap_modal": _collect_modal(sap),
    }
    try:
        observations["sap_screen_summary"] = _collect_screen_summary(sap)
    except Exception as exc:  # pragma: no cover - defensive SAP GUI path
        observations["sap_screen_summary_error"] = str(exc)
    return observations


def _verify_recipe_replay_observations(
    observations: dict[str, Any],
    *,
    allow_modal: bool = False,
) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    status = observations.get("sap_status", {}) or {}
    status_type = str(status.get("type", "")).upper()
    if status_type in {"E", "A"}:
        issues.append({
            "code": "sap_status_error",
            "message": f"SAP status type={status_type}: {status.get('message', '')}",
        })
    modal = observations.get("sap_modal", {}) or {}
    if modal.get("has_modal") and not allow_modal:
        issues.append({
            "code": "unexpected_modal",
            "message": f"예상치 못한 modal dialog가 남아있습니다: {modal.get('text', '')}",
        })
    if "sap_screen_summary" not in observations:
        issues.append({
            "code": "screen_summary_unavailable",
            "message": "screen_summary를 수집하지 못했습니다.",
        })
    return {
        "status": "ok" if not issues else "failed",
        "issues": issues,
    }


def _dispatch_recipe_pending(args: dict[str, Any]) -> dict[str, Any]:
    """미승인 candidate 세션 요약. 자동 제안된 레시피 후보를 한눈에 보여준다."""
    return _dispatch_recipe_pending_impl(args, _get_session_store)


_RECIPE_HANDLERS = {
    "recipe_save": _dispatch_recipe_save,
    "recipe_search": _dispatch_recipe_search,
    "recipe_show": _dispatch_recipe_show,
    "recipe_list": lambda _args: _dispatch_recipe_list(),
    "recipe_delete": _dispatch_recipe_delete,
    "recipe_success": _dispatch_recipe_success,
    "recipe_query": _dispatch_recipe_query,
    "recipe_pending": _dispatch_recipe_pending,
    "recipe_failure": _dispatch_recipe_failure,
    "recipe_enable": _dispatch_recipe_enable,
    "recipe_version_up": _dispatch_recipe_version_up,
    "recipe_history": _dispatch_recipe_history,
    "recipe_chain": _dispatch_recipe_chain,
    "recipe_replay_plan": _dispatch_recipe_replay_plan,
}


def _dispatch_session_start(args: dict[str, Any], ctx: DispatchContext) -> dict[str, Any]:
    return _dispatch_session_start_impl(args, ctx, _get_session_store, _get_existing_sap)


def _dispatch_session_end(args: dict[str, Any]) -> dict[str, Any]:
    return _dispatch_session_end_impl(args, _get_session_store)


def _dispatch_session_list(args: dict[str, Any]) -> dict[str, Any]:
    return _dispatch_session_list_impl(args, _get_session_store)


def _dispatch_session_show(args: dict[str, Any]) -> dict[str, Any]:
    return _dispatch_session_show_impl(args, _get_session_store)


def _dispatch_session_candidates(args: dict[str, Any]) -> dict[str, Any]:
    return _dispatch_session_candidates_impl(args, _get_session_store)


def _dispatch_session_approve(args: dict[str, Any]) -> dict[str, Any]:
    return _dispatch_session_approve_impl(
        args,
        _get_session_store,
        _get_recipe_store,
        _link_recipe_to_ontology,
    )


def _evaluate_authorization(
    tool_name: str,
    args: dict[str, Any],
    ctx: DispatchContext,
) -> tuple[AuthorizationDecision, Policy | None]:
    policy = load_policy()
    checker = PermissionChecker(policy)
    configured_agent = _resolve_agent_name(policy, ctx.agent_name)
    if configured_agent:
        checker.set_agent(configured_agent)
    check = checker.check_tool(tool_name, args)
    permission = TOOL_PERMISSION_MAP.get(tool_name, "")
    resource = checker.resolve_resource(tool_name, args)
    effective_agent = checker.agent_name or configured_agent or "anonymous"

    if not ctx.enforce_auth:
        return (
            AuthorizationDecision(
                agent=effective_agent,
                permission=permission.value if permission else "",
                resource=resource,
                allowed=True,
                reason="auth_disabled",
            ),
            policy,
        )

    if not check:
        return (
            AuthorizationDecision(
                agent=effective_agent,
                permission=permission.value if permission else "",
                resource=resource,
                allowed=False,
                reason=check.reason,
            ),
            policy,
        )

    approval_reason = checker.requires_approval(tool_name, args)
    if approval_reason:
        return (
            AuthorizationDecision(
                agent=effective_agent,
                permission=permission.value if permission else "",
                resource=resource,
                allowed=True,
                reason=check.reason,
                approval_required=True,
                approval_reason=approval_reason,
            ),
            policy,
        )
    return (
        AuthorizationDecision(
            agent=effective_agent,
            permission=permission.value if permission else "",
            resource=resource,
            allowed=True,
            reason=check.reason,
        ),
        policy,
    )


def _audit_authorization(
    policy: Policy | None,
    authz: AuthorizationDecision,
    tool_name: str,
    arguments: dict[str, Any],
) -> None:
    auditor = AuditLogger(policy.audit if policy else None)
    if not authz.allowed:
        auditor.log(
            agent=authz.agent,
            tool_name=tool_name,
            arguments=arguments,
            allowed=False,
            reason=authz.reason,
            permission=authz.permission,
            resource=authz.resource,
        )
        return
    if authz.approval_required:
        auditor.log(
            agent=authz.agent,
            tool_name=tool_name,
            arguments=arguments,
            allowed=False,
            reason=f"approval_required: {authz.approval_reason}",
            permission=authz.permission,
            resource=authz.resource,
        )
        return
    auditor.log(
        agent=authz.agent,
        tool_name=tool_name,
        arguments=arguments,
        allowed=True,
        reason=authz.reason,
        permission=authz.permission,
        resource=authz.resource,
    )


def _resolve_agent_name(policy: Any, explicit_agent_name: str | None) -> str | None:
    env_agent = os.getenv("SAP_CLI_AGENT", "").strip() or None
    candidate = explicit_agent_name or env_agent
    if not policy or not candidate:
        return None
    if policy.get_agent(candidate):
        return candidate
    return None


def _log_transcript(
    transcript: TranscriptLogger,
    *,
    should_log_transcript: bool,
    ctx: DispatchContext,
    command: str,
    tool_name: str,
    authz: AuthorizationDecision,
    arguments: dict[str, Any],
    outcome: str,
    before_state: dict[str, Any] | None = None,
    after_state: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
    error: str = "",
) -> None:
    if not should_log_transcript:
        return

    def _has_session(state: dict[str, Any] | None) -> bool:
        return bool(state and isinstance(state.get("session"), dict))

    if not _has_session(before_state) and not _has_session(after_state):
        sap = _get_existing_sap(ctx.slot_id)
        if sap is not None:
            try:
                session = sap.get_session_info().model_dump(mode="json")
                if before_state is None:
                    before_state = {"session": session}
                else:
                    before_state["session"] = session
            except Exception:
                pass

    reason = authz.approval_reason if authz.approval_required else authz.reason
    transcript.log(
        source=ctx.source,
        command=command,
        tool_name=tool_name,
        agent=authz.agent,
        arguments=arguments,
        permission=authz.permission,
        resource=authz.resource,
        allowed=authz.allowed,
        approval_required=authz.approval_required,
        reason=reason,
        outcome=outcome,
        before=before_state,
        after=after_state,
        result=result,
        error=error,
    )


def _log_session_step(
    *,
    ctx: DispatchContext,
    command: str,
    tool_name: str,
    arguments: dict[str, Any],
    outcome: str,
    error: str = "",
    before_state: dict[str, Any] | None = None,
    after_state: dict[str, Any] | None = None,
) -> None:
    _log_session_step_impl(
        ctx=ctx,
        command=command,
        tool_name=tool_name,
        arguments=arguments,
        outcome=outcome,
        get_session_store=_get_session_store,
        logger=_logger,
        error=error,
        before_state=before_state,
        after_state=after_state,
    )


def _capture_transcript_state(
    command: str,
    *,
    include_screen_summary: bool,
    slot_id: str = "ses0",
) -> dict[str, Any] | None:
    if command not in COMMAND_REGISTRY:
        return None
    if command in {"ontology_query", "ontology_ask", "macro_control", "macro_list", "macro_show", "macro_delete", "transcript_query", "transcript_plan"}:
        return None

    sap = _get_existing_sap(slot_id)
    if sap is None:
        return None

    try:
        state = {
            "session": sap.get_session_info().model_dump(mode="json"),
            "status": sap.get_status().model_dump(mode="json"),
            "modal": _collect_modal(sap),
        }
        if include_screen_summary:
            state["screen_summary"] = _collect_screen_summary(sap)
        return state
    except Exception as exc:  # pragma: no cover - defensive path
        return {"capture_error": str(exc)}


def _extract_transcript_state_from_result(result: dict[str, Any]) -> dict[str, Any] | None:
    post_action = result.get("post_action")
    if not isinstance(post_action, dict):
        return None

    state: dict[str, Any] = {}
    for key in ("session", "status", "modal", "screen_summary"):
        value = post_action.get(key)
        if value is not None:
            state[key] = value
    return state or None


def _get_existing_sap(slot_id: str = "ses0") -> SapGuiWrapper | None:
    from .service import get_pool

    pool = get_pool()
    wrapper = pool.get(slot_id)
    if wrapper is None:
        return None
    try:
        return wrapper if wrapper.is_connected else None
    except Exception:
        return wrapper


def _with_post_action(
    payload: dict[str, Any],
    sap: SapGuiWrapper,
    command: str,
    ctx: DispatchContext,
) -> dict[str, Any]:
    return _with_post_action_impl(
        payload,
        sap,
        command,
        ctx,
        POST_ACTION_COMMANDS,
        _collect_post_action,
    )


def _collect_post_action(
    sap: SapGuiWrapper,
    *,
    include_screen_summary: bool = False,
) -> dict[str, Any]:
    return _collect_post_action_impl(
        sap,
        include_screen_summary=include_screen_summary,
        collect_modal=_collect_modal,
        collect_screen_summary=_collect_screen_summary,
    )


def _collect_screen_summary(sap: SapGuiWrapper) -> dict[str, Any]:
    return _collect_screen_summary_impl(sap)


def _summarize_field(field: Any) -> dict[str, Any]:
    return _summarize_field_impl(field)


def _summarize_action(action: Any) -> dict[str, Any]:
    return _summarize_action_impl(action)


def _enum_value(value: Any) -> Any:
    return _enum_value_impl(value)


def _collect_modal(sap: SapGuiWrapper) -> dict[str, Any]:
    return _collect_modal_impl(sap)


# ---------------------------------------------------------------------------
# Session Pool dispatch
# ---------------------------------------------------------------------------

def _dispatch_slots() -> dict[str, Any]:
    return _dispatch_slots_impl(_get_pool)


def _dispatch_slot_allocate(args: dict[str, Any], ctx: DispatchContext) -> dict[str, Any]:
    return _dispatch_slot_allocate_impl(args, ctx, _get_pool, _get_sap)


def _dispatch_slot_release(args: dict[str, Any]) -> dict[str, Any]:
    return _dispatch_slot_release_impl(args, _get_pool)
