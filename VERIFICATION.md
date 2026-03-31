# SAP CLI 사내 검증 시나리오

SAP 실환경에서 수행하는 통합 검증 가이드.
각 시나리오는 **사전 조건 → 실행 단계 → 기대 결과 → 실패 시 확인사항** 구조로 작성.

---

## 사전 조건 (모든 시나리오 공통)

- Windows 10/11, SAP GUI 7.70+ 설치
- SAP GUI Scripting 활성화 (서버/클라이언트 모두)
- SAP 시스템에 로그인 완료 상태
- `pip install -e .` 설치 완료
- `sap --help` 정상 출력 확인

---

## 시나리오 1: 기본 연결 및 화면 탐색

### 목표

CLI로 SAP에 연결하고, 화면 정보를 정상적으로 읽어오는지 확인.

### 실행 단계

```powershell
# 1. SAP 연결
sap connect

# 2. 세션 정보 확인
sap session

# 3. 전체 화면 구조 조회
sap screen

# 4. 입력 가능한 필드만 조회
sap screen --fields-only

# 5. 액션(버튼) 목록 조회
sap screen --actions-only

# 6. 상태바 메시지 확인
sap status
```

### 기대 결과

| 단계 | 확인 항목 |
|------|----------|
| connect | `"connected": true` + 세션 정보(user, system_name, client) |
| session | user, language, transaction, system_name 필드 존재 |
| screen | transaction, screen_number, fields, buttons 포함된 JSON |
| screen -f | changeable_fields 배열에 1개 이상 필드 |
| screen -a | available_actions 배열에 1개 이상 액션 |
| status | message_type(S/W/E/I/A), message 필드 존재 |

### 실패 시 확인사항

- `sap connect` 실패 → SAP GUI 로그인 상태/스크립팅 활성화 확인
- `screen` 결과가 비어있음 → SAP GUI 버전이 너무 낮은지 확인 (7.70+ 필요)
- 필드 ID 형식이 예상과 다름 → `sap screen` 전체 출력으로 실제 ID 확인

---

## 시나리오 2: MM03 자재 조회 자동화

### 목표

MM03 트랜잭션에서 자재를 조회하고 데이터를 읽는 전체 사이클을 자동화.

### 사전 준비

- 조회할 자재번호 확인 (예: 시스템에 존재하는 자재번호)
- MM03은 조회 전용이므로 안전하게 테스트 가능

### 실행 단계

```powershell
# 1. SAP 연결 (이미 연결되어 있으면 생략 가능)
sap connect

# 2. MM03 트랜잭션 실행
sap run MM03

# 3. 초기 화면 확인 — 자재번호 입력 필드 확인
sap screen --fields-only
# → wnd[0]/usr/ctxtRMMG1-MATNR 필드 확인

# 4. 자재번호 입력
sap set "wnd[0]/usr/ctxtRMMG1-MATNR" "자재번호"

# 5. 입력 확인
sap get "wnd[0]/usr/ctxtRMMG1-MATNR"
# → 입력한 값이 반환되어야 함

# 6. Enter (뷰 선택 화면으로 이동)
sap vkey 0

# 7. 상태 확인 — 에러가 없는지 체크
sap status

# 8-1. 뷰 선택 팝업이 나타난 경우
sap modal
# → has_modal: true면 팝업이 뜬 것

# 8-2. 뷰 선택에서 Enter (기본 뷰로 진입)
sap vkey 0

# 9. 자재 기본데이터 화면 확인
sap screen --fields-only
# → MARA-MAKTX(자재내역), MARA-MEINS(기본단위) 등 필드

# 10. 특정 필드값 읽기
sap get "wnd[0]/usr/txtMARA-MAKTX"
# → 자재 내역(설명)이 반환

# 11. 뒤로가기
sap vkey 3
sap vkey 3
```

### 기대 결과

| 단계 | 확인 항목 |
|------|----------|
| run MM03 | `"executed": "MM03"` |
| set 자재번호 | `"value_set": "자재번호"` |
| get 자재번호 | 입력한 값과 일치 |
| vkey 0 (Enter) | 에러 없이 화면 이동 |
| status | message_type이 E가 아닌 것 |
| screen -f (기본데이터) | 자재 관련 필드 5개 이상 |
| get 자재내역 | 비어있지 않은 문자열 |
| vkey 3 (Back) | MM03 초기 화면 또는 이전 화면으로 복귀 |

