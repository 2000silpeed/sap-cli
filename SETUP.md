# SAP CLI 설치 및 설정 완전 가이드

이 문서는 Windows 환경에서 SAP CLI 프로젝트를 GitHub에서 clone한 후 완전히 동작하기까지의 모든 과정을 단계별로 안내합니다.

---

## 목차

1. [사전 준비](#1-사전-준비)
2. [SAP GUI 스크립팅 활성화](#2-sap-gui-스크립팅-활성화)
3. [프로젝트 설치](#3-프로젝트-설치)
4. [CLI 단독 사용 테스트](#4-cli-단독-사용-테스트)
5. [Claude Code MCP 서버 설정](#5-claude-code-mcp-서버-설정)
6. [Skill 설치](#6-skill-설치)
7. [전체 동작 확인 체크리스트](#7-전체-동작-확인-체크리스트)
8. [트러블슈팅 가이드](#8-트러블슈팅-가이드)

---

## 1. 사전 준비

### 1.1 운영체제

- **Windows 10 또는 Windows 11** 필수
- SAP GUI COM(ActiveX) 인터페이스는 Windows에서만 동작합니다. macOS/Linux에서는 사용할 수 없습니다.

### 1.2 Python 3.11 이상 설치

두 가지 방법 중 하나를 선택합니다.

**방법 A: Microsoft Store (권장)**

1. Windows 검색에서 "Microsoft Store"를 엽니다.
2. "Python 3.11" 또는 "Python 3.12"를 검색합니다.
3. "설치" 버튼을 클릭합니다.
4. 설치 완료 후 자동으로 PATH에 등록됩니다.

**방법 B: python.org에서 직접 설치**

1. https://www.python.org/downloads/ 에서 Python 3.11 이상 버전을 다운로드합니다.
2. 설치 시 반드시 **"Add Python to PATH"** 체크박스를 선택합니다.
3. "Install Now"를 클릭합니다.

**설치 확인:**

PowerShell(또는 명령 프롬프트)을 열고 다음을 실행합니다:

```powershell
python --version
```

`Python 3.11.x` 이상이 출력되면 정상입니다.

### 1.3 pip 확인

Python 설치 시 pip가 함께 설치됩니다. 확인:

```powershell
pip --version
```

`pip 23.x.x from ...` 형태로 출력되면 정상입니다.

만약 `pip`를 찾을 수 없다는 메시지가 나오면:

```powershell
python -m pip --version
```

이후 모든 `pip` 명령을 `python -m pip`으로 대체하세요.

### 1.4 Git 설치

Git이 설치되어 있지 않다면:

1. https://git-scm.com/download/win 에서 Git for Windows를 다운로드합니다.
2. 기본 옵션으로 설치합니다.
3. 설치 확인:

```powershell
git --version
```

### 1.5 SAP GUI for Windows

- **SAP GUI for Windows 7.70 이상**이 설치되어 있어야 합니다.
- GetObjectTree 기능을 사용하려면 **PL3(패치 레벨 3) 이상**이 필요합니다.
- SAP GUI 설치는 조직의 IT 부서 또는 SAP Basis 관리자에게 문의하세요.
- SAP GUI 버전 확인: SAP Logon 화면 상단 메뉴 > Help > About SAP Logon

---

## 2. SAP GUI 스크립팅 활성화

SAP GUI Scripting은 **서버 측**과 **클라이언트 측** 모두 활성화해야 합니다.

### 2.1 서버 측 설정 (Basis 관리자에게 요청)

SAP 시스템의 Basis 관리자에게 다음 파라미터 설정을 요청합니다.

트랜잭션 코드 `RZ11`에서 설정:

| 파라미터 | 값 | 설명 |
|---------|---|------|
| `sapgui/user_scripting` | `TRUE` | 스크립팅 기능 활성화 (필수) |
| `sapgui/user_scripting_per_user` | `TRUE` | 사용자별 제어 (선택, 보안 강화) |
| `sapgui/user_scripting_set_readonly` | `FALSE` | 읽기 전용 모드 해제 (필수 - FALSE여야 값 입력 가능) |

> **참고:** 이 설정은 SAP 시스템의 프로파일 파라미터입니다. 서버를 재시작해야 적용되는 경우도 있으므로 Basis 관리자에게 확인하세요. 개발/테스트 시스템에서 먼저 테스트하는 것을 권장합니다.

### 2.2 클라이언트 측 설정 (본인 PC)

1. SAP GUI를 실행합니다 (SAP Logon에서 아무 시스템에 로그인할 필요 없이, SAP GUI 창이 열려 있으면 됩니다).
2. 키보드에서 **Alt+F12**를 누릅니다.
   - 또는 SAP GUI 창 우측 상단의 아이콘 중 **Customize Local Layout** 아이콘(작은 렌치 모양)을 클릭합니다.
3. 드롭다운 메뉴에서 **"Options..."**을 클릭합니다.
4. 왼쪽 트리 메뉴에서 **"Accessibility & Scripting"**을 클릭하여 펼칩니다.
5. 그 아래의 **"Scripting"**을 클릭합니다.
6. 오른쪽 패널에서 다음을 설정합니다:
   - **"Enable Scripting"** 체크박스를 **선택(체크)** 합니다. (필수)
   - **"Notify When a Script Attaches to SAP GUI"** 체크박스를 **해제** 합니다. (권장 - 매번 확인 팝업이 뜨는 것을 방지)
   - **"Notify When a Script Opens a Connection"** 체크박스를 **해제** 합니다. (권장 - 매번 확인 팝업이 뜨는 것을 방지)
7. **"OK"** 버튼을 클릭하여 저장합니다.

### 2.3 설정 확인 방법

1. SAP GUI에서 아무 시스템에 로그인합니다.
2. **Alt+F12** > **"Options..."** 을 클릭합니다.
3. **"Accessibility & Scripting"** > **"Scripting"** 에서 "Enable Scripting"이 체크되어 있는지 확인합니다.
4. 만약 "Enable Scripting" 체크박스가 회색으로 비활성화되어 있다면, 서버 측에서 `sapgui/user_scripting`이 `TRUE`로 설정되지 않은 것입니다. Basis 관리자에게 다시 요청하세요.

---

## 3. 프로젝트 설치

### 3.1 저장소 클론

PowerShell을 열고 원하는 디렉토리로 이동한 후:

```powershell
git clone https://github.com/your-username/sap-cli.git
cd sap-cli
```

> **참고:** 실제 GitHub 저장소 URL은 프로젝트 관리자에게 확인하세요.

### 3.2 패키지 설치

```powershell
pip install -e .
```

이 명령은 다음 패키지를 자동으로 설치합니다:
- `pydantic` (>=2.0) - 데이터 모델
- `typer` (>=0.9) - CLI 프레임워크
- `rich` (>=13.0) - 터미널 출력 포맷팅
- `pywin32` (>=306) - Windows COM 인터페이스 (SAP GUI 연결에 필수)

### 3.3 설치 확인

```powershell
sap --help
```

다음과 같은 출력이 나오면 정상입니다:

```
Usage: sap [OPTIONS] COMMAND [ARGS]...

  SAP GUI Scripting CLI for AI Agents

Options:
  --help  Show this message and exit.

Commands:
  connect         실행 중인 SAP GUI에 연결.
  session         현재 세션 정보 조회.
  screen          현재 화면의 UI 구조를 JSON으로 출력.
  run             트랜잭션 실행.
  set             필드에 값 설정.
  get             필드 값 읽기.
  press           버튼 클릭.
  vkey            VKey 전송.
  status          상태바 메시지 조회.
  table           테이블 데이터 읽기.
  command         커맨드 필드에 명령 실행.
  modal           모달 윈도우(팝업) 정보 조회.
  record-start    매크로 녹화 시작.
  record-step     매크로 단계 기록 + 실제 SAP 실행.
  record-undo     마지막 녹화 단계 취소.
  record-status   현재 녹화 상태 조회.
  record-finish   녹화 종료 및 매크로 저장.
  record-cancel   녹화 취소.
  macro-list      저장된 매크로 목록.
  macro-show      매크로 상세 조회.
  macro-play      매크로 재생.
  macro-delete    매크로 삭제.
  ontology        온톨로지 조회.
  learn           현재 화면을 온톨로지로 학습하여 저장.
```

> 만약 `sap`을 찾을 수 없다는 메시지가 나오면 [트러블슈팅 8.1](#81-sap-명령을-찾을-수-없습니다)을 참조하세요.

---

## 4. CLI 단독 사용 테스트

MCP 서버 없이 CLI만으로 SAP를 조작하는 테스트입니다.

### 4.1 사전 조건

- SAP GUI가 실행 중이고, SAP 시스템에 **로그인이 완료된 상태**여야 합니다.
- SAP GUI Scripting이 활성화되어 있어야 합니다 (섹션 2 참조).

### 4.2 테스트 순서

**Step 1: SAP 연결**

```powershell
sap connect
```

성공 시 출력:
```json
{
  "connected": true,
  "session": {
    "session_id": "ses[0]",
    "user": "YOUR_USER",
    "language": "EN",
    "transaction": "SESSION_MANAGER",
    "screen_number": 100,
    "system_name": "DEV",
    "client": "800"
  }
}
```

**Step 2: 세션 정보 확인**

```powershell
sap session
```

현재 사용자, 트랜잭션, 시스템명 등이 JSON으로 출력됩니다.

**Step 3: 트랜잭션 실행**

```powershell
sap run MM03
```

MM03(자재 조회) 트랜잭션의 초기 화면으로 이동합니다.

**Step 4: 화면 구조 확인**

```powershell
sap screen --fields-only
```

현재 화면에서 입력 가능한 필드 목록이 JSON으로 출력됩니다. 자재번호 입력 필드의 ID를 확인할 수 있습니다.

**Step 5: 필드에 값 입력**

```powershell
sap set "wnd[0]/usr/ctxtRMMG1-MATNR" "100-100"
```

> 필드 ID는 Step 4에서 확인한 값을 사용합니다. 시스템에 따라 다를 수 있습니다.

**Step 6: Enter 키 전송**

```powershell
sap vkey 0
```

VKey 0은 Enter 키입니다. 뷰 선택 화면이 나타나면 한 번 더 실행합니다:

```powershell
sap vkey 0
```

**Step 7: 결과 확인**

```powershell
sap screen
sap status
```

자재 데이터가 표시된 화면 구조와 상태 메시지를 확인합니다.

**Step 8: 뒤로 가기**

```powershell
sap vkey 3
```

VKey 3은 F3(Back)입니다.

### 4.3 성공 시

모든 명령이 에러 없이 JSON 결과를 반환하고, SAP GUI 화면이 실제로 변경되는 것을 확인할 수 있으면 정상입니다.

### 4.4 실패 시 대처

| 증상 | 원인 | 해결 |
|------|------|------|
| `sap connect` 에서 에러 | SAP GUI가 실행되지 않았거나 로그인되지 않음 | SAP GUI에 먼저 로그인 |
| `"SAP GUI에 연결할 수 없습니다"` | 스크립팅이 비활성화됨 | 섹션 2의 스크립팅 활성화 확인 |
| `sap set`에서 에러 | 필드 ID가 잘못됨 | `sap screen --fields-only`로 정확한 ID 확인 |
| `sap vkey` 후 화면이 안 바뀜 | 필수 필드 미입력 | `sap status`로 에러 메시지 확인 |

---

## 5. Claude Code MCP 서버 설정

이 섹션이 가장 중요합니다. Claude Code에서 SAP를 자연어로 조작하려면 MCP 서버를 설정해야 합니다.

### 5.1 MCP 의존성 설치

기본 설치(`pip install -e .`)에는 MCP 패키지가 포함되지 않습니다. 별도로 설치해야 합니다:

```powershell
pip install -e ".[mcp]"
```

이 명령은 `mcp` (>=1.0) 패키지를 추가로 설치합니다.

설치 확인:

```powershell
python -c "import mcp; print('MCP OK')"
```

`MCP OK`가 출력되면 정상입니다.

### 5.2 MCP 서버 단독 실행 테스트

Claude Code에 연결하기 전에, MCP 서버가 정상적으로 시작되는지 먼저 확인합니다:

```powershell
python -m sap_cli.mcp_server
```

- 아무 에러 없이 대기 상태로 들어가면 정상입니다 (MCP 서버는 stdin/stdout으로 통신하므로 별도 출력이 없습니다).
- **Ctrl+C**로 종료합니다.
- 만약 `Error: mcp 패키지가 설치되지 않았습니다`라는 메시지가 나오면 5.1을 다시 수행하세요.

### 5.3 Claude Code settings.json 설정

Claude Code의 설정 파일에 MCP 서버를 등록합니다.

**설정 파일 위치:**

| 범위 | 경로 |
|------|------|
| 전역 (모든 프로젝트에 적용) | `%APPDATA%\Claude\settings.json` |
| 프로젝트별 (해당 프로젝트에서만 적용) | `프로젝트폴더\.claude\settings.json` |

> `%APPDATA%`는 보통 `C:\Users\사용자명\AppData\Roaming` 입니다.

> 프로젝트별 설정을 사용하려면, 프로젝트 루트에 `.claude` 폴더를 만들고 그 안에 `settings.json`을 생성합니다.

**설정 내용:**

파일이 없으면 새로 생성하고, 이미 있으면 `mcpServers` 항목을 추가합니다.

```json
{
  "mcpServers": {
    "sap-gui": {
      "command": "python",
      "args": ["-m", "sap_cli.mcp_server"],
      "cwd": "C:\\Users\\사용자명\\sap-cli"
    }
  }
}
```

> **중요:** `cwd`의 경로를 실제 프로젝트를 clone한 경로로 수정하세요. 백슬래시(`\`)를 이중(`\\`)으로 작성해야 합니다.

**기존에 다른 MCP 서버가 등록되어 있는 경우:**

```json
{
  "mcpServers": {
    "existing-server": {
      "command": "...",
      "args": ["..."]
    },
    "sap-gui": {
      "command": "python",
      "args": ["-m", "sap_cli.mcp_server"],
      "cwd": "C:\\Users\\사용자명\\sap-cli"
    }
  }
}
```

### 5.4 Python 경로를 명시적으로 지정하는 경우

시스템에 여러 Python 버전이 설치되어 있거나, `python` 명령이 PATH에서 찾아지지 않는 경우, 전체 경로를 지정할 수 있습니다:

```json
{
  "mcpServers": {
    "sap-gui": {
      "command": "C:\\Users\\사용자명\\AppData\\Local\\Programs\\Python\\Python311\\python.exe",
      "args": ["-m", "sap_cli.mcp_server"],
      "cwd": "C:\\Users\\사용자명\\sap-cli"
    }
  }
}
```

Python 설치 경로 확인 방법:

```powershell
where python
```

### 5.5 MCP 서버 동작 확인

1. `settings.json`을 저장합니다.
2. Claude Code를 재시작합니다 (이미 실행 중이라면 종료 후 다시 시작).
3. Claude Code에서 대화창에 다음과 같이 입력합니다:

```
사용 가능한 MCP 도구 목록을 보여줘
```

다음 16개의 SAP 도구가 목록에 표시되면 MCP 서버가 정상적으로 연결된 것입니다:

| 도구 | 설명 |
|------|------|
| `sap_connect` | SAP GUI에 연결 |
| `sap_session` | 세션 정보 조회 |
| `sap_screen` | 현재 화면 UI 구조 (JSON) |
| `sap_run` | 트랜잭션 실행 |
| `sap_set_field` | 필드 값 설정 |
| `sap_get_field` | 필드 값 읽기 |
| `sap_press` | 버튼 클릭 |
| `sap_send_vkey` | VKey 전송 |
| `sap_get_status` | 상태바 메시지 |
| `sap_table_read` | 테이블 데이터 읽기 |
| `sap_ontology_query` | 온톨로지 조회 |
| `sap_learn` | 현재 화면을 온톨로지로 학습 |
| `sap_macro_control` | 매크로 녹화 제어 |
| `sap_macro_record_step` | 녹화 중 단계 기록 + SAP 실행 |
| `sap_macro_play` | 매크로 재생 |
| `sap_macro_list` | 매크로 목록 조회 |

---

## 6. Skill 설치

Skill은 Claude Code가 SAP CLI의 사용법과 SAP 도메인 지식을 미리 알 수 있도록 하는 지식 파일입니다.

### 6.1 Skill 파일 확인

프로젝트의 `skill/` 폴더에 `SKILL.md` 파일이 있는지 확인합니다:

```powershell
dir skill\
```

### 6.2 Skill 설치 방법

**방법 A: 수동 복사**

1. 사용자 홈 디렉토리에 skill 폴더를 생성합니다:

```powershell
mkdir %USERPROFILE%\.claude\skills\sap-cli
```

2. SKILL.md 파일을 복사합니다:

```powershell
copy skill\SKILL.md %USERPROFILE%\.claude\skills\sap-cli\SKILL.md
```

**방법 B: Claude Code에서 직접 등록**

Claude Code에서 다음과 같이 입력합니다:

```
/install-skill 경로: C:\Users\사용자명\sap-cli\skill
```

또는 프로젝트의 `.claude/` 폴더에 skill을 심볼릭 링크로 연결할 수도 있습니다:

```powershell
mklink /D .claude\skills\sap-cli skill
```

### 6.3 설치 확인

Claude Code를 재시작한 후 다음과 같이 질문해봅니다:

```
SAP CLI 스킬이 설치되어 있어?
```

또는 SAP 관련 질문을 했을 때 Claude Code가 SAP CLI 도구를 자연스럽게 사용한다면 정상입니다.

---

## 7. 전체 동작 확인 체크리스트

모든 설정이 완료되었는지 아래 체크리스트로 확인합니다.

### 기본 환경

- [ ] Python 3.11 이상 설치 확인 (`python --version`)
- [ ] pip 정상 동작 확인 (`pip --version`)
- [ ] SAP GUI for Windows 7.70 이상 설치 확인
- [ ] SAP GUI 스크립팅 활성화 확인 (Alt+F12 > Options > Scripting)

### CLI 동작

- [ ] `pip install -e .` 설치 완료
- [ ] `sap --help` 정상 출력
- [ ] SAP GUI에 로그인한 상태에서 `sap connect` 성공
- [ ] `sap session` 출력에서 사용자/시스템 정보 확인
- [ ] `sap run MM03` 트랜잭션 실행 성공
- [ ] `sap screen` 화면 구조 JSON 출력 확인
- [ ] `sap screen --fields-only` 입력 필드 목록 확인
- [ ] `sap set "필드ID" "값"` 필드 값 설정 성공
- [ ] `sap vkey 0` Enter 키 전송 성공
- [ ] `sap status` 상태바 메시지 확인

### MCP 서버 연동

- [ ] `pip install -e ".[mcp]"` 설치 완료
- [ ] `python -m sap_cli.mcp_server` 에러 없이 시작
- [ ] `settings.json`에 `sap-gui` MCP 서버 등록
- [ ] Claude Code 재시작 후 SAP 도구 16개가 목록에 보임
- [ ] Claude Code에서 "SAP에 연결해줘" 테스트 성공
- [ ] Claude Code에서 "MM03에서 자재 100-100 조회해줘" 테스트 성공

### Skill (선택)

- [ ] `skill/SKILL.md` 파일을 `~/.claude/skills/sap-cli/`에 복사
- [ ] Claude Code에서 SAP 관련 질문 시 도메인 지식을 활용하는지 확인

---

## 8. 트러블슈팅 가이드

### 8.1 `sap` 명령을 찾을 수 없습니다

**증상:**
```
'sap'은(는) 내부 또는 외부 명령, 실행할 수 있는 프로그램, 또는 배치 파일이 아닙니다.
```

**원인:** `pip install -e .`로 설치된 스크립트가 PATH에 포함되지 않음.

**해결:**

1. Python Scripts 폴더를 PATH에 추가합니다:

```powershell
# Python Scripts 경로 확인
python -c "import sysconfig; print(sysconfig.get_path('scripts'))"
```

2. 출력된 경로 (예: `C:\Users\사용자명\AppData\Local\Programs\Python\Python311\Scripts`)를 시스템 환경변수 PATH에 추가합니다:
   - Windows 검색 > "환경 변수" > "시스템 환경 변수 편집" > "환경 변수" 버튼
   - "사용자 변수"에서 `Path`를 선택 > "편집" > "새로 만들기" > 위 경로를 붙여넣기
   - "확인" 클릭 후 PowerShell을 다시 엽니다.

3. 또는 PATH 추가 없이 직접 실행:

```powershell
python -m sap_cli.cli --help
```

### 8.2 SAP GUI에 연결할 수 없습니다

**증상:**
```json
{"error": true, "message": "SAP GUI에 연결할 수 없습니다"}
```

**해결 순서:**

1. SAP GUI가 실행 중인지 확인합니다. 작업 표시줄에 SAP 아이콘이 있어야 합니다.
2. SAP 시스템에 **로그인이 완료된 상태**인지 확인합니다. SAP Logon 화면이 아니라, 실제 SAP 시스템의 초기 화면(Easy Access)이 보여야 합니다.
3. SAP GUI 스크립팅이 활성화되어 있는지 확인합니다 (섹션 2 참조).
4. 다른 프로그램이 이미 SAP GUI Scripting을 점유하고 있지 않은지 확인합니다.
5. SAP GUI를 완전히 종료한 후 다시 시작하고, 다시 로그인한 뒤 시도합니다.

### 8.3 MCP 서버가 시작되지 않습니다

**증상:** Claude Code에서 SAP 도구가 보이지 않거나, MCP 연결 에러가 발생함.

**해결 순서:**

1. MCP 패키지 설치 확인:

```powershell
python -c "import mcp; print(mcp.__version__)"
```

에러가 나오면:

```powershell
pip install -e ".[mcp]"
```

2. MCP 서버를 수동으로 실행하여 에러 메시지 확인:

```powershell
python -m sap_cli.mcp_server
```

3. `settings.json`의 JSON 문법이 올바른지 확인합니다. 특히:
   - 쉼표(,) 위치
   - 중괄호/대괄호 짝
   - 백슬래시 이중 표기 (`\\`)
   - 마지막 항목 뒤에 쉼표가 없어야 함

4. `settings.json`의 `command` 경로가 올바른지 확인합니다:

```powershell
# settings.json에 지정한 python 경로로 직접 실행 테스트
python -m sap_cli.mcp_server
```

### 8.4 win32com 에러

**증상:**
```
ModuleNotFoundError: No module named 'win32com'
```
또는
```
ImportError: DLL load failed while importing win32com
```

**해결:**

1. pywin32 재설치:

```powershell
pip uninstall pywin32
pip install pywin32>=306
```

2. pywin32 설치 후 초기화 스크립트 실행:

```powershell
python -m pywin32_postinstall -install
```

> 이 명령은 관리자 권한이 필요할 수 있습니다. PowerShell을 "관리자 권한으로 실행"한 뒤 수행하세요.

3. Python을 재시작합니다.

### 8.5 Claude Code에서 도구가 안 보일 때

**해결 순서:**

1. `settings.json` 파일의 위치가 올바른지 확인합니다:
   - 전역: `%APPDATA%\Claude\settings.json`
   - 프로젝트: `프로젝트폴더\.claude\settings.json`

2. `settings.json`의 JSON 문법을 검증합니다. 온라인 JSON 검증기 (https://jsonlint.com/) 에 내용을 붙여넣어 확인할 수 있습니다.

3. `cwd` 경로가 실제 프로젝트 경로와 일치하는지 확인합니다:

```powershell
# 프로젝트 폴더에서 실행
cd C:\Users\사용자명\sap-cli
python -m sap_cli.mcp_server
```

4. Claude Code를 완전히 종료한 후 다시 시작합니다.

5. Claude Code의 MCP 서버 로그를 확인합니다. Claude Code 터미널에서 에러 메시지가 표시될 수 있습니다.

### 8.6 "필드 ID를 찾을 수 없습니다"

**증상:** `sap set` 또는 `sap get`에서 필드를 찾을 수 없다는 에러.

**해결:**

1. `sap screen --fields-only`로 현재 화면의 정확한 필드 ID를 확인합니다.
2. 화면이 바뀌면 필드 ID도 달라집니다. 항상 현재 화면 기준으로 필드 ID를 사용하세요.
3. 필드 ID는 대소문자를 구분합니다.

### 8.7 VKey 전송 후 예상치 못한 화면

**원인:** 필수 필드를 입력하지 않았거나, 입력 값이 유효하지 않은 경우 SAP가 에러를 표시합니다.

**해결:**

```powershell
sap status
```

상태바 메시지를 확인하면 SAP이 어떤 에러를 발생시켰는지 알 수 있습니다. 메시지 타입이 `E`(에러)인 경우 해당 내용을 해결한 후 다시 시도하세요.

### 8.8 Microsoft Store Python에서 경로 문제

Microsoft Store를 통해 Python을 설치한 경우, `python` 대신 `python3`으로 실행해야 할 수 있습니다.

`settings.json`에서:

```json
{
  "mcpServers": {
    "sap-gui": {
      "command": "python3",
      "args": ["-m", "sap_cli.mcp_server"],
      "cwd": "C:\\Users\\사용자명\\sap-cli"
    }
  }
}
```

또는 `py` 런처를 사용합니다:

```json
{
  "mcpServers": {
    "sap-gui": {
      "command": "py",
      "args": ["-3", "-m", "sap_cli.mcp_server"],
      "cwd": "C:\\Users\\사용자명\\sap-cli"
    }
  }
}
```
