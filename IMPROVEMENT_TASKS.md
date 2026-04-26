# SAP CLI 개선 실행 계획

작성일: 2026-04-25  
목적: 현재 기능 추가보다 우선해야 할 **일관성 정리, 운영 안정성, 유지보수성 개선** 작업을 실행 가능한 task 단위로 정리한다.

---

## 현재 진단 요약

현재 `sap_cli`는 단순 SAP GUI 자동화 CLI를 넘어 다음 계층까지 확장되어 있다.

- SAP GUI COM wrapper
- CLI / MCP / daemon / shell 실행 표면
- ReBAC 권한 제어
- audit / transcript / session 기록
- macro 녹화·재생
- ontology 구조 기억
- recipe 실행 경로 기억
- session 분석 기반 recipe 후보 생성
- multi-session slot pool

기능 범위는 충분히 커졌고, 이제는 **새 기능 추가보다 구조 정리와 운영 신뢰성 확보가 우선**이다.

최근 점검 기준:

- 전체 테스트: `482 passed`
- 실패: 없음
- 주요 구조 이슈: `COMMAND_REGISTRY` / MCP tool definition / `TOOL_PERMISSION_MAP` 불일치 1차 정리 완료

---

# Phase A: 즉시 안정화

목표: 현재 깨진 테스트와 ReBAC 운영 리스크를 먼저 제거한다.

## A-1. ALV Grid row limit 스펙 확정

### 문제

`SapGuiWrapper.read_table()`의 기본값은 `max_rows=50`이다.

하지만 테스트 `test_max_500_rows`는 기본 호출에서 500행을 기대한다.

현재 결과:

```text
Expected: 500 rows
Actual: 50 rows
```

### 결정 필요

둘 중 하나를 명확히 선택한다.

1. 기본 50행 유지
   - CLI/MCP 기본 응답을 가볍게 유지
   - 대량 ALV는 명시적으로 `max_rows=500` 요청
   - 테스트를 50행 기준으로 수정

2. 기본 500행으로 변경
   - 테스트 기대와 일치
   - 대량 ALV 기본 수집 가능
   - AI context/token 부담 증가 가능

### 권장

기본 50행 유지.

SAP ALV는 데이터가 클 수 있고, AI/MCP 응답에 500행을 기본으로 넣는 것은 무겁다.  
대량 데이터가 필요한 경우 명시적으로 `max_rows`를 지정하는 편이 안전하다.

### 진행 상태

완료일: 2026-04-25

- 기본 50행 유지로 스펙 확정
- `test_max_500_rows`는 명시적으로 `max_rows=500`을 넘기도록 수정
- 기본 50행 동작 검증 테스트 추가
- 전체 테스트 통과 확인: `482 passed`

### 작업

- [x] `tests/test_alv_grid.py::test_max_500_rows`를 명시적 `max_rows=500` 호출로 수정
- [x] 기본 50행 동작을 검증하는 테스트 추가 또는 기존 테스트 명확화
- [x] `read_table()` docstring과 README의 설명이 일치하는지 확인
- [x] 전체 테스트 실행

### 완료 기준

- [x] 전체 테스트가 통과한다.
- [x] 기본 row limit 스펙이 코드/테스트/문서에서 일치한다.

---

## A-2. ReBAC TOOL_PERMISSION_MAP 누락 보강

### 문제

`dispatcher.COMMAND_REGISTRY`에는 등록되어 있지만 `auth.policy.TOOL_PERMISSION_MAP`에 없는 도구가 있다.

확인된 누락:

```text
sap_annotate_dependency
sap_annotate_field
sap_capture
sap_capture_list
sap_fill_fields
sap_ontology_ask
sap_recipe_chain_search
sap_recipe_enable
sap_recipe_failure
sap_recipe_history
sap_recipe_pending
sap_recipe_query
sap_recipe_version_up
sap_session_approve
sap_session_candidates
sap_tree_read
```

### 영향

정책 파일이 없을 때는 전체 허용 모드라 문제가 드러나지 않는다.  
하지만 ReBAC enforcing 모드에서는 다음 문제가 발생할 수 있다.

- MCP surface에서 도구가 숨겨짐
- 권한 체크에서 알 수 없는 도구로 거부됨
- 최신 recipe/session/tree/fill 기능이 운영 환경에서 사용 불가

### 진행 상태

