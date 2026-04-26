# Windows SAP GUI 실기 검증 체크리스트

Windows + SAP GUI COM 환경에서 `sap_cli`가 실제 SAP GUI Scripting을 안정적으로 제어하는지 확인하기 위한 현장 검증 문서입니다.

이 문서는 macOS/Linux 자동 테스트로 검증할 수 없는 항목을 사내 Windows PC에서 같은 순서로 반복 검증하기 위해 작성되었습니다.

---

## 1. 검증 범위

### 목표

- SAP GUI COM 연결, 화면 탐색, 필드 입력, 상태바 확인이 실제 Windows 환경에서 동작하는지 확인한다.
- 일반 table control, ALV Grid, tree control 등 SAP GUI 컨트롤별 읽기 동작을 확인한다.
- macro, session, recipe, MCP, daemon, multi-session 기능이 SAP GUI 실환경에서 끊기지 않는지 확인한다.
- 실패 시 원인이 SAP GUI 설정, 권한, 화면 차이, CLI 버그 중 어디에 가까운지 분류할 수 있게 한다.

### 비목표

- SAP 업무 데이터의 정합성 검증은 하지 않는다.
- 생성/변경성 트랜잭션을 기본 검증 대상으로 삼지 않는다.
- 운영 시스템에서 실제 저장, 전표 생성, 승인, 배치 실행을 수행하지 않는다.

---

## 2. 사전 조건

### Windows PC

- Windows 10/11
- SAP GUI for Windows 7.70 이상 권장
- Python 3.11 이상
- PowerShell 사용 가능
- 프로젝트 소스 체크아웃 완료

### SAP GUI 설정

- SAP GUI Scripting이 클라이언트에서 활성화되어 있어야 한다.
- SAP 서버 프로파일에서도 scripting이 허용되어 있어야 한다.
- SAP GUI에 로그인된 세션이 1개 이상 있어야 한다.
- 팝업 보안 알림이 뜨는 환경이면 사내 정책에 따라 허용 후 검증한다.

### SAP 권한

아래 트랜잭션 권한이 있으면 전체 검증이 가능하다.

- `MM03`: 조회 전용 자재 마스터 검증
- `SE16N` 또는 조회 가능한 표준 리스트 화면: table/ALV 검증
- `SPRO` 또는 tree control이 있는 조회성 화면: tree 검증

권한이 부족한 경우 해당 항목은 `SKIP`으로 기록하되, 실패로 보지 않는다.

---

## 3. 프로젝트 준비

PowerShell에서 실행한다.

```powershell
cd C:\path\to\sap_cli
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
sap --help
```

### 기대 결과

- `sap --help`가 오류 없이 출력된다.
- import 오류, pywin32 오류가 없어야 한다.

### 실패 시 확인 포인트

- `sap` 명령을 찾을 수 없음: 가상환경 활성화 여부와 `pip install -e .` 성공 여부 확인
- `win32com` 관련 오류: Windows 환경인지, pywin32 설치가 정상인지 확인
- 실행 정책 오류: PowerShell execution policy 또는 `cmd.exe`에서의 가상환경 활성화 방식 확인

---

## 4. 검증 결과 기록 방식

각 항목은 아래 상태 중 하나로 기록한다.

- `PASS`: 기대 결과와 일치
- `FAIL`: 재현 가능한 오류 발생
- `PARTIAL`: 핵심 기능은 동작하지만 일부 필드/컨트롤 누락
- `SKIP`: 권한/시스템/데이터 부재로 미수행

기록 예시:

```text
일시: 2026-04-26 14:30
검증자:
PC:
Windows:
SAP GUI:
Python:
SAP SID/Client:
sap_cli commit:

항목: 5.1 sap connect
결과: PASS
증거: connected=true, user/system/client 확인
비고:
```

---

## 5. 기본 연결 및 화면 읽기

### 5.1 `sap connect`

```powershell
sap connect
```

기대 결과:

- JSON `status`가 성공 상태다.
- SAP 세션 정보가 반환된다.
- user, system, client 또는 이에 준하는 연결 식별 정보가 확인된다.

실패 시 확인 포인트:

- SAP GUI가 실행되어 있고 로그인 완료 상태인지 확인
- SAP GUI Scripting 서버/클라이언트 설정 확인
- 여러 SAP Logon 창이 떠 있으면 검증 대상 세션만 남기고 재시도
- 보안 팝업이 뒤에 숨어 있지 않은지 확인

### 5.2 `sap connections`

```powershell
sap connections
```

기대 결과:

