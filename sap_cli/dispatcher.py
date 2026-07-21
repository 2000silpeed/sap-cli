"""중앙 명령 dispatcher.

CLI, daemon, MCP 서버가 모두 이 경로를 타도록 만들어
권한, 감사, 실행, 후처리 문맥을 한곳에 모은다.
"""

from __future__ import annotations

import logging
import os
import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

_logger = logging.getLogger(__name__)

from .auth.policy import Policy, load_policy
from .auth.preflight import preflight_tool_call
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
from .dispatchers.execution_logging import (
    audit_authorization as _audit_authorization_impl,
    capture_transcript_state as _capture_transcript_state_impl,
    extract_transcript_state_from_result as _extract_transcript_state_from_result_impl,
    log_transcript as _log_transcript_impl,
)
from .dispatchers.events import dispatch_event_command, is_event_command
from .dispatchers.ui_maps import dispatch_ui_map_command, is_ui_map_command
from .dispatchers.planning import dispatch_planning_command, is_planning_command
from .dispatchers.jobs import dispatch_job_command, is_job_command
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
    "plan_approve",
    "plan_execute",
    "job_status",
    "job_reconcile",
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
    risk_metadata: dict[str, str] = field(default_factory=dict)


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
    cli_invocation: str = ""
    raw_argv: list[str] = field(default_factory=list)
    sensitive_values: tuple[str, ...] = ()


def _maybe_record_map_step(command: str, args: dict[str, Any], ctx: DispatchContext) -> None:
    """Fold a navigating action into the slot's active map recording, if any.

    Runs only when the operator has an active map-record session; recording is
    best-effort and never affects the command's own result.
    """

    from .ui_map.recorder import NAV_COMMANDS

    if command not in NAV_COMMANDS:
        return
    from .ui_map.runtime import try_get_active_recorder

    recorder = try_get_active_recorder(ctx.slot_id or "ses0")
    if recorder is None:
        return
    try:
        recorder.record(command, args)
    except Exception:
        pass


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
    sensitive_values = _dispatch_sensitive_values(command, args, ctx.sensitive_values)
    logging_args = _redact_sensitive_payload(args, sensitive_values)
    authz, policy = _evaluate_authorization(tool_name, args, ctx)
    _audit_authorization(policy, authz, tool_name, logging_args)

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
            arguments=logging_args,
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
            arguments=logging_args,
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
            arguments=logging_args,
            outcome="error",
            before_state=before_state,
            after_state=after_state,
            error=_redact_sensitive_text(str(exc), sensitive_values),
        )
        _log_session_step(
            ctx=ctx,
            command=command,
            tool_name=tool_name,
            arguments=logging_args,
            outcome="error",
            error=_redact_sensitive_text(str(exc), sensitive_values),
            before_state=before_state,
            after_state=after_state,
        )
        raise

    _maybe_record_map_step(command, args, ctx)

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
        arguments=logging_args,
        outcome="success",
        before_state=before_state,
        after_state=after_state,
        result=_redact_sensitive_payload(result, sensitive_values),
    )
    _log_session_step(
        ctx=ctx,
        command=command,
        tool_name=tool_name,
        arguments=logging_args,
        outcome="success",
        before_state=before_state,
        after_state=after_state,
    )
    return _redact_sensitive_payload(result, sensitive_values)


def _execute_command(
    command: str,
    args: dict[str, Any],
    ctx: DispatchContext,
    policy: Policy | None = None,
) -> dict[str, Any]:
    if command == "recipe_replay_execute":
        return _dispatch_recipe_replay_execute(args, ctx, policy)
    if is_event_command(command):
        return dispatch_event_command(command, args, ctx, get_sap=_get_sap)
    if is_job_command(command):
        return dispatch_job_command(command, args, ctx)
    if is_ui_map_command(command):
        return dispatch_ui_map_command(
            command,
            args,
            ctx,
            preflight_tcode=lambda tcode: preflight_tool_call(
                "sap_run",
                {"tcode": tcode},
                policy=policy,
                agent_name=ctx.agent_name,
                enforce_auth=ctx.enforce_auth,
            ),
        )
    if is_planning_command(command):
        return dispatch_planning_command(
            command,
            args,
            get_recipe_store=_get_recipe_store,
            policy=policy,
            agent_name=ctx.agent_name,
            enforce_auth=ctx.enforce_auth,
            ctx=ctx,
            get_sap=_get_sap,
            authorize_slot=lambda slot_id, agent: _get_pool().authorize_slot(
                slot_id, agent
            ),
        )
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
            dispatch_step=dispatch_command,
            policy=policy,
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

