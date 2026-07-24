---
name: sap-plan-author
description: >-
  자연어 SAP 업무 요청을 저장된 UI 지도 기반의 실행 계획으로 컴파일하는 스킬.
  `sap plan-preview` CLI만 사용하며 SAP를 조작하지 않는다. "MM03 조회 계획 보여줘",
  "실행하지 말고 계획만", "plan-preview가 goal_ambiguous를 냈어",
  "map_coverage_incomplete 어떻게 해결해", "승인 필요한 step 알려줘" 같은 요청에
  사용한다. 지도 제작은 sap-map-build, 실제 실행은 sap-plan-run 스킬이 담당한다.
---

# SAP 실행 계획 작성 (CLI 전용)

`sap plan-preview` 하나만 쓴다. 이 명령은 **SAP를 전혀 조작하지 않는다.** 저장된
UI 지도와 레시피를 읽어 step, 변수, 필요한 승인, 지도 버전/fingerprint를
확정하고 검증 결과를 돌려준다.

이 스킬은 계획을 **만들고 설명하는 데서 끝난다.** 실행은 절대 하지 않는다.
사용자가 실행을 확인하면 sap-plan-run 스킬로 넘긴다.

## 전제

**먼저 sap_cli 프로젝트 폴더로 이동해 `.venv`를 활성화한 뒤** 모든 `sap` 명령을
실행한다. 활성화하지 않으면 `sap`이 PATH에 없거나 다른 설치본을 쓸 수 있다.
(`cd <프로젝트 폴더>` → `.venv\Scripts\Activate.ps1`, macOS/Linux는
`source .venv/bin/activate`. 대안: `.venv\Scripts\sap ...` 직접 호출.)

```powershell
sap daemon status
sap map-list --tcode MM03
```

`plan-preview`는 persistent daemon 전용이다. daemon이 없으면 payload 없이
"persistent daemon 전용입니다" 오류로 즉시 실패한다. 이 경우 `sap daemon start`
(실패하면 `sap daemon doctor`)로 daemon을 먼저 올린 뒤 다시 미리보기한다.

## 기본 호출

```powershell
sap plan-preview "MM03에서 자재 기본 데이터를 조회해줘" `
  --tcode MM03 --map-id mm03-readonly --goal "기본 데이터" `
  --values-json '{"material":"TEST_MATNR"}'
```

**PowerShell 주의.** 큰따옴표가 여럿 든 `--values-json`을 명령줄에 직접 박으면
셸 인용 규칙에 걸려 깨진다. LLM이 CLI를 호출할 때는 값 객체를 파일에 써서
`@경로`로 넘긴다(`@-`는 stdin). 값이 하나뿐인 짧은 경우만 인라인을 쓴다.

```powershell
Set-Content values.json -Value '{"material":"TEST_MATNR","plant":"1000"}'
sap plan-preview "..." --tcode MM03 --map-id mm03-readonly --values-json '@values.json'
```

주요 옵션:

| 옵션 | 용도 |
|---|---|
| `--tcode` | 대상 TCode. 생략하면 요청문에서 추론하며 실패 시 `tcode_missing` |
| `--map-id` | 사용할 지도. 생략 시 scope로 자동 선택, 여러 개면 `map_ambiguous` |
| `--goal` | 목표 화면/요소 표현. 모호할 때 후보의 `use_goal_query` 값을 넣는다 |
| `--values-json` | 업무값 객체. 비밀번호·토큰은 넣지 않는다 |
| `--current-state` | 현재 화면 state id. 시작점을 고정할 때 |
| `--success-criteria-json` | 성공 판정 문자열 배열 |
| `--system` / `--client` | 지도 scope 명시 |

## 응답 읽는 순서

응답 최상위 키는 `validation`, `plan`, `plan_ref`, `activation`, `preflight`,
`transaction_authorization`이고, 의도 해석에 성공하면 `intent`가 추가된다.
**최상위 `status` 키는 없다.** 성공 여부는 `validation.valid`로 판단한다.

```powershell
$preview = sap plan-preview "..." --tcode MM03 --map-id mm03-readonly | ConvertFrom-Json
$preview.validation
$preview.plan.steps
$preview.plan_ref
```

1. **`validation.valid`** — `false`면 여기서 멈추고 `errors`/`questions`를 읽는다.
2. **`validation.questions`** — 사용자에게 물을 것. 임의로 고르지 않는다.
3. **`plan.steps`** — 실행 순서. `command`, `target_logical_id`, `side_effect`,
   `risk`, `approval_required`를 사람이 읽을 형태로 정리한다.
4. **`validation.unresolved_inputs`** — 아직 안 채워진 값. 사용자에게 요청한다.
5. **`validation.requires_approval` / `required_approvals`** — 승인이 필요한
   step id 목록.
6. **`plan_ref`** — `{plan_id, plan_hash, expires_in_seconds}`. 실행 단계에
   그대로 넘긴다.
7. **`activation`** — 유효할 때만 채워진다. `arguments`를 그대로 실행에 쓴다.

`plan_ref`는 daemon 메모리에만 있고 기본 TTL은 900초, 최대 128개 LRU다. 시간이
지나면 같은 요청으로 다시 preview해야 한다.

## 오류 코드와 대응

`validation.errors`에 오는 값과 처리 방법이다.

