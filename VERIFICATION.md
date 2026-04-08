# SAP CLI 사내 검증 시나리오

SAP 실환경에서 수행하는 통합 검증 가이드.
각 시나리오는 **사전 조건 → 실행 단계 → 기대 결과 → 실패 시 확인사항** 구조로 작성.

---

## 사전 조건 (모든 시나리오 공통)

- Windows 10/11, SAP GUI 7.70+ 설치
- SAP GUI Scripting 활성화 (서버/클라이언트 모두)
- SAP 시스템에 로그인 완료 상태
- 가상환경 생성 및 활성화: `python -m venv .venv && .venv\Scripts\activate`
- `pip install -e .` 설치 완료 (반드시 가상환경 내에서)
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

# 4. 입력 가능한 필드만 조회 (테이블 정보 포함)
sap screen --fields-only

# 5. 액션(버튼) 목록 조회
sap screen --actions-only

# 6. 패턴으로 화면 요소 검색 (필드/버튼/탭/테이블 부분 일치)
sap screen --search "Enter"
# → ID, 이름, 라벨, 텍스트, 시맨틱 동의어 모두 검색

# 7. 상태바 메시지 확인
sap status
```

### 기대 결과

| 단계 | 확인 항목 |
|------|----------|
| connect | `"connected": true` + 세션 정보(user, system_name, client) |
| session | user, language, transaction, system_name 필드 존재 |
| screen | transaction, screen_number, fields, buttons 포함된 JSON |
| screen -f | changeable_fields 배열에 1개 이상 필드 + tables 배열 (테이블이 있는 화면) |
| screen -a | available_actions 배열에 1개 이상 액션 |
| screen -s "키워드" | search, total_matches, 매칭된 fields/buttons/tabs/tables 반환 |
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
# → 자재번호 필드 + 테이블 정보가 함께 출력됨

# 3a. 필드를 패턴으로 검색 (ID, 이름, 라벨, 시맨틱 부분 일치)
sap screen --search "MATNR"
# → MATNR 관련 필드만 필터링하여 출력
sap screen --search "자재"
# → 한글 라벨/시맨틱 동의어로도 검색 가능

# 4. 자재번호 입력
sap set "wnd[0]/usr/ctxtRMMG1-MATNR" "자재번호"

# 5. 입력 확인
sap get "wnd[0]/usr/ctxtRMMG1-MATNR"
# → 입력한 값이 반환되어야 함

# 6. Enter (뷰 선택 화면으로 이동)
sap vkey 0

# 7. 상태 확인 — 에러가 없는지 체크
sap status

# 8. 뷰 선택 처리 — 팝업 또는 일반 화면 두 가지 경우가 있음
sap modal
# → has_modal: true면 팝업 방식, false면 일반 화면 방식

# 8a. 팝업 방식 (has_modal: true)
#     sap screen/vkey는 모달(wnd[1])을 자동 감지하므로 별도 처리 불필요
sap screen --fields-only
# → 팝업 안의 필드/테이블/체크박스가 보임 (wnd[1] 자동 스캔)
# → tables 항목에 뷰 선택 테이블 ID 표시 (예: wnd[1]/usr/tblSAPLMGMMTC_VIEW)

# 8a-1. 테이블에서 뷰 검색
sap screen --search "VIEW"
# → 뷰 선택 테이블 ID 확인

# 8a-2. 테이블 내용 읽기 (어떤 뷰가 있는지 확인)
sap table "wnd[1]/usr/tblSAPLMGMMTC_VIEW"
# → rows에 MSICHTAUSW-DYTXT 항목들 (Basic Data 1, MRP 1 등)

# 8a-3. 원하는 뷰 행 선택
sap table-select "wnd[1]/usr/tblSAPLMGMMTC_VIEW" 0
# → 첫 번째 행 (Basic Data 1) 선택

# 8a-4. 체크박스가 별도로 있는 경우 (시스템에 따라 다름)
sap set "wnd[1]/usr/chkUSRM1-AAUSW" "true"
# → /chk 접두사 자동 감지하여 체크박스 처리

# 8a-5. Enter로 진입
sap vkey 0
# → 팝업에 Enter 전송 (wnd[1] 자동 감지)

# 8b. 일반 화면 방식 (has_modal: false, SAP HANA 등)
#     뷰 선택이 일반 화면(예: screen 70)으로 나타나는 경우
sap screen --search "Select"
# → "Select View" 같은 버튼 ID 확인
sap press "해당_버튼_ID"
# → 뷰 선택 후 Enter

# 9. 자재 기본데이터 화면 확인
sap screen --fields-only
# → MARA-MAKTX(자재내역), MARA-MEINS(기본단위) 등 필드 + 테이블 정보
# 참고: SAP 버전에 따라 필드 ID가 다를 수 있음. 실제 출력 기준으로 판단

# 9a. 특정 필드 검색
sap screen --search "MAKTX"
# → 자재 내역 필드 ID/값 확인

# 10. 특정 필드값 읽기 (단건)
sap get "wnd[0]/usr/txtMARA-MAKTX"
# → {"name": "MARA-MAKTX", "field": "wnd[0]/usr/txtMARA-MAKTX", "value": "자재내역"}

# 10a. 여러 필드 한번에 읽기 (다건)
sap get "wnd[0]/usr/txtMARA-MAKTX" "wnd[0]/usr/txtMARA-MEINS"
# → [
#     {"name": "MARA-MAKTX", "field": "wnd[0]/usr/txtMARA-MAKTX", "value": "자재내역"},
#     {"name": "MARA-MEINS", "field": "wnd[0]/usr/txtMARA-MEINS", "value": "EA"}
#   ]
# name은 SAP 요소의 Name 속성에서 자동 추출

# 11. 뒤로가기
sap vkey 3
sap vkey 3
```

