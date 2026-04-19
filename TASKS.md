# SAP CLI 작업 이력

## 2026-03-31: Codex 리뷰 기반 전면 개선

### Phase 1: Codex 코드 리뷰 (10건 발견)

| # | 이슈 | 분류 | 상태 |
|---|------|------|------|
| 1 | Path Traversal 취약점 (macro/store, ontology/store) | 보안 | 완료 |
| 2 | 매크로 녹화/재생 데이터 불일치 (run/vkey value) | 기능 버그 | 완료 |
| 3 | 화면 검증 로직 반전 (실행 전→후) | 기능 버그 | 완료 |
| 4 | 온톨로지 그래프 네임스페이스 충돌 | 아키텍처 | 완료 |
| 5 | AI 에이전트 SAP 조작 제한 없음 | 보안 | 완료 (ReBAC) |
| 6 | COM 에러 핸들링이 실패를 숨김 | 안정성 | 완료 |
| 7 | MCP 선택적 의존성 임포트 크래시 | 즉시 크래시 | 완료 |
| 8 | 연결 상태 모델 부정확 (liveness) | 안정성 | 완료 |
| 9 | 화면 추출이 온톨로지 스펙과 불일치 | 품질 | 완료 |
| 10 | 프로덕션 준비 미비 (저장경로, 테스트, 중복) | 운영 | 완료 |

### Phase 2: 추가 구현

| 작업 | 내용 | 상태 |
|------|------|------|
| ReBAC 설계 | DESIGN_REBAC.md 작성 | 완료 |
| ReBAC P0 구현 | auth 모듈 (policy, checker, audit) | 완료 |
| MCP 권한 게이트 | handle_tool에 권한 체크 삽입 | 완료 |
| 서비스 레이어 | service.py 추출 (CLI/MCP 중복 제거) | 완료 |
| 데이터 외부화 | ~/.sap_cli/ 경로로 변경 + 시드 복사 | 완료 |
| 테스트 68개 | macro_store, recorder, ontology_store, graph, auth | 완료 |
| pywin32 조건부 | Windows 전용 의존성으로 변경 | 완료 |

### Phase 3: 온톨로지 JSON 품질 개선

| 작업 | 내용 | 상태 |
|------|------|------|
| VA01 필드명 오류 | VBKD-BSTNK→VBAK-BSTNK, KUNNR_SH→KUNNR | 완료 |
| MM03 타입 오류 | MARA-MEINS GuiComboBox→GuiTextField | 완료 |
| MM03 화면 4004 | 빈 화면에 조직레벨 필드 4개 + 뷰 선택 테이블 추가 | 완료 |
| MM03 기본데이터1 | 필드 7→13개 (중량단위, 구부문, 제품계층, EAN 등) | 완료 |
| VA01 오버뷰 | 필드 4→7개 (PO Date, Payment Terms, Incoterms) | 완료 |
| field_dependencies | 가상노드 → 실제 노드 참조로 교체 | 완료 |
| screen_flow | tcode 네임스페이스 + Back 네비게이션 추가 | 완료 |

### Phase 4: 실제 SAP 환경 테스트 대응

| 작업 | 내용 | 상태 |
|------|------|------|
| GetObjectTree 파서 재작성 | properties에 id만 있는 실제 JSON 구조 대응 | 완료 |
| 화면 스캔 속도 최적화 | GetObjectTree 대신 수동 탐색 직접 사용 | 완료 |
| 수동 탐색 개선 | usr/tbar/mbar 분리 스캔, 깊이 제한, 불필요 타입 skip | 완료 |

### Phase 5: Codex 일관성 검증 기반 수정

| # | 이슈 | 긴급도 | 상태 |
|---|------|--------|------|
| 1 | MCP _ACTION_MAP 미정의 (런타임 에러) | P0 | 완료 |
| 2 | MCP 매크로 value 정규화 누락 | P0 | 완료 |
| 3 | connect 싱글턴 불일치 | P0 | 완료 |
| 4 | menus 미수집 (mbar 스캔 누락) | P1 | 완료 |
| 5 | field_dependencies 노드 ID 불일치 | P1 | 완료 |
| 6 | MM03 max_length 누락 | P2 | 완료 |
| 7 | MacroStore 독스트링 이전 경로 | P2 | 완료 |
| 8 | 시드 복사 로직 (patterns 독립 체크) | P2 | 완료 |

### Phase 6: 문서 업데이트

| 파일 | 변경 내용 | 상태 |
|------|-----------|------|
| README.md | ReBAC 섹션, 테스트 섹션, 아키텍처 다이어그램 갱신 | 완료 |
| SETUP.md | 클론 URL, pywin32 설명, 권한 정책 설정 섹션 | 완료 |

---

## 커밋 이력 (2026-03-31)

```
e340878 Codex 일관성 검증 기반 6건 수정
a631fd1 화면 스캔 속도 2차 최적화: 불필요 타입 건너뛰기 + 깊이 제한
fcb4b0c 화면 스캔 속도 개선: GetObjectTree 대신 수동 탐색 직접 사용
6578426 GetObjectTree 파서 속도 최적화: 리프 노드만 FindById 호출
1e289db GetObjectTree 파서 전면 재작성 (실제 SAP 환경 대응)
6164493 README/SETUP 문서 업데이트
7d49f05 온톨로지 JSON 품질 개선 (Codex 검증 기반)
399393c Merge PR #1: pytest 설정 추가
ec29b59 Codex 리뷰 기반 10개 이슈 수정 + ReBAC 권한관리 구현
```

### Phase 7: 데몬 모드 + Shell REPL (2026-03-31)

| 작업 | 내용 | 상태 |
|------|------|------|
| 데몬 서버 | TCP 소켓 기반 상주 프로세스, COM 연결 유지 | 완료 |
| Shell REPL | 대화형/파이프 모드, 데몬 자동 시작 | 완료 |
| 중앙 dispatcher | CLI/데몬/MCP 통합 실행 경로 | 완료 |
| command_parser | 트랜잭션 코드 추출 유틸리티 | 완료 |

### Phase 8: Codex CC 코드리뷰 기반 수정 (2026-04-01)

| # | 이슈 | 심각도 | 상태 |
|---|------|--------|------|
| 1 | MCP vkey 인자 불일치 (vkey→key KeyError) | major | 완료 |
| 2 | 데몬 SAP 인스턴스 상태 불일치 | minor | 완료 |
| 3 | Shell 파이프 모드 에러 시 exit code 미반환 | major | 완료 |
| 4 | 트랜잭션 제한 정책 우회 (raw command fail-open) | major | 완료 |
| 5 | modal/macro_show/macro_delete 권한 검사 우회 | major | 완료 |
| 6 | 명령 정의 4중 중복 → 단일 COMMAND_REGISTRY 통합 | major | 완료 |

### Phase 9: AI 운영 커널 강화 (2026-04-01)

