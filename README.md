# SAP CLI - AI를 위한 SAP GUI 자동화 도구

SAP GUI Scripting API를 CLI/MCP로 래핑하여, AI 에이전트가 SAP 시스템을 구조적으로 조작할 수 있게 하는 도구.

SOS(Study Ontology System)의 온톨로지 사상을 적용하여, 단순 스크립트 실행이 아닌 **SAP 도메인을 이해하는 AI**를 구축한다.

---

## 목차

1. [사전 요구사항](#1-사전-요구사항)
2. [설치](#2-설치)
3. [SAP GUI 스크립팅 활성화](#3-sap-gui-스크립팅-활성화)
4. [기본 사용법 (CLI)](#4-기본-사용법-cli)
5. [매크로 녹화/재생](#5-매크로-녹화재생)
6. [온톨로지 시스템](#6-온톨로지-시스템)
7. [Claude Code MCP 연동](#7-claude-code-mcp-연동)
8. [MCP 도구를 활용한 AI 자동화 시나리오](#8-mcp-도구를-활용한-ai-자동화-시나리오)
9. [트러블슈팅](#9-트러블슈팅)
10. [아키텍처](#10-아키텍처)

---

## 1. 사전 요구사항

| 항목 | 요구사항 |
|------|---------|
| OS | Windows 10/11 (SAP GUI COM은 Windows 전용) |
| Python | 3.11 이상 |
| SAP GUI | SAP GUI for Windows 7.70 이상 (GetObjectTree 사용 시 PL3+) |
| SAP 시스템 | 스크립팅이 활성화된 SAP 시스템 |

---

## 2. 설치

```powershell
# 저장소 클론
git clone https://github.com/your-repo/sap-cli.git
cd sap-cli

# 설치 (편집 가능 모드)
pip install -e .

# MCP 서버도 사용할 경우
pip install -e ".[mcp]"

# 설치 확인
sap --help
```

---

## 3. SAP GUI 스크립팅 활성화

### 서버 측 (Basis 관리자에게 요청)

트랜잭션 `RZ11`에서 아래 파라미터 설정:

| 파라미터 | 값 | 설명 |
|---------|---|------|
| `sapgui/user_scripting` | `TRUE` | 스크립팅 기능 활성화 |
| `sapgui/user_scripting_per_user` | `TRUE` | 사용자별 제어 (선택) |
| `sapgui/user_scripting_set_readonly` | `FALSE` | 읽기 전용 모드 해제 |

### 클라이언트 측 (본인 PC)

1. SAP GUI 실행
2. 메뉴: **Customize Local Layout** (Alt+F12) > **Options**
3. **Accessibility & Scripting** > **Scripting** 탭
4. **Enable Scripting** 체크
5. **Notify When a Script Attaches to SAP GUI** 체크 해제 (선택 - 매번 확인 팝업 방지)
6. **Notify When a Script Opens a Connection** 체크 해제 (선택)
7. **OK** 클릭

---

## 4. 기본 사용법 (CLI)

### 4.1 SAP 연결

SAP GUI에 로그인한 상태에서:

```powershell
# 첫 번째 연결의 첫 번째 세션에 연결
sap connect

# 특정 연결/세션 지정
sap connect --connection 0 --session 1
```

출력 예시:
```json
{
  "connected": true,
  "session": {
    "session_id": "ses[0]",
    "user": "SAPUSER",
    "language": "EN",
    "transaction": "SESSION_MANAGER",
    "screen_number": 100,
    "system_name": "DEV",
    "client": "800"
  }
}
```

### 4.2 세션 정보 조회

```powershell
sap session
```

### 4.3 트랜잭션 실행

```powershell
# 트랜잭션 시작
sap run MM03

# 커맨드 필드에 직접 입력 (/n, /o 등 포함)
sap command "/nMM03"
sap command "/oSE80"      # 새 세션에서 열기
```

### 4.4 화면 탐색

```powershell
# 현재 화면의 전체 UI 구조 (JSON)
sap screen

# 입력 가능한 필드만 보기
sap screen --fields-only

# 가능한 액션(버튼/탭)만 보기
sap screen --actions-only
```

출력 예시 (`--fields-only`):
```json
{
  "transaction": "MM03",
  "screen_number": 4000,
  "changeable_fields": [
    {
      "id": "wnd[0]/usr/ctxtRMMG1-MATNR",
      "name": "RMMG1-MATNR",
      "type": "GuiCTextField",
      "label": "Material",
      "value": "",
      "changeable": true,
      "required": true
    }
  ]
}
```

### 4.5 필드 조작

```powershell
# 필드에 값 설정
sap set "wnd[0]/usr/ctxtRMMG1-MATNR" "100-100"

# 필드 값 읽기
sap get "wnd[0]/usr/ctxtRMMG1-MATNR"
```

### 4.6 키보드/버튼

```powershell
# VKey 전송
sap vkey 0     # Enter
sap vkey 3     # Back (F3)
sap vkey 8     # Execute (F8)
sap vkey 11    # Save (Ctrl+S)
sap vkey 12    # Cancel (F12)

# 버튼 클릭 (요소 ID 필요)
sap press "wnd[0]/tbar[0]/btn[0]"       # 첫 번째 툴바의 첫 번째 버튼
sap press "wnd[0]/tbar[1]/btn[8]"       # 두 번째 툴바의 Execute 버튼
```

### 4.7 상태바 확인

```powershell
sap status
```

출력:
```json
{
  "type": "S",
  "message": "Material 100-100 displayed"
}
```

타입: `S`=성공, `W`=경고, `E`=에러, `I`=정보, `A`=중단

### 4.8 테이블 읽기

```powershell
# 테이블 데이터를 JSON으로 출력
sap table "wnd[0]/usr/tblSAPMV45ATCTRL_U_ERF_AUFTRAG"
```

### 4.9 모달(팝업) 처리

```powershell
# 모달 윈도우 확인
sap modal

# 출력: {"has_modal": true, "text": "Do you want to save?"}
# 모달이 있으면 vkey로 응답
sap vkey 0    # Enter (확인)
sap vkey 12   # Cancel
```

### 4.10 전체 시나리오 예시: MM03 자재 조회

```powershell
# 1. SAP 연결
sap connect

# 2. MM03 트랜잭션 실행
sap run MM03

# 3. 현재 화면에서 입력 가능한 필드 확인
sap screen --fields-only

# 4. 자재번호 입력
sap set "wnd[0]/usr/ctxtRMMG1-MATNR" "100-100"

# 5. Enter (다음 화면으로)
sap vkey 0

# 6. 뷰 선택 화면에서 Enter (기본 뷰 선택)
sap vkey 0

# 7. 결과 화면 확인
sap screen

# 8. 상태 확인
sap status

# 9. 뒤로 가기
sap vkey 3
```

---

## 5. 매크로 녹화/재생

핵심 기능: 채팅으로 SAP 작업을 하나하나 실행하면서 기록하고, 나중에 변수만 바꿔서 재실행.

### 5.1 매크로 녹화

```powershell
# --- Step 1: 녹화 시작 ---
sap record-start "자재 기본데이터 조회"

# --- Step 2: SAP 작업 실행 (실제로 SAP에 실행되면서 동시에 기록됨) ---

# 트랜잭션 시작
sap record-step run MM03

# 자재번호 입력
sap record-step set "wnd[0]/usr/ctxtRMMG1-MATNR" "100-100" --desc "자재번호 입력"

# Enter
sap record-step vkey 0

# 뷰 선택 화면에서 Enter
sap record-step vkey 0

# --- Step 3: 녹화 상태 확인 (선택) ---
sap record-status
# 출력: {"recording": true, "steps_count": 4, "steps": [...]}

# --- Step 4: 실수했으면 마지막 단계 취소 ---
sap record-undo

# --- Step 5: 녹화 완료 + 변수 지정 ---
sap record-finish \
  --name "자재 기본데이터 조회" \
  --desc "MM03에서 자재번호로 기본 데이터를 조회한다" \
  --vars "{\"100-100\": \"조회할 자재번호\"}"
```

`--vars` 설명:
- Key: 녹화 중 **실제 사용한 값** (예: `"100-100"`)
- Value: 이 변수에 대한 **설명** (예: `"조회할 자재번호"`)
- 변수명은 SAP 필드 ID에서 자동 유추 (예: `ctxtRMMG1-MATNR` → `matnr`)

녹화 완료 시 출력:
```json
{
  "saved": true,
  "macro": {
    "id": "a1b2c3d4e5f6",
    "name": "자재 기본데이터 조회",
    "tcode": "MM03",
    "variables": [
      {
        "name": "matnr",
        "description": "조회할 자재번호",
        "default": "100-100",
        "required": false
      }
    ],
    "steps_count": 4
  }
}
```

### 5.2 저장된 매크로 확인

```powershell
# 매크로 목록
sap macro-list

# 매크로 상세 조회
sap macro-show a1b2c3d4e5f6
```

### 5.3 매크로 재생

```powershell
# 기본값으로 실행 (자재번호 = 100-100)
sap macro-play a1b2c3d4e5f6

# 변수 바꿔서 실행
sap macro-play a1b2c3d4e5f6 --vars "{\"matnr\": \"200-200\"}"

# 실제 실행 없이 프리뷰만 (dry run)
sap macro-play a1b2c3d4e5f6 --vars "{\"matnr\": \"300-300\"}" --dry-run
```

재생 결과:
```json
{
  "macro_id": "a1b2c3d4e5f6",
  "macro_name": "자재 기본데이터 조회",
  "success": true,
  "variables_used": {"matnr": "200-200"},
  "total_steps": 4,
  "completed_steps": 4,
  "step_results": [
    {"step_number": 1, "success": true, "status_message": ""},
    {"step_number": 2, "success": true, "status_message": ""},
    {"step_number": 3, "success": true, "status_message": ""},
    {"step_number": 4, "success": true, "actual_screen": 4200}
  ]
}
```

### 5.4 매크로 삭제

```powershell
sap macro-delete a1b2c3d4e5f6
```

### 5.5 record-step 액션 레퍼런스

| 액션 | 설명 | target | value |
|------|------|--------|-------|
| `run` | 트랜잭션 실행 | 트랜잭션 코드 (MM03) | - |
| `set` | 필드 값 설정 | 필드 ID | 설정할 값 |
| `press` | 버튼 클릭 | 버튼 ID | - |
| `vkey` | 키보드 키 전송 | VKey 번호 (0=Enter) | - |
| `checkbox` | 체크박스 토글 | 체크박스 ID | `true` 또는 `false` |
| `radio` | 라디오 버튼 선택 | 라디오 ID | - |
| `combo` | 드롭다운 선택 | 콤보박스 ID | 선택할 값 |
| `tab` | 탭 선택 | 탭 ID | - |
| `menu` | 메뉴 선택 | 메뉴 ID | - |

### 5.6 실전 예시: VA01 수주 생성 매크로

```powershell
# 녹화 시작
sap record-start "표준 수주 생성"

# 초기 화면
sap record-step run VA01
sap record-step set "wnd[0]/usr/ctxtVBAK-AUART" "OR" --desc "오더유형: 표준수주"
sap record-step set "wnd[0]/usr/ctxtVBAK-VKORG" "1000" --desc "판매조직"
sap record-step set "wnd[0]/usr/ctxtVBAK-VTWEG" "10" --desc "유통채널"
sap record-step set "wnd[0]/usr/ctxtVBAK-SPART" "00" --desc "제품군"
sap record-step vkey 0

# 오더 Overview 화면
sap record-step set "wnd[0]/usr/subSUBSCREEN_HEADER/ctxtKUAGV-KUNNR" "1001" --desc "고객번호"
sap record-step set "wnd[0]/usr/subSUBSCREEN_HEADER/ctxtRV45A-KETDAT" "31.12.2026" --desc "납품요청일"
sap record-step set "wnd[0]/usr/subSUBSCREEN_HEADER/ctxtVBKD-BSTNK" "PO-2026-001" --desc "고객 PO번호"

# 품목 입력 (테이블 첫 번째 행)
sap record-step set "wnd[0]/usr/tabsTAXI_TABSTRIP_OVERVIEW/.../ctxtRV45A-MABNR[0]" "FG-100" --desc "자재번호"
sap record-step set "wnd[0]/usr/tabsTAXI_TABSTRIP_OVERVIEW/.../txtRV45A-KWMENG[0]" "10" --desc "수량"
sap record-step vkey 0

# 저장
sap record-step vkey 11

# 녹화 완료 - 변경 가능한 값들을 변수로 지정
sap record-finish \
  --name "표준 수주 생성" \
  --desc "OR 타입 표준 수주를 생성한다" \
  --vars "{\"1001\": \"고객번호\", \"31.12.2026\": \"납품요청일\", \"PO-2026-001\": \"고객PO번호\", \"FG-100\": \"자재번호\", \"10\": \"주문수량\"}"
```

이후 재생:
```powershell
sap macro-play abc123 --vars "{\"kunnr\": \"2001\", \"ketdat\": \"15.06.2026\", \"bstnk\": \"PO-2026-099\", \"mabnr\": \"FG-200\", \"kwmeng\": \"50\"}"
```

---

## 6. 온톨로지 시스템

SAP 트랜잭션의 화면/필드/액션 구조를 자동 학습하여 도메인 지식으로 축적.

### 6.1 현재 화면 학습

```powershell
# SAP에서 특정 트랜잭션/화면을 열어놓은 상태에서:
sap learn
```

출력:
```json
{
  "learned": true,
  "transaction": "MM03",
  "screen_number": 4200,
  "fields_count": 15,
  "buttons_count": 8
}
```

### 6.2 학습된 온톨로지 조회

```powershell
# 저장된 온톨로지 목록
sap ontology --list

# 특정 트랜잭션의 전체 온톨로지
sap ontology MM03

# 필드 검색 (이름, 라벨, 시맨틱 동의어 모두 검색)
sap ontology MM03 --field "자재번호"
sap ontology MM03 --field "Material"
sap ontology MM03 --field "MATNR"

# 가능한 액션 조회
sap ontology MM03 --actions
```

필드 검색 결과:
```json
{
  "screen_number": 4000,
  "field": {
    "id": "wnd[0]/usr/ctxtRMMG1-MATNR",
    "name": "RMMG1-MATNR",
    "type": "GuiCTextField",
    "label": "Material",
    "changeable": true,
    "required": true,
    "semantic": {
      "sap_field_name": "MATNR",
      "description": "자재 마스터 번호",
      "synonyms": ["자재번호", "품번", "Material Number", "Part Number"]
    }
  }
}
```

### 6.3 온톨로지 학습 루프

매크로 실행이나 수동 작업 시 `sap learn`을 호출하면:
1. 현재 화면의 UI 구조를 `GetObjectTree()`로 스캔
2. 기존 온톨로지가 있으면 시맨틱 정보를 병합
3. 화면 전환 경로를 자동 기록 (시간공간)
4. 다음에 같은 트랜잭션 실행 시 AI가 화면 구조를 미리 알 수 있음

---

## 7. Claude Code MCP 연동

### 7.1 설정

Windows의 Claude Code `settings.json`에 추가:

```json
{
  "mcpServers": {
    "sap-gui": {
      "command": "python",
      "args": ["-m", "sap_cli.mcp_server"]
    }
  }
}
```

### 7.2 사용 가능한 MCP 도구 (16개)

#### 기본 SAP 조작 (8개)

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

#### 상태/데이터 (3개)

| 도구 | 설명 |
|------|------|
| `sap_get_status` | 상태바 메시지 |
| `sap_table_read` | 테이블 데이터 읽기 |
| `sap_ontology_query` | 온톨로지 조회 |

#### 학습 (1개)

| 도구 | 설명 |
|------|------|
| `sap_learn` | 현재 화면을 온톨로지로 학습 |

#### 매크로 (4개)

| 도구 | 설명 |
|------|------|
| `sap_macro_control` | 녹화 제어 (start/finish/cancel/status/undo) |
| `sap_macro_record_step` | 녹화 중 단계 기록 + SAP 실행 |
| `sap_macro_play` | 매크로 재생 |
| `sap_macro_list` | 매크로 목록 조회 |

---

## 8. MCP 도구를 활용한 AI 자동화 시나리오

### 시나리오 1: 자연어로 SAP 조작

사용자가 Claude Code에서 채팅:

```
"MM03에서 자재 100-100을 조회해줘"
```

Claude Code가 MCP 도구를 순차 호출:

```
1. sap_connect()
2. sap_run(tcode="MM03")
3. sap_screen(fields_only=true)  → 입력 필드 확인
4. sap_set_field(element_id="wnd[0]/usr/ctxtRMMG1-MATNR", value="100-100")
5. sap_send_vkey(vkey=0)  → Enter
6. sap_send_vkey(vkey=0)  → 뷰 선택 Enter
7. sap_screen()  → 결과 화면 읽기
8. sap_get_status()  → 성공 확인
```

### 시나리오 2: 매크로 녹화 (채팅 기반)

```
사용자: "지금부터 내가 하는 SAP 작업을 녹화해. 매크로 이름은 '자재조회'"

Claude: sap_macro_control(command="start", name="자재조회")

사용자: "MM03 실행해"

Claude: sap_macro_record_step(action="run", target="MM03", description="MM03 실행")

사용자: "자재번호 필드에 100-100 넣어"

Claude: sap_screen(fields_only=true)  → 필드 ID 확인
        sap_macro_record_step(action="set", target="wnd[0]/usr/ctxtRMMG1-MATNR",
                              value="100-100", description="자재번호 입력")

사용자: "엔터 두번"

Claude: sap_macro_record_step(action="vkey", target="0", description="Enter")
        sap_macro_record_step(action="vkey", target="0", description="Enter - 뷰 선택")

사용자: "녹화 끝. 자재번호를 변수로 만들어"

Claude: sap_macro_control(command="finish", name="자재조회",
                          variables={"100-100": "조회할 자재번호"})
```

### 시나리오 3: 매크로 재생 (변수 변경)

```
사용자: "자재조회 매크로 실행해. 자재번호는 200-200으로"

Claude: sap_macro_list()  → 매크로 ID 확인
        sap_macro_play(macro_id="a1b2c3", variables={"matnr": "200-200"})
```

### 시나리오 4: 온톨로지 기반 지능적 조작

```
사용자: "VA01에서 수주를 만들려면 뭘 입력해야 해?"

Claude: sap_ontology_query(tcode="VA01")  → 화면/필드/종속성 정보 반환

Claude 응답: "VA01 수주 생성에는 다음 필드가 필요합니다:
  - 오더유형 (AUART): 필수, 예: OR(표준수주)
  - 판매조직 (VKORG): 필수
  - 유통채널 (VTWEG): 필수
  - 제품군 (SPART): 필수
  그 다음 화면에서:
  - 고객번호 (KUNNR): 필수
  - 납품요청일 (KETDAT): 필수
  - 자재번호/수량: 품목 테이블에 입력"
```

### 시나리오 5: 배치 처리 (매크로 반복 실행)

```
사용자: "이 자재번호 리스트에 대해 자재조회 매크로를 반복 실행해줘:
100-100, 200-200, 300-300, 400-400"

Claude:
  for each material_number:
    sap_macro_play(macro_id="a1b2c3", variables={"matnr": material_number})
    sap_screen()  → 결과 수집
```

---

## 9. 트러블슈팅

### "SAP GUI에 연결할 수 없습니다"

- SAP GUI가 실행 중이고 로그인된 상태인지 확인
- SAP GUI 스크립팅 활성화 확인 (섹션 3 참조)
- 관리자 권한으로 실행 시도

### "필드 설정 실패"

- `sap screen --fields-only`로 정확한 필드 ID 확인
- `changeable: false`인 필드는 값을 설정할 수 없음 (조회 전용)
- 화면이 바뀌면 필드 ID가 달라질 수 있음 → `sap screen`으로 재확인

### "버튼 클릭 후 에러"

- SAP GUI API 특성: 버튼 Press 후 모든 요소 참조가 무효화됨
- 버튼 클릭 후에는 반드시 `sap screen`으로 화면을 다시 읽어야 함
- 이는 이미 `com_wrapper.py`에서 처리됨 (매 호출마다 FindById 새로 호출)

### "매크로 재생 실패"

- `--dry-run`으로 먼저 변수 치환이 올바른지 확인
- 에러 발생 스텝의 `error` 메시지 확인
- SAP 시스템 상태가 녹화 시와 다를 수 있음 (데이터 의존적 화면)
- `expected_screen`이 설정된 경우 화면번호 불일치로 실패할 수 있음

### "GetObjectTree 지원 안 됨"

- SAP GUI for Windows 7.70 PL3 이상 필요
- 미지원 시 자동으로 수동 탐색(Children 재귀 순회)으로 폴백
- 수동 탐색은 느리지만 동일하게 동작

### VKey 번호 레퍼런스

| VKey | 키 | 설명 |
|------|---|------|
| 0 | Enter | 확인/실행 |
| 3 | F3 | 뒤로 가기 |
| 8 | F8 | 실행 (Execute) |
| 11 | Ctrl+S | 저장 |
| 12 | F12 | 취소 |
| 15 | F1 | 도움말 |
| 16 | F4 | 검색 도움말 (Search Help) |

---

## 10. 아키텍처

```
┌─────────────────────────────────────────────┐
│  AI Agent (Claude Code / MCP Client)        │
│  - 자연어 → MCP 도구 호출                     │
│  - JSON 결과 해석 → 다음 액션 결정             │
└──────────────┬──────────────────────────────┘
               │ MCP (stdio)
┌──────────────▼──────────────────────────────┐
│  sap-cli (Python)                           │
│  ┌────────────────────────────────────┐     │
│  │ CLI (typer)  │  MCP Server (mcp)   │     │
│  └──────┬───────┴──────┬──────────────┘     │
│  ┌──────▼──────────────▼──────────────┐     │
│  │ Macro System                       │     │
│  │ ├── Recorder (녹화)                │     │
│  │ ├── Player (재생 + 변수 치환)       │     │
│  │ └── Store (JSON 저장)              │     │
│  └──────┬─────────────────────────────┘     │
│  ┌──────▼─────────────────────────────┐     │
│  │ COM Wrapper (win32com)             │     │
│  │ └── SAP GUI Scripting API          │     │
│  └──────┬─────────────────────────────┘     │
│  ┌──────▼─────────────────────────────┐     │
│  │ Ontology Layer                     │     │
│  │ ├── Schema (Object/Link/Action)    │     │
│  │ ├── Meta-Edge (필드 종속성 규칙)    │     │
│  │ ├── Graph (GraphRAG 탐색)          │     │
│  │ ├── Analysis Space (6공간 분석)    │     │
│  │ └── Knowledge (학습된 JSON 데이터)  │     │
│  └────────────────────────────────────┘     │
└──────────────┬──────────────────────────────┘
               │ COM/ActiveX
┌──────────────▼──────────────────────────────┐
│  SAP GUI for Windows                        │
└─────────────────────────────────────────────┘
```

### 디렉토리 구조

```
sap_cli/
├── pyproject.toml
├── README.md
├── sap_cli/
│   ├── cli.py                  # CLI 엔트리포인트 (22개 커맨드)
│   ├── com_wrapper.py          # SAP GUI COM 래퍼
│   ├── mcp_server.py           # MCP 서버 (16개 도구)
│   ├── learner.py              # 온톨로지 학습 엔진
│   ├── macro/
│   │   ├── schema.py           # 매크로 데이터 모델
│   │   ├── recorder.py         # 매크로 녹화기
│   │   ├── player.py           # 매크로 재생기
│   │   ├── store.py            # 매크로 저장소
│   │   └── library/            # 저장된 매크로 (JSON)
│   └── ontology/
│       ├── schema.py           # 온톨로지 4요소 스키마
│       ├── meta_edge.py        # 메타엣지 규칙 엔진
│       ├── analysis_space.py   # 6공간 분석 프레임워크
│       ├── graph.py            # GraphRAG 관계 그래프
│       ├── store.py            # 온톨로지 저장소
│       └── knowledge/          # 학습된 도메인 지식
│           ├── transactions/   # MM03.json, VA01.json ...
│           ├── meta_edges/     # 메타엣지 규칙
│           └── patterns/       # 공통 패턴 (메타온톨로지)
```