완료일: 2026-04-25

- 확인된 누락 16개를 `TOOL_PERMISSION_MAP`에 추가
- `CommandDef.permission`과 policy permission 분류가 일치하는지 consistency 테스트 추가
- ReBAC enforcing 모드에서 최신 MCP 도구가 권한 레벨대로 노출되는지 테스트 추가

### 작업

- [x] `sap_cli/auth/policy.py`의 `TOOL_PERMISSION_MAP`에 누락 도구 추가
- [x] 각 도구의 permission 분류 검토
  - `sap_fill_fields`: `WRITE`
  - `sap_tree_read`: `READ`
  - `sap_capture`: `READ`
  - `sap_capture_list`: `READ`
  - `sap_ontology_ask`: `READ`
  - `sap_annotate_field`: `WRITE`
  - `sap_annotate_dependency`: `WRITE`
  - `sap_recipe_query`: `READ`
  - `sap_recipe_pending`: `READ`
  - `sap_recipe_failure`: `WRITE`
  - `sap_recipe_enable`: `ADMIN`
  - `sap_recipe_version_up`: `WRITE`
  - `sap_recipe_history`: `READ`
  - `sap_recipe_chain_search`: `READ`
  - `sap_session_candidates`: `READ`
  - `sap_session_approve`: `ADMIN`
- [x] ReBAC enforcing 테스트 추가

### 완료 기준

- [x] `COMMAND_REGISTRY`의 모든 tool_name이 `TOOL_PERMISSION_MAP`에 존재한다.
- [x] 정책 활성화 상태에서 최신 MCP 도구가 의도대로 노출된다.

---

## A-3. Registry / MCP / Policy 일관성 테스트 추가

### 문제

현재 명령 정의가 여러 곳에 흩어져 있다.

- `dispatcher.COMMAND_REGISTRY`
- `auth.policy.TOOL_PERMISSION_MAP`
- `mcp_server.TOOL_DEFINITIONS`
- CLI Typer command definitions

이 중 하나가 누락되어도 테스트가 자동으로 잡지 못한다.

### 진행 상태

완료일: 2026-04-25

- `tests/test_command_surface_consistency.py` 추가
- dispatcher/policy 권한 누락 및 권한 분류 drift 검증 추가
- MCP definition과 dispatcher mapping drift 검증 추가
- dispatcher에는 있으나 MCP에는 의도적으로 노출하지 않는 도구 allowlist 확정

### 작업

- [x] `tests/test_command_surface_consistency.py` 추가
- [x] `COMMAND_REGISTRY.tool_name ⊆ TOOL_PERMISSION_MAP` 검증
- [x] `MCP_TOOL_TO_COMMAND`와 `TOOL_DEFINITIONS` 일치 검증
- [x] 의도적으로 MCP에 노출하지 않는 dispatcher 도구는 allowlist로 관리
- [x] 의도적으로 CLI에 없는 MCP 전용 도구도 allowlist로 문서화

### 권장 allowlist 초안

dispatcher에는 있으나 MCP에는 없을 수 있는 도구:

```text
sap_capture
sap_capture_list
sap_ontology_ask
sap_ontology_rebuild
```

이 목록은 2026-04-25 Phase A 작업에서 테스트 allowlist로 확정했다.

MCP에는 노출하지만 아직 별도 CLI subcommand가 없는 도구:

```text
recipe_chain
recipe_enable
recipe_failure
recipe_history
recipe_pending
recipe_query
recipe_show
recipe_success
recipe_version_up
session_approve
session_candidates
session_end
session_list
session_show
session_start
slot_allocate
slot_release
```

이 목록은 2026-04-25 Phase B 착수 작업에서 `tests/test_command_surface_consistency.py`의 allowlist와
`CommandDef.expose_cli=False` 메타데이터로 문서화했다.

### 완료 기준

- [x] 향후 새 command 추가 시 policy/MCP 누락이 테스트에서 잡힌다.
- [x] 의도적 비노출 도구는 allowlist와 주석으로 설명된다.

---

# Phase B: 명령 표면 정리

목표: CLI/MCP/daemon/dispatcher 간 명령 노출 정책을 정리한다.

## B-1. COMMAND_REGISTRY를 단일 진실 공급원으로 격상

### 문제

현재 `COMMAND_REGISTRY`는 중앙 dispatcher의 핵심이지만, MCP schema와 policy map은 별도로 관리된다.

