# 멀티유저 거버넌스 설계안 (Phase 21~23)

> 본 문서는 SAP CLI의 MCP 서버를 **여러 사용자가 동시에 사용하는 사내 공용 서버**로 격상하기 위한 설계안이다.
> 기반 권한 모델은 `DESIGN_REBAC.md` 의 ReBAC P0 (정책/체커/감사) 위에 얹는다.
> 즉, 본 문서는 ReBAC 의 "에이전트 → 리소스" 축에 **사용자(User) 차원과 소유권(Ownership) 차원** 을 추가한다.

---

## 1. 문제 정의

현재 SAP CLI는 사실상 **단일 워크스테이션 전제** 로 설계돼 있다.

| 영역 | 현재 가정 | 멀티유저 환경에서의 문제 |
|------|-----------|---------------------------|
| MCP 서버 | stdio 단일 클라이언트 | 동시 접속 클라이언트 식별 불가 |
| 레시피 저장 | `recipes/<id>.json` 단일 풀 | 누가 만들었는지, 누가 공유 가능한지 모름 |
| 검색 | 키워드 매칭 전체 반환 | 권한 무관 노출 → 정보 새거나 노이즈 |
| 버전 업 | 단독 사용자 가정 | 동시 version_up 충돌 검출 불가 |
| 감사 | 에이전트 단위 로그 | 사용자 단위 추적/리포트 부재 |
| 온톨로지 | 단일 그래프 | 팀별 커스터마이징 분리 불가 |

**원칙:** 한 번에 모든 걸 풀려 하지 않는다. 가장 침습이 적은 "사용자 식별 + 소유권 표시" 부터 단계적으로 도입한다.

---

## 2. 전체 아키텍처 (목표 상태)

```
사용자 (sso_id)
  │
  │  (사내 MCP 게이트웨이가 X-SAP-User 헤더 주입)
  ▼
MCP Server ── Identity Resolver ── policy.yaml (사용자/팀 매핑)
  │                                    │
  │                                    ▼
  ├─ Recipe Layer  ── owned_by, scope (own/team/public)
  ├─ Ontology Layer ── overlay (base + team_overlay)
  ├─ Audit Layer   ── per-user 조회/리포트
  └─ ReBAC Checker ── 기존 리소스 권한 + 사용자 차원
```

---

## 3. Phase 21 — 사용자 식별 + 소유권 (P0, 가장 작음)

### 3.1 목표

모든 레시피 / 세션 / 감사 로그에 `created_by`, `updated_by` 필드를 추가한다. **검색·승인·실행 동작 자체는 그대로** 두고, 데이터 차원만 확장한다.

### 3.2 사용자 식별 흐름

```
1) 사내 MCP 게이트웨이가 SSO 인증 후 X-SAP-User 헤더 주입
2) MCP 서버는 환경변수 SAP_CLI_USER, 또는 stdio 첫 핸드셰이크 메시지에서 user_id 추출
3) 추출 실패 시 fallback: SAP_CLI_USER 환경변수 → "anonymous"
4) 모든 dispatcher 호출에 user_id 컨텍스트 전달
```

**전제:** 인증 자체는 사내 게이트웨이가 책임진다. SAP CLI 는 헤더/환경변수를 **신뢰할 수 있는 입력** 으로만 받는다 (게이트웨이가 변조 차단).

### 3.3 데이터 모델 변경

레시피 (`Recipe` Pydantic 모델에 추가):

```python
created_by: str = "system"          # 기존 데이터 마이그레이션 호환
updated_by: str = "system"
created_at: str = ""                # 이미 있으면 유지
updated_at: str = ""
```

세션 (`Session` 모델):

```python
user_id: str = "system"
```

감사 로그 (`auth/audit.py`):

```python
{ "user_id": "...", "agent": "...", "tool": "...", "resource": "...", ... }
```

### 3.4 마이그레이션 전략

