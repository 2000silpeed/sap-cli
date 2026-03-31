"""SAP GUI MCP Server - Claude Code에서 SAP를 직접 조작할 수 있는 MCP 도구.

Claude Code의 settings.json에 다음을 추가하여 사용:
{
  "mcpServers": {
    "sap-gui": {
      "command": "python",
      "args": ["-m", "sap_cli.mcp_server"]
    }
  }
}
"""

from __future__ import annotations

import json
import sys
from typing import Any

try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import TextContent, Tool
    HAS_MCP = True
except ImportError:
    HAS_MCP = False

from .auth.checker import PermissionChecker
from .auth.audit import AuditLogger
from .auth.policy import load_policy, TOOL_PERMISSION_MAP
from .com_wrapper import SapGuiError, SapGuiWrapper
from .macro.player import MacroPlayer
from .service import (
    ACTION_MAP as _ACTION_MAP,
    get_sap as _get_sap,
    get_store as _get_store,
    get_learner as _get_learner,
    get_recorder as _get_recorder,
    get_macro_store as _get_macro_store,
    map_action as _map_action,
    execute_sap_action as _execute_sap_action,
)

# 권한 체크 초기화 (정책 파일 없으면 전체 허용)
_policy = load_policy()
_checker = PermissionChecker(_policy)
_auditor = AuditLogger(_policy.audit if _policy else None)


def _json_result(data: Any) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps(data, ensure_ascii=False, indent=2))]


def _error_result(msg: str) -> list[TextContent]:
    return _json_result({"error": True, "message": msg})


# -------------------------------------------------------------------
# Tool Definitions
# -------------------------------------------------------------------

TOOLS: list = []

