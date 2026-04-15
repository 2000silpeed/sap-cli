# MCP Session Store + 온톨로지 학습 파이프라인 설계

## 1. 문제 정의

현재 transcript는 **개별 SAP 명령 단위**로 기록한다.
AI가 사용자 요청을 받아 여러 명령을 순차 실행하는 **전체 흐름**이 보이지 않는다.

- 사용자가 "VL33N에서 46개 아이템 배송 처리해줘"라고 요청
- AI가 connect → run VL33N → screen → table read → set → press 등 10개 명령 실행
- transcript에는 10개 개별 기록만 남고, **왜 이 순서로 실행했는지** 맥락이 없음

## 2. 목표

1. AI의 "의도 → 추론 → 실행 → 결과" 전체 흐름을 **세션 단위**로 기록
2. 성공한 세션에서 **핵심 흐름만 추출** (시행착오 제거)
3. 관리자 승인 후 온톨로지에 **workflow recipe**로 저장
4. 다음에 비슷한 요청이 오면 recipe를 참고하여 더 빠르고 정확하게 실행

## 3. 기존 구조와의 관계

```
기존 (유지):
  transcript (JSONL)     → 개별 명령 감사 로그 (보안/감사 목적)
  ontology (JSON)        → 트랜잭션/화면/필드 구조 지식
  BusinessProcess (JSON) → 수동 정의 업무 프로세스

신규 (추가):
  MCP Session Store      → AI 실행 세션 기록 (의도+추론+명령 시퀀스)
  Workflow Recipe         → 검증된 실행 패턴 (온톨로지에 저장)
```

기존 transcript는 그대로 유지 (보안 감사용). MCP Session은 별도 저장소에 기록.

## 4. 데이터 모델

### 4.1 MCP Session

```python
class McpSession(BaseModel):
    """AI 실행 세션 — 사용자 요청부터 최종 결과까지."""
    id: str                          # UUID
    started_at: str                  # ISO 8601
    finished_at: str | None = None
    status: str = "running"          # running, completed, failed, aborted

    # 컨텍스트
    agent: str = ""                  # AI 에이전트명
    sap_user: str = ""
    sap_system: str = ""
    source: str = "mcp"              # mcp, cli, shell

    # 사용자 요청 (AI가 받은 원문)
    user_request: str = ""           # "VL33N에서 46개 아이템 배송 처리해줘"

    # AI 추론 (왜 이 명령을 선택했는지)
    ai_reasoning: str = ""           # "VL33N 배송 문서 변경 → 아이템 선택 → 배송 처리"

    # 실행 단계
    steps: list[SessionStep] = []

    # 결과
    outcome: str = ""                # success, partial, failed
    outcome_summary: str = ""        # "46개 아이템 중 46개 배송 처리 완료"

    # 학습 후보 마킹
    candidate: bool = False          # 온톨로지 학습 후보 여부
    approved: bool = False           # 관리자 승인 여부
    recipe_id: str | None = None     # 승인 후 생성된 recipe ID
```

### 4.2 Session Step

```python
class SessionStep(BaseModel):
    """세션 내 개별 단계."""
    step_number: int
    timestamp: str

    # 무엇을 했는지
    command: str                     # dispatcher command (run, set, press, ...)
    tool_name: str                   # MCP tool name
    arguments: dict[str, Any] = {}

    # 왜 했는지 (AI가 채움)
    intent: str = ""                 # "테이블에서 전체 아이템 선택"

    # 결과
    outcome: str = ""                # success, error, skipped
    error: str = ""

    # SAP 상태 변화
    before_transaction: str = ""
    before_screen: int = 0
    after_transaction: str = ""
    after_screen: int = 0
    status_message: str = ""         # SAP 상태바

    # 시행착오 마킹
    is_exploration: bool = False     # True = 헤맨 단계 (정제 시 제거 대상)
    is_recovery: bool = False        # True = 에러 복구 단계
```

### 4.3 Workflow Recipe (온톨로지 확장)