### 기대 결과

| 단계 | 확인 항목 |
|------|----------|
| run MM03 | `"executed": "MM03"` |
| screen --search "MATNR" | 자재번호 필드가 검색됨 (fields에 1건 이상) |
| set 자재번호 | `"value_set": "자재번호"` |
| get 자재번호 | 입력한 값과 일치 |
| vkey 0 (Enter) | 에러 없이 화면 이동 |
| status | message_type이 E가 아닌 것 |
| modal 감지 | 팝업이 떴으면 has_modal: true, screen/vkey가 wnd[1] 자동 타겟 |
| screen -f (팝업) | 팝업 안의 테이블/체크박스가 보임 (tables 항목 포함) |
| table (뷰 목록) | rows에 뷰 이름(MSICHTAUSW-DYTXT) 목록 반환 |
| table-select | `"selected": true` + 원하는 행 선택 |
| set chk (체크박스) | /chk 자동 감지하여 체크박스 on/off |
| screen -f (기본데이터) | 자재 관련 필드 5개 이상 |
| get 단건 | name, field, value 포함된 JSON 객체 |
| get 다건 | name, field, value 포함된 JSON 배열 |
| vkey 3 (Back) | MM03 초기 화면 또는 이전 화면으로 복귀 |

### 실패 시 확인사항

- `"자재가 존재하지 않습니다"` → 실제 존재하는 자재번호로 변경
- vkey 0 후 화면이 안 바뀜 → `sap status` 확인 (필수 입력 누락 가능)
- 필드 ID가 다름 → `sap screen --search "키워드"`로 패턴 검색하여 실제 ID 확인
- 뷰 선택 팝업 없이 바로 진입 → 사용자 설정에 따라 팝업 생략될 수 있음 (정상)
- **팝업인데 screen에 안 잡힘** → com_wrapper가 wnd[1] 자동 감지. `sap modal`로 먼저 확인
- **테이블 안 체크박스가 안 보임** → `sap screen` 전체 출력에서 fields 항목 확인 (테이블 내부도 스캔됨)
- **체크박스 set 실패** → element_id에 `/chk` 포함되어야 자동 감지. `sap screen --search "chk"` 로 확인
- **table-select 실패** → `sap table "테이블ID"`로 먼저 행 수 확인, row 번호가 범위 내인지 확인
- **SAP HANA 버전에서 화면번호가 다름** → 시드 데이터(4004)와 실제(70 등)가 다를 수 있음. `sap learn`으로 시드 갱신
- **팝업인데 screen에 안 잡힘** → com_wrapper가 wnd[1] 자동 감지. `sap modal`로 먼저 확인
- **SAP HANA 버전에서 화면번호가 다름** → 시드 데이터(4004)와 실제(70 등)가 다를 수 있음. `sap session`으로 실제 화면번호 확인 후 `sap learn`으로 시드 갱신

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
sap record-start mm03_lookup

# 3. 녹화 상태 확인
sap record-status
# → recording: true, steps_count: 0

# 4. 트랜잭션 실행 (녹화됨)
sap record-step run MM03

