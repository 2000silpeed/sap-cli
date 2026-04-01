# ReBAC 권한관리 설계안

## 개요

SAP CLI의 MCP 서버가 AI 에이전트에게 노출하는 SAP 조작을 **ReBAC(Relationship-Based Access Control)**으로 제어한다.

기존 RBAC은 "역할 → 권한" 매핑이 고정적이지만, ReBAC은 **리소스 간 관계**를 기반으로 권한을 동적으로 판단한다. SAP의 Authorization Object 개념과 자연스럽게 대응된다.

## 왜 ReBAC인가

1. **SAP 자체가 관계 기반**: SAP Authorization Object는 (Activity + Field Value) 조합으로 권한을 결정. 예: `M_MATE_WRK`는 자재유형(MTART)과 플랜트(WERKS) 관계로 접근 제어.
2. **AI 에이전트 맥락**: 같은 에이전트라도 "조회 모드"와 "실행 모드"에서 허용 범위가 달라야 함.
3. **온톨로지와 자연스러운 통합**: 이미 트랜잭션 → 화면 → 필드 그래프가 존재. 권한도 이 그래프 위의 관계로 표현 가능.

## 온톨로지와 Transcript의 역할 분리

이 설계에서 온톨로지는 버려지는 것이 아니다. 오히려 ReBAC과 transcript를 붙이면서 역할이 더 선명해진다.

- 온톨로지: 구조적 의미 기억. 트랜잭션, 화면, 필드, 액션, 화면 전이의 일반 규칙을 담는다.
- transcript: 사건 기록. 특정 시점에 어떤 agent가 어떤 tool을 어떤 화면에서 실행했고, before/after가 어땠는지 담는다.
- replay review planner: transcript를 읽어 운영상 재실행 전제조건과 위험을 계산한다.

즉 온톨로지는 "세상이 어떻게 생겼는가", transcript는 "방금 무슨 일이 있었는가"를 담당한다.

## 핵심 모델

### 1. 리소스 (Resource)

```
리소스 타입        예시                     그래프 노드 ID
─────────────────────────────────────────────────────────
system            SAP ERP (SID: P01)       system:P01
transaction       MM03, VA01               tx:MM03
screen            MM03 초기화면            screen:MM03:100
field             자재번호 필드            field:MM03:100:RMMG1-MATNR
table             자재 테이블              table:MM03:200:GRID
```

### 2. 관계 (Relation)

```
관계              의미                              예시
──────────────────────────────────────────────────────────
owner             시스템 소유자                      admin → system:P01
viewer            조회만 가능                        agent_readonly → tx:MM03
operator          조회 + 실행 가능                   agent_standard → tx:VA01
supervisor        조회 + 실행 + 승인 가능            human_user → tx:ME21N
parent            상위 리소스 (권한 상속)             tx:MM03 → screen:MM03:100
```

### 3. 권한 (Permission)

```
권한              매핑되는 SAP 조작
──────────────────────────────────────────
read              sap_session, sap_screen, sap_get_field, sap_get_status, sap_table_read
execute           sap_run, sap_press, sap_send_vkey
write             sap_set_field, sap_macro_record_step
admin             sap_connect, sap_learn, sap_macro_control
```

### 4. 권한 판단 흐름

```
에이전트가 sap_set_field("wnd[0]/usr/ctxtRMMG1-MATNR", "100-100") 호출

1. 리소스 식별: field:MM03:100:RMMG1-MATNR
2. 필요 권한: write
3. 관계 체크 (상향 탐색):
   - agent에 field:MM03:100:RMMG1-MATNR과의 관계가 있는가? → 없음
   - field의 parent인 screen:MM03:100 과의 관계? → 없음
   - screen의 parent인 tx:MM03 과의 관계? → operator 관계 있음
   - operator는 write 권한 포함? → YES → 허용
4. 감사 로그 기록
```

### 5. 정책 파일 구조

`~/.sap_cli/policy.yaml`:

```yaml
# ReBAC 정책 정의
version: 1

# 에이전트 정의
agents:
  claude_readonly:
    description: "조회 전용 에이전트"
    relations:
      - resource: "tx:*"
        relation: viewer

  claude_verifier:
    description: "실행 후 검토 전용 에이전트"
    relations:
      - resource: "tx:*"
        relation: viewer

  claude_mm:
    description: "MM 모듈 운영 에이전트"
    relations:
      - resource: "tx:MM*"
        relation: operator
      - resource: "tx:VA*"
        relation: viewer

  claude_full:
    description: "전체 권한 에이전트 (승인 필요)"
    relations:
      - resource: "system:*"
        relation: operator
    requires_approval:
      - permission: write
        transactions: ["ME21N", "ME29N", "FB01", "VF01"]

# 관계 → 권한 매핑
relation_permissions:
  viewer:    [read]
  operator:  [read, execute, write]
  supervisor: [read, execute, write, admin]
  owner:     [read, execute, write, admin]

# 위험 트랜잭션 (항상 승인 필요)
dangerous_transactions:
  - tcode: "SM30"
    reason: "테이블 직접 수정"
  - tcode: "SE38"
    reason: "ABAP 프로그램 실행"
  - tcode: "SU01"
    reason: "사용자 관리"

# 감사 로그 설정
audit:
  enabled: true
  log_dir: "~/.sap_cli/audit/"
  log_level: "all"  # all | write_only | dangerous_only

transcript:
  enabled: true
  log_dir: "~/.sap_cli/transcripts/"
  include_reads: true
  include_screen_summary: false
```

