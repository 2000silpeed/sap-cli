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
| FieldRegistryEntry 모델 | sap_field_name, description, occurrences(tcode+screen+element_id) | - | |
| FieldRegistry 모델 | dict[sap_field_name → FieldRegistryEntry] | - | |
| store: load/save/rebuild | field_registry.json CRUD + 전체 트랜잭션 스캔으로 재구축 | - | |
| learner 연동 | learn_current_screen() 후 _update_field_registry() 자동 호출 | 자동 | |
| 수동 어노테이션 | check_table, domain, data_type (SAP GUI에서 못 뽑는 정보) | 수동 | |
| CLI: sap relationships field MATNR | "MATNR이 쓰이는 트랜잭션/화면 목록" 조회 | - | |

**자동 추출 가능:** sap_field_name은 이미 _build_semantic에서 추출됨. learn만 하면 자동 축적.
**수동 필요:** check_table(MARA 등), domain, data_type(CHAR/NUMC 등)은 SAP GUI Scripting으로 못 뽑음.

---

### Phase 11-2: 필드 종속성 그래프

WERKS 없이 LGORT를 입력할 수 없다, KUNNR가 ZTERM 기본값을 결정한다 — 이런 관계.

| 작업 | 내용 | 자동/수동 | 상태 |
|------|------|----------|------|
| FieldRelationship 모델 | source_field, target_field, type(결정/종속/활성화/입력순서), observed_in | - | |
| FieldDependencyType enum | VALUE_DETERMINES, INPUT_ORDER, LOOKUP, FIELD_ACTIVATION | - | |
| store: load/save | field_relationships.json CRUD | - | |
| 기존 시드 자동 변환 | 트랜잭션별 field_dependencies를 글로벌 FieldRelationship으로 승격 | 자동 | |
| CLI: sap relationships depends WERKS | "LGORT가 WERKS에 종속 (MM03, ME21N에서 관찰됨)" | - | |
| CLI: sap annotate-dependency | 수동으로 필드 종속 관계 추가 | 수동 | |

**자동 추출 가능:** 기존 시드 데이터의 field_dependencies에서 자동 변환.
**수동 필요:** INPUT_ORDER(입력 순서), 새 트랜잭션의 종속 관계.

---

### Phase 11-3: 문서 흐름 온톨로지

VA01(수주) → VL01N(출하) → VF01(대금청구) 같은 문서 간 관계.

| 작업 | 내용 | 자동/수동 | 상태 |
|------|------|----------|------|
| DocumentFlowEdge 모델 | source_tcode, target_tcode, flow_type(생성/참조/후속), linking_field(VBELN 등) | - | |
| DocumentFlowType enum | CREATES, REFERENCES, SUCCEEDS, GENERATES | - | |
| store: load/save/rebuild | document_flow.json CRUD | - | |
| BusinessProcess 자동 변환 | DataFlow를 DocumentFlowEdge로 승격 | 자동 | |
| related_transactions 변환 | TransactionOntology.related_transactions를 edge로 변환 (low confidence) | 자동 | |
| CLI: sap relationships flow VA01 | "VA01 → VL01N (VBELN) → VF01 (VBELN)" | - | |

**자동 추출 가능:** BusinessProcess의 DataFlow + related_transactions에서 자동 생성.
**수동 필요:** flow_type 구분(CREATES vs REFERENCES), linking_field가 없는 경우.

---

### Phase 11-4: 그래프 엔진 통합

graph.py에 새 관계 데이터를 통합하여 경로 탐색, 영향도 분석 지원.

| 작업 | 내용 | 상태 |
|------|------|------|
| LinkType 확장 | SAME_FIELD, DOCUMENT_FLOW, FIELD_DETERMINES 추가 | |
| build_from_store 확장 | FieldRegistry → SAME_FIELD 엣지, DocumentFlow → DOCUMENT_FLOW 엣지 | |
| 쿼리 메서드 추가 | find_field_across_transactions(), get_document_flow(), get_field_dependencies() | |
| 경로 탐색 | get_process_path(from_tcode, to_tcode) — BFS로 문서 흐름 경로 찾기 | |

---