| 작업 | 내용 | 상태 |
|------|------|------|
| post_action 보강 | mutating action 뒤 session/status/modal/screen_summary 자동 첨부 | 완료 |
| MCP surface 분리 | agent profile별 list_tools/call_tool 노출 제한 | 완료 |
| transcript 적재 | before/after 상태, approval, 결과를 JSONL로 기록 | 완료 |
| transcript 조회 | CLI/MCP/Shell에서 transcript query 지원 | 완료 |
| replay review planner | transcript 기반 재실행 검토 계획 생성 | 완료 |
| 문서 갱신 | README, DESIGN_REBAC, VERIFICATION, SKILL 반영 | 완료 |
| 온톨로지 연결 방향 정리 | transcript를 실행 기억, 온톨로지를 구조 기억으로 문서화 | 완료 |

## 커밋 이력 (2026-04-01)

```
2204d20 Codex 코드리뷰 6건 수정: registry 통합, 보안 강화, 버그 수정
34f9e33 데몬 모드 추가: COM 연결 유지로 명령 실행 속도 5초→0.2초
```

---

## Phase 10: 복잡 화면 안정성 + fill/capture 기능 (2026-04-08)

| 작업 | 내용 | 상태 |
|------|------|------|
| _scan_children 버그 수정 | Children index out of range 시 전체 스캔 중단 → 개별 건너뛰기 | 완료 |
| tcode fallback | learn 시 SAP 내부 tcode(SE38) 대신 원래 tcode(VL03N) 사용 | 완료 |
| sap fill 명령 | 여러 필드 한번에 입력 + 필드명 자동 매핑 + 자동 Enter | 완료 |
| sap capture 명령 | SAP GUI 스크린샷 캡처 (사용자 설명서 제작용) | 완료 |
| sap captures 명령 | 저장된 스크린샷 목록 조회 | 완료 |
| Pillow 의존성 추가 | pyproject.toml에 Pillow>=10.0 (Windows) 추가 | 완료 |
| MCP 도구 등록 | sap_fill_fields, sap_capture, sap_capture_list | 완료 |
| 테스트 15건 | scan_children 9건 + tcode fallback 6건 (test_scan_children.py) | 완료 |
| 문서 갱신 | README, SETUP, VERIFICATION, TASKS, SKILL 반영 | 완료 |

---

## Phase 11: 진짜 온톨로지로 전환 — 관계 중심 재설계

현재 "온톨로지"라 부르는 것은 **화면 스냅샷**(필드 목록)일 뿐, 관계가 없다.
진짜 온톨로지는 필드 간, 트랜잭션 간, 문서 간 **관계(edge)**가 핵심이다.

### 저장 구조 변경 (하위 호환)

```
knowledge/
  transactions/           ← 기존 유지
  processes/              ← 기존 유지
  field_registry.json     ← NEW: 글로벌 필드 인덱스
  field_relationships.json ← NEW: 필드 간 종속/결정 관계
  document_flow.json      ← NEW: 트랜잭션 간 문서 흐름
```

기존 파일은 그대로 두고 3개 파일만 추가. 없으면 빈 상태로 시작, `sap learn` 할 때마다 자동 축적.

---

### Phase 11-1: 필드 레지스트리 (크로스 트랜잭션 필드 연결)

MATNR이 MM03, VA01, ME21N에 다 나오는데 지금은 각 JSON에 따로 있어서 연결이 안 됨.
글로벌 필드 인덱스를 만들어서 "이 필드가 어디어디에서 쓰이는지" 한번에 조회.

| 작업 | 내용 | 자동/수동 | 상태 |
|------|------|----------|------|
| FieldRegistryEntry 모델 | sap_field_name, description, occurrences(tcode+screen+element_id) | - | ✅ |
| FieldRegistry 모델 | dict[sap_field_name → FieldRegistryEntry] | - | ✅ |
| store: load/save/rebuild | field_registry.json CRUD + 전체 트랜잭션 스캔으로 재구축 | - | ✅ |
| learner 연동 | learn_current_screen() 후 _update_field_registry() 자동 호출 | 자동 | ✅ |
| 수동 어노테이션 | check_table, domain, data_type (SAP GUI에서 못 뽑는 정보) | 수동 | ✅ |
| CLI: sap relationships field MATNR | "MATNR이 쓰이는 트랜잭션/화면 목록" 조회 | - | ✅ |

**자동 추출 가능:** sap_field_name은 이미 _build_semantic에서 추출됨. learn만 하면 자동 축적.
**수동 필요:** check_table(MARA 등), domain, data_type(CHAR/NUMC 등)은 SAP GUI Scripting으로 못 뽑음.

---

### Phase 11-2: 필드 종속성 그래프 ✅

WERKS 없이 LGORT를 입력할 수 없다, KUNNR가 ZTERM 기본값을 결정한다 — 이런 관계.

| 작업 | 내용 | 자동/수동 | 상태 |
|------|------|----------|------|
| FieldRelationship 모델 | source_field, target_field, type(결정/종속/활성화/입력순서), observed_in | - | ✅ |
| FieldDependencyType enum | VALUE_DETERMINES, INPUT_ORDER, LOOKUP, FIELD_ACTIVATION | - | ✅ |
| store: load/save/rebuild | field_relationships.json CRUD + 전체 트랜잭션 스캔으로 재구축 | - | ✅ |
| 기존 시드 자동 변환 | 트랜잭션별 field_dependencies를 글로벌 FieldRelationship으로 승격 | 자동 | ✅ |
| CLI: sap depends WERKS | "LGORT가 WERKS에 종속 (ME21N에서 관찰됨)" | - | ✅ |
| MCP: sap_field_dependencies | AI가 필드 종속성을 조회할 수 있도록 MCP 도구 추가 | - | ✅ |
| CLI: sap annotate-dependency | 수동으로 필드 종속 관계 추가 | 수동 | ✅ |

**자동 추출 결과:** 8개 종속 관계 (ME21N 1, MIGO 1, MM03 2, VA01 4)
**수동 필요:** INPUT_ORDER(입력 순서), 새 트랜잭션의 종속 관계.

---

### Phase 11-3: 문서 흐름 온톨로지 ✅

VA01(수주) → VL01N(출하) → VF01(대금청구) 같은 문서 간 관계.

| 작업 | 내용 | 자동/수동 | 상태 |
|------|------|----------|------|
| DocumentFlowEdge 모델 | source_tcode, target_tcode, flow_type(생성/참조/후속), linking_field(VBELN 등), confidence | - | ✅ |
| DocumentFlowType enum | CREATES, REFERENCES, SUCCEEDS, GENERATES | - | ✅ |
| store: load/save/rebuild | document_flow.json CRUD + 프로세스/트랜잭션 스캔으로 재구축 | - | ✅ |
| BusinessProcess 자동 변환 | DataFlow를 DocumentFlowEdge로 승격 (high confidence) | 자동 | ✅ |
| related_transactions 변환 | TransactionOntology.related_transactions를 edge로 변환 (low confidence) | 자동 | ✅ |
| CLI: sap flow VA01 | "VA01 → VL01N (VBELN) → VF01 (VBELN)" | - | ✅ |
| MCP: sap_document_flow | AI가 문서 흐름을 조회할 수 있도록 MCP 도구 추가 | - | ✅ |

**자동 추출 결과:** 30개 엣지 (high confidence 5 from DataFlow, low confidence 25 from related_transactions)
**수동 필요:** flow_type 구분(CREATES vs REFERENCES), linking_field가 없는 경우.

---

### Phase 11-4: 그래프 엔진 통합 ✅