```python
class WorkflowRecipe(BaseModel):
    """검증된 AI 실행 패턴 — 온톨로지에 저장."""
    id: str                          # UUID
    name: str                        # "VL33N 대량 배송 처리"
    description: str = ""
    tcode: str                       # 주 트랜잭션
    module: str = ""                 # SD, MM, FI, ...

    # 적용 조건
    trigger_pattern: str = ""        # "배송 처리", "대량 아이템 선택" 등 매칭 키워드
    preconditions: list[str] = []    # "VL33N 화면에 진입한 상태"

    # 정제된 실행 단계 (시행착오 제거 후)
    steps: list[RecipeStep] = []

    # 메타데이터
    source_sessions: list[str] = []  # 원본 session ID 목록
    success_count: int = 0           # 성공 횟수
    created_at: str = ""
    updated_at: str = ""
    approved_by: str = ""            # 승인자
```

```python
class RecipeStep(BaseModel):
    """레시피의 개별 단계."""
    step_number: int
    command: str
    tool_name: str
    arguments_template: dict[str, Any] = {}  # 변수 치환 가능 (${item_count} 등)
    intent: str = ""
    expected_screen: int = 0
    expected_status: str = ""        # "success" 등
```

## 5. 저장 구조

```
~/.sap_cli/
├── transcripts/              # 기존 유지 (개별 명령 감사 로그)
│   └── transcript-YYYY-MM-DD.jsonl
├── sessions/                 # 신규: MCP 세션 기록
│   ├── 2026-04-15/
│   │   ├── {session_id}.json
│   │   └── {session_id}.json
│   └── 2026-04-16/
│       └── ...
├── knowledge/                # 기존 온톨로지
│   ├── transactions/
│   │   └── VL33N.json        # ← recipes 필드 추가
│   ├── recipes/              # 신규: 검증된 워크플로우 레시피
│   │   ├── {recipe_id}.json
│   │   └── ...
│   ├── processes/            # 기존 유지
│   └── ...
```

## 6. 구현 계획

### Phase 14-1: MCP Session Logger (핵심)

세션 시작/종료, 단계 기록, user_request/ai_reasoning 수집.

| 작업 | 내용 | 파일 |
|------|------|------|
| 스키마 정의 | McpSession, SessionStep 모델 | ontology/schema.py |
| Session Store | 세션 CRUD (생성/단계추가/종료/조회) | session_store.py (신규) |
| MCP 연동 | handle_tool에서 세션 시작/단계 기록 훅 | mcp_server.py |
| Dispatcher 연동 | _log_transcript과 병행하여 세션 단계 기록 | dispatcher.py |
| MCP 도구 | sap_session_start, sap_session_end — AI가 직접 세션 경계 선언 | mcp_server.py |
| 설정 | SessionConfig 추가 (enabled, log_dir) | auth/policy.py |

**핵심 설계 결정 — 세션 경계 감지:**

MCP 서버는 stateless (도구 호출 단위)이므로 AI가 직접 세션 시작/종료를 선언한다:

```
AI: sap_session_start(request="VL33N 배송 처리", reasoning="...")
AI: sap_connect(...)
AI: sap_run(tcode="VL33N")
AI: sap_table_read(...)
AI: ...
AI: sap_session_end(outcome="success", summary="46개 배송 완료")
```

세션을 선언하지 않으면 기존처럼 transcript만 기록 (하위 호환).

### Phase 14-2: Session Analyzer (정제 엔진)

성공 세션에서 시행착오를 제거하고 핵심 흐름만 추출.

| 작업 | 내용 | 파일 |
|------|------|------|
| 탐색 단계 감지 | screen 조회, status 확인 등 정보 수집만 한 단계 마킹 | session_analyzer.py (신규) |
| 에러 복구 감지 | 에러 → 복구 패턴 감지 (vkey 12=취소, /n 등) | session_analyzer.py |
| 반복 제거 | 동일 명령 반복 실행 (retry) 중 마지막만 유지 | session_analyzer.py |
| 핵심 흐름 추출 | is_exploration, is_recovery 단계 제거 후 정제된 시퀀스 반환 | session_analyzer.py |
| candidate 자동 마킹 | 성공 세션 + 3단계 이상 + tcode 포함 → candidate=True | session_analyzer.py |

**정제 규칙:**

```
제거 대상:
  - command가 screen, status, modal, session인 단계 (읽기 전용 확인)
  - outcome이 error인 단계 + 바로 다음 복구 단계 (vkey 12, /nex 등)
  - 동일 command+arguments 연속 반복 중 마지막 외

유지 대상:
  - run, set, press, vkey, command, fill (실제 조작)
  - table_read → set → press 패턴 (데이터 기반 입력)
  - learn (온톨로지 학습도 기록)
```

