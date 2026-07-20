---
name: sap-map-build
description: >-
  SAP UI 지도(화면 그래프)를 `sap` CLI만으로 제작·보강·점검하는 스킬. MCP 없이
  map-traverse-start / map-traverse-resume / map-augment-start / map-report /
  map-view / map-search / map-path를 사용한다. "MM03 지도 만들어줘",
  "화면 지도 수집", "지도에 탭 추가", "map-traverse가 needs_user_input으로 멈췄어",
  "지도 커버리지 확인", "UI map 보강" 같은 요청에 사용한다. 계획 작성은
  sap-plan-author, 실행은 sap-plan-run 스킬이 담당한다.
---

# SAP UI 지도 제작 (CLI 전용)

TCode 화면 구조와 화면 사이 이동 경로를 값 없이 수집해 버전이 있는 UI 지도로
저장한다. 이 스킬은 **MCP 도구를 쓰지 않는다.** 모든 단계는 `sap` CLI 명령을
실제 셸에서 실행한다.

지도는 **업무값을 저장하지 않는다.** 화면 상태, 구조적 요소(logical id / SAP id /
type), 확인된 전이만 남는다. 자재번호·고객번호·금액 같은 값을 지도에 넣으려 하지
않는다.

## 절대 규칙

1. **의사코드 금지.** `sap map-traverse-start(...)` 같은 표기 대신 실제 명령을
   실행한다.
2. **저장·변경 동작은 어떤 모드에서도 수집 대상이 아니다.** 런타임이
   `commit`/`destructive`/`external` side effect를 차단하며, 라벨·ID에 다음
   표현이 있으면 후보에서 제외한다.
   - 영문: `save`, `post`, `delete`, `release`, `send`, `print`
   - 한글: `저장`, `전기`, `삭제`, `릴리스`, `릴리즈`, `승인`, `전송`, `발송`,
     `인쇄`, `출력`
3. **`passive` 외 모든 모드는 테스트 client에서만.** `--test-client-confirmed`
   없이는 실행 자체가 거부된다. 사용자에게 "지금 접속한 client가 테스트
   client인지" 먼저 확인받는다.
4. **버튼·메뉴 이름을 추측해 누르지 않는다.** 조회성 분기는 `guided`에서 사용자가
   고르거나 `allowlisted` 규칙으로 명시한 것만 수집한다.
5. **`passive` 한 번으로 끝내지 않는다.** 계획에 쓸 수 있는 지도는 최소한
   `guided`로 대상 화면까지 연결되어 있어야 한다.

## 0단계 — daemon과 SAP 세션 준비

```powershell
sap daemon status
sap daemon start
sap connect
sap session
```

`map-traverse-start`, `map-traverse-resume`, `map-augment-start`,
`map-job-cancel`은 **persistent daemon 전용**이다. daemon이 없으면 direct
fallback 없이 실패한다. 반면 `map-list`, `map-search`, `map-path`, `map-report`,
`map-view`, `map-job-status`, `map-scan`은 daemon 없이도 동작한다.

## 1단계 — 기준 화면 수집 (passive)

대상 TCode를 SAP GUI에서 연 상태로 시작한다. `--map-id`는 같은 업무 화면군에서
계속 재사용할 이름이다.

```powershell
sap map-traverse-start MM03 --map-id mm03-readonly --mode passive
sap map-job-status <job-id>
sap map-report mm03-readonly MM03
```

`passive`는 현재 화면 한 장만 스캔하고 즉시 `stop_reason=passive_complete`로
끝난다. `--test-client-confirmed`가 필요 없는 유일한 모드다.

## 2단계 — 사용자와 함께 확장 (guided)

```powershell
sap map-traverse-start MM03 --map-id mm03-readonly --mode guided --test-client-confirmed
sap map-job-status <job-id>
```

job이 `needs_user_input`으로 멈추면 `pending_questions[]`를 읽는다. 각 항목은
`resume_token`, `question`, `reason`, `required_fields`, `choices`, `risk`,
`scope`를 가진다. **`reason`에 따라 답변 JSON 모양이 다르다.**

### 답변 형식 (reason별)

| reason | 필수 키 | 답변 예시 |
|---|---|---|
| `guided_candidate_selection` | `candidate_id` | `{"guided-…":{"candidate_id":"<choices의 candidate_id>"}}` |
| `semantic_review` | `accepted_candidate_ids` | `{"semantic-…":{"accepted_candidate_ids":["<candidate_id>"]}}` |
| `user_requested_missing_screen` | `continue` (+ 대개 `transition`) | `{"augment-…":{"continue":true,"transition":{"action":"press","target_logical_id":"<logical id>"}}}` |

툴바 버튼이나 컨텍스트 메뉴 항목으로 이동했다면 `item_id`가 필요하다.