if HAS_MCP:
    TOOLS = [
    Tool(
        name="sap_connect",
        description="실행 중인 SAP GUI에 연결합니다. SAP GUI가 이미 실행 중이고 로그인된 상태여야 합니다.",
        inputSchema={
            "type": "object",
            "properties": {
                "connection_index": {"type": "integer", "default": 0, "description": "연결 인덱스 (기본: 0)"},
                "session_index": {"type": "integer", "default": 0, "description": "세션 인덱스 (기본: 0)"},
            },
        },
    ),
    Tool(
        name="sap_session",
        description="현재 SAP 세션 정보를 조회합니다 (사용자, 트랜잭션, 화면번호, 시스템명 등).",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="sap_screen",
        description="현재 SAP 화면의 전체 UI 구조를 JSON으로 반환합니다. 필드, 버튼, 탭, 테이블 등 모든 요소를 포함합니다.",
        inputSchema={
            "type": "object",
            "properties": {
                "fields_only": {"type": "boolean", "default": False, "description": "입력 가능한 필드만 반환"},
                "actions_only": {"type": "boolean", "default": False, "description": "가능한 액션만 반환"},
            },
        },
    ),
    Tool(
        name="sap_run",
        description="SAP 트랜잭션을 실행합니다. 예: MM03(자재조회), VA01(수주생성), ME21N(구매오더).",
        inputSchema={
            "type": "object",
            "properties": {
                "tcode": {"type": "string", "description": "트랜잭션 코드 (예: MM03)"},
            },
            "required": ["tcode"],
        },
    ),
    Tool(
        name="sap_set_field",
        description="SAP 화면의 필드에 값을 설정합니다.",
        inputSchema={
            "type": "object",
            "properties": {
                "element_id": {"type": "string", "description": "필드 ID (예: wnd[0]/usr/ctxtRMMG1-MATNR)"},
                "value": {"type": "string", "description": "설정할 값"},
            },
            "required": ["element_id", "value"],
        },
    ),
    Tool(
        name="sap_get_field",
        description="SAP 화면 필드의 현재 값을 읽습니다.",
        inputSchema={
            "type": "object",
            "properties": {
                "element_id": {"type": "string", "description": "필드 ID"},
            },
            "required": ["element_id"],
        },
    ),
    Tool(
        name="sap_press",
        description="SAP 화면의 버튼을 클릭합니다.",
        inputSchema={
            "type": "object",
            "properties": {
                "element_id": {"type": "string", "description": "버튼 ID (예: wnd[0]/tbar[0]/btn[0])"},
            },
            "required": ["element_id"],
        },
    ),
    Tool(
        name="sap_send_vkey",
        description="SAP GUI에 키보드 키를 전송합니다. 주요 VKey: 0=Enter, 3=Back, 8=Execute(F8), 11=Save(Ctrl+S), 12=Cancel.",
        inputSchema={
            "type": "object",
            "properties": {
                "vkey": {"type": "integer", "description": "VKey 번호"},
            },
            "required": ["vkey"],
        },
    ),
    Tool(
        name="sap_get_status",
        description="SAP 상태바의 메시지를 조회합니다. 타입: S=성공, W=경고, E=에러, I=정보, A=중단.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="sap_table_read",
        description="SAP 화면의 테이블/그리드 데이터를 JSON으로 읽습니다.",
        inputSchema={
            "type": "object",
            "properties": {
                "element_id": {"type": "string", "description": "테이블/그리드 ID"},
            },
            "required": ["element_id"],
        },
    ),
    Tool(
        name="sap_ontology_query",
        description="학습된 SAP 온톨로지를 조회합니다. 트랜잭션의 화면 구조, 필드, 가능한 액션 등 도메인 지식을 반환합니다.",
        inputSchema={
            "type": "object",
            "properties": {
                "tcode": {"type": "string", "description": "트랜잭션 코드"},
                "field_search": {"type": "string", "description": "필드명/라벨 검색 (선택)"},
                "list_all": {"type": "boolean", "default": False, "description": "저장된 모든 온톨로지 목록"},
            },
        },
    ),
    Tool(
        name="sap_learn",
        description="현재 SAP 화면을 스캔하여 온톨로지로 학습합니다. 학습된 정보는 이후 sap_ontology_query로 조회할 수 있습니다.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="sap_macro_record_step",
        description="매크로 녹화 중 단계를 기록하면서 동시에 SAP에 실행합니다. record_start를 먼저 호출해야 합니다.",
        inputSchema={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["set", "press", "vkey", "run", "checkbox", "radio", "combo", "tab", "menu"]},
                "target": {"type": "string", "description": "대상 ID 또는 값"},
                "value": {"type": "string", "description": "값 (set/vkey/checkbox/combo용)"},
                "description": {"type": "string", "description": "단계 설명"},
            },
            "required": ["action", "target"],
        },
    ),
    Tool(
        name="sap_macro_control",
        description="매크로 녹화를 제어합니다 (시작/종료/취소/상태/실행취소).",
        inputSchema={
            "type": "object",
            "properties": {
                "command": {"type": "string", "enum": ["start", "finish", "cancel", "status", "undo"]},
                "name": {"type": "string", "description": "매크로 이름 (start/finish 시)"},
                "description": {"type": "string"},
                "variables": {"type": "object", "description": "변수 지정 (finish 시). {name: description}"},
            },
            "required": ["command"],
        },
    ),
    Tool(
        name="sap_macro_play",
        description="저장된 매크로를 변수 값을 바꿔서 재생합니다.",
        inputSchema={
            "type": "object",
            "properties": {
                "macro_id": {"type": "string", "description": "매크로 ID"},
                "variables": {"type": "object", "description": "변수 값. {name: value}"},
                "dry_run": {"type": "boolean", "default": False},
            },
            "required": ["macro_id"],
        },
    ),
    Tool(
        name="sap_macro_list",
        description="저장된 매크로 목록을 조회합니다. 트랜잭션 코드로 필터링 가능.",
        inputSchema={
            "type": "object",
            "properties": {
                "tcode": {"type": "string", "description": "트랜잭션 코드로 필터링 (선택)"},
            },
        },
    ),
]


# -------------------------------------------------------------------
# Tool Handlers
# -------------------------------------------------------------------

