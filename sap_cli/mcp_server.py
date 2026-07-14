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
    from mcp.types import ImageContent, TextContent, Tool
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
from .ui_action_catalog import mcp_tool_definitions


def _json_result(data: Any) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps(data, ensure_ascii=False, indent=2))]


def _error_result(msg: str) -> list[TextContent]:
    return _json_result({"error": True, "message": msg})


def _capture_result(data: dict[str, Any]) -> list:
    import base64
    from pathlib import Path

    contents: list = _json_result(data)
    path_str = data.get("path")
    if HAS_MCP and path_str:
        try:
            img_bytes = Path(path_str).read_bytes()
            b64 = base64.b64encode(img_bytes).decode("ascii")
            contents.append(ImageContent(type="image", data=b64, mimeType="image/png"))
        except Exception:
            pass
    return contents


# -------------------------------------------------------------------
# Tool Definitions
# -------------------------------------------------------------------

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "sap_connect",
        "description": "실행 중인 SAP GUI에 연결합니다. SAP GUI가 이미 실행 중이고 로그인된 상태여야 합니다. system 파라미터로 시스템 ID(SID)를 지정하면 자동으로 올바른 연결을 찾습니다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "connection_index": {"type": "integer", "default": 0, "description": "연결 인덱스 (기본: 0)"},
                "session_index": {"type": "integer", "default": 0, "description": "세션 인덱스 (기본: 0)"},
                "system": {"type": "string", "description": "시스템 ID(SID)로 연결 자동 탐색 (예: 'PR1', 'ERP')"},
            },
        },
    },
    {
        "name": "sap_connections",
        "description": "현재 SAP GUI의 모든 활성 연결 목록을 조회합니다. 각 연결의 시스템 ID, 클라이언트, 세션 정보를 반환합니다.",
        "inputSchema": {"type": "object", "properties": {}},
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
        "name": "sap_map_scan",
        "description": (
            "현재 SAP session의 모든 window와 component 계층, 전체 ALV/Table/Tree "
            "정의, menu/toolbar, modal stack을 읽기 전용 UiState 지도로 수집합니다."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"slot_id": {"type": "string"}},
        },
    },
    {
        "name": "sap_map_list",
        "description": "저장된 최신 SAP UI 지도 목록을 환경 scope로 조회합니다.",
        "inputSchema": {"type": "object", "properties": {
            "tcode": {"type": "string"}, "system_id": {"type": "string"},
            "client": {"type": "string"}, "language": {"type": "string"}
        }},
    },
    {
        "name": "sap_map_search",
        "description": "버전 지도에서 상태, UI 요소, 전이를 민감값 없이 검색합니다.",
        "inputSchema": {"type": "object", "properties": {
            "map_id": {"type": "string"}, "tcode": {"type": "string"},
            "query": {"type": "string"}, "system_id": {"type": "string"},
            "client": {"type": "string"}, "language": {"type": "string"},
            "version": {"type": "integer", "minimum": 1},
            "kinds": {"type": "array", "items": {"enum": ["state", "component", "transition"]}},
            "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 50}
        }, "required": ["map_id", "tcode", "query"]},
    },
    {
        "name": "sap_map_path",
        "description": "저장된 지도에서 상태 또는 UI 요소까지 최단 실행 경로를 조회합니다.",
        "inputSchema": {"type": "object", "properties": {
            "map_id": {"type": "string"}, "tcode": {"type": "string"},
            "from_state_id": {"type": "string"}, "target": {"type": "string"},
            "target_type": {"enum": ["state", "component"], "default": "state"},
            "system_id": {"type": "string"}, "client": {"type": "string"},
            "language": {"type": "string"}, "version": {"type": "integer", "minimum": 1},
            "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 50}
        }, "required": ["map_id", "tcode", "from_state_id", "target"]},
    },
    {
        "name": "sap_map_job_status",
        "description": "cooperative 지도 생성 job과 대기 중인 사용자 질문을 조회합니다.",
        "inputSchema": {"type": "object", "properties": {
            "job_id": {"type": "string"},
            "status": {"enum": ["queued", "running", "needs_user_input", "paused", "interrupted", "completed", "failed", "cancelled"]},
            "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 50}
        }},
    },
    {
        "name": "sap_map_report",
        "description": "저장된 지도의 구조 coverage, collection 종류, modal, unreachable 및 scan 계측을 업무 값 없이 조회합니다.",
        "inputSchema": {"type": "object", "properties": {
            "map_id": {"type": "string"}, "tcode": {"type": "string"},
            "system_id": {"type": "string"}, "client": {"type": "string"},
            "language": {"type": "string"}, "version": {"type": "integer", "minimum": 1}
        }, "required": ["map_id", "tcode"]},
    },
    {
        "name": "sap_map_job_cancel",
        "description": "지도 생성 job을 취소합니다.",
        "inputSchema": {"type": "object", "properties": {"job_id": {"type": "string"}}, "required": ["job_id"]},
    },
    {
        "name": "sap_map_augment_start",
        "description": (
            "완료된 기존 지도에서 기준 state와 사용자가 추가할 누락 화면/분기를 지정해 "
            "durable needs_user_input job을 만듭니다. SAP 화면은 조작하지 않습니다."
        ),
        "inputSchema": {"type": "object", "properties": {
            "map_id": {"type": "string"}, "tcode": {"type": "string"},
            "from_state_id": {"type": "string"}, "request": {"type": "string"},
            "system_id": {"type": "string"}, "client": {"type": "string"},
            "language": {"type": "string"}, "version": {"type": "integer", "minimum": 1},
            "scope": {"type": "object"}, "slot_id": {"type": "string"}
        }, "required": ["map_id", "tcode", "from_state_id", "request"]},
    },
    {
        "name": "sap_map_traverse_start",
        "description": "지도 순회 job을 daemon에 등록하고 즉시 queued 상태로 반환합니다. active 모드는 테스트 클라이언트 확인이 필요합니다.",
        "inputSchema": {"type": "object", "properties": {
            "tcode": {"type": "string"}, "map_id": {"type": "string"},
            "mode": {"enum": ["passive", "reversible", "allowlisted"], "default": "passive"},
            "budget": {"type": "object"},
            "allowed_actions": {"type": "array", "items": {"type": "string"}},
            "allowed_branches": {
                "type": "array",
                "description": (
                    "테스트 client에서만 허용할 exact navigation branch. "
                    "source state/fingerprint, logical/SAP id, type, restore_vkey를 고정합니다."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "rule_id": {"type": "string"},
                        "action": {"enum": [
                            "press", "select_menu", "press_toolbar_button",
                            "select_context_menu_item"
                        ]},
                        "source_state_id": {"type": "string"},
                        "source_fingerprint": {"type": "string"},
                        "target_logical_id": {"type": "string"},
                        "target_sap_id": {"type": "string"},
                        "component_type": {"enum": [
                            "GuiButton", "GuiMenu", "GuiToolbar", "GuiShell"
                        ]},
                        "item_id": {"type": "string"},
                        "restore_vkey": {"enum": [3, 12]},
                        "expected_program": {"type": "string"},
                        "expected_screen_number": {"type": "integer", "minimum": 0},
                        "expected_modal_signature": {"type": "string"}
                    },
                    "required": [
                        "action", "target_logical_id", "target_sap_id",
                        "component_type", "restore_vkey"
                    ],
                    "anyOf": [
                        {"required": ["source_state_id"]},
                        {"required": ["source_fingerprint"]}
                    ]
                }
            },
            "navigate": {"type": "boolean", "default": True},
            "test_client_confirmed": {"type": "boolean", "default": False},
            "slot_id": {"type": "string"}
        }, "required": ["tcode"]},
    },
    {
        "name": "sap_map_traverse_resume",
        "description": "사용자 답변·수동 SAP 이동 후 재개하거나, budget-paused job에 더 큰 budget을 적용합니다. 답변 값은 디스크에 저장하지 않습니다.",
        "inputSchema": {"type": "object", "properties": {
            "job_id": {"type": "string"}, "answers": {"type": "object"},
            "budget": {"type": "object"},
            "slot_id": {"type": "string"}
        }, "required": ["job_id"]},
    },
    {
        "name": "sap_plan_preview",
        "description": "자연어 SAP 요청을 저장된 UI 지도만 사용해 전체 명령·guard·예상 전이·위험·승인 조건이 포함된 실행 계획으로 컴파일합니다. SAP 화면은 스캔하거나 조작하지 않습니다.",
        "inputSchema": {"type": "object", "properties": {
            "request": {"type": "string"}, "tcode": {"type": "string"},
            "map_id": {"type": "string"}, "system_id": {"type": "string"},
            "client": {"type": "string"}, "current_state_id": {"type": "string"},
            "goal_query": {"type": "string"}, "values": {"type": "object"},
            "success_criteria": {"type": "array", "items": {"type": "string"}},
            "api_version": {"type": "string"}, "gui_version": {"type": "string"},
            "application_release": {"type": "string"}, "schema_version": {"type": "string"}
        }, "required": ["request"]},
    },
    {
        "name": "sap_plan_approve",
        "description": "관리자가 daemon 메모리에 plan/step/agent/slot에 묶인 일회용 승인 토큰을 발급합니다.",
        "inputSchema": {"type": "object", "properties": {
            "plan_id": {"type": "string"}, "plan_hash": {"type": "string"},
            "subject_agent": {"type": "string"},
            "step_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1},
            "expires_in_seconds": {"type": "number", "exclusiveMinimum": 0, "maximum": 900},
            "slot_id": {"type": "string"}
        }, "required": ["plan_id", "plan_hash", "subject_agent", "step_ids"]},
    },
    {
        "name": "sap_plan_execute",
        "description": "daemon 메모리에 보관된 불변 실행 계획을 한 번의 IPC로 실행합니다. 같은 execution_id는 재실행하지 않습니다.",
        "inputSchema": {"type": "object", "properties": {
            "plan_id": {"type": "string"}, "plan_hash": {"type": "string"},
            "execution_id": {"type": "string"},
            "verify_mode": {"type": "string", "enum": ["cheap", "full"], "default": "cheap"},
            "approval_token": {"type": "string"},
            "slot_id": {"type": "string"}
        }, "required": ["plan_id", "plan_hash", "execution_id"]},
    },
    {
        "name": "sap_job_status",
        "description": "daemon의 계획 실행과 지도 순회 공통 작업 상태를 값 노출 없이 조회합니다.",
        "inputSchema": {"type": "object", "properties": {
            "job_id": {"type": "string"}, "slot_id": {"type": "string"}
        }},
    },
    {
        "name": "sap_job_reconcile",
        "description": "outcome_unknown 또는 dirty checkpoint를 수동 검증 뒤 명시적으로 정리합니다. 자동 재실행하지 않습니다.",
        "inputSchema": {"type": "object", "properties": {
            "job_id": {"type": "string"},
            "resolution": {
                "enum": [
                    "verified_applied",
                    "verified_not_applied",
                    "verified_failed",
                    "release_after_manual_verification",
                    "cancel",
                ]
            },
            "slot_id": {"type": "string"}
        }, "required": ["job_id", "resolution"]},
    },
    {
        "name": "sap_inspect_element",
        "description": (
            "SAP GUI element_id 하나를 live로 검사해 Type/SubType, 값, changeable, "
            "사용 가능한 Scripting capability, SAP GUI Scripting API graph preflight, 콤보 entries 등을 반환합니다. "
            "전용 도구가 없거나 어떤 조작이 가능한지 모를 때 먼저 사용하세요."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "element_id": {"type": "string", "description": "검사할 SAP GUI element ID"},
            },
            "required": ["element_id"],
        },
    },
    {
        "name": "sap_combo_entries",
        "description": (
            "GuiComboBox의 선택 가능한 Entries(key/value/pos)를 직접 조회합니다. "
            "SAP COM의 Entries/Entries()/0-base/1-base 컬렉션 변형을 모두 시도합니다."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "element_id": {"type": "string", "description": "조회할 GuiComboBox element ID"},
            },
            "required": ["element_id"],
        },
    },
    {
        "name": "sap_scripting_query",
        "description": (
            "프로젝트에 내장된 SAP GUI Scripting API 그래프 온톨로지를 조회합니다. "
            "component_type(예: GuiComboBox), intent(예: set/select_node), node_id로 필요한 subgraph만 가져옵니다. "
            "전용 도구나 조작법을 추측하지 말고 이 그래프를 먼저 조회하세요."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "component_type": {"type": "string", "description": "SAP GUI Scripting Type (예: GuiComboBox, GuiShell)"},
                "intent": {"type": "string", "description": "의도/action (예: set, select, press, select_node)"},
                "node_id": {"type": "string", "description": "직접 조회할 graph node id (예: object:GuiComboBox)"},
                "depth": {"type": "integer", "description": "주변 graph 확장 깊이", "default": 1},
                "coverage": {"type": "boolean", "description": "공식 API 대비 coverage matrix 요약 반환", "default": False},
                "include_gaps": {"type": "boolean", "description": "coverage gap 샘플 포함", "default": True},
                "gap_limit": {"type": "integer", "description": "종류별 gap 최대 출력 수", "default": 50, "minimum": 0},
            },
        },
    },
    {
        "name": "sap_api_object_action",
        "description": (
            "SAP GUI Scripting의 Application/Connection/Session/SessionInfo 계층에서 "
            "공식 method/property를 실행합니다. 문서 member와 권한·위험도를 확인하며, "
            "visual element는 sap_ui_action을 사용하세요."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "scope": {
                    "type": "string",
                    "description": (
                        "sap_gui_auto, rot_wrapper, application, connection, session, "
                        "session_info, active_window 또는 이전 호출의 handle:<id>"
                    ),
                },
                "action": {
                    "type": "string",
                    "enum": ["call_method", "get_property", "set_property"],
                },
                "member_name": {"type": "string", "description": "공식 API member 이름"},
                "method_args": {
                    "type": "array",
                    "items": {},
                    "description": (
                        "공식 signature의 ByVal 입력만 순서대로 전달합니다. "
                        "ByRef/out은 자동 생성되며, 객체 인자는 handle:<id> 또는 "
                        "{\"handle_id\": \"...\"}로 전달합니다."
                    ),
                },
                "args_json": {"type": "string", "description": "method_args JSON 배열 문자열"},
                "property_value": {"description": "set_property 값"},
                "slot_id": {"type": "string", "description": "대상 세션 슬롯"},
            },
            "required": ["scope", "action", "member_name"],
        },
    },
    {
        "name": "sap_event_subscribe",
        "description": "상주 daemon에서 검증된 SAP application/session 이벤트를 구독합니다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "scope": {"type": "string", "enum": ["application", "session"]},
                "events": {"type": "array", "items": {"type": "string"}},
                "ttl_seconds": {"type": "number", "default": 300},
                "queue_limit": {"type": "integer", "default": 256, "minimum": 1},
                "redact_sensitive": {"type": "boolean", "default": True},
                "slot_id": {"type": "string"},
            },
            "required": ["scope", "events"],
        },
    },
    {
        "name": "sap_event_poll",
        "description": "SAP 이벤트 구독 큐를 비차단 방식으로 조회합니다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "subscription_id": {"type": "string"},
                "cursor": {"type": "integer", "default": 0, "minimum": 0},
                "limit": {"type": "integer", "default": 100, "minimum": 1},
                "slot_id": {"type": "string"},
            },
            "required": ["subscription_id"],
        },
    },
    {
        "name": "sap_event_unsubscribe",
        "description": "SAP 이벤트 구독을 해제합니다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "subscription_id": {"type": "string"},
                "slot_id": {"type": "string"},
            },
            "required": ["subscription_id"],
        },
    },
    {
        "name": "sap_event_list",
        "description": "현재 agent와 session slot이 소유한 SAP 이벤트 구독을 조회합니다.",
        "inputSchema": {
            "type": "object",
            "properties": {"slot_id": {"type": "string"}},
        },
    },
    {
        "name": "sap_ui_action",
        "description": (
            "SAP GUI Scripting capability 지도와 live element Type을 기반으로 범용 UI 액션을 수행합니다. "
            "실행 전에 ontology_preflight로 Type/SubType, capability, API graph 요약을 자동 조회해 응답에 포함합니다. "
            "set/select/press/focus/read/search_help, 트리 node 액션(select_node/expand_node/collapse_node/doubleclick_node/press_node), "
            "테이블 select_row 등을 지원합니다. 전용 도구가 없을 때의 fallback 실행 도구입니다."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "element_id": {"type": "string", "description": "대상 SAP GUI element ID"},
                "action": {"type": "string", "description": "수행할 액션: set/select/press/focus/read/read_table/read_tree/read_cell/set_cell/set_current_cell/click_cell/context_menu/context_menu_item/select_context_menu_item/select_column/deselect_column/clear_selection/press_toolbar_button/press_toolbar_context_button/select_toolbar_menu_item/scroll/scroll_to/page_down/page_up/append_text/clear_text/maximize/restore/minimize/close/call_method/get_property/set_property/search_help/select_node/expand_node/collapse_node/doubleclick_node/press_node/select_row/doubleclick_cell 등"},
                "value": {"type": "string", "description": "액션 값. 필드/콤보 값, node_key, row 번호 등"},
                "node_key": {"type": "string", "description": "트리 노드 key (트리 액션용)"},
                "column": {"type": "string", "description": "트리 버튼/링크 컬럼 또는 테이블 컬럼"},
                "row": {"type": "integer", "description": "테이블 행 번호 (select_row용, 0-based)"},
                "max_rows": {"type": "integer", "description": "read_table 최대 행 수", "default": 50},
                "columns": {
                    "oneOf": [
                        {"type": "string"},
                        {"type": "array", "items": {"type": "string"}},
                    ],
                    "description": "read_table 컬럼 목록. 쉼표 구분 문자열 또는 문자열 배열",
                },
                "method_args": {
                    "type": "array",
                    "items": {},
                    "description": (
                        "call_method의 ByVal 입력 배열. ByRef/out은 graph signature로 "
                        "자동 생성되며 객체 인자는 handle:<id> 또는 handle_id 객체 사용"
                    ),
                },
                "property_name": {"type": "string", "description": "get_property/set_property 대상 속성명"},
                "property_value": {"description": "set_property 값. 생략 시 value 사용"},
            },
            "required": ["element_id", "action"],
        },
    },
    {
        "name": "sap_run",
        "description": (
            "SAP 트랜잭션을 실행합니다. 예: MM03(자재조회), VA01(수주생성), ME21N(구매오더). "
            "응답의 recipe_hints 필드에 해당 tcode의 기존 레시피가 자동 포함됩니다. "
            "후보가 있으면 먼저 검토하고, 목적에 맞는 레시피가 있으면 sap_recipe_show로 steps를 가져와 따라 실행하세요. "
            "직접 탐색(sap_tree_read 등)은 recipe_hints에 맞는 레시피가 없을 때만 수행하세요."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "tcode": {"type": "string", "description": "트랜잭션 코드 (예: MM03)"},
                "report": {
                    "type": "string",
                    "description": (
                        "SA38/SE38 등 report launcher에서 입력할 ABAP report/program 이름. "
                        "DangerousReport 정책 분류와 preflight 승인 판단에 사용됩니다."
                    ),
                },
                "program": {
                    "type": "string",
                    "description": "report의 별칭. report가 있으면 report 값이 우선됩니다.",
                },
                "report_field_id": {
                    "type": "string",
                    "default": "wnd[0]/usr/ctxtRS38M-PROGRAMM",
                    "description": "report/program을 입력할 SAP GUI 필드 ID.",
                },
                "execute_report": {
                    "type": "boolean",
                    "default": False,
                    "description": "report 입력 후 Execute(F8, VKey 8)를 전송할지 여부.",
                },
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
        "description": (
            "SAP 화면의 필드 값을 설정합니다. GuiComboBox 콤보박스도 element_id로 직접 설정할 수 있습니다. "
            "실행 전에 live Type/SubType과 Scripting API graph를 ontology_preflight로 자동 조회해 응답에 포함합니다."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "element_id": {"type": "string", "description": "필드/콤보박스 ID (예: wnd[0]/usr/ctxtRMMG1-MATNR, wnd[0]/usr/cmbLFART)"},
                "value": {"type": "string", "description": "설정할 값. 콤보박스는 key 또는 표시값"},
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
        "description": "SAP 화면 필드의 현재 값을 읽습니다. 여러 필드를 한번에 조회할 수 있습니다. GuiComboBox는 현재 key/text와 선택 가능한 entries(key/value)도 반환합니다.",
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
        "description": (
            "SAP 화면의 버튼을 클릭하거나 탭을 선택합니다. GuiTab이면 자동으로 Select()를 호출합니다. "
            "실행 전에 live Type/SubType과 Scripting API graph를 ontology_preflight로 자동 조회해 응답에 포함합니다."
        ),
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
            "경로가 있으면 execution_plan이 반환되므로 그대로 실행하면 됩니다. "
            "query_type으로 시맨틱 검색, 크로스 트랜잭션 필드 조회, 프로세스 가이드, 종속성 체인을 사용할 수 있습니다."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "tcode": {"type": "string", "description": "트랜잭션 코드"},
                "field_search": {"type": "string", "description": "필드명/라벨 검색 (선택)"},
                "action_search": {"type": "string", "description": "액션 경로 검색 (예: 'shipping point', '가격결정'). SPRO 등에서 저장된 네비게이션 경로를 찾습니다."},
                "list_all": {"type": "boolean", "default": False, "description": "저장된 모든 온톨로지 목록"},
                "query_type": {
                    "type": "string",
                    "enum": ["field_semantic", "field_cross", "process_guide", "dependency_chain"],
                    "description": (
                        "확장 쿼리 타입. "
                        "field_semantic: synonyms/domain_context 기반 퍼지 필드 검색 (field_search 필수). "
                        "field_cross: 특정 필드가 사용되는 모든 트랜잭션/화면 조회 (field_search 필수). "
                        "process_guide: 프로세스 단계별 가이드 (tcode 또는 field_search로 프로세스 ID 지정). "
                        "dependency_chain: 필드 종속성 체인 추적 (field_search 필수)."
                    ),
                },
            },
        },
    },
    {
        "name": "sap_ontology_ask",
        "description": "학습된 SAP 온톨로지에 자연어로 질문하고 그래프 기반 답변을 받습니다. 예: 'VA01이 뭐야?', 'MATNR 어디서 사용?'",
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "온톨로지에 물어볼 자연어 질문"},
            },
            "required": ["question"],
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
        "name": "sap_ontology_rebuild",
        "description": "온톨로지 전체 인덱스를 재구축합니다: field registry, field dependencies, document flow. 관리자 권한이 필요합니다.",
        "inputSchema": {"type": "object", "properties": {}},
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
        "name": "sap_capture",
        "description": "현재 SAP GUI 화면을 스크린샷으로 캡처합니다. 저장 경로와 함께 이미지를 직접 반환하여 AI가 화면을 볼 수 있습니다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "label": {"type": "string", "description": "스크린샷 라벨"},
                "output": {"type": "string", "description": "저장 경로. 생략 시 자동 생성"},
            },
        },
    },
    {
        "name": "sap_capture_list",
        "description": "저장된 SAP GUI 스크린샷 목록을 조회합니다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "tcode": {"type": "string", "description": "트랜잭션 코드 필터"},
            },
        },
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
        "description": (
            "매크로 녹화 중 단계를 기록하면서 동시에 SAP에 실행합니다. "
            "sap_macro_control(start) 먼저 호출해야 합니다. "
            "IMPORTANT: value는 반드시 실제 값(예: '4500001234', 'KR10')이어야 합니다. "
            "${var}, {{var}}, {var} 같은 플레이스홀더는 절대 사용하지 마세요. "
            "변수화는 finish 시 자동으로 처리됩니다."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["set", "press", "vkey", "run", "checkbox", "radio", "combo", "tab", "menu", "read-screen", "table-select", "table-cell", "table-doubleclick", "click-cell", "ui-action", "tree-select", "tree-expand", "tree-doubleclick", "tree-collapse"]},
                "target": {"type": "string", "description": "대상 ID 또는 값. 트리/테이블 액션은 element_id"},
                "value": {"type": "string", "description": "실제 값 (예: '4500001234'). ${var} 같은 플레이스홀더 사용 금지. 변수화는 finish에서 자동 처리."},
                "params": {"type": "object", "description": "추가 파라미터. 예: {\"row\": 0, \"column\": \"MATNR\"}, {\"node_key\": \"0001\"}, {\"ui_action\": \"doubleclick_cell\"}"},
                "row": {"type": "integer", "description": "테이블 행 번호 (0-based)"},
                "column": {"type": "string", "description": "테이블/트리 컬럼"},
                "node_key": {"type": "string", "description": "트리 node_key"},
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
                "variables": {"type": ["object", "array"], "description": "변수 지정 (finish 시). {변수명: 실제값}, {변수명: {value: 실제값, desc: 설명}}, 또는 [{name, value, description}]"},
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
                "variables": {"type": ["object", "array", "string"], "description": "변수 값. {name: value}, {name: {value}}, [{name, value}], 또는 'name=value'"},
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
            "전제조건, 결과 화면 등을 포함해야 나중에 올바른 레시피를 찾을 수 있습니다. "
            "[중복 탐지 HITL] 신규 레시피 저장 시 유사 레시피가 자동 탐지됩니다. "
            "결과 status='duplicate_candidates'가 오면 candidates/recommended/reason을 사용자에게 보여주고 "
            "확인받은 후 on_duplicate=keep_existing|replace|keep_both로 다시 호출하세요. "
            "사용자 확인 없이 임의로 on_duplicate를 설정하지 마세요."
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
                "replay_success_criteria": {
                    "type": "object",
                    "description": (
                        "향후 sap_recipe_replay_execute에서 재사용할 최종 검증 기준. "
                        "예: expected_tcode, expected_screen_number, title_contains, "
                        "allowed_status_types, blocked_status_types, require_no_modal. "
                        "빈 객체를 전달하면 저장된 기준을 초기화합니다."
                    ),
                },
                "on_duplicate": {
                    "type": "string",
                    "enum": ["keep_existing", "replace", "keep_both"],
                    "description": (
                        "중복 탐지 결과에 대한 사용자 결정. 미지정 시 유사 레시피 자동 탐지 후 status='duplicate_candidates'로 반환. "
                        "'keep_existing'=저장 취소 / 'replace'=기존 삭제 후 저장(success_count 승계) / 'keep_both'=강제 저장. "
                        "반드시 사용자 확인을 받은 뒤에만 지정하세요."
                    ),
                },
                "approved_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "on_duplicate='replace'일 때 필수. 1차 응답의 candidate_ids를 그대로 전달하세요. "
                        "서버가 현재 유사 후보 집합과 비교해 변동이 있으면 status='candidate_drift'로 거부합니다 "
                        "(사용자가 승인하지 않은 레시피가 삭제되는 것을 방지)."
                    ),
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
        "name": "sap_recipe_query",
        "description": (
            "트랜잭션 코드와/또는 키워드로 레시피를 통합 검색합니다. "
            "BM25 lexical search, lightweight semantic overlap, tcode/step_kind boost, recipe quality score를 "
            "섞은 hybrid reranking으로 sap_recipe_search보다 정밀하게 검색합니다. "
            "사용자의 SAP 작업 요청을 받았을 때, 직접 탐색 전에 이 도구로 기존 레시피를 먼저 확인하세요. "
            "결과에는 retrieval 점수와 match_reasons가 포함되며, 결과가 1개면 execution_plan이 바로 포함됩니다. "
            "여러 개면 context를 비교하여 sap_recipe_show로 상세 조회하세요."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "tcode": {"type": "string", "description": "트랜잭션 코드 (예: VL33N, ME21N)"},
                "keywords": {"type": "string", "description": "검색 키워드 (예: '출고 납품 변경', 'shipping point 설정')"},
            },
        },
    },
    {
        "name": "sap_recipe_chain_search",
        "description": (
            "여러 트랜잭션을 걸치는 복합 레시피를 검색합니다. 예: SPRO에서 설정 후 MM03으로 검증, "
            "또는 VA01에서 수주 생성 후 VL01N으로 출고 등 멀티-tcode 워크플로우를 찾을 때 사용. "
            "tcodes에 지정한 트랜잭션이 모두 포함된 레시피(match='all', 기본) 또는 하나라도 포함된 레시피(match='any')를 반환합니다. "
            "결과의 tcode_sequence로 어떤 순서로 진행되는지 확인 후 sap_recipe_show로 상세 단계를 가져오세요."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "tcodes": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "검색할 트랜잭션 코드 목록 (예: [\"SPRO\", \"MM03\"])",
                },
                "match": {
                    "type": "string",
                    "enum": ["all", "any"],
                    "description": "'all'(기본): 모든 tcode가 포함된 레시피만. 'any': 하나라도 포함하면 매칭.",
                },
            },
            "required": ["tcodes"],
        },
    },
    {
        "name": "sap_recipe_version_up",
        "description": (
            "기존 레시피의 새 버전을 저장합니다. SAP 패치/업그레이드로 화면이나 버튼이 변경되어 기존 단계가 더 이상 동작하지 않을 때, "
            "수정된 단계를 새 버전으로 저장하고 이전 단계는 _archive 폴더에 보존합니다. "
            "version 번호가 +1 되며, failure_count/disabled 상태는 초기화됩니다 (success_count·description·keywords는 승계). "
            "과거 버전 목록은 sap_recipe_history로 조회할 수 있습니다."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "recipe_id": {"type": "string", "description": "버전업 대상 레시피 ID"},
                "new_steps": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "tool": {"type": "string"},
                            "args": {"type": "object"},
                            "hint": {"type": "string"},
                        },
                        "required": ["tool"],
                    },
                    "description": "새 버전의 실행 단계 (성공 경로만)",
                },
                "new_context": {"type": "string", "description": "새 버전의 context (선택; 미지정 시 기존 유지)"},
                "change_note": {"type": "string", "description": "변경 사유 메모 (예: 'EHP8 패치로 btn[5]→btn[7]')"},
                "replay_success_criteria": {
                    "type": "object",
                    "description": (
                        "새 버전에서 사용할 replay 최종 검증 기준. 미지정 시 기존 기준을 유지하고, "
                        "빈 객체를 지정하면 기준을 초기화합니다."
                    ),
                },
            },
            "required": ["recipe_id", "new_steps"],
        },
    },
    {
        "name": "sap_recipe_history",
        "description": (
            "레시피의 과거 버전 목록을 조회합니다. _archive 폴더에 보관된 이전 버전들의 메타(version, step_count, context 등)를 반환합니다. "
            "현재 활성 버전과 이전 버전들을 비교하여 어떤 단계가 어떻게 바뀌었는지 추적할 때 사용합니다."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "recipe_id": {"type": "string", "description": "조회할 레시피 ID"},
            },
            "required": ["recipe_id"],
        },
    },
    {
        "name": "sap_recipe_replay_plan",
        "description": (
            "레시피를 바로 실행하기 전에 replay 검증 계획을 생성합니다. "
            "precheck → dry-run plan → execute → verify → report 흐름을 구조화해 반환하며, "
            "현재 tcode/screen과 레시피 시작 조건의 일치 여부, 실행 전 예상 단계, 실행 후 status/modal/screen_summary 검증 기준, "
            "성공 시 sap_recipe_success 및 실패 시 sap_recipe_failure 보고 지침을 포함합니다. 이 도구 자체는 SAP를 조작하지 않습니다."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "recipe_id": {"type": "string", "description": "replay 계획을 만들 레시피 ID"},
                "current_tcode": {
                    "type": "string",
                    "description": "현재 SAP tcode (선택). 제공하면 레시피 시작 tcode와 비교합니다.",
                },
                "current_screen": {
                    "type": "string",
                    "description": "현재 화면 번호/요약 식별자 (선택). report/verify 컨텍스트에 포함합니다.",
                },
                "allow_disabled": {
                    "type": "boolean",
                    "description": "true이면 disabled 레시피도 blocked 처리하지 않고 검토 계획을 반환합니다.",
                },
            },
            "required": ["recipe_id"],
        },
    },
    {
        "name": "sap_recipe_replay_execute",
        "description": (
            "레시피 replay 계획을 실제로 실행합니다. 실행 전 precheck와 step별 권한/approval/risk 검사를 수행하고, "
            "허용된 SAP action/form/read step만 순서대로 호출합니다. 실행 후 sap_status, sap_modal, sap_screen_summary를 "
            "자동 수집해 검증하며, 성공 시 sap_recipe_success를 자동 호출하고 실패 시 sap_recipe_failure를 자동 호출합니다."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "recipe_id": {"type": "string", "description": "실행할 레시피 ID"},
                "current_tcode": {
                    "type": "string",
                    "description": "현재 SAP tcode (선택). 제공하면 레시피 시작 tcode와 비교합니다.",
                },
                "current_screen": {
                    "type": "string",
                    "description": "현재 화면 번호/요약 식별자 (선택). precheck/report 컨텍스트에 포함합니다.",
                },
                "allow_disabled": {
                    "type": "boolean",
                    "description": "true이면 disabled 레시피도 blocked 처리하지 않고 실행을 허용합니다.",
                },
                "allow_modal": {
                    "type": "boolean",
                    "description": "true이면 실행 후 modal dialog가 남아 있어도 verify 실패로 보지 않습니다.",
                },
                "auto_report": {
                    "type": "boolean",
                    "description": "false이면 success/failure 카운터를 자동 기록하지 않습니다. 기본값은 true입니다.",
                },
                "success_criteria": {
                    "type": "object",
                    "description": (
                        "실행 후 검증 기준 커스터마이징. 명시하면 기본적으로 recipe metadata에 저장되어 "
                        "다음 replay_execute에서 재사용됩니다. 예: expected_tcode, expected_screen_number, "
                        "title_contains, allowed_status_types, blocked_status_types, require_no_modal."
                    ),
                },
                "save_success_criteria": {
                    "type": "boolean",
                    "description": (
                        "success_criteria를 recipe metadata에 저장할지 여부. 기본값은 "
                        "success_criteria가 비어있지 않으면 true입니다. false이면 현재 실행에만 적용합니다."
                    ),
                },
                "observation_scope": {
                    "type": "string",
                    "description": (
                        "step별 관찰값 저장 범위. final(기본)=최종 관찰값만, "
                        "status=각 step 후 session/status/modal, screen=각 step 후 screen_summary까지 포함."
                    ),
                    "enum": ["final", "status", "screen"],
                },
            },
            "required": ["recipe_id"],
        },
    },
    {
        "name": "sap_recipe_failure",
        "description": (
            "레시피 실행이 실패했음을 기록합니다. sap_recipe_search/show로 가져온 레시피의 단계를 따라 실행했는데 "
            "예상한 결과가 나오지 않았거나 도중에 막혔을 때 호출하세요. "
            "성공/실패 누적이 3회 이상이고 실패율이 50% 이상이면 해당 레시피는 자동 비활성화되어 "
            "이후 검색 결과에서 제외됩니다(sap_recipe_enable로 수동 복구 가능). "
            "실패 보고는 SAP 패치/업그레이드 후 깨진 레시피를 자연스럽게 걸러내기 위한 신호입니다."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "recipe_id": {"type": "string", "description": "실패한 레시피 ID"},
                "reason": {
                    "type": "string",
                    "description": "실패 이유 (선택). 어느 단계에서 무엇이 다르게 동작했는지 간단히.",
                },
            },
            "required": ["recipe_id"],
        },
    },
    {
        "name": "sap_recipe_enable",
        "description": (
            "비활성화된 레시피를 수동 재활성화합니다. SAP 환경 복구나 레시피 수정(sap_recipe_save로 동일 ID 업데이트) 후 "
            "사용할 수 있도록 되돌릴 때 호출하세요. 기본적으로 failure_count도 0으로 초기화됩니다."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "recipe_id": {"type": "string", "description": "재활성화할 레시피 ID"},
                "reset_failure_count": {
                    "type": "boolean",
                    "description": "true(기본) 시 failure_count도 0으로 초기화. false면 누적값 유지.",
                },
            },
            "required": ["recipe_id"],
        },
    },
    {
        "name": "sap_recipe_pending",
        "description": (
            "자동 평가에서 레시피 후보로 분류되었으나 아직 승인되지 않은 세션 목록을 반환합니다. "
            "sap_session_end 호출 시 응답의 recipe_suggestion 필드로도 같은 정보가 즉시 노출되지만, "
            "이 도구는 누적된 모든 미승인 후보를 한 번에 확인할 때 사용합니다. "
            "각 항목을 sap_session_show로 검토 후 sap_session_approve로 레시피화하세요."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "반환할 후보 최대 개수 (기본 20)"},
            },
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
        "name": "sap_session_mark",
        "description": (
            "현재 실행 세션에 명시적인 표식 step을 추가합니다. SAP 화면 조작이 아니더라도 "
            "파일 준비, 외부 bash/CLI, 사람 확인, 검증 절차처럼 recipe가 나중에 따라야 하는 "
            "단계를 step_kind/followable/requires_sap와 함께 기록할 때 사용합니다."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "대상 세션 ID"},
                "text": {"type": "string", "description": "표식 step 설명"},
                "step_kind": {"type": "string", "description": "note/bash/external/manual/check 등 step 종류"},
                "cli_invocation": {"type": "string", "description": "따라 실행할 외부 CLI/bash 원문"},
                "raw_argv": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "cli_invocation의 argv 배열 표현",
                },
                "followable": {"type": "boolean", "description": "recipe가 따라야 하는 단계인지 여부 (기본 true)"},
                "requires_sap": {"type": "boolean", "description": "SAP 연결/화면이 필요한 단계인지 여부 (기본 false)"},
            },
            "required": ["session_id", "text"],
        },
    },
    {
        "name": "sap_session_excel",
        "description": (
            "Excel 파일 조작/준비/검증 단계를 현재 실행 세션에 간단 기록합니다. "
            "SAP 작업 전후의 엑셀 열기, 값 입력, 필터, 복사/붙여넣기, export 확인 같은 "
            "비SAP 단계를 recipe가 따라갈 수 있도록 step_kind=excel로 저장합니다."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "대상 세션 ID"},
                "text": {"type": "string", "description": "엑셀 step 설명"},
                "action": {"type": "string", "description": "open/read/write/filter/copy/paste/export/check 등"},
                "workbook": {"type": "string", "description": "엑셀 파일 경로/이름"},
                "sheet": {"type": "string", "description": "시트 이름"},
                "range_ref": {"type": "string", "description": "셀/범위 예: A1, A1:D20"},
                "value": {"type": "string", "description": "입력/확인할 값"},
                "cli_invocation": {"type": "string", "description": "실제 실행할 외부 CLI/bash 원문"},
                "raw_argv": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "cli_invocation의 argv 배열 표현",
                },
                "followable": {"type": "boolean", "description": "recipe가 따라야 하는 단계인지 여부 (기본 true)"},
            },
            "required": ["session_id", "text"],
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
    {
        "name": "sap_session_candidates",
        "description": (
            "온톨로지 학습 후보(candidate) 세션 목록을 조회합니다. "
            "성공한 세션 중 3단계 이상의 핵심 흐름과 tcode를 포함하는 세션만 표시됩니다. "
            "관리자가 후보를 확인한 후 sap_session_approve로 승인하면 레시피로 변환됩니다."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "최대 결과 수 (기본 50)"},
            },
        },
    },
    {
        "name": "sap_session_approve",
        "description": (
            "후보(candidate) 세션을 승인하여 워크플로우 레시피로 변환합니다. "
            "정제된 세션 흐름이 레시피로 저장되고, 해당 tcode의 온톨로지에 자동 연결됩니다. "
            "승인된 레시피는 이후 sap_recipe_search로 검색하여 AI가 참고할 수 있습니다."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "승인할 세션 ID"},
                "approved_by": {"type": "string", "description": "승인자 이름 (기본: admin)"},
                "replay_success_criteria": {
                    "type": "object",
                    "description": (
                        "레시피로 저장할 replay 최종 검증 기준. 미지정 시 세션의 마지막 "
                        "transaction/screen에서 expected_tcode/expected_screen_number를 자동 추론합니다. "
                        "빈 객체를 지정하면 자동 추론 없이 기준을 비웁니다."
                    ),
                },
                "derive_replay_success_criteria": {
                    "type": "boolean",
                    "description": (
                        "replay_success_criteria가 미지정일 때 세션 최종 상태에서 검증 기준을 자동 생성할지 여부. "
                        "기본값은 true입니다."
                    ),
                },
            },
            "required": ["session_id"],
        },
    },
    {
        "name": "sap_session_pool",
        "description": "활성 SAP 세션 슬롯 목록을 조회합니다. 각 슬롯의 연결 상태, 할당된 에이전트, 현재 트랜잭션 등을 반환합니다.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "sap_session_allocate",
        "description": "새 SAP 세션 슬롯을 할당합니다. 병렬 작업 시 각 에이전트가 고유 슬롯(ses0, ses1 등)을 사용하여 독립적으로 SAP 작업을 수행할 수 있습니다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "slot_id": {"type": "string", "description": "슬롯 ID (예: 'ses0', 'ses1'). 고유해야 함."},
                "agent_name": {"type": "string", "description": "이 슬롯에 할당할 에이전트 이름"},
                "system": {"type": "string", "description": "연결할 시스템 ID(SID). 지정 시 자동 연결"},
                "session_index": {"type": "integer", "description": "세션 인덱스. 지정 시 해당 세션에 연결"},
                "create_new_session": {"type": "boolean", "default": False, "description": "true이면 ses0의 연결에서 새 SAP 세션을 생성하여 이 슬롯에 할당"},
            },
            "required": ["slot_id"],
        },
    },
    {
        "name": "sap_session_release",
        "description": "SAP 세션 슬롯을 해제합니다. close=true이면 SAP 세션도 함께 종료합니다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "slot_id": {"type": "string", "description": "해제할 슬롯 ID"},
                "close": {"type": "boolean", "default": False, "description": "true이면 SAP 세션도 종료"},
            },
            "required": ["slot_id"],
        },
    },
]