```json
{"augment-…": {"continue": true, "transition": {
  "action": "press_toolbar_button",
  "target_logical_id": "<collection component logical id>",
  "item_id": "<toolbar_actions의 항목 id>"
}}}
```

```powershell
sap map-traverse-resume <job-id> '{"<resume-token>":{"candidate_id":"<candidate_id>"}}'
```

규칙:

- `answers`는 **resume_token을 키로 하는 객체**다. 토큰이 틀리면
  `unknown resume token` 오류가 난다.
- `candidate_id`는 반드시 그 질문의 `choices`에 있는 값이어야 한다. 지어내지
  않는다.
- `transition` 객체는 키 구성이 정확히 맞아야 한다. 하나라도 더하거나 빠지면
  거부된다. 대상 컨트롤 종류에 따라 두 가지다.

  | 분기 종류 | 키 | `action` | 대상 타입 |
  |---|---|---|---|
  | 일반 컨트롤 | `action`, `target_logical_id` | `press` / `select_menu` / `select_tab` | GuiButton / GuiMenu / GuiTab |
  | 컬렉션 항목 | `action`, `target_logical_id`, `item_id` | `press_toolbar_button` / `select_context_menu_item` | GuiToolbar / GuiShell / GuiGridView / GuiTableControl |

  컬렉션 분기의 `item_id`는 해당 collection의 `toolbar_actions`
  (`press_toolbar_button`) 또는 `context_menu_actions`
  (`select_context_menu_item`)에 실제로 있고, `enabled`/`visible`이 `false`가
  아닌 항목이어야 한다. 지어내면 거부된다.
- 답변 **값은 디스크에 저장되지 않는다.** 어떤 필드에 답했는지만
  `resume_provenance`로 남는다.

`semantic_review`는 외부 LLM API를 부르지 않는다. 로컬 온톨로지와 값 없는 UI
구조를 비교해 만든 후보 중 `accepted_candidate_ids`로 고른 것만 확정된다. 후보
밖의 자유 문장은 지도에 기록할 수 없다.

### guided에서 사용자에게 물을 때

`choices[i]`는 `candidate_id`, `action`, `target_logical_id`, `label`,
`side_effect`, `reversible`를 담고 있다. 사용자에게는 `label`과 `action`을
사람이 읽을 형태로 보여 주고, 업무적으로 맞는 경로 하나를 고르게 한다. 여러 개를
한꺼번에 고르게 하지 않는다 — guided는 한 번에 하나만 실행한다.

## 3단계 — 누락 화면 보강 (map-augment-start)

지도를 다 만든 뒤 빠진 탭·팝업이 보이면 전체를 다시 수집하지 않는다. 사용자가
SAP GUI에서 그 화면으로 **직접 이동한 뒤** 보강한다.

```powershell
sap map-augment-start mm03-readonly MM03 <from-state-id> "분류 탭의 조회 화면"
sap map-job-status <job-id>
sap map-traverse-resume <job-id> '{"<resume-token>":{"continue":true,"transition":{"action":"press","target_logical_id":"<logical id>"}}}'
sap map-report mm03-readonly MM03
```

출발 화면에 버튼·메뉴·탭처럼 재실행 가능한 선택지가 있으면 `transition`은
**필수**다. `continue`만 보내면 사람이 이동한 사실만 남고 계획이 재사용할 경로가
없어지므로 job이 답변을 받아들이지 않는다.

`map-augment-start`는 SAP를 전혀 건드리지 않는다. 질문이 있는 job만 만든다.

## 4단계 — 자동 확장 (reversible / allowlisted)

`guided`로 충분히 확인한 뒤에만 자동 모드를 쓴다. 항상 budget을 함께 건다.

```powershell
sap map-traverse-start MM03 --map-id mm03-readonly --mode reversible --test-client-confirmed `
  --budget-json '{"max_states":100,"max_depth":5,"max_seconds":300}'
```

### 모드 비교

| 모드 | 동작 | test_client_confirmed |
|---|---|---|
| `passive` | 현재 화면 1회 스캔 후 종료. read_only만 허용 | 불필요 |
| `guided` | 안전 후보를 목록으로 제시하고 사용자가 고른 하나만 실행 | 필수 |
| `reversible` | 되돌릴 수 있는 후보를 자동 확장. tab/scroll 중복 제거 포함 | 필수 |
| `allowlisted` | `--allowed-actions` + 분기별 명시 규칙만 실행 | 필수 |

`--allowed-branches-json`은 `allowlisted`에서만 쓸 수 있다. 다른 모드에 주면
`allowed_branches require allowlisted mode`로 거부된다.

### budget 키

| 키 | 기본값 |
|---|---|
| `max_depth` | 8 |
| `max_states` | 250 |
| `max_transitions` | 1000 |
| `max_nodes_per_tree` | 1000 |
| `max_pages_per_collection` | 100 |
| `max_seconds` | 900.0 |
| `max_rescans_per_checkpoint` | 1 (0 또는 1) |

budget이 소진되면 job은 `paused`가 되고 `stop_reason`이 소진된 키 이름
(`max_states` 등)으로 남는다. 이때는 **답변이 아니라 budget으로 재개**한다.

```powershell
sap map-traverse-resume <job-id> '{}' --budget-json '{"max_states":200,"max_seconds":600}'
```

`paused` job에 `answers`를 보내면 거부된다. 반대로 `needs_user_input` job에
budget만 보내도 거부된다.

### allowlisted 분기 규칙

```json
[{"action":"press","source_state_id":"<state id>","target_logical_id":"<logical id>",
  "target_sap_id":"<SAP id>","component_type":"GuiButton","restore_vkey":3}]
