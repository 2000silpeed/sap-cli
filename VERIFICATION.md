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
sap ontology MM03
# → 학습된 화면 정보가 반환

# 5. 필드 검색 테스트
sap ontology MM03 --field "자재"
# → 자재 관련 필드 정보

# 6. 저장된 온톨로지 전체 목록
sap ontology --list
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
| ontology MM03 | 학습된 화면 구조 JSON (fields, buttons, screen_flow 등) |
| ontology MM03 --field | 검색어에 매칭되는 필드 정보 |
| ontology --list | transactions 배열에 MM03 포함 |

### 실패 시 확인사항

- `learn` 시간이 오래 걸림 → 화면 요소가 많은 경우 정상 (10초 이내)
- fields_count: 0 → SAP GUI 버전 확인, 스크립팅 API로 요소 접근 가능한지 확인
- ontology 조회 시 빈 결과 → `~/.sap_cli/ontology/` 디렉토리에 JSON 파일 생성 여부 확인
- 온톨로지 파일 위치: `~/.sap_cli/ontology/transactions/` 디렉토리

---

## 시나리오 4.5: fill 명령 + 스크린샷 캡처

### 목표

`sap fill`로 여러 필드를 한번에 입력하고, `sap capture`로 단계별 스크린샷을 캡처.

### 실행 단계

```powershell
# 1. SAP 연결
sap connect

# 2. MM03 트랜잭션 실행
sap run MM03

# 3. 초기 화면 스크린샷
sap capture 01_MM03_초기화면

# 4. 필드명으로 한번에 입력 (필드명 자동 매핑 + Enter)
sap fill MATNR=자재번호
# → MATNR을 화면에서 자동 검색하여 element_id 매핑
# → 값 세팅 후 자동 Enter

# 5. 뷰 선택 후 기본데이터 화면으로 이동
sap vkey 0

# 6. 기본데이터 화면 스크린샷
sap capture 02_기본데이터화면

# 7. 저장된 스크린샷 목록 확인
sap captures MM03

# 8. Enter 없이 값만 세팅 (--no-enter)
sap vkey 3
sap vkey 3
sap run MM03
sap fill MATNR=자재번호 --no-enter
sap capture 03_입력후_엔터전

# 9. 정리
sap vkey 3
```

### 기대 결과

| 단계 | 확인 항목 |
|------|----------|
| capture (초기) | `"captured": true` + path에 PNG 파일 경로 |
| fill MATNR | `"filled": 1` + `"enter_sent": true` + results에 매핑된 element_id |
| fill 필드 못 찾음 | `"errors": ["필드를 찾을 수 없습니다: XXX"]` |
| fill --no-enter | `"enter_sent": false` |
| capture (기본데이터) | 다른 파일명으로 두 번째 PNG 저장 |
| captures MM03 | screenshots 배열에 캡처한 파일 목록 |

### 실패 시 확인사항

- `fill` 시 필드를 찾을 수 없음 → `sap screen --fields-only`로 실제 필드명 확인. `sap learn` 불필요 (현재 화면을 실시간 스캔)
- `capture` 시 SAP 윈도우를 찾을 수 없음 → SAP GUI가 최소화되어 있지 않은지 확인
- 스크린샷 파일이 안 생김 → `~/.sap_cli/screenshots/` 디렉토리 확인, Pillow 설치 확인 (`pip install Pillow`)
- 스크린샷 위치: `~/.sap_cli/screenshots/{tcode}/` 디렉토리

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
sap ontology --list
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
| ontology --list | processes 배열에 2개 포함 |
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
sap ontology [TCODE]

# 3. 비교
# 시드의 필드 ID가 실제 화면과 일치하는지
# changeable, required 속성이 맞는지
# max_length가 맞는지

# 4. 불일치 시 실제 화면을 학습하여 시드 갱신
sap learn
sap ontology [TCODE]

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

## 경영진 데모 시나리오: 매크로 녹화/재생 + 스크린샷 설명서

### 목적

경영진에게 SAP 반복 업무 자동화의 가치를 **5분 이내**에 보여주는 시나리오.
"사람이 매일 10분 걸리는 작업을 AI가 30초에 끝내고, 설명서까지 자동 생성한다"를 시연.