TOOL_DEFINITIONS.extend(mcp_tool_definitions())

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
    raw = os.getenv("SAP_CLI_MCP_SCREEN_SUMMARY", "0").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _include_post_action_context(command: str) -> bool:
    """Return whether MCP should enrich the command result after execution."""
    return command not in {"connect", "plan_approve", "plan_execute"}


def _include_screen_summary(command: str) -> bool:
    """Return whether MCP should include a compact screen summary."""
    return command not in {"connect", "plan_approve", "plan_execute"} and _screen_summary_enabled()


def _use_daemon() -> bool:
    """Use daemon by default unless SAP_CLI_USE_DAEMON=0/false/no/off."""
    raw = os.getenv("SAP_CLI_USE_DAEMON", "1").strip().lower()
    return raw not in {"0", "false", "no", "off", "direct"}


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
    slot_id: str = "ses0",
) -> dict[str, Any] | None:
    """daemon 경유 실행. 성공하면 결과 dict, 실패/미실행이면 None."""
    from . import daemon

    ctx: dict[str, Any] = {
        "source": "mcp",
        "tool_name": tool_name,
        "agent_name": agent_name,
        "include_post_action_context": _include_post_action_context(command),
        "include_screen_summary": _include_screen_summary(command),
        "slot_id": slot_id,
    }
    if session_id:
        ctx["session_id"] = session_id

    result = daemon.send_command(
        command, args, timeout=daemon.command_timeout(command), context=ctx
    )
    if result is not None:
        return result

    if not daemon.start_daemon():
        return None
    return daemon.send_command(
        command, args, timeout=daemon.command_timeout(command), context=ctx
    )