graph.py에 새 관계 데이터를 통합하여 경로 탐색, 영향도 분석 지원.

| 작업 | 내용 | 상태 |
|------|------|------|
| LinkType 확장 | SAME_FIELD, DOCUMENT_FLOW, FIELD_DETERMINES 추가 | ✅ |
| build_from_store 확장 | FieldRegistry → SAME_FIELD 엣지, DocumentFlow → DOCUMENT_FLOW 엣지 | ✅ |
| 쿼리 메서드 추가 | find_field_across_transactions(), get_document_flow(), get_field_dependencies() | ✅ |
| 경로 탐색 | get_process_path(from_tcode, to_tcode) — BFS로 문서 흐름 경로 찾기 | ✅ |

---

### Phase 11-5: MCP 도구 노출 ✅

AI가 관계를 자연스럽게 조회할 수 있도록 MCP 도구 추가.

| 작업 | 내용 | 상태 |
|------|------|------|
| sap_field_dependencies MCP 도구 | 필드 종속성 조회 | ✅ |
| sap_document_flow MCP 도구 | 문서 흐름 조회 | ✅ |
| sap_process_path MCP 도구 | BFS 경로 탐색 | ✅ |
| dispatcher 연동 | field_dependencies, document_flow, process_path, ontology_rebuild 명령 + _dispatch 함수 | ✅ |
| sap_screen에 힌트 | hint 필드에 온톨로지 도구 안내 | ✅ |
| sap fill 온톨로지 연동 | 한글 동의어(synonyms_ko)로 필드 매핑 | ✅ |

---

### Phase 11-6: 자동 축적 (Learn → 온톨로지 자동 강화) ✅

| 작업 | 내용 | 상태 |
|------|------|------|
| learn 후 필드 레지스트리 자동 갱신 | _update_field_registry(screen) | ✅ (11-1) |
| learn 후 문서 흐름 힌트 자동 추가 | learn_transaction → update_document_flow_hints() 자동 호출 | ✅ |
| rebuild 명령 | sap rebuild — field_registry + field_dependencies + document_flow 일괄 재구축 | ✅ |

---

### 구현 순서와 의존 관계

```
Phase 11-1 (필드 레지스트리) ← 모든 Phase의 기반
    ↓
Phase 11-2 (필드 종속성)  ←→  Phase 11-3 (문서 흐름)  ← 병렬 가능
    ↓                              ↓
Phase 11-4 (그래프 엔진 통합) ← 11-2, 11-3 완료 후
    ↓
Phase 11-5 (MCP 노출) ← 11-4 완료 후
    ↓
Phase 11-6 (자동 축적) ← 마무리
```

### 자동 vs 수동 요약

| 관계 유형 | 자동 추출 | 수동 필요 |
|----------|----------|----------|
| 필드 발생 위치 (occurrences) | sap_field_name으로 자동 | X |
| 크로스 트랜잭션 동일 필드 | sap_field_name 매칭 자동 | X |
| 필드 종속 (기존 시드) | field_dependencies에서 자동 변환 | X |
| 필드 입력 순서 | X | 수동 어노테이션 |
| check_table / domain / data_type | X | 수동 어노테이션 |
| 문서 흐름 (프로세스 기반) | BusinessProcess.DataFlow 자동 변환 | X |
| 문서 흐름 (linking_field 없는) | related_transactions에서 low confidence | linking_field 수동 |
| flow_type 구분 | X | CREATES/REFERENCES 수동 |

---

## Phase 12: ALV Grid 데이터 읽기 강화 (2026-04-09) ✅

현재 `sap table`은 `GuiGridView`와 `GuiTableControl`만 지원.
실제 SAP에서 ALV Grid는 `GuiShell`(subtype=GridView) 안에 있는 경우가 많아 읽기 실패함.

| 작업 | 내용 | 상태 |
|------|------|------|
| GuiShell ALV 감지 | _scan_children에서 GuiShell SubType=GridView일 때 tables로 분류 | ✅ |
| ALV Grid 데이터 읽기 | _read_alv_grid: GetCellValue, ColumnOrder, ColumnCount 등 ALV 전용 API | ✅ |
| ALV 컬럼 헤더 | GetColumnTitles로 한글/영문 컬럼명 수집 | ✅ |
| ALV 필터/정렬 상태 | GetFilteredColumnKeys, GetSortedColumnKeys → filtered_columns, sorted_columns | ✅ |
| ALV 행 선택 | select_table_row에서 GuiShell ALV 지원 (SelectedRows) | ✅ |
| ALV 더블클릭 | double_click_table_cell: SetCurrentCell + DoubleClickCurrentCell | ✅ |
| _scan_children ALV 인식 | GuiShell SubType=GridView → tables, 나머지 → trees | ✅ |
| 스크롤 처리 | FirstVisibleRow 조정하며 전체 읽기 (최대 500행) | ✅ |
| MCP 도구 | sap_table_read 설명 개선 + sap_table_doubleclick 도구 추가 | ✅ |
| CLI 명령 | sap table-doubleclick 명령 추가 | ✅ |
| 권한 등록 | TOOL_PERMISSION_MAP에 sap_table_doubleclick 등록 | ✅ |
| 테스트 15건 | ALV 감지/읽기/스크롤/선택/더블클릭/필터정렬 (test_alv_grid.py) | ✅ |

---

## Phase 13: MCP → Daemon 단일 연결 모드 (2026-04-09)

### 배경

MCP 서버는 `python -m sap_cli.mcp_server`로 **상주 프로세스**이므로
Python 시작/import 오버헤드는 없다. 그러나:

1. **COM 연결 경합** — MCP 서버와 CLI/Shell이 각각 별도 COM 연결을 가짐.
   같은 SAP 세션에 두 개의 COM 연결이 붙으면 상태 불일치 및 충돌 가능.
2. **첫 명령 지연** — MCP 서버 시작 후 첫 `sap_connect`에서 COM 초기화 ~3초.
   daemon이 이미 연결된 상태라면 즉시 사용 가능.
3. **연결 복원** — COM 연결 끊어지면 MCP 프로세스에서 직접 복구해야 함.
   daemon은 이미 연결 관리 + liveness 체크를 내장.

### 아키텍처

```
변경 전:
  MCP Server ──dispatch_command()──→ service.py ──→ COM 연결 A
  CLI        ──daemon(TCP)─────────→ service.py ──→ COM 연결 B   ← 경합

변경 후 (SAP_CLI_USE_DAEMON=1):
  MCP Server ──daemon.send_command(TCP)──→ daemon ──→ COM 연결 (단일)
  CLI        ──daemon.send_command(TCP)──→ daemon ──→ COM 연결 (단일)
  Shell      ──daemon.send_command(TCP)──→ daemon ──→ COM 연결 (단일)

MCP 서버 역할: MCP 프로토콜(stdio/JSON-RPC) ↔ 도구 가시성/권한 체크
Daemon 역할:   COM 연결 관리 + dispatch_command 실행
```

### 프로토콜 확장

daemon TCP 요청에 `context` 필드 추가:

```json
{
  "cmd": "screen",
  "args": {"tcode": "MM03"},
  "context": {
    "source": "mcp",
    "tool_name": "sap_screen",
    "agent_name": "operator",
    "include_screen_summary": true
  }
}
```