# 5. 자재번호 입력 (녹화됨) — 변수로 지정 (예: 100-100)
sap record-step set "wnd[0]/usr/ctxtRMMG1-MATNR" "100-100"

# 6. Enter 전송 (녹화됨)
sap record-step vkey 0

# 7. 뷰 선택 팝업에서 테이블 행 선택 (녹화됨, 팝업이 뜬 경우만)
sap record-step table-select "wnd[1]/usr/tblSAPLMGMMTC_VIEW" 0

# 8. 뷰 선택 Enter (녹화됨)
sap record-step vkey 0

# 9. 현재 녹화 상태 확인
sap record-status
# → steps_count: 5 (또는 4)

# 10. 녹화 종료 및 저장 (--vars의 값은 5번에서 실제 입력한 값과 동일해야 함)
sap record-finish --name mm03_lookup --desc "MM03 자재 조회" --vars '{"matnr": "100-100"}'

# 11. 뒤로가기 (SAP 상태 복원)
sap vkey 3
sap vkey 3
```

### Phase B: 매크로 확인

```powershell
# 12. 저장된 매크로 목록
sap macro-list

# 13. 매크로 상세 조회 (macro-list에서 확인한 ID 사용)
sap macro-show MACRO_ID
# → steps, variables, description 확인
```

### Phase C: 매크로 재생

```powershell
# 14. 드라이런 (실제 실행 없이 계획만 확인)
sap macro-play MACRO_ID --vars '{"matnr": "200-200"}' --dry-run
# → 각 단계와 대입될 변수값 확인

# 15. 실제 재생 (다른 자재번호로)
sap macro-play MACRO_ID --vars '{"matnr": "200-200"}'

# 16. 결과 확인 — 200-200의 기본데이터 화면에 도달
sap screen --fields-only
sap get "wnd[0]/usr/txtMARA-MAKTX"
# → 200-200의 자재 내역이 반환되어야 함

# 17. 정리
sap vkey 3
sap vkey 3
```

### 기대 결과

| 단계 | 확인 항목 |
|------|----------|
| record-start | `"recording": true` |
| record-step (각각) | `"recorded": true` + SAP 화면 실제 변경 |
| record-step table-select | 뷰 선택 팝업에서 테이블 행 선택 성공 |
| record-status | steps_count가 녹화한 단계 수와 일치 |
| record-finish | `"saved": true` + macro_id, path 반환 |
| macro-list | 방금 저장한 매크로가 목록에 존재 |
| macro-show | steps 배열에 녹화한 단계들, variables에 matnr |
| macro-play --dry-run | 각 단계 + 변수 대입 결과 표시 |
| macro-play (실행) | 자재번호2 기본데이터 화면에 도달 |

### 실패 시 확인사항

- `record-finish` 실패 → `record-status`로 녹화 상태 확인, `record-cancel` 후 재시도
- `macro-play` 중 에러 → 녹화 시 화면 상태와 재생 시 화면 상태가 다를 수 있음 (같은 초기 화면에서 시작)
- 변수 대입이 안 됨 → `macro-show`에서 variables 필드 확인, `record-finish`의 --vars 형식 확인 (JSON: `'{"key": "설명"}'`)
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

## 시나리오 7: Transcript / Replay Review

### 목표

AI가 실행한 SAP 조작이 transcript로 남고, verifier가 이를 read-only로 검토할 수 있는지 확인.
또한 이 기록이 온톨로지와 충돌하지 않고, 실제 실행 기억으로 보완되는지 확인.

### 사전 준비

- 시나리오 2 또는 시나리오 5를 통해 최소 1회 이상 SAP 조작 수행
- `~/.sap_cli/transcripts/` 경로에 JSONL 파일이 생성되어 있어야 함
- 가능하면 정책 파일의 `active_agent`를 `verifier`로 설정한 상태도 함께 확인

### 실행 단계

```powershell
# 1. 최근 transcript 조회
sap transcript --limit 10

# 2. 승인 필요/에러 케이스만 조회
sap transcript --outcome approval_required
sap transcript --outcome error

# 3. 특정 명령에 대한 재실행 검토 계획 생성
sap transcript-plan --command set --index 0

