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
   client인지" 먼저 확인받는다. **client 번호로 운영 여부를 판단하지 않는다** —
   `100`이라고 운영이 아니고, 운영 여부는 client 카테고리(landscape마다 다름)로
   정해진다. 첫 tick 이후 `map-job-status`의 `observed_scope`(실제 접속
   system/client/user)를 사용자에게 보여주고 사실과 맞는지 확인받는다.
4. **버튼·메뉴 이름을 추측해 누르지 않는다.** 조회성 분기는 `guided`에서 사용자가
   고르거나 `allowlisted` 규칙으로 명시한 것만 수집한다.
5. **`passive` 한 번으로 끝내지 않는다.** 계획에 쓸 수 있는 지도는 최소한
   `guided`로 대상 화면까지 연결되어 있어야 한다.

## PowerShell 인자 전달 — JSON은 `@파일`로 넘긴다

**중요.** PowerShell에서 큰따옴표가 든 JSON을 명령줄에 직접 박으면 셸의 네이티브
인자 인용 규칙에 걸려 깨진다(버전마다 이스케이프가 달라 신뢰할 수 없다). LLM이
CLI를 호출할 때는 JSON을 **파일에 써서 `@경로`로 넘긴다.** 파일은 셸이 아니라
파일 쓰기 도구로 만들어 따옴표 문제를 원천 차단한다.

`--budget-json`, `--allowed-branches-json`, `--values-json`, `--scope-json`,
`--plan-ref-json`, resume의 `answers`, approve의 `step_ids` 등 모든 JSON 인자가
`@경로`(파일) 와 `@-`(stdin) 를 받는다.

```powershell
# JSON을 파일로 저장(파일 도구 사용) 후 경로만 넘긴다 — 어느 셸에서도 안전
Set-Content -Path answer.json -Value '{"guided-1a2b":{"candidate_id":"c1"}}'
sap map-traverse-resume <job-id> '@answer.json'
```

인라인으로 꼭 써야 하면 값 없이 짧은 것만(예: `'{}'`)에 한한다. 답변·budget처럼
중첩 따옴표가 있는 JSON은 반드시 `@파일`을 쓴다. 아래 예시의
`'{"...":...}'` 표기는 개념 설명이며, 실제 실행은 `@파일`로 바꾼다.

## 운영 루프 (이 순서를 벗어나지 않는다)

지도 수집은 **비동기 job**이다. `map-traverse-start`는 즉시 job을 하나 만들고
돌려줄 뿐, 수집은 daemon이 백그라운드에서 진행한다. 에이전트는 아래 루프를 돌며
**관찰하고(폴링) → 막히면 사용자에게 묻고 → 답을 넣어 재개**한다. 화면을 직접
조작하지 않는다.

```
1. START   sap map-traverse-start ...
           → 응답 {"job": {"job_id": "...", "status": "..."}} 에서 job_id 를 꺼낸다
2. POLL    sap map-job-status <job_id>          # status 가 바뀔 때까지 1~2초 간격 반복
3. status 로 분기:
     running / queued        → 아직 진행 중. 계속 POLL (2로)
     needs_user_input        → job.pending_questions 를 읽어 사용자에게 묻고,
                                받은 답으로 RESUME → 다시 POLL (2로)
     paused                  → budget 소진. --budget-json 으로 RESUME → POLL
     completed               → 끝. map-report 로 검증
     failed / interrupted    → 아래 "job 상태와 대응" 표
```

핵심 규칙:

- **job_id 는 START 응답에서 나온다.** 지어내지 않는다. 잃어버리면 인자 없는
  `sap map-job-status` 로 본인 job 목록을 다시 찾는다.
- **폴링은 job을 진행시키지 않는다** — 그냥 관찰이다. daemon이 알아서 다음
  전이를 진행하므로, `running` 이면 잠깐 기다렸다 다시 POLL 한다.
- **guided 는 화면마다 질문을 새로 낸다.** 한 번 RESUME 했다고 끝이 아니다.
  `status` 가 `completed` 가 될 때까지 (needs_user_input → resume → poll) 를
  반복한다.
- **답은 반드시 살아있는 job의 `pending_questions` 에서 읽어온다.**
  `resume_token`·`candidate_id` 를 기억이나 추측으로 만들지 않는다. 매 RESUME
  전에 최신 `map-job-status` 로 현재 질문을 다시 확인한다.

### 언제 사용자에게 멈춰 묻는가

`needs_user_input` 의 `reason` 이 판단 기준이다.

| reason | 누가 고르나 | 행동 |
|---|---|---|
| `guided_candidate_selection` | **사용자** | choices의 `label`/`action`을 사람이 읽을 형태로 보여주고 **하나** 고르게 한다. 절대 임의 선택 금지 (절대 규칙 4) |
| `runtime_input_required` | **사용자** | 필수 입력 필드의 테스트 값을 묻는다. 답변은 메모리에서만 SAP에 입력하고 지도/job 파일에는 저장하지 않는다 |
| `map_coverage_incomplete` | **사용자** | 어떤 화면이 빠졌는지 보여주고, 직접 이동 후 보강할지 확인 |
| `semantic_review` | 에이전트 | 구조적 의미 연결 후보다. 값이 없으므로 에이전트가 `accepted_candidate_ids`로 확정 가능. 애매하면 비우고 넘어간다 |

