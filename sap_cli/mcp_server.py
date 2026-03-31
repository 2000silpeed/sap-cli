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

from .dispatcher import (
    ApprovalRequiredError,
    DispatchContext,
    PermissionDeniedError,
    dispatch_command,
    resolve_mcp_call,
)


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
    try:
        command, normalized_args = resolve_mcp_call(name, arguments)
        result = dispatch_command(
            command,
            normalized_args,
            DispatchContext(source="mcp", tool_name=name),
        )
        return _json_result(result)
    except ApprovalRequiredError as e:
        return _json_result(e.to_payload())
    except (PermissionDeniedError, ValueError) as e:
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