### 데모 스토리

> "MM03 자재 조회를 매크로로 녹화합니다. 녹화한 매크로를 다른 자재번호로 재생하면서
> 각 단계를 스크린샷으로 남깁니다. 최종적으로 사용자 설명서 소재가 자동 생성됩니다."

### 사전 준비

- SAP GUI 로그인 완료, `sap connect` 성공 상태
- 조회할 자재번호 2개 준비 (녹화용 A, 재생용 B)
- 터미널 + SAP GUI 화면이 동시에 보이도록 배치 (듀얼 모니터 권장)

---

### Phase 1: 매크로 녹화 (수동 → 자동화) [약 2분]

발표 멘트: *"지금부터 사람이 하는 SAP 작업을 그대로 녹화합니다."*

```powershell
# 1. 녹화 시작
sap record-start mm03_demo

# 2. MM03 실행 (녹화됨 + 실제 SAP 화면 이동)
sap record-step run MM03

# 3. 자재번호 입력 (녹화됨)
sap record-step set "wnd[0]/usr/ctxtRMMG1-MATNR" "자재번호A"

# 4. Enter (녹화됨)
sap record-step vkey 0

# 5. 뷰 선택에서 Enter (녹화됨)
sap record-step vkey 0

# 6. 녹화 상태 확인 — "4단계가 기록되었습니다"
sap record-status

# 7. 녹화 완료 + 변수 지정
sap record-finish --name mm03_demo --desc "MM03 자재 기본데이터 조회" --vars '{"자재번호A": "조회할 자재번호"}'
```

발표 멘트: *"4단계가 녹화되었고, 자재번호가 변수로 지정되었습니다. 이제 다른 자재번호로 재생해보겠습니다."*

---

### Phase 2: 매크로 재생 + 스크린샷 캡처 [약 2분]

발표 멘트: *"이번에는 다른 자재번호로 자동 실행하면서, 각 단계를 스크린샷으로 남깁니다."*

```powershell
# 8. 뒤로가기 (초기 상태로 복귀)
sap vkey 3
sap vkey 3

# 9. 재생 전 초기 화면 스크린샷
sap capture 01_재생전_초기화면

# 10. 매크로 재생 — 자재번호만 바꿔서 실행
sap macro-play MACRO_ID --vars '{"matnr": "자재번호B"}'
# → 4단계가 자동으로 빠르게 실행됨
# → SAP 화면이 자동으로 넘어가는 것을 보여줌

# 11. 재생 완료 후 결과 화면 스크린샷
sap capture 02_재생완료_결과화면

# 12. 실제 데이터 확인 — 자재번호B의 내역이 보이는지
sap screen --search "MAKTX"
sap get "wnd[0]/usr/txtMARA-MAKTX"
```

발표 멘트: *"자재번호만 바꿔서 4단계를 자동 실행했고, 결과 화면까지 도달했습니다."*

---

### Phase 3: fill 명령 데모 (한번에 입력) [약 30초]

발표 멘트: *"필드가 여러 개인 화면에서는 fill 명령으로 한번에 입력할 수도 있습니다."*

```powershell
# 13. 뒤로가기
sap vkey 3
sap vkey 3

# 14. 필드명으로 한번에 입력 + 자동 Enter
sap fill MATNR=자재번호A
# → "MATNR"이라는 이름으로 화면에서 필드를 자동 찾아서 입력 + Enter

# 15. 결과 스크린샷
sap capture 03_fill명령_결과

# 16. 정리
sap vkey 3
sap vkey 3
```

---

### Phase 4: 산출물 확인 [약 30초]

발표 멘트: *"자동으로 생성된 산출물을 확인합니다."*

```powershell
# 17. 매크로 목록
sap macro-list
# → mm03_demo 매크로가 보임

# 18. 스크린샷 목록
sap captures MM03
# → 3개 PNG 파일 경로 확인
# → 이 파일들이 사용자 설명서의 소재가 됨

# 19. 매크로 상세 — 단계별 액션 확인
sap macro-show MACRO_ID
# → steps: [run MM03, set MATNR, vkey 0, vkey 0]
# → variables: [{name: "matnr", description: "조회할 자재번호"}]
```