기존 context 없는 요청도 하위 호환 유지 (`source="daemon"` 기본값).

### 구현 항목

| # | 작업 | 내용 | 상태 |
|---|------|------|------|
| 1 | daemon 프로토콜 확장 | TCP 요청에 context 필드 전달 → DispatchContext 구성 | ✅ |
| 2 | daemon.send_command 확장 | context dict를 인자로 받아 전송 | ✅ |
| 3 | MCP daemon 위임 | handle_tool에서 SAP_CLI_USE_DAEMON=1이면 daemon 경유 | ✅ |
| 4 | daemon 자동 시작 | MCP가 daemon 모드인데 미실행 시 auto-start + fallback | ✅ |
| 5 | 테스트 12건 | daemon 위임, context 전달, fallback, 에러 처리 (test_mcp_daemon.py) | ✅ |

### 설정 방법

```json
// Claude Code settings.json
{
  "mcpServers": {
    "sap-gui": {
      "command": "python",
      "args": ["-m", "sap_cli.mcp_server"],
      "env": {
        "SAP_CLI_USE_DAEMON": "1"
      }
    }
  }
}
```

---

## Phase 14: MCP Session Store + 온톨로지 학습 파이프라인 (2026-04-15)

> 상세 설계: [DESIGN_MCP_SESSION.md](DESIGN_MCP_SESSION.md)

### 배경

현재 transcript는 개별 SAP 명령 단위로 기록한다.
AI가 사용자 요청을 받아 여러 명령을 실행하는 **전체 흐름**(의도 → 추론 → 실행 → 결과)이 보이지 않는다.
성공한 흐름을 온톨로지에 학습시켜 다음에 비슷한 요청이 오면 더 정확하게 실행하는 구조가 필요하다.

### 핵심 구조

```
사용자 요청 → AI 추론 → SAP 명령 시퀀스 → 결과
         ↓                                  ↓
    MCP Session Store (별도 저장소)     성공 시 candidate 마킹
                                           ↓
                                    AI 정제 (헤맨 부분 제거)
                                           ↓
                                    관리자 승인 → Workflow Recipe
                                           ↓
                                    온톨로지에 저장 → 다음 실행에 참고
```

### Phase 14-1: MCP Session Logger (핵심)

| # | 작업 | 내용 | 상태 |
|---|------|------|------|
| 1 | 스키마 정의 | McpSession, SessionStep 모델 (schema.py) | ✅ |
| 2 | Session Store | sessions/ 디렉토리 CRUD (session_store.py 신규) | ✅ |
| 3 | 설정 추가 | SessionConfig (enabled, log_dir) — auth/policy.py | ✅ |
| 4 | MCP 세션 도구 | sap_session_start, sap_session_end, sap_session_list, sap_session_show | ✅ |
| 5 | Dispatcher 연동 | _log_session_step + SESSION_SKIP_COMMANDS + _active_session_id 관리 | ✅ |
| 6 | 테스트 20건 | 세션 CRUD + 단계 기록 + 조회 + 보안(path traversal) + 동시성 | ✅ |

### Phase 14-2: Session Analyzer (정제 엔진)

| # | 작업 | 내용 | 상태 |
|---|------|------|------|
| 1 | 탐색 단계 감지 | screen/status/modal 조회만 한 단계 → is_exploration 마킹 | ✅ |
| 2 | 에러 복구 감지 | 에러 → vkey 12(취소)/n 패턴 → is_recovery 마킹 | ✅ |
| 3 | 반복 제거 | 동일 명령 연속 retry 중 마지막만 유지 | ✅ |
| 4 | 핵심 흐름 추출 | exploration/recovery 제거 후 정제된 시퀀스 반환 | ✅ |
| 5 | candidate 마킹 | 성공 + 3단계 이상 + tcode 포함 → candidate=True | ✅ |
| 6 | 테스트 | 정제 규칙별 + candidate 마킹 (27건 pass) | ✅ |

### Phase 14-3: Workflow Recipe + 관리자 승인

| # | 작업 | 내용 | 상태 |
|---|------|------|------|
| 1 | 스키마 정의 | Recipe 모델에 source_session, approved_by 필드 추가 (recipe.py) | ✅ |
| 2 | Recipe Store | 기존 RecipeStore 활용 (recipe.py) | ✅ |
| 3 | 세션→레시피 변환 | session_to_recipe + _generalize_args 변수 치환 (session_analyzer.py) | ✅ |
| 4 | 승인 도구 | sap_session_candidates, sap_session_approve MCP 도구 + dispatcher | ✅ |
| 5 | 온톨로지 연결 | approve 시 _link_recipe_to_ontology로 자동 연결 | ✅ |
| 6 | 테스트 | 변환 + 승인 흐름 + 온톨로지 반영 (26건 pass) | ✅ |

### Phase 14-4: Recipe 활용 (AI 자동 참조)

| # | 작업 | 내용 | 상태 |
|---|------|------|------|
| 1 | ontology_query 확장 | 트랜잭션 조회 시 관련 recipes 요약 포함 (context, keywords, recipe_hint) | ✅ |
| 2 | recipe 키워드 검색 | RecipeStore.query_for_ai (tcode+키워드 통합 검색) | ✅ |
| 3 | sap_recipe_query | recipe 조회 MCP 도구 (tcode/키워드) | ✅ |
| 4 | 테스트 | 검색 + AI 참조 시나리오 (21건 pass) | ✅ |

### 구현 순서

```
Phase 14-1 (Session Logger) ← 핵심, 나머지의 기반
    ↓
Phase 14-2 (Session Analyzer) ← 14-1 완료 후
    ↓
Phase 14-3 (Recipe + 승인) ← 14-2 완료 후
    ↓
Phase 14-4 (Recipe 활용) ← 14-3 완료 후
```

---

## 남은 작업

### ReBAC / 운영 커널 후속 작업
- transcript의 screen transition을 온톨로지 screen_flow 후보로 반영
- 반복 성공한 action 패턴을 field dependency / action prior 후보로 반영
- 런타임 승인 워크플로우 (MCP sampling 활용)
- transcript 기반 replay executor 또는 verifier dashboard

### 사내 검증 (SAP 환경 필요) → VERIFICATION.md 참조 ✅ 기본 검증 완료 (2026-04-16)
- [x] 시나리오 1: 기본 연결 및 화면 탐색
- [x] 시나리오 2: MM03 자재 조회 자동화
- [x] 시나리오 3: 매크로 녹화 → 재생 사이클
- [x] 시나리오 4: 온톨로지 학습
- [x] 시나리오 5: MCP 서버 + Claude Code 연동
- [x] 시나리오 6: 데몬 모드 + Shell
- [x] 시나리오 7: Transcript / Replay Review
- [x] 시나리오 8: 업무 프로세스 조회 및 관리
- [x] 시나리오 9: 업무 프로세스 실행 (ProcessExecutor)
- [x] 시나리오 10: 시드 트랜잭션 온톨로지 검증 (7개)
- [ ] 시나리오 16: 필드 레지스트리 / 종속성 / 문서 흐름 (Phase 11)
- [ ] 시나리오 17: MCP 세션 로깅 + 분석 + 레시피 승인 (Phase 14)
- [ ] 시나리오 18: 온톨로지 Q&A — 자연어 질문 (Phase 15)
- [ ] 시나리오 19: 멀티 시스템 연결 + 세션 복구 (Phase 16)
- [ ] 시나리오 20: 병렬 멀티 세션 — 슬롯 기반 (Phase 17)