- 기존 `recipes/*.json` 의 누락 필드는 **로드 시점에 default 값으로 채움** (Pydantic default 활용 — 별도 마이그레이션 스크립트 불필요)
- 첫 update 시점에 `created_by="system"` → 그대로 유지, `updated_by` 만 현재 사용자로 갱신

### 3.5 산출물

- `sap_cli/identity.py` (UserContext, resolve_user)
- `Recipe.created_by/updated_by` 필드
- `Session.user_id` 필드
- `audit.log` 에 user_id 필드
- 테스트: 환경변수 → 컨텍스트, 익명 fallback, 로드 호환성

### 3.6 비기능 요구

- 기존 단일 사용자 모드는 그대로 동작 (env 미설정 시 anonymous)
- 디스크 스키마 호환성 유지 (Pydantic default)

---

## 4. Phase 22 — 스코프 기반 검색 + 권한 필터링 (P1)

### 4.1 목표

`sap_recipe_search` / `sap_recipe_list` / `sap_recipe_chain_search` 결과를 **사용자/팀/공용 스코프** 로 필터링.

### 4.2 스코프 정의

| scope | 의미 | 결정 방식 |
|-------|------|-----------|
| `own` | 본인이 만든 것 | `created_by == user_id` |
| `team` | 같은 팀 멤버가 만든 것 | `created_by ∈ team_members[user_team]` |
| `public` | 명시적으로 공개된 것 | `recipe.shared == True` |
| `all` | 권한이 허용하는 모든 것 | own ∪ team ∪ public |

### 4.3 정책 파일 확장 (`~/.sap_cli/policy.yaml`)

```yaml
teams:
  mm_team:
    members: ["user_a", "user_b"]
    leader: "user_a"
  fi_team:
    members: ["user_c", "user_d"]

users:
  user_a:
    team: mm_team
    role: operator     # ReBAC 기존 역할
    can_share: true    # 본인 레시피를 public 으로 올릴 수 있는가
  user_b:
    team: mm_team
    role: viewer
    can_share: false
```

### 4.4 API 변경

```jsonc
// MCP tool: sap_recipe_search
{
  "query": "MM03 재무",
  "scope": "team",        // own | team | public | all (default: all)
  "include_disabled": false
}
```

응답에 다음 메타 추가:

```jsonc
{
  "results": [...],
  "scope_summary": { "own": 3, "team": 5, "public": 12 },
  "filtered_out_by_permission": 2
}
```

### 4.5 신규 MCP 도구

- `sap_recipe_share { "recipe_id": "...", "shared": true|false }` — 본인 레시피를 public 으로 토글 (`can_share` 권한 필요)
- `sap_recipe_transfer { "recipe_id": "...", "to_user": "..." }` — 소유권 이전 (관리자 권한)

### 4.6 산출물

- `sap_cli/scope.py` (스코프 해석기)
- `recipe.search()` / `recipe.find_chain()` 시그니처에 `user_ctx` / `scope` 추가
- 정책 파일 스키마 v2 (teams 섹션)
- 테스트: 스코프별 가시성, 공유 토글, 권한 없는 사용자 차단

### 4.7 비기능 요구

- 검색 성능: 사용자당 레시피 수 < 1000 가정, 인덱싱 불필요
- 스코프 default 는 `all` 로 두어 기존 사용자 동작 유지

---

## 5. Phase 23 — 협업 버전닝 + 감사 가시성 (P2)

### 5.1 목표

여러 사용자가 같은 레시피를 동시에 수정하는 상황에 대비. 감사 로그를 사용자 단위로 조회·집계.

### 5.2 협업 버전닝

문제: 사용자 A 가 v2 로 올린 직후 사용자 B 가 v1 기준으로 v2 를 다시 올리려고 함 → 충돌.

해결 (낙관적 동시성):

```python
def version_up(recipe_id, expected_version, note, user_id):
    current = load(recipe_id)
    if current.version != expected_version:
        raise VersionConflict(
            current=current.version,
            expected=expected_version,
            updated_by=current.updated_by,
            updated_at=current.updated_at,
        )
    # 진행
```