### 6. MCP Tool Surface 분리

ReBAC는 실행 시점의 허용/거부만 담당하지 않는다. MCP 서버는 활성 agent profile에 따라 보이는 도구 자체를 줄인다.

- `readonly`, `verifier`: read-only tool surface만 노출
- `operator`: 실행/입력 tool surface 노출, admin surface 숨김
- `mm_operator`, `sd_operator`: 실행 tool surface는 보이지만 실제 호출 시 리소스 범위로 재검사

이 방식의 장점은 prompt로 "하지 마"라고 쓰는 것보다 훨씬 강하다는 점이다. verifier agent는 `sap_run`, `sap_set_field`, `sap_send_vkey`를 아예 보지 못한다.

## 구현 계획

### Phase 1: 기본 권한 게이트 (필수)

1. `sap_cli/auth/policy.py` — 정책 파일 로드/파싱
2. `sap_cli/auth/checker.py` — ReBAC 권한 판단 엔진
3. `sap_cli/auth/audit.py` — 감사 로그 기록
4. MCP 서버의 `handle_tool`에 권한 체크 삽입

```python
# handle_tool 내부
def handle_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    # 권한 체크
    resource = resolve_resource(name, arguments)
    permission = tool_permission_map[name]
    if not checker.is_allowed(current_agent, resource, permission):
        return _error_result(f"권한 없음: {permission} on {resource}")

    # 위험 트랜잭션 승인 체크
    if checker.requires_approval(resource):
        return _json_result({
            "approval_required": True,
            "resource": str(resource),
            "reason": checker.get_danger_reason(resource),
        })

    # 실행 + 감사 로그
    result = _execute_tool(name, arguments)
    audit.log(current_agent, name, arguments, result)
    return result
```

### Phase 2: 온톨로지 연동

- 그래프의 parent 관계를 활용한 권한 상속
- 필드 레벨 세밀한 제어 (특정 필드만 읽기 전용)
- 학습된 화면 구조 기반 동적 리소스 발견

### Phase 3: 런타임 승인 워크플로우

- 위험 트랜잭션 실행 시 사용자 승인 요청
- MCP 프로토콜의 sampling 기능 활용
- 승인/거부 이력 감사 로그에 기록

### Phase 4: Transcript / Replay Review 계층

- dispatcher가 before/after 상태를 JSONL transcript로 기록
- `sap_transcript_query`로 최근 성공/실패/승인요청 흐름 조회
- `sap_transcript_plan`으로 재실행 전제조건, 위험도, ontology 조회 힌트, 권장 확인 순서 생성
- operator와 verifier를 같은 시스템 안에서 분리
- 이후 단계에서 transcript의 `before_screen -> action -> after_screen`를 온톨로지 screen flow 후보로 승격 가능

## SAP Authorization Object 매핑

```
ReBAC 관계          SAP Authorization Object   Activity
──────────────────────────────────────────────────────────
viewer on tx:MM03   M_MATE_MAT (자재조회)      03 (Display)
operator on tx:VA01 V_VBAK_AAT (수주)          01 (Create)
supervisor on tx:*  S_TCODE (트랜잭션 권한)    *
```

## 감사 로그와 Transcript 형식

```jsonl
{"ts":"2026-03-29T10:30:00Z","agent":"claude_mm","tool":"sap_set_field","resource":"field:MM03:100:RMMG1-MATNR","permission":"write","allowed":true,"arguments":{"element_id":"wnd[0]/usr/ctxtRMMG1-MATNR","value":"100-100"}}
{"ts":"2026-03-29T10:30:05Z","agent":"claude_mm","tool":"sap_run","resource":"tx:ME21N","permission":"execute","allowed":false,"reason":"dangerous_transaction:requires_approval"}
```

Transcript 예시:

```jsonl
{"ts":"2026-04-01T12:00:00Z","source":"mcp","command":"set","tool":"sap_set_field","agent":"operator","permission":"write","resource":"element:wnd[0]/usr/ctxtRMMG1-MATNR","allowed":true,"approval_required":false,"outcome":"success","before":{"session":{"transaction":"MM03","screen_number":4000}},"after":{"session":{"transaction":"MM03","screen_number":4000},"status":{"type":"S","message":"ok"},"screen_summary":{"title":"Material Display"}}}
```

이 transcript를 바탕으로 `sap_transcript_plan`은 아래 같은 검토 결과를 만든다.

- 위험도: `high`
- replayable 여부
- before/after transaction, screen
- ontology_context (`available`, `tcode`, `query_hint`)
- preconditions
- proposed_sequence (`sap session` → `sap ontology MM03` → 후보 action → `sap status` → `sap modal`)

## 구현 우선순위

1. **P0**: 정책 파일 로드 + read/write 모드 분리 + 트랜잭션 허용목록
2. **P1**: 감사 로그 + 위험 트랜잭션 경고 + transcript 적재
3. **P2**: MCP tool surface 분리 + transcript review planner
4. **P3**: 온톨로지 그래프 기반 관계 상속
5. **P4**: 런타임 승인 워크플로우
