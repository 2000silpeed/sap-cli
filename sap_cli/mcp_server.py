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
        "description": "현재 SAP 화면의 전체 UI 구조를 JSON으로 반환합니다. 필드, 버튼, 탭, 테이블 등 모든 요소를 포함합니다. search로 특정 요소를 패턴 검색할 수 있습니다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "fields_only": {"type": "boolean", "default": False, "description": "입력 가능한 필드만 반환"},
                "actions_only": {"type": "boolean", "default": False, "description": "가능한 액션만 반환"},
                "search": {"type": "string", "description": "필드/버튼/테이블을 패턴으로 검색 (ID, 이름, 라벨, 시맨틱 부분 일치)"},
            },
        },
    },
    {
        "name": "sap_run",
        "description": "SAP 트랜잭션을 실행합니다. 예: MM03(자재조회), VA01(수주생성), ME21N(구매오더). 복잡한 네비게이션(SPRO 등)을 시작하기 전에 sap_recipe_search로 기존 레시피를 먼저 확인하세요.",
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
        "name": "sap_fill_fields",
        "description": "여러 필드를 한번에 입력합니다. 필드명(MATNR) 또는 element_id 모두 사용 가능. 현재 화면을 스캔하여 필드명→element_id를 자동 매핑합니다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "fields": {
                    "type": "object",
                    "description": "필드명=값 쌍 (예: {\"MATNR\": \"100-100\", \"WERKS\": \"1000\"})",
                    "additionalProperties": {"type": "string"},
                },
                "enter": {
                    "type": "boolean",
                    "description": "입력 후 자동 Enter 전송 (기본: true)",
                    "default": True,
                },
            },
            "required": ["fields"],
        },
    },
    {
        "name": "sap_get_field",
        "description": "SAP 화면 필드의 현재 값을 읽습니다. 여러 필드를 한번에 조회할 수 있습니다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "element_id": {"type": "string", "description": "필드 ID (단일 조회 시)"},
                "element_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "필드 ID 목록 (여러 건 조회 시)",
                },
            },
        },
    },
    {
        "name": "sap_press",
        "description": "SAP 화면의 버튼을 클릭하거나 탭을 선택합니다. GuiTab이면 자동으로 Select()를 호출합니다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "element_id": {"type": "string", "description": "버튼 또는 탭 ID (예: wnd[0]/tbar[0]/btn[0], wnd[0]/usr/tabsTAB/tabpTAB01)"},
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
        "description": "SAP 화면의 테이블/그리드 데이터를 JSON으로 읽습니다. GuiTableControl, GuiGridView, GuiShell(ALV Grid) 모두 지원. ALV는 스크롤 자동 처리. 기본 50행만 읽으며 max_rows로 조정 가능(최대 500). columns로 특정 컬럼만 읽으면 속도가 크게 향상됩니다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "element_id": {"type": "string", "description": "테이블/그리드 ID (sap_screen의 tables에서 확인)"},
                "max_rows": {"type": "integer", "description": "최대 읽을 행 수 (기본 50, 최대 500)", "default": 50},
                "columns": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "읽을 컬럼 이름 목록 (생략하면 전체 컬럼). 필요한 컬럼만 지정하면 속도 향상.",
                },
            },
            "required": ["element_id"],
        },
    },
    {
        "name": "sap_tree_read",
        "description": (
            "SAP 화면의 트리 컨트롤 데이터를 읽습니다. "
            "토큰 절약을 위해 depth=1로 얕게 조회한 뒤, child_count>0인 노드를 node_key로 드릴다운하세요. "
            "닫힌 노드는 sap_tree_expand로 먼저 펼쳐야 자식이 보입니다. "
            "[SPRO 트리 탐색 가이드] "
            "1) depth=0으로 루트 확인 → 2) sap_tree_expand로 펼치기 → "
            "3) node_key+depth=0으로 자식 조회 → 반복하여 목표 노드 도달 → "
            "4) sap_tree_press로 실행. "
            "SPRO 트리 컬럼: column='2'=IMG Activity 실행, column='1'=Documentation 조회. "
            "[레시피 활용] SPRO 등 트리 네비게이션 전에 sap_recipe_search로 기존 레시피를 먼저 확인하세요. "
            "레시피가 있으면 그 단계를 따라 실행. 없으면 직접 탐색 후 성공 경로를 sap_recipe_save로 저장."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "element_id": {"type": "string", "description": "트리 ID (sap_screen의 trees에서 확인)"},
                "node_key": {"type": "string", "description": "특정 노드의 자식만 조회 (생략 시 전체 트리)"},
                "depth": {"type": "integer", "description": "깊이 제한 (-1=무제한, 0=현재만, 1=자식까지). 큰 트리에서는 1 권장"},
            },
            "required": ["element_id"],
        },
    },
    {
        "name": "sap_tree_select",
        "description": "트리 노드를 선택합니다. sap_tree_read로 노드 목록을 먼저 확인하세요.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "element_id": {"type": "string", "description": "트리 ID (sap_screen의 trees에서 확인)"},
                "node_key": {"type": "string", "description": "노드 키 (sap_tree_read 결과의 key)"},
            },
            "required": ["element_id", "node_key"],
        },
    },
    {
        "name": "sap_tree_doubleclick",
        "description": "트리 노드를 더블클릭합니다. SPRO IMG Activity 진입, BOM 펼치기 등에 사용. sap_tree_read로 노드 키를 먼저 확인하세요.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "element_id": {"type": "string", "description": "트리 ID"},
                "node_key": {"type": "string", "description": "노드 키"},
            },
            "required": ["element_id", "node_key"],
        },
    },
    {
        "name": "sap_tree_expand",
        "description": "트리 노드를 확장하여 하위 노드를 펼칩니다. SPRO 메뉴 탐색 시 사용.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "element_id": {"type": "string", "description": "트리 ID"},
                "node_key": {"type": "string", "description": "노드 키"},
            },
            "required": ["element_id", "node_key"],
        },
    },
    {
        "name": "sap_tree_collapse",
        "description": "트리 노드를 접습니다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "element_id": {"type": "string", "description": "트리 ID"},
                "node_key": {"type": "string", "description": "노드 키"},
            },
            "required": ["element_id", "node_key"],
        },
    },
    {
        "name": "sap_tree_press",
        "description": (
            "트리 내 버튼/아이콘을 클릭합니다. "
            "SPRO에서는 column='2'가 IMG Activity 실행, column='1'이 Documentation입니다. "
            "SPRO가 아닌 트리에서 column 생략 시 자동 탐색합니다."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "element_id": {"type": "string", "description": "트리 ID"},
                "node_key": {"type": "string", "description": "노드 키"},
                "column": {"type": "string", "description": "실행할 컬럼 이름 (discovered_columns 참조, 생략 시 자동 탐색)", "default": ""},
            },
            "required": ["element_id", "node_key"],
        },
    },
    {
        "name": "sap_table_select",
        "description": "테이블에서 특정 행을 선택합니다. MM03 뷰 선택 등에서 사용.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "element_id": {"type": "string", "description": "테이블 ID"},
                "row": {"type": "integer", "description": "선택할 행 번호 (0-based)"},
            },
            "required": ["element_id", "row"],
        },
    },
    {
        "name": "sap_table_doubleclick",
        "description": "테이블 셀을 더블클릭합니다. ALV Grid에서 행 진입(디테일 화면 이동), 문서 번호 클릭 등에 사용.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "element_id": {"type": "string", "description": "테이블 ID"},
                "row": {"type": "integer", "description": "더블클릭할 행 번호 (0-based)"},
                "column": {"type": "string", "description": "컬럼 ID (빈 문자열이면 첫 컬럼)", "default": ""},
            },
            "required": ["element_id", "row"],
        },
    },
    {
        "name": "sap_ontology_query",
        "description": (
            "학습된 SAP 온톨로지를 조회합니다. 화면 구조, 필드, 가능한 액션 경로 등 도메인 지식을 반환합니다. "
            "SPRO 등 트리 네비게이션이 필요할 때 action_search로 저장된 경로를 먼저 검색하세요. "
            "경로가 있으면 execution_plan이 반환되므로 그대로 실행하면 됩니다."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "tcode": {"type": "string", "description": "트랜잭션 코드"},
                "field_search": {"type": "string", "description": "필드명/라벨 검색 (선택)"},
                "action_search": {"type": "string", "description": "액션 경로 검색 (예: 'shipping point', '가격결정'). SPRO 등에서 저장된 네비게이션 경로를 찾습니다."},
                "list_all": {"type": "boolean", "default": False, "description": "저장된 모든 온톨로지 목록"},
            },
        },
    },
    {
        "name": "sap_ontology_relationships",
        "description": "크로스 트랜잭션 필드 관계를 조회합니다. 특정 필드(MATNR 등)가 어떤 트랜잭션/화면에서 쓰이는지 한번에 확인합니다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "field": {"type": "string", "description": "SAP 필드명 (예: MATNR, WERKS). 생략 시 전체 요약"},
                "rebuild": {"type": "boolean", "default": False, "description": "전체 트랜잭션에서 레지스트리 재구축"},
            },
        },
    },
    {
        "name": "sap_field_dependencies",
        "description": "필드 종속성 그래프를 조회합니다. WERKS → LGORT 같은 필드 간 종속/결정 관계를 확인합니다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "field": {"type": "string", "description": "SAP 필드명 (예: WERKS, KUNNR). 생략 시 전체 요약"},
                "rebuild": {"type": "boolean", "default": False, "description": "전체 트랜잭션에서 종속성 그래프 재구축"},
            },
        },
    },
    {
        "name": "sap_document_flow",
        "description": "트랜잭션 간 문서 흐름을 조회합니다. VA01 → VL01N → VF01 같은 문서 관계를 확인합니다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "tcode": {"type": "string", "description": "트랜잭션 코드 (예: VA01, ME21N). 생략 시 전체 요약"},
                "rebuild": {"type": "boolean", "default": False, "description": "프로세스/트랜잭션에서 문서 흐름 재구축"},
            },
        },
    },
    {
        "name": "sap_process_path",
        "description": "두 트랜잭션 간 문서 흐름 경로를 탐색합니다. 예: MM01에서 MIRO까지 어떤 경로로 가는지 BFS로 찾습니다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "from_tcode": {"type": "string", "description": "출발 트랜잭션 코드 (예: VA01)"},
                "to_tcode": {"type": "string", "description": "도착 트랜잭션 코드 (예: VF01)"},
            },
            "required": ["from_tcode", "to_tcode"],
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
        "description": "현재 SAP 화면을 스캔하여 온톨로지로 학습합니다. Tooltip, LeftLabel 등 SAP COM 속성에서 시맨틱 정보를 자동 추출합니다.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "sap_annotate_field",
        "description": "온톨로지 필드의 시맨틱 정보를 수동으로 입력/수정합니다. 영어와 한국어를 모두 지원합니다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "tcode": {"type": "string", "description": "트랜잭션 코드 (예: MM03)"},
                "field": {"type": "string", "description": "필드 ID, 이름, 또는 SAP 필드명 (예: RMMG1-MATNR, MATNR)"},
                "desc": {"type": "string", "description": "영문 설명"},
                "desc_ko": {"type": "string", "description": "한글 설명"},
                "synonyms": {"type": "string", "description": "영문 동의어 (쉼표 구분)"},
                "synonyms_ko": {"type": "string", "description": "한글 동의어 (쉼표 구분)"},
                "validation": {"type": "string", "description": "유효성 규칙"},
                "domain_context": {"type": "string", "description": "도메인 맥락"},
            },
            "required": ["tcode", "field"],
        },
    },
    {
        "name": "sap_annotate_dependency",
        "description": "필드 종속 관계를 수동으로 추가/수정합니다. 예: WERKS → LGORT (input_order), KUNNR → ZTERM (value_determines).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "source": {"type": "string", "description": "결정하는 필드 SAP 필드명 (예: WERKS, KUNNR)"},
                "target": {"type": "string", "description": "종속되는 필드 SAP 필드명 (예: LGORT, ZTERM)"},
                "type": {"type": "string", "enum": ["value_determines", "input_order", "lookup", "field_activation"], "default": "value_determines", "description": "종속성 유형"},
                "description": {"type": "string", "description": "관계 설명"},
                "observed_in": {"type": "string", "description": "관찰된 트랜잭션 코드 (쉼표 구분, 예: VA01,ME21N)"},
            },
            "required": ["source", "target"],
        },
    },
    {
        "name": "sap_macro_record_step",
        "description": "매크로 녹화 중 단계를 기록하면서 동시에 SAP에 실행합니다. record_start를 먼저 호출해야 합니다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["set", "press", "vkey", "run", "checkbox", "radio", "combo", "tab", "menu", "table-select"]},
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
                "variables": {"type": "object", "description": "변수 지정 (finish 시). {변수명: 실제값} 또는 {변수명: {value: 실제값, desc: 설명}}"},
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
    {
        "name": "sap_process_start",
        "description": "업무 프로세스 실행을 시작합니다. 스텝별로 실행하면서 데이터를 전달할 준비를 합니다. 시작 후 process_step으로 각 스텝 완료를 보고하고, process_status로 다음 스텝에 넣을 입력값을 확인하세요.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "process_id": {"type": "string", "description": "실행할 프로세스 ID"},
            },
            "required": ["process_id"],
        },
    },
    {
        "name": "sap_process_step",
        "description": "프로세스 스텝 실행 결과를 보고합니다. outputs에 이 스텝에서 생성/추출한 값(예: 자재번호, 문서번호)을 넣으면 DataFlow에 따라 다음 스텝 입력에 자동 연결됩니다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "step_number": {"type": "integer", "description": "완료한 스텝 번호"},
                "status": {"type": "string", "enum": ["success", "error", "skipped"], "default": "success"},
                "outputs": {"type": "object", "description": "이 스텝의 출력값. 예: {\"MATNR\": \"100-100\", \"EBELN\": \"4500012345\"}"},
                "error": {"type": "string", "description": "에러 메시지 (status가 error일 때)"},
            },
            "required": ["step_number"],
        },
    },
    {
        "name": "sap_process_status",
        "description": "현재 실행 중인 프로세스의 상태를 조회합니다. 다음 스텝에 입력할 값(pending_inputs)과 지금까지 수집된 데이터(context)를 보여줍니다.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "sap_recipe_save",
        "description": (
            "AI 네비게이션 레시피를 저장합니다. SPRO 등 트리 네비게이션에 성공한 후, 성공 경로를 레시피로 저장하면 다음에 같은 요청이 올 때 빠르게 실행할 수 있습니다. "
            "반드시 성공한 경로만 저장하세요. 시행착오는 제외하고 최종 성공 단계만 포함. "
            "[중요] context 필드에 이 레시피가 정확히 무엇을 하는지 상세히 기록하세요. "
            "같은 영역(예: Shipping Point)에도 여러 레시피가 있을 수 있으므로 구체적 목적, "
            "전제조건, 결과 화면 등을 포함해야 나중에 올바른 레시피를 찾을 수 있습니다."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "recipe_id": {"type": "string", "description": "레시피 ID (영문, 숫자, _, - 만 허용. 예: spro-shipping-point-create)"},
                "description": {"type": "string", "description": "레시피 짧은 설명 (예: SPRO > 물류 > Shipping Point 신규 생성)"},
                "context": {
                    "type": "string",
                    "description": (
                        "레시피의 상세 맥락. 이 레시피가 정확히 무엇을 하는지, 어떤 상황에서 사용하는지, "
                        "전제조건은 무엇인지, 완료 후 어떤 화면/결과가 나오는지 자세히 기록. "
                        "예: 'Shipping Point를 새로 생성하는 경로. 물류실행 > 배송 > 기본설정 > Shipping Point 정의에서 "
                        "신규 엔트리를 추가한다. 완료 후 Shipping Point 코드가 생성된다. "
                        "플랜트 할당은 별도 레시피(spro-shipping-point-plant-assign) 참조.'"
                    ),
                },
                "keywords": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "검색 키워드 (예: [\"가격결정\", \"pricing\", \"조건테이블\"])",
                },
                "steps": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "tool": {"type": "string", "description": "MCP 도구 이름 (예: sap_run, sap_tree_expand)"},
                            "args": {"type": "object", "description": "도구 인자"},
                            "hint": {"type": "string", "description": "이 단계 설명 (AI가 참고)"},
                        },
                        "required": ["tool"],
                    },
                    "description": "실행 단계 배열 (성공 경로만)",
                },
            },
            "required": ["recipe_id", "description", "context", "steps"],
        },
    },
    {
        "name": "sap_recipe_search",
        "description": (
            "저장된 레시피를 키워드로 검색합니다. SPRO 네비게이션 등 복잡한 작업을 시작하기 전에 먼저 호출하세요. "
            "결과가 1개면 steps가 바로 포함됩니다. 여러 개면 요약(context)만 반환되므로 "
            "context를 비교하여 올바른 레시피를 선택한 후 sap_recipe_show로 상세 steps를 조회하세요. "
            "모든 단계를 성공적으로 완료한 후 반드시 sap_recipe_success를 호출하여 성공을 기록하세요."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "검색어 (한글/영문 모두 가능)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "sap_recipe_show",
        "description": "레시피 ID로 상세 정보(steps 포함)를 조회합니다. sap_recipe_search에서 여러 결과가 나왔을 때, context를 비교하여 선택한 후 이 도구로 실행할 steps를 가져오세요.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "recipe_id": {"type": "string", "description": "조회할 레시피 ID"},
            },
            "required": ["recipe_id"],
        },
    },
    {
        "name": "sap_recipe_list",
        "description": "저장된 모든 레시피 목록을 조회합니다.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "sap_recipe_delete",
        "description": "레시피를 삭제합니다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "recipe_id": {"type": "string", "description": "삭제할 레시피 ID"},
            },
            "required": ["recipe_id"],
        },
    },
    {
        "name": "sap_recipe_success",
        "description": "[필수] 레시피의 모든 단계를 성공적으로 완료한 후 반드시 호출하세요. sap_recipe_search로 레시피를 찾아 실행했다면, 완료 후 이 도구를 호출해야 성공 카운트가 올라가고 다음 검색에서 우선순위가 높아집니다. 호출하지 않으면 성공이 기록되지 않습니다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "recipe_id": {"type": "string", "description": "성공한 레시피 ID"},
            },
            "required": ["recipe_id"],
        },
    },
    {
        "name": "sap_session_start",
        "description": (
            "AI 실행 세션을 시작합니다. 사용자의 SAP 작업 요청을 받았을 때 가장 먼저 호출하세요. "
            "세션은 사용자 요청 → AI 추론 → SAP 명령 시퀀스 → 결과의 전체 흐름을 기록합니다. "
            "세션을 시작하면 이후 실행되는 모든 SAP 명령이 자동으로 세션 단계로 기록됩니다. "
            "작업 완료 후 반드시 sap_session_end를 호출하세요."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "user_request": {"type": "string", "description": "사용자의 원래 요청 (예: 'VL33N에서 46개 아이템 배송 처리해줘')"},
                "ai_reasoning": {"type": "string", "description": "AI의 실행 계획/추론 (예: 'VL33N 배송 문서 변경 → 아이템 선택 → 배송 처리')"},
            },
            "required": ["user_request"],
        },
    },
    {
        "name": "sap_session_end",
        "description": (
            "AI 실행 세션을 종료합니다. SAP 작업이 완료되면 결과와 함께 호출하세요. "
            "성공한 세션은 자동으로 온톨로지 학습 후보로 평가됩니다."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "sap_session_start에서 반환된 세션 ID"},
                "outcome": {"type": "string", "enum": ["success", "partial", "failed"], "description": "결과 (success/partial/failed)"},
                "outcome_summary": {"type": "string", "description": "결과 요약 (예: '46개 아이템 중 46개 배송 처리 완료')"},
            },
            "required": ["session_id", "outcome"],
        },
    },
    {
        "name": "sap_session_list",
        "description": "기록된 AI 실행 세션 목록을 조회합니다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "날짜 필터 (YYYY-MM-DD)"},
                "status": {"type": "string", "description": "상태 필터 (running/completed/failed/aborted)"},
                "limit": {"type": "integer", "description": "최대 결과 수 (기본 20)"},
            },
        },
    },
    {
        "name": "sap_session_show",
        "description": "특정 AI 실행 세션의 상세 정보를 조회합니다. 세션의 모든 단계(steps)와 결과를 확인할 수 있습니다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "조회할 세션 ID"},
            },
            "required": ["session_id"],
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