### 실패 시 확인사항

- `"자재가 존재하지 않습니다"` → 실제 존재하는 자재번호로 변경
- vkey 0 후 화면이 안 바뀜 → `sap status` 확인 (필수 입력 누락 가능)
- 필드 ID가 다름 → `sap screen --fields-only`로 실제 ID 확인 (시스템마다 커스텀 가능)
- 뷰 선택 팝업 없이 바로 진입 → 사용자 설정에 따라 팝업 생략될 수 있음 (정상)

---

## 시나리오 3: 매크로 녹화 → 재생 사이클

### 목표

MM03 자재 조회 과정을 매크로로 녹화한 뒤, 다른 자재번호로 재생하여 자동화 확인.

### 사전 준비

- 시나리오 2가 성공적으로 완료된 상태
- 조회할 자재번호 2개 준비 (녹화용, 재생용)

### Phase A: 매크로 녹화

```powershell
# 1. SAP 연결 확인
sap connect

# 2. 녹화 시작
sap record-start --name mm03_lookup

# 3. 녹화 상태 확인
sap record-status
# → recording: true, steps_count: 0

# 4. 트랜잭션 실행 (녹화됨)
sap record-step --action run --target MM03

# 5. 자재번호 입력 (녹화됨) — 변수로 지정
sap record-step --action set --target "wnd[0]/usr/ctxtRMMG1-MATNR" --value "자재번호1"

# 6. Enter 전송 (녹화됨)
sap record-step --action vkey --target "0"

# 7. 뷰 선택 Enter (녹화됨, 팝업이 뜬 경우만)
sap record-step --action vkey --target "0"

# 8. 현재 녹화 상태 확인
sap record-status
# → steps_count: 4 (또는 3)

# 9. 녹화 종료 및 저장
sap record-finish --name mm03_lookup --description "MM03 자재 조회" --variables "matnr=자재번호"

# 10. 뒤로가기 (SAP 상태 복원)
sap vkey 3
sap vkey 3
```

### Phase B: 매크로 확인

```powershell
# 11. 저장된 매크로 목록
sap macro-list

# 12. 매크로 상세 조회 (macro-list에서 확인한 ID 사용)
sap macro-show MACRO_ID
# → steps, variables, description 확인
```

### Phase C: 매크로 재생

```powershell
# 13. 드라이런 (실제 실행 없이 계획만 확인)
sap macro-play MACRO_ID --variables '{"matnr": "자재번호2"}' --dry-run
# → 각 단계와 대입될 변수값 확인

# 14. 실제 재생 (다른 자재번호로)
sap macro-play MACRO_ID --variables '{"matnr": "자재번호2"}'

# 15. 결과 확인 — 자재번호2의 기본데이터 화면에 도달
sap screen --fields-only
sap get "wnd[0]/usr/txtMARA-MAKTX"
# → 자재번호2의 자재 내역이 반환되어야 함

# 16. 정리
sap vkey 3
sap vkey 3
```

### 기대 결과

| 단계 | 확인 항목 |
|------|----------|
| record-start | `"recording": true` |
| record-step (각각) | `"recorded": true` + SAP 화면 실제 변경 |
| record-status | steps_count가 녹화한 단계 수와 일치 |
| record-finish | `"saved": true` + macro_id, path 반환 |
| macro-list | 방금 저장한 매크로가 목록에 존재 |
| macro-show | steps 배열에 녹화한 단계들, variables에 matnr |
| macro-play --dry-run | 각 단계 + 변수 대입 결과 표시 |
| macro-play (실행) | 자재번호2 기본데이터 화면에 도달 |

### 실패 시 확인사항