### 추가 온톨로지 보강 (SAP 환경 필요)
- MM03 추가 화면 (MRP, 구매, 판매, 저장위치 뷰)
- VA01 하위 탭 (가격조건, 출하, 대금청구, 파트너)
- ME21N, FB01 등 추가 트랜잭션 온톨로지

---

## 2026-04-02: Phase 10 — BusinessProcess (업무 프로세스) 레이어

### 배경
현재 온톨로지는 **트랜잭션 1개 = 1파일** 단위로 저장된다.
하지만 실무 SAP 작업은 여러 트랜잭션을 순서대로 실행하는 **업무 프로세스** 단위:
- 자재 조달: MM01 → ME21N → MIGO → MIRO
- 판매 처리: VA01 → VL01N → VF01
- 자재 변경: MM02 → MM03(확인)

트랜잭션 간 **데이터 흐름**(앞 스텝의 출력이 뒤 스텝의 입력)까지 구조화해야
AI가 end-to-end 업무를 자율 실행할 수 있다.

### 구현 계획

| # | 작업 | 내용 | 상태 |
|---|------|------|------|
| 1 | 스키마 정의 | `BusinessProcess`, `ProcessStep`, `DataFlow` 모델 (schema.py 확장) | 완료 |
| 2 | 저장소 확장 | OntologyStore에 processes/ 디렉토리 + CRUD 메서드 추가 | 완료 |
| 3 | 시드 데이터 | 자재 조달 프로세스 예시 JSON 1건 작성 | 완료 |
| 4 | CLI/MCP 노출 | process list/show/create/delete 명령 추가 (dispatcher + MCP) | 완료 |
| 5 | 테스트 | process CRUD + 데이터 흐름 + 시드 + 보안 + 무결성 검증 (12개 테스트) | 완료 |
| 6 | Codex 리뷰 수정 | TOOL_PERMISSION_MAP 등록, MCP 표면 노출, 무결성 검증, 테스트 개선 | 완료 |
| 7 | 실행 엔진 | ProcessExecutor — 스텝별 실행, 출력 추출, DataFlow 연결, 실행 기록 저장 | 완료 |

### 1. 스키마 설계 (`sap_cli/ontology/schema.py` 확장)

```python
class DataFlow(BaseModel):
    """스텝 간 데이터 전달 — 앞 스텝 출력 → 뒤 스텝 입력."""
    source_step: int          # 출력 스텝 번호 (0-based)
    source_field: str         # 출력 필드 (예: "MATNR", "$document_number")
    target_step: int          # 입력 스텝 번호
    target_field: str         # 입력 필드 (예: "MATNR", "EBELN")
    description: str = ""

class ProcessStep(BaseModel):
    """업무 프로세스의 단일 스텝 = 트랜잭션 1회 실행."""
    step_number: int          # 0-based 순서
    tcode: str                # 트랜잭션 코드
    description: str = ""     # "자재 마스터 생성"
    input_fields: dict[str, str] = {}   # 고정 입력값 {"MTART": "ROH"}
    expected_output: list[str] = []     # 추출할 출력 필드명 ["MATNR", "$status"]
    optional: bool = False    # 실패해도 프로세스 계속 가능 여부

class BusinessProcess(BaseModel):
    """여러 트랜잭션을 연결한 업무 프로세스."""
    id: str                   # "procurement_basic"
    name: str                 # "기본 자재 조달 프로세스"
    module: str = ""          # "MM"
    description: str = ""
    steps: list[ProcessStep] = []
    data_flows: list[DataFlow] = []     # 스텝 간 데이터 연결
    tags: list[str] = []                # ["조달", "구매", "입고"]
```

### 2. 저장소 구조

```
~/.sap_cli/knowledge/
├── transactions/       # 기존 (트랜잭션별)
├── processes/          # 신규 (업무 프로세스별)
│   ├── procurement_basic.json
│   └── sales_order_to_billing.json
├── meta_edges/
└── patterns/
```

### 3. 시드 데이터 예시 — 자재 조달 프로세스

```json
{
  "id": "procurement_basic",
  "name": "기본 자재 조달 프로세스",
  "module": "MM",
  "description": "자재 생성 → 구매오더 → 입고 → 송장검증",
  "steps": [
    {"step_number": 0, "tcode": "MM01", "description": "자재 마스터 생성",
     "input_fields": {"MTART": "ROH", "MBRSH": "M"},
     "expected_output": ["MATNR"]},
    {"step_number": 1, "tcode": "ME21N", "description": "구매오더 생성",
     "expected_output": ["EBELN"]},
    {"step_number": 2, "tcode": "MIGO", "description": "입고 처리",
     "expected_output": ["MBLNR"]},
    {"step_number": 3, "tcode": "MIRO", "description": "송장 검증",
     "expected_output": ["BELNR"]}
  ],
  "data_flows": [
    {"source_step": 0, "source_field": "MATNR", "target_step": 1, "target_field": "MATNR",
     "description": "생성된 자재번호를 구매오더에 입력"},
    {"source_step": 1, "source_field": "EBELN", "target_step": 2, "target_field": "EBELN",
     "description": "구매오더번호를 입고에 참조"},
    {"source_step": 2, "source_field": "MBLNR", "target_step": 3, "target_field": "MBLNR",
     "description": "입고 문서번호를 송장검증에 참조"}
  ],
  "tags": ["조달", "구매", "입고", "송장"]
}
```

### 4. CLI/MCP 명령

- `sap process-list` — 등록된 프로세스 목록
- `sap process-show <id>` — 프로세스 상세 (스텝 + 데이터 흐름)
- `sap process-create <id> --name "..." --steps MM01,ME21N,MIGO` — 신규 생성
- `sap process-delete <id>` — 프로세스 삭제
- MCP: `process_list`, `process_show`, `process_create`, `process_delete` 도구 추가

### 5. ProcessExecutor — 실행 엔진 (`sap_cli/ontology/process_executor.py`)

BusinessProcess가 "설계도"라면, ProcessExecutor는 **실행 컨텍스트를 관리하는 런타임**이다.
AI 에이전트가 스텝을 수동으로 실행하면서 결과를 보고하면, executor가 DataFlow 기반으로 다음 스텝 입력값을 자동 연결한다.

**사용 흐름:**
```
1. sap_process_start("procurement_basic")
   → status: running, current_step: 0, pending_inputs: {MTART: "ROH"}

2. AI가 MM01 실행 → 자재 생성 → 상태바에서 번호 추출
   → sap_process_step(0, outputs: {MATNR: "100-100"})
   → context에 step0.MATNR = "100-100" 저장

3. sap_process_status 호출
   → current_step: 1, pending_inputs: {MATNR: "100-100"}  (DataFlow 자동)

4. AI가 ME21N 실행 → MATNR "100-100" 입력 → 구매오더 생성
   → sap_process_step(1, outputs: {EBELN: "4500012345"})

5. ... 반복 ...

6. 마지막 스텝 완료 시 자동으로 실행 기록 JSON 저장
   → ~/.sap_cli/executions/procurement_basic_20260402T....json
```