def _use_daemon() -> bool:
    """SAP_CLI_USE_DAEMON=1 이면 daemon 경유 모드."""
    raw = os.getenv("SAP_CLI_USE_DAEMON", "0").strip().lower()
    return raw in {"1", "true", "yes"}


_active_session_id: str | None = None


# -------------------------------------------------------------------
# Tool Handlers
# -------------------------------------------------------------------

def _dispatch_via_daemon(
    command: str,
    args: dict[str, Any],
    tool_name: str,
    agent_name: str | None,
    session_id: str | None = None,
) -> dict[str, Any] | None:
    """daemon 경유 실행. 성공하면 결과 dict, 실패/미실행이면 None."""
    from . import daemon

    if not daemon.is_running():
        if not daemon.start_daemon():
            return None

    ctx: dict[str, Any] = {
        "source": "mcp",
        "tool_name": tool_name,
        "agent_name": agent_name,
        "include_screen_summary": _screen_summary_enabled(),
    }
    if session_id:
        ctx["session_id"] = session_id
    result = daemon.send_command(command, args, context=ctx)
    if result is None:
        return None
    return result


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

    global _active_session_id

    try:
        command, normalized_args = resolve_mcp_call(name, arguments)

        # daemon 위임 모드
        if _use_daemon():
            daemon_result = _dispatch_via_daemon(
                command, normalized_args, name, effective_agent,
                session_id=_active_session_id,
            )
            if daemon_result is not None:
                if daemon_result.get("ok"):
                    data = daemon_result["data"]
                    if command == "session_start" and isinstance(data, dict):
                        _active_session_id = data.get("session_id")
                    elif command == "session_end" and isinstance(data, dict) and "error" not in data:
                        _active_session_id = None
                    return _json_result(data)
                if daemon_result.get("approval_required"):
                    return _json_result({
                        "approval_required": True,
                        "tool": daemon_result.get("tool"),
                        "reason": daemon_result.get("reason", ""),
                    })
                return _error_result(daemon_result.get("error", "daemon error"))
            # daemon 사용 불가 → fallback to direct dispatch

        ctx = DispatchContext(
            source="mcp",
            tool_name=name,
            agent_name=effective_agent,
            include_screen_summary=_screen_summary_enabled(),
            session_id=_active_session_id,
        )
        result = dispatch_command(command, normalized_args, ctx)

        if command == "session_start" and isinstance(result, dict):
            _active_session_id = result.get("session_id")
        elif command == "session_end" and isinstance(result, dict) and "error" not in result:
            _active_session_id = None

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
