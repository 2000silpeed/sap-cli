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


@app.callback()
def _main_callback(
    slot: str = typer.Option("ses0", "--slot", help="세션 슬롯 ID (병렬 작업용, 모든 명령에 적용)"),
) -> None:
    global _global_slot_id
    _global_slot_id = slot


def _parse_vars(raw: str) -> dict[str, Any]:
    """다양한 형식의 --vars 값을 dict로 파싱.

    지원: '{"k":"v"}', "{'k':'v'}", "{k:v}", "k:v,k2:v2"
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
        if ":" not in pair:
            continue
        k, v = pair.split(":", 1)
        result[k.strip().strip("'\"")] = v.strip().strip("'\"")
    if not result:
        raise ValueError(f"변수 파싱 실패: {raw}")
    return result


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


def _dispatch_local(cmd: str, args: dict, slot_id: str = "ses0") -> dict:
    try:
        return dispatch_command(cmd, args, DispatchContext(source="cli", slot_id=slot_id))
    except ApprovalRequiredError as e:
        _handle_approval_required(e)
    except (PermissionDeniedError, ValueError, RuntimeError) as e:
        _handle_error(e)
    except Exception as e:
        _handle_error(e)
    raise typer.Exit(1)


def _run_command(cmd: str, args: dict, *, allow_daemon: bool = False, slot_id: str | None = None) -> None:
    effective_slot = slot_id or _global_slot_id
    if allow_daemon and _try_daemon(cmd, args, slot_id=effective_slot):
        return
    _output_json(_dispatch_local(cmd, args, slot_id=effective_slot))


def _try_daemon(cmd: str, args: dict, slot_id: str = "ses0") -> bool:
    """데몬으로 명령 전송 시도. 성공하면 결과 출력 후 True 반환."""
    from . import daemon
    result = daemon.send_command(cmd, args, context={"slot_id": slot_id})
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
        _output_json({"error": True, "message": result["error"]})
        raise typer.Exit(1)
    return True


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
    element_id: str = typer.Argument(help="필드 ID (예: wnd[0]/usr/ctxtRMMG1-MATNR)"),
    value: str = typer.Argument(help="설정할 값"),
) -> None:
    """필드에 값 설정."""
    _run_command(
        "set",
        {"element_id": element_id, "value": value},
        allow_daemon=True,
    )


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
        params["replay_success_criteria"] = _json.loads(replay_success_criteria_json)
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
    action: str = typer.Argument(help="start / stop / status"),
) -> None:
    """데몬 프로세스 관리 (명령 실행 속도 최적화).

    start:  백그라운드 데몬 시작 (COM 연결 유지)
    stop:   데몬 종료
    status: 데몬 상태 확인
    """
    from . import daemon

    if action == "start":
        if daemon.is_running():
            _output_json({"daemon": "already running", "port": daemon.PORT})
            return
        ok = daemon.start_daemon()
        if ok:
            _output_json({"daemon": "started", "port": daemon.PORT})
        else:
            _output_json({"error": True, "message": "데몬 시작 실패"})
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
            _output_json({"daemon": "not running"})

    else:
        _output_json({"error": True, "message": f"알 수 없는 액션: {action}. start/stop/status 중 선택"})
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
    action: str = typer.Argument(help="액션 (set/press/vkey/run/checkbox/radio/combo/tab/menu)"),
    target: str = typer.Argument(help="대상 ID 또는 값"),
    value: str = typer.Argument(None, help="값 (set/vkey/checkbox/combo용)"),
    description: str = typer.Option("", "--desc", "-d"),
) -> None:
    """매크로 단계 기록 + 실제 SAP 실행."""
    _output_json(
        _dispatch_local(
            "record_step",
            {
                "action": action,
                "target": target,
                "value": value,
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
) -> None:
    """녹화 종료 및 매크로 저장."""
    try:
        vars_dict: dict[str, str] | None = None
        if variables:
            vars_dict = _parse_vars(variables)
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
    dry_run: bool = typer.Option(False, "--dry-run", help="실제 실행 없이 프리뷰"),
) -> None:
    """매크로 재생."""
    try:
        vars_dict: dict[str, str] = {}
        if variables:
            vars_dict = _parse_vars(variables)
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
