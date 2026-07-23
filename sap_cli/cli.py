"""SAP GUI Scripting CLI - AI 에이전트를 위한 SAP 자동화 도구.

Usage:
    sap connect                          # SAP GUI에 연결
    sap session                          # 세션 정보 조회
    sap screen                           # 현재 화면 구조 (JSON)
    sap screen --fields-only             # 입력 가능한 필드만
    sap screen --actions-only            # 가능한 액션만
    sap run MM03                         # 트랜잭션 실행
    sap set "wnd[0]/usr/ctxtFIELD" "값"  # 필드 값 설정
    sap get "wnd[0]/usr/ctxtFIELD"       # 필드 값 읽기
    sap press "wnd[0]/tbar[0]/btn[0]"    # 버튼 클릭
    sap vkey 0                           # VKey 전송 (0=Enter)
    sap status                           # 상태바 메시지
    sap table "wnd[0]/usr/tblCONTROL"    # 테이블 읽기
    sap ontology MM03                    # 온톨로지 조회
    sap shell                            # 대화형 셸 (REPL)
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
from pathlib import Path
from typing import Any, Optional

import typer
from rich.console import Console

from .auth.checker import PermissionChecker
from .auth.policy import TOOL_PERMISSION_MAP, Policy, load_policy
from .dispatcher import (
    ApprovalRequiredError,
    DispatchContext,
    PermissionDeniedError,
    dispatch_command,
    _resolve_agent_name,
)
from .storage import atomic_write_json
from .ui_action_catalog import get_ui_action_defs

app = typer.Typer(
    name="sap",
    help="SAP GUI Scripting CLI for AI Agents",
    no_args_is_help=True,
)
console = Console()
auth_app = typer.Typer(help="ReBAC 정책/도구 노출 진단")
app.add_typer(auth_app, name="auth")

# 글로벌 슬롯 ID — @app.callback()에서 설정
_global_slot_id: str = "ses0"
_ACTIVE_CLI_SESSION_PATH = Path.home() / ".sap_cli" / "active_cli_session.json"


@app.callback()
def _main_callback(
    slot: str = typer.Option("ses0", "--slot", help="세션 슬롯 ID (병렬 작업용, 모든 명령에 적용)"),
) -> None:
    global _global_slot_id
    _global_slot_id = slot


def _parse_vars(raw: str) -> Any:
    """다양한 형식의 --vars 값을 dict로 파싱.

    지원: '{"k":"v"}', "[{'name':'k','value':'v'}]", "{'k':'v'}",
    "{k:v}", "k:v,k2:v2", "k=v,k2=v2"
    값이 dict인 경우도 허용: '{"k": {"value": "v", "desc": "설명"}}'
    """
    # 1) 정상 JSON
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    # 2) 작은따옴표 JSON
    try:
        return json.loads(raw.replace("'", '"'))
    except json.JSONDecodeError:
        pass
    # 3) 따옴표 없는 {k:v, k2:v2} 또는 k:v,k2:v2
    stripped = raw.strip().strip("{}")
    pairs = re.split(r"\s*,\s*", stripped)
    result: dict[str, str] = {}
    for pair in pairs:
        sep = "=" if "=" in pair else ":" if ":" in pair else ""
        if not sep:
            continue
        k, v = pair.split(sep, 1)
        result[k.strip().strip("'\"")] = v.strip().strip("'\"")
    if not result:
        raise ValueError(f"변수 파싱 실패: {raw}")
    return result


def _merge_var_assignments(parsed: Any, assignments: list[str] | None) -> Any:
    """Merge repeated ``--var KEY=VALUE`` options into a parsed vars payload."""
    if not assignments:
        return parsed
    merged: dict[str, Any] = {}
    if isinstance(parsed, dict):
        merged.update(parsed)
    elif parsed not in (None, ""):
        merged["variables"] = parsed
    for assignment in assignments:
        sep = "=" if "=" in assignment else ":" if ":" in assignment else ""
        if not sep:
            raise ValueError(f"--var 형식 오류: {assignment} (예: MATNR=200-200)")
        key, value = assignment.split(sep, 1)
        merged[key.strip().strip("'\"")] = value.strip().strip("'\"")
    return merged


def _load_json_argument(raw: str, *, label: str) -> Any:
    """JSON 문자열 또는 @파일 경로를 파싱한다.

    Recipe version-up/replay CLI는 steps나 success criteria처럼 긴 JSON을 자주
    받으므로, 직접 JSON 문자열과 ``@path/to/file.json`` 입력을 모두 허용한다.
    """
    payload = raw
    if raw.startswith("@"):
        payload = Path(raw[1:]).expanduser().read_text(encoding="utf-8")
    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise typer.BadParameter(f"{label} JSON 파싱 실패: {exc}") from exc


def _output_json(data: dict | list) -> None:
    """JSON 출력 (AI 파싱 최적화)."""
    print(json.dumps(data, ensure_ascii=False, indent=2))


def _handle_error(e: Exception) -> None:
    result = {"error": True, "message": str(e)}
    _output_json(result)
    raise typer.Exit(1)


def _handle_approval_required(e: ApprovalRequiredError) -> None:
    _output_json(e.to_payload())
    raise typer.Exit(1)


def _cli_invocation() -> str:
    """Return the exact CLI command shape users expect to replay in a shell."""
    return shlex.join(["sap", *sys.argv[1:]])


def _read_active_cli_session_id() -> str | None:
    env_session_id = (os.getenv("SAP_CLI_SESSION_ID") or "").strip()
    if env_session_id:
        return env_session_id
    try:
        data = json.loads(_ACTIVE_CLI_SESSION_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    session_id = str(data.get("session_id") or "").strip()
    return session_id or None


def _write_active_cli_session_id(session_id: str) -> None:
    atomic_write_json(_ACTIVE_CLI_SESSION_PATH, {"session_id": session_id}, mode=0o644)


def _clear_active_cli_session_id(session_id: str | None = None) -> bool:
    active = _read_active_cli_session_id()
    if session_id and active and session_id != active:
        return False
    try:
        _ACTIVE_CLI_SESSION_PATH.unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return False


def _cli_context(slot_id: str = "ses0") -> DispatchContext:
    session_id = _read_active_cli_session_id()
    return DispatchContext(
        source="cli",
        slot_id=slot_id,
        session_id=session_id,
        cli_invocation=_cli_invocation(),
        raw_argv=list(sys.argv[1:]),
    )


def _daemon_context(slot_id: str = "ses0") -> dict[str, Any]:
    ctx: dict[str, Any] = {
        "source": "cli",
        "slot_id": slot_id,
        "cli_invocation": _cli_invocation(),
        "raw_argv": list(sys.argv[1:]),
    }
    session_id = _read_active_cli_session_id()
    if session_id:
        ctx["session_id"] = session_id
    return ctx


def _dispatch_local(cmd: str, args: dict, slot_id: str = "ses0") -> dict:
    try:
        return dispatch_command(cmd, args, _cli_context(slot_id))
    except ApprovalRequiredError as e:
        _handle_approval_required(e)
    except (PermissionDeniedError, ValueError, RuntimeError) as e:
        _handle_error(e)
    except Exception as e:
        _handle_error(e)
    raise typer.Exit(1)


def _json_arg(raw: str, *, label: str = "JSON") -> str:
    """`@경로`/`@-` 참조를 실제 JSON 텍스트로 바꾼다.

    LLM이 PowerShell에서 CLI를 호출할 때, 큰따옴표가 든 JSON 리터럴을 명령줄에
    직접 박으면 셸의 네이티브 인자 인용 규칙에 걸려 깨진다. JSON을 파일에 써 두고
    (셸이 아닌 파일 도구로 작성) `@경로`로 넘기면 어느 셸에서도 인용 문제가
    사라진다. `@-`는 표준입력에서 읽는다. JSON 값은 `@`로 시작할 수 없으므로
    (항상 `{`,`[`,`"`,숫자 등) 이 접두사는 모호하지 않다.
    """
    if not isinstance(raw, str) or not raw.startswith("@"):
        return raw
    ref = raw[1:]
    if ref == "-":
        return sys.stdin.read()
    try:
        with open(os.path.expanduser(ref), encoding="utf-8") as fh:
            return fh.read()
    except OSError as exc:
        raise typer.BadParameter(f"{label} 파일을 읽을 수 없습니다: {ref} ({exc})") from exc


def _run_command(cmd: str, args: dict, *, allow_daemon: bool = False, slot_id: str | None = None) -> None:
    effective_slot = slot_id or _global_slot_id
    if allow_daemon and _use_daemon_default() and _try_daemon(cmd, args, slot_id=effective_slot):
        return
    if cmd in {
        "plan_preview",
        "plan_approve",
        "plan_execute",
        "job_status",
        "job_reconcile",
        "map_job_cancel",
        "map_augment_start",
        "map_traverse_start",
        "map_traverse_resume",
    }:
        _handle_error(
            RuntimeError(
                "plan-execute는 persistent daemon 전용입니다. daemon을 시작하거나 재시작해 주세요."
                if cmd == "plan_execute"
                else "map-augment-start는 persistent daemon 전용입니다. daemon을 시작하거나 재시작해 주세요."
                if cmd == "map_augment_start"
                else f"{cmd.replace('_', '-')}는 persistent daemon 전용입니다. daemon을 시작하거나 재시작해 주세요."
            )
        )
    _output_json(_dispatch_local(cmd, args, slot_id=effective_slot))


def _use_daemon_default() -> bool:
    """Use daemon by default unless SAP_CLI_USE_DAEMON=0/false/no/off."""
    raw = os.getenv("SAP_CLI_USE_DAEMON", "1").strip().lower()
    return raw not in {"0", "false", "no", "off", "direct"}


def _try_daemon(cmd: str, args: dict, slot_id: str = "ses0") -> bool:
    """데몬으로 명령 전송 시도. 성공하면 결과 출력 후 True 반환."""
    from . import daemon
    timeout_for = getattr(daemon, "command_timeout", lambda _cmd: 60.0)
    send_kwargs = {"context": _daemon_context(slot_id)}
    if cmd == "plan_execute":
        send_kwargs["timeout"] = timeout_for(cmd)
    # Confirm a live daemon with a cheap ping before sending the real command.
    # This avoids blocking on the command timeout when the port is held but
    # unresponsive (mid-init or wedged), and the ping — unlike a real command —
    # is side-effect free, so abandoning it can never double-apply anything.
    autostart = os.getenv("SAP_CLI_DAEMON_AUTOSTART", "1").strip().lower() not in {
        "0", "false", "no", "off"
    }
    if not daemon.ensure_daemon(autostart=autostart):
        return False
    result = daemon.send_command(cmd, args, **send_kwargs)
    if result is None:
        return False
    if result["ok"]:
        _output_json(result["data"])
    elif result.get("approval_required"):
        _output_json({
            "approval_required": True,
            "tool": result.get("tool"),
            "reason": result.get("reason", "approval required"),
        })
        raise typer.Exit(1)
    else:
        if _is_unknown_daemon_command_error(str(result.get("error", ""))):
            # A long-running daemon may be older than the CLI process after
            # git pull / editable reinstall. Fall back to local dispatch so
            # newly added commands such as inspect can still run.
            return False
        _output_json({"error": True, "message": result["error"]})
        raise typer.Exit(1)
    return True


def _is_unknown_daemon_command_error(message: str) -> bool:
    normalized = message.lower()
    return (
        "unknown command" in normalized
        or "알수 없는 명령" in message
        or "알 수 없는 명령" in message
    )


# -------------------------------------------------------------------
# Commands
# -------------------------------------------------------------------

@app.command()
def connect(
    connection: int = typer.Option(0, "--connection", "-c", help="연결 인덱스"),
    session: int = typer.Option(0, "--session", "-s", help="세션 인덱스"),
    system: str = typer.Option(None, "--system", help="시스템 ID(SID)로 연결 자동 탐색"),
) -> None:
    """실행 중인 SAP GUI에 연결."""
    args: dict = {"connection": connection, "session": session}
    if system:
        args["system"] = system
    _run_command("connect", args, allow_daemon=True)


@app.command("connections")
def list_connections() -> None:
    """모든 활성 SAP 연결 목록 조회."""
    _run_command("connections", {}, allow_daemon=True)


@app.command("slots")
def list_slots() -> None:
    """활성 세션 슬롯 목록 조회."""
    _run_command("slots", {}, allow_daemon=True)


@app.command("session")
def session_info() -> None:
    """현재 세션 정보 조회."""
    _run_command("session", {}, allow_daemon=True)


@app.command()
def screen(
    fields_only: bool = typer.Option(False, "--fields-only", "-f", help="입력 가능 필드만"),
    actions_only: bool = typer.Option(False, "--actions-only", "-a", help="가능한 액션만"),
    search: str = typer.Option(None, "--search", "-s", help="필드/버튼/테이블을 패턴으로 검색 (부분 일치)"),
) -> None:
    """현재 화면의 UI 구조를 JSON으로 출력."""
    _run_command(
        "screen",
        {"fields_only": fields_only, "actions_only": actions_only, "search": search},
        allow_daemon=True,
    )


@app.command()
def run(
    tcode: str = typer.Argument(help="트랜잭션 코드 (예: MM03, VA01, SA38)"),
    report: Optional[str] = typer.Option(
        None,
        "--report",
        "-r",
        help="SA38/SE38 등에서 입력할 ABAP report/program 이름",
    ),
    program: Optional[str] = typer.Option(
        None,
        "--program",
        help="--report의 별칭. --report가 있으면 --report가 우선",
    ),
    execute_report: bool = typer.Option(
        False,
        "--execute-report",
        help="report/program 입력 후 Execute(F8)를 전송",
    ),
    report_field_id: str = typer.Option(
        "wnd[0]/usr/ctxtRS38M-PROGRAMM",
        "--report-field-id",
        help="report/program을 입력할 SAP GUI 필드 ID",
    ),
) -> None:
    """트랜잭션 실행. report/program 지정 시 ABAP report 실행 경로를 준비."""
    params: dict[str, Any] = {"tcode": tcode}
    report_name = report or program
    if report_name:
        params["report"] = report_name
        params["report_field_id"] = report_field_id
        params["execute_report"] = execute_report
    _run_command("run", params, allow_daemon=True)


@app.command("set")
def set_field(
    element_id: str = typer.Argument(help="필드/콤보박스 ID (예: wnd[0]/usr/ctxtRMMG1-MATNR, wnd[0]/usr/cmbLFART)"),
    value: str = typer.Argument(help="설정할 값 (콤보박스는 key 또는 표시값)"),
) -> None:
    """필드 또는 콤보박스에 값 설정."""
    _run_command(
        "set",
        {"element_id": element_id, "value": value},
        allow_daemon=True,
    )


@app.command("inspect")
def inspect_element(
    element_id: str = typer.Argument(help="검사할 SAP GUI element ID"),
) -> None:
    """SAP GUI 요소의 Type/SubType/capability/entries를 live로 검사."""
    _run_command("inspect", {"element_id": element_id}, allow_daemon=True)


@app.command("combo-entries")
def combo_entries(
    element_id: str = typer.Argument(help="조회할 GuiComboBox element ID"),
) -> None:
    """콤보박스 선택 가능 entries(key/value)를 조회."""
    _run_command("combo_entries", {"element_id": element_id}, allow_daemon=True)


@app.command("scripting-query")
def scripting_query(
    component_type: str = typer.Option("", "--type", "-t", help="SAP GUI Scripting Type (예: GuiComboBox)"),
    intent: str = typer.Option("", "--intent", "-i", help="의도/action (예: set, select_node)"),
    node_id: str = typer.Option("", "--node-id", "-n", help="graph node id (예: object:GuiComboBox)"),
    depth: int = typer.Option(1, "--depth", "-d", help="subgraph 확장 깊이"),
    coverage: bool = typer.Option(False, "--coverage", help="공식 API 대비 coverage matrix 요약"),
    include_gaps: bool = typer.Option(True, "--include-gaps/--no-gaps", help="coverage gap 샘플 포함"),
    gap_limit: int = typer.Option(50, "--gap-limit", min=0, help="종류별 coverage gap 최대 출력 수"),
) -> None:
    """SAP GUI Scripting API 그래프 온톨로지 조회."""
    _run_command(
        "scripting_query",
        {
            "component_type": component_type,
            "intent": intent,
            "node_id": node_id,
            "depth": depth,
            "coverage": coverage,
            "include_gaps": include_gaps,
            "gap_limit": gap_limit,
        },
        allow_daemon=False,
    )


@app.command("api-object")
def api_object_action(
    scope: str = typer.Argument(
        help=(
            "sap_gui_auto, rot_wrapper, application, connection, session, "
            "session_info, active_window 또는 handle:<id>"
        )
    ),
    action: str = typer.Argument(help="call_method, get_property, set_property"),
    member_name: str = typer.Argument(help="공식 SAP GUI Scripting member 이름"),
    args_json: str = typer.Option(
        "", "--args-json", help="call_method ByVal 입력 JSON 배열(ByRef/out은 자동)"
    ),
    property_value: str | None = typer.Option(None, "--property-value", help="set_property 값"),
) -> None:
    """SAP GUI Scripting 객체 계층의 공식 API member를 실행."""
    params: dict[str, Any] = {
        "scope": scope,
        "action": action,
        "member_name": member_name,
    }
    if args_json:
        params["args_json"] = args_json
    if property_value is not None:
        params["property_value"] = property_value
    _run_command("api_object_action", params, allow_daemon=True)


@app.command("map-scan")
def map_scan() -> None:
    """현재 SAP 창 전체를 계층형 UiState 지도로 수집."""
    _run_command("map_scan", {}, allow_daemon=True)


@app.command("map-list")
def map_list(
    tcode: str = typer.Option("", "--tcode"),
    system_id: str = typer.Option("", "--system"),
    client: str = typer.Option("", "--client"),
    language: str = typer.Option("", "--language"),
) -> None:
    """저장된 최신 UI 지도를 SAP scope로 조회."""
    _run_command(
        "map_list",
        {"tcode": tcode, "system_id": system_id, "client": client, "language": language},
        allow_daemon=True,
    )


@app.command("map-search")
def map_search(
    map_id: str = typer.Argument(),
    tcode: str = typer.Argument(),
    query: str = typer.Argument(),
    system_id: str = typer.Option("", "--system"),
    client: str = typer.Option("", "--client"),
    language: str = typer.Option("", "--language"),
    version: int | None = typer.Option(None, "--version", min=1),
    kinds: str = typer.Option("", "--kinds", help="state,component,transition"),
    limit: int = typer.Option(50, "--limit", min=1, max=500),
) -> None:
    """저장된 지도에서 상태·요소·전이를 검색."""
    _run_command(
        "map_search",
        {
            "map_id": map_id, "tcode": tcode, "query": query,
            "system_id": system_id, "client": client, "language": language,
            "version": version, "kinds": kinds, "limit": limit,
        },
        allow_daemon=True,
    )


@app.command("map-path")
def map_path(
    map_id: str = typer.Argument(),
    tcode: str = typer.Argument(),
    from_state_id: str = typer.Argument(),
    target: str = typer.Argument(),
    target_type: str = typer.Option("state", "--target-type"),
    system_id: str = typer.Option("", "--system"),
    client: str = typer.Option("", "--client"),
    language: str = typer.Option("", "--language"),
    version: int | None = typer.Option(None, "--version", min=1),
    limit: int = typer.Option(50, "--limit", min=1, max=500),
) -> None:
    """시작 상태에서 상태 또는 UI 요소까지 최단 경로를 조회."""
    _run_command("map_path", locals(), allow_daemon=True)


@app.command("map-job-status")
def map_job_status(
    job_id: str = typer.Argument(""),
    status: str = typer.Option("", "--status"),
    limit: int = typer.Option(50, "--limit", min=1, max=500),
) -> None:
    """지도 생성 job과 사용자 질문 상태를 조회."""
    _run_command(
        "map_job_status", {"job_id": job_id, "status": status or None, "limit": limit},
        allow_daemon=True,
    )


@app.command("map-report")
def map_report(
    map_id: str = typer.Argument(),
    tcode: str = typer.Argument(),
    system_id: str = typer.Option("", "--system"),
    client: str = typer.Option("", "--client"),
    language: str = typer.Option("", "--language"),
    version: int | None = typer.Option(None, "--version", min=1),
) -> None:
    """지도 구조 coverage·복구·스캔 계측을 업무 값 없이 출력."""
    _run_command("map_report", locals(), allow_daemon=True)


@app.command("map-view")
def map_view(
    map_id: str = typer.Argument(),
    tcode: str = typer.Argument(),
    system_id: str = typer.Option("", "--system"),
    client: str = typer.Option("", "--client"),
    language: str = typer.Option("", "--language"),
    version: int | None = typer.Option(None, "--version", min=1),
) -> None:
    """화면과 다음 화면 연결을 구조화된 그래프 뷰로 출력."""
    _run_command("map_view", locals(), allow_daemon=True)


@app.command("map-delete")
def map_delete(
    map_id: str = typer.Argument(),
    tcode: str = typer.Argument(),
    system_id: str = typer.Option("", "--system"),
    client: str = typer.Option("", "--client"),
    language: str = typer.Option("", "--language"),
    yes: bool = typer.Option(
        False, "--yes", help="영구 삭제 확인. 없으면 삭제 대상만 미리 본다"
    ),
) -> None:
    """저장된 UI 지도를 모든 버전째 영구 삭제. --yes 없이는 미리보기만 한다."""
    _run_command(
        "map_delete",
        {
            "map_id": map_id,
            "tcode": tcode,
            "system_id": system_id,
            "client": client,
            "language": language,
            "confirm": yes,
        },
        allow_daemon=True,
    )


@app.command("map-record-start")
def map_record_start(
    map_id: str = typer.Argument(),
    tcode: str = typer.Argument(),
) -> None:
    """지도 기록 세션 시작. 이후 press/act로 직접 이동하면 자동으로 지도에 저장."""
    _run_command(
        "map_record_start", {"map_id": map_id, "tcode": tcode}, allow_daemon=True
    )


@app.command("map-record-finish")
def map_record_finish() -> None:
    """지도 기록 세션 종료 후 요약 반환."""
    _run_command("map_record_finish", {}, allow_daemon=True)


@app.command("map-record-cancel")
def map_record_cancel() -> None:
    """지도 기록 세션 중단 (저장된 화면은 유지)."""
    _run_command("map_record_cancel", {}, allow_daemon=True)


@app.command("map-record-status")
def map_record_status() -> None:
    """현재 지도 기록 세션 상태 조회."""
    _run_command("map_record_status", {}, allow_daemon=True)


@app.command("map-job-cancel")
def map_job_cancel(job_id: str = typer.Argument()) -> None:
    """대기 또는 실행 중인 지도 생성 job을 취소."""
    _run_command("map_job_cancel", {"job_id": job_id}, allow_daemon=True)


@app.command("map-augment-start")
def map_augment_start(
    map_id: str = typer.Argument(),
    tcode: str = typer.Argument(),
    from_state_id: str = typer.Argument(),
    request: str = typer.Argument(help="사용자가 직접 추가할 누락 화면 또는 분기 설명"),
    system_id: str = typer.Option("", "--system"),
    client: str = typer.Option("", "--client"),
    language: str = typer.Option("", "--language"),
    version: int | None = typer.Option(None, "--version", min=1),
    scope_json: str = typer.Option("{}", "--scope-json"),
) -> None:
    """완료된 기존 지도에 사용자 안내형 누락 화면 보강 job을 생성."""
    try:
        scope = json.loads(_json_arg(scope_json, label="--scope-json"))
    except json.JSONDecodeError as exc:
        raise typer.BadParameter("--scope-json must be a JSON object") from exc
    if not isinstance(scope, dict):
        raise typer.BadParameter("--scope-json must be a JSON object")
    _run_command(
        "map_augment_start",
        {
            "map_id": map_id,
            "tcode": tcode,
            "from_state_id": from_state_id,
            "request": request,
            "system_id": system_id,
            "client": client,
            "language": language,
            "version": version,
            "scope": scope,
        },
        allow_daemon=True,
    )


@app.command("map-traverse-start")
def map_traverse_start(
    tcode: str = typer.Argument(),
    map_id: str = typer.Option("", "--map-id"),
    mode: str = typer.Option(
        "passive", "--mode", help="passive | guided | reversible | allowlisted"
    ),
    budget_json: str = typer.Option("{}", "--budget-json"),
    allowed_actions: str = typer.Option("", "--allowed-actions"),
    allowed_branches_json: str = typer.Option("[]", "--allowed-branches-json"),
    navigate: bool = typer.Option(True, "--navigate/--no-navigate"),
    test_client_confirmed: bool = typer.Option(False, "--test-client-confirmed"),
    semantic_review: bool = typer.Option(
        True, "--semantic-review/--no-semantic-review",
        help="guided 수집 중 MCP AI 의미 후보 검토 사용",
    ),
) -> None:
    """지도 job을 등록합니다. guided는 후보를 하나씩 물어본 뒤 확장합니다."""
    try:
        budget = json.loads(_json_arg(budget_json, label="--budget-json"))
        allowed_branches = json.loads(
            _json_arg(allowed_branches_json, label="--allowed-branches-json")
        )
    except json.JSONDecodeError as exc:
        raise typer.BadParameter(
            "--budget-json and --allowed-branches-json must be valid JSON"
        ) from exc
    if not isinstance(allowed_branches, list):
        raise typer.BadParameter("--allowed-branches-json must be a JSON array")
    _run_command(
        "map_traverse_start",
        {
            "tcode": tcode, "map_id": map_id, "mode": mode, "budget": budget,
            "allowed_actions": [item.strip() for item in allowed_actions.split(",") if item.strip()],
            "allowed_branches": allowed_branches,
            "navigate": navigate, "test_client_confirmed": test_client_confirmed,
            "semantic_review": semantic_review,
        },
        allow_daemon=True,
    )


@app.command("map-traverse-resume")
def map_traverse_resume(
    job_id: str = typer.Argument(),
    answers_json: str = typer.Argument(
        "{}", help="resume_token별 답변 JSON object. @경로/@- 로 파일·stdin에서 읽음"
    ),
    budget_json: str = typer.Option(
        "{}", "--budget-json", help="paused job의 확장 budget. @경로/@- 지원"
    ),
) -> None:
    """사용자 답변 또는 더 큰 budget으로 지도 순회를 재개."""
    try:
        answers = json.loads(_json_arg(answers_json, label="answers_json"))
    except json.JSONDecodeError as exc:
        raise typer.BadParameter("answers_json must be a JSON object") from exc
    try:
        budget = json.loads(_json_arg(budget_json, label="--budget-json"))
    except json.JSONDecodeError as exc:
        raise typer.BadParameter("--budget-json must be a JSON object") from exc
    if not isinstance(answers, dict) or not isinstance(budget, dict):
        raise typer.BadParameter("answers_json and --budget-json must be JSON objects")
    if not answers and not budget:
        raise typer.BadParameter("answers_json or --budget-json is required")
    _run_command(
        "map_traverse_resume",
        {"job_id": job_id, "answers": answers, "budget": budget},
        allow_daemon=True,
    )


@app.command("plan-preview")
def plan_preview(
    request: str = typer.Argument(help="SAP에서 수행할 자연어 요청"),
    tcode: str = typer.Option("", "--tcode"),
    map_id: str = typer.Option("", "--map-id"),
    system_id: str = typer.Option("", "--system"),
    client: str = typer.Option("", "--client"),
    current_state_id: str = typer.Option("", "--current-state"),
    goal_query: str = typer.Option("", "--goal"),
    values_json: str = typer.Option("{}", "--values-json"),
    success_criteria_json: str = typer.Option("[]", "--success-criteria-json"),
    api_version: str = typer.Option("", "--api-version"),
    gui_version: str = typer.Option("", "--gui-version"),
    application_release: str = typer.Option("", "--application-release"),
    schema_version: str = typer.Option("", "--schema-version"),
) -> None:
    """자연어 요청을 저장 지도 기반의 실행 전 전체 계획으로 컴파일."""
    try:
        values = json.loads(_json_arg(values_json, label="--values-json"))
    except json.JSONDecodeError as exc:
        raise typer.BadParameter("--values-json must be a JSON object") from exc
    try:
        success_criteria = json.loads(
            _json_arg(success_criteria_json, label="--success-criteria-json")
        )
    except json.JSONDecodeError as exc:
        raise typer.BadParameter("--success-criteria-json must be a JSON array") from exc
    if not isinstance(success_criteria, list) or not all(
        isinstance(item, str) for item in success_criteria
    ):
        raise typer.BadParameter("--success-criteria-json must be a JSON string array")
    _run_command(
        "plan_preview",
        {
            "request": request, "tcode": tcode, "map_id": map_id,
            "system_id": system_id, "client": client,
            "current_state_id": current_state_id, "goal_query": goal_query,
            "values": values, "success_criteria": success_criteria,
            "api_version": api_version, "gui_version": gui_version,
            "application_release": application_release, "schema_version": schema_version,
        },
        allow_daemon=True,
    )


@app.command("plan-execute")
def plan_execute(
    plan_id: str = typer.Argument("", help="미리보기 plan_ref의 plan_id"),
    plan_hash: str = typer.Argument("", help="미리보기 plan_ref의 plan_hash"),
    execution_id: str = typer.Argument("", help="재시도에 사용할 ID (생략하면 자동 발급)"),
    verify_mode: str = typer.Option("cheap", "--verify"),
    approval_token: str = typer.Option("", "--approval-token"),
    plan_ref_json: str = typer.Option(
        "", "--plan-ref-json", help="plan-preview가 돌려준 plan_ref JSON object. @경로/@- 지원"
    ),
) -> None:
    """미리보기의 plan_ref를 사용해 daemon 메모리 계획을 한 번에 실행."""
    plan_ref: dict[str, Any] | None = None
    if plan_ref_json:
        try:
            decoded = json.loads(_json_arg(plan_ref_json, label="--plan-ref-json"))
        except json.JSONDecodeError as exc:
            raise typer.BadParameter("--plan-ref-json must be a JSON object") from exc
        if not isinstance(decoded, dict):
            raise typer.BadParameter("--plan-ref-json must be a JSON object")
        plan_ref = decoded
    _run_command(
        "plan_execute",
        {
            "plan_id": plan_id,
            "plan_hash": plan_hash,
            "execution_id": execution_id,
            "verify_mode": verify_mode,
            "approval_token": approval_token,
            "plan_ref": plan_ref,
        },
        allow_daemon=True,
    )


@app.command("plan-approve")
def plan_approve(
    plan_id: str = typer.Argument(),
    plan_hash: str = typer.Argument(),
    subject_agent: str = typer.Argument(help="승인 대상 실행 에이전트"),
    step_ids_json: str = typer.Argument(
        help="승인할 step_id JSON array. @경로/@- 로 파일·stdin에서 읽음"
    ),
    expires_in_seconds: float = typer.Option(300.0, "--expires-in"),
) -> None:
    """관리자가 daemon 메모리에 일회용 계획 승인 토큰을 발급."""
    try:
        step_ids = json.loads(_json_arg(step_ids_json, label="step_ids_json"))
    except json.JSONDecodeError as exc:
        raise typer.BadParameter("step_ids_json must be a JSON array") from exc
    if not isinstance(step_ids, list):
        raise typer.BadParameter("step_ids_json must be a JSON array")
    _run_command(
        "plan_approve",
        {
            "plan_id": plan_id,
            "plan_hash": plan_hash,
            "subject_agent": subject_agent,
            "step_ids": step_ids,
            "expires_in_seconds": expires_in_seconds,
        },
        allow_daemon=True,
    )


@app.command("job-status")
def job_status(job_id: str = typer.Argument("")) -> None:
    """daemon의 계획 실행·지도 순회 공통 작업 상태를 조회."""
    _run_command("job_status", {"job_id": job_id}, allow_daemon=True)


@app.command("job-reconcile")
def job_reconcile(
    job_id: str = typer.Argument(),
    resolution: str = typer.Argument(),
) -> None:
    """미확정 작업을 수동 확인 결과에 따라 명시적으로 정리."""
    _run_command(
        "job_reconcile",
        {"job_id": job_id, "resolution": resolution},
        allow_daemon=True,
    )


@app.command("event-subscribe")
def event_subscribe(
    scope: str = typer.Argument(help="application 또는 session"),
    events: str = typer.Argument(help="쉼표로 구분한 공식 이벤트 이름"),
    ttl_seconds: float = typer.Option(300, "--ttl"),
    queue_limit: int = typer.Option(256, "--queue-limit", min=1),
) -> None:
    """상주 daemon에서 SAP GUI 이벤트를 구독."""
    _run_command(
        "event_subscribe",
        {
            "scope": scope,
            "events": [item.strip() for item in events.split(",") if item.strip()],
            "ttl_seconds": ttl_seconds,
            "queue_limit": queue_limit,
        },
        allow_daemon=True,
    )


@app.command("event-poll")
def event_poll(
    subscription_id: str = typer.Argument(),
    cursor: int = typer.Option(0, "--cursor", min=0),
    limit: int = typer.Option(100, "--limit", min=1),
) -> None:
    """구독된 SAP GUI 이벤트를 비차단 조회."""
    _run_command(
        "event_poll",
        {"subscription_id": subscription_id, "cursor": cursor, "limit": limit},
        allow_daemon=True,
    )


@app.command("event-unsubscribe")
def event_unsubscribe(subscription_id: str = typer.Argument()) -> None:
    """SAP GUI 이벤트 구독을 해제."""
    _run_command(
        "event_unsubscribe",
        {"subscription_id": subscription_id},
        allow_daemon=True,
    )


@app.command("event-list")
def event_list() -> None:
    """현재 slot의 SAP GUI 이벤트 구독을 조회."""
    _run_command("event_list", {}, allow_daemon=True)


@app.command("scripting-graph-build")
def scripting_graph_build(
    out: Optional[str] = typer.Option(
        None,
        "--out",
        "-o",
        help="생성 graph JSON 경로. 기본값: 패키지 overlay graph.generated.json",
    ),
    version: str = typer.Option("800.08", "--version", help="SAP Help deliverable version"),
    snapshot_dir: Optional[str] = typer.Option(
        None, "--snapshot-dir", help="공식 metadata/TOC/XHTML snapshot 보관 경로"
    ),
    request_delay: float = typer.Option(
        0.02, "--request-delay", min=0.0, help="SAP Help page 요청 간격(초)"
    ),
) -> None:
    """공식 SAP Help XHTML에서 strict 검증된 API overlay를 생성."""
    from scripts.build_sap_help_manifest import (
        build_graph,
        fetch_official_pages,
        validate_graph,
    )

    default_out = (
        Path(__file__).resolve().parent
        / "ontology"
        / "knowledge"
        / "scripting_api"
        / "graph.generated.json"
    )
    out_path = out or str(default_out)
    if out_path == "-":
        raise typer.BadParameter("CLI 명령은 JSON summary를 출력해야 하므로 --out '-'는 지원하지 않습니다.")

    metadata, toc, pages = fetch_official_pages(
        version=version,
        snapshot_dir=Path(snapshot_dir).expanduser() if snapshot_dir else None,
        request_delay=request_delay,
    )
    graph = build_graph(metadata=metadata, toc=toc, pages=pages)
    counts = validate_graph(graph, toc, strict=True)
    target = Path(out_path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _output_json({
        "status": "ok",
        "validation": "strict_official_xhtml",
        "version": graph["version"],
        "nodes": len(graph["nodes"]),
        "edges": len(graph["edges"]),
        "counts": counts,
        "out": str(target),
        "source": graph["sources"][0]["url"],
        "overlay_loaded_by_runtime": target.resolve() == default_out.resolve(),
        "next_check": "sap scripting-query --type GuiSession --depth 1",
    })


@app.command("scripting-graph-build-legacy")
def scripting_graph_build_legacy(
    input_source: str = typer.Argument(
        help="검토용 Text/Markdown export, local file, stdin '-' 또는 HTTP(S) URL"
    ),
    out: str = typer.Option(..., "--out", "-o", help="검토용 graph JSON 경로"),
    source: str = typer.Option("", "--source", help="graph source URL override"),
) -> None:
    """Best-effort parser로 검토용 graph를 생성하며 runtime overlay는 보호."""
    from scripts.build_sap_scripting_graph import build_graph, read_source, write_graph

    text, detected_source = read_source(input_source)
    graph = build_graph(text, source=source or detected_source)
    try:
        write_graph(graph, out)
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="--out") from exc
    _output_json(
        {
            "status": "review_required",
            "validation": "best_effort_legacy",
            "nodes": len(graph["nodes"]),
            "edges": len(graph["edges"]),
            "out": str(Path(out).expanduser()),
            "source": source or detected_source,
            "overlay_loaded_by_runtime": False,
        }
    )


@app.command("act")
def ui_action(
    element_id: str = typer.Argument(help="대상 SAP GUI element ID"),
    action: str = typer.Argument(help="액션 (set/select/press/focus/read/read_table/read_cell/set_cell/click_cell/context_menu/context_menu_item/select_column/press_toolbar_button/select_toolbar_menu_item/scroll/page_down/append_text/maximize/call_method/get_property/set_property/search_help/select_node/select_row/doubleclick_cell 등)"),
    value: str | None = typer.Argument(None, help="액션 값 (필드값, 콤보값, node_key, row 등)"),
    node_key: str = typer.Option("", "--node-key", help="트리 node_key"),
    column: str = typer.Option("", "--column", help="트리 press_node 컬럼 또는 테이블 컬럼"),
    row: int | None = typer.Option(None, "--row", help="테이블 행 번호 (0-based)"),
    max_rows: int = typer.Option(50, "--max-rows", help="read_table 최대 행 수"),
    max_lines: int | None = typer.Option(None, "--max-lines", help="텍스트 에디터 read 최대 행 수"),
    max_chars: int | None = typer.Option(None, "--max-chars", help="텍스트 에디터 read 최대 문자 수"),
    columns: str = typer.Option("", "--columns", help="read_table 컬럼 목록(쉼표 구분)"),
    args_json: str = typer.Option(
        "", "--args-json", help="call_method ByVal 입력 JSON 배열(ByRef/out은 자동)"
    ),
    property_name: str = typer.Option("", "--property-name", help="get_property/set_property 대상 속성명"),
    property_value: str = typer.Option("", "--property-value", help="set_property 값(value 인자 대신 사용 가능)"),
) -> None:
    """SAP GUI Scripting capability 기반 범용 UI 액션 실행."""
    params: dict[str, Any] = {
        "element_id": element_id,
        "action": action,
    }
    if value is not None:
        params["value"] = value
    if node_key:
        params["node_key"] = node_key
    if column:
        params["column"] = column
    if row is not None:
        params["row"] = row
    if max_rows != 50:
        params["max_rows"] = max_rows
    if max_lines is not None:
        params["max_lines"] = max_lines
    if max_chars is not None:
        params["max_chars"] = max_chars
    if columns:
        params["columns"] = columns
    if args_json:
        params["args_json"] = args_json
    if property_name:
        params["property_name"] = property_name
    if property_value:
        params["property_value"] = property_value
    _run_command("ui_action", params, allow_daemon=True)


def _make_generated_ui_action_command(action_command: str, action_name: str):
    def generated_ui_action(
        element_id: str = typer.Argument(help="대상 SAP GUI element ID"),
        value: str | None = typer.Argument(None, help="액션 값 (필드값, node_key, row, method/property 이름 등)"),
        node_key: str = typer.Option("", "--node-key", help="트리 node_key"),
        column: str = typer.Option("", "--column", help="테이블 컬럼, 트리 컬럼, toolbar/menu item id, method 이름 등"),
        row: int | None = typer.Option(None, "--row", help="테이블 행 번호 (0-based)"),
        max_rows: int = typer.Option(50, "--max-rows", help="read_table 최대 행 수"),
        max_lines: int | None = typer.Option(None, "--max-lines", help="텍스트 에디터 read 최대 행 수"),
        max_chars: int | None = typer.Option(None, "--max-chars", help="텍스트 에디터 read 최대 문자 수"),
        columns: str = typer.Option("", "--columns", help="read_table 컬럼 목록(쉼표 구분)"),
        args_json: str = typer.Option(
            "", "--args-json", help="call_method ByVal 입력 JSON 배열(ByRef/out은 자동)"
        ),
        property_name: str = typer.Option("", "--property-name", help="get_property/set_property 대상 속성명"),
        property_value: str = typer.Option("", "--property-value", help="set_property 값(value 인자 대신 사용 가능)"),
    ) -> None:
        """SAP GUI Scripting capability 기반 개별 UI 액션 실행."""
        params: dict[str, Any] = {"element_id": element_id}
        if value is not None:
            params["value"] = value
        if node_key:
            params["node_key"] = node_key
        if column:
            params["column"] = column
        if row is not None:
            params["row"] = row
        if max_rows != 50:
            params["max_rows"] = max_rows
        if max_lines is not None:
            params["max_lines"] = max_lines
        if max_chars is not None:
            params["max_chars"] = max_chars
        if columns:
            params["columns"] = columns
        if args_json:
            params["args_json"] = args_json
        if property_name:
            params["property_name"] = property_name
        if property_value:
            params["property_value"] = property_value
        _run_command(action_command, params, allow_daemon=True)

    generated_ui_action.__name__ = f"{action_command}_command"
    generated_ui_action.__doc__ = f"Adaptive UI action: {action_name}"
    return generated_ui_action


for _ui_action_def in get_ui_action_defs():
    app.command(
        _ui_action_def.cli_name,
        help=_ui_action_def.description,
    )(_make_generated_ui_action_command(_ui_action_def.command, _ui_action_def.action))


@app.command("fill")
def fill_fields(
    assignments: list[str] = typer.Argument(help="필드=값 쌍 (예: MATNR=100-100 WERKS=1000)"),
    enter: bool = typer.Option(True, "--enter/--no-enter", help="입력 후 자동 Enter (기본: 활성)"),
) -> None:
    """여러 필드를 한번에 입력. 필드명 또는 element_id 사용 가능.

    예시:
      sap fill MATNR=100-100 WERKS=1000 LGORT=0001
      sap fill MATNR=100-100 WERKS=1000 --no-enter
    """
    fields: dict[str, str] = {}
    for a in assignments:
        if "=" not in a:
            typer.echo(f"잘못된 형식: {a!r} (필드=값 형식으로 입력하세요)")
            raise typer.Exit(1)
        k, v = a.split("=", 1)
        fields[k.strip()] = v.strip()
    _run_command("fill", {"fields": fields, "enter": enter}, allow_daemon=True)


@app.command("get")
def get_field(
    element_ids: list[str] = typer.Argument(help="필드 ID (여러 개 가능)"),
) -> None:
    """필드 값 읽기. 여러 필드를 한번에 조회하면 배열로 반환."""
    if len(element_ids) == 1:
        _run_command("get", {"element_id": element_ids[0]}, allow_daemon=True)
    else:
        _run_command("get", {"element_ids": element_ids}, allow_daemon=True)


@app.command()
def press(
    element_id: str = typer.Argument(help="버튼 ID (예: wnd[0]/tbar[0]/btn[0])"),
) -> None:
    """버튼 클릭."""
    _run_command("press", {"element_id": element_id}, allow_daemon=True)


@app.command()
def vkey(
    key: int = typer.Argument(help="VKey 번호 (0=Enter, 3=Back, 8=Execute, 11=Save, 12=Cancel)"),
) -> None:
    """VKey 전송."""
    _run_command("vkey", {"key": key}, allow_daemon=True)


@app.command()
def status() -> None:
    """상태바 메시지 조회."""
    _run_command("status", {}, allow_daemon=True)


@app.command()
def table(
    element_id: str = typer.Argument(help="테이블/그리드 ID"),
    max_rows: int = typer.Option(50, "--max-rows", "-r", help="최대 읽을 행 수 (기본 50, 최대 500)"),
    columns: Optional[str] = typer.Option(None, "--columns", "-c", help="읽을 컬럼 (쉼표 구분, 예: MATNR,MAKTX)"),
) -> None:
    """테이블 데이터 읽기."""
    params: dict[str, Any] = {"element_id": element_id, "max_rows": max_rows}
    if columns:
        params["columns"] = columns
    _run_command("table", params, allow_daemon=True)


@app.command("tree")
def read_tree(
    element_id: str = typer.Argument(help="트리 ID (sap screen의 trees에서 확인)"),
    node_key: str = typer.Option("", "--node-key", "-n", help="특정 노드의 자식만 조회"),
    depth: int = typer.Option(-1, "--depth", "-d", help="트리 깊이 제한 (-1=무제한, 0=현재만, 1=자식까지)"),
) -> None:
    """트리 컨트롤 데이터 읽기 (Document Flow 등)."""
    params: dict[str, Any] = {"element_id": element_id}
    if node_key:
        params["node_key"] = node_key
    if depth >= 0:
        params["depth"] = depth
    _run_command("tree", params, allow_daemon=True)


@app.command("tree-select")
def tree_select(
    element_id: str = typer.Argument(help="트리 ID"),
    node_key: str = typer.Argument(help="노드 키 (sap tree 결과의 key)"),
) -> None:
    """트리 노드 선택."""
    _run_command("tree_select", {"element_id": element_id, "node_key": node_key}, allow_daemon=True)


@app.command("tree-doubleclick")
def tree_doubleclick(
    element_id: str = typer.Argument(help="트리 ID"),
    node_key: str = typer.Argument(help="노드 키"),
) -> None:
    """트리 노드 더블클릭 (SPRO IMG Activity 진입 등)."""
    _run_command("tree_doubleclick", {"element_id": element_id, "node_key": node_key}, allow_daemon=True)


@app.command("tree-expand")
def tree_expand(
    element_id: str = typer.Argument(help="트리 ID"),
    node_key: str = typer.Argument(help="노드 키"),
) -> None:
    """트리 노드 확장 (하위 메뉴 펼치기)."""
    _run_command("tree_expand", {"element_id": element_id, "node_key": node_key}, allow_daemon=True)


@app.command("tree-collapse")
def tree_collapse(
    element_id: str = typer.Argument(help="트리 ID"),
    node_key: str = typer.Argument(help="노드 키"),
) -> None:
    """트리 노드 접기."""
    _run_command("tree_collapse", {"element_id": element_id, "node_key": node_key}, allow_daemon=True)


@app.command("tree-press")
def tree_press(
    element_id: str = typer.Argument(help="트리 ID"),
    node_key: str = typer.Argument(help="노드 키"),
    column: str = typer.Argument("", help="버튼이 있는 컬럼 이름"),
) -> None:
    """트리 내 버튼/아이콘 클릭 (SPRO 실행 아이콘 등)."""
    _run_command("tree_press", {"element_id": element_id, "node_key": node_key, "column": column}, allow_daemon=True)


@app.command("table-select")
def table_select(
    element_id: str = typer.Argument(help="테이블 ID"),
    row: int = typer.Argument(help="선택할 행 번호 (0-based)"),
) -> None:
    """테이블에서 행 선택 (MM03 뷰 선택 등)."""
    _run_command("table_select", {"element_id": element_id, "row": row}, allow_daemon=True)


@app.command("table-doubleclick")
def table_doubleclick(
    element_id: str = typer.Argument(help="테이블 ID"),
    row: int = typer.Argument(help="더블클릭할 행 번호 (0-based)"),
    column: str = typer.Argument("", help="컬럼 ID (ALV 행 진입 시)"),
) -> None:
    """테이블 셀 더블클릭 (ALV 행 진입 등)."""
    _run_command("table_doubleclick", {"element_id": element_id, "row": row, "column": column}, allow_daemon=True)


@app.command()
def command(
    cmd: str = typer.Argument(help="SAP 커맨드 (예: /nMM03, /oSE80)"),
) -> None:
    """커맨드 필드에 명령 실행."""
    _run_command("command", {"cmd": cmd}, allow_daemon=True)


@app.command()
def modal() -> None:
    """모달 윈도우(팝업) 정보 조회."""
    _run_command("modal", {}, allow_daemon=True)


@app.command("transcript")
def transcript_cmd(
    limit: int = typer.Option(20, "--limit", "-n", help="최대 조회 개수"),
    outcome: Optional[str] = typer.Option(None, "--outcome", "-o", help="success / denied / approval_required / error"),
    command: Optional[str] = typer.Option(None, "--command", "-c", help="dispatcher 명령 필터"),
    tool: Optional[str] = typer.Option(None, "--tool", "-t", help="tool 이름 필터"),
    agent: Optional[str] = typer.Option(None, "--agent", "-a", help="agent 이름 필터"),
    text: Optional[str] = typer.Option(None, "--text", help="전체 entry 텍스트 검색"),
    full: bool = typer.Option(False, "--full", help="요약 대신 전체 entry 반환"),
) -> None:
    """기록된 transcript 조회."""
    _output_json(
        _dispatch_local(
            "transcript_query",
            {
                "limit": limit,
                "outcome": outcome,
                "command": command,
                "tool": tool,
                "agent": agent,
                "text": text,
                "full": full,
            },
        )
    )


@app.command("transcript-plan")
def transcript_plan_cmd(
    limit: int = typer.Option(20, "--limit", "-n", help="검색할 최대 entry 수"),
    index: int = typer.Option(0, "--index", "-i", help="필터 결과에서 선택할 entry 인덱스"),
    outcome: Optional[str] = typer.Option(None, "--outcome", "-o", help="success / denied / approval_required / error"),
    command: Optional[str] = typer.Option(None, "--command", "-c", help="dispatcher 명령 필터"),
    tool: Optional[str] = typer.Option(None, "--tool", "-t", help="tool 이름 필터"),
    agent: Optional[str] = typer.Option(None, "--agent", "-a", help="agent 이름 필터"),
    text: Optional[str] = typer.Option(None, "--text", help="전체 entry 텍스트 검색"),
) -> None:
    """선택한 transcript entry의 재실행 검토 계획 생성."""
    _output_json(
        _dispatch_local(
            "transcript_plan",
            {
                "limit": limit,
                "index": index,
                "outcome": outcome,
                "command": command,
                "tool": tool,
                "agent": agent,
                "text": text,
            },
        )
    )


# -------------------------------------------------------------------
# Session Recording Commands
# -------------------------------------------------------------------

@app.command("session-start")
def session_start_cmd(
    user_request: Optional[str] = typer.Argument(None, help="이번 SAP 작업 목표/요청"),
    ai_reasoning: str = typer.Option("", "--reasoning", "-r", help="AI 판단/맥락 메모"),
) -> None:
    """CLI/bash 실행을 recipe 후보 세션으로 기록 시작."""
    result = _dispatch_local(
        "session_start",
        {
            "user_request": user_request or "",
            "ai_reasoning": ai_reasoning,
        },
    )
    session_id = str(result.get("session_id") or "")
    if session_id:
        _write_active_cli_session_id(session_id)
        result["active_cli_session"] = True
        result["active_session_file"] = str(_ACTIVE_CLI_SESSION_PATH)
        result["note"] = "이후 sap CLI 명령은 이 세션에 자동 기록됩니다. 끝나면 sap session-end 를 실행하세요."
    _output_json(result)


@app.command("session-use")
def session_use_cmd(
    session_id: str = typer.Argument(help="활성화할 session_id"),
) -> None:
    """이미 시작된 세션을 현재 CLI active session으로 지정."""
    _write_active_cli_session_id(session_id)
    _output_json(
        {
            "session_id": session_id,
            "active_cli_session": True,
            "active_session_file": str(_ACTIVE_CLI_SESSION_PATH),
        }
    )


@app.command("session-active")
def session_active_cmd() -> None:
    """현재 CLI active session 조회."""
    session_id = _read_active_cli_session_id()
    _output_json(
        {
            "session_id": session_id,
            "active_cli_session": bool(session_id),
            "active_session_file": str(_ACTIVE_CLI_SESSION_PATH),
        }
    )


@app.command("session-clear")
def session_clear_cmd() -> None:
    """CLI active session 포인터만 제거."""
    session_id = _read_active_cli_session_id()
    removed = _clear_active_cli_session_id()
    _output_json(
        {
            "session_id": session_id,
            "active_cli_session": False,
            "cleared": removed,
            "active_session_file": str(_ACTIVE_CLI_SESSION_PATH),
        }
    )


@app.command("session-mark")
def session_mark_cmd(
    text: str = typer.Argument(help="세션/레시피에 남길 step 설명"),
    kind: str = typer.Option("note", "--kind", "-k", help="note/bash/external/manual/check 등 step 종류"),
    cli_invocation: Optional[str] = typer.Option(None, "--cli", help="따라 실행할 외부 CLI/bash 원문"),
    raw_argv_json: Optional[str] = typer.Option(None, "--argv-json", help="raw argv JSON 배열"),
    session_id: Optional[str] = typer.Option(None, "--session-id", help="대상 session_id. 생략하면 active session 사용"),
    followable: bool = typer.Option(True, "--followable/--not-followable", help="recipe가 따라야 하는 단계인지 여부"),
    requires_sap: bool = typer.Option(False, "--requires-sap/--no-requires-sap", help="SAP 연결/화면이 필요한 단계인지 여부"),
) -> None:
    """SAP 외부/수동/검증 단계도 recipe가 따를 수 있도록 세션에 표식 추가."""
    import json as _json

    sid = session_id or _read_active_cli_session_id()
    if not sid:
        _handle_error(RuntimeError("active session이 없습니다. 먼저 sap session-start 를 실행하세요."))
    raw_argv: list[str] = []
    if raw_argv_json:
        parsed = _json.loads(raw_argv_json)
        if not isinstance(parsed, list):
            _handle_error(ValueError("--argv-json은 JSON 배열이어야 합니다"))
        raw_argv = [str(item) for item in parsed]
    elif cli_invocation:
        raw_argv = shlex.split(cli_invocation)
    result = _dispatch_local(
        "session_mark",
        {
            "session_id": sid,
            "text": text,
            "step_kind": kind,
            "cli_invocation": cli_invocation or "",
            "raw_argv": raw_argv,
            "followable": followable,
            "requires_sap": requires_sap,
            "source": "cli",
        },
    )
    _output_json(result)


@app.command("session-excel")
def session_excel_cmd(
    text: str = typer.Argument(help="엑셀 조작 step 설명"),
    action: str = typer.Option("note", "--action", "-a", help="open/read/write/filter/copy/paste/export/check 등"),
    workbook: Optional[str] = typer.Option(None, "--workbook", "-w", help="엑셀 파일 경로/이름"),
    sheet: Optional[str] = typer.Option(None, "--sheet", "-s", help="시트 이름"),
    range_ref: Optional[str] = typer.Option(None, "--range", "-r", help="셀/범위 예: A1, A1:D20"),
    value: Optional[str] = typer.Option(None, "--value", "-v", help="입력/확인할 값"),
    cli_invocation: Optional[str] = typer.Option(None, "--cli", help="실제 실행할 외부 CLI/bash 원문"),
    raw_argv_json: Optional[str] = typer.Option(None, "--argv-json", help="raw argv JSON 배열"),
    session_id: Optional[str] = typer.Option(None, "--session-id", help="대상 session_id. 생략하면 active session 사용"),
    followable: bool = typer.Option(True, "--followable/--not-followable", help="recipe가 따라야 하는 단계인지 여부"),
) -> None:
    """Excel 조작/준비/검증 단계를 recipe용 세션 step으로 간단 기록."""
    import json as _json

    sid = session_id or _read_active_cli_session_id()
    if not sid:
        _handle_error(RuntimeError("active session이 없습니다. 먼저 sap session-start 를 실행하세요."))
    raw_argv: list[str] = []
    if raw_argv_json:
        parsed = _json.loads(raw_argv_json)
        if not isinstance(parsed, list):
            _handle_error(ValueError("--argv-json은 JSON 배열이어야 합니다"))
        raw_argv = [str(item) for item in parsed]
    elif cli_invocation:
        raw_argv = shlex.split(cli_invocation)
    result = _dispatch_local(
        "session_excel",
        {
            "session_id": sid,
            "text": text,
            "action": action,
            "workbook": workbook,
            "sheet": sheet,
            "range_ref": range_ref,
            "value": value,
            "cli_invocation": cli_invocation or "",
            "raw_argv": raw_argv,
            "followable": followable,
            "requires_sap": False,
            "source": "cli",
        },
    )
    _output_json(result)


@app.command("session-end")
def session_end_cmd(
    session_id: Optional[str] = typer.Argument(None, help="종료할 session_id. 생략하면 active session 사용"),
    outcome: str = typer.Option("success", "--outcome", "-o", help="success / failed 등"),
    summary: str = typer.Option("", "--summary", "-s", help="작업 결과 요약"),
) -> None:
    """CLI/bash 기록 세션 종료 및 recipe 후보 생성."""
    sid = session_id or _read_active_cli_session_id()
    if not sid:
        _handle_error(RuntimeError("active session이 없습니다. 먼저 sap session-start 를 실행하세요."))
    result = _dispatch_local(
        "session_end",
        {
            "session_id": sid,
            "outcome": outcome,
            "outcome_summary": summary,
        },
    )
    if "error" not in result:
        _clear_active_cli_session_id(sid)
        result["active_cli_session"] = False
    _output_json(result)


@app.command("session-list")
def session_list_cmd(
    date: Optional[str] = typer.Option(None, "--date", help="YYYY-MM-DD"),
    status: Optional[str] = typer.Option(None, "--status", help="running/completed 등"),
    limit: int = typer.Option(20, "--limit", "-n", help="조회할 최대 세션 수"),
) -> None:
    """기록된 작업 세션 목록 조회."""
    _run_command("session_list", {"date": date, "status": status, "limit": limit})


@app.command("session-show")
def session_show_cmd(
    session_id: str = typer.Argument(help="조회할 session_id"),
) -> None:
    """작업 세션 상세 조회."""
    _run_command("session_show", {"session_id": session_id})


@app.command("session-candidates")
def session_candidates_cmd(
    limit: int = typer.Option(20, "--limit", "-n", help="조회할 최대 후보 수"),
) -> None:
    """레시피 승인 후보 세션 조회."""
    _run_command("session_candidates", {"limit": limit})


@app.command("session-approve")
def session_approve_cmd(
    session_id: str = typer.Argument(help="승인할 session_id"),
    approved_by: str = typer.Option("cli", "--approved-by", help="승인자"),
    success_criteria_json: Optional[str] = typer.Option(
        None,
        "--replay-success-criteria",
        help="replay 성공 기준 JSON",
    ),
) -> None:
    """후보 세션을 recipe로 승인 저장."""
    import json as _json

    params: dict[str, Any] = {"session_id": session_id, "approved_by": approved_by}
    if success_criteria_json:
        params["replay_success_criteria"] = _json.loads(
            _json_arg(success_criteria_json, label="--success-criteria-json")
        )
    _run_command("session_approve", params)


# -------------------------------------------------------------------
# Recipe Commands
# -------------------------------------------------------------------

@app.command("recipe-save")
def recipe_save(
    recipe_id: str = typer.Argument(help="레시피 ID"),
    description: str = typer.Argument(help="레시피 설명"),
    steps_json: str = typer.Argument(help="단계 JSON 배열"),
    keywords: Optional[str] = typer.Option(None, "--keywords", "-k", help="검색 키워드 (쉼표 구분)"),
    context: Optional[str] = typer.Option(None, "--context", "-c", help="레시피 상세 맥락"),
    replay_success_criteria_json: Optional[str] = typer.Option(
        None,
        "--replay-success-criteria",
        help="replay 최종 검증 기준 JSON. 예: '{\"expected_tcode\":\"VA02\"}'",
    ),
) -> None:
    """AI 네비게이션 레시피 저장."""
    import json as _json
    params: dict[str, Any] = {
        "recipe_id": recipe_id,
        "description": description,
        "steps": _json.loads(steps_json),
    }
    if keywords:
        params["keywords"] = [k.strip() for k in keywords.split(",")]
    if context is not None:
        params["context"] = context
    if replay_success_criteria_json is not None:
        params["replay_success_criteria"] = _json.loads(
            _json_arg(replay_success_criteria_json, label="--replay-success-criteria-json")
        )
    _run_command("recipe_save", params)


@app.command("recipe-search")
def recipe_search(
    query: str = typer.Argument(help="검색어"),
) -> None:
    """레시피 검색."""
    _run_command("recipe_search", {"query": query})


@app.command("recipe-show")
def recipe_show(
    recipe_id: str = typer.Argument(help="조회할 레시피 ID"),
) -> None:
    """레시피 상세 실행 계획 조회."""
    _run_command("recipe_show", {"recipe_id": recipe_id})


@app.command("recipe-query")
def recipe_query(
    tcode: Optional[str] = typer.Option(None, "--tcode", "-t", help="트랜잭션 코드"),
    keywords: Optional[str] = typer.Option(None, "--keywords", "-k", help="검색 키워드"),
) -> None:
    """tcode와/or 키워드로 레시피 통합 검색."""
    params: dict[str, Any] = {}
    if tcode:
        params["tcode"] = tcode
    if keywords:
        params["keywords"] = keywords
    _run_command("recipe_query", params)


@app.command("recipe-list")
def recipe_list() -> None:
    """저장된 모든 레시피 목록."""
    _run_command("recipe_list", {})


@app.command("recipe-pending")
def recipe_pending(
    limit: int = typer.Option(20, "--limit", "-n", help="조회할 최대 후보 수"),
) -> None:
    """미승인 레시피 후보 세션 목록 조회."""
    _run_command("recipe_pending", {"limit": limit})


@app.command("recipe-history")
def recipe_history(
    recipe_id: str = typer.Argument(help="이력을 조회할 레시피 ID"),
) -> None:
    """레시피 버전 이력 조회."""
    _run_command("recipe_history", {"recipe_id": recipe_id})


@app.command("recipe-version-up")
def recipe_version_up(
    recipe_id: str = typer.Argument(help="버전업할 레시피 ID"),
    new_steps_json: str = typer.Argument(help="새 steps JSON 배열 또는 @steps.json"),
    new_context: str = typer.Option("", "--context", "-c", help="새 context. 비우면 기존 context 유지"),
    change_note: str = typer.Option("", "--change-note", "-m", help="변경 사유 메모"),
    replay_success_criteria_json: Optional[str] = typer.Option(
        None,
        "--replay-success-criteria",
        help="새 replay 검증 기준 JSON 또는 @criteria.json. '{}'를 주면 기준 초기화",
    ),
) -> None:
    """기존 레시피를 새 steps로 버전업하고 이전 버전을 archive에 보존."""
    params: dict[str, Any] = {
        "recipe_id": recipe_id,
        "new_steps": _load_json_argument(new_steps_json, label="new_steps"),
    }
    if new_context:
        params["new_context"] = new_context
    if change_note:
        params["change_note"] = change_note
    if replay_success_criteria_json is not None:
        params["replay_success_criteria"] = _load_json_argument(
            replay_success_criteria_json,
            label="replay_success_criteria",
        )
    _run_command("recipe_version_up", params)


@app.command("recipe-replay-plan")
def recipe_replay_plan(
    recipe_id: str = typer.Argument(help="replay 계획을 만들 레시피 ID"),
    current_tcode: str = typer.Option("", "--current-tcode", "-t", help="현재 SAP tcode. 제공 시 시작 조건과 비교"),
    current_screen: str = typer.Option("", "--current-screen", "-s", help="현재 화면 번호/요약 식별자"),
    allow_disabled: bool = typer.Option(False, "--allow-disabled", help="disabled 레시피도 blocked 처리 없이 계획 반환"),
) -> None:
    """레시피 실행 전 precheck/dry-run/verify/report 계획을 생성."""
    _run_command(
        "recipe_replay_plan",
        {
            "recipe_id": recipe_id,
            "current_tcode": current_tcode,
            "current_screen": current_screen,
            "allow_disabled": allow_disabled,
        },
    )


@app.command("recipe-replay-execute")
def recipe_replay_execute(
    recipe_id: str = typer.Argument(help="실행할 레시피 ID"),
    current_tcode: str = typer.Option("", "--current-tcode", "-t", help="현재 SAP tcode. 제공 시 시작 조건과 비교"),
    current_screen: str = typer.Option("", "--current-screen", "-s", help="현재 화면 번호/요약 식별자"),
    allow_disabled: bool = typer.Option(False, "--allow-disabled", help="disabled 레시피도 실행 허용"),
    allow_modal: bool = typer.Option(False, "--allow-modal", help="실행 후 modal dialog가 남아 있어도 verify 실패로 보지 않음"),
    auto_report: bool = typer.Option(True, "--auto-report/--no-auto-report", help="성공/실패 카운터 자동 기록 여부"),
    success_criteria_json: Optional[str] = typer.Option(
        None,
        "--success-criteria",
        help="실행 후 검증 기준 JSON 또는 @criteria.json",
    ),
    save_success_criteria: Optional[bool] = typer.Option(
        None,
        "--save-success-criteria/--no-save-success-criteria",
        help="success criteria를 recipe metadata에 저장할지 여부",
    ),
    observation_scope: str = typer.Option(
        "final",
        "--observation-scope",
        help="final/status/screen 중 하나. step별 관찰값 저장 범위",
    ),
) -> None:
    """레시피 replay 계획을 실제로 실행하고 결과를 자동 검증/보고."""
    params: dict[str, Any] = {
        "recipe_id": recipe_id,
        "current_tcode": current_tcode,
        "current_screen": current_screen,
        "allow_disabled": allow_disabled,
        "allow_modal": allow_modal,
        "auto_report": auto_report,
        "observation_scope": observation_scope,
    }
    if success_criteria_json is not None:
        params["success_criteria"] = _load_json_argument(
            success_criteria_json,
            label="success_criteria",
        )
    if save_success_criteria is not None:
        params["save_success_criteria"] = save_success_criteria
    _run_command("recipe_replay_execute", params, allow_daemon=True)


@app.command("recipe-chain")
def recipe_chain(
    tcodes: str = typer.Argument(help="검색할 tcode 목록 (쉼표 구분, 예: SPRO,MM03)"),
    match: str = typer.Option("all", "--match", "-m", help="all 또는 any"),
) -> None:
    """여러 tcode를 걸치는 복합 레시피 검색."""
    _run_command("recipe_chain", {"tcodes": tcodes, "match": match})


@app.command("recipe-success")
def recipe_success(
    recipe_id: str = typer.Argument(help="성공을 기록할 레시피 ID"),
) -> None:
    """레시피 실행 성공 기록."""
    _run_command("recipe_success", {"recipe_id": recipe_id})


@app.command("recipe-failure")
def recipe_failure(
    recipe_id: str = typer.Argument(help="실패를 기록할 레시피 ID"),
    reason: str = typer.Option("", "--reason", "-r", help="실패 사유"),
) -> None:
    """레시피 실행 실패 기록. 임계 도달 시 자동 비활성화될 수 있음."""
    params = {"recipe_id": recipe_id}
    if reason:
        params["reason"] = reason
    _run_command("recipe_failure", params)


@app.command("recipe-enable")
def recipe_enable(
    recipe_id: str = typer.Argument(help="재활성화할 레시피 ID"),
    reset_failure_count: bool = typer.Option(
        True,
        "--reset-failure-count/--keep-failure-count",
        help="재활성화하면서 실패 카운트를 초기화할지 여부",
    ),
) -> None:
    """비활성화된 레시피를 수동 재활성화."""
    _run_command(
        "recipe_enable",
        {
            "recipe_id": recipe_id,
            "reset_failure_count": reset_failure_count,
        },
    )


@app.command("recipe-delete")
def recipe_delete(
    recipe_id: str = typer.Argument(help="삭제할 레시피 ID"),
) -> None:
    """레시피 삭제."""
    _run_command("recipe_delete", {"recipe_id": recipe_id})


# -------------------------------------------------------------------
# Auth Diagnostic Commands
# -------------------------------------------------------------------

@app.command("data-dir")
def data_dir() -> None:
    """로컬 SAP CLI 데이터 위치를 출력."""
    from .data_diagnostics import data_locations

    _output_json(data_locations())


@app.command("data-status")
def data_status_cmd() -> None:
    """로컬 SAP CLI 데이터 저장 상태(파일 수/용량)를 출력."""
    from .data_diagnostics import data_status

    _output_json(data_status())


@app.command("data-backup")
def data_backup_cmd(
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="백업 아카이브 출력 경로"),
    archive_format: str = typer.Option("zip", "--format", help="백업 형식: zip 또는 tar"),
) -> None:
    """recipes/macros/ontology 데이터를 zip 또는 tar.gz로 백업."""
    from .data_diagnostics import data_backup

    try:
        _output_json(data_backup(output=output, archive_format=archive_format))
    except ValueError as e:
        _handle_error(e)


@app.command("data-clean")
def data_clean_cmd(
    older_than_days: int = typer.Option(
        30,
        "--older-than-days",
        help="이 일수보다 오래된 audit/transcript/session 파일을 정리 대상으로 선택",
    ),
    dry_run: bool = typer.Option(
        True,
        "--dry-run/--apply",
        help="기본은 삭제하지 않고 대상만 출력. 실제 삭제하려면 --apply 사용",
    ),
    category: Optional[list[str]] = typer.Option(
        None,
        "--category",
        "-c",
        help="정리 대상 카테고리(audit, transcripts, sessions). 반복 지정 가능",
    ),
) -> None:
    """오래된 audit/transcript/session 파일을 dry-run 또는 실제 삭제."""
    from .data_diagnostics import data_clean

    try:
        _output_json(
            data_clean(
                older_than_days=older_than_days,
                dry_run=dry_run,
                categories=category or None,
            )
        )
    except ValueError as e:
        _handle_error(e)


def _default_policy_path() -> Path:
    """load_policy()와 같은 기본 정책 파일 경로를 반환."""
    yaml_path = Path.home() / ".sap_cli" / "policy.yaml"
    if yaml_path.exists():
        return yaml_path
    json_path = yaml_path.with_suffix(".json")
    if json_path.exists():
        return json_path
    return yaml_path


def _load_auth_diagnostic(agent: str | None = None) -> tuple[Policy | None, PermissionChecker, str | None]:
    """진단 명령용 policy/checker/effective agent 구성."""
    policy = load_policy()
    checker = PermissionChecker(policy)
    configured_agent = _resolve_agent_name(policy, agent)
    if configured_agent:
        checker.set_agent(configured_agent)
    effective_agent = checker.agent_name or configured_agent
    return policy, checker, effective_agent


def _agent_relation_summary(policy: Policy | None, agent_name: str | None) -> list[dict[str, str]]:
    if not policy or not agent_name:
        return []
    agent_policy = policy.get_agent(agent_name)
    if not agent_policy:
        return []
    return [
        {"resource": binding.resource, "relation": binding.relation.value}
        for binding in agent_policy.relations
    ]


def _visible_mcp_tool_names(checker: PermissionChecker) -> list[str]:
    from .mcp_server import _visible_tool_names_for_checker

    return _visible_tool_names_for_checker(checker)


@auth_app.command("status")
def auth_status(
    agent: Optional[str] = typer.Option(None, "--agent", help="진단할 agent 이름 (미지정 시 SAP_CLI_AGENT 또는 active_agent)"),
) -> None:
    """현재 ReBAC 정책 로드 상태와 active/effective agent를 출력."""
    policy, checker, effective_agent = _load_auth_diagnostic(agent)
    visible_tools = _visible_mcp_tool_names(checker)
    policy_path = _default_policy_path()

    _output_json(
        {
            "policy_loaded": policy is not None,
            "policy_path": str(policy_path),
            "mode": "enforcing" if checker.is_enforcing else "allow_all",
            "active_agent": policy.active_agent if policy else None,
            "env_agent": os.getenv("SAP_CLI_AGENT") or None,
            "effective_agent": effective_agent,
            "agent_known": bool(policy and effective_agent and policy.get_agent(effective_agent)),
            "agents": sorted(policy.agents) if policy else [],
            "relations": _agent_relation_summary(policy, effective_agent),
            "visible_mcp_tool_count": len(visible_tools),
        }
    )


@auth_app.command("tools")
def auth_tools(
    agent: Optional[str] = typer.Option(None, "--agent", help="진단할 agent 이름 (미지정 시 SAP_CLI_AGENT 또는 active_agent)"),
) -> None:
    """현재 agent에 노출되는 MCP tool 목록을 출력."""
    policy, checker, effective_agent = _load_auth_diagnostic(agent)
    visible_tools = _visible_mcp_tool_names(checker)

    _output_json(
        {
            "policy_loaded": policy is not None,
            "mode": "enforcing" if checker.is_enforcing else "allow_all",
            "effective_agent": effective_agent,
            "visible_mcp_tools": visible_tools,
            "visible_mcp_tool_count": len(visible_tools),
        }
    )


@auth_app.command("check")
def auth_check(
    tool: str = typer.Argument(help="확인할 MCP tool 이름 (예: sap_recipe_query)"),
    agent: Optional[str] = typer.Option(None, "--agent", help="진단할 agent 이름 (미지정 시 SAP_CLI_AGENT 또는 active_agent)"),
) -> None:
    """특정 tool의 permission/resource/노출/허용 여부를 진단."""
    policy, checker, effective_agent = _load_auth_diagnostic(agent)
    visible_tools = set(_visible_mcp_tool_names(checker))
    permission = TOOL_PERMISSION_MAP.get(tool)
    check = checker.check_tool(tool, {})

    _output_json(
        {
            "tool": tool,
            "known_tool": permission is not None,
            "permission": permission.value if permission else None,
            "resource": checker.resolve_resource(tool, {}),
            "policy_loaded": policy is not None,
            "mode": "enforcing" if checker.is_enforcing else "allow_all",
            "effective_agent": effective_agent,
            "visible_on_mcp": tool in visible_tools,
            "allowed": check.allowed,
            "reason": check.reason,
        }
    )


@app.command("auth-status")
def auth_status_alias(
    agent: Optional[str] = typer.Option(None, "--agent", help="진단할 agent 이름 (미지정 시 SAP_CLI_AGENT 또는 active_agent)"),
) -> None:
    """`sap auth status`의 top-level 별칭."""
    auth_status(agent)


@app.command("auth-tools")
def auth_tools_alias(
    agent: Optional[str] = typer.Option(None, "--agent", help="진단할 agent 이름 (미지정 시 SAP_CLI_AGENT 또는 active_agent)"),
) -> None:
    """`sap auth tools`의 top-level 별칭."""
    auth_tools(agent)


@app.command("auth-check")
def auth_check_alias(
    tool: str = typer.Argument(help="확인할 MCP tool 이름 (예: sap_recipe_query)"),
    agent: Optional[str] = typer.Option(None, "--agent", help="진단할 agent 이름 (미지정 시 SAP_CLI_AGENT 또는 active_agent)"),
) -> None:
    """`sap auth check <tool>`의 top-level 별칭."""
    auth_check(tool, agent)


# -------------------------------------------------------------------
# Daemon Commands
# -------------------------------------------------------------------

@app.command("daemon")
def daemon_cmd(
    action: str = typer.Argument(help="start / stop / status / doctor / restart"),
) -> None:
    """데몬 프로세스 관리 (명령 실행 속도 최적화).

    start:   백그라운드 데몬 시작 (COM 연결 유지)
    stop:    데몬 종료
    status:  데몬 상태 확인 (미기동 시 원인 진단 포함)
    doctor:  실행 환경(인터프리터/venv, sap_cli·pywin32·mcp import, 로그 tail) 전체 진단
    restart: 종료 후 재기동
    """
    from . import daemon

    if action == "start":
        if daemon.is_running():
            _output_json({"daemon": "already running", "port": daemon.PORT})
            return
        ok, err = daemon.start_daemon_detailed()
        if ok:
            _output_json({"daemon": "started", "port": daemon.PORT})
        else:
            _output_json({"error": True, "message": err or "데몬 시작 실패", "doctor": daemon.doctor()})
            raise typer.Exit(1)

    elif action == "restart":
        daemon.stop_daemon()
        daemon.wait_until_stopped()  # 죽어가는 데몬을 already-running으로 오판 방지
        ok, err = daemon.start_daemon_detailed()
        if ok:
            _output_json({"daemon": "restarted", "port": daemon.PORT})
        else:
            _output_json({"error": True, "message": err or "데몬 재기동 실패", "doctor": daemon.doctor()})
            raise typer.Exit(1)

    elif action == "stop":
        if daemon.stop_daemon():
            _output_json({"daemon": "stopped"})
        else:
            _output_json({"error": True, "message": "데몬이 실행 중이지 않습니다"})

    elif action == "status":
        if daemon.is_running():
            result = daemon.send_command("ping")
            data = result.get("data", {}) if result else {}
            _output_json({"daemon": "running", "port": daemon.PORT, **data})
        else:
            # 미기동 시 왜 안 뜨는지(잘못된 venv/import 실패/로그) 함께 보여준다.
            _output_json({"daemon": "not running", **daemon.doctor()})

    elif action == "doctor":
        _output_json(daemon.doctor())

    else:
        _output_json({"error": True, "message": f"알 수 없는 액션: {action}. start/stop/status/doctor/restart 중 선택"})
        raise typer.Exit(1)


@app.command("shell")
def shell_cmd() -> None:
    """대화형 셸 모드 (REPL). 명령 실행 지연 없음."""
    from .shell import run_shell
    run_shell()


# -------------------------------------------------------------------
# Macro Commands
# -------------------------------------------------------------------

@app.command("record-start")
def record_start(name: str = typer.Argument(help="매크로 이름")) -> None:
    """매크로 녹화 시작."""
    _output_json(_dispatch_local("macro_control", {"command": "start", "name": name}))


@app.command("record-step")
def record_step(
    action: str = typer.Argument(help="액션 (set/press/enter/vkey/run/checkbox/radio/combo/tab/menu/read-screen/select-row/table-cell/table-doubleclick/click-cell/tree-doubleclick/ui-action)"),
    target: str = typer.Argument(help="대상 ID 또는 값"),
    value: str = typer.Argument(None, help="값 (set/vkey/checkbox/combo용)"),
    node_key: str = typer.Option("", "--node-key", help="트리 node_key"),
    column: str = typer.Option("", "--column", help="테이블/트리 컬럼"),
    row: int | None = typer.Option(None, "--row", help="테이블 행 번호 (0-based)"),
    description: str = typer.Option("", "--desc", "-d"),
) -> None:
    """매크로 단계 기록 + 실제 SAP 실행."""
    params: dict[str, Any] = {}
    if node_key:
        params["node_key"] = node_key
    if column:
        params["column"] = column
    if row is not None:
        params["row"] = row
    _output_json(
        _dispatch_local(
            "record_step",
            {
                "action": action,
                "target": target,
                "value": value,
                "params": params,
                "description": description,
            },
        )
    )


@app.command("record-undo")
def record_undo() -> None:
    """마지막 녹화 단계 취소."""
    _output_json(_dispatch_local("macro_control", {"command": "undo"}))


@app.command("record-status")
def record_status() -> None:
    """현재 녹화 상태 조회."""
    _output_json(_dispatch_local("macro_control", {"command": "status"}))


@app.command("record-finish")
def record_finish(
    name: str = typer.Option(None, "--name", "-n", help="매크로 이름 (변경 시)"),
    description: str = typer.Option("", "--desc", "-d"),
    variables: str = typer.Option(None, "--vars", "-v",
        help='변수 지정 JSON. 예: \'{"matnr": "100-100"}\' 또는 \'{"matnr": {"value": "100-100", "desc": "자재번호"}}\''),
    var: Optional[list[str]] = typer.Option(None, "--var", help="변수 지정 KEY=VALUE. 여러 번 사용 가능"),
) -> None:
    """녹화 종료 및 매크로 저장."""
    try:
        vars_dict: Any = None
        if variables:
            vars_dict = _parse_vars(variables)
        vars_dict = _merge_var_assignments(vars_dict, var)
        _output_json(
            _dispatch_local(
                "macro_control",
                {
                    "command": "finish",
                    "name": name,
                    "description": description,
                    "variables": vars_dict,
                },
            )
        )
    except json.JSONDecodeError as e:
        _handle_error(e)


@app.command("record-cancel")
def record_cancel() -> None:
    """녹화 취소."""
    _output_json(_dispatch_local("macro_control", {"command": "cancel"}))


@app.command("macro-list")
def macro_list() -> None:
    """저장된 매크로 목록."""
    _output_json(_dispatch_local("macro_list", {}))


@app.command("macro-show")
def macro_show(macro_id: str = typer.Argument(help="매크로 ID")) -> None:
    """매크로 상세 조회."""
    _output_json(_dispatch_local("macro_show", {"macro_id": macro_id}))


@app.command("macro-play")
def macro_play(
    macro_id: str = typer.Argument(help="매크로 ID"),
    variables: str = typer.Option(None, "--vars", "-v",
        help='변수 JSON. 예: \'{"material_number": "200-200"}\''),
    var: Optional[list[str]] = typer.Option(None, "--var", help="변수 KEY=VALUE. 여러 번 사용 가능"),
    dry_run: bool = typer.Option(False, "--dry-run", help="실제 실행 없이 프리뷰"),
) -> None:
    """매크로 재생."""
    try:
        vars_dict: Any = {}
        if variables:
            vars_dict = _parse_vars(variables)
        vars_dict = _merge_var_assignments(vars_dict, var)
        _output_json(
            _dispatch_local(
                "macro_play",
                {
                    "macro_id": macro_id,
                    "variables": vars_dict,
                    "dry_run": dry_run,
                },
            )
        )
    except json.JSONDecodeError as e:
        _handle_error(e)


@app.command("macro-delete")
def macro_delete(macro_id: str = typer.Argument(help="매크로 ID")) -> None:
    """매크로 삭제."""
    _output_json(_dispatch_local("macro_delete", {"macro_id": macro_id}))


# -------------------------------------------------------------------
# Ontology Commands
# -------------------------------------------------------------------

@app.command()
def ontology(
    tcode: Optional[str] = typer.Argument(None, help="트랜잭션 코드"),
    field: Optional[str] = typer.Option(None, "--field", "-f", help="필드 검색"),
    actions: bool = typer.Option(False, "--actions", "-a", help="가용 액션 조회"),
    list_all: bool = typer.Option(False, "--list", "-l", help="저장된 온톨로지 목록"),
) -> None:
    """온톨로지 조회 - 학습된 SAP 도메인 지식."""
    if tcode is None and not list_all:
        typer.echo("트랜잭션 코드를 지정하세요. 예: sap ontology MM03")
        raise typer.Exit(1)
    _output_json(
        _dispatch_local(
            "ontology_query",
            {
                "tcode": tcode,
                "field_search": field,
                "actions": actions,
                "list_all": list_all,
            },
        )
    )


@app.command("learn")
def learn_screen_cmd() -> None:
    """현재 화면을 온톨로지로 학습하여 저장."""
    _output_json(_dispatch_local("learn", {}))


@app.command("capture")
def capture_screen(
    label: Optional[str] = typer.Argument(None, help="스크린샷 라벨 (예: initial, after_input)"),
    output: Optional[str] = typer.Option(None, "--output", "-o", help="저장 경로 (기본: 자동 생성)"),
) -> None:
    """현재 SAP GUI 화면을 스크린샷으로 캡처."""
    _output_json(_dispatch_local("capture", {"label": label, "output": output}))


@app.command("relationships")
def relationships(
    field: Optional[str] = typer.Argument(None, help="SAP 필드명 (예: MATNR, WERKS)"),
    rebuild: bool = typer.Option(False, "--rebuild", "-r", help="전체 트랜잭션에서 레지스트리 재구축"),
) -> None:
    """필드 관계 조회 - 특정 필드가 사용되는 모든 트랜잭션/화면."""
    _output_json(
        _dispatch_local("ontology_relationships", {"field": field, "rebuild": rebuild})
    )


@app.command("depends")
def field_dependencies(
    field: Optional[str] = typer.Argument(None, help="SAP 필드명 (예: WERKS, KUNNR)"),
    rebuild: bool = typer.Option(False, "--rebuild", "-r", help="전체 트랜잭션에서 종속성 그래프 재구축"),
) -> None:
    """필드 종속성 조회 - WERKS → LGORT 같은 필드 간 종속/결정 관계."""
    _output_json(
        _dispatch_local("field_dependencies", {"field": field, "rebuild": rebuild})
    )


@app.command("flow")
def document_flow(
    tcode: Optional[str] = typer.Argument(None, help="트랜잭션 코드 (예: VA01, ME21N)"),
    rebuild: bool = typer.Option(False, "--rebuild", "-r", help="프로세스/트랜잭션에서 문서 흐름 재구축"),
) -> None:
    """문서 흐름 조회 - VA01 → VL01N → VF01 같은 트랜잭션 간 문서 관계."""
    _output_json(
        _dispatch_local("document_flow", {"tcode": tcode, "rebuild": rebuild})
    )


@app.command("rebuild")
def ontology_rebuild() -> None:
    """온톨로지 전체 재구축 — 필드 레지스트리 + 필드 종속성 + 문서 흐름 일괄 재생성."""
    _output_json(_dispatch_local("ontology_rebuild", {}))


@app.command("path")
def process_path(
    from_tcode: str = typer.Argument(help="출발 트랜잭션 코드 (예: VA01)"),
    to_tcode: str = typer.Argument(help="도착 트랜잭션 코드 (예: VF01)"),
) -> None:
    """문서 흐름 경로 탐색 - 두 트랜잭션 간 최단 경로를 BFS로 탐색."""
    _output_json(
        _dispatch_local("process_path", {"from_tcode": from_tcode, "to_tcode": to_tcode})
    )


@app.command("ontology-ask")
def ontology_ask(
    question: str = typer.Argument(help="자연어 질문 (예: 'VA01이 뭐야?', 'MATNR 어디서 사용?')"),
) -> None:
    """온톨로지 자연어 Q&A - 질문을 파싱하여 그래프 기반으로 답변."""
    _output_json(_dispatch_local("ontology_ask", {"question": question}))


@app.command("process-list")
def process_list_cmd() -> None:
    """등록된 업무 프로세스 목록 조회."""
    _run_command("process_list", {})


@app.command("process-show")
def process_show_cmd(
    process_id: str = typer.Argument(help="프로세스 ID"),
) -> None:
    """업무 프로세스 상세 조회."""
    _run_command("process_show", {"process_id": process_id})


@app.command("process-create")
def process_create_cmd(
    process_id: str = typer.Argument(help="프로세스 ID"),
    name: str = typer.Option(..., "--name", "-n", help="프로세스 이름"),
    definition_json: str = typer.Option(..., "--definition", "-d", help="프로세스 정의 JSON"),
) -> None:
    """새 업무 프로세스 등록."""
    import json as _json
    definition = _json.loads(definition_json)
    definition["id"] = process_id
    definition["name"] = name
    _run_command("process_create", {"definition": definition})


@app.command("process-delete")
def process_delete_cmd(
    process_id: str = typer.Argument(help="삭제할 프로세스 ID"),
) -> None:
    """업무 프로세스 삭제."""
    _run_command("process_delete", {"process_id": process_id})


@app.command("process-start")
def process_start_cmd(
    process_id: str = typer.Argument(help="실행할 프로세스 ID"),
) -> None:
    """업무 프로세스 실행 시작."""
    _run_command("process_start", {"process_id": process_id})


@app.command("process-step")
def process_step_cmd(
    step_number: int = typer.Argument(help="스텝 번호 (0-based)"),
    outputs_json: Optional[str] = typer.Option(None, "--outputs", "-o", help="스텝 출력값 JSON"),
) -> None:
    """프로세스 스텝 실행 결과 보고."""
    import json as _json
    params: dict[str, Any] = {"step_number": step_number}
    if outputs_json:
        params["outputs"] = _json.loads(outputs_json)
    _run_command("process_step", params)


@app.command("process-status")
def process_status_cmd() -> None:
    """현재 실행 중인 프로세스 상태 조회."""
    _run_command("process_status", {})


@app.command("captures")
def list_captures(
    tcode: Optional[str] = typer.Argument(None, help="트랜잭션 코드로 필터"),
) -> None:
    """저장된 스크린샷 목록 조회."""
    _output_json(_dispatch_local("capture_list", {"tcode": tcode}))


@app.command("annotate")
def annotate_field(
    tcode: str = typer.Argument(help="트랜잭션 코드 (예: MM03)"),
    field: str = typer.Argument(help="필드 ID, 이름, 또는 SAP 필드명 (예: RMMG1-MATNR, MATNR)"),
    desc: Optional[str] = typer.Option(None, "--desc", help="영문 설명"),
    desc_ko: Optional[str] = typer.Option(None, "--desc-ko", help="한글 설명"),
    synonyms: Optional[str] = typer.Option(None, "--synonyms", help="영문 동의어 (쉼표 구분)"),
    synonyms_ko: Optional[str] = typer.Option(None, "--synonyms-ko", help="한글 동의어 (쉼표 구분)"),
    validation: Optional[str] = typer.Option(None, "--validation", help="유효성 규칙"),
    domain_context: Optional[str] = typer.Option(None, "--domain-context", help="도메인 맥락"),
) -> None:
    """필드 시맨틱 정보를 수동으로 입력/수정 (영어/한국어)."""
    _output_json(
        _dispatch_local(
            "annotate",
            {
                "tcode": tcode,
                "field": field,
                "desc": desc,
                "desc_ko": desc_ko,
                "synonyms": synonyms,
                "synonyms_ko": synonyms_ko,
                "validation": validation,
                "domain_context": domain_context,
            },
        )
    )


@app.command("annotate-dependency")
def annotate_dependency(
    source: str = typer.Argument(help="결정하는 필드 (예: WERKS, KUNNR)"),
    target: str = typer.Argument(help="종속되는 필드 (예: LGORT, ZTERM)"),
    dep_type: str = typer.Option("value_determines", "--type", help="종속성 유형: value_determines, input_order, lookup, field_activation"),
    description: Optional[str] = typer.Option(None, "--desc", help="관계 설명"),
    observed_in: Optional[str] = typer.Option(None, "--observed-in", help="관찰된 트랜잭션 (쉼표 구분, 예: VA01,ME21N)"),
) -> None:
    """필드 종속 관계를 수동으로 추가/수정."""
    _output_json(
        _dispatch_local(
            "annotate_dependency",
            {
                "source": source,
                "target": target,
                "type": dep_type,
                "description": description,
                "observed_in": observed_in,
            },
        )
    )


if __name__ == "__main__":
    app()