### 작업

- [x] `CommandDef`에 추가 메타데이터 도입 검토
  - `expose_cli: bool`
  - `expose_mcp: bool`
  - `description: str`
  - `category: str`
  - `risk_level: str`
- [x] MCP tool definition 자동 생성 가능성 검토
- [x] 최소한 policy permission은 `CommandDef.permission`에서 자동 검증되도록 테스트 작성

### 진행 상태

진행일: 2026-04-25

- `CommandDef`에 `expose_cli`, `expose_mcp`, `description`, `category`, `risk_level` 메타데이터 필드를 추가했다.
- 대규모 리팩토링 없이 현재 수동 MCP schema는 유지하되, `CommandDef.expose_mcp`와 실제 `TOOL_DEFINITIONS`가 drift 없이 일치하는지 테스트를 추가했다.
- CLI는 Typer command 이름과 dispatcher command key가 다를 수 있으므로, `sap_cli/cli.py`의 `_run_command()` / `_dispatch_local()` 호출을 정적으로 수집해 `CommandDef.expose_cli`와 비교하는 테스트를 추가했다.
- MCP 전용 command는 allowlist와 `expose_cli=False`로 명시했다.
- policy permission은 기존 `test_registry_permission_matches_policy_permission`에서 `CommandDef.permission`과 `TOOL_PERMISSION_MAP` 일치를 검증하며, 이번 작업에서 유지/강화했다.
- `MCP_TOOL_TO_COMMAND`를 `CommandDef.expose_mcp=True`인 command만 포함하도록 변경해 MCP 역해석 경로에서도 비노출 도구가 `Unknown tool`로 거부되게 했다.
- `expose_mcp=False` 도구가 MCP mapping/resolve 경로에 절대 포함되지 않는 consistency 테스트를 추가했다.

### 완료 기준

- [x] command 추가 시 수정해야 할 위치가 줄어든다.
- [x] dispatcher/policy/MCP drift가 구조적으로 줄어든다.

---

## B-2. Recipe CLI 표면 확장 여부 결정

### 문제

최신 recipe 기능은 MCP에는 있으나 CLI에는 일부만 보인다.

CLI에 있는 명령:

```text
recipe-save
recipe-search
recipe-show
recipe-query
recipe-list
recipe-pending
recipe-history
recipe-chain
recipe-delete
```

MCP/dispatcher에는 있으나 CLI에 없는 상태 변경 중심 명령:

```text
recipe-failure
recipe-enable
recipe-version-up
recipe-success
```

### 작업

- [x] 각 recipe 기능을 CLI에도 노출할지 결정
- [x] 운영자용 CLI 명령은 추가하는 방향 권장
- [x] 명령 이름 규칙 통일
  - `recipe-version-up`
  - `recipe-history`
  - `recipe-failure`
  - `recipe-enable`
  - `recipe-chain`
  - `recipe-pending`
- [x] README 사용 예시 추가

### 진행 상태

진행일: 2026-04-25

- Phase B-2의 작은 단계로 READ 계열 recipe CLI를 추가했다.
  - `recipe-show`
  - `recipe-query`
  - `recipe-pending`
  - `recipe-history`
  - `recipe-chain`
- `CommandDef.expose_cli`와 `tests/test_command_surface_consistency.py`의 MCP-only allowlist를 갱신해 CLI/registry drift 검증을 유지했다.
- 상태 변경 명령인 `recipe-failure`, `recipe-enable`, `recipe-version-up`, `recipe-success`는 이번 단계에서는 MCP/dispatcher 중심으로 유지한다.
- README 4.11에 신규 CLI 사용 예시와 MCP-only 유지 항목을 문서화했다.

추가 진행일: 2026-04-26

- 운영자 품질관리용 WRITE 계열 recipe CLI 명령을 작은 단계로 추가했다.
  - `recipe-success`
  - `recipe-failure`
- `CommandDef.expose_cli`와 MCP-only allowlist를 갱신해 CLI/registry drift 검증을 유지했다.
- `recipe-enable`은 ADMIN 계열, `recipe-version-up`은 steps JSON 입력 UX가 복잡하므로 아직 MCP/dispatcher 중심으로 유지한다.
- README 4.11에 `recipe-success` / `recipe-failure` 예시와 남은 MCP 중심 항목을 갱신했다.