사용자에게 물을 때는 **멈추고 답을 기다린다.** 답을 받기 전에 다음 RESUME 를
보내지 않는다.

### 언제 "지도가 충분한가" (끝 판단)

100% 커버리지를 쫓지 않는다. **이번에 세울 계획이 지나갈 화면·필드가 지도에
들어왔고 도달 가능하면** 그 지도는 충분하다.

- 1차 종료 신호: `map-report` 의 `coverage_gate.complete == true`
- 그게 아니어도, 목표 화면이 `map-path` 로 시작 상태에서 도달되면 계획을 세울 수
  있다. 나머지 미수집 분기는 나중에 `map-augment-start` 로 채운다.
- 반대로 `coverage_gate.complete` 가 `true` 여도, 사용자가 원하는 목표 화면이
  아직 없으면 끝이 아니다. guided 로 그 화면까지 확장한다.

즉 끝은 **coverage 숫자가 아니라 "그 계획에 필요한 화면이 도달 가능한가"** 로
판단한다. 무한 확장하지 말고, 목표에 닿으면 멈춘다.

## 0단계 — 프로젝트 폴더 + 가상환경, daemon과 SAP 세션 준비

Windows에서는 이 문서의 모든 명령을 PowerShell로 실행한다. bash/source/Unix 경로를
사용하지 말고 `.venv\Scripts\Activate.ps1` 또는 `.venv\Scripts\sap.exe`를 사용한다.

`sap` CLI는 프로젝트의 가상환경에 editable 설치돼 있다. **먼저 sap_cli 프로젝트
폴더로 이동해 `.venv`를 활성화한 뒤** 모든 `sap` 명령을 실행한다. 활성화하지
않으면 `sap`이 PATH에 없거나 다른 설치본을 쓸 수 있다.

```powershell
cd <sap_cli 프로젝트 폴더>          # pyproject.toml과 sap_cli/ 가 있는 곳
.venv\Scripts\Activate.ps1          # Windows (macOS/Linux: source .venv/bin/activate)
sap --help                          # 활성화 확인
sap daemon status
sap daemon start
sap connect
sap session
```

활성화가 어려우면 venv 실행파일을 직접 부른다: `.venv\Scripts\sap ...` /
`.venv/bin/sap ...`. 테스트도 같은 venv에서: `.venv\Scripts\pytest tests\ -q`.

**프로젝트 폴더 찾는 법** (경로는 사용자마다 다르므로 박아두지 않는다): ①
사용자에게 물어본다(가장 확실). ② 이미 설치돼 있으면 `pip show sap-cli`의
"Editable project location" 또는
`python -c "import sap_cli, pathlib; print(pathlib.Path(sap_cli.__file__).resolve().parent.parent)"`로
역추적한다. ③ 그래도 모르면 `name = "sap-cli"`를 담은 `pyproject.toml` 폴더를
찾는다. venv는 보통 그 폴더의 `.venv`(다르면 사용자에게 확인).

daemon 기동이 실패하면 `sap daemon doctor`로 원인(인터프리터/venv, import 실패,
`~/.sap_cli/daemon.log` tail)을 먼저 확인한다.

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

### guided 한 판 전체 예시 (이대로 따라 돈다)

응답 JSON은 예시다. 실제 값은 매번 `map-job-status`로 다시 읽는다.

```powershell
# START — job_id 를 꺼낸다
sap map-traverse-start MM03 --map-id mm03-readonly --mode guided --test-client-confirmed
# → {"job": {"job_id": "map-9f3a...", "status": "running", "pending_questions": []}}

# POLL — status 가 바뀔 때까지 반복
sap map-job-status map-9f3a...
# → {"job": {"status": "running", ...}}          # 아직 → 잠깐 기다렸다 다시 POLL
sap map-job-status map-9f3a...
# → {"job": {"status": "needs_user_input",
#            "pending_questions": [
#              {"resume_token": "guided-1a2b...",
#               "reason": "guided_candidate_selection",
#               "required_fields": ["candidate_id"],
#               "choices": [
#                 {"candidate_id": "c1", "action": "select_tab",
#                  "label": "기본 데이터", "reversible": true},
#                 {"candidate_id": "c2", "action": "select_tab",
#                  "label": "구매", "reversible": true}]}]}}
```

여기서 **멈추고 사용자에게 묻는다**: "기본 데이터 / 구매 중 어느 탭을 지도에
넣을까요?" 사용자가 "기본 데이터"라고 하면 그 `candidate_id`(`c1`)로 RESUME 한다.