_REPLAY_PLACEHOLDER_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_SENSITIVE_VARIABLE_RE = re.compile(
    r"(?:password|passwd|passcode|secret|token|credential|api[_-]?key|pin|pwd)",
    re.IGNORECASE,
)
_REDACTED = "<redacted>"


def _normalize_replay_variables(raw: Any) -> dict[str, str]:
    """Normalize replay variables using the established macro payload shapes."""

    from .macro.variables import normalize_runtime_variables

    return normalize_runtime_variables(raw)


def _explicit_sensitive_variable_names(raw: Any) -> set[str]:
    """Collect variable names explicitly marked ``sensitive`` in structured input."""

    names: set[str] = set()
    if isinstance(raw, Mapping):
        if raw.get("sensitive") is True:
            name = raw.get("name") or raw.get("key") or raw.get("id") or raw.get("variable")
            if name:
                names.add(str(name))
        for key, value in raw.items():
            if isinstance(value, Mapping) and value.get("sensitive") is True:
                names.add(str(key))
            names.update(_explicit_sensitive_variable_names(value))
    elif isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, bytearray)):
        for value in raw:
            names.update(_explicit_sensitive_variable_names(value))
    return names


def _sensitive_replay_values(raw: Any) -> tuple[str, ...]:
    if raw in (None, ""):
        return ()
    try:
        variables = _normalize_replay_variables(raw)
    except (TypeError, ValueError):
        return ()
    explicit = {name.casefold() for name in _explicit_sensitive_variable_names(raw)}
    values = {
        str(value)
        for name, value in variables.items()
        if value not in (None, "")
        and (_SENSITIVE_VARIABLE_RE.search(name) or name.casefold() in explicit)
    }
    return tuple(sorted(values, key=lambda value: (-len(value), value)))


def _sensitive_plan_values(args: Mapping[str, Any]) -> tuple[str, ...]:
    from .planning.intent import parse_intent

    parsed = parse_intent(str(args.get("request") or args.get("text") or ""))
    values = {
        str(parsed.inputs[name])
        for name in parsed.sensitive_inputs
        if parsed.inputs.get(name) not in (None, "")
    }
    supplied = args.get("values") or args.get("supplied_values") or {}
    if isinstance(supplied, Mapping):
        values.update(
            str(value)
            for name, value in supplied.items()
            if value not in (None, "") and _SENSITIVE_VARIABLE_RE.search(str(name))
        )
    return tuple(sorted(values, key=lambda value: (-len(value), value)))


def _dispatch_sensitive_values(
    command: str,
    args: dict[str, Any],
    inherited: Sequence[str] = (),
) -> tuple[str, ...]:
    values = {str(value) for value in inherited if str(value)}
    if command == "recipe_replay_execute":
        values.update(_sensitive_replay_values(args.get("variables")))
    elif command == "macro_play":
        values.update(_sensitive_replay_values(args.get("variables")))
    elif command == "plan_preview":
        values.update(_sensitive_plan_values(args))
    elif command == "plan_execute":
        token = str(args.get("approval_token", "") or "")
        if token:
            values.add(token)
    elif command == "plan_approve":
        step_ids = args.get("step_ids")
        if isinstance(step_ids, (list, tuple, set)):
            values.update(str(item) for item in step_ids if str(item))
    return tuple(sorted(values, key=lambda value: (-len(value), value)))


