# SAP GUI 자율 실행 목표 완료 감사

기준일: 2026-07-15

이 문서는 자동 테스트 통과 여부와 최종 목표 달성 여부를 분리한다. 각 항목은
현재 작업 트리와 실제 Windows SAP GUI 증거가 함께 있어야 완료로 판정한다.

## 판정 기준

- `PASS`: 요구 범위 전체를 코드, 회귀 테스트, 실제 SAP 증거로 확인
- `PARTIAL`: 기반은 있으나 실행 경로 또는 실기 증거가 빠짐
- `FAIL`: 요구한 공개 실행 경로가 없거나 잘못된 완료 판정이 가능
- `PENDING`: 구현 후 Windows 실기 확인 대기

## 목표별 현재 판정

| 목표 | 판정 | 확인된 증거 | 완료를 막는 항목 |
|---|---|---|---|
| SAP GUI Scripting API 전수 반영 | PARTIAL | PL08 strict 그래프 76 objects, 466 methods, 403 properties, 17 events, 10 enums, 438 enum values와 1,410 coverage 행 재현. adapter/policy 누락 0, 객체 handle과 ByRef/out 계약 구현. Windows canary의 daemon-only `api_live_probes`는 low-risk read member만 실행하고 성공 증거를 값 없는 durable 원장과 coverage report에 반영 | Windows SAP GUI `live_tested` 0. event 및 승인된 안전 probe batch의 실기 증거 필요 |
| TCode 전체 화면·요소 지도 | PARTIAL | lossless scanner, 탭·가로/세로 스크롤·트리·collection 순회, modal 업무값 비종속 fingerprint, immutable delta, partial/unobserved/restoration coverage gate. Table·ALV/Grid·ApoGrid viewport 셀은 값 없이 button/link/F4/edit affordance를 저장하며, Calendar는 날짜값 없이 구조 상태로 저장한다. 입력·status 본문은 영속하지 않음. 실제 session TCode가 caller hint보다 우선하며, 다른 TCode로 넘어간 전이는 원 지도에 병합하지 않음. MCP에서 mode를 생략한 지도 요청은 guided로 시작해 화면 하나만 passive로 저장하고 끝나는 것을 막음 | 위험하거나 되돌릴 수 없는 동작은 hard deny. 특수 control과 대형 collection의 Windows 실기 coverage 미확인 |
| 사용자 요청형 누락 화면 보강 | PARTIAL | `map-augment-start`/`sap_map_augment_start`, durable `needs_user_input`, 수동 이동 후 같은 지도 delta 병합, 반복 보강, daemon 재시작 hydration, budget pause/resume, resolved gap 이력. 출발 화면에 버튼·메뉴·탭 선택지가 있으면 실제 사용한 구조적 전이를 반드시 확인받아 replayable 간선으로 저장하므로, `continue`만 보내서 실행 불가 수동 간선이 생기지 않음 | 실제 Windows SAP에서 반복 보강·route teaching 흐름 미검증 |
| 자연어→지도 기반 전체 계획 | PARTIAL | deterministic parser, 모든 경로 상태의 입력 배치, ambiguous/missing 필드 질문, immutable plan hash, preview/preflight. 목표 버튼의 저장된 후속 action을 포함하고, 짧은 수동 경로보다 긴 실행 가능 경로를 우선. 목표를 찾지 못했으며 관측되지 않은 collection/전이 coverage가 있으면 `map_coverage_incomplete`로 보강할 state와 reason을 질문함 | 지도에 최종 업무 action 전이가 없으면 계획기가 보수적으로 중단함. Windows 업무 fixture 미검증 |
| 계획 1회 전달·무재스캔 실행 | PARTIAL | preview·순회 job·보강 job은 daemon 전용, 실행은 daemon 단일 IPC, live scope/program/dynpro/tab/subscreen/modal cheap 검증. 기본 `fast` 계획은 drift 시 full scan을 금지하고 fail-closed 재preview로 전환하며, canary는 각 실행의 full screen scan 0회를 강제 | Windows에서 fast path·drift·20회 성능 미검증 |
| 권한·승인·복구 | PARTIAL | daemon 발급 일회성 승인 토큰, caller grant 거부, IPC HMAC·nonce·timestamp, owner/slot 결합, outcome 사전검증·분리 상태 코드 | localhost TCP의 같은 OS 사용자 위협은 완전 격리되지 않음. Windows DACL과 장애 복구 실기 미검증 |
| Windows 실기·성능 | PENDING | 안전 canary runner와 JSONL/JSON/Markdown report | 실제 ALV/Table/Tree/Modal 및 20회 p50/p95/drift 결과 없음 |

## 수정 게이트

1. 공식 API 그래프의 모든 객체·멤버·enum을 coverage 원장에 포함한다.
2. 지원하지 않는 ByRef/out 또는 특수 COM 계약은 지원 완료로 계산하지 않는다.
3. completed 지도에도 사용자가 누락 화면을 지정해 새 durable checkpoint를 만들고
   같은 지도에 반복적으로 병합할 수 있어야 한다.
4. 지원하지 않는 자동 전이는 `completed`로 숨기지 않고 coverage와
   `needs_user_input`으로 드러낸다.
5. 지도 completeness와 collection별 partial/unobserved 상태를 보고서에서 gate한다.
6. 계획 실행 전 실제 SAP system/client/language와 지도 scope를 대조한다.
7. 계획의 예상 상태 signature를 실제 cheap probe와 단계별로 대조한다.
8. 승인은 daemon이 발급·보관하고 만료·주체·plan hash·step을 검증한 일회성 증표만
   실행기가 소비한다.
9. blocked/failed/completed/outcome_unknown 상태를 runtime, coordinator, ledger에서
   동일하게 기록한다.
10. Windows SAP GUI에서 안전 fixture로 구조 coverage와 20회 반복 성능을 확인한다.

## 최종 완료 규칙

위 표의 모든 항목이 `PASS`이고, 전체 회귀 테스트와 `git diff --check`가 통과하며,
민감정보를 제거한 Windows 실기 보고서가 있어야 전체 목표를 완료로 표시한다.

## 2026-07-15 코드 재감사 결과

- 전체 자동 테스트: `1187 passed` (2026-07-19 기준)
- 집중 반례: run 직후 오화면, modal 부재, 숨은 subscreen, 빈 지도 coverage,
  복원 증거 누락, 사용자 보강 뒤 unresolved gap, modal 업무 식별자 변형, exact
  allowlisted branch, user-taught route의 계획 컴파일, 지도 scope 불일치,
  가로 스크롤 축, modal signature, VKey(11) 승인, 저장 타입 기반 input batch,
  invalid plan reference 차단, fast path 재스캔 금지를 고정
- 정적 확인: `git diff --check`, `python -m compileall -q sap_cli scripts` 통과
- 최종 미완료 사유: Windows SAP GUI 실기와 API live conformance가 아직 없음