추가 진행일: 2026-04-26

- 운영자 수동 복구용 ADMIN 계열 recipe CLI 명령을 작은 단계로 추가했다.
  - `recipe-enable`
- `recipe-enable`은 기본적으로 실패 카운트를 초기화하며, `--keep-failure-count`로 기존 실패 카운트를 유지할 수 있다.
- `CommandDef.expose_cli`와 MCP-only allowlist를 갱신해 CLI/registry drift 검증을 유지했다.
- README 4.11에 `recipe-enable` 예시와 남은 MCP 중심 항목을 갱신했다.
- `recipe-version-up`은 steps JSON 입력 UX가 복잡하므로 아직 MCP/dispatcher 중심으로 유지한다.

### 완료 기준

- [x] 사람이 CLI만 써도 recipe 품질 관리는 가능하며, 버전 관리는 아직 MCP/dispatcher 중심이다.
- [x] MCP-only 기능이면 문서에 명시되어 있다.

---

## B-3. Auth 진단 명령 추가

### 목적

ReBAC 운영 시 “왜 이 도구가 안 보이는지 / 왜 거부됐는지”를 사용자가 직접 확인할 수 있게 한다.

### 후보 명령

```bash
sap auth-status
sap auth-tools
sap auth-check sap_recipe_query
```

### 진행 상태

진행일: 2026-04-26

- SAP GUI 실행 경로와 분리된 read-only 진단 CLI 그룹과 top-level 별칭을 추가했다.
  - `sap auth status`
  - `sap auth tools`
  - `sap auth check <tool>`
  - `sap auth-status`
  - `sap auth-tools`
  - `sap auth-check <tool>`
- `sap auth status`는 policy 로드 여부, 기본 policy path, active/effective agent, agent relation 요약, visible MCP tool 개수를 출력한다.
- `sap auth tools`는 현재 agent에 실제 노출되는 MCP tool 목록을 출력한다.
- `sap auth check <tool>`은 tool permission, resource, MCP 노출 여부, 권한 판단 결과와 reason을 출력한다.
- 정책이 없으면 `mode: "allow_all"`, 유효한 policy/agent가 있으면 `mode: "enforcing"`으로 표시한다.

### 작업

- [x] 현재 policy 로드 여부 출력
- [x] active_agent 출력
- [x] agent relation 요약 출력
- [x] visible MCP tools 출력
- [x] 특정 tool/resource에 대한 permission 판단 결과 출력

### 완료 기준

- [x] 권한 문제를 로그를 뒤지지 않고 CLI에서 진단할 수 있다.

---

# Phase C: 저장 안정성 공통화

목표: recipe에서 강화한 atomic write 패턴을 다른 저장소에도 일관 적용한다.

## C-1. storage 유틸 모듈 추가

### 문제

저장 방식이 모듈마다 다르다.

- `RecipeStore`: atomic write + fsync 강화됨
- `MacroStore`: 단순 `write_text`
- `SessionStore`: temp + replace는 있으나 fsync 약함
- `OntologyStore`: 별도 확인 및 통일 필요
- transcript/audit: append 기반이라 별도 정책 필요

### 작업

- [x] `sap_cli/storage.py` 추가
- [x] 공통 함수 정의
  - `atomic_write_text(path, text, mode=0o644)`
  - `atomic_write_json(path, data, mode=0o644)`
  - `fsync_dir_best_effort(directory)`
  - `safe_read_json(path)`
- [x] short-write loop 적용
- [x] tmp 파일 cleanup 보장
- [x] Windows/POSIX 동작 차이 주석화

### 완료 기준

- [ ] 저장 안정성 코드가 중복되지 않는다.
- [ ] 새 저장소는 공통 유틸을 사용한다.

---

## C-2. RecipeStore를 storage 유틸로 이관

### 작업

- [x] 현재 `RecipeStore.save()` 동작을 보존하면서 `storage.py` 사용
- [x] 기존 short-write, fsync, mode 0o644 보존
- [x] `tests/test_recipe_dedup.py`와 versioning/quality/cross_tcode 테스트 통과

### 완료 기준

- [x] Recipe 저장 안정성 회귀 없음

---

## C-3. MacroStore 저장 안정성 강화

### 작업

- [x] `MacroStore.save()`를 atomic write로 변경
- [x] mode 0o644 적용
- [x] corrupt write/partial write 관련 테스트 추가