- `record-finish` 실패 → `record-status`로 녹화 상태 확인, `record-cancel` 후 재시도
- `macro-play` 중 에러 → 녹화 시 화면 상태와 재생 시 화면 상태가 다를 수 있음 (같은 초기 화면에서 시작)
- 변수 대입이 안 됨 → `macro-show`에서 variables 필드 확인, `record-finish`의 --variables 형식 확인
- 매크로 파일 위치: `~/.sap_cli/macros/` 디렉토리 확인

---

## 시나리오 4: 온톨로지 학습

### 목표

현재 SAP 화면을 스캔하여 온톨로지로 저장하고, 저장된 온톨로지를 조회.

### 실행 단계

```powershell
# 1. SAP 연결
sap connect

# 2. MM03 초기 화면으로 이동
sap run MM03

# 3. 현재 화면 학습
sap learn
# → 화면의 필드, 버튼, 레이아웃을 스캔하여 온톨로지로 저장

# 4. 학습 결과 확인
sap ontology --tcode MM03
# → 학습된 화면 정보가 반환

# 5. 필드 검색 테스트
sap ontology --tcode MM03 --field-search "자재"
# → 자재 관련 필드 정보

# 6. 저장된 온톨로지 전체 목록
sap ontology --list-all
# → 학습된 트랜잭션/패턴 목록

# 7. (선택) 자재번호 입력 후 다음 화면에서도 학습
sap set "wnd[0]/usr/ctxtRMMG1-MATNR" "자재번호"
sap vkey 0
sap vkey 0
sap learn
# → 기본데이터 화면의 온톨로지도 학습

# 8. 정리
sap vkey 3
sap vkey 3
```

### 기대 결과

| 단계 | 확인 항목 |
|------|----------|
| learn | `"learned": true` + transaction, screen_number, fields_count, buttons_count |
| fields_count | 1개 이상 (MM03 초기화면: 자재번호 필드) |
| buttons_count | 1개 이상 |
| ontology --tcode | 학습된 화면 구조 JSON (fields, buttons, screen_flow 등) |
| ontology --field-search | 검색어에 매칭되는 필드 정보 |
| ontology --list-all | transactions 배열에 MM03 포함 |

### 실패 시 확인사항

- `learn` 시간이 오래 걸림 → 화면 요소가 많은 경우 정상 (10초 이내)
- fields_count: 0 → SAP GUI 버전 확인, 스크립팅 API로 요소 접근 가능한지 확인
- ontology 조회 시 빈 결과 → `~/.sap_cli/ontology/` 디렉토리에 JSON 파일 생성 여부 확인
- 온톨로지 파일 위치: `~/.sap_cli/ontology/transactions/` 디렉토리

---

## 시나리오 5: MCP 서버 + Claude Code 연동

### 목표

Claude Code에서 자연어로 SAP를 조작하여, AI 에이전트 동작을 검증.

### 사전 준비

- `pip install -e ".[mcp]"` 설치 완료
- Claude Code settings.json에 sap-gui MCP 서버 등록 (SETUP.md 섹션 5 참조)
- Claude Code 재시작 완료
- SAP GUI 로그인 상태

### Phase A: 기본 연동 확인

Claude Code 대화창에서 입력:

```
SAP에 연결해줘
```

기대: Claude가 `sap_connect` 도구를 호출하고, 연결 성공 메시지를 보여줌.

```
현재 세션 정보 보여줘
```

기대: Claude가 `sap_session`을 호출하고, 사용자/시스템 정보를 보여줌.

```
지금 화면에서 입력할 수 있는 필드 목록 알려줘
```

기대: Claude가 `sap_screen` (fields_only)을 호출하고, 필드 ID + 라벨을 정리해서 보여줌.

### Phase B: 자동화 시나리오

```
MM03 트랜잭션에서 자재 [자재번호] 조회해줘
```

기대: Claude가 아래 도구들을 순차 호출:
1. `sap_run` (tcode: MM03)
2. `sap_screen` (fields_only — 필드 ID 파악)
3. `sap_set_field` (자재번호 입력)
4. `sap_send_vkey` (Enter)
5. 필요시 추가 `sap_send_vkey` (뷰 선택)
6. `sap_screen` 또는 `sap_get_field` (결과 읽기)

```
자재 내역이 뭐야?
```