### Phase 14-3: Workflow Recipe + 온톨로지 반영

정제된 세션을 recipe로 변환하고 관리자 승인 후 온톨로지에 저장.

| 작업 | 내용 | 파일 |
|------|------|------|
| 스키마 정의 | WorkflowRecipe, RecipeStep 모델 | ontology/schema.py |
| Recipe Store | recipes/ 디렉토리 CRUD | ontology/store.py 확장 |
| 세션→레시피 변환 | 정제된 세션을 RecipeStep 시퀀스로 변환 | session_analyzer.py |
| 변수 치환 | 구체값을 ${variable}로 일반화 | session_analyzer.py |
| 온톨로지 연결 | TransactionOntology.recipes에 recipe_id 추가 | ontology/store.py |
| MCP 도구 | sap_session_candidates — candidate 목록 조회 | mcp_server.py |
| MCP 도구 | sap_session_approve — candidate 승인 → recipe 생성 | mcp_server.py |
| MCP 도구 | sap_recipe_query — recipe 조회 (tcode/키워드) | mcp_server.py |
| CLI 명령 | sap session list/show/approve/reject | dispatcher.py |

**승인 흐름:**

```
1. AI 세션 실행 → session 저장 (status=completed)
2. Analyzer가 자동 정제 + candidate=True 마킹
3. 관리자: sap session candidates → 후보 목록 확인
4. 관리자: sap session approve {session_id} → recipe 생성
   - AI가 정제된 흐름을 보여주고 관리자가 확인
   - 승인 시 recipe JSON 생성 → knowledge/recipes/
   - 해당 tcode의 TransactionOntology.recipes에 recipe_id 추가
5. 이후 AI가 비슷한 요청을 받으면:
   - sap_ontology_query(tcode="VL33N") → recipes 목록 확인
   - sap_recipe_query(recipe_id="...") → 실행 단계 참고
```

### Phase 14-4: Recipe 활용 (AI 자동 참조)

| 작업 | 내용 | 파일 |
|------|------|------|
| ontology_query 확장 | 트랜잭션 조회 시 관련 recipes 요약 포함 | dispatcher.py |
| recipe 키워드 검색 | trigger_pattern 기반 매칭 | ontology/store.py |
| recipe 실행 가이드 | steps를 AI가 이해할 수 있는 형태로 반환 | dispatcher.py |

## 7. 구현 순서와 의존 관계

```
Phase 14-1 (Session Logger) ← 핵심, 나머지의 기반
    ↓
Phase 14-2 (Session Analyzer) ← 14-1 완료 후
    ↓
Phase 14-3 (Recipe + 승인) ← 14-2 완료 후
    ↓
Phase 14-4 (Recipe 활용) ← 14-3 완료 후
```

## 8. 기존 코드 영향 범위

| 파일 | 변경 내용 |
|------|-----------|
| ontology/schema.py | McpSession, SessionStep, WorkflowRecipe, RecipeStep 모델 추가 |
| auth/policy.py | SessionConfig 추가 |
| session_store.py | 신규 파일 — 세션 CRUD |
| session_analyzer.py | 신규 파일 — 정제 + candidate 마킹 + 레시피 변환 |
| ontology/store.py | recipes/ CRUD 메서드 추가 |
| mcp_server.py | session_start/end, session_candidates/approve, recipe_query 도구 |
| dispatcher.py | 세션 관련 명령 핸들러 + ontology_query 확장 |

## 9. 설계 원칙

1. **하위 호환**: 세션을 선언하지 않으면 기존 동작과 동일
2. **별도 저장소**: transcript(감사)와 session(학습)은 독립 — 목적이 다름
3. **수동 승인 기본**: candidate 자동 마킹 → 관리자 확인 후 승인
4. **AI 정제**: 헤맨 부분 자동 제거, 핵심 흐름만 recipe로
5. **점진적 학습**: recipe가 쌓일수록 AI가 더 정확한 실행 계획 수립
6. **SOS 철학 유지**: recipe는 시간공간(TemporalSpace)의 확장 — 검증된 시퀀스