### 완료 기준

- [x] 매크로 저장 중 실패해도 기존 파일이 손상되지 않는다.

---

## C-4. SessionStore 저장 안정성 강화

### 작업

- [x] `SessionStore._save()`를 공통 atomic write로 변경
- [x] directory fsync best effort 적용
- [x] 기존 session 테스트 통과

### 완료 기준

- [x] session 저장 정책이 recipe와 일관된다.

---

## C-5. OntologyStore 저장 안정성 검토

### 작업

- [x] `OntologyStore`의 모든 write path 조사
- [x] transaction/process/field_registry/relationships/document_flow 저장 방식 확인
- [x] atomic write 적용
- [x] path traversal 검증 유지

### 완료 기준

- [x] ontology 데이터 저장 중 실패해도 기존 지식 파일이 손상되지 않는다.

---

# Phase D: Dispatcher 구조 분리

목표: 3000줄 이상의 `dispatcher.py`를 기능별로 분리해 유지보수성을 높인다.

## D-1. dispatcher 분리 설계

### 현재 문제

`sap_cli/dispatcher.py`가 다음 역할을 모두 포함한다.

- command registry
- authorization
- audit
- transcript
- post-action capture
- SAP action dispatch
- macro dispatch
- ontology dispatch
- process dispatch
- recipe dispatch
- session dispatch
- slot dispatch

### 권장 구조

```text
sap_cli/dispatchers/
  __init__.py
  core.py
  sap_actions.py
  macro.py
  ontology.py
  process.py
  recipe.py
  session.py
  slots.py
```

`dispatcher.py`는 하위 호환 import 및 중앙 entrypoint만 담당한다.

### 진행 상태

진행일: 2026-04-26

- 대규모 리팩토링 없이 Phase D-1 설계 산출물 `DESIGN_DISPATCHER.md`를 추가했다.
- 현재 `sap_cli.dispatcher`에서 유지해야 하는 외부 import surface를 명시했다.
- `dispatchers/` 목표 구조, 단계별 분리 순서, command routing 규칙, 금지 사항, 검증 기준을 정리했다.
- MCP `TOOL_DEFINITIONS` 자동 생성은 Phase D 범위에서 무리하게 진행하지 않는 것으로 명시했다.
- D-2 착수 전에는 registry 분리 또는 recipe 분리 중 하나만 선택해 작은 커밋으로 진행한다.
- D-3의 작은 선행 단계로 command registry를 `sap_cli/dispatchers/registry.py`로 분리했다.
- `sap_cli.dispatcher`는 `CommandDef`, `COMMAND_REGISTRY`, `COMMAND_TOOL_MAP`, `MCP_TOOL_TO_COMMAND`를 re-export해 기존 import surface를 유지한다.

### 완료 기준

- [x] 외부 API `dispatch_command()`는 유지된다.
- [x] 테스트는 수정 최소화로 통과한다.

---

## D-2. Recipe dispatch 분리

### 이유

recipe 기능이 가장 빠르게 커지고 있으며, `_dispatch_recipe_*` 함수가 많다.

### 이동 대상

```text
_get_recipe_store
_collect_recipe_hints
_extract_recipe_tcodes
_unlink_recipe_from_ontology
_delete_recipe_with_links
_link_recipe_to_ontology
_dispatch_recipe_save
_dispatch_recipe_search
_dispatch_recipe_show
_dispatch_recipe_list
_dispatch_recipe_delete
_dispatch_recipe_success
_dispatch_recipe_failure
_dispatch_recipe_enable
_dispatch_recipe_version_up
_dispatch_recipe_history
_dispatch_recipe_chain
_dispatch_recipe_query
_dispatch_recipe_pending
```

### 완료 기준

- [x] recipe 관련 테스트 전체 통과
- [x] dispatcher.py 줄 수 감소
- [x] recipe 기능 수정 시 recipe dispatcher만 보면 된다.

### 진행 메모