**추가된 MCP 도구:**
- `sap_process_start` — 프로세스 실행 시작, 컨텍스트 초기화
- `sap_process_step` — 스텝 완료 보고 + 출력값 등록 (DataFlow 자동 연결)
- `sap_process_status` — 현재 상태, 다음 입력값(pending_inputs), 컨텍스트 조회

**핵심 메서드:**
- `get_step_inputs(step)` — 고정 입력 + DataFlow에서 온 값 합산
- `complete_step(step, outputs)` — 출력을 context에 저장, 다음 스텝으로 이동
- `skip_step(step)` — optional 스텝 건너뛰기
- `extract_document_number(msg)` — SAP 상태바 메시지에서 문서번호 파싱
- `finish()` / `abort()` — 실행 기록 JSON 저장

### Phase 11: 사내 검증 기반 버그 수정 (2026-04-08)

| # | 이슈 | 상태 |
|---|------|------|
| 1 | 모달(wnd[1]) 자동 감지 — screen/vkey가 팝업 무시하던 문제 | 완료 |
| 2 | set 명령에서 체크박스(/chk)/라디오(/rad) 자동 감지 | 완료 |
| 3 | 테이블 내부 스캔을 체크박스/라디오만 수집 (노이즈 방지) | 완료 |
| 4 | table-select 명령 추가 (CLI + dispatcher + MCP) | 완료 |
| 5 | --fields-only가 읽기전용 필드 + 테이블도 출력 | 완료 |
| 6 | --search 패턴 검색 추가 (ID/라벨/시맨틱 부분 일치) | 완료 |
| 7 | 탭(GuiTab) 안의 필드 재귀 스캔 누락 수정 | 완료 |
| 8 | 가상환경(.venv) 필수 사용 문서화 | 완료 |
| 9 | 조달/판매 시드 트랜잭션 5건 + 판매 프로세스 추가 | 완료 |

## 커밋 이력 (2026-04-02 ~ 04-08)

```
ee5b562 fix: 탭(GuiTab) 안의 필드도 재귀 스캔하도록 수정
f78b60f fix: --fields-only가 읽기전용 필드도 출력하도록 변경
f2d7f89 fix: 테이블 내부 스캔을 체크박스/라디오만 수집하도록 제한
41a7fe2 feat: sap screen --search 패턴 검색 추가
0409833 docs: VERIFICATION.md 시나리오 1~2에 screen 검색, table-select, 체크박스 반영
6ee1ac4 feat: CLI에 table-select 명령 추가
5591520 feat: table_select 명령 추가 — 테이블 행 선택
438433d fix: 테이블 안의 체크박스/필드도 sap screen에서 스캔
1b51c98 fix: set 명령에서 체크박스/라디오버튼 자동 감지 처리
9f06c77 docs: VERIFICATION.md 시나리오 2 갱신 — 모달 자동 감지, HANA 대응 추가
044e169 fix: 모달(팝업) 윈도우 자동 감지 — screen/vkey가 wnd[1]도 처리
c317f2f docs: VERIFICATION.md에 시나리오 8~10 추가
409edb8 fix: --fields-only에서 테이블 정보도 함께 출력
e2ab184 docs: 설치 가이드에 가상환경(.venv) 필수 사용 명시
96ad2b9 feat: 조달/판매 트랜잭션 시드 5건 + 판매 프로세스 추가
f745c4f docs: README/HTML에 ProcessExecutor 실행 흐름, 시드 7개 트랜잭션 반영
f08f5a5 docs: TASKS.md에 ProcessExecutor 설계와 커밋 이력 추가
bce060e feat: ProcessExecutor 추가 — 스텝별 실행, DataFlow 연결, 실행 기록 저장
97b42dd docs: 인터랙티브 가이드 플레이북에 업무 프로세스 시나리오 추가
0022d0d docs: 인터랙티브 가이드에 BusinessProcess 섹션 추가
717ddc1 docs: README에 업무 프로세스(BusinessProcess) 섹션 추가
f08f1ee fix: Codex 리뷰 4건 수정 — 권한맵, MCP 노출, 무결성 검증, 테스트 격리
9a19d63 feat: BusinessProcess 레이어 추가 — 트랜잭션 간 업무 프로세스 구조화
19b318e feat: 레시피에 context 필드 추가 — 상세 맥락 저장
388f138 feat: 레시피-온톨로지 연동 + AI 학습 가이드
e494fad feat: AI 네비게이션 레시피 시스템 구현
28465f4 fix: 트리 부모-자식 관계 구성 fallback 추가
36ae83a fix: 트리 노드 텍스트/컬럼 수집 로직 견고화
3d73691 feat: 트리 노드 액션 5종 추가 + 트리 읽기 개선
98a6eb1 fix: Codex 리뷰 P1/P2 3건 수정
```

---

## 장기 계획 (Roadmap)

### Phase 15: 온톨로지 Q&A + 레시피 고도화

> 상세 설계: [DESIGN_MCP_SESSION.md § 10](DESIGN_MCP_SESSION.md)

#### Phase 15-1: Claude Code Q&A 스킬 (코드 변경 없음)

| # | 작업 | 내용 | 상태 |
|---|------|------|------|
| 1 | 스킬 파일 생성 | `skill/sap-ontology-qa/SKILL.md` — 온톨로지 JSON 직접 읽어 답변 | ✅ |
| 2 | 질문 유형 정의 | 트랜잭션 요약, 필드 의미, 교차 조회, 필수 필드, 프로세스, 종속성, 패턴 | ✅ |
| 3 | 한국어 매핑 | 키워드→tcode, 한국어→SAP필드 매핑 테이블 | ✅ |

#### Phase 15-2: CLI 커맨드

| # | 작업 | 내용 | 상태 |
|---|------|------|------|
| 1 | ontology ask | `sap ontology-ask "<질문>"` — OntologyGraph 기반 자연어→그래프 탐색 | ✅ |
| 2 | 질문 파서 | tcode/필드명/모듈명 추출 + 질문 유형 분류 (한국어/영문 지원) | ✅ |
| 3 | 포매터 | 7개 질문 유형별 구조화된 JSON 답변 생성 | ✅ |
| 4 | 테스트 | 파서 11개 + 포매터 9개 = 20개 테스트 전체 통과 | ✅ |

#### Phase 15-3: MCP 도구 강화

| # | 작업 | 내용 | 상태 |
|---|------|------|------|
| 1 | sap_ontology_query 확장 | field_semantic, field_cross, process_guide, dependency_chain 쿼리 타입 추가 | ✅ |
| 2 | 시맨틱 검색 | synonyms + domain_context 기반 퍼지 매칭 | ✅ |
| 3 | 테스트 | 시맨틱 검색 10개 + 확장 쿼리 13개 = 23개 테스트 전체 통과 | ✅ |

#### 장기: 레시피 RAG (레시피 100개+ 축적 후)

| 작업 | 내용 | 상태 |
|------|------|------|
| 레시피 RAG 검색 | 키워드 매칭 → 벡터 임베딩 기반 시맨틱 검색 전환 | 대기 |
| 온톨로지 통합 심화 | 같은 tcode라도 목적별 다른 경로 표현 | 대기 |