---

### 데모 핵심 메시지

| 보여주는 것 | 경영진이 느끼는 가치 |
|-------------|---------------------|
| 녹화 4단계 → 재생 자동 실행 | "매일 반복하는 SAP 작업을 한번 녹화하면 계속 재활용" |
| 변수 치환 (자재번호 교체) | "같은 작업을 다른 데이터로 바로 적용" |
| 스크린샷 자동 캡처 | "신입 교육용 설명서를 자동으로 만들 수 있음" |
| fill 한번에 입력 | "여러 필드를 한 줄로 입력 — 사람보다 빠르고 실수 없음" |
| 전체 5분 이내 | "학습 곡선 없이 바로 효과를 볼 수 있음" |

### 데모 실패 대비 체크리스트

| 확인 | 조치 |
|------|------|
| `sap connect` 실패 | SAP GUI 로그인 상태 + 스크립팅 활성화 확인 |
| `record-step` 시 화면 안 넘어감 | 자재번호가 실제 존재하는지 확인 |
| `record-finish` 시 vars 파싱 에러 | JSON 형식 확인 (`'{"key":"val"}'`) |
| `macro-play` 시 에러 | `macro-show`로 단계 확인, `--dry-run`으로 사전 검증 |
| `capture` 시 SAP 윈도우 못 찾음 | SAP GUI가 최소화되지 않았는지 확인 |
| `fill` 시 필드 못 찾음 | `sap screen --fields-only`로 실제 필드명 확인 |

### 데모 후 Q&A 대비

| 질문 | 답변 |
|------|------|
| "다른 트랜잭션도 가능한가?" | "네, 모든 SAP 트랜잭션에 동일하게 적용됩니다. VA01 수주, ME21N 구매오더 등." |
| "얼마나 빨라지나?" | "수동 10분 → 매크로 30초. 대량 처리 시 10건 기준 100분 → 5분." |
| "에러가 나면?" | "각 단계마다 SAP 상태바를 확인하고, 에러 시 즉시 중단하여 보고합니다." |
| "보안은?" | "ReBAC 권한 관리로 에이전트별 실행 범위를 제한합니다. 모든 조작이 transcript에 기록됩니다." |
| "AI가 필요한가?" | "CLI만으로도 자동화 가능하고, Claude AI를 붙이면 자연어로 지시할 수 있습니다." |

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

---

## 시나리오 11: 트리 컨트롤 (SPRO 네비게이션)

### 목표

SPRO 등 트리 기반 화면에서 노드 읽기, 펼치기, 더블클릭, 버튼 클릭이 정상 작동하는지 확인.

### 사전 조건

- SAP 연결 완료 (`sap connect` 성공)
- SPRO 실행 가능한 권한

### 실행 단계

```powershell
# Step 1: SPRO 진입
sap run SPRO

# → 기대: SPRO 초기 화면 (SAP Reference IMG)
sap status
# → 기대: 에러 없음
```

```powershell
# Step 2: 화면 구조 확인 — trees 배열에서 트리 ID 확인
sap screen
# → 기대: trees 배열에 트리 ID가 포함됨
# → 메모: 트리 ID를 복사해둘 것 (예: "wnd[0]/shellcont/shell")
```

```powershell
# Step 3: 트리 노드 읽기 (얕은 조회)
sap tree "<트리ID>" --depth 0
# → 기대: 루트 노드 목록이 출력됨
# → 확인: 각 노드에 key, text, level이 있는지
# → 메모: 루트 노드의 key를 복사 (예: "00001")
```

```powershell
# Step 4: 루트 노드 펼치기
sap tree-expand "<트리ID>" "<루트key>"
# → 기대: 에러 없이 완료

# Step 5: 펼친 후 자식 노드 확인
sap tree "<트리ID>" --node-key "<루트key>" --depth 1
# → 기대: 하위 노드 목록이 나옴 (text가 비어있지 않은지 확인)
# → 메모: 자식 노드 중 하나의 key를 복사
```

```powershell
# Step 6: 한 단계 더 펼치기
sap tree-expand "<트리ID>" "<자식key>"
sap tree "<트리ID>" --node-key "<자식key>" --depth 1
# → 기대: 더 깊은 하위 노드가 나옴
```

