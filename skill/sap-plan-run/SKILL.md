---
name: sap-plan-run
description: >-
  확정된 SAP 실행 계획을 `sap plan-approve` / `sap plan-execute` CLI로 실행하고
  결과를 판정하는 스킬. MCP를 쓰지 않는다. "계획 실행해줘", "확인했어 실행해",
  "승인 토큰 발급", "plan_blocked가 떴어", "outcome_unknown 어떻게 처리해",
  "실행 결과 요약해줘" 같은 요청에 사용한다. 계획 작성은 sap-plan-author 스킬이
  선행되어야 한다.
---

# SAP 실행 계획 실행 (CLI 전용)

`sap plan-approve`와 `sap plan-execute`만 쓴다. 이 스킬은 **plan-preview로 만든
유효한 `plan_ref`가 이미 있고, 사용자가 계획 내용을 확인한 뒤에만** 진입한다.

## 실행 전 게이트

다음이 모두 참이어야 실행한다. 하나라도 아니면 실행하지 않고 사용자에게
되묻는다.

1. `validation.valid`가 `true`이고 `plan_ref`가 존재한다.
2. 사용자가 TCode·입력값·화면 이동·성공 조건을 **명시적으로 확인**했다.
3. `required_approvals`가 비어 있거나, 필요한 승인 토큰을 발급받았다.
4. `plan_ref`가 만료되지 않았다 (기본 TTL 900초).

계획이 오래됐거나 지도가 바뀌었을 가능성이 있으면 실행 대신 **재미리보기**를
요청한다.

## 1) 승인이 필요한 경우

`validation.requires_approval`이 `true`면 `required_approvals`의 step만 승인한다.
토큰은 특정 plan hash · 승인 step · 실행 agent에 묶인 **일회용** 값이다.

```powershell
$approval = sap plan-approve $preview.plan_ref.plan_id $preview.plan_ref.plan_hash `
  <실행-agent> '["<승인할 step-id>"]' --expires-in 300 | ConvertFrom-Json
```

- `step_ids`는 계획 안의 step id이거나 `"authorize-tcode"`여야 한다. 계획 밖의
  step을 넣으면 `approval contains a step outside the immutable plan`으로 거부된다.
- `--expires-in` 기본 300초, 최대 900초.
- 응답은 `approval_token`, `expires_in_seconds`, `daemon_memory_only`,
  `single_use` 네 키다. 토큰은 디스크에 저장되지 않는다.
- **승인 대상 step을 임의로 넓히지 않는다.** 사용자가 승인한 step만 넣는다.

## 2) 실행

```powershell
sap plan-execute --plan-ref-json ($preview.plan_ref | ConvertTo-Json -Compress) --verify cheap
```

승인이 필요한 계획이면 토큰을 함께 넘긴다.

```powershell
sap plan-execute --plan-ref-json ($preview.plan_ref | ConvertTo-Json -Compress) `
  --verify cheap --approval-token $approval.approval_token
```

| 옵션 | 설명 |
|---|---|
| `--plan-ref-json` | preview의 `plan_ref` 객체를 그대로 전달 (권장) |
| `plan_id` / `plan_hash` 위치인자 | `--plan-ref-json` 대신 개별 전달 가능. 둘 다 주면 값이 일치해야 한다 |
| `execution_id` | 생략하면 자동 발급. 명시하면 같은 ID 재전송 시 SAP 재조작 없이 이전 결과를 돌려준다 |
| `--verify` | `cheap`(기본) 또는 `full`. 그 외 값은 오류 |
| `--approval-token` | plan-approve의 토큰 |

`--verify cheap`은 화면 전체를 다시 읽지 않는 빠른 경로다. `full`은 마지막
검증에 화면을 다시 읽으므로 조사·검증 목적에만 쓴다.

`approval_grants`를 직접 넘기려 하지 않는다. 호출자가 주면 거부된다. 승인은
반드시 `--approval-token` 경로다.

실행이 오래 걸리는 계획은 `SAP_CLI_PLAN_EXECUTE_TIMEOUT`(초)으로 클라이언트
대기 시간을 늘린다.

## 3) 결과 판정

응답에는 항상 `execution_id`, `status`, `replayed`가 있다.