- 현재 열려 있는 SAP GUI connection/session 목록이 반환된다.
- 최소 1개 이상의 세션이 표시된다.

실패 시 확인 포인트:

- `sap connect`가 먼저 성공했는지 확인
- SAP GUI 세션이 잠금 화면 또는 modal popup 상태인지 확인
- SAP Logon Pad만 열려 있고 실제 로그인 세션이 없는지 확인

### 5.3 `sap screen`

```powershell
sap screen
sap screen --fields-only
sap screen --actions-only
```

기대 결과:

- 현재 트랜잭션, 화면 번호, 필드/버튼/탭 정보가 JSON으로 출력된다.
- `--fields-only`는 입력 가능 필드 또는 읽기 가능한 필드 목록을 반환한다.
- `--actions-only`는 실행 가능한 버튼/툴바 액션을 반환한다.

실패 시 확인 포인트:

- SAP 화면이 완전히 로딩되었는지 확인
- modal dialog가 떠 있으면 먼저 닫거나 해당 팝업을 기준으로 결과가 나오는지 확인
- 커스텀 테마/GUI 버전에 따라 label 추출이 다를 수 있으므로 raw id를 기록

### 5.4 `sap status`

```powershell
sap status
```

기대 결과:

- 상태바 message type과 message text가 반환된다.
- 메시지가 없어도 빈 상태를 오류로 처리하지 않는다.

실패 시 확인 포인트:

- SAP GUI 하단 상태바가 있는 일반 dynpro 화면인지 확인
- modal popup 또는 HTML control 화면에서는 상태바 접근 방식이 다를 수 있다.

---

## 6. MM03 조회 플로우

사전 준비:

- 실제 존재하는 조회용 자재번호를 하나 준비한다.
- 자재 조회 권한이 있는 client에서 수행한다.

아래 예시의 `TEST_MATNR`는 현장 자재번호로 바꾼다.

### 6.1 `sap run MM03`

```powershell
sap run MM03
sap status
sap screen --fields-only
```

기대 결과:

- MM03 초기 화면으로 이동한다.
- 자재번호 입력 필드가 `screen --fields-only` 결과에 나타난다.

실패 시 확인 포인트:

- `MM03` 권한 확인
- 이전 화면의 modal popup 또는 unsaved changes 경고 확인
- SAP OK code 입력이 차단되는 보안 정책 여부 확인

### 6.2 `sap fill MATNR=...`

```powershell
sap fill MATNR=TEST_MATNR
sap get MATNR
```

기대 결과:

- 자재번호 필드에 값이 입력된다.
- `sap get MATNR` 또는 실제 필드 ID 조회 결과가 입력값과 일치한다.

실패 시 확인 포인트:

- `MATNR` alias가 해당 화면에서 매핑되는지 확인
- 실패하면 `sap screen --fields-only`에서 실제 field id를 확인해 그 ID로 재시도
- 자재번호 필드가 비활성화된 variant 화면인지 확인

### 6.3 조회 화면 진입 및 상태 확인

```powershell
sap vkey 0
sap status
sap screen
```

기대 결과:

- 정상 자재이면 다음 선택/조회 화면으로 이동한다.
- 존재하지 않는 자재이면 상태바 오류가 JSON에 드러난다.
- CLI가 오류 상태를 숨기지 않는다.

실패 시 확인 포인트:

- 자재번호 존재 여부 확인
- plant/sales org 등 추가 필수 팝업이 뜨는지 확인
- 팝업이 뜨면 `sap screen` 결과에 modal 또는 popup성 정보가 포함되는지 기록

---

## 7. Table control 검증

일반 table control이 있는 조회성 화면에서 수행한다.

예시 후보:

- MM03의 일부 탭/뷰
- 사내 조회성 Z 화면
- 표준 리스트가 아닌 dynpro table control 화면

```powershell
sap screen --fields-only
sap table "TABLE_OR_GRID_ID_FROM_SCREEN" --max-rows 50
```

기대 결과:

- table id, row/column 수, 표시 가능한 cell 데이터가 반환된다.
- 기본 row 제한은 기존 정책대로 유지되어야 하며, 과도하게 많은 행을 읽지 않는다.
- `"TABLE_OR_GRID_ID_FROM_SCREEN"`은 `sap screen --fields-only` 또는 `sap screen` 결과의 실제 table/grid id로 바꾼다.

실패 시 확인 포인트:

- 화면이 ALV Grid인지 일반 table control인지 구분
- table이 스크롤 밖에 있거나 탭 뒤에 숨어 있지 않은지 확인
- 테이블이 비어 있으면 빈 결과와 오류를 구분해 기록