```powershell
# Step 7: 노드 선택
sap tree-select "<트리ID>" "<자식key>"
# → 기대: 에러 없이 완료

# Step 8: 노드 더블클릭 (IMG Activity 진입)
sap tree-doubleclick "<트리ID>" "<리프노드key>"
# → 기대: 새 화면으로 전환되거나 설정 화면이 열림
sap screen --fields-only
# → 기대: 설정 화면의 필드가 표시됨
```

```powershell
# Step 9: 뒤로 가기
sap vkey 3
# → 기대: SPRO 트리 화면으로 복귀
```

```powershell
# Step 10: 트리 버튼 클릭 (SPRO 실행 아이콘)
# SPRO 트리 컬럼: '2'=IMG Activity 실행, '1'=Documentation
sap tree-press "<트리ID>" "<리프노드key>" "2"
# → 기대: IMG Activity가 실행됨 (더블클릭과 동일한 효과)
sap vkey 3
```

```powershell
# Step 11: 노드 접기
sap tree-collapse "<트리ID>" "<루트key>"
sap tree "<트리ID>" --depth 0
# → 기대: 다시 루트 노드만 표시됨
```

### 검증 포인트

- [ ] 트리 ID가 sap screen에서 정상 확인됨
- [ ] 트리 노드에 text가 정상 출력됨 (비어있지 않음)
- [ ] tree-expand로 하위 노드가 펼쳐짐
- [ ] tree-doubleclick으로 화면 전환됨
- [ ] tree-press로 IMG Activity 실행됨
- [ ] tree-collapse로 접기 정상
- [ ] --depth, --node-key 옵션이 정상 작동

### 실패 시 확인사항

- 트리 text가 전부 비어있으면: Column Tree 컬럼 수집 문제 → `sap tree` 출력에서 items 확인
- tree-press에서 에러: 컬럼 이름이 '2'가 아닐 수 있음 → `sap tree` 출력의 items 키 확인
- 트리 ID를 못 찾음: `sap screen` 출력에서 trees 배열 확인, 없으면 shells 배열 확인

---

## 시나리오 12: AI 네비게이션 레시피 — 저장/검색/재활용

### 목표

레시피 저장 → 검색 → 다른 세션에서 재활용까지의 전체 사이클 검증.

### 사전 조건

- SAP 연결 완료
- 시나리오 11 (SPRO 트리) 완료 — 성공한 경로를 알고 있어야 함

### 실행 단계

```powershell
# Step 1: 레시피 목록 확인 (처음이면 빈 목록)
sap recipe-list
# → 기대: {"count": 0, "recipes": []}
```

```powershell
# Step 2: 시나리오 11에서 성공한 SPRO 경로를 레시피로 저장
# 예시: SPRO > Enterprise Structure > Definition > Logistics 경로
sap recipe-save "spro-logistics-definition" "SPRO > Enterprise Structure > Definition > Logistics" "[{\"tool\":\"sap_run\",\"args\":{\"tcode\":\"SPRO\"},\"hint\":\"SPRO 진입\"},{\"tool\":\"sap_tree_expand\",\"args\":{\"element_id\":\"<트리ID>\",\"node_key\":\"<key1>\"},\"hint\":\"SAP Reference IMG 펼치기\"},{\"tool\":\"sap_tree_expand\",\"args\":{\"element_id\":\"<트리ID>\",\"node_key\":\"<key2>\"},\"hint\":\"Enterprise Structure 펼치기\"},{\"tool\":\"sap_tree_doubleclick\",\"args\":{\"element_id\":\"<트리ID>\",\"node_key\":\"<key3>\"},\"hint\":\"Definition 진입\"}]" -k "enterprise structure,logistics,조직구조,물류"

# → 기대: {"saved": "spro-logistics-definition", "updated": false, "step_count": 4, "linked_tcodes": ["SPRO"]}
# → linked_tcodes에 "SPRO"가 포함되면 온톨로지 자동 연결 성공
```

```powershell
# Step 3: 레시피 목록 확인
sap recipe-list
# → 기대: count=1, 방금 저장한 레시피가 표시됨
# → 확인: description, keywords, step_count, context가 정상인지
```