| status | error_code | 뜻 | 대응 |
|---|---|---|---|
| `completed` | — | 정상 실행 | `result`로 결과 요약 |
| `failed` | `plan_blocked` | 승인 부족 등으로 실행 전 차단 | 필요한 승인 확인 후 재시도 |
| `failed` | `plan_failed` | 실행 중 step 실패 | `result.traces`의 `failure_code` 확인 |
| `outcome_unknown` | `executor_outcome_unknown` | 실행기 예외. `result` 없음 | **재시도 금지.** 아래 참조 |
| `outcome_unknown` | `unknown_commit_outcome` | commit 시도 후 결과 불명 | **재시도 금지.** 아래 참조 |

`replayed`가 `true`면 같은 `execution_id`로 이미 실행된 건이며 SAP를 다시
조작하지 않았다는 뜻이다.

`result` 구조:

- `status` — `success` / `failed` / `blocked`
- `errors` — 실패 사유
- `traces[]` — step별 `step_id`, `order`, `command`, `status`, `attempts`,
  `result`, `probe`, `failure_code`, `recovery`
- `final_probe` — 마지막 화면 확인 결과
- `commit_attempted` — 저장/전기 시도 여부
- `get_screen_count` — 전체 화면 스캔 횟수

사용자에게는 성공/실패, 문서번호 같은 결과값, 상태바 메시지, 실패한 step과
사유를 정리해 보고한다. `traces` 전체를 덤프하지 않는다.

## 4) outcome_unknown 처리 — 가장 중요한 규칙

`outcome_unknown`은 **SAP에 반영됐는지 알 수 없다**는 뜻이다. 자동 재시도하면
전표 이중 생성 같은 사고가 난다. 런타임도 이 실행을 자동 재시도하지 않고 slot
lease를 잡아 둔다.

절차는 이렇다.

1. 사용자에게 "SAP에서 해당 문서/변경이 실제로 반영됐는지 직접 확인"을 요청한다.
2. 확인 결과에 따라 명시적으로 정리한다.

```powershell
sap job-status
sap job-reconcile plan:<execution_id> verified_applied
```

| resolution | 사용 시점 |
|---|---|
| `verified_applied` | SAP에 반영된 것을 확인함 → `completed`로 종결 |
| `verified_not_applied` | 반영되지 않은 것을 확인함 → `failed`로 종결 |
| `verified_failed` | 실패로 확정 (`release_after_manual_verification`도 같은 뜻) |

reconcile 전까지는 같은 계획을 다시 실행하지 않는다.

## 5) 그 밖의 실패

| 증상 | 원인 | 대응 |
|---|---|---|
| `cached plan UI-map dependency is stale` | 지도가 바뀌어 계획이 폐기됨 | plan-preview를 다시 만든다 |
| plan을 찾을 수 없음 | TTL 만료 또는 daemon 재시작 | plan-preview를 다시 만든다 |
| `execution_id is already bound to a different request` | 같은 ID로 다른 계획 실행 시도 | 새 `execution_id`를 쓴다 |
| `SAP slot is reserved by another daemon job` | 다른 job이 slot 점유 | `sap job-status`로 확인 후 대기 |
| `plan execution requires the persistent daemon runtime` | daemon 없음 | `sap daemon start` 후 재미리보기 |
| `approval_token_rejected` | 토큰 불일치/만료 | 다시 승인 발급 |

locator가 어긋나거나 지도 scope가 안 맞아 실패하면 **개별 CLI 명령으로 우회
실행하지 않는다.** 지도 보강 또는 새 계획이 정상 경로다.

## 하지 말 것

- 사용자 확인 없이 실행하기
- `outcome_unknown`을 재시도하기
- 승인 step 범위를 임의로 넓히기
- 실패한 계획을 `sap set` / `sap press` / `sap vkey`로 수동 완주하기
- 계획에 없던 저장·전기·발송 동작을 추가하기

## 실행 후 마무리

1. 결과와 상태바 메시지를 요약한다.
2. 지도와 실제 화면이 달랐던 지점이 있으면 보강이 필요한 화면을 알린다.
3. 반복 업무면 `sap recipe-*` 계열로 레시피화를 제안한다.