## 10. 온톨로지 Q&A — SAP 문의 자동 응답 (Phase 15)

### 10.1 목표

저장된 온톨로지(7개 트랜잭션 + 프로세스 + 패턴 + 레시피)를 활용하여
SAP 관련 질문에 **구체적이고 구조화된 답변**을 자동 제공한다.

예시:
- "VA01에서 필수 필드가 뭐야?" → 스크린별 required=true 필드 목록
- "판매오더에서 납품까지 흐름" → DocumentFlow / BusinessProcess 경로
- "AUART가 뭐야?" → semantic 정보 (synonyms, validation, domain_context)
- "WERKS 필드가 어디어디 나와?" → FieldRegistry 교차 조회

### 10.2 기존 구조와의 관계

```
온톨로지 (이미 있음):
  knowledge/transactions/   → 7개 tcode 화면/필드/시맨틱 구조
  knowledge/processes/      → 업무 프로세스 (procurement, sales_to_billing)
  knowledge/patterns/       → 공통 패턴 (search_help, save_flow)
  knowledge/recipes/        → AI 네비게이션 레시피

조회 엔진 (이미 있음):
  OntologyGraph             → 결정론적 그래프 탐색
  OntologyStore             → JSON CRUD + 시맨틱 검색

신규 추가:
  Claude Code 스킬          → SAP 질문 자동 감지 → 온톨로지 읽기 → 답변
  CLI 커맨드 확장            → sap ontology ask "<질문>"
  MCP 도구 강화              → sap_ontology_query 시맨틱 검색 확장
```

### 10.3 구현 3단계

#### 단계 1: Claude Code 스킬 (코드 변경 없음)

`skill/sap-ontology-qa/` 디렉토리에 스킬 정의 파일 추가.
SAP 관련 키워드(tcode, 필드명, 모듈명)를 감지하면 자동 트리거.

**동작 방식:**
1. 사용자 질문에서 tcode / 필드명 / 모듈명 추출
2. `sap_cli/ontology/knowledge/` 하위 JSON 파일 직접 Read
3. 관련 정보를 구조화하여 답변

**트리거 키워드:**
- tcode: VA01, VL01N, VF01, ME21N, MIGO, MIRO, MM03 등
- 필드명: VBAK-AUART, MATNR, WERKS 등
- 모듈명: SD, MM, FI 등
- 한국어: "판매오더", "자재", "구매오더", "입고", "송장" 등

**장점:** MCP 서버 구동 없이 동작. 온톨로지 JSON만 읽으면 됨.

#### 단계 2: CLI 커맨드 확장

```bash
sap ontology ask "VA01에서 필수 필드"
sap ontology ask "판매에서 납품까지 흐름"
sap ontology ask "WERKS 필드가 사용되는 트랜잭션"
```

내부적으로 OntologyGraph + OntologyStore를 활용하여:
- tcode 매칭 → TransactionOntology 조회
- 필드명 매칭 → FieldRegistry 교차 조회
- 프로세스 매칭 → BusinessProcess 경로 반환
- 키워드 매칭 → semantic.synonyms 기반 퍼지 검색

#### 단계 3: MCP 도구 강화

기존 `sap_ontology_query`를 확장:

| 쿼리 타입 | 설명 | 데이터 소스 |
|-----------|------|------------|
| field_semantic | 필드 의미/검증/시맨틱 조회 | TransactionOntology.screens[].fields[].semantic |
| field_cross | 특정 필드가 나타나는 모든 tcode | FieldRegistry |
| process_guide | 업무 프로세스 스텝별 안내 | BusinessProcess |
| transaction_summary | tcode 전체 요약 (화면, 필드, 레시피) | TransactionOntology + Recipe |
| dependency_chain | 필드 종속성 체인 (A→B→C) | FieldDependencyGraph |

### 10.4 우선순위

```
단계 1 (스킬)      ← 코드 변경 없음, 즉시 적용 가능
    ↓
단계 2 (CLI)       ← OntologyGraph 활용, 오프라인 조회
    ↓
단계 3 (MCP 확장)  ← SAP 연결 시 실시간 조회와 결합
```

### 10.5 설계 원칙