```powershell
# Step 4: 키워드 검색
sap recipe-search "logistics"
# → 기대: 방금 저장한 레시피가 검색됨

sap recipe-search "조직구조"
# → 기대: 한글 키워드로도 검색됨

sap recipe-search "pricing"
# → 기대: 관련 없는 검색어이므로 결과 없음 (count=0)
```

```powershell
# Step 5: 온톨로지에서 레시피 연결 확인
sap ontology SPRO
# → 기대: recipes 배열에 "spro-logistics-definition"이 포함됨
# → 기대: recipe_details에 레시피 요약이 표시됨
```

```powershell
# Step 6: 레시피 업데이트 (같은 ID로 다시 저장)
sap recipe-save "spro-logistics-definition" "SPRO > Enterprise Structure > Definition > Logistics (updated)" "[{\"tool\":\"sap_run\",\"args\":{\"tcode\":\"SPRO\"},\"hint\":\"SPRO 진입\"}]" -k "enterprise structure,logistics,조직구조"
# → 기대: {"saved": "spro-logistics-definition", "updated": true, ...}
```

```powershell
# Step 7: 두 번째 레시피 저장 (같은 영역, 다른 목적)
sap recipe-save "spro-logistics-assignment" "SPRO > Enterprise Structure > Assignment > Logistics" "[{\"tool\":\"sap_run\",\"args\":{\"tcode\":\"SPRO\"},\"hint\":\"SPRO 진입\"}]" -k "enterprise structure,logistics,할당,assignment"
# → 기대: 두 번째 레시피 저장 성공
```

```powershell
# Step 8: 구분 검색 — 같은 영역에서 다른 레시피 찾기
sap recipe-search "logistics definition"
# → 기대: spro-logistics-definition이 상위에 표시

sap recipe-search "logistics assignment"
# → 기대: spro-logistics-assignment가 상위에 표시
```

```powershell
# Step 9: 레시피 삭제
sap recipe-delete "spro-logistics-assignment"
# → 기대: {"deleted": true, ...}

sap recipe-list
# → 기대: count=1, 삭제된 레시피는 사라짐
```

### 검증 포인트

- [ ] recipe-save로 레시피 저장 성공
- [ ] recipe-list에서 저장된 레시피 확인
- [ ] recipe-search로 키워드 검색 (한글/영문 모두)
- [ ] 온톨로지에 레시피 자동 연결 (linked_tcodes)
- [ ] ontology 조회 시 recipe_details 포함
- [ ] 같은 ID로 재저장 시 updated=true
- [ ] 같은 영역의 다른 레시피 구분 검색 가능
- [ ] recipe-delete로 삭제 정상

---

## 시나리오 13: MCP 서버에서 AI 레시피 활용 (Claude Code)

### 목표

Claude Code (MCP 연동)에서 AI가 레시피를 자동으로 활용하는지 확인.

### 사전 조건

- `pip install -e ".[mcp]"` 설치
- Claude Code settings.json에 MCP 서버 설정 완료
- SAP GUI 로그인 상태
- 시나리오 12에서 레시피 1개 이상 저장됨

### 실행 단계

```
# Step 1: Claude Code에서 SAP 연결
Claude에게: "SAP에 연결해줘"
# → 기대: sap_connect 호출 성공
```

```
# Step 2: 레시피가 있는 작업 요청
Claude에게: "SPRO에서 Enterprise Structure Definition Logistics 열어줘"
# → 기대:
#   1. AI가 먼저 sap_recipe_search("logistics definition") 호출
#   2. 레시피를 찾으면 steps를 따라 실행
#   3. sap_recipe_success 호출로 성공 기록
```

```
# Step 3: 레시피가 없는 새 작업 요청
Claude에게: "SPRO에서 판매조직 정의 화면 열어줘"
# → 기대:
#   1. AI가 sap_recipe_search("판매조직") 호출 → 결과 없음
#   2. AI가 직접 SPRO 트리 탐색 (tree_expand → tree_read 반복)
#   3. 목표 도달 후 sap_recipe_save 호출 → 경로 저장
#   4. context에 상세 맥락이 포함되는지 확인
```

