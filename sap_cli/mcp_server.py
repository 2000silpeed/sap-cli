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
import os
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
from .auth.policy import load_policy
from .dispatcher import (
    ApprovalRequiredError,
    DispatchContext,
    PermissionDeniedError,
    _resolve_agent_name,
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

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "sap_connect",
        "description": "실행 중인 SAP GUI에 연결합니다. SAP GUI가 이미 실행 중이고 로그인된 상태여야 합니다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "connection_index": {"type": "integer", "default": 0, "description": "연결 인덱스 (기본: 0)"},
                "session_index": {"type": "integer", "default": 0, "description": "세션 인덱스 (기본: 0)"},
            },
        },
    },
    {
        "name": "sap_session",
        "description": "현재 SAP 세션 정보를 조회합니다 (사용자, 트랜잭션, 화면번호, 시스템명 등).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "sap_screen",
        "description": "현재 SAP 화면의 전체 UI 구조를 JSON으로 반환합니다. 필드, 버튼, 탭, 테이블 등 모든 요소를 포함합니다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "fields_only": {"type": "boolean", "default": False, "description": "입력 가능한 필드만 반환"},
                "actions_only": {"type": "boolean", "default": False, "description": "가능한 액션만 반환"},
            },
        },
    },
    {
        "name": "sap_run",
        "description": "SAP 트랜잭션을 실행합니다. 예: MM03(자재조회), VA01(수주생성), ME21N(구매오더).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "tcode": {"type": "string", "description": "트랜잭션 코드 (예: MM03)"},
            },
            "required": ["tcode"],
        },
    },
    {
        "name": "sap_command",
        "description": "SAP 커맨드 필드에 명령을 전송합니다. 예: /nMM03, /oVA03.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "cmd": {"type": "string", "description": "SAP 명령 문자열"},
            },
            "required": ["cmd"],
        },
    },
    {
        "name": "sap_set_field",
        "description": "SAP 화면의 필드에 값을 설정합니다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "element_id": {"type": "string", "description": "필드 ID (예: wnd[0]/usr/ctxtRMMG1-MATNR)"},
                "value": {"type": "string", "description": "설정할 값"},
            },
            "required": ["element_id", "value"],
        },
    },
    {
        "name": "sap_get_field",
        "description": "SAP 화면 필드의 현재 값을 읽습니다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "element_id": {"type": "string", "description": "필드 ID"},
            },
            "required": ["element_id"],
        },
    },
    {
        "name": "sap_press",
        "description": "SAP 화면의 버튼을 클릭합니다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "element_id": {"type": "string", "description": "버튼 ID (예: wnd[0]/tbar[0]/btn[0])"},
            },
            "required": ["element_id"],
        },
    },
    {
        "name": "sap_send_vkey",
        "description": "SAP GUI에 키보드 키를 전송합니다. 주요 VKey: 0=Enter, 3=Back, 8=Execute(F8), 11=Save(Ctrl+S), 12=Cancel.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "vkey": {"type": "integer", "description": "VKey 번호"},
            },
            "required": ["vkey"],
        },
    },
    {
        "name": "sap_get_status",
        "description": "SAP 상태바의 메시지를 조회합니다. 타입: S=성공, W=경고, E=에러, I=정보, A=중단.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "sap_modal",
        "description": "현재 모달 윈도우(팝업) 존재 여부와 텍스트를 조회합니다.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "sap_table_read",
        "description": "SAP 화면의 테이블/그리드 데이터를 JSON으로 읽습니다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "element_id": {"type": "string", "description": "테이블/그리드 ID"},
            },
            "required": ["element_id"],
        },
    },
    {
        "name": "sap_ontology_query",
        "description": "학습된 SAP 온톨로지를 조회합니다. 트랜잭션의 화면 구조, 필드, 가능한 액션 등 도메인 지식을 반환합니다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "tcode": {"type": "string", "description": "트랜잭션 코드"},
                "field_search": {"type": "string", "description": "필드명/라벨 검색 (선택)"},
                "list_all": {"type": "boolean", "default": False, "description": "저장된 모든 온톨로지 목록"},
            },
        },
    },
    {
        "name": "sap_transcript_query",
        "description": "기록된 transcript를 조회합니다. 최근 성공/실패/승인요청 흐름을 확인할 때 사용합니다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 20, "description": "반환할 최대 entry 수"},
                "outcome": {"type": "string", "description": "success / denied / approval_required / error"},
                "command": {"type": "string", "description": "dispatcher command 필터"},
                "tool": {"type": "string", "description": "tool 이름 필터"},
                "agent": {"type": "string", "description": "agent 이름 필터"},
                "text": {"type": "string", "description": "entry 전체 텍스트 검색"},
                "full": {"type": "boolean", "default": False, "description": "요약 대신 전체 entry 반환"},
            },
        },
    },
    {
        "name": "sap_transcript_plan",
        "description": "선택한 transcript entry를 다시 실행하기 전에 필요한 확인 순서와 위험도를 요약합니다. 실제 재실행은 하지 않습니다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 20, "description": "검색할 최대 entry 수"},
                "index": {"type": "integer", "default": 0, "description": "필터 결과에서 선택할 entry 인덱스"},
                "outcome": {"type": "string", "description": "success / denied / approval_required / error"},
                "command": {"type": "string", "description": "dispatcher command 필터"},
                "tool": {"type": "string", "description": "tool 이름 필터"},
                "agent": {"type": "string", "description": "agent 이름 필터"},
                "text": {"type": "string", "description": "entry 전체 텍스트 검색"},
            },
        },
    },
    {
        "name": "sap_learn",
        "description": "현재 SAP 화면을 스캔하여 온톨로지로 학습합니다. 학습된 정보는 이후 sap_ontology_query로 조회할 수 있습니다.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "sap_macro_record_step",
        "description": "매크로 녹화 중 단계를 기록하면서 동시에 SAP에 실행합니다. record_start를 먼저 호출해야 합니다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["set", "press", "vkey", "run", "checkbox", "radio", "combo", "tab", "menu"]},
                "target": {"type": "string", "description": "대상 ID 또는 값"},
                "value": {"type": "string", "description": "값 (set/vkey/checkbox/combo용)"},
                "description": {"type": "string", "description": "단계 설명"},
            },
            "required": ["action", "target"],
        },
    },
    {
        "name": "sap_macro_control",
        "description": "매크로 녹화를 제어합니다 (시작/종료/취소/상태/실행취소).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "enum": ["start", "finish", "cancel", "status", "undo"]},
                "name": {"type": "string", "description": "매크로 이름 (start/finish 시)"},
                "description": {"type": "string"},
                "variables": {"type": "object", "description": "변수 지정 (finish 시). {name: description}"},
            },
            "required": ["command"],
        },
    },
    {
        "name": "sap_macro_play",
        "description": "저장된 매크로를 변수 값을 바꿔서 재생합니다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "macro_id": {"type": "string", "description": "매크로 ID"},
                "variables": {"type": "object", "description": "변수 값. {name: value}"},
                "dry_run": {"type": "boolean", "default": False},
            },
            "required": ["macro_id"],
        },
    },
    {
        "name": "sap_macro_list",
        "description": "저장된 매크로 목록을 조회합니다. 트랜잭션 코드로 필터링 가능.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "tcode": {"type": "string", "description": "트랜잭션 코드로 필터링 (선택)"},
            },
        },
    },
    {
        "name": "sap_macro_show",
        "description": "저장된 매크로의 상세 정의를 조회합니다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "macro_id": {"type": "string", "description": "매크로 ID"},
            },
            "required": ["macro_id"],
        },
    },
    {
        "name": "sap_macro_delete",
        "description": "저장된 매크로를 삭제합니다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "macro_id": {"type": "string", "description": "매크로 ID"},
            },
            "required": ["macro_id"],
        },
    },
    {
        "name": "sap_process_list",
        "description": "등록된 업무 프로세스 목록을 조회합니다. 업무 프로세스는 여러 트랜잭션을 연결한 end-to-end 워크플로우입니다.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "sap_process_show",
        "description": "업무 프로세스의 상세 정의를 조회합니다 (스텝, 데이터 흐름 포함).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "process_id": {"type": "string", "description": "프로세스 ID"},
            },
            "required": ["process_id"],
        },
    },
    {
        "name": "sap_process_create",
        "description": "새 업무 프로세스를 등록합니다. 트랜잭션 간 순서와 데이터 흐름을 정의합니다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "프로세스 ID (영문, _, - 만 허용)"},
                "name": {"type": "string", "description": "프로세스 이름"},
                "module": {"type": "string", "description": "SAP 모듈 (예: MM, SD)"},
                "description": {"type": "string"},
                "steps": {
                    "type": "array",
                    "description": "프로세스 스텝 목록",
                    "items": {
                        "type": "object",
                        "properties": {
                            "step_number": {"type": "integer"},
                            "tcode": {"type": "string"},
                            "description": {"type": "string"},
                            "input_fields": {"type": "object"},
                            "expected_output": {"type": "array", "items": {"type": "string"}},
                            "optional": {"type": "boolean", "default": False},
                        },
                        "required": ["step_number", "tcode"],
                    },
                },
                "data_flows": {
                    "type": "array",
                    "description": "스텝 간 데이터 흐름",
                    "items": {
                        "type": "object",
                        "properties": {
                            "source_step": {"type": "integer"},
                            "source_field": {"type": "string"},
                            "target_step": {"type": "integer"},
                            "target_field": {"type": "string"},
                            "description": {"type": "string"},
                        },
                        "required": ["source_step", "source_field", "target_step", "target_field"],
                    },
                },
                "tags": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["id", "name", "steps"],
        },
    },
    {
        "name": "sap_process_delete",
        "description": "등록된 업무 프로세스를 삭제합니다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "process_id": {"type": "string", "description": "프로세스 ID"},
            },
            "required": ["process_id"],
        },
    },
]