1. **결정론적 조회**: 벡터 검색이 아닌 구조적 그래프 탐색 (SOS 철학 유지)
2. **시맨틱 풍부성**: 필드의 synonyms, validation, domain_context까지 활용
3. **한국어 우선**: synonyms에 한국어 포함, 질문도 한국어 지원
4. **점진적 확장**: 온톨로지 데이터가 쌓일수록 답변 범위 자동 확대
5. **하위 호환**: 기존 ontology_query, field_dependencies 도구는 그대로 유지

---

## 11. ADT 온톨로지 통합 — ABAP 코드 메타데이터 연동 (Phase 19)

> 참고 레포: [mcp-abap-adt](https://github.com/mario-andreschak/mcp-abap-adt), [mcp-abap-abap-adt-api](https://github.com/mario-andreschak/mcp-abap-abap-adt-api)

### 11.1 목표

현재 온톨로지는 **GUI 화면 기반** (tcode → screen → field/button/tree).
ADT REST API로 **코드 레벨 메타데이터**를 추가하여 온톨로지를 확장한다.

```
현재:  tcode → screen → field → value_help
확장:  tcode → program → class → table → data_element → domain
              ↓                    ↓
        function_module      check_table
```

AI 에이전트가 "VA01이 읽는 테이블은?", "MATNR 필드의 체크 테이블은?" 같은 코드 레벨 질문에 답할 수 있게 된다.

### 11.2 기존 구조와의 관계

```
기존 유지:
  ontology/schema.py    — SOS 기반 노드/엣지 스키마
  ontology/graph.py     — OntologyGraph 탐색 엔진
  ontology/store.py     — JSON 파일 기반 저장
  ontology/meta_edge.py — 엣지 타입 정의

확장:
  adt/client.py         — ADT REST API HTTP 클라이언트 (신규)
  adt/mapper.py         — ADT 응답 → 온톨로지 노드/엣지 변환 (신규)
  ontology/schema.py    — 노드/엣지 타입 추가 (수정)
  cli.py                — `sap adt` 서브커맨드 추가 (수정)
  mcp_server.py         — sap_adt_query 도구 추가 (수정)
```

### 11.3 ADT 클라이언트 설계

#### 11.3.1 인증 흐름

```
1. GET /sap/bc/adt/discovery
   → Header: Authorization: Basic base64(user:pass)
   → Header: X-SAP-Client: 100
   → Header: X-CSRF-Token: Fetch
   → Response Header에서 X-CSRF-Token 추출

2. 이후 모든 POST/PUT 요청에 CSRF 토큰 첨부
```

#### 11.3.2 연결 설정

```json
// ~/.sap_cli/adt_config.json
{
  "connections": {
    "DEV": {
      "url": "https://sapdev.company.com:44300",
      "client": "100",
      "user": "DEVELOPER",
      "language": "KO"
    },
    "PRD": {
      "url": "https://sapprd.company.com:44300",
      "client": "100",
      "user": "READONLY"
    }
  },
  "default": "DEV"
}
```

비밀번호는 설정 파일에 저장하지 않음. 환경변수 `SAP_ADT_PASSWORD` 또는 첫 연결 시 프롬프트.

#### 11.3.3 주요 ADT 엔드포인트

| 용도 | HTTP | 경로 |
|------|------|------|
| 서비스 디스커버리 | GET | `/sap/bc/adt/discovery` |
| 트랜잭션 속성 | GET | `/sap/bc/adt/repository/informationsystem/objectproperties/values` |
| 오브젝트 검색 | GET | `/sap/bc/adt/repository/informationsystem/search?query=<q>&type=<type>` |
| 프로그램 소스 | GET | `/sap/bc/adt/programs/programs/<name>/source/main` |
| 클래스 소스 | GET | `/sap/bc/adt/oo/classes/<name>/source/main` |
| 함수모듈 소스 | GET | `/sap/bc/adt/functions/groups/<group>/fmodules/<name>/source/main` |
| 테이블 DDic | GET | `/sap/bc/adt/ddic/tables/<name>/source/main` |
| 구조체 DDic | GET | `/sap/bc/adt/ddic/structures/<name>/source/main` |
| 데이터 요소 | GET | `/sap/bc/adt/ddic/dataelements/<name>` |
| 도메인 | GET | `/sap/bc/adt/ddic/domains/<name>` |
| 인터페이스 소스 | GET | `/sap/bc/adt/oo/interfaces/<name>/source/main` |
| 패키지 구조 | GET | `/sap/bc/adt/packages/<name>` |

### 11.4 온톨로지 스키마 확장

#### 11.4.1 새 노드 타입

```python
# ontology/schema.py 확장
"program": {
    "required": ["name", "type"],        # type: REPORT, MODULE_POOL, FUNCTION_GROUP, CLASS_POOL
    "optional": ["package", "description", "created_by"]
}
"abap_class": {
    "required": ["name"],
    "optional": ["package", "superclass", "interfaces", "description"]
}
"function_module": {
    "required": ["name", "function_group"],
    "optional": ["description", "import_params", "export_params"]
}
"table": {
    "required": ["name", "table_type"],   # TRANSP, CLUSTER, POOL
    "optional": ["package", "description", "fields"]
}
"data_element": {
    "required": ["name"],
    "optional": ["domain", "description", "label_short", "label_medium", "label_long"]
}
"domain": {
    "required": ["name", "data_type"],
    "optional": ["length", "value_range", "check_table"]
}
```

#### 11.4.2 새 엣지 타입

```python
# ontology/meta_edge.py 확장
"executes_program":    (tcode → program)
"calls_function":      (program → function_module)
"uses_class":          (program → abap_class)
"reads_table":         (program → table)  # SELECT 문 기반
"writes_table":        (program → table)  # INSERT/UPDATE/MODIFY 문 기반
"has_field":           (table → data_element)
"uses_domain":         (data_element → domain)
"checks_against":      (domain → table)   # check_table 관계
"belongs_to_package":  (program|class|table → package)
```

#### 11.4.3 기존 온톨로지와의 연결

```
기존 field 노드의 data_element 속성을 통해 자동 연결:

  screen:field(MATNR)
      ↓ uses_data_element
  data_element(MATNR)
      ↓ uses_domain
  domain(MATNR)
      ↓ checks_against
  table(MARA)  ← "자재 마스터 테이블"
```

### 11.5 ADT → 온톨로지 매핑 파이프라인

```
1. sap adt enrich <tcode>
   → ADT에서 tcode 속성 조회 → program 노드 생성 + executes_program 엣지
   → 프로그램 소스 조회 → SELECT/CALL FUNCTION 파싱 → table/function 노드 + 엣지
   → 테이블 DDic 조회 → data_element/domain 노드 + 엣지
   → 온톨로지 store에 저장

2. 자동화 (Phase 19-4 이후):
   learner.py 확장 — 화면 학습 시 ADT 메타데이터도 동시 수집
```

### 11.6 MCP 도구 설계

```python
# sap_adt_query: ADT 기반 코드 메타데이터 조회
{
    "name": "sap_adt_query",
    "params": {
        "query_type": "tcode_info | search_object | table_structure | code_trace | source_code",
        "target": "<tcode|object_name|table_name>",
        "options": {}  # search_type, max_results 등
    }
}

# 기존 sap_ontology_query 확장
# → code_trace 쿼리 타입 추가: tcode에서 실행되는 프로그램/테이블 역추적
```

### 11.7 설계 원칙

1. **읽기 전용 우선**: 쓰기 기능은 경영진 Go 이후에만 검토
2. **점진적 확장**: 기존 GUI 온톨로지는 그대로 유지, 코드 메타데이터는 별도 레이어로 추가
3. **SOS 철학 유지**: 벡터 검색이 아닌 구조적 그래프 탐색
4. **권한 분리**: ADT 접근은 GUI COM 접근과 별도 인증. ReBAC 정책에 `adt:read` 액션 추가
5. **소스 파싱은 최소한**: 정규식 기반 SELECT/CALL FUNCTION 추출만. AST 파싱은 과잉
6. **오프라인 캐시**: ADT 응답은 로컬 캐시 → 네트워크 없이도 온톨로지 조회 가능

### 11.8 착수 조건

- [ ] Phase 16 (연결 안정성) 완료
- [ ] Phase 17 (AI 학습 루프) 완료 또는 병행 가능 판단
- [ ] 레시피 50개+ 축적 (코드 레벨 질문 수요 확인)
- [ ] SAP 시스템 ADT SICF 서비스 활성화 확인
- [ ] 경영진 Go/No-Go 결정 후