def _is_unknown_daemon_command_error(message: str) -> bool:
    normalized = message.lower()
    return (
        "unknown command" in normalized
        or "알수 없는 명령" in message
        or "알 수 없는 명령" in message
    )


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
        slot_id = str(normalized_args.pop("slot_id", "ses0"))

        # daemon 위임 모드
        if _use_daemon():
            daemon_result = _dispatch_via_daemon(
                command, normalized_args, name, effective_agent,
                session_id=_active_session_id,
                slot_id=slot_id,
            )
            if daemon_result is not None:
                if daemon_result.get("ok"):
                    data = daemon_result["data"]
                    if command == "session_start" and isinstance(data, dict):
                        _active_session_id = data.get("session_id")
                    elif command == "session_end" and isinstance(data, dict) and "error" not in data:
                        _active_session_id = None
                    if command == "capture" and isinstance(data, dict) and data.get("captured"):
                        return _capture_result(data)
                    return _json_result(data)
                if daemon_result.get("approval_required"):
                    return _json_result({
                        "approval_required": True,
                        "tool": daemon_result.get("tool"),
                        "reason": daemon_result.get("reason", ""),
                    })
                daemon_error = str(daemon_result.get("error", "daemon error"))
                if not _is_unknown_daemon_command_error(daemon_error):
                    return _error_result(daemon_error)
                # Running daemon can be older than the MCP server process after
                # an update. If it does not know a new command, fall back to
                # direct dispatch instead of surfacing a stale-daemon error.
            # daemon 사용 불가 → fallback to direct dispatch

        if command in {
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
            return _error_result(
                f"{name}는 persistent daemon 전용이며 direct fallback을 허용하지 않습니다."
            )

        ctx = DispatchContext(
            source="mcp",
            tool_name=name,
            agent_name=effective_agent,
            include_post_action_context=_include_post_action_context(command),
            include_screen_summary=_include_screen_summary(command),
            session_id=_active_session_id,
            slot_id=slot_id,
        )
        result = dispatch_command(command, normalized_args, ctx)

        if command == "session_start" and isinstance(result, dict):
            _active_session_id = result.get("session_id")
        elif command == "session_end" and isinstance(result, dict) and "error" not in result:
            _active_session_id = None

        if command == "capture" and isinstance(result, dict) and result.get("captured"):
            return _capture_result(result)
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