### Phase 16: 연결 안정성 (2026-04-15 ✅)

| 작업 | 내용 | 상태 |
|------|------|------|
| 멀티 시스템 연결 | connection_index 자동 탐색 — 시스템 ID로 올바른 연결 선택 | ✅ |
| 설계 방향 | Connections 순회하며 Description/SystemName 매칭, sap connect --system SID 지원 | ✅ |
| 세션 복구 | COM 연결 끊김 시 자동 재연결 + 상태 복원 | ✅ |

### Phase 17: 병렬 멀티 세션 — 여러 에이전트가 각각의 SAP 세션 운영

> 상세 설계: [DESIGN_MULTI_SESSION.md](DESIGN_MULTI_SESSION.md)

SAP GUI 세션을 여러 개 띄우고, 각 에이전트가 고유 슬롯(slot)에 바인딩되어 독립적으로 작업.
현재 싱글턴 구조를 SessionPool로 전환하여 slot_id 기반 다중 세션 관리.

#### Phase 17-1: SessionPool 코어 + service 레이어 (2026-04-16 ✅)

| # | 작업 | 내용 | 상태 |
|---|------|------|------|
| 1 | SessionSlot 모델 | slot_id, connection/session 인덱스, wrapper, 에이전트명 | ✅ |
| 2 | SessionPool 클래스 | slot CRUD + threading.Lock + max_sessions 제한 | ✅ |
| 3 | SapGuiWrapper.create_session | 새 SAP 세션 생성 COM API (CreateSession) | ✅ |
| 4 | service.py 전환 | 싱글턴(_sap) → get_sap(slot_id="ses0") 풀 방식 | ✅ |
| 5 | 테스트 29건 | pool CRUD/최대세션/에이전트/스레드안전/service 하위호환 | ✅ |

#### Phase 17-2: Dispatcher + Daemon 연동 (2026-04-16 ✅)

| # | 작업 | 내용 | 상태 |
|---|------|------|------|
| 1 | DispatchContext.slot_id | slot_id 필드 추가 (기본 "ses0") | ✅ |
| 2 | dispatcher slot 라우팅 | _get_sap() → _get_sap(ctx.slot_id) 전 24개소 변경 | ✅ |
| 3 | daemon context.slot_id | TCP 프로토콜에 slot_id 전달 + 풀 기반 전환 | ✅ |
| 4 | MCP slot_id 전달 | handle_tool에서 slot_id 추출 → daemon/dispatcher 전달 | ✅ |
| 5 | 기존 테스트 호환 | 355개 전체 통과 | ✅ |

#### Phase 17-3: MCP + CLI 표면 (2026-04-16 ✅)

| # | 작업 | 내용 | 상태 |
|---|------|------|------|
| 1 | MCP 도구 slot_id | 모든 SAP 도구에 선택적 slot_id 파라미터 추가 | 완료 |
| 2 | sap_session_pool 도구 | 활성 슬롯 목록 조회 | 완료 |
| 3 | sap_session_allocate 도구 | 새 슬롯 할당 (자동 세션 생성) | 완료 |
| 4 | sap_session_release 도구 | 슬롯 해제 (세션 유지/종료 선택) | 완료 |
| 5 | CLI --slot 옵션 | sap slots, sap connect --slot, sap run --slot | 완료 |
| 6 | 테스트 | MCP + CLI 슬롯 라우팅 (디스패처 통합 9개) | 완료 |

#### Phase 17-4: COM 스레딩 안정화 + 문서 (2026-04-16 ✅)

| # | 작업 | 내용 | 상태 |
|---|------|------|------|
| 1 | CoInitialize 가드 | com_wrapper에 _ensure_com_initialized() 추가 (pythoncom.CoInitialize) | 완료 |
| 2 | TTL 자동 해제 | SessionPool.sweep_expired() + max_sessions 도달 시 자동 sweep | 완료 |
| 3 | 문서 갱신 | README 아키텍처 다이어그램, DESIGN_MULTI_SESSION 구현 기록, TASKS.md | 완료 |
| 4 | Windows 실기 검증 | COM STA/MTA, CreateSession 실제 동작 | 사내 검증 대기 |

#### Phase 17-5: Codex 리뷰 대응 (2026-04-16 ✅)

| # | 이슈 | 심각도 | 조치 | 상태 |
|---|------|--------|------|------|
| 1 | 트랜스크립트/세션 로깅 ses0 하드코딩 | P1 | ctx.slot_id 전달 | 완료 |
| 2 | daemon session_id 유실 | P1 | daemon에 session_id 복사 추가 | 완료 |
| 3 | 슬롯 할당 시 독립 세션 미생성 | P1 | create_new_session 옵션 추가 | 완료 |
| 4 | 재연결 시 슬롯 메타데이터 무시 | P1 | get_sap()가 저장된 인덱스로 재연결 | 완료 |
| 5 | TTL sweep 수동 전용 | P1 | list_slots()에서도 자동 sweep | 완료 |
| 6 | get_pool() 스레드 비안전 | P1 | double-checked locking 적용 | 완료 |
| 7 | CLI connect만 --slot 지원 | P1 | 글로벌 --slot 옵션 | 완료 |
| 8 | COM STA 가드 불완전 | P2 | Windows 실기 검증 필요 | 사내 대기 |
| 9 | slot_id 검증 부족 | P2 | 정규식 검증 + 테스트 6개 | 완료 |

### Phase 18: AI 학습 루프 (구 Phase 17)

#### Phase 18-0: 레시피 중복 감지 + 힌트 자동 주입 (2026-04-19 ✅)

커밋: `40e792a feat: 레시피 중복 감지(HITL) + sap_run 레시피 힌트 자동 주입`

| 작업 | 내용 | 상태 |
|------|------|------|
| `RecipeStore.find_similar` | 키워드 Jaccard(0.35) + tcode 시퀀스(0.35) + 설명/컨텍스트 단어(0.30) | 완료 |
| `efficiency_score` | 단계수/성공횟수/컨텍스트/최신도 기반 효율성 평가 | 완료 |
| `sap_recipe_save` HITL 2단계 | `on_duplicate=replace/keep_both` + `approved_ids` + TOCTOU 재검증 | 완료 |
| 온톨로지 정리 | replace/update/delete 경로에서 stale recipes/action_paths unlink | 완료 |
| `sap_run` 힌트 주입 | 실행 후 `recipe_hints` 자동 첨부 (context 필드 제외) | 완료 |
| 원자적 저장 | tmp + os.replace + fsync(파일/디렉토리) + short-write 루프 | 완료 |
| `_safe_read` | I/O 오류와 JSON/검증 오류 로그 분리 | 완료 |
| 테스트 25개 | `tests/test_recipe_dedup.py` (전체 399 passed) | 완료 |

#### Phase 18-1: 자동 레시피 제안 (2026-04-19 ✅)

| 작업 | 내용 | 상태 |
|------|------|------|
| `sap_session_end` 자동 평가 | candidate 분류 시 응답에 `recipe_suggestion` 자동 첨부 | 완료 |
| `sap_recipe_pending` MCP 도구 | 누적 미승인 candidate 세션 일괄 조회 | 완료 |
| 테스트 9개 | `tests/test_recipe_autosuggest.py` | 완료 |

