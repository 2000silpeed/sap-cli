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
import re
import sys
from typing import Any, Optional

import typer
from rich.console import Console

from .dispatcher import (
    ApprovalRequiredError,
    DispatchContext,
    PermissionDeniedError,
    dispatch_command,
)

app = typer.Typer(
    name="sap",
    help="SAP GUI Scripting CLI for AI Agents",
    no_args_is_help=True,
)
console = Console()


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


def _dispatch_local(cmd: str, args: dict) -> dict:
    try:
        return dispatch_command(cmd, args, DispatchContext(source="cli"))
    except ApprovalRequiredError as e:
        _handle_approval_required(e)
    except (PermissionDeniedError, ValueError, RuntimeError) as e:
        _handle_error(e)
    except Exception as e:
        _handle_error(e)
    raise typer.Exit(1)


def _run_command(cmd: str, args: dict, *, allow_daemon: bool = False) -> None:
    if allow_daemon and _try_daemon(cmd, args):
        return
    _output_json(_dispatch_local(cmd, args))


def _try_daemon(cmd: str, args: dict) -> bool:
    """데몬으로 명령 전송 시도. 성공하면 결과 출력 후 True 반환."""
    from . import daemon
    result = daemon.send_command(cmd, args)
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
) -> None:
    """실행 중인 SAP GUI에 연결."""
    _run_command(
        "connect",
        {"connection": connection, "session": session},
        allow_daemon=True,
    )


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
def run(tcode: str = typer.Argument(help="트랜잭션 코드 (예: MM03, VA01)")) -> None:
    """트랜잭션 실행."""
    _run_command("run", {"tcode": tcode}, allow_daemon=True)


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
) -> None:
    """테이블 데이터 읽기."""
    _run_command("table", {"element_id": element_id}, allow_daemon=True)


@app.command("table-select")
def table_select(
    element_id: str = typer.Argument(help="테이블 ID"),
    row: int = typer.Argument(help="선택할 행 번호 (0-based)"),
) -> None:
    """테이블에서 행 선택 (MM03 뷰 선택 등)."""
    _run_command("table_select", {"element_id": element_id, "row": row}, allow_daemon=True)


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


if __name__ == "__main__":
    app()