MCP API:

```jsonc
{
  "recipe_id": "...",
  "expected_version": 2,    // 신규 필수 파라미터
  "note": "..."
}
```

충돌 응답 시 사용자가 `sap_recipe_history` 로 변경 사항 확인 후 재시도.

### 5.3 disabled 가시성

현재 disabled 는 단일 플래그. 멀티유저에서는:

- 누가/언제/왜 disable 했는지 표시 (`disabled_by`, `disabled_at` 필드 추가)
- 본인이 비활성화한 레시피와 시스템(자동)이 비활성화한 레시피 구분

### 5.4 감사 조회 도구

신규 MCP 도구:

```
sap_audit_query {
  "user_id": "...",      // 옵션
  "tool": "...",         // 옵션
  "since": "ISO8601",    // 옵션
  "until": "ISO8601",    // 옵션
  "limit": 100
}
```

권한:
- 본인 로그: 누구나 조회 가능
- 타인 로그: 관리자(`role: admin`) 만 조회 가능

### 5.5 사용자 대시보드 CLI

```
sap audit summary --user user_a --period 7d
# → 레시피 실행 횟수, 성공률, 가장 자주 쓴 tcode, disabled 기여도 등
```

### 5.6 산출물

- `Recipe.expected_version` 충돌 검사 로직
- `Recipe.disabled_by`, `Recipe.disabled_at`
- `sap_audit_query`, `sap audit summary`
- 테스트: 충돌 검출, 권한별 감사 노출, 집계 정확성

---

## 6. 온톨로지 거버넌스 (장기 검토)

Phase 21~23 이후 상황을 보고 결정. 후보:

- **Base 그래프 + 팀별 overlay**: 팀이 자체 추가한 노드/엣지를 별도 파일로 보관, 로드 시 머지
- **Read-only base + Write-allowed overlay**: 기본 온톨로지는 운영자만 수정 가능, 팀은 overlay 만 편집
- **Pull request 모델**: 팀 overlay 의 base 승격을 운영자 승인 흐름으로

→ 레시피 거버넌스가 안정화된 후 (Phase 23 완료 후 1개월) 별도 설계 문서 작성.

---

## 7. 위험과 트레이드오프

| 위험 | 완화책 |
|------|--------|
| 헤더 위변조 | 사내 MCP 게이트웨이가 인증 책임. SAP CLI 는 신뢰 경계 안에서만 동작 |
| 정책 파일이 비대해짐 | 100명 이상 시 외부 디렉토리(LDAP) 연동 검토 (Phase 24+) |
| 기존 레시피 마이그레이션 누락 | Pydantic default 로 자동 채움, 명시 마이그레이션 스크립트 불필요 |
| 동시성 (file lock) | atomic save 는 이미 있음. 충돌은 expected_version 으로 검출 (DB 도입 회피) |
| 감사 로그 비대 | 일별 로테이션, 90일 후 압축 보관 (별도 스크립트) |

---

## 8. 의존 관계 / 진행 순서

```
Phase 21 (사용자 식별)
    ├──▶ Phase 22 (스코프 검색)         — 21의 created_by 필드 필요
    └──▶ Phase 23 (협업 + 감사)         — 21의 user_id 컨텍스트 필요

Phase 22 ──▶ Phase 23 (스코프 정책 위에 충돌/감사 얹음)
```

**가장 짧은 가치 도달 경로:** Phase 21 만 완료해도 "누가 만들었는지" 가 보이므로 사내 운영팀 거버넌스의 90% 가치 확보. Phase 22~23 은 사용자 수가 10명 넘어갈 때 본격 필요.

---

## 9. Out of Scope (명시적으로 안 함)

- SSO/OAuth 직접 구현 (게이트웨이 책임)
- 데이터베이스 도입 (파일 기반 유지)
- 웹 UI (CLI/MCP 만)
- 실시간 알림 (감사 로그 polling 으로 대체)
- 온톨로지 거버넌스 (별도 설계로 분리)