### Phase 11-5: MCP 도구 노출

AI가 관계를 자연스럽게 조회할 수 있도록 MCP 도구 추가.

| 작업 | 내용 | 상태 |
|------|------|------|
| sap_ontology_relationships MCP 도구 | field/document_flow/field_dependencies/process_path 파라미터 | |
| dispatcher 연동 | ontology_relationships 명령 + _dispatch 함수 | |
| sap_screen에 힌트 | "ontology_available: true" + 간단한 안내 메시지 (컨텍스트 낭비 없이) | |
| sap fill 온톨로지 연동 | 한글 동의어(synonyms_ko)로 필드 매핑 | |

---

### Phase 11-6: 자동 축적 (Learn → 온톨로지 자동 강화)

| 작업 | 내용 | 상태 |
|------|------|------|
| learn 후 필드 레지스트리 자동 갱신 | _update_field_registry(screen) | |
| learn 후 문서 흐름 힌트 자동 추가 | related_transactions + 같은 필드 보유 트랜잭션 연결 | |
| rebuild 명령 | sap ontology rebuild — 기존 데이터에서 전체 재구축 | |

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

## 남은 작업

### Phase 12: ALV Grid 데이터 읽기 강화

현재 `sap table`은 `GuiGridView`와 `GuiTableControl`만 지원.
실제 SAP에서 ALV Grid는 `GuiShell`(subtype=GridView) 안에 있는 경우가 많아 읽기 실패함.

| 작업 | 내용 | 상태 |
|------|------|------|
| GuiShell ALV 감지 | read_table에서 GuiShell일 때 SubType으로 ALV 여부 판단 | |
| ALV Grid 데이터 읽기 | GetCellValue, ColumnOrder, ColumnCount 등 ALV 전용 API 사용 | |
| ALV 컬럼 헤더 | GetColumnTitles로 한글/영문 컬럼명 수집 | |
| ALV 필터/정렬 상태 | GetFilteredColumnKeys, GetSortedColumnKeys 읽기 | |
| ALV 행 선택 | SelectedRows 속성으로 다중 행 선택 지원 | |
| ALV 더블클릭 | SetCurrentCell + DoubleClickCurrentCell로 행 진입 | |
| _scan_children ALV 인식 | GuiShell subtype이 ALV일 때 tables에 추가 (현재는 trees로만 잡힘) | |
| 스크롤 처리 | VisibleRowCount 넘는 행은 FirstVisibleRow 조정 후 재읽기 | |
| MCP: sap_table_read 개선 | GuiShell ALV도 자동 처리되도록 | |
| 테스트 | ALV 읽기/선택/스크롤 테스트 케이스 | |

### ReBAC / 운영 커널 후속 작업
- transcript의 screen transition을 온톨로지 screen_flow 후보로 반영
- 반복 성공한 action 패턴을 field dependency / action prior 후보로 반영
- 런타임 승인 워크플로우 (MCP sampling 활용)
- transcript 기반 replay executor 또는 verifier dashboard

### 사내 검증 (SAP 환경 필요) → VERIFICATION.md 참조
- [ ] 시나리오 1: 기본 연결 및 화면 탐색
- [ ] 시나리오 2: MM03 자재 조회 자동화
- [ ] 시나리오 3: 매크로 녹화 → 재생 사이클
- [ ] 시나리오 4: 온톨로지 학습
- [ ] 시나리오 5: MCP 서버 + Claude Code 연동
- [ ] 시나리오 6: 데몬 모드 + Shell
- [ ] 시나리오 7: Transcript / Replay Review
- [ ] 시나리오 8: 업무 프로세스 조회 및 관리
- [ ] 시나리오 9: 업무 프로세스 실행 (ProcessExecutor)
- [ ] 시나리오 10: 시드 트랜잭션 온톨로지 검증 (7개)

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

- `sap process list` — 등록된 프로세스 목록
- `sap process show <id>` — 프로세스 상세 (스텝 + 데이터 흐름)
- `sap process create <id> --name "..." --steps MM01,ME21N,MIGO` — 신규 생성
- `sap process delete <id>` — 프로세스 삭제
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
```