---

## 8. ALV Grid 검증

ALV Grid가 있는 조회성 화면에서 수행한다.

예시 후보:

- `SE16N`에서 작은 테이블을 조회
- 조회 전용 사내 ALV 리포트

```powershell
sap run SE16N
sap screen --fields-only
sap fill GD-TAB=TABLE_NAME
sap vkey 0
sap table "ALV_GRID_ID_FROM_SCREEN" --max-rows 50
```

기대 결과:

- ALV Grid 컨트롤이 감지된다.
- 컬럼명과 표시 행 일부가 JSON으로 반환된다.
- 권한 오류 또는 데이터 없음 메시지는 `sap status`에서 확인 가능하다.

실패 시 확인 포인트:

- `SE16N` 권한 확인
- 조회 대상 테이블의 데이터량이 너무 많지 않은지 확인
- ALV Grid가 SAP GUI Scripting에서 접근 가능한 control인지 확인
- 사내 ALV wrapper가 표준 `GuiGridView`가 아닐 수 있으므로 control type을 기록

---

## 9. Tree control 검증

SPRO 또는 document flow 등 tree control 화면에서 수행한다.

```powershell
sap run SPRO
sap screen
sap tree "TREE_ID_FROM_SCREEN" --depth 1
```

기대 결과:

- tree control id와 노드 일부가 반환된다.
- 펼침/선택 가능한 노드 정보가 확인된다.
- `"TREE_ID_FROM_SCREEN"`은 `sap screen` 결과의 실제 tree id로 바꾼다.

실패 시 확인 포인트:

- `SPRO` 권한 확인
- tree가 로딩되기 전에 명령을 실행하지 않았는지 확인
- HTML 기반 화면 또는 custom control이면 표준 tree 추출이 제한될 수 있다.

---

## 10. 화면 캡처 검증

```powershell
sap capture
```

기대 결과:

- 현재 SAP 화면의 캡처 파일 경로가 반환된다.
- 파일이 실제로 생성되어 열 수 있다.

실패 시 확인 포인트:

- SAP GUI 창이 최소화되어 있지 않은지 확인
- 원격 데스크톱 세션 잠금/백그라운드 상태인지 확인
- 보안 정책상 화면 캡처가 제한되는지 확인

---

## 11. Macro record/play 검증

조회 전용 화면에서 짧은 매크로만 기록한다.

권장 플로우:

```powershell
sap record-start mm03_smoke
sap record-step run MM03
sap record-step set MATNR TEST_MATNR
sap record-step vkey 0
sap record-finish --name mm03_smoke --desc "MM03 smoke 조회"
sap macro-show mm03_smoke
sap macro-play mm03_smoke --dry-run
sap macro-play mm03_smoke
```

기대 결과:

- 기록 파일이 생성된다.
- `macro-show`에서 step 목록을 확인할 수 있다.
- `--dry-run`은 실제 SAP 조작 없이 실행 계획을 보여준다.
- 실제 play는 MM03 조회 화면까지 재현한다.

실패 시 확인 포인트:

- `record-start` 후 `record-step`으로 기록한 단계와 실제 화면 조작 순서가 일치하는지 확인
- `record-step`은 기록과 실제 SAP 실행을 함께 수행하므로, 같은 동작을 별도의 `sap run`/`sap fill`로 중복 실행하지 않는다.
- 매크로 기록 중 modal popup이 끼어들었는지 확인
- alias가 아닌 raw field id가 필요한 화면인지 확인
- 조회 대상 데이터가 바뀌어 replay가 실패했는지 확인
- 변경성 트랜잭션에서 macro-play를 실행하지 않았는지 확인

---

## 12. Session start/end 검증

```powershell
sap session
sap slots
sap --slot ses0 session
sap session
```

기대 결과:

- 현재 SAP 세션 정보가 반환된다.
- `slots`에서 기본 슬롯 상태가 확인된다.
- SAP GUI 연결 자체가 비정상 종료되지 않는다.

실패 시 확인 포인트:

- 슬롯 메타 파일 권한 확인
- 여러 SAP GUI 세션 중 어느 세션을 대상으로 하는지 기록
- 전역 `--slot` 옵션은 `sap --slot ses0 session`처럼 subcommand 앞에 둔다.

---

## 13. Recipe suggestion 검증

MM03 또는 조회성 화면에서 수행한다.

```powershell
sap recipe-query --tcode MM03
sap recipe-list
sap recipe-show RECIPE_ID
```

기대 결과:

- 현재 화면/트랜잭션에 맞는 후보 recipe가 반환된다.
- recipe 목록과 상세 정보가 JSON으로 출력된다.
- replay 품질 메타가 있으면 표시 범위를 기록한다.

실패 시 확인 포인트:

- recipe 데이터 디렉터리가 비어 있지 않은지 확인
- 현재 화면의 transaction code가 recipe 조건과 맞는지 확인
- suggestion 결과가 없으면 오류가 아니라 `no candidates`인지 구분

---

## 14. MCP 연결 검증

Claude Desktop 또는 MCP client에서 `sap_cli` MCP server를 연결한다.

최소 검증 도구:

- connect
- connections
- screen
- status
- run

기대 결과:

- MCP client에서 도구 목록이 노출된다.
- `sap connect`에 준하는 연결 결과를 tool call로 받을 수 있다.
- CLI와 MCP 결과의 핵심 필드가 일치한다.

실패 시 확인 포인트:

- MCP server 실행 경로와 가상환경 경로 확인
- Windows 경로 escaping 확인
- MCP client가 오래된 server process를 붙잡고 있지 않은지 재시작 후 재검증
- CLI에서는 성공하지만 MCP에서만 실패하면 환경변수/working directory 차이를 기록

---

## 15. Daemon 모드 검증

```powershell
sap daemon start
sap daemon status
sap connect
sap screen
sap daemon stop
sap daemon status
```

기대 결과:

- daemon start 후 status가 running으로 표시된다.
- daemon 경유 명령이 일반 CLI 명령과 같은 SAP 세션을 읽는다.
- daemon stop 후 status가 stopped 또는 미실행 상태로 표시된다.

실패 시 확인 포인트:

- 포트 충돌 여부 확인
- 이전 daemon process가 남아 있는지 확인
- Windows 방화벽/보안 솔루션이 local IPC 또는 localhost 통신을 차단하는지 확인
- daemon과 SAP GUI가 같은 사용자 세션에서 실행 중인지 확인

---

## 16. Multi-session slot 검증

SAP GUI에 두 개 이상의 세션 또는 connection을 열고 수행한다.

```powershell
sap connections
sap --slot ses0 connect --session 0
sap --slot ses1 connect --session 1
sap --slot ses0 run MM03
sap --slot ses1 session
sap slots
```

기대 결과:

- slot별 세션 메타가 분리된다.
- 하나의 slot 명령이 다른 slot의 상태를 손상시키지 않는다.
- 실제 SAP GUI 세션 선택이 의도와 다르면 반드시 기록한다.

실패 시 확인 포인트:

- SAP GUI connection/session index가 CLI의 slot 정책과 일치하는지 확인
- 동일 화면을 여러 세션에서 열어 혼동하지 않도록 각 세션의 transaction을 다르게 두고 검증
- 세션 pool 파일 또는 잠금 파일 충돌 여부 확인

---

## 17. 최종 판정 기준

### PASS 기준

- 기본 연결: `connect`, `connections`, `screen`, `status`가 모두 PASS
- 안전 조회 플로우: `run MM03`, `fill`, `get`, `press Enter`, `status`가 PASS
- 컨트롤 읽기: table control, ALV Grid, tree 중 권한이 있는 항목이 PASS 또는 정당한 SKIP
- 운영 보조 기능: capture, macro, session, recipe 중 핵심 조회 플로우가 PASS
- 통합 실행 방식: MCP, daemon, multi-session 중 실제 운영 예정 방식이 PASS

### FAIL 기준

- SAP GUI Scripting 활성 상태에서 `sap connect`가 지속적으로 실패
- `sap screen`이 일반 SAP dynpro 화면에서 빈 결과만 반환
- 상태바 오류를 CLI가 성공처럼 숨김
- macro/play 또는 daemon 실행 중 SAP GUI가 비정상 종료됨
- MCP/daemon이 CLI와 다른 세션을 읽어 운영상 혼동을 유발함

---

## 18. 최종 보고 양식

```text
검증 일시:
검증자:
PC/OS:
SAP GUI 버전:
SAP SID/Client:
Python 버전:
sap_cli commit:

요약:
- PASS:
- FAIL:
- PARTIAL:
- SKIP:

필수 항목:
- connect:
- connections:
- screen:
- run/fill/get/status:
- table control:
- ALV Grid:
- tree:
- capture:
- macro:
- session:
- recipe:
- MCP:
- daemon:
- multi-session:

발견 이슈:
1.
2.
3.

운영 전 차단 이슈:
- 있음/없음

추가 확인 필요:
-
```