| error | 뜻 | 대응 |
|---|---|---|
| `tcode_missing` | TCode를 못 정함 | `--tcode` 명시 |
| `operation_ambiguous` | 동작이 여러 갈래 | 요청문을 좁히거나 사용자에게 확인 |
| `map_missing` | scope에 맞는 지도 없음 | sap-map-build로 지도 생성 |
| `map_ambiguous` | 후보 지도 여럿 | `--map-id` 명시 |
| `saved_map_is_stale` | 지도가 현재 런타임과 안 맞음 | `warnings[0]`의 사유 확인 후 재수집 |
| `map_build_incomplete` | 지도 수집이 미완 | `map-report` 확인 후 guided 확장 |
| `start_state_missing` | 시작 화면을 못 찾음 | `--current-state` 지정 또는 지도 보강 |
| `goal_selection_required` / `goal_ambiguous` | 목표 후보 여럿 | 아래 참조 |
| `goal_semantic_inference_required` | 부분 지도에서 목표 관련 분기 판단 필요 | 아래의 LLM 분기 판단 절차 |
| `map_coverage_incomplete` | 목표까지 경로 부족 | 아래 참조 |
| `input_target_missing` / `input_target_ambiguous` | 값 넣을 필드 확정 실패 | questions의 choices로 확인 |
| `collection_target_ambiguous` / `collection_target_requires_user_guidance` | 테이블/트리 대상 불명확 | 사용자에게 대상 행/열 확인 |
| `transition_state_missing:<id>` | 전이가 가리키는 화면 없음 | 지도 재수집 |

### 목표가 모호할 때

`questions`에 `goal_selection_required` 또는 `goal_ambiguous`가 오면
`choices[]`에 `state_id`, `logical_id`, `label`, `type`, `use_goal_query`,
`path_transition_ids`가 들어 있다. **임의로 고르지 않는다.** 사용자에게 화면과
요소를 보여 주고, 고른 항목의 `use_goal_query`를 다음 호출의 `--goal`로 넘긴다.

```powershell
sap plan-preview "..." --tcode MM03 --map-id mm03-readonly --goal "<use_goal_query 값>"
```

`goal_semantic_inference_required`는 단순 오류가 아니다. 호출 LLM이 자연어 목표와
각 `choices[]`의 `screen_context`, `actionable_controls`, `reason`을 의미적으로 비교한다.
관련 후보가 하나면 사용자에게 다시 묻지 않고 그 후보의 `augment_args`로
`sap_map_augment_start`를 호출한다. 후보가 없거나 둘 이상이 비슷할 때만 사용자가
`candidate_id`를 선택하게 한다. 보강 job이 완료되면 원래 요청과 목표로
`sap_plan_preview`를 다시 호출한다. 관측하지 않은 목적지를 계획에 직접 추측해 넣지는 않는다.

### 지도 커버리지가 부족할 때

`map_coverage_incomplete` 질문에는 `map_id`, `tcode`, `from_state_id`와
`choices[]`(`kind`가 `transition` 또는 `collection`, 각 `state_id`와 `reason`)가
온다. 이 `state_id`를 그대로 `sap map-augment-start`의 `from_state_id`로 쓴다.

```powershell
sap map-augment-start mm03-readonly MM03 <choices의 state_id> "<누락 화면 설명>"
```

보강이 끝나면 같은 요청으로 preview를 다시 만든다.

### 값이 비어 있을 때

`unresolved_inputs`에 변수명이 오고, `questions`에는
`{"code":"input_target_missing","field":"<이름>","question":"…"}`가 온다.
사용자에게 그 값만 물어 `--values-json`에 추가한다. 비밀번호·토큰류는 요청하지
않는다. (민감 변수는 응답에서 `***REDACTED***`로 마스킹된다.)

## 계획을 사용자에게 설명하는 형식

`plan.steps`를 그대로 덤프하지 않는다. 다음 항목으로 정리한다.

1. **사용할 TCode와 지도** — `plan.tcode`, `plan.map_id`, `plan.map_version`
2. **화면 이동과 입력 순서** — step별 `order`, `command`, 대상 요소 이름
3. **사용할 업무값** — `plan.variables` (민감값 제외)
4. **성공 판정 기준** — `plan.success_criteria`
5. **위험 단계** — `side_effect`가 `commit`/`destructive`/`external`이거나
   `risk`가 `high`/`critical`인 step, 그리고 `required_approvals`
6. **실행 전 확인 요청** — 위 내용이 맞는지 사용자에게 명시적으로 묻는다

`side_effect` 값은 `read_only`, `reversible`, `navigation`, `commit`,
`destructive`, `external`. `risk`는 `low`, `medium`, `high`, `critical`.

조회성 계획이라도 사용자 확인 없이 실행으로 넘어가지 않는다.

## 하지 말 것

- `validation.valid`가 `false`인데 실행을 제안하는 것
- `questions`의 `choices` 밖 값을 지어내는 것
- `plan_ref`를 사용자에게 확인받지 않고 sap-plan-run으로 넘기는 것
- 계획이 막혔을 때 `sap set` / `sap vkey` 같은 개별 명령으로 우회 실행하는 것
  (지도를 보강해 다시 계획을 세우는 것이 정상 경로다)
- 요청문이나 `--values-json`에 비밀번호, 토큰, 개인식별정보를 넣는 것

## 다음 단계

계획이 유효하고 사용자가 내용을 확인했으면 **sap-plan-run** 스킬로 넘긴다.
넘길 때는 `plan_ref` 전체(JSON)와 `required_approvals`를 함께 전달한다.