# 4. MCP 환경에서 verifier agent로 같은 조회 수행
# Claude Code:
# "최근 transcript 보여줘"
# "가장 최근 set 단계 다시 실행해도 되는지 검토해줘"
```

### 기대 결과

| 단계 | 확인 항목 |
|------|----------|
| transcript 조회 | before/after transaction, screen, status 요약 확인 |
| transcript-plan | `risk_level`, `replayable`, `ontology_context`, `preconditions`, `proposed_sequence` 반환 |
| verifier profile | `sap_transcript_query`, `sap_transcript_plan`, `sap_screen`, `sap_get_status`는 보이고 mutating tool은 숨겨짐 |
| 승인 케이스 | `approval_required: true` 또는 위험도 `high`로 표시 |

### 실패 시 확인사항

- transcript가 비어 있음 → 실제 SAP 조작이 최소 1회 이상 실행됐는지 확인
- transcript-plan이 항목을 못 찾음 → `sap transcript --limit 20`으로 먼저 필터 조건 확인
- verifier에서 mutating tool이 보임 → `~/.sap_cli/policy.yaml`의 `active_agent` 값과 MCP 서버 재시작 여부 확인

---

## 시나리오 8: 업무 프로세스 조회 및 관리

### 목표

등록된 업무 프로세스(BusinessProcess)를 조회하고, 새 프로세스를 생성/삭제하는 CRUD를 검증.

### 사전 준비

- 시나리오 1이 성공한 상태 (SAP 연결 불필요, 온톨로지만 사용)
- 시드 데이터 확인: `~/.sap_cli/knowledge/processes/` 에 JSON 파일 존재

### 실행 단계

```powershell
# 1. 프로세스 목록 조회
sap process list
# → procurement_basic, sales_to_billing 2개 반환

# 2. 조달 프로세스 상세 조회
sap process show procurement_basic
# → steps 4개 (MM01→ME21N→MIGO→MIRO), data_flows 3개

# 3. 판매 프로세스 상세 조회
sap process show sales_to_billing
# → steps 3개 (VA01→VL01N→VF01), data_flows 2개

# 4. 프로세스 구조 검증 — 데이터 흐름 확인
# procurement_basic:
#   step0.MATNR → step1.MATNR (자재번호)
#   step1.EBELN → step2.EBELN (구매오더번호)
#   step2.MBLNR → step3.MBLNR (입고문서번호)

# 5. 온톨로지 목록에서 프로세스도 보이는지 확인
sap ontology --list-all
# → transactions, patterns, processes 모두 반환

# 6. 새 프로세스 생성 테스트 (MCP 환경에서)
# Claude Code: "자재 변경 프로세스를 만들어줘. MM02로 변경하고 MM03으로 확인하는 2스텝"
# 기대: sap_process_create 호출

# 7. 생성된 프로세스 확인
sap process list
sap process show <생성된_id>

# 8. 프로세스 삭제 테스트
sap process delete <생성된_id>
sap process list
# → 삭제된 프로세스가 목록에서 사라짐
```

### 기대 결과

| 단계 | 확인 항목 |
|------|----------|
| process list | count: 2, id/name/module/steps_count/tags 필드 존재 |
| process show (조달) | steps 4개, data_flows 3개, 스텝별 tcode/description 확인 |
| process show (판매) | steps 3개, data_flows 2개 |
| data_flows | source_step < target_step, source_field/target_field 정확 |
| ontology --list-all | processes 배열에 2개 포함 |
| process create | saved: true, process_id 반환, 무결성 검증 통과 |
| process delete | deleted: true, 목록에서 제거 |

### 실패 시 확인사항

- process list가 비어있음 → `~/.sap_cli/knowledge/processes/` 디렉토리에 JSON 파일 존재 확인
- 시드 데이터 없음 → 패키지 내 `sap_cli/ontology/knowledge/processes/` 에서 복사되었는지 확인
- process create에서 무결성 에러 → 중복 step_number, 역방향 data_flow, 존재하지 않는 step 참조 확인

---

## 시나리오 9: 업무 프로세스 실행 (ProcessExecutor)

### 목표

조달 프로세스를 ProcessExecutor로 실행하면서 스텝 간 데이터 흐름이 자동 연결되는지 검증.
AI 에이전트가 각 스텝을 수동 실행하고, 결과(문서번호)를 보고하면 다음 스텝 입력값이 자동으로 계산되는지 확인.

### 사전 준비

- 시나리오 1, 8이 성공한 상태
- SAP 시스템에 자재 생성(MM01) 권한이 있어야 실제 실행 가능
- 권한이 없으면 Phase A(데이터 흐름 검증)만으로도 핵심 검증 가능

### Phase A: 프로세스 실행 시작 및 데이터 흐름 검증 (SAP 없이 가능)

```powershell
# 1. 조달 프로세스 실행 시작
sap process start procurement_basic
# → status: running, current_step: 0
# → pending_inputs: {MTART: "ROH", MBRSH: "M"} (고정 입력)