TOOLS: list = []
if HAS_MCP:
    TOOLS = [Tool(**definition) for definition in TOOL_DEFINITIONS]


def _load_checker(agent_name: str | None = None) -> tuple[PermissionChecker, str | None]:
    policy = load_policy()
    checker = PermissionChecker(policy)
    configured_agent = _resolve_agent_name(policy, agent_name)
    if configured_agent:
        checker.set_agent(configured_agent)
    effective_agent = checker.agent_name or configured_agent
    return checker, effective_agent


def _visible_tool_names_for_checker(checker: PermissionChecker) -> list[str]:
    if not checker.is_enforcing:
        return [definition["name"] for definition in TOOL_DEFINITIONS]
    return [
        definition["name"]
        for definition in TOOL_DEFINITIONS
        if checker.can_access_tool_surface(definition["name"])
    ]


def get_visible_tool_names(agent_name: str | None = None) -> list[str]:
    checker, _ = _load_checker(agent_name)
    return _visible_tool_names_for_checker(checker)


def get_visible_tools(agent_name: str | None = None) -> list[Tool]:
    if not HAS_MCP:
        return []
    visible_names = set(get_visible_tool_names(agent_name))
    return [tool for tool in TOOLS if tool.name in visible_names]


def _screen_summary_enabled() -> bool:
    raw = os.getenv("SAP_CLI_MCP_SCREEN_SUMMARY", "1").strip().lower()
    return raw not in {"0", "false", "no"}


# -------------------------------------------------------------------
# Tool Handlers
# -------------------------------------------------------------------

def handle_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    checker, effective_agent = _load_checker()
    visible_tool_names = set(_visible_tool_names_for_checker(checker))
    known_tool_names = {definition["name"] for definition in TOOL_DEFINITIONS}

    if name not in known_tool_names:
        return _error_result(f"Unknown tool: {name}")

    if name not in visible_tool_names:
        if effective_agent:
            return _error_result(
                f"활성 에이전트 프로필 '{effective_agent}'에는 '{name}' 도구가 노출되지 않습니다."
            )
        return _error_result(f"현재 MCP 구성에는 '{name}' 도구가 노출되지 않습니다.")

    try:
        command, normalized_args = resolve_mcp_call(name, arguments)
        result = dispatch_command(
            command,
            normalized_args,
            DispatchContext(
                source="mcp",
                tool_name=name,
                agent_name=effective_agent,
                include_screen_summary=_screen_summary_enabled(),
            ),
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
        return get_visible_tools()

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        return handle_tool(name, arguments)

    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
