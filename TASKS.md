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

## 남은 작업

### ReBAC / 운영 커널 후속 작업
- transcript의 screen transition을 온톨로지 screen_flow 후보로 반영
- 반복 성공한 action 패턴을 field dependency / action prior 후보로 반영
- 온톨로지 그래프 기반 관계 상속 (필드 레벨 세밀 제어)
- 런타임 승인 워크플로우 (MCP sampling 활용)
- transcript 기반 replay executor 또는 verifier dashboard

### 사내 검증 (SAP 환경 필요) → VERIFICATION.md 참조
- [ ] 시나리오 1: 기본 연결 및 화면 탐색
- [ ] 시나리오 2: MM03 자재 조회 자동화
- [ ] 시나리오 3: 매크로 녹화 → 재생 사이클
- [ ] 시나리오 4: 온톨로지 학습
- [ ] 시나리오 5: MCP 서버 + Claude Code 연동
- [ ] 시나리오 6: 데몬 모드 + Shell

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
- MCP: `process_list`, `process_show`, `process_create` 도구 추가