def handle_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    # ReBAC 권한 체크
    check = _checker.check_tool(name, arguments)
    perm = TOOL_PERMISSION_MAP.get(name, "unknown")
    if not check:
        _auditor.log(
            agent=_checker._agent_name,
            tool_name=name,
            arguments=arguments,
            allowed=False,
            reason=check.reason,
            permission=perm if isinstance(perm, str) else perm.value,
        )
        return _error_result(f"권한 없음: {check.reason}")

    # 승인 필요 여부 체크
    approval_reason = _checker.requires_approval(name, arguments)
    if approval_reason:
        _auditor.log(
            agent=_checker._agent_name,
            tool_name=name,
            arguments=arguments,
            allowed=False,
            reason=f"approval_required: {approval_reason}",
            permission=perm if isinstance(perm, str) else perm.value,
        )
        return _json_result({
            "approval_required": True,
            "tool": name,
            "reason": approval_reason,
        })

    # 감사 로그 (허용된 호출)
    _auditor.log(
        agent=_checker._agent_name,
        tool_name=name,
        arguments=arguments,
        allowed=True,
        reason=check.reason,
        permission=perm if isinstance(perm, str) else perm.value,
    )

    try:
        if name == "sap_connect":
            import sap_cli.service as _svc
            sap = SapGuiWrapper()
            info = sap.connect(
                arguments.get("connection_index", 0),
                arguments.get("session_index", 0),
            )
            _svc._sap = sap
            return _json_result({"connected": True, "session": info.model_dump(mode="json")})

        elif name == "sap_session":
            info = _get_sap().get_session_info()
            return _json_result(info.model_dump(mode="json"))

        elif name == "sap_screen":
            sap = _get_sap()
            screen = sap.get_screen()
            if arguments.get("fields_only"):
                fields = screen.get_changeable_fields()
                return _json_result({
                    "transaction": screen.transaction,
                    "screen_number": screen.screen_number,
                    "changeable_fields": [f.model_dump(mode="json") for f in fields],
                })
            elif arguments.get("actions_only"):
                actions = screen.get_available_actions()
                return _json_result({
                    "transaction": screen.transaction,
                    "screen_number": screen.screen_number,
                    "available_actions": [a.model_dump(mode="json") for a in actions],
                })
            return _json_result(screen.model_dump(mode="json"))

        elif name == "sap_run":
            sap = _get_sap()
            sap.run_transaction(arguments["tcode"])
            info = sap.get_session_info()
            status = sap.get_status()
            return _json_result({
                "executed": arguments["tcode"],
                "current_transaction": info.transaction,
                "screen_number": info.screen_number,
                "status": status.model_dump(mode="json"),
            })

        elif name == "sap_set_field":
            sap = _get_sap()
            sap.set_field(arguments["element_id"], arguments["value"])
            actual = sap.get_field(arguments["element_id"])
            return _json_result({
                "field": arguments["element_id"],
                "value_set": arguments["value"],
                "actual_value": actual,
            })

        elif name == "sap_get_field":
            value = _get_sap().get_field(arguments["element_id"])
            return _json_result({"field": arguments["element_id"], "value": value})

        elif name == "sap_press":
            sap = _get_sap()
            sap.press(arguments["element_id"])
            info = sap.get_session_info()
            status = sap.get_status()
            return _json_result({
                "pressed": arguments["element_id"],
                "screen_number": info.screen_number,
                "status": status.model_dump(mode="json"),
            })

        elif name == "sap_send_vkey":
            sap = _get_sap()
            sap.send_vkey(arguments["vkey"])
            info = sap.get_session_info()
            status = sap.get_status()
            return _json_result({
                "vkey_sent": arguments["vkey"],
                "screen_number": info.screen_number,
                "transaction": info.transaction,
                "status": status.model_dump(mode="json"),
            })

        elif name == "sap_get_status":
            status = _get_sap().get_status()
            return _json_result(status.model_dump(mode="json"))

        elif name == "sap_table_read":
            tbl = _get_sap().read_table(arguments["element_id"])
            return _json_result(tbl.model_dump(mode="json"))

        elif name == "sap_ontology_query":
            store = _get_store()
            if arguments.get("list_all"):
                return _json_result({
                    "transactions": store.list_transactions(),
                    "patterns": store.list_patterns(),
                })
            tcode = arguments.get("tcode", "")
            if not tcode:
                return _error_result("tcode를 지정하세요.")
            if field_search := arguments.get("field_search"):
                result = store.query_field(tcode, field_search)
                return _json_result(result or {"error": True, "message": "필드를 찾을 수 없습니다."})
            ont = store.load_transaction(tcode)
            if ont:
                return _json_result(ont.model_dump(mode="json"))
            return _error_result(f"'{tcode}' 온톨로지가 없습니다.")

        elif name == "sap_learn":
            learner = _get_learner()
            screen = learner.learn_current_screen()
            return _json_result({
                "learned": True,
                "transaction": screen.transaction,
                "screen_number": screen.screen_number,
                "fields_count": len(screen.fields),
                "buttons_count": len(screen.buttons),
            })

        elif name == "sap_macro_record_step":
            sap = _get_sap()
            recorder = _get_recorder()
            action = arguments["action"]
            target = arguments["target"]
            value = arguments.get("value")
            desc = arguments.get("description", "")

            try:
                action_type = _map_action(action)
            except ValueError as e:
                return _error_result(str(e))

            # 실제 SAP 실행
            _execute_sap_action(sap, action.lower(), target, value)

            status = sap.get_status()
            info = sap.get_session_info()

            # run/vkey는 target이 실행 값이므로 value에도 저장
            record_value = value
            if action.lower() in ("run", "vkey") and not value:
                record_value = target

            step = recorder.record_step(
                action=action_type,
                target_id=target,
                value=record_value,
                description=desc,
                screen_number=info.screen_number,
            )

            return _json_result({
                "recorded": True,
                "step_number": step.step_number,
                "action": action,
                "target": target,
                "value": value,
                "screen_number": info.screen_number,
                "status": status.model_dump(mode="json"),
            })

        elif name == "sap_macro_control":
            cmd = arguments["command"]
            recorder = _get_recorder()

            if cmd == "start":
                macro_name = arguments.get("name", "unnamed")
                recorder.start(macro_name)
                return _json_result({"recording": True, "name": macro_name})

            elif cmd == "finish":
                store = _get_macro_store()
                macro_name = arguments.get("name") or recorder._name
                desc = arguments.get("description", "")
                vars_dict = arguments.get("variables")

                macro = recorder.finish(
                    name=macro_name,
                    description=desc,
                    variables=vars_dict,
                )
                path = store.save(macro)
                return _json_result({
                    "saved": True,
                    "macro_id": macro.id,
                    "name": macro.name,
                    "steps_count": len(macro.steps),
                    "variables": [v.model_dump(mode="json") for v in macro.variables],
                    "path": str(path),
                })

            elif cmd == "cancel":
                recorder.cancel()
                return _json_result({"cancelled": True})

            elif cmd == "status":
                steps = recorder.current_steps
                return _json_result({
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

            elif cmd == "undo":
                removed = recorder.undo_last()
                if removed:
                    return _json_result({
                        "undone": True,
                        "removed_step": removed.step_number,
                        "action": removed.action.value,
                        "remaining_steps": len(recorder.current_steps),
                    })
                return _json_result({"undone": False, "message": "취소할 단계가 없습니다."})

            else:
                return _error_result(f"알 수 없는 명령: {cmd}")

        elif name == "sap_macro_play":
            store = _get_macro_store()
            macro = store.load(arguments["macro_id"])
            if not macro:
                return _error_result(f"매크로 '{arguments['macro_id']}'를 찾을 수 없습니다.")

            vars_dict = arguments.get("variables", {})
            sap = _get_sap()
            player = MacroPlayer(sap)

            if arguments.get("dry_run"):
                result = player.dry_run(macro, vars_dict)
            else:
                result = player.play(macro, vars_dict)

            return _json_result(result.model_dump(mode="json"))

        elif name == "sap_macro_list":
            store = _get_macro_store()
            tcode = arguments.get("tcode")
            if tcode:
                macros = store.find_by_tcode(tcode)
                return _json_result({
                    "macros": [m.model_dump(mode="json") for m in macros],
                    "count": len(macros),
                })
            macros_list = store.list_macros()
            return _json_result({"macros": macros_list, "count": len(macros_list)})

        else:
            return _error_result(f"Unknown tool: {name}")

    except SapGuiError as e:
        return _error_result(str(e))
    except Exception as e:
        return _error_result(f"Unexpected error: {e}")


# -------------------------------------------------------------------
# MCP Server Main
# -------------------------------------------------------------------

async def main() -> None:
    if not HAS_MCP:
        print("Error: mcp 패키지가 설치되지 않았습니다. pip install mcp 로 설치하세요.", file=sys.stderr)
        sys.exit(1)

    server = Server("sap-gui")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return TOOLS

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        return handle_tool(name, arguments)

    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