- D-2의 첫 작은 단계로 `sap_cli/dispatchers/` 패키지와 `recipe.py` recipe command router를 추가했다.
- 대규모 함수 이동은 피하고, 기존 `sap_cli.dispatcher` private import/monkeypatch 호환성을 유지한 채 `_execute_command()`의 recipe if-chain만 feature router로 넘겼다.
- 다음 단계에서 `_dispatch_recipe_*` 구현과 recipe helper를 `sap_cli/dispatchers/recipe.py`로 옮기면 `dispatcher.py` 줄 수 감소 완료 기준을 체크할 수 있다.
- 두 번째 작은 단계로 `recipe_failure`, `recipe_enable`, `recipe_history`, `recipe_chain` 실제 구현을 `sap_cli/dispatchers/recipe.py`로 이동했다.
- `sap_cli.dispatcher._dispatch_recipe_*` private import 표면은 wrapper로 유지하고, wrapper가 현재 module의 `_get_recipe_store`를 넘기므로 기존 monkeypatch 테스트 호환성을 유지한다.
- `dispatcher.py`는 3145줄에서 3078줄로 감소했다.
- recipe 관련 테스트 통과 확인: `108 passed`.
- 세 번째 작은 단계로 `recipe_search`, `recipe_show`, `recipe_list`, `recipe_delete`, `recipe_success` 실제 구현을 `sap_cli/dispatchers/recipe.py`로 이동했다.
- `recipe_delete`는 dispatcher module의 `_delete_recipe_with_links`를 implementation에 주입하므로 기존 helper monkeypatch 호환성을 유지한다.
- `dispatcher.py`는 3078줄에서 2987줄로 추가 감소했다.
- recipe 관련 테스트 통과 확인: `146 passed`.
- 네 번째 작은 단계로 `recipe_query`, `recipe_pending` 실제 구현을 `sap_cli/dispatchers/recipe.py`로 이동했다.
- `recipe_query` wrapper는 dispatcher module의 `_get_recipe_store`를, `recipe_pending` wrapper는 `_get_session_store`를 implementation에 주입하므로 기존 monkeypatch 테스트 호환성을 유지한다.
- `dispatcher.py`는 2987줄에서 2894줄로 추가 감소했다.
- recipe 관련 테스트 통과 확인: `146 passed`.
- 다섯 번째 작은 단계로 `recipe_version_up` 실제 구현을 `sap_cli/dispatchers/recipe.py`로 이동했다.
- `recipe_version_up` wrapper는 dispatcher module의 `_get_recipe_store`, `_unlink_recipe_from_ontology`, `_link_recipe_to_ontology`를 implementation에 주입하므로 기존 helper monkeypatch 호환성을 유지한다.
- `dispatcher.py`는 2894줄에서 2864줄로 추가 감소했다.
- recipe 관련 테스트 통과 확인: `146 passed`.
- 여섯 번째 작은 단계로 `recipe_save` 실제 구현과 recipe helper 계열(`_get_recipe_store`, `_collect_recipe_hints`, `_extract_recipe_tcodes`, `_unlink_recipe_from_ontology`, `_delete_recipe_with_links`, `_link_recipe_to_ontology`) 구현을 `sap_cli/dispatchers/recipe.py`로 이동했다.
- `sap_cli.dispatcher`에는 기존 private import/monkeypatch 호환용 wrapper를 유지하고, wrapper가 현재 module의 helper 이름을 implementation에 주입하므로 기존 테스트의 monkeypatch 동작을 유지한다.
- `dispatcher.py`는 2864줄에서 2529줄로 추가 감소했다.
- recipe 관련 테스트 통과 확인: `146 passed`.
- D-2 완료 기준 3개를 모두 충족했다.

---

## D-3. Registry 분리

### 이동 대상

```text
CommandDef
COMMAND_REGISTRY
COMMAND_TOOL_MAP
MCP_TOOL_TO_COMMAND
```

### 완료 기준

- [x] `sap_cli.dispatcher` import 호환성 유지
- [x] registry / policy / MCP / CLI drift 테스트 통과
- [x] MCP `TOOL_DEFINITIONS` 자동 생성은 진행하지 않음

### 진행 메모

- `CommandDef`, `COMMAND_REGISTRY`, `COMMAND_TOOL_MAP`, `MCP_TOOL_TO_COMMAND`를 `sap_cli/dispatchers/registry.py`로 이동했다.
- `sap_cli/dispatchers/__init__.py`에서도 registry public surface를 re-export한다.
- `sap_cli.dispatcher`는 같은 이름을 import해 기존 CLI, MCP, daemon, 테스트 import 호환성을 유지한다.
- 핵심 drift 테스트 통과 확인: `tests/test_command_surface_consistency.py`.