# 2. 현재 상태 확인
sap process status
# → current_step: 0, current_tcode: MM01
# → pending_inputs: 고정 입력값
# → context: {} (아직 비어있음)

# 3. Step 0 완료 보고 (MM01 — 자재 생성 결과)
sap process step 0 --status success --outputs '{"MATNR": "100-100"}'
# → context에 step0.MATNR = "100-100" 저장

# 4. 다음 스텝 입력값 확인 — 핵심 검증 포인트
sap process status
# → current_step: 1, current_tcode: ME21N
# → pending_inputs: {MATNR: "100-100"}  ← DataFlow 자동 연결!

# 5. Step 1 완료 보고 (ME21N — 구매오더 생성)
sap process step 1 --status success --outputs '{"EBELN": "4500012345"}'

# 6. 다음 스텝 입력값 확인
sap process status
# → current_step: 2, current_tcode: MIGO
# → pending_inputs: {EBELN: "4500012345"}  ← DataFlow 자동!

# 7. Step 2 완료 보고 (MIGO — 입고)
sap process step 2 --status success --outputs '{"MBLNR": "5000000001"}'

# 8. 다음 스텝 입력값 확인
sap process status
# → current_step: 3, current_tcode: MIRO
# → pending_inputs: {MBLNR: "5000000001"}

# 9. Step 3 완료 보고 (MIRO — 송장검증)
sap process step 3 --status success --outputs '{"BELNR": "5100000001"}'
# → 마지막 스텝이므로 자동 완료
# → saved_to: ~/.sap_cli/executions/procurement_basic_...json
# → status: completed
```

### Phase B: 실제 SAP 연동 실행 (SAP 환경 필요)

```powershell
# 1. 프로세스 시작
sap process start procurement_basic

# 2. 상태 확인 → pending_inputs 보고 고정 입력값 확인
sap process status

# 3. Step 0: MM01 실행
sap run MM01
sap screen --fields-only
# → 필드 ID 확인 후 입력
sap set "wnd[0]/usr/..." "ROH"    # MTART
# ... 나머지 필드 입력 ...
sap vkey 11                        # 저장
sap status
# → "Material 100-XXX created" 같은 상태 메시지 확인
# → 자재번호 추출

# 4. Step 0 완료 보고
sap process step 0 --status success --outputs '{"MATNR": "추출한자재번호"}'

# 5. 다음 입력 확인
sap process status
# → pending_inputs에 MATNR이 자동으로 들어있음

# 6. Step 1: ME21N 실행 → pending_inputs의 MATNR 사용
sap run ME21N
# ... MATNR, LIFNR, 수량 등 입력 → 저장
sap status
# → "Standard PO 4500XXXXXX has been created"

# 7. Step 1 완료 보고
sap process step 1 --status success --outputs '{"EBELN": "추출한PO번호"}'

# 8. Step 2: MIGO 실행 → pending_inputs의 EBELN 사용
sap run MIGO
# ... 구매오더번호 입력 → Check → Post
sap status

# 9. Step 2 완료 보고
sap process step 2 --status success --outputs '{"MBLNR": "추출한입고문서번호"}'

# 10. Step 3: MIRO 실행 → pending_inputs의 MBLNR 사용
sap run MIRO
# ... 구매오더번호/금액 입력 → 저장
sap status

# 11. Step 3 완료 → 자동 저장
sap process step 3 --status success --outputs '{"BELNR": "추출한전표번호"}'
# → saved_to 경로 확인
```

### Phase C: MCP 환경에서 AI 자율 실행

Claude Code 대화창에서:

```
조달 프로세스 실행해줘. 자재번호 ROH, 공급업체 1000, 구매조직 1000으로.
```

기대: Claude가 아래 순서로 도구를 호출:
1. `sap_process_show` — 프로세스 구조 확인
2. `sap_process_start` — 실행 시작
3. `sap_run(MM01)` → 필드 입력 → 저장 → `sap_process_step(0, outputs)`
4. `sap_process_status` — 다음 입력 확인
5. `sap_run(ME21N)` → pending_inputs 기반 입력 → 저장 → `sap_process_step(1, outputs)`
6. ... 반복 → 완료

### Phase D: 에러 및 스킵 케이스

```powershell
# 에러 케이스
sap process start procurement_basic
sap process step 0 --status error --error "자재유형이 유효하지 않습니다"
sap process status
# → failed_steps: 1, status: running (계속 가능)