def _redact_sensitive_text(value: str, sensitive_values: Sequence[str]) -> str:
    redacted = value
    for secret in sensitive_values:
        if secret:
            redacted = redacted.replace(secret, _REDACTED)
    return redacted


def _redact_sensitive_payload(value: Any, sensitive_values: Sequence[str]) -> Any:
    if not sensitive_values:
        return value
    if isinstance(value, str):
        return _redact_sensitive_text(value, sensitive_values)
    if isinstance(value, Mapping):
        return {
            key: _redact_sensitive_payload(item, sensitive_values)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_sensitive_payload(item, sensitive_values) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_sensitive_payload(item, sensitive_values) for item in value)
    return value


def _resolve_replay_value(
    value: Any,
    variables: Mapping[str, str],
    missing: set[str],
) -> Any:
    if isinstance(value, str):
        exact = _REPLAY_PLACEHOLDER_RE.fullmatch(value)
        if exact:
            name = exact.group(1)
            if name not in variables:
                missing.add(name)
                return value
            resolved = variables[name]
            for unresolved in _REPLAY_PLACEHOLDER_RE.findall(str(resolved)):
                missing.add(unresolved)
            return resolved

        def replace(match: re.Match[str]) -> str:
            name = match.group(1)
            if name not in variables:
                missing.add(name)
                return match.group(0)
            return str(variables[name])

        resolved = _REPLAY_PLACEHOLDER_RE.sub(replace, value)
        for unresolved in _REPLAY_PLACEHOLDER_RE.findall(resolved):
            missing.add(unresolved)
        return resolved
    if isinstance(value, list):
        return [_resolve_replay_value(item, variables, missing) for item in value]
    if isinstance(value, tuple):
        return tuple(_resolve_replay_value(item, variables, missing) for item in value)
    if isinstance(value, Mapping):
        return {
            key: _resolve_replay_value(item, variables, missing)
            for key, item in value.items()
        }
    return value


def _resolve_recipe_replay_steps(
    dry_run_plan: list[dict[str, Any]],
    raw_variables: Any,
) -> dict[str, Any]:
    """Strictly bind every placeholder before authorization or SAP access."""

    try:
        variables = _normalize_replay_variables(raw_variables)
    except (TypeError, ValueError) as exc:
        return {
            "status": "blocked",
            "steps": [],
            "issues": [{
                "code": "invalid_variables",
                "message": f"recipe variables 구조가 올바르지 않습니다: {type(exc).__name__}",
            }],
        }

    missing: set[str] = set()
    resolved = _resolve_replay_value(dry_run_plan, variables, missing)
    assert isinstance(resolved, list)
    if missing:
        return {
            "status": "blocked",
            "steps": [],
            "issues": [{
                "code": "unresolved_variables",
                "variables": sorted(missing),
                "message": "필수 recipe 변수가 누락되었거나 치환 후에도 미해결 상태입니다.",
            }],
        }
    return {"status": "ok", "steps": resolved, "issues": []}