---

## D-4. Session dispatch 분리

### 이동 대상

```text
_dispatch_session_start
_dispatch_session_end
_dispatch_session_list
_dispatch_session_show
_dispatch_session_candidates
_dispatch_session_approve
_log_session_step
```

### 완료 기준

- [x] session 관련 테스트 전체 통과
- [x] session → recipe suggestion 흐름 회귀 없음

### 진행 메모

- `sap_cli/dispatchers/session.py`를 추가해 session start/end/list/show/candidates/approve 및 session step logging 구현을 이동했다.
- `sap_cli.dispatcher`에는 기존 private import/monkeypatch 호환을 위한 thin wrapper를 유지했다.
- 중앙 `_execute_command`는 `is_session_command()`와 `dispatch_session_command()`를 통해 session 명령군을 라우팅한다.
- `sap_cli/dispatchers/__init__.py`에서도 session command public surface를 re-export한다.
- 검증: `uv run pytest tests/test_recipe_autosuggest.py tests/test_session*.py` → 150 passed.

---

## D-5. Ontology / Process dispatch 분리

### 이동 대상

- ontology query
- ontology ask
- relationships
- dependencies
- document flow
- process list/show/create/delete/start/step/status
- process path
- rebuild
- annotate

### 완료 기준

- [ ] ontology/process 테스트 전체 통과
- [ ] MCP ontology query 동작 회귀 없음

---

# Phase E: Recipe 운영 고도화

목표: recipe를 실제 사내 운영 자산으로 만들기 위한 품질·검증·소유권 기반을 준비한다.

## E-1. Recipe 품질 점수 개선

### 현재

`efficiency_score()`는 다음 요소를 사용한다.

- 단계 수
- 성공 횟수
- context 길이
- 최신성

### 개선 후보

- [ ] 최근 성공률
- [ ] 마지막 성공 시각
- [ ] 마지막 실패 시각
- [ ] 평균 실행 시간
- [ ] 특정 SAP system/client 성공 여부
- [ ] 실패 사유 유형
- [ ] disabled 여부 penalty

### 완료 기준

- [ ] 검색 결과에서 실제로 더 믿을 수 있는 recipe가 상위에 온다.

---

## E-2. Recipe replay 검증 플로우 설계

### 목표

recipe를 바로 실행하기보다 아래 흐름을 지원한다.

```text
precheck → dry-run plan → execute → verify → report
```

### 작업

- [ ] recipe step precondition 모델 검토
- [ ] 현재 화면/tcode가 recipe 시작 조건과 맞는지 확인
- [ ] 실행 전 예상 단계 출력
- [ ] 실행 후 status/modal/screen_summary로 검증
- [ ] 실패 시 `sap_recipe_failure` 자동 호출 가능성 검토

### 완료 기준

- [ ] recipe 실행의 성공/실패가 자동으로 품질 관리에 반영된다.

---

## E-3. Recipe ownership 준비

### Phase 21과 연결

향후 멀티유저 거버넌스에서 recipe에는 다음 필드가 필요하다.

- `created_by`
- `updated_by`
- `approved_by`
- `shared_scope`
- `team`

### 작업

- [ ] schema 확장 전 migration 전략 수립
- [ ] 기존 recipe 역호환 default 결정
- [ ] ownership 없는 recipe의 처리 정책 결정

### 완료 기준

- [ ] Phase 21 사용자 식별 도입 시 recipe schema 충돌이 없다.

---

# Phase F: 운영 도구와 데이터 관리

목표: 사내 도입 후 데이터가 쌓였을 때 운영자가 관리할 수 있게 한다.

## F-1. 데이터 위치 진단 명령 추가

### 후보 명령

```bash
sap data-dir
sap data-status
```

### 출력 대상

- policy
- recipes
- macros
- ontology
- audit
- transcripts
- sessions
- captures

### 완료 기준

- [ ] 사용자가 `~/.sap_cli` 내부 상태를 쉽게 확인할 수 있다.

---

## F-2. 백업/정리 명령 설계

### 후보 명령

```bash
sap data-backup
sap data-clean
```

### 작업

- [ ] recipe/ontology/macro 백업 tar/zip 생성
- [ ] transcript/audit/session 오래된 로그 정리 정책 설계
- [ ] 삭제 전 dry-run 옵션 제공

### 완료 기준