# Optional 스텝 스킵 (MIRO가 optional인 프로세스를 별도 정의한 경우)
sap process step 2 --status skipped --error "입고 불필요"
```

### Phase E: 실행 기록 확인

```powershell
# 실행 기록 파일 확인
ls ~/.sap_cli/executions/
# → procurement_basic_20260402T... .json

# 기록 내용 확인 (JSON)
# → process_id, started_at, finished_at, status,
#    context (모든 스텝 출력값), step_results (각 스텝 결과)
```

### 기대 결과

| 단계 | 확인 항목 |
|------|----------|
| process start | status: running, current_step: 0 |
| process status | pending_inputs에 고정값 + DataFlow 값 정확히 표시 |
| process step (성공) | context에 outputs 저장, current_step 증가 |
| DataFlow 자동 연결 | step0.MATNR → step1의 pending_inputs에 MATNR 자동 표시 |
| 체인 연결 | step1.EBELN → step2, step2.MBLNR → step3 순차 전달 |
| 마지막 스텝 완료 | status: completed, saved_to 경로 반환 |
| 실행 기록 | JSON에 process_id, context, step_results, timestamps 포함 |
| 에러 보고 | status: error인 스텝은 step_results에 error 메시지 포함 |

### 실패 시 확인사항

- "실행 중인 프로세스가 없습니다" → `sap process start`를 먼저 호출했는지 확인
- pending_inputs가 비어있음 → `sap process show`로 data_flows 정의 확인
- 실행 기록이 안 만들어짐 → `~/.sap_cli/executions/` 디렉토리 존재 확인
- MCP에서 도구가 안 보임 → TOOL_PERMISSION_MAP에 process 도구가 등록되어 있는지 확인
- 상태바에서 문서번호 추출 실패 → `sap status`로 원문 확인 후 수동으로 outputs에 입력

---

## 시나리오 10: 시드 트랜잭션 온톨로지 검증

### 목표

시드로 제공되는 7개 트랜잭션 온톨로지가 실제 SAP 화면과 일치하는지 검증.

### 대상 트랜잭션

| 트랜잭션 | 모듈 | 시드 화면 | 핵심 필드 |
|----------|------|----------|----------|
| MM03 | MM | 4000, 4004, 4200 | MATNR, WERKS, LGORT |
| ME21N | MM | 1 | BSART, LIFNR, EKORG, MATNR, MENGE |
| MIGO | MM | 1 | ACTION, REFDOC, EBELN |
| MIRO | MM | 1 | BLDAT, WRBTR, EBELN |
| VA01 | SD | 4001, 4002 | AUART, VKORG, VTWEG, MATNR |
| VL01N | SD | 4000 | VSTEL, VBELN |
| VF01 | SD | 1000 | VBELN, FKDAT, FKART |

### 실행 단계

```powershell
# 각 트랜잭션에 대해 반복:
sap connect
sap run [TCODE]

# 1. 실제 화면의 필드 목록
sap screen --fields-only

# 2. 시드 데이터의 필드 목록
sap ontology --tcode [TCODE]

# 3. 비교
# 시드의 필드 ID가 실제 화면과 일치하는지
# changeable, required 속성이 맞는지
# max_length가 맞는지

# 4. 불일치 시 실제 화면을 학습하여 시드 갱신
sap learn
sap ontology --tcode [TCODE]

# 5. 뒤로가기
sap vkey 3
```

### 기대 결과

| 확인 항목 | 성공 기준 |
|----------|----------|
| 필드 ID 일치 | 시드의 element ID가 실제 화면과 동일 |
| 필드 속성 일치 | changeable, required, max_length가 실제와 일치 |
| 화면번호 일치 | 시드의 screen_number가 실제와 동일 |
| 프로그램명 일치 | 시드의 program이 실제와 동일 |

### 실패 시 확인사항

- 필드 ID가 다름 → SAP 시스템의 커스터마이징에 따라 달라질 수 있음. `sap learn`으로 해당 시스템에 맞게 갱신
- 화면번호가 다름 → Enjoy 트랜잭션(ME21N 등)은 SAP 버전에 따라 다를 수 있음
- 필드가 존재하지 않음 → 시스템 설정/권한에 따라 일부 필드가 숨겨질 수 있음
- required 불일치 → 시스템 커스텀(SHD0 등)에 따라 필수 여부가 달라질 수 있음

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