#### Phase 18-2: 레시피 품질 관리 (2026-04-19 ✅)

| 작업 | 내용 | 상태 |
|------|------|------|
| Recipe 스키마 확장 | `failure_count`, `last_failed_at`, `disabled`, `disabled_reason` (역호환 default) | 완료 |
| 자동 비활성화 정책 | total≥3 AND failure_rate≥0.5 → `should_auto_disable()` | 완료 |
| `report_failure` / `enable` | RecipeStore atomic save + 임계 감지 | 완료 |
| 검색 disabled 자동 제외 | search/find_by_tcode/query_for_ai/find_similar에 `include_disabled` 옵션 | 완료 |
| `sap_recipe_failure` / `sap_recipe_enable` MCP 도구 | AI가 실패 보고 / 관리자 재활성화 | 완료 |
| 테스트 20개 | `tests/test_recipe_quality.py` | 완료 |

#### Phase 18-3: 레시피 버전닝 (2026-04-19 ✅)

| 작업 | 내용 | 상태 |
|------|------|------|
| Recipe 스키마 확장 | `version`, `previous_version_archive` (역호환 default) | 완료 |
| `_archive` 폴더 lazy 생성 | `~/.sap_cli/recipes/_archive/<id>__v<N>__<ts>.json` (atomic save 회귀 보존) | 완료 |
| `version_up()` | os.replace로 원자 archive + version+1 + 메타 승계 + failure 상태 초기화 | 완료 |
| `history()` / `load_archive()` | 과거 버전 메타 조회 + 디렉토리 탈출 차단 | 완료 |
| `sap_recipe_version_up` / `sap_recipe_history` MCP 도구 | 버전업 + 이력 조회 | 완료 |
| 테스트 16개 | `tests/test_recipe_versioning.py` | 완료 |

#### Phase 18-4: 크로스 트랜잭션 레시피 (2026-04-19 ✅)

| 작업 | 내용 | 상태 |
|------|------|------|
| Recipe 스키마 확장 | `tcode_sequence: list[str]` (save 시 자동 채움, 역호환 default) | 완료 |
| `find_chain(tcodes, match)` | match='all'\|'any', disabled 자동 제외, 시퀀스 길이순 정렬 | 완료 |
| `_collect_recipe_hints` 강화 | 멀티-tcode 레시피에 `cross_tcode`/`tcode_sequence` 메타 노출 | 완료 |
| `sap_recipe_chain_search` MCP 도구 | 여러 tcode가 모두/하나라도 포함된 복합 레시피 검색 | 완료 |
| 테스트 17개 | `tests/test_recipe_cross_tcode.py` (전체 461 passed) | 완료 |

### ~~Phase 19: 온톨로지 Q&A~~ (Phase 15에서 완료, 폐기)

### Phase 20: ADT 온톨로지 통합 — ABAP 코드 메타데이터 연동

> 참고 레포: [mcp-abap-adt](https://github.com/mario-andreschak/mcp-abap-adt), [mcp-abap-abap-adt-api](https://github.com/mario-andreschak/mcp-abap-abap-adt-api)

SAP ADT(ABAP Development Tools) REST API를 활용하여 온톨로지에 **코드 레벨 메타데이터**를 추가.
현재 GUI 화면 기반(tcode → screen → field) 온톨로지를 **tcode → program → class/table/function** 까지 확장.

**전제 조건:**
- SAP 시스템에서 `/sap/bc/adt` SICF 서비스 활성화
- ADT 접근 권한이 있는 SAP 계정 (개발자 키 불필요, 읽기 전용)
- 레시피 50개+ 축적 후 착수 권장

#### Phase 20-1: ADT 클라이언트 기반 구축

| # | 작업 | 내용 | 상태 |
|---|------|------|------|
| 1 | ADT HTTP 클라이언트 | `adt_client.py` — Basic Auth + CSRF 토큰 자동 처리, `requests` 기반 | 대기 |
| 2 | 연결 설정 | `~/.sap_cli/adt_config.json` — URL, client, user, 시스템별 다중 연결 | 대기 |
| 3 | CLI 커맨드 | `sap adt connect` / `sap adt status` — ADT 연결 테스트 | 대기 |
| 4 | 테스트 | CSRF 토큰 흐름 mock 테스트, 연결 실패 핸들링 | 대기 |

#### Phase 20-2: 읽기 전용 ADT 도구 (온톨로지 보강용)

| # | 작업 | 내용 | 상태 |
|---|------|------|------|
| 1 | 트랜잭션 프로그램 조회 | `sap adt tcode-info <tcode>` — tcode → 실행 프로그램명, 프로그램 타입 | 대기 |
| 2 | 오브젝트 검색 | `sap adt search <query>` — 프로그램/클래스/함수모듈/테이블 검색 | 대기 |
| 3 | 테이블 DDic 조회 | `sap adt table <table>` — 필드 목록, 데이터 요소, 도메인, 체크 테이블 | 대기 |
| 4 | 소스 코드 조회 | `sap adt source <object>` — 프로그램/클래스/함수모듈 소스 읽기 전용 | 대기 |
| 5 | 패키지 구조 조회 | `sap adt package <package>` — 패키지 하위 오브젝트 트리 | 대기 |

#### Phase 20-3: 온톨로지 스키마 확장

| # | 작업 | 내용 | 상태 |
|---|------|------|------|
| 1 | 노드 타입 추가 | `program`, `class`, `function_module`, `table`, `data_element`, `domain` | 대기 |
| 2 | 엣지 타입 추가 | `executes_program`, `reads_table`, `calls_function`, `uses_data_element` | 대기 |
| 3 | tcode→program 자동 매핑 | ADT에서 tcode 속성 조회 → 온톨로지에 `executes_program` 엣지 자동 생성 | 대기 |
| 4 | field→data_element 매핑 | 화면 필드의 DDic 바인딩 → `uses_data_element` 엣지 생성 | 대기 |
| 5 | 테스트 | 확장 스키마 검증, 기존 온톨로지 호환성 테스트 | 대기 |

#### Phase 20-4: MCP 도구 확장

| # | 작업 | 내용 | 상태 |
|---|------|------|------|
| 1 | sap_adt_query MCP 도구 | tcode_info, search_object, table_structure 쿼리 타입 | 대기 |
| 2 | 온톨로지 쿼리 확장 | `code_trace` 쿼리 — tcode에서 실행되는 프로그램/테이블 역추적 | 대기 |
| 3 | 통합 Q&A | "VA01이 사용하는 테이블은?" 같은 코드 레벨 질문 지원 | 대기 |
| 4 | 테스트 | MCP 도구 단위 테스트 + 통합 시나리오 테스트 | 대기 |

#### 장기: ADT 쓰기 기능 (Go/No-Go 후)

| 작업 | 내용 | 상태 |
|------|------|------|
| 오브젝트 잠금/수정 | 소스코드 수정 + 트랜스포트 생성 (경영진 승인 필요) | 대기 |
| ATC 정적 분석 | 코드 품질 검사 자동 실행 | 대기 |
| 단위 테스트 실행 | ABAP Unit 원격 실행 + 결과 수집 | 대기 |
