# 레시피 문법 및 사용 규약

레시피를 검색·저장·재생할 때 이 문서를 따른다. 레시피와 매크로 문법을 섞지 않는다.

## 1. 가장 중요한 문법 구분

레시피 step은 반드시 다음 형태다.

```json
{
  "tool": "sap_run",
  "args": {"tcode": "MM03"},
  "hint": "MM03 실행"
}
```

다음은 매크로 step 문법이므로 레시피에 사용하지 않는다.

```json
{
  "action": "RunTransaction",
  "target_id": "MM03",
  "value": ""
}
```

레시피에서 허용되는 핵심 키:

- `tool`: 실제 MCP 도구 이름. `sap_` 접두사를 포함한다.
- `args`: 그 도구에 전달할 JSON 객체다. 문자열이나 배열로 전달하지 않는다.
- `hint`: 선택적인 단계 설명이다.
- `precondition`: 선택적인 시작 화면 조건이다.
- `step_kind`, `followable`, `requires_sap`: 세션에서 생성된 복합 단계에 사용한다.

`tool_name`, `action`, `arguments`, `params`, `target_id`를 `tool`/`args` 대신
사용하지 않는다.

## 2. 작업별 올바른 도구 선택

### 기존 레시피 검색

자연어 SAP 작업을 받으면 직접 조작하기 전에 먼저 검색한다.

```json
{
  "tcode": "MM03",
  "keywords": "자재 기본 데이터 조회"
}
```

MCP: `sap_recipe_query`

- 결과가 1개이고 `execution_plan`이 있으면 내용을 검토한다.
- 결과가 여러 개면 `context`와 `match_reasons`를 비교한다.
- 선택한 ID를 `sap_recipe_show`의 `recipe_id`로 전달한다.
- 검색 결과 객체 전체를 `recipe_id`에 전달하지 않는다.

### 실행 전 검토

```json
{
  "recipe_id": "mm03-basic-data-read",
  "current_tcode": "MM03",
  "current_screen": "0060"
}
```

MCP: `sap_recipe_replay_plan`

이 도구는 SAP를 조작하지 않는다. `status`, `precheck`, 예상 step과 검증 기준을
확인한다. `blocked`이면 실행하지 않는다.

### 실제 실행

```json
{
  "recipe_id": "mm03-basic-data-read",
  "current_tcode": "MM03",
  "current_screen": "0060",
  "observation_scope": "final"
}
```

MCP: `sap_recipe_replay_execute`

- `recipe_id`는 문자열 ID만 전달한다.
- 레시피의 `steps`를 `sap_recipe_replay_execute` 인자로 다시 전달하지 않는다.
- `success_criteria`는 객체다. JSON 문자열로 전달하지 않는다.
- 이 도구는 성공·실패 통계를 기본적으로 자동 기록한다. 자동 실행 뒤
  `sap_recipe_success`를 중복 호출하지 않는다.
- 레시피 step을 AI가 하나씩 수동 실행한 경우에만 완료 후
  `sap_recipe_success` 또는 `sap_recipe_failure`를 호출한다.

### 새 레시피 저장

MCP `sap_recipe_save`의 최소 구조:

```json
{
  "recipe_id": "mm03-basic-data-read",
  "description": "MM03 자재 기본 데이터 조회",
  "context": "MM03에서 자재번호를 입력하고 기본 데이터 화면을 조회한다. 조회 전용.",
  "keywords": ["MM03", "자재", "기본 데이터", "조회"],
  "steps": [
    {
      "tool": "sap_run",
      "args": {"tcode": "MM03"},
      "hint": "MM03 실행"
    },
    {
      "tool": "sap_set_field",
      "args": {
        "element_id": "wnd[0]/usr/ctxtRMMG1-MATNR",
        "value": "${material}"
      },
      "hint": "자재번호 입력"
    },
    {
      "tool": "sap_send_vkey",
      "args": {"vkey": 0},
      "hint": "Enter"
    }
  ],
  "replay_success_criteria": {
    "expected_tcode": "MM03",
    "require_no_modal": true,
    "allowed_status_types": ["S", "W"]
  }
}
```

저장 규칙:

- 실제 성공한 최종 경로만 저장한다.
- 실패한 클릭, 되돌아가기, 중복 입력은 제거한다.
- 업무값을 하드코딩하지 말고 `${variable}` 형태를 사용한다.
- `context`에 목적, 전제조건, 결과 화면, 조회/변경 여부를 적는다.
- `recipe_id`에는 영문·숫자·`_`·`-`만 사용한다.
- `steps`는 배열이고 각 항목의 `args`는 객체여야 한다.
- `replay_success_criteria`는 최종 상태 검증 기준이지 실행 step이 아니다.

