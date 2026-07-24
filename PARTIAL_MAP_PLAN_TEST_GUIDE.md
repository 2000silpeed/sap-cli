# 부분 지도 기반 LLM 계획 보강 테스트 가이드

이 문서는 Windows SAP GUI 환경에서 부분적으로 기록된 UI 지도를 사용해 계획을
요청했을 때, LLM이 목표와 관련된 미관측 분기를 판단하고 필요한 지도만 보강한 뒤
계획을 다시 생성하는 흐름을 검증하는 절차입니다.

## 1. 최신 버전 설치와 daemon 재시작

업데이트 전 daemon이 계속 실행 중이면 새 명령과 플래너 로직이 반영되지 않습니다.
PowerShell에서 저장소를 업데이트하고 daemon을 재시작합니다.

```powershell
cd C:\path\to\sap-cli
git pull
pip install -e .

sap daemon stop
sap daemon start
sap daemon status
```

`sap daemon status`에서 daemon이 실행 중인지 확인합니다. SAP GUI에 로그인한 다음
세션 연결도 확인합니다.

```powershell
sap connect
sap session
```

## 2. 새 테스트 지도에 일부 화면만 기록

기존 지도의 완성도나 이전 테스트 결과가 영향을 주지 않도록 새로운 `map-id`를
사용합니다.

```powershell
sap run MM03
sap map-record-start mm03-partial-test MM03
```

대화형 AI에는 다음과 같이 요청할 수 있습니다.

> 현재 화면 구조를 읽고 다음에 이동할 곳을 하나씩 나에게 물어보면서 MM03 지도를
> 기록해줘. 모든 분기는 기록하지 말고 기본 데이터 화면까지만 기록해줘.

몇 단계만 이동한 뒤 기록 상태를 확인하고 종료합니다.

```powershell
sap map-record-status
sap map-record-finish
sap map-report mm03-partial-test MM03
```

`map-record`는 사용자가 실제로 이동한 화면을 기록합니다. 이동하지 않은 모든 버튼을
자동으로 미관측 항목으로 표시하는 것은 아닙니다. 새 플랜 로직을 검증하려면
`map-report`에서 다음 중 하나가 확인되어야 합니다.

```text
unobserved_actionable_branches > 0
has_unreachable = true
```

## 3. 미관측 표시가 없을 때 guided 순회 사용

기록 지도에 미관측 표시가 없다면 guided 순회를 시작하고, 모든 후보를 완료하지 않은
부분 상태를 만들어 테스트합니다.

```powershell
sap map-traverse-start MM03 `
  --map-id mm03-partial-test `
  --mode guided `
  --test-client-confirmed

sap map-job-status <job-id>
sap map-report mm03-partial-test MM03
```

MM03 첫 화면에서 자재번호 같은 실행 입력이 필요하면 AI가
`runtime_input_required` 질문을 사용자에게 보여줘야 합니다. 입력값은 지도나 job
파일에 저장하지 않고 현재 실행 메모리에서만 사용합니다.

## 4. 기록하지 않은 화면을 목표로 계획 요청

일부러 아직 기록하지 않은 분기를 목표로 지정합니다.

```powershell
sap plan-preview "MM03에서 구매 관련 정보를 조회해줘" `
  --tcode MM03 `
  --map-id mm03-partial-test `
  --goal "구매"
```

MCP AI에는 다음처럼 자연어로 요청할 수 있습니다.

> `mm03-partial-test` 지도를 사용해서 MM03 구매 관련 정보 조회 계획을 만들어줘.
> 지도가 부분적이면 요청과 관련된 미관측 분기를 의미적으로 판단해서 필요한 분기만
> 보강하고 다시 계획해줘.

## 5. 기대 동작

1. 목표가 저장된 지도에 있으면 즉시 실행 계획을 생성합니다.
2. 목표가 없고 미관측 후보가 있으면 `goal_semantic_inference_required`를 반환합니다.
3. 호출 LLM이 자연어 목표와 후보의 `screen_context`, `actionable_controls`를
   의미적으로 비교합니다.
4. 관련 후보가 하나면 사용자에게 다시 묻지 않고 그 후보의 `augment_args`로
   `sap_map_augment_start`를 호출합니다.
5. 지도 보강이 완료되면 원래 요청으로 `sap_plan_preview`를 다시 호출합니다.
6. 후보가 없거나 비슷한 후보가 여러 개일 때만 사용자에게 선택을 요청합니다.

미관측 목적지는 아직 상태 fingerprint와 실행 전이가 없으므로, LLM이 추측해서 실행
계획에 직접 넣어서는 안 됩니다. 반드시 해당 분기를 먼저 관측하고 저장한 후 계획을
다시 생성해야 합니다.

## 6. 응답 JSON 확인 항목

CLI 응답을 직접 확인할 때 다음 필드를 봅니다.

```text
validation.errors[0] = goal_semantic_inference_required
validation.questions[0].code = goal_semantic_inference_required
validation.questions[0].decision_owner = calling_llm
validation.questions[0].goal_query
validation.questions[0].choices[].candidate_id
validation.questions[0].choices[].screen_context
validation.questions[0].choices[].actionable_controls
validation.questions[0].choices[].augment_args
```

각 `augment_args`에는 다음 보강 호출에 필요한 값이 포함됩니다.

```json
{
  "map_id": "mm03-partial-test",
  "tcode": "MM03",
  "from_state_id": "<관련 화면 state id>",
  "request": "계획 목표 '구매'와 관련된 미관측 분기 확인"
}
```

## 7. 판단 기준

미관측 후보의 존재 여부는 지도에 저장된 구조적 증거를 사용하는 규칙 기반 판단입니다.

- 해결되지 않은 `manifest.unreachable`
- collection coverage의 `partial`
- `metadata_only`
- `not_observed`
- `unobserved`
- `unknown`
- `not_sampled`

어느 후보가 사용자의 자연어 목표와 관련 있는지는 호출 LLM이 판단합니다. 즉,
미관측 여부를 LLM이 만들어내지는 않으며, 실제 지도에 존재하는 폐쇄형 후보 중
관련 분기를 의미적으로 선택합니다.

## 8. 문제 해결

### 계속 기존 오류 문구가 나오는 경우

이전 daemon이 실행 중일 가능성이 큽니다.

```powershell
sap daemon stop
sap daemon start
sap daemon status
```

설치 위치와 실제 실행 파일도 확인합니다.

```powershell
Get-Command sap
python -c "import sap_cli; print(sap_cli.__version__)"
```

### 계획이 바로 `goal_ambiguous`를 반환하는 경우

지도에 구조적 미관측 표시가 없을 수 있습니다. `map-report`를 확인하고 3절의 guided
순회로 미관측 후보를 생성합니다.

### LLM이 모든 후보를 사용자에게 다시 묻는 경우

AI가 최신 MCP 도구 설명 또는 스킬 문서를 읽고 있는지 확인합니다.
`goal_semantic_inference_required`에서는 의미상 관련 후보가 하나일 경우 해당
`augment_args`로 자동 보강해야 하며, 후보가 없거나 여러 개가 비슷할 때만 질문합니다.

### 보강 후에도 같은 응답이 반복되는 경우

보강 job이 완료됐는지 확인하고 완료된 지도 버전으로 계획을 다시 요청합니다.

```powershell
sap map-job-status <job-id>
sap map-report mm03-partial-test MM03
```

job이 `needs_user_input`이면 pending question의 `resume_token`과 선택지를 사용해
`map-traverse-resume`으로 계속 진행해야 합니다.