기대: Claude가 `sap_get_field`로 자재 내역 필드를 읽어서 답변.

```
뒤로 가줘
```

기대: Claude가 `sap_send_vkey` (vkey: 3)을 호출.

### Phase C: 온톨로지 활용

```
현재 화면을 학습해줘
```

기대: Claude가 `sap_learn`을 호출하고 학습 결과를 보여줌.

```
MM03에서 사용할 수 있는 필드 목록 알려줘
```

기대: Claude가 `sap_ontology_query`를 호출하여 학습된 정보 기반으로 답변.

### Phase D: 권한 제어 확인 (정책 파일이 있는 경우)

```powershell
# policy.yaml에서 active_agent: readonly로 설정 후 Claude Code 재시작
```

```
MM03에서 자재번호 필드에 100-100 입력해줘
```

기대: `sap_set_field`이 권한 거부됨 (readonly는 WRITE 권한 없음).
Claude가 권한 부족 메시지를 사용자에게 전달.

### 기대 결과 요약

| 테스트 | 성공 기준 |
|--------|----------|
| 기본 연결 | connect → session → screen 순차 성공 |
| 자재 조회 | MM03 → 자재번호 입력 → 데이터 화면 도달 |
| 자연어 매핑 | Claude가 사용자 의도를 올바른 MCP 도구로 매핑 |
| 온톨로지 활용 | learn 후 ontology_query로 학습 데이터 활용 |
| 권한 제어 | readonly 에이전트가 write 작업 시 거부 |

### 실패 시 확인사항

- MCP 도구가 안 보임 → SETUP.md 섹션 5 참조
- Claude가 도구를 안 씀 → Skill 설치 여부 확인 (SETUP.md 섹션 6)
- `Unexpected error` → `python -m sap_cli.mcp_server`를 직접 실행하여 에러 로그 확인
- 권한 거부가 안 됨 → `~/.sap_cli/policy.yaml` 파일 존재 여부, active_agent 설정 확인

---

## 시나리오 6: 데몬 모드 + Shell

### 목표

데몬 모드로 COM 연결을 유지하고, Shell REPL에서 빠르게 명령 실행.

### 실행 단계

```powershell
# 1. 데몬 시작
sap daemon start

# 2. 데몬 상태 확인
sap daemon status
# → daemon: running, sap_connected: true/false

# 3. Shell 진입
sap shell

# Shell 내에서:
sap> connect
sap> session
sap> run MM03
sap> screen -f
sap> set wnd[0]/usr/ctxtRMMG1-MATNR 자재번호
sap> vkey 0
sap> vkey 0
sap> status
sap> screen -f
sap> vkey 3
sap> vkey 3
sap> exit

# 4. 파이프 모드 테스트
echo "connect" | sap shell
echo "session" | sap shell

# 5. 데몬 종료
sap daemon stop
```

### 기대 결과

| 단계 | 확인 항목 |
|------|----------|
| daemon start | 데몬 프로세스 시작, PID 파일 생성 |
| daemon status | `"daemon": "running"` |
| shell 명령 | 각 명령이 0.5초 이내 응답 (데몬 덕분에 COM 재연결 없음) |
| 파이프 모드 | JSON 결과 출력 + 정상 종료 (exit code 0) |
| 파이프 실패 | exit code 1 반환 |
| daemon stop | `"daemon stopped"` |

### 실패 시 확인사항

- 데몬 시작 실패 → 포트 18471이 이미 사용 중인지 확인
- Shell에서 응답 없음 → `sap daemon status`로 데몬 상태 확인
- Shell 연결 실패 → 방화벽이 localhost 소켓을 차단하는지 확인

---

## 검증 결과 기록 양식

각 시나리오 수행 후 아래 형식으로 결과를 기록합니다.

```
시나리오: [번호] [이름]
일시: YYYY-MM-DD HH:MM
환경: SAP GUI [버전], SAP 시스템 [SID], Python [버전]
검증자: [이름]
결과: PASS / FAIL / PARTIAL

성공 항목:
- [항목1]
- [항목2]

실패 항목:
- [항목]: [에러 메시지 또는 증상]

비고:
- [특이사항]
```