def _dispatch_recipe_replay_execute(
    args: dict[str, Any],
    ctx: DispatchContext,
    _policy: Policy | None,
) -> dict[str, Any]:
    """Execute a recipe replay plan with automatic verification and reporting."""
    started_at = time.perf_counter()
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

    sensitive_values = _dispatch_sensitive_values(
        "recipe_replay_execute", args, ctx.sensitive_values
    )
    binding = _resolve_recipe_replay_steps(
        plan.get("dry_run_plan", []), args.get("variables", {})
    )
    if binding["status"] != "ok":
        return {
            "recipe_id": recipe_id,
            "status": "blocked",
            "stage": "variable_binding",
            "plan": plan,
            "variable_binding": binding,
            "executed_steps": [],
            "report_called": False,
            "message": "recipe 변수가 완전히 치환되지 않아 실행하지 않았습니다.",
        }

    resolved_steps = binding["steps"]
    plan["dry_run_plan"] = _redact_sensitive_payload(resolved_steps, sensitive_values)
    preflight = _preflight_recipe_replay_steps(resolved_steps, ctx)
    if preflight["status"] != "ok":
        return {
            "recipe_id": recipe_id,
            "status": "blocked",
            "stage": "preflight",
            "plan": plan,
            "preflight": _redact_sensitive_payload(preflight, sensitive_values),
            "executed_steps": [],
            "report_called": False,
            "message": "step 권한/approval/risk 검사를 통과하지 못해 실행하지 않았습니다.",
        }

    observation_scope = str(args.get("observation_scope", "final") or "final").lower()
    if observation_scope not in {"final", "status", "screen"}:
        return {
            "recipe_id": recipe_id,
            "status": "blocked",
            "stage": "preflight",
            "plan": plan,
            "executed_steps": [],
            "report_called": False,
            "message": "observation_scope는 final/status/screen 중 하나여야 합니다.",
        }

    (
        success_criteria,
        success_criteria_source,
        success_criteria_persisted,
    ) = _resolve_recipe_replay_success_criteria(recipe_id, args)

    executed_steps: list[dict[str, Any]] = []
    step_ctx = DispatchContext(
        source=ctx.source,
        agent_name=ctx.agent_name,
        enforce_auth=ctx.enforce_auth,
        include_post_action_context=ctx.include_post_action_context,
        include_screen_summary=False,
        session_id=ctx.session_id,
        slot_id=ctx.slot_id,
        sensitive_values=sensitive_values,
    )
    for step in preflight["steps"]:
        order = step["order"]
        command = step["command"]
        step_args = step["args"]
        precondition_result = _check_recipe_step_precondition(
            step.get("precondition", {}),
            step_ctx,
            order=order,
        )
        if precondition_result["status"] != "ok":
            reason = "precondition failed: " + "; ".join(
                issue["message"] for issue in precondition_result["issues"]
            )
            return _recipe_replay_failed(
                recipe_id,
                "precondition",
                plan,
                executed_steps,
                reason,
                ctx,
                auto_report=bool(args.get("auto_report", True)),
                execution_ms=_elapsed_ms(started_at),
                failure_type=_classify_recipe_replay_failure(precondition_result),
                success_criteria=success_criteria,
                success_criteria_source=success_criteria_source,
                success_criteria_persisted=success_criteria_persisted,
            )
        step_started_at = time.perf_counter()
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
                execution_ms=_elapsed_ms(started_at),
                failure_type="approval_required",
                success_criteria=success_criteria,
                success_criteria_source=success_criteria_source,
                success_criteria_persisted=success_criteria_persisted,
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
                execution_ms=_elapsed_ms(started_at),
                failure_type="permission_denied",
                success_criteria=success_criteria,
                success_criteria_source=success_criteria_source,
                success_criteria_persisted=success_criteria_persisted,
            )
        except Exception as exc:
            reason = _redact_sensitive_text(
                f"step {order} execution error: {exc}", sensitive_values
            )
            return _recipe_replay_failed(
                recipe_id,
                "execution_error",
                plan,
                executed_steps,
                reason,
                ctx,
                auto_report=bool(args.get("auto_report", True)),
                execution_ms=_elapsed_ms(started_at),
                failure_type="execution_error",
                success_criteria=success_criteria,
                success_criteria_source=success_criteria_source,
                success_criteria_persisted=success_criteria_persisted,
            )
        executed_step = {
            "order": order,
            "tool": step["tool"],
            "command": command,
            "args": _redact_sensitive_payload(step_args, sensitive_values),
            "execution_ms": _elapsed_ms(step_started_at),
            "result": _redact_sensitive_payload(result, sensitive_values),
        }
        if observation_scope in {"status", "screen"}:
            executed_step["observations"] = _collect_recipe_replay_observations(
                ctx,
                include_screen_summary=observation_scope == "screen",
            )
        executed_steps.append(executed_step)

    observations = _collect_recipe_replay_observations(ctx)
    verify = _verify_recipe_replay_observations(
        observations,
        allow_modal=bool(args.get("allow_modal", False)),
        success_criteria=success_criteria,
    )
    auto_report = bool(args.get("auto_report", True))
    execution_ms = _elapsed_ms(started_at)
    if verify["status"] != "ok":
        failure_type = _classify_recipe_replay_failure(verify)
        reason = "verify failed: " + "; ".join(issue["message"] for issue in verify["issues"])
        failure_report = (
            _dispatch_recipe_failure({"recipe_id": recipe_id, "reason": reason})
            if auto_report
            else None
        )
        replay_metrics = _record_recipe_replay_metrics(
            recipe_id,
            status="failed",
            execution_ms=execution_ms,
            observations=observations,
            failure_type=failure_type,
        )
        return {
            "recipe_id": recipe_id,
            "status": "failed",
            "stage": "verify",
            "execution_ms": execution_ms,
            "success_criteria": success_criteria,
            "success_criteria_source": success_criteria_source,
            "success_criteria_persisted": success_criteria_persisted,
            "observation_scope": observation_scope,
            "plan": plan,
            "executed_steps": executed_steps,
            "observations": observations,
            "verify": verify,
            "report_called": auto_report,
            "report": failure_report,
            "replay_metrics": replay_metrics,
        }

    success_report = _dispatch_recipe_success({"recipe_id": recipe_id}) if auto_report else None
    replay_metrics = _record_recipe_replay_metrics(
        recipe_id,
        status="success",
        execution_ms=execution_ms,
        observations=observations,
    )
    return {
        "recipe_id": recipe_id,
        "status": "success",
        "stage": "report" if auto_report else "verify",
        "execution_ms": execution_ms,
        "success_criteria": success_criteria,
        "success_criteria_source": success_criteria_source,
        "success_criteria_persisted": success_criteria_persisted,
        "observation_scope": observation_scope,
        "plan": plan,
        "executed_steps": executed_steps,
        "observations": observations,
        "verify": verify,
        "report_called": auto_report,
        "report": success_report,
        "replay_metrics": replay_metrics,
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
            issue = {
                "order": order,
                "tool": tool,
                "command": command,
                "code": "permission_denied",
                "message": authz.reason,
            }
            if authz.risk_metadata:
                issue["risk"] = authz.risk_metadata
                issue["code"] = authz.risk_metadata.get("preflight_code") or issue["code"]
            issues.append(issue)
            continue
        if authz.approval_required:
            issue = {
                "order": order,
                "tool": tool,
                "command": command,
                "code": "approval_required",
                "message": authz.approval_reason,
            }
            if authz.risk_metadata:
                issue["risk"] = authz.risk_metadata
                issue["code"] = authz.risk_metadata.get("preflight_code") or issue["code"]
            issues.append(issue)
            continue
        steps.append({
            "order": order,
            "tool": tool,
            "command": command,
            "args": step_args,
            "precondition": dict(raw_step.get("precondition", {}) or {}),
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
    execution_ms: int = 0,
    failure_type: str = "",
    success_criteria: dict[str, Any] | None = None,
    success_criteria_source: str = "",
    success_criteria_persisted: bool = False,
) -> dict[str, Any]:
    observations = _collect_recipe_replay_observations(ctx)
    failure_type = failure_type or stage
    failure_report = (
        _dispatch_recipe_failure({"recipe_id": recipe_id, "reason": reason})
        if auto_report
        else None
    )
    replay_metrics = _record_recipe_replay_metrics(
        recipe_id,
        status="failed",
        execution_ms=execution_ms,
        observations=observations,
        failure_type=failure_type,
    )
    return {
        "recipe_id": recipe_id,
        "status": "failed",
        "stage": stage,
        "execution_ms": execution_ms,
        "success_criteria": success_criteria or {},
        "success_criteria_source": success_criteria_source or "default",
        "success_criteria_persisted": success_criteria_persisted,
        "plan": plan,
        "executed_steps": executed_steps,
        "observations": observations,
        "verify": {"status": "failed", "issues": [{"code": stage, "message": reason}]},
        "report_called": auto_report,
        "report": failure_report,
        "replay_metrics": replay_metrics,
    }


def _collect_recipe_replay_observations(
    ctx: DispatchContext,
    *,
    include_screen_summary: bool = True,
) -> dict[str, Any]:
    sap = _get_sap(ctx.slot_id)
    observations: dict[str, Any] = {
        "sap_session": sap.get_session_info().model_dump(mode="json"),
        "sap_status": sap.get_status().model_dump(mode="json"),
        "sap_modal": _collect_modal(sap),
    }
    if include_screen_summary:
        try:
            observations["sap_screen_summary"] = _collect_screen_summary(sap)
        except Exception as exc:  # pragma: no cover - defensive SAP GUI path
            observations["sap_screen_summary_error"] = str(exc)
    return observations


def _normalize_recipe_success_criteria(raw: Any) -> dict[str, Any]:
    from .recipe import normalize_replay_success_criteria

    return normalize_replay_success_criteria(raw)


def _resolve_recipe_replay_success_criteria(
    recipe_id: str,
    args: dict[str, Any],
) -> tuple[dict[str, Any], str, bool]:
    """Resolve replay verification criteria from request or saved recipe metadata.

    Explicit ``success_criteria`` always wins for the current execution.  Non-empty
    explicit criteria are persisted by default so the next replay can reuse the
    same verification contract.  Passing ``success_criteria={}`` with
    ``save_success_criteria=true`` clears the saved contract.
    """
    explicit_criteria = "success_criteria" in args and args.get("success_criteria") is not None
    if explicit_criteria:
        criteria = _normalize_recipe_success_criteria(args.get("success_criteria", {}))
        should_save = bool(args.get("save_success_criteria", bool(criteria)))
        persisted = False
        if should_save:
            recipe = _get_recipe_store().update_replay_success_criteria(recipe_id, criteria)
            persisted = recipe is not None
        return criteria, "request", persisted

    recipe = _get_recipe_store().load(recipe_id)
    if recipe is None:
        return {}, "default", False

    saved = _normalize_recipe_success_criteria(recipe.replay_success_criteria)
    if saved:
        return saved, "recipe", False
    return {}, "default", False


def _actual_tcode_from_observations(observations: dict[str, Any]) -> str:
    session = observations.get("sap_session", {}) or {}
    screen = observations.get("sap_screen_summary", {}) or {}
    return str(session.get("transaction") or screen.get("transaction") or "").upper().strip()


def _actual_screen_number_from_observations(observations: dict[str, Any]) -> str:
    session = observations.get("sap_session", {}) or {}
    screen = observations.get("sap_screen_summary", {}) or {}
    return str(session.get("screen_number") or screen.get("screen_number") or "").strip()


def _screen_title_from_observations(observations: dict[str, Any]) -> str:
    screen = observations.get("sap_screen_summary", {}) or {}
    return str(screen.get("title") or "")


def _check_recipe_step_precondition(
    precondition: dict[str, Any],
    ctx: DispatchContext,
    *,
    order: int,
) -> dict[str, Any]:
    if not precondition:
        return {"status": "ok", "issues": []}

    observations = _collect_recipe_replay_observations(ctx)
    issues: list[dict[str, Any]] = []

    expected_tcode = str(
        precondition.get("tcode") or precondition.get("transaction") or ""
    ).upper().strip()
    if expected_tcode:
        actual_tcode = _actual_tcode_from_observations(observations)
        if actual_tcode != expected_tcode:
            issues.append({
                "code": "precondition_tcode_mismatch",
                "order": order,
                "expected": expected_tcode,
                "actual": actual_tcode,
                "message": (
                    f"step {order} precondition tcode 불일치: "
                    f"expected={expected_tcode}, actual={actual_tcode}"
                ),
            })

    expected_screen = str(
        precondition.get("screen_number") or precondition.get("screen") or ""
    ).strip()
    if expected_screen:
        actual_screen = _actual_screen_number_from_observations(observations)
        if actual_screen != expected_screen:
            issues.append({
                "code": "precondition_screen_mismatch",
                "order": order,
                "expected": expected_screen,
                "actual": actual_screen,
                "message": (
                    f"step {order} precondition screen 불일치: "
                    f"expected={expected_screen}, actual={actual_screen}"
                ),
            })

    expected_program = str(precondition.get("program") or "").strip()
    if expected_program:
        screen = observations.get("sap_screen_summary", {}) or {}
        actual_program = str(screen.get("program") or "").strip()
        if actual_program != expected_program:
            issues.append({
                "code": "precondition_program_mismatch",
                "order": order,
                "expected": expected_program,
                "actual": actual_program,
                "message": (
                    f"step {order} precondition program 불일치: "
                    f"expected={expected_program}, actual={actual_program}"
                ),
            })

    title_contains = str(
        precondition.get("title_contains") or precondition.get("screen_contains") or ""
    ).strip()
    if title_contains:
        actual_title = _screen_title_from_observations(observations)
        if title_contains.lower() not in actual_title.lower():
            issues.append({
                "code": "precondition_title_mismatch",
                "order": order,
                "expected": title_contains,
                "actual": actual_title,
                "message": (
                    f"step {order} precondition title 불일치: "
                    f"'{title_contains}'를 찾지 못했습니다."
                ),
            })

    if bool(precondition.get("modal_absent", False)):
        modal = observations.get("sap_modal", {}) or {}
        if modal.get("has_modal"):
            issues.append({
                "code": "precondition_modal_present",
                "order": order,
                "message": f"step {order} precondition 실패: modal dialog가 열려 있습니다.",
            })

    return {
        "status": "ok" if not issues else "failed",
        "issues": issues,
        "observations": observations,
    }


def _verify_recipe_replay_observations(
    observations: dict[str, Any],
    *,
    allow_modal: bool = False,
    success_criteria: dict[str, Any] | None = None,
) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    criteria = success_criteria or {}
    status = observations.get("sap_status", {}) or {}
    status_type = str(status.get("type", "")).upper()
    blocked_status_types = {
        str(item).upper()
        for item in criteria.get("blocked_status_types", ["E", "A"])
    }
    allowed_status_types = {
        str(item).upper()
        for item in criteria.get("allowed_status_types", [])
        if str(item).strip()
    }
    if allowed_status_types and status_type not in allowed_status_types:
        issues.append({
            "code": "sap_status_not_allowed",
            "message": (
                f"SAP status type={status_type}가 "
                f"allowed_status_types={sorted(allowed_status_types)}에 없습니다."
            ),
        })
    elif status_type in blocked_status_types:
        issues.append({
            "code": "sap_status_error",
            "message": f"SAP status type={status_type}: {status.get('message', '')}",
        })
    modal = observations.get("sap_modal", {}) or {}
    require_no_modal = bool(criteria.get("require_no_modal", True))
    if modal.get("has_modal") and require_no_modal and not allow_modal:
        issues.append({
            "code": "unexpected_modal",
            "message": f"예상치 못한 modal dialog가 남아있습니다: {modal.get('text', '')}",
        })
    if "sap_screen_summary" not in observations:
        issues.append({
            "code": "screen_summary_unavailable",
            "message": "screen_summary를 수집하지 못했습니다.",
        })
    expected_tcode = str(criteria.get("expected_tcode") or "").upper().strip()
    if expected_tcode and _actual_tcode_from_observations(observations) != expected_tcode:
        issues.append({
            "code": "expected_tcode_mismatch",
            "expected": expected_tcode,
            "actual": _actual_tcode_from_observations(observations),
            "message": (
                "최종 tcode가 기대값과 다릅니다: "
                f"expected={expected_tcode}, actual={_actual_tcode_from_observations(observations)}"
            ),
        })
    expected_screen = str(
        criteria.get("expected_screen_number") or criteria.get("expected_screen") or ""
    ).strip()
    if expected_screen and _actual_screen_number_from_observations(observations) != expected_screen:
        issues.append({
            "code": "expected_screen_mismatch",
            "expected": expected_screen,
            "actual": _actual_screen_number_from_observations(observations),
            "message": (
                "최종 screen이 기대값과 다릅니다: "
                f"expected={expected_screen}, actual={_actual_screen_number_from_observations(observations)}"
            ),
        })
    title_contains = str(criteria.get("title_contains") or "").strip()
    if title_contains and title_contains.lower() not in _screen_title_from_observations(observations).lower():
        issues.append({
            "code": "expected_title_mismatch",
            "expected": title_contains,
            "actual": _screen_title_from_observations(observations),
            "message": f"최종 화면 title에서 '{title_contains}'를 찾지 못했습니다.",
        })
    return {
        "status": "ok" if not issues else "failed",
        "issues": issues,
    }


def _elapsed_ms(started_at: float) -> int:
    return max(int((time.perf_counter() - started_at) * 1000), 0)


def _classify_recipe_replay_failure(verify: dict[str, Any]) -> str:
    issues = verify.get("issues", []) or []
    if not issues:
        return "unknown"
    first = issues[0] or {}
    return str(first.get("code") or "unknown")


def _record_recipe_replay_metrics(
    recipe_id: str,
    *,
    status: str,
    execution_ms: int,
    observations: dict[str, Any],
    failure_type: str = "",
) -> dict[str, Any] | None:
    session = observations.get("sap_session", {}) or {}
    recipe = _get_recipe_store().record_replay_execution(
        recipe_id,
        status=status,
        execution_ms=execution_ms,
        failure_type=failure_type,
        system_name=str(session.get("system_name", "")),
        client=str(session.get("client", "")),
    )
    if recipe is None:
        return None
    return {
        "replay_execution_count": recipe.replay_execution_count,
        "replay_average_execution_ms": recipe.replay_average_execution_ms,
        "replay_last_execution_ms": recipe.replay_last_execution_ms,
        "replay_last_status": recipe.replay_last_status,
        "replay_last_failure_type": recipe.replay_last_failure_type,
        "replay_failure_type_counts": recipe.replay_failure_type_counts,
        "replay_system_client_stats": recipe.replay_system_client_stats,
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
    configured_agent = _resolve_agent_name(policy, ctx.agent_name)
    preview = preflight_tool_call(
        tool_name,
        args,
        policy=policy,
        agent_name=configured_agent,
        enforce_auth=ctx.enforce_auth,
    )
    return (
        AuthorizationDecision(
            agent=preview.agent,
            permission=preview.permission,
            resource=preview.resource,
            allowed=preview.allowed,
            reason=preview.reason,
            approval_required=preview.approval_required,
            approval_reason=preview.approval_reason,
            risk_metadata=preview.risk_metadata,
        ),
        policy,
    )


def _audit_authorization(
    policy: Policy | None,
    authz: AuthorizationDecision,
    tool_name: str,
    arguments: dict[str, Any],
) -> None:
    _audit_authorization_impl(policy, authz, tool_name, arguments)


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
    _log_transcript_impl(
        transcript,
        should_log_transcript=should_log_transcript,
        ctx=ctx,
        command=command,
        tool_name=tool_name,
        authz=authz,
        arguments=arguments,
        outcome=outcome,
        get_existing_sap=_get_existing_sap,
        before_state=before_state,
        after_state=after_state,
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
    return _capture_transcript_state_impl(
        command,
        include_screen_summary=include_screen_summary,
        slot_id=slot_id,
        command_registry=COMMAND_REGISTRY,
        get_existing_sap=_get_existing_sap,
        collect_modal=_collect_modal,
        collect_screen_summary=_collect_screen_summary,
    )


def _extract_transcript_state_from_result(result: dict[str, Any]) -> dict[str, Any] | None:
    return _extract_transcript_state_from_result_impl(result)


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