```
# Step 4: 새 세션에서 저장된 레시피 재활용 확인
Claude Code 새 세션 시작
Claude에게: "SPRO에서 판매조직 정의 열어줘" (Step 3과 동일 요청)
# → 기대:
#   1. AI가 sap_recipe_search("판매조직") 호출 → 레시피 발견
#   2. 저장된 steps를 따라 빠르게 실행 (헤매지 않음)
#   3. 실행 시간이 Step 3보다 크게 단축됨
```

```
# Step 5: 레시피 성공 카운트 확인
sap recipe-list
# → 기대: 해당 레시피의 success_count가 1 이상
```

### 검증 포인트

- [ ] AI가 작업 전 sap_recipe_search를 자동 호출하는지
- [ ] 레시피가 있으면 steps를 따라 실행하는지
- [ ] 레시피가 없으면 직접 탐색 후 sap_recipe_save를 호출하는지
- [ ] context에 상세 맥락이 포함되는지
- [ ] 새 세션에서 레시피를 재활용하는지
- [ ] success_count가 증가하는지
- [ ] 2회차 실행이 1회차보다 빠른지

### 실패 시 확인사항

- AI가 recipe_search를 안 함: MCP 도구 설명에 안내가 있는지 확인 → sap_run, sap_tree_read 설명 확인
- AI가 recipe_save를 안 함: sap_recipe_save 도구 설명의 가이드 확인
- context가 비어있음: sap_recipe_save 설명의 context 작성 가이드 확인

---

## 시나리오 14: ALV Grid 읽기/더블클릭

### 목표

ALV Grid(GuiGridView)에서 테이블 데이터 읽기, 행 선택, 더블클릭이 정상 작동하는지 확인.

### 사전 조건

- SAP 연결 완료
- ALV Grid가 있는 트랜잭션 접근 가능 (예: SE16, MB52, ME2M 등)

### 실행 단계

```powershell
# Step 1: ALV Grid가 있는 트랜잭션 실행
sap run SE16
# → 기대: SE16 초기 화면

# Step 2: 테이블명 입력 (예: MARA)
sap fill TABLE=MARA
sap vkey 0
# → 기대: 선택 화면 또는 ALV Grid 표시
```

```powershell
# Step 3: 화면 구조 확인
sap screen
# → 기대: tables 배열에 ALV Grid ID가 있음
# → 메모: 테이블 ID 복사 (예: "wnd[0]/usr/cntlGRID1/shellcont/shell")
```

```powershell
# Step 4: ALV Grid 데이터 읽기
sap table "<ALV_ID>"
# → 기대: 행 데이터가 JSON으로 출력됨
# → 확인: row_count, column_count, rows, columns 모두 있는지
# → 확인: rows가 비어있지 않은지
```

```powershell
# Step 5: 테이블 행 선택
sap table-select "<ALV_ID>" 0
# → 기대: 첫 번째 행 선택 (에러 없음)
```

```powershell
# Step 6: 테이블 셀 더블클릭 (column 생략 — 첫 컬럼 자동 선택)
sap table-doubleclick "<ALV_ID>" 0
# → 기대: 디테일 화면으로 이동
sap screen --fields-only
# → 기대: 디테일 화면 필드가 표시됨
```

```powershell
# Step 7: 뒤로 가기
sap vkey 3
# → 기대: ALV Grid 화면으로 복귀
```

### 검증 포인트

- [ ] ALV Grid ID가 sap screen에서 확인됨
- [ ] sap table로 데이터 읽기 성공 (rows 비어있지 않음)
- [ ] table-select로 행 선택 성공
- [ ] table-doubleclick 시 column 생략해도 첫 컬럼 자동 선택
- [ ] 디테일 화면 전환 및 복귀 정상

### 실패 시 확인사항

- table-doubleclick 에러: ALV Grid 타입 확인 → GuiGridView인지 GuiShell(GridView)인지
- 데이터가 비어있음: VisibleRowCount가 0일 수 있음 → 화면을 활성화한 후 재시도
- 무한 대기: VisibleRowCount=0 방지 로직 확인 (com_wrapper.py)

---

## 시나리오 15: 통합 풀 사이클 (전체 기능 횡단)

