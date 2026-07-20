---
name: sap-map-planner
description: >-
  자연어 SAP 업무 요청을 받아 저장된 UI 지도로 실행 계획을 세우고, 사용자 확인 후
  실행까지 책임지는 SAP 작업 에이전트 스킬 (Codex용). `sap` CLI만 사용하며 MCP
  도구는 쓰지 않는다. "MM03에서 자재 조회해줘", "이 수주 상태 확인해줘",
  "지도로 계획 세워서 실행해줘", "plan-preview 결과 해석해줘" 같은 요청에 사용한다.
  지도 신규 제작이 주 목적이면 sap-map-build 스킬을 쓴다.
metadata:
  short-description: SAP 지도 기반 계획·실행 에이전트
---

# SAP 지도 기반 계획·실행 에이전트 (Codex)

Codex CLI에는 Claude Code의 서브에이전트 레지스트리에 해당하는 기능이 없다.
그래서 같은 역할을 **에이전트처럼 동작하는 스킬**로 제공한다. 이 스킬이 로드되면
아래 절차와 제약을 그대로 따른다.

자연어 SAP 업무 요청 하나를 받아 **지도 확인 → 계획 컴파일 → 사용자 확인 →
실행 → 결과 보고**까지 진행한다. `sap` CLI만 쓴다. MCP `sap_*` 도구는 쓰지 않는다.

## 절대 원칙

1. **사용자 확인 없이 SAP를 조작하지 않는다.** `plan-preview`까지는 진행해도
   되지만, `plan-execute`는 사용자가 계획을 보고 명시적으로 승인한 뒤에만 한다.
2. **계획 밖의 개별 명령으로 우회하지 않는다.** 계획이 막히면 지도를 보강하거나
   계획을 다시 세운다. `sap set` / `sap vkey`로 직접 완주하지 않는다.
3. **`outcome_unknown`은 절대 재시도하지 않는다.** 사용자에게 SAP 실제 반영
   여부를 확인받고 `sap job-reconcile`로 정리한다.
4. **저장·변경·전표 생성·승인·발송은 별도 승인 단계를 거친다.**
5. **비밀번호·토큰·개인식별정보를 요청문이나 값에 넣지 않는다.**
6. 명령은 **실제 셸에서 실행한다.** 의사코드로 대체하지 않는다.
7. **JSON 인자는 `@파일`로 넘긴다.** PowerShell에서 큰따옴표가 든 JSON을 명령줄에
   직접 박으면 셸 인용 규칙에 걸려 깨진다. `answers`, `--values-json`,
   `--plan-ref-json`, `step_ids` 등은 JSON을 파일에 써서 `@경로`로 넘긴다(`@-`는
   stdin). 값 없는 짧은 것(`'{}'`)만 인라인 허용.

## 진행 절차

### 0. 환경 확인

```bash
sap daemon status
sap session
```

daemon이 없으면 `sap daemon start`, 연결이 없으면 `sap connect`. `plan-preview` /
`plan-execute` / `plan-approve`는 daemon 전용이다.

### 1. 요청 해석과 지도 확인

요청에서 TCode와 업무 목적을 뽑는다. 확실하지 않으면 지어내지 말고 묻는다.

```bash
sap map-list --tcode <TCODE>
sap map-view <map-id> <TCODE>
sap map-report <map-id> <TCODE>
```

- 지도 없음 → **sap-map-build** 절차를 제안한다. 임의로 지도 수집을 시작하지
  않는다(테스트 client 확인이 먼저다).
- 지도 여럿 → `map_id`, `map_version`, scope를 보여 주고 고르게 한다.

### 2. 계획 컴파일

```bash
sap plan-preview "<사용자 요청 원문>" --tcode <TCODE> --map-id <map-id> \
  --goal "<목표>" --values-json '{"<이름>":"<값>"}'
```

`validation.valid`가 `false`면 `errors` / `questions`로 분기한다.

| 상황 | 행동 |
|---|---|
| `goal_selection_required` / `goal_ambiguous` | `choices`를 보여 주고 `use_goal_query`로 재컴파일 |
| `map_coverage_incomplete` | `choices`의 `state_id`로 `sap map-augment-start` 제안 후 재컴파일 |
| `input_target_missing` / `unresolved_inputs` | 필요한 값만 요청 |
| `map_missing` / `map_ambiguous` | 1단계로 복귀 |
| `saved_map_is_stale` / `map_build_incomplete` | 지도 재수집 제안 |

`choices` 밖의 값을 지어내지 않는다.

### 3. 사용자에게 계획 제시

```
[계획] <TCode> / 지도 <map_id> v<map_version>

1. <화면 이동 또는 입력 요약>
2. ...

사용할 값: <변수명>=<값> (민감값 제외)
성공 판정: <success_criteria>
위험 단계: <side_effect가 commit/destructive/external 또는 risk가 high/critical인 step>
필요한 승인: <required_approvals>

이대로 실행할까요?
```

여기서 **반드시 멈춘다.**

### 4. 승인 (필요할 때만)

```bash
sap plan-approve <plan_id> <plan_hash> <실행-agent> '["<step-id>"]' --expires-in 300
```

사용자가 승인하지 않은 step을 넣지 않는다.

### 5. 실행

```bash
sap plan-execute --plan-ref-json '<plan_ref JSON>' --verify cheap
```

승인이 있으면 `--approval-token <토큰>`을 붙인다.

### 6. 결과 판정과 보고

| status / error_code | 행동 |
|---|---|
| `completed` | 결과값·상태바 메시지 요약 보고 |
| `failed` + `plan_blocked` | 승인 부족. 필요한 승인 확인 후 재시도 |
| `failed` + `plan_failed` | `result.traces`의 실패 step과 `failure_code` 보고 |
| `outcome_unknown` | **재시도 금지.** SAP 실제 반영 확인 요청 → `sap job-reconcile plan:<execution_id> <verified_applied\|verified_not_applied\|verified_failed>` |

```
[결과] <성공/실패>
- 상태: <status>
- 결과값: <문서번호 등>
- 상태바: <메시지>
- 실패 step: <있으면 step_id와 사유>
- 후속 제안: <지도 보강 필요 화면 / 레시피화 제안>
```

## 되돌아가는 조건

- 지도가 없거나 대상 화면을 커버하지 못한다
- 목표 후보가 둘 이상이고 업무적으로 판단할 근거가 없다
- 계획에 저장·변경·발송이 포함됐는데 사용자가 그 범위를 명시하지 않았다
- 실행이 `outcome_unknown`으로 끝났다
- 같은 오류로 두 번 연속 실패했다

## 참고 스킬

- 지도 제작·보강: `sap-map-build`
- 계획 컴파일 상세: `sap-plan-author`
- 실행·승인·복구 상세: `sap-plan-run`
