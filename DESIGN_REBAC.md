# ReBAC 권한관리 설계안

## 개요

SAP CLI의 MCP 서버가 AI 에이전트에게 노출하는 SAP 조작을 **ReBAC(Relationship-Based Access Control)**으로 제어한다.

기존 RBAC은 "역할 → 권한" 매핑이 고정적이지만, ReBAC은 **리소스 간 관계**를 기반으로 권한을 동적으로 판단한다. SAP의 Authorization Object 개념과 자연스럽게 대응된다.

## 왜 ReBAC인가

1. **SAP 자체가 관계 기반**: SAP Authorization Object는 (Activity + Field Value) 조합으로 권한을 결정. 예: `M_MATE_WRK`는 자재유형(MTART)과 플랜트(WERKS) 관계로 접근 제어.
2. **AI 에이전트 맥락**: 같은 에이전트라도 "조회 모드"와 "실행 모드"에서 허용 범위가 달라야 함.
3. **온톨로지와 자연스러운 통합**: 이미 트랜잭션 → 화면 → 필드 그래프가 존재. 권한도 이 그래프 위의 관계로 표현 가능.

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
```

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

## SAP Authorization Object 매핑

```
ReBAC 관계          SAP Authorization Object   Activity
──────────────────────────────────────────────────────────
viewer on tx:MM03   M_MATE_MAT (자재조회)      03 (Display)
operator on tx:VA01 V_VBAK_AAT (수주)          01 (Create)
supervisor on tx:*  S_TCODE (트랜잭션 권한)    *
```

## 감사 로그 형식

```jsonl
{"ts":"2026-03-29T10:30:00Z","agent":"claude_mm","tool":"sap_set_field","resource":"field:MM03:100:RMMG1-MATNR","permission":"write","allowed":true,"arguments":{"element_id":"wnd[0]/usr/ctxtRMMG1-MATNR","value":"100-100"}}
{"ts":"2026-03-29T10:30:05Z","agent":"claude_mm","tool":"sap_run","resource":"tx:ME21N","permission":"execute","allowed":false,"reason":"dangerous_transaction:requires_approval"}
```

## 구현 우선순위

1. **P0**: 정책 파일 로드 + read/write 모드 분리 + 트랜잭션 허용목록
2. **P1**: 감사 로그 + 위험 트랜잭션 경고
3. **P2**: 온톨로지 그래프 기반 관계 상속
4. **P3**: 런타임 승인 워크플로우