- [ ] 운영 데이터가 누적되어도 관리 가능하다.

---

# Phase G: SAP 실기 검증 체계화

목표: macOS/Linux 테스트로 검증 불가한 Windows + SAP GUI COM 동작을 체계적으로 확인한다.

## G-1. Windows SAP 실기 검증 체크리스트 작성

### 파일 후보

```text
WINDOWS_SAP_VERIFICATION.md
```

### 검증 항목

- [ ] `sap connect`
- [ ] `sap connections`
- [ ] `sap screen`
- [ ] `sap run MM03`
- [ ] `sap fill MATNR=...`
- [ ] `sap get`
- [ ] `sap status`
- [ ] `sap table` 일반 table control
- [ ] `sap table` ALV Grid
- [ ] `sap tree` SPRO/document flow
- [ ] `sap capture`
- [ ] macro record/play
- [ ] session start/end
- [ ] recipe suggestion
- [ ] MCP 연결
- [ ] daemon 모드
- [ ] multi-session slot

### 완료 기준

- [ ] 사내 PC에서 그대로 따라할 수 있는 검증 문서가 있다.
- [ ] 각 실패 케이스별 확인 포인트가 있다.

---

# Phase H: 장기 확장 준비

목표: core 안정화 후 ADT/거버넌스 확장에 착수할 수 있게 준비한다.

## H-1. ADT 통합 전제 조건 재확인

### 전제

ADT 통합은 recipe 50개 이상 축적 후 착수 권장.

### 이유

현재 우선순위는 GUI 자동화 core 안정화와 recipe 축적이다.  
ADT는 강력하지만 인증/권한/시스템 설정 의존성이 추가된다.

### 준비 작업

- [ ] 사내 SAP에서 `/sap/bc/adt` SICF 활성화 여부 확인
- [ ] ADT 읽기 권한 계정 확인
- [ ] Basic Auth/SSO 가능 방식 확인
- [ ] 외부 라이브러리 `requests` 도입 여부 검토

---

## H-2. 멀티유저 거버넌스 Phase 21 착수 조건

### 착수 조건

- [ ] recipe가 실제 업무에서 1개월 이상 축적됨
- [ ] 최소 30~50개 recipe 확보
- [ ] recipe owner/share 요구가 실제로 발생
- [ ] audit/transcript 저장 안정성 정리 완료

### Phase 21 핵심

- user_id 식별
- recipe ownership
- session user_id
- audit user_id

---

# 추천 실행 순서

## Sprint 1: 안정화

1. A-1 ALV 테스트 실패 해결
2. A-2 `TOOL_PERMISSION_MAP` 누락 보강
3. A-3 consistency 테스트 추가

완료 기준:

- 전체 테스트 통과
- registry/policy/MCP drift 자동 감지

---

## Sprint 2: 명령 표면 정리

1. B-1 registry 단일 진실 공급원화
2. B-2 recipe CLI 확장 여부 결정 및 구현
3. B-3 auth 진단 명령 추가

완료 기준:

- CLI/MCP/권한 표면이 설명 가능해짐

---

## Sprint 3: 저장 안정성 통일

1. C-1 storage 유틸 추가
2. C-2 RecipeStore 이관
3. C-3 MacroStore 강화
4. C-4 SessionStore 강화
5. C-5 OntologyStore 검토

완료 기준:

- 주요 JSON 자산 저장 방식이 통일됨

---

## Sprint 4: 구조 분리

1. D-1 dispatcher 분리 설계
2. D-2 recipe dispatch 분리
3. D-3 registry 분리
4. D-4 session dispatch 분리
5. D-5 ontology/process dispatch 분리

완료 기준:

- dispatcher.py가 중앙 라우터 역할로 축소됨

---

## Sprint 5: 운영 고도화

1. E-1 recipe 품질 점수 개선
2. E-2 recipe replay 검증 설계
3. F-1 data status 명령
4. G-1 Windows SAP 검증 문서

완료 기준:

- 사내 운영/검증/데이터 관리가 쉬워짐

---

# 가장 먼저 할 3개 작업

```text
1. ALV 테스트 실패 해결
2. TOOL_PERMISSION_MAP 누락 16개 보강
3. registry/policy/MCP consistency 테스트 추가
```

이 3개를 먼저 끝내면 현재 코드베이스의 안정성과 운영 신뢰도가 즉시 올라간다.
