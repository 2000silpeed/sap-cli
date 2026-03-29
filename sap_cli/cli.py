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
"""

from __future__ import annotations

import json
import sys
from typing import Optional

import typer
from rich.console import Console

from .com_wrapper import SapGuiError, SapGuiWrapper
from .macro.schema import Macro, MacroVariable
from .macro.player import MacroPlayer
from .service import (
    get_sap as _get_sap,
    get_store as _get_store,
    get_recorder as _get_recorder,
    get_macro_store as _get_macro_store,
    map_action as _map_action,
    execute_sap_action as _execute_sap_action,
)

app = typer.Typer(
    name="sap",
    help="SAP GUI Scripting CLI for AI Agents",
    no_args_is_help=True,
)
console = Console()


def _output_json(data: dict | list) -> None:
    """JSON 출력 (AI 파싱 최적화)."""
    print(json.dumps(data, ensure_ascii=False, indent=2))


def _handle_error(e: Exception) -> None:
    result = {"error": True, "message": str(e)}
    _output_json(result)
    raise typer.Exit(1)


# -------------------------------------------------------------------
# Commands
# -------------------------------------------------------------------

@app.command()
def connect(
    connection: int = typer.Option(0, "--connection", "-c", help="연결 인덱스"),
    session: int = typer.Option(0, "--session", "-s", help="세션 인덱스"),
) -> None:
    """실행 중인 SAP GUI에 연결."""
    try:
        sap = SapGuiWrapper()
        info = sap.connect(connection, session)
        global _sap
        _sap = sap
        _output_json({
            "connected": True,
            "session": info.model_dump(mode="json"),
        })
    except SapGuiError as e:
        _handle_error(e)


@app.command("session")
def session_info() -> None:
    """현재 세션 정보 조회."""
    try:
        sap = _get_sap()
        info = sap.get_session_info()
        _output_json(info.model_dump(mode="json"))
    except SapGuiError as e:
        _handle_error(e)


@app.command()
def screen(
    fields_only: bool = typer.Option(False, "--fields-only", "-f", help="입력 가능 필드만"),
    actions_only: bool = typer.Option(False, "--actions-only", "-a", help="가능한 액션만"),
) -> None:
    """현재 화면의 UI 구조를 JSON으로 출력."""
    try:
        sap = _get_sap()
        ontology = sap.get_screen()

        if fields_only:
            fields = ontology.get_changeable_fields()
            _output_json({
                "transaction": ontology.transaction,
                "screen_number": ontology.screen_number,
                "changeable_fields": [f.model_dump(mode="json") for f in fields],
            })
        elif actions_only:
            actions = ontology.get_available_actions()
            _output_json({
                "transaction": ontology.transaction,
                "screen_number": ontology.screen_number,
                "available_actions": [a.model_dump(mode="json") for a in actions],
            })
        else:
            _output_json(ontology.model_dump(mode="json"))
    except SapGuiError as e:
        _handle_error(e)


@app.command()
def run(tcode: str = typer.Argument(help="트랜잭션 코드 (예: MM03, VA01)")) -> None:
    """트랜잭션 실행."""
    try:
        sap = _get_sap()
        sap.run_transaction(tcode)
        info = sap.get_session_info()
        status = sap.get_status()
        _output_json({
            "executed": tcode,
            "current_transaction": info.transaction,
            "screen_number": info.screen_number,
            "status": status.model_dump(mode="json"),
        })
    except SapGuiError as e:
        _handle_error(e)


@app.command("set")
def set_field(
    element_id: str = typer.Argument(help="필드 ID (예: wnd[0]/usr/ctxtRMMG1-MATNR)"),
    value: str = typer.Argument(help="설정할 값"),
) -> None:
    """필드에 값 설정."""
    try:
        sap = _get_sap()
        sap.set_field(element_id, value)
        actual = sap.get_field(element_id)
        _output_json({
            "field": element_id,
            "value_set": value,
            "actual_value": actual,
        })
    except SapGuiError as e:
        _handle_error(e)


@app.command("get")
def get_field(
    element_id: str = typer.Argument(help="필드 ID"),
) -> None:
    """필드 값 읽기."""
    try:
        sap = _get_sap()
        value = sap.get_field(element_id)
        _output_json({
            "field": element_id,
            "value": value,
        })
    except SapGuiError as e:
        _handle_error(e)


@app.command()
def press(
    element_id: str = typer.Argument(help="버튼 ID (예: wnd[0]/tbar[0]/btn[0])"),
) -> None:
    """버튼 클릭."""
    try:
        sap = _get_sap()
        sap.press(element_id)
        status = sap.get_status()
        info = sap.get_session_info()
        _output_json({
            "pressed": element_id,
            "screen_number": info.screen_number,
            "status": status.model_dump(mode="json"),
        })
    except SapGuiError as e:
        _handle_error(e)


@app.command()
def vkey(
    key: int = typer.Argument(help="VKey 번호 (0=Enter, 3=Back, 8=Execute, 11=Save, 12=Cancel)"),
) -> None:
    """VKey 전송."""
    try:
        sap = _get_sap()
        sap.send_vkey(key)
        status = sap.get_status()
        info = sap.get_session_info()
        _output_json({
            "vkey_sent": key,
            "screen_number": info.screen_number,
            "transaction": info.transaction,
            "status": status.model_dump(mode="json"),
        })
    except SapGuiError as e:
        _handle_error(e)


@app.command()
def status() -> None:
    """상태바 메시지 조회."""
    try:
        sap = _get_sap()
        msg = sap.get_status()
        _output_json(msg.model_dump(mode="json"))
    except SapGuiError as e:
        _handle_error(e)


@app.command()
def table(
    element_id: str = typer.Argument(help="테이블/그리드 ID"),
) -> None:
    """테이블 데이터 읽기."""
    try:
        sap = _get_sap()
        tbl = sap.read_table(element_id)
        _output_json(tbl.model_dump(mode="json"))
    except SapGuiError as e:
        _handle_error(e)


@app.command()
def command(
    cmd: str = typer.Argument(help="SAP 커맨드 (예: /nMM03, /oSE80)"),
) -> None:
    """커맨드 필드에 명령 실행."""
    try:
        sap = _get_sap()
        sap.send_command(cmd)
        info = sap.get_session_info()
        status = sap.get_status()
        _output_json({
            "command": cmd,
            "transaction": info.transaction,
            "screen_number": info.screen_number,
            "status": status.model_dump(mode="json"),
        })
    except SapGuiError as e:
        _handle_error(e)


@app.command()
def modal() -> None:
    """모달 윈도우(팝업) 정보 조회."""
    try:
        sap = _get_sap()
        has = sap.has_modal()
        result: dict = {"has_modal": has}
        if has:
            result["text"] = sap.get_modal_text()
        _output_json(result)
    except SapGuiError as e:
        _handle_error(e)


# -------------------------------------------------------------------
# Macro Commands
# -------------------------------------------------------------------

@app.command("record-start")
def record_start(name: str = typer.Argument(help="매크로 이름")) -> None:
    """매크로 녹화 시작."""
    try:
        recorder = _get_recorder()
        recorder.start(name)
        _output_json({"recording": True, "name": name})
    except RuntimeError as e:
        _handle_error(e)


@app.command("record-step")
def record_step(
    action: str = typer.Argument(help="액션 (set/press/vkey/run/checkbox/radio/combo/tab/menu)"),
    target: str = typer.Argument(help="대상 ID 또는 값"),
    value: str = typer.Argument(None, help="값 (set/vkey/checkbox/combo용)"),
    description: str = typer.Option("", "--desc", "-d"),
) -> None:
    """매크로 단계 기록 + 실제 SAP 실행."""
    try:
        sap = _get_sap()
        recorder = _get_recorder()
        action_type = _map_action(action)

        # 실제 SAP 실행
        _execute_sap_action(sap, action.lower(), target, value)

        # 실행 후 상태/화면 정보
        status = sap.get_status()
        info = sap.get_session_info()

        # 녹화 기록: run/vkey는 target이 실행 값이므로 value에도 저장
        record_value = value
        if action.lower() in ("run", "vkey") and not value:
            record_value = target

        step = recorder.record_step(
            action=action_type,
            target_id=target,
            value=record_value,
            description=description,
            screen_number=info.screen_number,
        )

        _output_json({
            "recorded": True,
            "step_number": step.step_number,
            "action": action,
            "target": target,
            "value": value,
            "screen_number": info.screen_number,
            "status": status.model_dump(mode="json"),
        })
    except (SapGuiError, RuntimeError, ValueError) as e:
        _handle_error(e)


@app.command("record-undo")
def record_undo() -> None:
    """마지막 녹화 단계 취소."""
    try:
        recorder = _get_recorder()
        removed = recorder.undo_last()
        if removed:
            _output_json({
                "undone": True,
                "removed_step": removed.step_number,
                "action": removed.action.value,
                "remaining_steps": len(recorder.current_steps),
            })
        else:
            _output_json({"undone": False, "message": "취소할 단계가 없습니다."})
    except RuntimeError as e:
        _handle_error(e)


@app.command("record-status")
def record_status() -> None:
    """현재 녹화 상태 조회."""
    recorder = _get_recorder()
    steps = recorder.current_steps
    _output_json({
        "recording": recorder.is_recording,
        "steps_count": len(steps),
        "steps": [
            {
                "step_number": s.step_number,
                "action": s.action.value,
                "target_id": s.target_id,
                "value": s.value,
            }
            for s in steps
        ],
    })


@app.command("record-finish")
def record_finish(
    name: str = typer.Option(None, "--name", "-n", help="매크로 이름 (변경 시)"),
    description: str = typer.Option("", "--desc", "-d"),
    variables: str = typer.Option(None, "--vars", "-v",
        help='변수 지정 JSON. 예: \'{"material_number": "자재번호"}\''),
) -> None:
    """녹화 종료 및 매크로 저장."""
    try:
        recorder = _get_recorder()
        store = _get_macro_store()

        vars_dict: dict[str, str] | None = None
        if variables:
            vars_dict = json.loads(variables)

        macro_name = name or recorder._name
        macro = recorder.finish(
            name=macro_name,
            description=description,
            variables=vars_dict,
        )

        path = store.save(macro)
        _output_json({
            "saved": True,
            "macro_id": macro.id,
            "name": macro.name,
            "steps_count": len(macro.steps),
            "variables": [v.model_dump(mode="json") for v in macro.variables],
            "path": str(path),
        })
    except (RuntimeError, json.JSONDecodeError) as e:
        _handle_error(e)


@app.command("record-cancel")
def record_cancel() -> None:
    """녹화 취소."""
    recorder = _get_recorder()
    recorder.cancel()
    _output_json({"cancelled": True})


@app.command("macro-list")
def macro_list() -> None:
    """저장된 매크로 목록."""
    store = _get_macro_store()
    macros = store.list_macros()
    _output_json({"macros": macros, "count": len(macros)})


@app.command("macro-show")
def macro_show(macro_id: str = typer.Argument(help="매크로 ID")) -> None:
    """매크로 상세 조회."""
    store = _get_macro_store()
    macro = store.load(macro_id)
    if macro:
        _output_json(macro.model_dump(mode="json"))
    else:
        _output_json({"error": True, "message": f"매크로 '{macro_id}'를 찾을 수 없습니다."})


@app.command("macro-play")
def macro_play(
    macro_id: str = typer.Argument(help="매크로 ID"),
    variables: str = typer.Option(None, "--vars", "-v",
        help='변수 JSON. 예: \'{"material_number": "200-200"}\''),
    dry_run: bool = typer.Option(False, "--dry-run", help="실제 실행 없이 프리뷰"),
) -> None:
    """매크로 재생."""
    try:
        store = _get_macro_store()
        macro = store.load(macro_id)
        if not macro:
            _output_json({"error": True, "message": f"매크로 '{macro_id}'를 찾을 수 없습니다."})
            raise typer.Exit(1)

        vars_dict: dict[str, str] = {}
        if variables:
            vars_dict = json.loads(variables)

        sap = _get_sap()
        player = MacroPlayer(sap)

        if dry_run:
            result = player.dry_run(macro, vars_dict)
        else:
            result = player.play(macro, vars_dict)

        _output_json(result.model_dump(mode="json"))
    except (SapGuiError, json.JSONDecodeError) as e:
        _handle_error(e)


@app.command("macro-delete")
def macro_delete(macro_id: str = typer.Argument(help="매크로 ID")) -> None:
    """매크로 삭제."""
    store = _get_macro_store()
    deleted = store.delete(macro_id)
    _output_json({"deleted": deleted, "macro_id": macro_id})


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
    store = _get_store()

    if list_all:
        tcodes = store.list_transactions()
        patterns = store.list_patterns()
        _output_json({
            "transactions": tcodes,
            "patterns": patterns,
        })
        return

    if tcode is None:
        typer.echo("트랜잭션 코드를 지정하세요. 예: sap ontology MM03")
        raise typer.Exit(1)

    if field:
        result = store.query_field(tcode, field)
        if result:
            _output_json(result)
        else:
            _output_json({"error": True, "message": f"필드 '{field}'를 찾을 수 없습니다."})
        return

    if actions:
        result = store.query_actions(tcode)
        _output_json({"transaction": tcode, "actions": result})
        return

    ont = store.load_transaction(tcode)
    if ont:
        _output_json(ont.model_dump(mode="json"))
    else:
        _output_json({"error": True, "message": f"'{tcode}' 온톨로지가 없습니다."})


@app.command("learn")
def learn_screen_cmd() -> None:
    """현재 화면을 온톨로지로 학습하여 저장."""
    try:
        sap = _get_sap()
        store = _get_store()
        screen_ont = sap.get_screen()
        store.save_screen(screen_ont.transaction, screen_ont)
        _output_json({
            "learned": True,
            "transaction": screen_ont.transaction,
            "screen_number": screen_ont.screen_number,
            "fields_count": len(screen_ont.fields),
            "buttons_count": len(screen_ont.buttons),
        })
    except SapGuiError as e:
        _handle_error(e)


if __name__ == "__main__":
    app()