```powershell
# RESUME — resume_token 을 키로, 고른 candidate_id 를 넣는다
sap map-traverse-resume map-9f3a... '{"guided-1a2b...":{"candidate_id":"c1"}}'
# → {"job": {"status": "running", ...}}

# 다시 POLL — guided 는 새 화면에서 다음 질문을 낸다
sap map-job-status map-9f3a...
# → needs_user_input (다음 후보) → 다시 사용자에게 묻고 RESUME …
#    이 (poll → 묻기 → resume) 를 status 가 completed 가 될 때까지 반복한다.

# completed 되면 검증
sap map-report mm03-readonly MM03
```

핵심: RESUME 한 번으로 끝나지 않는다. `completed` 전까지 루프를 계속 돈다. 목표
화면(예: 조회하려는 데이터 탭)이 지도에 들어오면 거기서 멈춰도 된다 — "끝 판단"
참고.

## 2-B단계 — 사용자에게 하나씩 물으며 기록 (map-record)

daemon 순회(guided)에 맡기지 않고 **LLM/사용자가 직접 `sap press`·`sap act`로
탭·버튼을 눌러 순회**하고 싶을 때 쓴다. 기록 세션을 켜두면 그 클릭들이 자동으로
지도에 저장된다.

```powershell
sap map-record-start mm03-readonly MM03     # 현재 화면을 첫 state로 기록
# 이후 평소처럼 직접 이동:
sap press "wnd[0]/usr/tabs/tabpSANM"        # 탭/버튼 클릭 → 도달 화면+전이 자동 저장
sap act  "wnd[0]/usr/..." press
# ...탭을 하나씩 눌러 순회...
sap map-record-status                        # 기록 상태(state/transition 수) 확인
sap map-record-finish                        # 종료 + 요약
```

시작·상태 응답의 `safe_actions`를 설명하고 “다음에 무엇을 할까요?”라고 묻는다.
사용자 응답 전에는 SAP를 조작하지 않고, 답을 실행한 뒤 다시 한 단계씩 묻는다.

사용자가 Windows SAP GUI에서 직접 클릭하는 모습을 기록하려면:

```powershell
sap map-manual-record-start mm03-manual MM03
# SAP GUI에서 직접 조작
sap map-manual-record-poll
sap map-record-finish
```

이 모드는 persistent daemon과 SAP GUI Scripting 이벤트가 필요하며 업무 입력값은
저장하지 않는다.

동작 규칙:

- `record-start` 후 **navigating 액션(`press`/`act`/`vkey`/`run`)마다** daemon이
  도달 화면을 스캔해 새 state로 저장한다. 화면이 안 바뀌면 아무것도 안 남긴다.
- **재실행 가능한 전이**는 안전한 구조 컨트롤 클릭에서만 만든다: GuiTab→select_tab,
  GuiButton→press, GuiMenu→select_menu. `vkey`/`run`으로 바뀐 화면은 state만
  기록하고 전이는 남기지 않는다.
- **저장·전기·삭제·승인·전송·인쇄** 등 위험 표식 컨트롤 클릭은 전이로 기록하지
  않는다(화면은 값 없이 구조만 남을 수 있음).
- 지도 TCode를 벗어난 화면(다른 transaction)은 흡수하지 않는다.
- **daemon 전용**이다. daemon이 없으면 세션 상태가 유지되지 않아 기록되지 않는다.
- 잘못 기록됐으면 `sap map-delete`로 통째로 지우고 다시 시작한다.

guided(2단계)와 record(2-B)는 목적이 같다(지도 채우기). daemon이 후보를 못
찾거나 LLM이 스스로 순회하는 편이 나을 때 record를 쓴다.

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

### 지도 삭제

잘못 만든 지도나 폐기할 지도는 모든 버전째 지운다. **되돌릴 수 없다.**

```powershell
sap map-delete mm03-readonly MM03            # --yes 없이는 삭제 대상만 미리 본다
sap map-delete mm03-readonly MM03 --yes      # 실제 삭제
```

`--yes` 없이 부르면 `deleted:false`, `confirmation_required:true`와 삭제 대상
(`target`)만 돌려주고 지우지 않는다. 반드시 대상을 사용자에게 보여주고 확인받은
뒤 `--yes`(MCP는 `confirm:true`)로 실행한다. scope가 모호하면(같은 map_id가 여러
system/client) `--system/--client/--language`로 정확히 지정한다. 지도는 구조
경로만 담으므로(업무값 없음) 지운 뒤 다시 스캔해 재생성할 수 있다.

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

job 요약에는 `test_client_confirmed`(호출자가 넣은 확인 플래그)와
`observed_scope`(첫 tick에서 읽은 실제 접속 system/client/user/language)가 함께
들어 있다. 이 둘을 대조해 "테스트 client라고 확인했는데 실제로도 맞는지"를
사용자에게 검증받는다. 데몬은 client 번호로 운영을 자동 판정하지 않으므로,
사실 확인은 사람이 한다.

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