```

`action`은 `press` / `select_menu` / `press_toolbar_button` /
`select_context_menu_item` 중 하나, `restore_vkey`는 되돌아가는 VKey(3=F3,
12=F12)다. `source_state_id` 또는 `source_fingerprint` 중 하나는 있어야 한다.
매칭 시 `action`, `target_logical_id`, `target_sap_id`, `item_id`,
`restore_vkey`가 모두 일치해야 후보가 통과한다.

`component_type`은 `GuiButton`, `GuiMenu`, `GuiToolbar`, `GuiShell`,
`GuiGridView`, `GuiTableControl` 중 하나다. 컬렉션 분기
(`press_toolbar_button`, `select_context_menu_item`)는 `GuiToolbar`,
`GuiShell`, `GuiGridView`, `GuiTableControl`만 허용하며 `item_id`가 필수다.

## 5단계 — 점검

```powershell
sap map-list --tcode MM03
sap map-view mm03-readonly MM03
sap map-report mm03-readonly MM03
sap map-search mm03-readonly MM03 "기본 데이터" --kinds state,component --limit 20
sap map-path mm03-readonly MM03 <from-state-id> <target> --target-type component
```

`map-view`는 `outline`(화면별 다음 경로)과 `mermaid`(그래프 문자열)를 준다.
사용자에게 지도를 설명할 때는 `mermaid`를 그대로 렌더링해 보여 준다.

`map-report`의 `coverage_gate.complete`가 `false`면 계획 컴파일이
`map_coverage_incomplete`로 막힐 수 있다. 아래 항목을 우선 확인한다.

| 지표 | 뜻 | 대응 |
|---|---|---|
| `pending_actionable_branches` | 아직 확인 안 한 조회성 분기 | guided로 확장 |
| `partial_collections` | 테이블/트리 일부만 수집 | budget을 늘려 재개 |
| `has_unreachable` | 도달 경로가 없는 화면 | 직접 이동 후 map-augment-start |
| `has_unknown_shells` | 타입 미상 컨트롤 | 해당 화면 재수집 |
| `has_missing_restore_evidence` | 복귀 경로 미확인 | allowlisted 규칙에 `restore_vkey` 명시 |

## job 상태와 대응

| status | 뜻 | 다음 행동 |
|---|---|---|
| `queued` / `running` | 진행 중 | `map-job-status`로 폴링 |
| `needs_user_input` | 질문 대기 | `pending_questions` 읽고 `map-traverse-resume`에 answers |
| `paused` | budget 소진 | `--budget-json`으로 재개 |
| `interrupted` | daemon이 중간에 죽음 | `sap job-reconcile map:<job-id> verified_not_applied`로 재큐 또는 `map-job-cancel` |
| `completed` | 정상 종료 | `map-report`로 검증 |
| `failed` | 오류 종료 | `error`, `stop_reason` 확인 후 재시도 |
| `cancelled` | 취소됨 | — |

```powershell
sap map-job-cancel <job-id>
sap job-status
```

`map-job-status`는 인자 없이 부르면 본인 agent와 현재 slot의 job 목록을 준다.

## 저장 위치

| 데이터 | 경로 | 환경변수 |
|---|---|---|
| UI 지도 | `~/.sap_cli/ui_maps` | `SAP_CLI_UI_MAP_DIR` |
| 지도 job | `~/.sap_cli/ui_map_jobs` | `SAP_CLI_UI_MAP_JOB_DIR` |

지도는 SAP 시스템 · client · 언어 · TCode 조합마다 분리된다. scope 값은 해시로
디렉토리명이 되므로 TCode가 그대로 경로에 노출되지 않는다. 다른 시스템에서 만든
지도는 재사용되지 않는다.

## 사용자에게 보고하는 방식

지도 작업이 끝나면 다음을 정리해 전달한다.

- `map_id`, `map_version`, scope(system/client/language/tcode)
- `states` / `transitions` 개수와 `coverage_gate.complete`
- 아직 남은 미해결 화면과 그 원인
- 이 지도로 지금 세울 수 있는 계획의 범위

그 다음 계획 작성은 **sap-plan-author** 스킬로 넘긴다.