`duplicate_candidates`가 반환되면 자동으로 덮어쓰지 않는다. 후보와 추천 이유를
사용자에게 설명하고 답변을 받은 다음에만:

- 유지: `on_duplicate="keep_existing"`
- 기존 교체: `on_duplicate="replace"`와 반환된 `candidate_ids`를
  `approved_ids`로 그대로 전달
- 둘 다 저장: `on_duplicate="keep_both"`

### 버전 업데이트

기존 레시피 수정은 `sap_recipe_save`로 같은 ID를 덮어쓰지 말고
`sap_recipe_version_up`을 사용한다.

```json
{
  "recipe_id": "mm03-basic-data-read",
  "new_steps": [
    {
      "tool": "sap_run",
      "args": {"tcode": "MM03"},
      "hint": "MM03 실행"
    }
  ],
  "change_note": "SAP GUI 패치 후 화면 경로 변경"
}
```

`new_steps`는 배열이다. 전체 객체를 문자열로 감싸지 않는다.

## 3. CLI와 MCP 인자의 차이

MCP 인자는 이미 구조화된 JSON이다. JSON 문자열로 이중 인코딩하지 않는다.

올바른 MCP:

```json
{"recipe_id":"r1","steps":[{"tool":"sap_run","args":{"tcode":"MM03"}}]}
```

잘못된 MCP:

```json
{"recipe_id":"r1","steps":"[{\"tool\":\"sap_run\"}]"}
```

CLI의 `recipe-save`와 `recipe-version-up`은 JSON을 명령행 인자로 받는다. Windows
PowerShell에서는 인용부호 실수를 피하기 위해 파일 입력을 우선 사용한다.

`steps.json`:

```json
[
  {
    "tool": "sap_run",
    "args": {"tcode": "MM03"},
    "hint": "MM03 실행"
  }
]
```

PowerShell:

```powershell
sap recipe-save `
  "mm03-basic-data-read" `
  "MM03 자재 기본 데이터 조회" `
  (Get-Content .\steps.json -Raw) `
  --context "MM03 조회 전용 경로" `
  --keywords "MM03,자재,기본 데이터,조회"
```

`recipe-version-up`은 `@파일` 표기를 직접 지원한다.

```powershell
sap recipe-version-up "mm03-basic-data-read" @steps.json `
  --change-note "화면 경로 변경"
```

`recipe-replay-execute`의 성공 기준도 파일 사용을 권장한다.

```powershell
sap recipe-replay-execute "mm03-basic-data-read" `
  --success-criteria @criteria.json
```

CMD에서는 PowerShell의 백틱과 `$variable` 문법을 사용하지 않는다. 긴 JSON은
PowerShell 또는 JSON 파일을 사용한다.

## 4. 실행 순서

```text
sap_recipe_query
→ 후보 비교
→ sap_recipe_show
→ sap_recipe_replay_plan
→ precheck 검토 및 필요한 사용자 확인
→ sap_recipe_replay_execute
→ 결과 확인
```

직접 step을 실행하는 경우:

```text
sap_recipe_query
→ sap_recipe_show
→ 각 step의 tool(args)를 순서대로 호출
→ 성공: sap_recipe_success
→ 실패: sap_recipe_failure
```

자동 replay와 수동 step 실행을 섞지 않는다.

## 5. 흔한 오류

| 잘못된 사용 | 올바른 사용 |
|---|---|
| `recipe_id`에 검색 결과 객체 전달 | 문자열 ID만 전달 |
| `steps`에 JSON 문자열 전달 | MCP에서는 객체 배열 전달 |
| step에 `action`, `target_id` 사용 | `tool`, `args`, `hint` 사용 |
| `tool: "run"` | `tool: "sap_run"` |
| `args: "{\"tcode\":\"MM03\"}"` | `args: {"tcode":"MM03"}` |
| `sap_recipe_show`로 실행 시도 | show 후 replay plan/execute 사용 |
| replay execute 후 success 재호출 | 자동 통계를 그대로 사용 |
| 중복 후보를 임의로 replace | 사용자 확인 후 approved_ids 전달 |
| 실패 경로까지 저장 | 성공한 최소 경로만 저장 |
| 기존 레시피를 save로 수정 | version_up 사용 |

도구 호출 전 MCP schema의 필수 필드와 타입을 다시 확인한다. 스키마와 이 문서가
다르면 실행 중인 daemon/MCP가 오래된 것이므로 업데이트하고 재시작한다.