### 목표

하나의 업무 흐름에서 CLI의 모든 주요 기능을 연속으로 사용하여 통합 안정성 확인.

### 시나리오: "새 자재 조회 → 화면 학습 → 매크로 녹화 → 레시피 저장"

```powershell
# ── Phase A: 기본 연결 + 조작 ──

# A1. 연결
sap connect
sap session
sap status

# A2. 트랜잭션 실행
sap run MM03

# A3. 필드 입력 (fill 명령)
sap fill MATNR=100-100
# → 기대: 자재번호 입력 + Enter

# A4. 모달 처리 (뷰 선택 팝업)
sap modal
sap screen --fields-only
sap table-select "wnd[1]/usr/tblSAPLMGMMTC_VIEW" 0
sap vkey 0

# A5. 화면 정보 읽기
sap screen --search "MAKTX"
sap get "wnd[0]/usr/txtMAKT-MAKTX"
sap status
```

```powershell
# ── Phase B: 온톨로지 학습 ──

# B1. 현재 화면 학습
sap learn
# → 기대: 화면 온톨로지가 저장됨

# B2. 학습 결과 확인
sap ontology MM03
# → 기대: screens, fields, recipe_details(있으면) 출력
```

```powershell
# ── Phase C: 매크로 녹화 ──

# C1. 뒤로 가기 (MM03 초기 화면으로)
sap vkey 3
sap vkey 3

# C2. 매크로 녹화 시작
sap record-start
sap record-status
# → 기대: {"recording": true, ...}

# C3. 단계 기록 (자재 조회 과정)
sap record-step run MM03
sap record-step set "wnd[0]/usr/ctxtRMMG1-MATNR" 100-100
sap record-step vkey 0

# C4. 녹화 종료
sap record-finish mm03-lookup "MM03 자재 조회"
# → 기대: 매크로 저장 성공

# C5. 매크로 확인
sap macro-list
sap macro-show mm03-lookup
```

```powershell
# ── Phase D: 스크린샷 ──

# D1. 현재 화면 캡처
sap capture "mm03-result"
# → 기대: 스크린샷 파일 저장

# D2. 캡처 목록
sap captures MM03
# → 기대: 방금 저장한 캡처가 목록에 표시
```

```powershell
# ── Phase E: 레시피 ──

# E1. 레시피 저장 (지금까지의 경로)
sap recipe-save "mm03-basic-lookup" "MM03 기본 자재 조회" "[{\"tool\":\"sap_run\",\"args\":{\"tcode\":\"MM03\"},\"hint\":\"MM03 실행\"},{\"tool\":\"sap_fill_fields\",\"args\":{\"fields\":{\"MATNR\":\"100-100\"}},\"hint\":\"자재번호 입력\"}]" -k "MM03,자재조회,material"

# E2. 검색 확인
sap recipe-search "자재조회"
# → 기대: 방금 저장한 레시피가 검색됨
```

```powershell
# ── Phase F: 데몬 모드 ──

# F1. 데몬 시작
sap daemon start
sap daemon status
# → 기대: {"daemon": "running", ...}

# F2. 데몬 경유 명령 실행 (속도 비교)
sap status
sap screen --fields-only

# F3. 데몬 종료
sap daemon stop
```

```powershell
# ── Phase G: 정리 ──

# G1. 매크로 삭제
sap macro-delete mm03-lookup

# G2. 레시피 삭제
sap recipe-delete "mm03-basic-lookup"

# G3. 최종 상태 확인
sap recipe-list
sap macro-list
```

### 검증 포인트

- [ ] Phase A: 연결 → 트랜잭션 → fill → 모달 → 필드 읽기 전체 성공
- [ ] Phase B: 온톨로지 학습 + 조회 성공
- [ ] Phase C: 매크로 녹화 → 저장 → 목록 확인 성공
- [ ] Phase D: 스크린샷 캡처 + 목록 확인 성공
- [ ] Phase E: 레시피 저장 + 검색 성공
- [ ] Phase F: 데몬 start/status/stop + 데몬 경유 명령 성공
- [ ] Phase G: 정리(삭제) 성공
- [ ] 전체 흐름에서 에러/크래시 없이 완주
