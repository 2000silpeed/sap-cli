# SAP CLI - AI를 위한 SAP GUI 자동화 도구

SAP GUI Scripting API를 CLI/MCP로 래핑하여, AI 에이전트가 SAP 시스템을 구조적으로 조작할 수 있게 하는 도구.

SOS(Study Ontology System)의 온톨로지 사상을 적용하여, 단순 스크립트 실행이 아닌 **SAP 도메인을 이해하는 AI**를 구축한다.

인터랙티브 지도·계획·실행 가이드는 [sap-cli-interactive-guide.html](./sap-cli-interactive-guide.html)에서 바로 볼 수 있다.

## AI로 시작하는 10분 퀵스타트

SAP 업무는 익숙하지만 스크립팅이 처음이라면 명령어를 외울 필요가 없습니다. SAP GUI가
로그인된 Windows PC에서 SAP CLI에 연결된 AI에게 아래 순서대로 요청하면 됩니다. 작업은
반드시 테스트 client의 조회성 TCode부터 시작하세요.

1. 지도 만들기: “`MM03` 조회 화면의 지도를 만들어줘. 저장·변경 동작은 하지 말고,
   화면 선택이 필요하면 나에게 물어봐.”
2. 계획 검토: “만든 지도에서 자재 기본 데이터를 조회하는 계획을 보여줘. 아직 실행하지
   마.”
3. 실행: 단계와 입력값을 확인한 뒤 “방금 승인한 계획 그대로 실행해줘.”라고 요청합니다.

지도에 없는 화면이 나오면 AI가 멈춰서 현재 화면으로 이동해 달라고 묻습니다. 사용자가
이동을 마치면 AI가 그 화면과 경로를 지도에 덧붙입니다. 전체 안내와 바로 쓸 수 있는
요청문은 [AI 초보자용 지도·계획·실행 가이드](AI_MAP_PLANNING_BEGINNER_GUIDE.md)에 있습니다.

---

## 목차

1. [사전 요구사항](#1-사전-요구사항)
2. [설치](#2-설치)
3. [SAP GUI 스크립팅 활성화](#3-sap-gui-스크립팅-활성화)
4. [기본 사용법 (CLI)](#4-기본-사용법-cli)
   - 4.1 [SAP 연결 (멀티 시스템 지원)](#41-sap-연결-멀티-시스템-지원)
   - 4.10 [트리 컨트롤 (SPRO 등)](#410-트리-컨트롤-spro-등)
   - 4.11 [AI 네비게이션 레시피](#411-ai-네비게이션-레시피)
   - 4.14 [온톨로지 Q&A (시맨틱 검색)](#414-온톨로지-qa-시맨틱-검색)
   - 4.15 [UI 지도 생성과 지도 기반 계획 실행](#415-ui-지도-생성과-지도-기반-계획-실행)
5. [매크로 녹화/재생](#5-매크로-녹화재생)
6. [온톨로지 시스템](#6-온톨로지-시스템)
   - 6.4 [스크린샷 캡처](#64-스크린샷-캡처-사용자-설명서-제작)
   - 6.5 [업무 프로세스 (BusinessProcess)](#65-업무-프로세스-businessprocess)
7. [Claude Code MCP 연동](#7-claude-code-mcp-연동)
   - 7.3 [데몬 모드 (단일 연결)](#73-데몬-모드-단일-연결)
   - 7.4 [AI 실행 세션 관리](#74-ai-실행-세션-관리)
8. [MCP 도구를 활용한 AI 자동화 시나리오](#8-mcp-도구를-활용한-ai-자동화-시나리오)
9. [트러블슈팅](#9-트러블슈팅)
10. [아키텍처](#10-아키텍처)
11. [테스트](#11-테스트)
12. [AI 스킬·서브에이전트 설치](#12-ai-스킬서브에이전트-설치)

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
git clone https://github.com/2000silpeed/sap-cli.git
cd sap-cli

# 가상환경 생성 및 활성화
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1   # Windows PowerShell
# source .venv/bin/activate   # macOS/Linux

# CLI + MCP + 개발/테스트 의존성까지 설치
python -m pip install -e ".[mcp,dev]"

# 설치 확인
sap --help
```

> **주의**: 반드시 가상환경(.venv) 내에서 설치하세요. 시스템 Python에 직접 설치하면 의존성 충돌이 발생할 수 있습니다.

Windows에서 PowerShell이 `Activate.ps1` 실행을 차단하면 현재 사용자 범위에서
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`를 한 번 적용하세요. SAP GUI
자동화와 COM 연결은 SAP GUI for Windows가 실행 중인 동일한 Windows 사용자 세션에서
수행해야 합니다.

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

### 4.1 SAP 연결 (멀티 시스템 지원)

SAP GUI에 로그인한 상태에서:

```powershell
# 첫 번째 연결의 첫 번째 세션에 연결
sap connect

# 특정 연결/세션 지정
sap connect --connection 0 --session 1

# 시스템 ID(SID)로 자동 연결 — 여러 시스템에 동시 로그인 시 유용
sap connect --system PR1
sap connect --system ERP

# 모든 활성 연결 목록 조회
sap connections
```

출력 예시 (`sap connect`):
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

출력 예시 (`sap connections`):
```json
{
  "connection_count": 2,
  "connections": [
    {"index": 0, "system_name": "PR1", "client": "100", "session_count": 1,
     "sessions": [{"index": 0, "user": "SAPUSER", "transaction": "SESSION_MANAGER"}]},
    {"index": 1, "system_name": "ERP", "client": "200", "session_count": 2,
     "sessions": [{"index": 0, "user": "ADMIN", "transaction": "MM03"}, {"index": 1, "user": "ADMIN", "transaction": "VA01"}]}
  ]
}
```

> **세션 자동 복구**: COM 연결이 끊어지면 이전 연결 파라미터(시스템 ID, 연결/세션 인덱스)로 자동 재연결을 시도합니다. 복구 후 시스템 ID가 달라지면 안전하게 거부합니다.

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

# 필드만 보기 (읽기전용 포함, 테이블 정보 포함)
sap screen --fields-only

# 가능한 액션(버튼/탭)만 보기
sap screen --actions-only

# 패턴으로 화면 요소 검색 (ID, 이름, 라벨, 시맨틱 부분 일치)
sap screen --search "MATNR"
sap screen --search "자재"
sap screen --search "Save"
```

> 모달(팝업)이 떠 있으면 `sap screen`/`sap vkey`가 자동으로 모달(wnd[1])을 대상으로 합니다.
> 탭 안의 필드, 테이블 안의 체크박스/라디오버튼도 스캔됩니다.

출력 예시 (`--fields-only`):
```json
{
  "transaction": "MM03",
  "screen_number": 4000,
  "fields": [
    {
      "id": "wnd[0]/usr/ctxtRMMG1-MATNR",
      "name": "RMMG1-MATNR",
      "type": "GuiCTextField",
      "label": "Material",
      "value": "",
      "changeable": true
    }
  ],
  "tables": [...]
}
```

### 4.5 필드 조작

```powershell
# 필드에 값 설정 (1개씩)
sap set "wnd[0]/usr/ctxtRMMG1-MATNR" "100-100"

# 콤보박스 직접 설정 (key 또는 표시값)
sap set "wnd[0]/usr/cmbLFART" "LF"

# 콤보박스 key/value 목록 확인 후 선택
sap get "wnd[0]/usr/cmbLFART"
sap set "wnd[0]/usr/cmbLFART" "Standard Delivery"

# 여러 필드 한번에 입력 + 자동 Enter (필드명 자동 매핑)
sap fill MATNR=100-100 WERKS=1000 LGORT=0001

# Enter 없이 값만 세팅
sap fill MATNR=100-100 WERKS=1000 --no-enter

# element_id 직접 사용도 가능
sap fill "wnd[0]/usr/ctxtRMMG1-MATNR=100-100" "wnd[0]/usr/ctxtRMMG1-WERKS=1000"

# 필드 값 읽기
sap get "wnd[0]/usr/ctxtRMMG1-MATNR"

# 체크박스 설정 (/chk 접두사 자동 감지)
sap set "wnd[1]/usr/chkUSRM1-AAUSW" "true"

# 라디오버튼 선택 (/rad 접두사 자동 감지)
sap set "wnd[0]/usr/radRMMG1-AUESSION" "true"
```

> `sap fill`은 현재 화면을 스캔하여 필드명(MATNR 등)을 실제 element_id로 자동 매핑합니다. 체크박스(`/chk`)와 라디오버튼(`/rad`)도 자동 감지됩니다.

### 4.5.1 요소 검사와 범용 UI 액션

전용 명령이 없거나 어떤 조작이 가능한지 모를 때는 SAP GUI Scripting
capability 지도를 live element 검사와 함께 사용합니다.

```powershell
# Type/SubType, 현재 값, changeable, 콤보 entries, 가능한 action 확인
sap inspect "wnd[0]/usr/cmbLFART"

# SAP GUI Scripting API 그래프에서 GuiComboBox/select 관련 subgraph 조회
sap scripting-query --type GuiComboBox --intent select --depth 2

# 타입에 맞춰 자동 실행: text field는 Text, combo는 Entries→Key, checkbox는 Selected
sap act "wnd[0]/usr/cmbLFART" set "Standard Delivery"
sap act "wnd[0]/usr/chkSELECT" set true
sap act "wnd[0]/usr/btnSAVE" press

# 같은 기능은 capability catalog에서 자동 생성된 개별 CLI 명령으로도 사용 가능
sap ui-set "wnd[0]/usr/cmbLFART" "Standard Delivery"
sap ui-press "wnd[0]/usr/btnSAVE"
sap ui-append-text "wnd[0]/usr/cntlEDITOR/shell" "추가 텍스트"

# 검색 도움말
sap act "wnd[0]/usr/ctxtRMMG1-MATNR" search_help

# 트리 fallback
sap act "wnd[0]/shellcont/shell" select_node --node-key "00005"
sap act "wnd[0]/shellcont/shell" expand_node --node-key "00001"
```

MCP에서는 `sap_scripting_query`로 API 그래프를 조회하고,
`sap_inspect_element`로 live element capability를 확인한 뒤
`sap_ui_action`으로 `set/select/press/focus/read/search_help/select_node` 등을 실행합니다.
또한 capability catalog에서 발견된 action은 `sap_ui_set`, `sap_ui_press`,
`sap_ui_append_text`, `sap_ui_get_property`처럼 개별 MCP 도구로 자동 노출됩니다.

SAP GUI Scripting API 그래프 seed는 프로젝트에 포함되어 있습니다.

```text
sap_cli/ontology/knowledge/scripting_api/graph.json
```

공식 SAP GUI Scripting API 그래프를 재생성하려면 strict XHTML builder를 사용합니다.
이 명령은 SAP Help의 version-pinned metadata·TOC·각 object/enum page를 읽고,
PL08 golden count와 dangling edge를 검증한 뒤에만 runtime overlay를 기록합니다.

```powershell
# 공식 SAP Help XHTML에서 strict overlay 생성
sap scripting-graph-build `
  --version 800.08 `
  --out sap_cli/ontology/knowledge/scripting_api/graph.generated.json `
  --snapshot-dir .context/sap-help-800.08

# PDF/text best-effort parser는 검토용 별도 파일만 생성 가능
sap scripting-graph-build-legacy sap_gui_scripting_api.txt `
  --out .context/graph.review-required.json `
  --source "https://help.sap.com/doc/9215986e54174174854b0af6bb14305a/800.08/en-US/sap_gui_scripting_api.pdf"
```

Legacy parser는 `graph.generated.json`을 덮어쓸 수 없습니다. 공식 overlay에는
`scripting-graph-build`만 사용하세요.

### 4.6 키보드/버튼

```powershell
# VKey 전송 (모달이 있으면 자동으로 모달에 전송)
sap vkey 0     # Enter
sap vkey 3     # Back (F3)
sap vkey 8     # Execute (F8)
sap vkey 11    # Save (Ctrl+S)
sap vkey 12    # Cancel (F12)

# 버튼 클릭 (요소 ID 필요)
sap press "wnd[0]/tbar[0]/btn[0]"       # 첫 번째 툴바의 첫 번째 버튼
sap press "wnd[0]/tbar[1]/btn[8]"       # 두 번째 툴바의 Execute 버튼

# 탭 선택 (press로 탭 ID를 넘기면 자동으로 Select() 호출)
sap press "wnd[0]/usr/tabsTABSTRIP/tabpTAB01"   # 탭 전환
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

### 4.8 테이블 읽기/선택

```powershell
# 테이블 데이터를 JSON으로 출력
sap table "wnd[0]/usr/tblSAPMV45ATCTRL_U_ERF_AUFTRAG"

# 테이블에서 행 선택 (0-based)
sap table-select "wnd[1]/usr/tblSAPLMGMMTC_VIEW" 0
```

### 4.9 모달(팝업) 처리

```powershell
# 모달 윈도우 확인
sap modal

# 출력: {"has_modal": true, "text": "Do you want to save?"}
# 모달이 있으면 vkey/screen/set이 자동으로 모달(wnd[1])을 대상으로 함
sap screen --fields-only   # → 모달 안의 필드/테이블 표시
sap vkey 0                 # → 모달에 Enter 전송
sap vkey 12                # → 모달에 Cancel
```

### 4.10 트리 컨트롤 (SPRO 등)

```powershell
# 트리 노드 읽기 (sap screen의 trees에서 트리 ID 확인)
sap tree "wnd[0]/shellcont/shell"

# 특정 노드의 자식만 조회 (토큰 절약)
sap tree "wnd[0]/shellcont/shell" --node-key "00001" --depth 1

# 트리 노드 펼치기/접기
sap tree-expand "wnd[0]/shellcont/shell" "00001"
sap tree-collapse "wnd[0]/shellcont/shell" "00001"

# 노드 선택
sap tree-select "wnd[0]/shellcont/shell" "00005"

# 노드 더블클릭 (IMG Activity 진입 등)
sap tree-doubleclick "wnd[0]/shellcont/shell" "00005"

# 트리 내 버튼 클릭 (SPRO 실행 아이콘)
sap tree-press "wnd[0]/shellcont/shell" "00005" "2"
```

SPRO 트리 컬럼: `2` = IMG Activity 실행, `1` = Documentation 조회

### 4.11 AI 네비게이션 레시피

AI가 성공한 SAP 조작 경로를 저장하고, 다음에 같은 요청이 오면 재활용합니다.
매크로(VBS)와 달리 AI에게 힌트를 주는 방식으로, AI가 상황 판단하면서 따라갑니다.

```powershell
# 레시피 검색
sap recipe-search "가격결정"

# tcode/키워드 기반 통합 검색
sap recipe-query --tcode SPRO --keywords "가격결정"

# recipe-query는 BM25 + semantic overlap + tcode/step_kind boost + 품질점수로 hybrid reranking합니다.
# 응답의 retrieval.match_reasons / matched_snippets로 왜 매칭됐는지 확인할 수 있습니다.

# 특정 레시피 상세 실행 계획 조회
sap recipe-show "spro-pricing-condition"

# 저장된 모든 레시피 목록
sap recipe-list

# 미승인 레시피 후보 세션 조회
sap recipe-pending --limit 20

# 레시피 버전 이력 조회
sap recipe-history "spro-pricing-condition"

# 여러 tcode를 걸치는 복합 레시피 검색
sap recipe-chain "SPRO,MM03" --match all

# 레시피 실행 성공 기록
sap recipe-success "spro-pricing-condition"

# 레시피 실행 실패 기록 (반복 실패 시 자동 비활성화 가능)
sap recipe-failure "spro-pricing-condition" --reason "화면 버튼 ID 변경"

# 비활성화된 레시피 수동 재활성화 (기본: 실패 카운트 초기화)
sap recipe-enable "spro-pricing-condition"

# 실패 카운트를 유지하면서 재활성화
sap recipe-enable "spro-pricing-condition" --keep-failure-count

# 레시피 저장 (AI가 MCP로 자동 저장하지만, 수동도 가능)
sap recipe-save "spro-pricing-condition" "SPRO > SD > 가격결정" '[{"tool":"sap_run","args":{"tcode":"SPRO"},"hint":"SPRO 진입"}]' -k "가격결정,pricing"

# replay 최종 검증 기준까지 함께 저장
sap recipe-save "va02-change" "VA02 수주 변경" '[{"tool":"sap_run","args":{"tcode":"VA02"}}]' --replay-success-criteria '{"expected_tcode":"VA02","expected_screen_number":"4001"}'

# replay 실행 전 계획 검토
sap recipe-replay-plan "va02-change" --current-tcode VA02 --current-screen 4001

# replay 실제 실행 + 자동 검증/성공·실패 보고
sap recipe-replay-execute "va02-change" --success-criteria '{"expected_tcode":"VA02"}'

# 긴 JSON은 @파일 입력 가능
sap recipe-replay-execute "va02-change" --success-criteria @criteria.json --no-save-success-criteria

# SAP 화면 변경 후 새 steps로 버전업
sap recipe-version-up "va02-change" @steps.json --change-note "화면 버튼 ID 변경"

# 레시피 삭제
sap recipe-delete "spro-pricing-condition"
```

### CLI/bash 실행을 recipe 후보로 기록

터미널에서 직접 수행한 `sap ...` 명령을 recipe 후보로 남기려면 작업 앞뒤를
`session-start` / `session-end`로 감싼다. 세션 중 실행된 CLI step에는 원문
`cli_invocation`과 `raw_argv`가 같이 저장되어, 나중에 어떤 bash 명령을 실행했는지
그대로 확인할 수 있다.

```powershell
sap session-start "MM03 자재 조회 성공 경로"
sap connect --system PR1
sap run MM03
sap screen --fields-only
sap set "wnd[0]/usr/ctxtRMMG1-MATNR" "100-100"
sap vkey 0
sap status
sap session-end --outcome success --summary "MM03 조회 성공"
```

보조 명령:

```powershell
sap session-active
sap session-use <session_id>
sap session-clear
sap session-mark "입력 CSV 생성" --kind bash --cli "python scripts/prepare_input.py" --no-requires-sap
sap session-excel "업로드 대상 범위 확인" --action check --workbook input.xlsx --sheet Sheet1 --range A1:D20
sap session-list
sap session-show <session_id>
sap session-candidates
sap session-approve <session_id>
```

`session-mark`는 SAP 화면 조작이 아니지만 recipe가 따라야 하는 준비/검증/외부 CLI
단계를 남길 때 사용한다. 저장된 recipe step에는 `step_kind`, `followable`,
`requires_sap`, `cli_invocation`이 함께 들어간다.
Excel 관련 단계는 `session-excel`로 더 간단히 남길 수 있으며 `step_kind=excel`,
`requires_sap=false`가 자동 지정된다.

CLI에는 조회 중심 recipe 명령과 운영자 품질관리용 `recipe-success`,
`recipe-failure`, `recipe-enable`을 노출합니다. 또한 replay 운영을 위해
`recipe-replay-plan`, `recipe-replay-execute`, `recipe-version-up`도 CLI에서 사용할 수
있습니다. 긴 steps/success criteria JSON은 `@steps.json`, `@criteria.json`처럼 파일로 넘길 수 있습니다.

AI MCP 사용 흐름:
1. 사용자 요청 → AI가 `sap_ontology_query(tcode, action_search="키워드")`로 온톨로지 내 경로 검색
2. 경로 있으면 `execution_plan` 따라 즉시 실행 (별도 탐색 불필요)
3. 경로 없으면 `sap_recipe_search`로 외부 레시피 검색, 그래도 없으면 직접 탐색
4. 성공 시 `sap_recipe_save`로 저장 → 온톨로지 `action_paths`에 자동 연결
   - `replay_success_criteria`를 함께 저장하면 이후 `sap_recipe_replay_execute`가 같은 최종 검증 기준을 재사용합니다.
   - `sap_session_approve`로 세션을 레시피화할 때는 세션의 마지막 transaction/screen에서 `expected_tcode`/`expected_screen_number`를 자동 추론합니다.
   - `sap_recipe_version_up`은 기준 미지정 시 기존 기준을 유지하고, 새 `replay_success_criteria`를 전달하면 갱신 또는 초기화할 수 있습니다.
5. `sap_recipe_success`로 성공 카운트 증가

온톨로지와 레시피의 관계:
- `sap_ontology_query SPRO` → `available_actions`에 저장된 모든 경로 표시
- `sap_ontology_query SPRO --action-search "shipping point"` → 해당 경로의 실행 계획 반환
- 레시피 저장 시 온톨로지 `action_paths`에도 자동 등록

### 4.13 스크린샷 캡처

```powershell
# 현재 SAP GUI 화면 캡처
sap capture                      # 자동 파일명
sap capture initial              # 라벨 지정
sap capture --output ./step1.png # 경로 지정

# 저장된 스크린샷 목록
sap captures                     # 전체
sap captures MM03                # tcode별 필터
```

저장 위치: `~/.sap_cli/screenshots/{tcode}/{tcode}_{step}_screen{num}_{label}.png`

### 4.12 전체 시나리오 예시: MM03 자재 조회

```powershell
# 1. SAP 연결
sap connect

# 2. MM03 트랜잭션 실행
sap run MM03

# 3. 자재번호 한번에 입력 (필드명 자동 매핑 + Enter)
sap fill MATNR=100-100
# 또는 기존 방식: sap set "wnd[0]/usr/ctxtRMMG1-MATNR" "100-100" && sap vkey 0

# 6. 뷰 선택 — 팝업인 경우 (모달 자동 감지)
sap modal
sap screen --fields-only       # 팝업 안 테이블/체크박스 확인
sap table-select "wnd[1]/usr/tblSAPLMGMMTC_VIEW" 0  # 첫 번째 뷰 선택
sap vkey 0                     # Enter (모달에 자동 전송)

# 7. 기본데이터 화면 확인
sap screen --search "MAKTX"

# 8. 자재 내역 읽기
sap get "wnd[0]/usr/txtMARA-MAKTX"

# 9. 상태 확인
sap status

# 10. 뒤로 가기
sap vkey 3
```

### 4.11 Transcript 조회와 재실행 검토

AI에게 SAP 도구를 주면 "무엇을 실행했는가"뿐 아니라 "왜 그렇게 실행했고 다시 해도 되는가"가 중요합니다.

`sap_cli`는 모든 호출을 `~/.sap_cli/transcripts/`에 JSONL로 적재하고, 그 기록을 다시 읽어 review/replay planner로 사용할 수 있습니다.

```powershell
# 최근 10개 transcript 요약 조회
sap transcript --limit 10

# 승인 필요했던 항목만 보기
sap transcript --outcome approval_required

# MM03 관련 실행만 보기
sap transcript --command run --text MM03

# 가장 최근 set 단계에 대한 재실행 검토 계획 생성
sap transcript-plan --command set --index 0
```

`sap transcript-plan`은 실제 재실행을 하지 않습니다. 대신 아래 내용을 요약합니다.

- 위험도 (`low` / `medium` / `high`)
- 원본 entry의 before/after transaction과 screen
- 해당 transcript에서 추론한 트랜잭션의 ontology 존재 여부와 조회 힌트
- 사람 승인 필요 여부
- 재실행 전에 확인할 조건
- 권장 확인 순서 (`sap session`, `sap ontology MM03`, `sap status`, `sap modal`, `sap screen --fields-only`)
- 원본 단계를 다시 실행하려면 어떤 CLI 명령이 필요한지

### 4.14 온톨로지 Q&A (시맨틱 검색)

SAP 도메인 질문에 대해 축적된 온톨로지를 기반으로 구조화된 답변을 제공합니다.

```powershell
# 자연어로 SAP 질문하기
sap ontology-ask "MM03에서 자재번호를 입력하는 필드는?"
sap ontology-ask "수주를 생성하려면 어떤 필드가 필수인가?"

# 온톨로지 관계 탐색
sap ontology-relationships MM03 --type field_dependency
sap ontology-relationships VA01 --type document_flow

# 필드 종속성 조회
sap field-dependencies MM03 MATNR

# 문서 흐름 조회 (트랜잭션 간 연결)
sap document-flow VA01

# 프로세스 경로 탐색
sap process-path MM01 MIRO
```

MCP 도구로도 동일하게 사용할 수 있습니다:

```
sap_ontology_ask(question="SPRO에서 출하 포인트를 설정하는 경로는?")
sap_ontology_relationships(tcode="MM03", relationship_type="field_dependency")
sap_field_dependencies(tcode="VA01", field_name="KUNNR")
sap_document_flow(tcode="VA01")
sap_process_path(from_tcode="MM01", to_tcode="MIRO")
```

> 온톨로지 Q&A는 SAP 연결 없이도 동작합니다 — 저장된 온톨로지 JSON에서 직접 답변을 생성합니다.

### 4.15 UI 지도 생성과 지도 기반 계획 실행

이 기능은 한 번 수집한 화면 구조를 버전이 있는 UI 지도로 보관하고, 이후 자연어
요청을 그 지도에서 실행 계획으로 먼저 확정한 뒤 한 번에 실행합니다. 실행 단계마다
`sap screen`으로 화면 전체를 다시 읽지 않습니다. daemon이 유지한 SAP GUI 연결로
TCode·상태바·모달·대상 요소의 가시성만 가볍게 확인합니다.

지도는 SAP 시스템, client, 언어, TCode별로 분리됩니다. 다른 시스템이나 언어에서
만든 지도를 재사용하지 않으며, 지도 버전이나 fingerprint가 바뀌면 기존 계획은
실행 전에 폐기됩니다.

저장소는 지도 전체를 하나의 그래프로 관리하되, 화면 상태와 상태별 전이를 따로 주소화해
보관합니다. `plan-preview`는 최신 지도 포인터와 작은 구조 색인에서 후보를 찾고, 시작
화면부터 목표까지 필요한 연결 부분만 읽습니다. `plan-execute`도 계획이 참조한 상태와
전이만 읽습니다. 전체 지도 fingerprint는 최신 포인터로 계속 검증하므로, 읽지 않은
부분이 바뀐 경우에도 이전 계획은 실행하지 않습니다. 기존 지도는 처음 부분 조회할 때
한 번만 그래프 저장 형식으로 보완됩니다.

#### 1) daemon과 SAP 세션 준비

SAP GUI에 로그인한 뒤 daemon을 한 번만 시작합니다. 이후 지도 수집과 계획 실행은
각 단계마다 Python 프로세스나 COM 연결을 새로 만들지 않고 이 daemon을 사용합니다.

```powershell
sap daemon start
sap connect
sap session
```

#### 2) TCode의 기준 화면 수집

가장 안전한 시작은 `passive` 모드입니다. 해당 TCode를 열고 현재 화면의 필드,
탭, 버튼, 툴바, 테이블/ALV, 트리, 콤보박스 같은 구조 정보를 값 없이 수집합니다.
`map-id`는 같은 업무 화면군에서 계속 사용할 이름입니다.

```powershell
sap map-traverse-start MM03 --map-id mm03-readonly --mode passive
sap map-job-status <job-id>
sap map-report mm03-readonly MM03
```

#### 저장된 화면 연결 지도 보기

화면 노드와 다음 화면으로 이동하는 연결을 읽기 쉬운 outline과 Mermaid 그래프로
확인하려면 다음 명령을 사용합니다. 업무값과 관찰 파라미터는 출력하지 않습니다.

```powershell
sap map-view mm03-readonly MM03
```

MCP에서는 `sap_map_view`를 호출하면 같은 구조를 받습니다. 응답의 `mermaid`는
대화형 AI 화면에서 그대로 그래프로 렌더링할 수 있고, `outline`은 화면별 다음 경로를
텍스트로 확인할 때 사용합니다.

자연어 MCP 요청에서 `sap_map_traverse_start`의 mode를 생략하면 기본값은 `guided`입니다.
AI는 테스트 client 확인을 먼저 받고 안전 후보를 하나씩 물어야 하며, 단순 `passive`
한 화면 수집으로 지도 작업을 끝내면 안 됩니다.

`passive` 다음에는 테스트 client에서 `guided` 모드를 권장합니다. 이 모드는 안전하게
되돌릴 수 있는 탭·트리·스크롤 후보를 목록으로 보여 주고, 사용자가 고른 하나만 실행해
다음 화면을 지도에 추가합니다. 선택 값은 업무 데이터가 아니라 구조적 candidate ID뿐이며,
다음 후보가 있으면 다시 멈춥니다.

```powershell
sap map-traverse-start MM03 --map-id mm03-readonly --mode guided --test-client-confirmed
sap map-job-status <job-id>
sap map-traverse-resume <job-id> '{"<resume-token>":{"candidate_id":"<choices의 candidate_id>"}}'
```

기본 `--semantic-review`는 외부 LLM API를 호출하지 않습니다. 화면을 새로 수집할 때
로컬 온톨로지의 화면·필드와 값 없는 UI 구조를 비교해 `semantic_review` 질문을 만들고,
MCP AI가 `accepted_candidate_ids`만 선택해 의미 연결을 확정합니다. AI는 후보 밖의
자유 문장이나 업무값을 지도에 기록할 수 없습니다. 의미 검토가 끝나면 같은 화면의
`guided_candidate_selection` 목록이 이어집니다.

```json
{
  "<semantic-resume-token>": {
    "accepted_candidate_ids": ["<semantic_review choices의 candidate_id>"]
  }
}
```

`guided` 흐름을 테스트 client에서 충분히 확인한 뒤에만, 같은 안전 후보를 연속으로
확장하는 `reversible` 모드를 사용합니다. 이 경우에도 state 수·깊이·시간 한도를 함께 둡니다.

```powershell
sap map-traverse-start MM03 --map-id mm03-readonly --mode reversible --test-client-confirmed --budget-json '{"max_states":100,"max_depth":5,"max_seconds":300}'
sap map-job-status <job-id>
sap map-report mm03-readonly MM03
```

탭은 이미 활성 상태로 수집한 대상에 다시 전환·스캔·복원하지 않습니다. 예를 들어 세 개의
탭이 서로 이동 후보로 보이더라도, 첫 도달 경로만 기록하고 각 탭 내부의 필드·ALV·분기는
계속 수집합니다. 이 동작은 같은 탭 사이를 반복 왕복하는 시간을 줄입니다.

버튼, 메뉴, 툴바/ALV 항목을 눌러 다음 화면으로 가는 분기는 `guided`에서도 추측하지
않습니다. `save`, `post`, `delete`, `release`, `send`, `print` 같은 변경·외부 전송
동작은 어떤 모드에서도 지도 수집 대상으로 허용되지 않습니다. 조회성 분기만 정확한
source state, logical ID, SAP ID, control type, 복귀 VKey를 지정해 `allowlisted`
모드로 추가합니다. 전체 규칙 예시는 [Windows 검증 가이드](WINDOWS_SAP_VERIFICATION.md)에 있습니다.

#### 3) 누락 화면을 사용자와 함께 지도에 보강

지도 수집 job이 `needs_user_input`으로 멈추면 결과의 `resume_token`, `choices`를
확인합니다. 사용자가 SAP GUI에서 누락 화면으로 직접 이동한 뒤, 다음 명령으로 그
상태를 추가합니다. 값은 넘기지 않고 `continue`와 선택한 구조적 전이만 저장하므로
업무 데이터가 지도에 남지 않습니다.

```powershell
sap map-augment-start mm03-readonly MM03 <from-state-id> "분류 탭의 조회 화면"
sap map-job-status <job-id>
sap map-traverse-resume <job-id> '{"<resume-token>":{"continue":true,"transition":{"action":"press","target_logical_id":"<choice의 logical id>"}}}'
sap map-report mm03-readonly MM03
```

출발 화면에 버튼·메뉴·탭 또는 스캔된 ALV 툴바/컨텍스트 메뉴처럼 재실행 가능한 선택지가
있으면 `transition` 선택은 필수입니다. ALV 선택지는 `action`, `target_logical_id`,
`item_id`를 그대로 보냅니다. `continue`만 보내면 화면은 기록할 수 있어도 이후 계획이
재사용할 수 없는 수동 경로가 되므로, 이제 job이 그 답변을 받아들이지 않습니다. 저장·전기·
삭제·승인·전송·인쇄 표식의 경로는 선택 및 allowlist 대상에서 차단됩니다.

이 과정을 반복하면 지도의 `states`, `transitions`, 미해결 화면(`unreachable`),
사용자 확인 대기 항목을 `map-report`에서 확인할 수 있습니다. 모든 화면을 무작정
순회하는 대신, 안전하게 확인된 경로와 사용자가 가르친 경로만 늘어납니다.

#### 4) 자연어 요청을 실행 전 계획으로 변환

지도 수집이 끝난 뒤에는 자연어 요청과 필요한 업무값을 `plan-preview`로 보냅니다.
이 명령은 SAP를 조작하지 않고 저장된 지도와 레시피에서 전체 step, 변수, 필요한
승인, 지도 버전/fingerprint를 확정합니다. `validation.valid`가 `true`이고
`plan_ref`가 있어야 실행할 수 있습니다.

```powershell
$preview = sap plan-preview "MM03에서 자재 기본 데이터를 조회해줘" --tcode MM03 --map-id mm03-readonly --goal "기본 데이터" --values-json '{"material":"TEST_MATNR"}' | ConvertFrom-Json
$preview.validation
$preview.plan.steps
$preview.plan_ref
```

지도에 목표 필드나 필요한 전이가 없다면 계획은 실행하지 않고 `map_missing`,
`goal_not_found`, `needs_user_input` 같은 오류 또는 질문을 반환합니다. 이때 3단계로
돌아가 지도를 보강한 뒤 새 계획을 만듭니다.

자연어 목표가 지도 레이블보다 길면 planner는 저장된 경로에서 하나로만 확정되는 짧은
목표 표현을 제한적으로 다시 찾습니다. 후보가 여러 개면 `goal_selection_required`
또는 `goal_ambiguous`의 `questions` 안에 화면·요소·경로와 `use_goal_query`가 반환됩니다.
임의로 경로를 넓히지 않고 먼저 이 선택지를 묻습니다. 사용자는 해당 값을 `--goal` 또는
다음 AI 요청에 지정해 목적지를 확정합니다.

#### 5) 승인 후 확정 계획을 한 번에 실행

유효한 미리보기에는 `activation`이 함께 반환됩니다. AI/MCP는 사용자가 확인한 뒤
`activation.arguments`를 `sap_plan_execute`에 그대로 넘기면 됩니다. CLI에서는 아래처럼
`plan_ref`를 그대로 전달할 수 있습니다. `execution_id`를 생략하면 자동 발급하며, 같은
명시적 `execution_id`를 다시 보내면 중복 SAP 조작 대신 이전 결과를 돌려줍니다.

```powershell
sap plan-execute --plan-ref-json ($preview.plan_ref | ConvertTo-Json -Compress) --verify cheap
```

변경, 저장, 전표 생성, 승인, 외부 전송이 포함된 계획은 미리보기 결과의
`required_approvals`에 있는 step만 관리자가 승인해야 합니다. 승인 토큰은 특정
계획 hash와 실행 agent에 묶인 일회용 값입니다.

```powershell
$approval = sap plan-approve $preview.plan_ref.plan_id $preview.plan_ref.plan_hash <실행-agent> '["<승인할 step-id>"]' | ConvertFrom-Json
sap plan-execute --plan-ref-json ($preview.plan_ref | ConvertTo-Json -Compress) --verify cheap --approval-token $approval.approval_token
```

기본값 `--verify cheap`은 전체 화면 스캔을 하지 않는 빠른 실행 경로입니다. locator가
달라지거나 지도 scope가 맞지 않으면 실패하고 새 미리보기 또는 지도 보강을 요구합니다.
`--verify full`은 마지막 검증을 위해 화면을 다시 읽을 수 있으므로, 조사·검증 목적에만
사용합니다.

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

# 같은 의미의 자연어형 매크로 액션
sap record-step enter enter --desc "Enter로 확인"

# 테이블의 세 번째 행 선택 (행 번호는 0-based)
sap record-step select-row "<table element ID>" --row 2 --desc "대상 행 선택"

# 조회 결과를 output 레코드에 저장할 필드 기록
sap record-step read-field "wnd[0]/usr/ctxtRMMG1-MATNR" --as material_number

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
| `press` | 버튼 클릭 / 탭 선택 | 버튼 또는 탭 ID | - |
| `vkey` | 키보드 키 전송 | VKey 번호 (0=Enter) | - |
| `enter` | Enter 전송 (VKey 0) | `enter` | - |
| `read-field` | 필드 값을 읽어 실행 결과에 캡처 | 필드 ID | `--as` 결과 이름 |
| `checkbox` | 체크박스 토글 | 체크박스 ID | `true` 또는 `false` |
| `radio` | 라디오 버튼 선택 | 라디오 ID | - |
| `combo` | 드롭다운 선택 | 콤보박스 ID | 선택할 값 |
| `tab` | 탭 선택 | 탭 ID | - |
| `menu` | 메뉴 선택 | 메뉴 ID | - |

| `table-select` / `select-row` / `table-row` | 테이블 행 선택 | 테이블 ID | 행 번호 (0-based) |

사용자가 “n번째 행을 선택해”, “이 행을 클릭해”, “선택한 행으로 진행해”라고 말하면
`select_row`로 해석한다. 먼저 `sap screen` 또는 `sap inspect <table ID>`로 테이블
element를 확인하고, 행 순서가 동적으로 바뀌는 화면에서는 선택 후 셀 값/화면을
검증한다. “엔터”, “확인하고 진행해”는 `enter` 또는 VKey `0`으로 실행한다.

`read-field` 단계가 포함된 매크로는 실행 결과의 `captured_data`에 읽은 값이 들어간다.
파일에 누적하려면 `--out`을 사용한다. 매 실행 성공 시 한 줄씩 JSONL에 append한다.

```powershell
sap macro-play <macro_id> --var matnr=100-100 --out .\output\materials.jsonl

# JSON 배열 또는 JSONL 입력으로 반복 수집
sap macro-play <macro_id> --batch .\input\materials.jsonl --out .\output\materials.jsonl
```

`--batch`의 각 줄은 매크로 변수 객체다. 각 실행은 독립적으로 preflight/검증되고,
성공한 `captured_data`만 결과 JSONL에 한 줄씩 추가된다. 실패한 항목은 전체 반복을
중단하지 않고 해당 결과에 오류를 남긴다.

### 5.6 fill 명령으로 간단하게

매크로 녹화 없이도 `sap fill`로 여러 필드를 한번에 입력할 수 있습니다:

```powershell
sap run VA01
sap fill AUART=OR VKORG=1000 VTWEG=10 SPART=00
# → 4개 필드 세팅 + 자동 Enter → 오더 Overview 화면으로 이동
sap fill KUNNR=1001 KETDAT=31.12.2026 BSTNK=PO-2026-001
# → 고객/납품일/PO번호 입력 + Enter
```

### 5.7 실전 예시: VA01 수주 생성 매크로

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

중요한 점은, 온톨로지와 transcript는 경쟁 관계가 아니라 서로 다른 층이라는 것입니다.

- 온톨로지: "이 트랜잭션은 보통 어떤 화면/필드/액션으로 구성되는가"
- transcript: "이번 실행에서는 실제로 어떤 화면에서 무엇을 했고 결과가 어땠는가"
- planner: "그 기록을 다시 실행하려면 무엇을 먼저 확인해야 하는가"

즉 온톨로지는 구조적 기억이고, transcript는 실행 기억입니다. `sap_cli`는 이 둘을 함께 유지합니다.

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

## 6.4 스크린샷 캡처 (사용자 설명서 제작)

SAP GUI 화면을 단계별로 캡처하여 사용자 설명서 소재를 생성합니다.

```powershell
# 현재 화면 캡처
sap capture                      # 자동 파일명
sap capture initial              # 라벨 지정

# 저장된 스크린샷 목록
sap captures                     # 전체
sap captures VL03N               # tcode별 필터

# 특정 경로에 저장
sap capture --output ./docs/step1.png
```

**설명서 제작 워크플로우 예시:**

```powershell
sap run VL03N
sap capture 01_초기화면
sap fill VBELN=80000123
sap capture 02_번호입력
sap vkey 0
sap capture 03_상세화면
sap captures VL03N
# → 3개 스크린샷 경로 확인
```

저장 위치: `~/.sap_cli/screenshots/{tcode}/`
파일명 규칙: `{tcode}_{step}_screen{num}_{label}.png`

---

## 6.5 업무 프로세스 (BusinessProcess)

온톨로지가 **트랜잭션 1개**의 화면/필드 구조를 저장한다면, 업무 프로세스는 **여러 트랜잭션을 연결한 end-to-end 워크플로우**를 구조화한다.

실무에서 SAP 작업은 단일 트랜잭션이 아닌 일련의 흐름이다:
- 자재 조달: MM01(자재 생성) → ME21N(구매오더) → MIGO(입고) → MIRO(송장검증)
- 판매 처리: VA01(수주) → VL01N(출하) → VF01(대금청구)

### 6.5.1 핵심 개념

- **ProcessStep**: 프로세스의 각 스텝 = 트랜잭션 1회 실행. 고정 입력값과 예상 출력 필드를 정의한다.
- **DataFlow**: 스텝 간 데이터 전달. 앞 스텝의 출력(예: 자재번호)이 뒤 스텝의 입력으로 자동 연결된다.

### 6.5.2 프로세스 조회

```powershell
# 등록된 프로세스 목록
sap process-list

# 프로세스 상세 조회
sap process-show procurement_basic
```

출력 예시:
```json
{
  "id": "procurement_basic",
  "name": "기본 자재 조달 프로세스",
  "module": "MM",
  "steps": [
    {"step_number": 0, "tcode": "MM01", "description": "자재 마스터 생성",
     "expected_output": ["MATNR"]},
    {"step_number": 1, "tcode": "ME21N", "description": "구매오더 생성",
     "expected_output": ["EBELN"]},
    {"step_number": 2, "tcode": "MIGO", "description": "입고 처리"},
    {"step_number": 3, "tcode": "MIRO", "description": "송장 검증"}
  ],
  "data_flows": [
    {"source_step": 0, "source_field": "MATNR", "target_step": 1, "target_field": "MATNR",
     "description": "생성된 자재번호를 구매오더에 입력"},
    {"source_step": 1, "source_field": "EBELN", "target_step": 2, "target_field": "EBELN",
     "description": "구매오더번호를 입고에 참조"}
  ]
}
```

### 6.5.3 프로세스 실행 (ProcessExecutor)

프로세스 정의가 "설계도"라면, ProcessExecutor는 **스텝 간 데이터를 자동 연결하는 런타임**이다.
AI 에이전트가 각 스텝을 실행하고 결과를 보고하면, 다음 스텝에 넣을 입력값을 자동으로 알려준다.

```powershell
# 1. 프로세스 실행 시작
sap process-start procurement_basic
# → status: running, current_step: 0, pending_inputs: {MTART: "ROH"}

# 2. AI가 MM01 실행 → 자재 생성 → 상태바에서 번호 추출
#    sap run MM01 → 필드 입력 → 저장
sap process-step 0 --outputs '{"MATNR": "100-100"}'
# → context에 저장, pending_inputs: {MATNR: "100-100"} (DataFlow 자동)

# 3. 다음 스텝 입력 확인
sap process-status
# → current_step: 1, current_tcode: ME21N, pending_inputs: {MATNR: "100-100"}

# 4. AI가 ME21N 실행 → MATNR 입력 → 구매오더 생성
sap process-step 1 --outputs '{"EBELN": "4500012345"}'

# 5. ... 반복 → 마지막 스텝 완료 시 실행 기록 자동 저장
```

### 6.5.4 MCP 도구

프로세스 관리:
- `sap_process_list` — 프로세스 목록 조회
- `sap_process_show` — 프로세스 상세 조회
- `sap_process_create` — 새 프로세스 등록 (스텝과 데이터 흐름 정의)
- `sap_process_delete` — 프로세스 삭제

프로세스 실행:
- `sap_process_start` — 프로세스 실행 시작, 컨텍스트 초기화
- `sap_process_step` — 스텝 완료 보고 + 출력값 등록 (DataFlow 자동 연결)
- `sap_process_status` — 현재 상태, 다음 입력값(pending_inputs) 조회

### 6.5.5 저장 위치

```
~/.sap_cli/knowledge/processes/
├── procurement_basic.json     # 자재 조달 (MM01→ME21N→MIGO→MIRO)
└── sales_to_billing.json      # 판매 (VA01→VL01N→VF01)

~/.sap_cli/executions/
└── procurement_basic_20260402T....json  # 실행 기록
```

### 6.5.6 시드 트랜잭션 온톨로지

프로세스의 각 스텝에 해당하는 트랜잭션 온톨로지가 시드로 제공된다:

- 조달: MM03(자재조회), ME21N(구매오더), MIGO(입고), MIRO(송장검증)
- 판매: VA01(수주), VL01N(출하), VF01(대금청구)

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

### 7.2 사용 가능한 MCP 도구

> `~/.sap_cli/policy.yaml` 파일이 존재하면 ReBAC 권한관리가 활성화되어 에이전트별로 허용된 도구만 호출할 수 있습니다. 정책 파일이 없으면 모든 도구가 허용됩니다 (하위 호환).

기본 MCP 표면은 지도 제작·온톨로지 검토·계획·실행에 필요한 핵심 도구만 노출하는
`compact`입니다. 세부 UI 액션, 매크로·레시피 관리, 이벤트, 세션 풀, 디버그 도구는 CLI에
그대로 남아 있지만 MCP 목록에서는 숨깁니다. 기존 전문 자동화가 꼭 필요할 때만 MCP 서버
환경 변수 `SAP_CLI_MCP_SURFACE=full`을 지정합니다.

`compact` 핵심 도구는 `sap_session`, `sap_daemon_status`, `sap_screen`,
`sap_scripting_query`, `sap_run`, `sap_ui_action`, 지도 수집/재개/보강/보고 도구,
`sap_ontology_query`, `sap_plan_preview`, `sap_plan_approve`, `sap_plan_execute`,
`sap_job_status`입니다.


#### 기본 SAP 조작

| 도구 | 설명 |
|------|------|
| `sap_connect` | SAP GUI에 연결 (system 파라미터로 SID 자동 탐색) |
| `sap_connections` | 모든 활성 SAP 연결 목록 조회 |
| `sap_session` | 세션 정보 조회 |
| `sap_daemon_status` | daemon 상태 + 실행 환경(인터프리터/venv, import 가능 여부, 로그 tail) 진단. `action=start/restart`로 기동·재기동 |
| `sap_screen` | 현재 화면 UI 구조 (JSON) |
| `sap_inspect_element` | 단일 element의 Type/SubType/capability/entries 검사 |
| `sap_scripting_query` | SAP GUI Scripting API 그래프 온톨로지 subgraph 조회 |
| `sap_ui_action` | Scripting capability 기반 범용 UI 액션 fallback |
| `sap_run` | 트랜잭션 실행 |
| `sap_command` | SAP 커맨드 필드에 명령 실행 |
| `sap_set_field` | 필드 값 설정 |
| `sap_get_field` | 필드 값 읽기 |
| `sap_press` | 버튼 클릭 / 탭 선택 (GuiTab 자동 감지) |
| `sap_send_vkey` | VKey 전송 |
| `sap_fill_fields` | 여러 필드를 한번에 입력 (필드명 자동 매핑) |
| `sap_ui_*` | capability catalog에서 자동 생성되는 개별 UI action 도구 (`sap_ui_set`, `sap_ui_append_text`, `sap_ui_scroll_to`, `sap_ui_call_method` 등) |

#### 상태/지식/운영

| 도구 | 설명 |
|------|------|
| `sap_get_status` | 상태바 메시지 |
| `sap_modal` | 모달 팝업 존재 여부와 텍스트 |
| `sap_table_read` | 테이블 데이터 읽기 |
| `sap_table_select` | 테이블 행 선택 |
| `sap_table_doubleclick` | 테이블 셀 더블클릭 (ALV 행 진입) |
| `sap_ontology_query` | 온톨로지 조회 + action_search로 경로 검색 |
| `sap_transcript_query` | 최근 transcript 조회 |
| `sap_transcript_plan` | transcript 재실행 검토 계획 생성 |

#### 트리 컨트롤 (SPRO 등)

| 도구 | 설명 |
|------|------|
| `sap_tree_read` | 트리 노드 읽기 (depth/node_key로 범위 제한) |
| `sap_tree_select` | 트리 노드 선택 |
| `sap_tree_doubleclick` | 트리 노드 더블클릭 (IMG Activity 진입) |
| `sap_tree_expand` | 트리 노드 확장 (하위 메뉴 펼치기) |
| `sap_tree_collapse` | 트리 노드 접기 |
| `sap_tree_press` | 트리 내 버튼/아이콘 클릭 (SPRO 실행 아이콘) |

#### AI 네비게이션 레시피

| 도구 | 설명 |
|------|------|
| `sap_recipe_search` | 키워드로 레시피 검색 (작업 전 자동 조회 권장) |
| `sap_recipe_save` | 성공한 네비게이션 경로를 레시피로 저장 |
| `sap_recipe_list` | 저장된 레시피 목록 조회 |
| `sap_recipe_delete` | 레시피 삭제 |
| `sap_recipe_success` | 레시피 실행 성공 기록 (우선순위 상승) |

#### 온톨로지 Q&A (시맨틱 검색)

| 도구 | 설명 |
|------|------|
| `sap_ontology_ask` | 자연어로 SAP 도메인 질문 (온톨로지 기반 답변) |
| `sap_ontology_relationships` | 온톨로지 관계 탐색 (필드 종속성, 문서 흐름 등) |
| `sap_field_dependencies` | 특정 필드의 종속성 조회 |
| `sap_document_flow` | 트랜잭션 간 문서 흐름 조회 |
| `sap_process_path` | 두 트랜잭션 사이의 프로세스 경로 탐색 |
| `sap_ontology_rebuild` | 온톨로지 관계 그래프 재구축 |

#### AI 실행 세션 관리

| 도구 | 설명 |
|------|------|
| `sap_session_start` | AI 실행 세션 시작 (의도/맥락 기록) |
| `sap_session_end` | AI 실행 세션 종료 (결과 기록) |

#### 병렬 멀티 세션 (3개)

여러 AI 에이전트가 각각 독립된 SAP 세션에서 동시에 작업할 수 있습니다.

| 도구 | 설명 |
|------|------|
| `sap_session_pool` | 활성 세션 슬롯 목록 조회 (연결 상태, 에이전트, 트랜잭션) |
| `sap_session_allocate` | 새 슬롯 할당 (slot_id, agent_name, system, create_new_session) |
| `sap_session_release` | 슬롯 해제 (close=true이면 SAP 세션도 종료) |

모든 SAP 도구에 선택적 `slot_id` 파라미터가 추가되어, 특정 슬롯의 SAP 세션에서 작업을 수행할 수 있습니다. 미지정 시 기본 슬롯(`ses0`)을 사용합니다.

```
Agent A: sap_run(tcode="MM03", slot_id="ses0")  → ses0에서 MM03 실행
Agent B: sap_run(tcode="VA01", slot_id="ses1")  → ses1에서 VA01 실행
```

#### 학습

| 도구 | 설명 |
|------|------|
| `sap_learn` | 현재 화면을 온톨로지로 학습 |

#### 매크로 (6개)

| 도구 | 설명 |
|------|------|
| `sap_macro_control` | 녹화 제어 (start/finish/cancel/status/undo) |
| `sap_macro_record_step` | 녹화 중 단계 기록 + SAP 실행 |
| `sap_macro_play` | 매크로 재생 |
| `sap_macro_list` | 매크로 목록 조회 |
| `sap_macro_show` | 매크로 상세 조회 |
| `sap_macro_delete` | 매크로 삭제 |

### 7.3 데몬 모드 (단일 연결)

MCP 서버는 각 도구 호출마다 새로운 Python 프로세스를 시작할 수 있어, COM 초기화 오버헤드가 누적됩니다. 데몬 모드를 사용하면 하나의 상주 프로세스에서 COM 연결을 유지하여 응답 시간을 크게 줄입니다.

```powershell
# 데몬 시작
sap daemon start

# 데몬 상태 확인 (미기동 시 원인 진단 포함)
sap daemon status

# 실행 환경 전체 진단 (인터프리터/venv, sap_cli·pywin32·mcp import, 로그 tail)
sap daemon doctor

# 종료 후 재기동
sap daemon restart

# 데몬 정지
sap daemon stop
```

데몬이 안 뜨거나 명령이 계속 실패하면 `sap daemon doctor`(MCP에서는
`sap_daemon_status` 도구)를 먼저 실행하세요. 어떤 Python 인터프리터/가상환경으로
도는지, `sap_cli`·`pywin32`·`mcp`를 import 할 수 있는지, 기동 실패 시
`~/.sap_cli/daemon.log`의 마지막 에러를 한 번에 보여줍니다. 흔한 원인은 데몬이
`sap_cli`가 설치되지 않은 다른 가상환경으로 스폰되는 경우입니다. 콜드 스타트가
느린 환경은 `SAP_CLI_DAEMON_START_TIMEOUT`(기본 20초)으로 기동 대기 예산을
조정합니다.

MCP 서버가 데몬 모드를 사용하도록 설정:

```json
{
  "mcpServers": {
    "sap-gui": {
      "command": "python",
      "args": ["-m", "sap_cli.mcp_server"],
      "env": {
        "SAP_CLI_USE_DAEMON": "1",
        "SAP_CLI_MCP_SURFACE": "compact",
        "SAP_CLI_MCP_SCREEN_SUMMARY": "0",
        "SAP_CLI_IDLE_WAIT_SECONDS": "0.3",
        "SAP_CLI_POST_BUSY_SETTLE_SECONDS": "0.02"
      }
    }
  }
}
```

`SAP_CLI_USE_DAEMON=1`이면 MCP → 데몬(TCP 18471)으로 명령을 위임하고, 데몬이 없으면 자동으로 직접 실행으로 폴백합니다.
`SAP_CLI_MCP_SCREEN_SUMMARY=0`은 mutating 작업 뒤 전체 화면을 다시 스캔하지 않게 해서 단계별 응답을 빠르게 합니다. 검증 단계에서 화면 요약까지 필요하면 `1`로 켜면 됩니다.

### 7.4 병렬 멀티 세션 운영

여러 AI 에이전트가 각각 독립된 SAP 세션에서 동시에 작업할 수 있습니다. 각 에이전트는 고유한 `slot_id`를 사용하여 자신만의 SAP 세션을 운영합니다.

#### CLI에서 슬롯 사용

```powershell
# 기본 슬롯(ses0)으로 연결
sap connect --system PR1

# ses1 슬롯으로 연결 (--slot은 모든 명령에 적용)
sap --slot ses1 connect --system PR1

# 슬롯별 트랜잭션 실행
sap --slot ses0 run MM03
sap --slot ses1 run VA01

# 활성 슬롯 목록 조회
sap slots
```

#### MCP에서 슬롯 사용

```python
# 에이전트 A: ses0 슬롯에서 자재 조회
sap_session_allocate(slot_id="ses0", agent_name="agent_a", system="PR1")
sap_run(tcode="MM03", slot_id="ses0")

# 에이전트 B: 새 SAP 세션을 생성하여 ses1에 할당
sap_session_allocate(slot_id="ses1", agent_name="agent_b", create_new_session=true)
sap_run(tcode="VA01", slot_id="ses1")

# 작업 완료 후 슬롯 해제
sap_session_release(slot_id="ses1", close=true)
```

#### 슬롯 관리 규칙

- SAP GUI는 연결당 최대 6세션을 지원하며, `SessionPool.max_sessions`(기본 6)으로 제한
- 1시간 미사용 슬롯은 TTL 정책에 의해 자동 해제
- `slot_id`는 영문/숫자/언더스코어/하이픈만 허용 (1-32자)
- 슬롯을 지정하지 않으면 기본 `ses0`으로 동작 (하위 호환)

### 7.5 AI 실행 세션 관리

AI 에이전트의 실행 단위를 "세션"으로 관리합니다. 하나의 사용자 요청 → 여러 도구 호출 → 결과를 하나의 세션으로 묶어 추적합니다.

```
사용자: "MM03에서 자재 100-100을 조회해줘"

AI Agent:
  sap_session_start(user_request="자재 조회", ai_reasoning="MM03 사용")
  sap_connect()
  sap_run(tcode="MM03")
  sap_fill_fields(fields={"MATNR": "100-100"})
  sap_screen()
  sap_session_end(outcome="success")
```

세션 기록은 `~/.sap_cli/sessions/{date}/{session_id}.json`에 저장되며, 각 단계의 before/after 상태가 함께 기록됩니다.

---

## 7.6 ReBAC 권한관리

에이전트 유형에 따라 허용하는 MCP 도구를 제한하는 정책 기반 접근 제어(ReBAC)를 지원합니다.

### 정책 파일 설치

```powershell
# 기본 정책 파일을 사용자 설정 디렉토리로 복사
copy sap_cli\auth\default_policy.yaml %USERPROFILE%\.sap_cli\policy.yaml
```

### 에이전트 프리셋 (6종)

| 프리셋 | 설명 |
|--------|------|
| `readonly` | 조회성 도구만 허용. 데이터 변경 불가 |
| `verifier` | 조회 + 사후 검토 전용. transcript 조회/계획 포함 |
| `standard` | 일반 업무용. 매크로 재생 허용, 녹화는 불가 |
| `operator` | 일반 운영용. 표준 실행/입력 작업 허용 |
| `mm_operator` | MM(자재관리) 도메인 특화. MM 트랜잭션 허용 |
| `sd_operator` | SD(영업/배포) 도메인 특화. SD 트랜잭션 허용 |

SM30, SE38/SA38, SU01 등 위험 트랜잭션은 모든 프리셋에서 차단됩니다.
`sap run SA38 --report RSPARAM --execute-report`처럼 report/program을 명시하면
DangerousReport 정책도 함께 평가되어 report 단위 승인/차단 사유가 audit/preflight에 남습니다.

정책 파일이 있으면 MCP 서버는 권한 검사만 하는 것이 아니라, 프로필에 맞지 않는 도구는 `list_tools` 단계에서 아예 숨깁니다.

- `readonly` / `verifier`: `sap_run`, `sap_set_field`, `sap_send_vkey` 같은 mutating surface가 보이지 않음
- `operator`: 실행/입력 도구는 보이지만 `sap_connect`, `sap_learn`, `sap_macro_delete` 같은 admin surface는 숨김
- `mm_operator`, `sd_operator`: 실행 surface는 보이지만 실제 호출 시 트랜잭션 범위로 다시 제한

도구 호출 이력은 `~/.sap_cli/audit/` 경로에 감사 로그로, 실행 전후 상태는 `~/.sap_cli/transcripts/` 경로에 transcript로 기록됩니다.

### Auth 진단 CLI

ReBAC 운영 중 “정책이 로드됐는지”, “현재 agent가 무엇인지”, “왜 MCP 도구가 보이지 않는지”를 SAP GUI 연결 없이 확인할 수 있습니다.

```powershell
# 정책 로드 여부, active/effective agent, relation 요약, visible MCP tool 개수
sap auth status
sap auth-status

# 현재 agent에 노출되는 MCP tool 목록
sap auth tools
sap auth-tools

# 특정 tool의 permission/resource/노출/허용 여부
sap auth check sap_recipe_query
sap auth check sap_recipe_failure --agent readonly
sap auth-check sap_recipe_query
```

정책 파일이 없으면 `mode: "allow_all"`로 표시되며, 정책 파일이 있고 active/effective agent가 유효하면 `mode: "enforcing"`으로 표시됩니다.

### 정책 파일 예시

```yaml
version: 1
active_agent: verifier   # readonly | verifier | standard | operator | mm_operator | sd_operator

agents:
  readonly:
    description: "조회 전용 에이전트"
    relations:
      - resource: "tx:*"
        relation: viewer    # read 권한만

  standard:
    description: "일반 운영 에이전트"
    relations:
      - resource: "tx:*"
        relation: operator  # read + execute + write

dangerous_transactions:
  - tcode: "SM30"
    reason: "테이블 직접 수정"

audit:
  enabled: true
  log_dir: "~/.sap_cli/audit"

transcript:
  enabled: true
  log_dir: "~/.sap_cli/transcripts"
  include_reads: true
  include_screen_summary: false
```

`active_agent` 값을 변경하면 다음 MCP 서버 재시작 시 적용됩니다. 권한 모델 상세는 `DESIGN_REBAC.md`를 참조하세요.

### 7.7 Transcript와 Verifier 루프

AI 운영에서 중요한 건 "실행"보다 "실행 후 검토"입니다.

`sap_cli`는 mutating 작업 뒤 `post_action`에 아래 문맥을 자동으로 붙입니다.

- `session`
- `status`
- `modal`
- 선택적 `screen_summary`

또한 같은 내용을 transcript로 적재해서, 다른 에이전트가 바로 읽을 수 있게 합니다.

예시:

```text
operator:
  sap_run -> sap_set_field -> sap_send_vkey

verifier:
  sap_transcript_query(outcome="success")
  sap_transcript_plan(command="set", index=0)
  sap_screen / sap_get_status 로 재확인
```

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

### 시나리오 3: AI가 UI 지도를 녹화하고 계획을 실행

사용자가 반복 업무를 지도에 기록한 뒤, 자연어 요청을 계획으로 검토하고 승인 후
실행하는 흐름이다. 지도 녹화는 테스트 client에서 시작하며, 저장·전기·삭제·승인·
전송·인쇄 같은 변경/외부 전송 동작은 자동 녹화하지 않는다.

```
사용자: "MM03 조회 화면을 지도에 기록해. 테스트 client이고 조회만 할 거야"

Claude: sap_daemon_status(action="start")
        sap_connect(system="PR1")
        sap_map_traverse_start(
          tcode="MM03", map_id="mm03-readonly", mode="passive"
        )
        sap_job_status(job_id="...")
        sap_map_report(map_id="mm03-readonly", tcode="MM03")
```

현재 화면 한 장만으로 목표 화면에 도달할 수 없다면 AI는 임의로 업무 버튼을 누르지
않는다. `guided` 지도의 `needs_user_input` 질문과 구조적 `candidate_id`를 사용자에게
보여주고, 사용자가 고른 안전한 탭/트리/스크롤 후보만 다음처럼 재개한다.

```
사용자: "조회 탭으로 계속 기록해"

Claude: sap_map_traverse_resume(
          job_id="...",
          answers={"<resume-token>": {"candidate_id": "<candidate-id>"}}
        )
        sap_job_status(job_id="...")
        sap_map_report(map_id="mm03-readonly", tcode="MM03")
```

지도가 완성되면 AI는 바로 SAP를 조작하지 않고 먼저 실행 계획을 만든다.

```
사용자: "방금 지도에서 자재 TEST_MATNR의 기본 데이터를 조회하는 계획을 보여줘"

Claude: sap_plan_preview(
          request="자재 TEST_MATNR의 기본 데이터 조회",
          tcode="MM03", map_id="mm03-readonly",
          goal_query="기본 데이터", values={"material": "TEST_MATNR"}
        )
        → validation.valid, plan.steps, plan_ref, required_approvals 확인
```

`validation.valid=true`이고 사용자가 계획을 확인한 경우에만 `plan_ref`의 activation
정보를 그대로 넘겨 실행한다. 계획이 지도 버전/fingerprint와 맞지 않거나 목표 화면이
없으면 실행하지 않고 지도를 보강하거나 새 preview를 만든다.

```
사용자: "계획대로 실행해"

Claude: sap_plan_execute(plan_ref=<preview.plan_ref>, verify_mode="cheap")
        sap_get_status()
        sap_screen()  → 최종 화면과 결과 확인
```

변경 단계가 포함된 계획은 `required_approvals`의 step만 별도 승인받은 뒤
`sap_plan_approve`가 반환한 일회성 approval token과 함께 실행한다. 이 흐름의 핵심은
`지도 녹화 → 지도 보고서 확인 → 계획 preview → 사용자 승인 → 계획 실행 → 결과 검증`의
순서를 지키는 것이다.

### 시나리오 4: 매크로 재생 (변수 변경)

```
사용자: "자재조회 매크로 실행해. 자재번호는 200-200으로"

Claude: sap_macro_list()  → 매크로 ID 확인
        sap_macro_play(macro_id="a1b2c3", variables={"matnr": "200-200"})
```

### 시나리오 5: 온톨로지 기반 지능적 조작

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

### 시나리오 6: 배치 처리 (매크로 반복 실행)

```
사용자: "이 자재번호 리스트에 대해 자재조회 매크로를 반복 실행해줘:
100-100, 200-200, 300-300, 400-400"

Claude:
  for each material_number:
    sap_macro_play(macro_id="a1b2c3", variables={"matnr": material_number})
    sap_screen()  → 결과 수집
```

### 시나리오 7: Operator / Verifier 협업

```
사용자: "방금 실행한 MM03 입력 단계를 다시 해도 안전한지 검토해줘"

Verifier Agent:
  sap_transcript_query(command="set", limit=5)
  sap_transcript_plan(command="set", index=0)

응답:
  - 이 단계는 MM03 screen 4000에서 기록됨
  - permission=write 이므로 위험도 high
  - 재실행 전 sap session, sap screen --fields-only로 같은 화면인지 확인 필요
  - 실제 후보 명령: sap set "wnd[0]/usr/ctxtRMMG1-MATNR" "100-100"
  - 실행 직후 sap status, sap modal 확인 권장
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

### "AI가 왜 이런 행동을 했는지 모르겠습니다"

```powershell
sap transcript --limit 20
sap transcript --outcome approval_required
sap transcript-plan --command set --index 0
```

- `sap transcript`로 최근 before/after 상태와 결과를 확인
- `sap transcript-plan`으로 같은 단계를 다시 실행하려면 어떤 확인이 필요한지 검토
- verifier profile을 쓰면 mutating tool 없이도 transcript만 읽어 사후 분석 가능

### "GetObjectTree 지원 안 됨"

- SAP GUI for Windows 7.70 PL3 이상 필요
- 미지원 시 자동으로 수동 탐색(Children 재귀 순회)으로 폴백
- 수동 탐색은 느리지만 동일하게 동작

### "매크로/온톨로지 데이터가 안 보입니다"

버전 업그레이드 이후 데이터 저장 위치가 패키지 내부에서 사용자 홈 디렉토리로 이동했습니다.

| 데이터 종류 | 이전 위치 | 현재 위치 |
|------------|----------|----------|
| 매크로 | `sap_cli/macro/library/` | `~/.sap_cli/macros/` |
| 온톨로지 지식 | `sap_cli/ontology/knowledge/` | `~/.sap_cli/knowledge/` |

패키지에 포함된 기본 데이터(시드 데이터)는 첫 실행 시 `~/.sap_cli/` 경로로 자동 복사됩니다. 이전에 직접 추가한 데이터가 있다면 수동으로 해당 경로로 옮겨야 합니다.

### VKey 번호 레퍼런스

| VKey | 키 | 설명 |
|------|---|------|
| 0 | Enter | 확인/실행 |
| 3 | F3 | 뒤로 가기 |
| 4 | F4 | 검색 도움말 (Search Help) |
| 8 | F8 | 실행 (Execute) |
| 11 | Ctrl+S | 저장 |
| 12 | F12 | 취소 |
| 15 | Shift+F3 | 종료 (Exit) |
| 16 | Shift+F4 | 검색 도움말 — `search_help` 액션이 내부적으로 사용 |

---

## 10. 아키텍처

```
┌──────────────────────┐  ┌──────────────────────┐
│  Agent A (slot=ses0) │  │  Agent B (slot=ses1) │
│  MCP Client          │  │  MCP Client          │
└──────────┬───────────┘  └──────────┬───────────┘
           │ MCP (stdio)             │ MCP (stdio)
┌──────────▼─────────────────────────▼───────────┐
│  sap-cli (Python)                              │
│  ┌────────────────────────────────────┐        │
│  │ CLI (typer)  │  MCP Server (mcp)   │        │
│  └──────┬───────┴──────┬──────────────┘        │
│         │              │                        │
│         │    ┌─────────▼──────────────┐        │
│         │    │ Daemon (TCP 18471)     │        │
│         │    │ + slot_id 라우팅        │        │
│         │    │ + 세션 자동 복구        │        │
│         │    └─────────┬──────────────┘        │
│  ┌──────▼──────────────▼──────────────┐        │
│  │ Central Dispatcher                  │        │
│  │ ├── Auth (ReBAC 정책/감사)          │        │
│  │ ├── Session Store (AI 실행 세션)    │        │
│  │ └── Transcript (실행 기록)          │        │
│  └──────┬─────────────────────────────┘        │
│  ┌──────▼──────────────────────────────┐       │
│  │ Service Layer                       │       │
│  │ └── SessionPool (slot별 래퍼 관리)  │       │
│  │     ├── ses0 → SapGuiWrapper → ses[0]│      │
│  │     ├── ses1 → SapGuiWrapper → ses[1]│      │
│  │     └── TTL 자동 해제 (1시간)       │       │
│  └──────┬─────────────────────────────┘        │
│  ┌──────▼──────────────────────────────┐    │
│  │ Macro System                        │    │
│  │ ├── Recorder (녹화)                 │    │
│  │ ├── Player (재생 + 변수 치환)        │    │
│  │ └── Store (JSON 저장)               │    │
│  └──────┬─────────────────────────────┘     │
│  ┌──────▼─────────────────────────────┐     │
│  │ COM Wrapper (win32com)             │     │
│  │ ├── 멀티 시스템 연결 (SID 탐색)     │     │
│  │ ├── 세션 자동 복구 (identity 검증)  │     │
│  │ └── SAP GUI Scripting API          │     │
│  └──────┬─────────────────────────────┘     │
│  ┌──────▼─────────────────────────────┐     │
│  │ Ontology Layer                     │     │
│  │ ├── Schema (Object/Link/Action)    │     │
│  │ ├── Q&A (시맨틱 검색)              │     │
│  │ ├── Meta-Edge (필드 종속성 규칙)    │     │
│  │ ├── Graph (GraphRAG 탐색)          │     │
│  │ ├── Analysis Space (6공간 분석)    │     │
│  │ ├── BusinessProcess (업무 프로세스)  │     │
│  │ ├── ProcessExecutor (실행 엔진)    │     │
│  │ ├── Recipe (AI 네비게이션 레시피)   │     │
│  │ └── Knowledge (시드 데이터)         │     │
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
├── sap-cli-interactive-guide.html  # 인터랙티브 가이드
├── sap_cli/
│   ├── cli.py                  # CLI 엔트리포인트
│   ├── com_wrapper.py          # SAP GUI COM 래퍼 (멀티 시스템 + 세션 복구)
│   ├── dispatcher.py           # 중앙 명령 디스패처 (권한/감사/실행)
│   ├── mcp_server.py           # MCP 서버
│   ├── daemon.py               # 데몬 서버 (TCP 18471, 단일 COM 연결)
│   ├── service.py              # CLI/MCP 공통 서비스 레이어
│   ├── session_pool.py         # 병렬 멀티 세션 풀 (slot_id 기반)
│   ├── learner.py              # 온톨로지 학습 엔진
│   ├── recipe.py               # AI 네비게이션 레시피 저장/검색
│   ├── session_store.py        # AI 실행 세션 기록
│   ├── transcript_store.py     # 실행 transcript 저장
│   ├── transcript_planner.py   # 재실행 검토 계획 생성
│   ├── command_parser.py       # SAP 명령 파서
│   ├── auth/
│   │   ├── policy.py           # 정책 로더 (policy.yaml 파싱)
│   │   ├── checker.py          # 도구 허용 여부 판단
│   │   ├── audit.py            # 감사 로그 기록
│   │   ├── transcript.py       # Transcript 로거
│   │   └── default_policy.yaml # 기본 정책 파일 (배포용)
│   ├── macro/
│   │   ├── schema.py           # 매크로 데이터 모델
│   │   ├── recorder.py         # 매크로 녹화기
│   │   ├── player.py           # 매크로 재생기
│   │   └── store.py            # 매크로 저장소
│   └── ontology/
│       ├── schema.py           # 온톨로지 스키마 (SapSession, ScreenOntology 등)
│       ├── meta_edge.py        # 메타엣지 규칙 엔진
│       ├── analysis_space.py   # 6공간 분석 프레임워크
│       ├── graph.py            # GraphRAG 관계 그래프
│       ├── store.py            # 온톨로지 저장소
│       ├── process_executor.py # 업무 프로세스 실행 엔진
│       └── knowledge/          # 시드 데이터 (첫 실행 시 ~/.sap_cli/knowledge/로 복사됨)
│           ├── transactions/   # MM03, ME21N, MIGO, MIRO, VA01, VL01N, VF01
│           ├── processes/      # procurement_basic, sales_to_billing
│           ├── meta_edges/     # 메타엣지 규칙
│           └── patterns/       # 공통 패턴 (메타온톨로지)
├── skill/
│   ├── SKILL.md                # SAP GUI 자동화 스킬 정의 (MCP 도구 표면 + 도메인 지식)
│   ├── sap-cli-usage/SKILL.md  # CLI 화면 조작 사용법
│   ├── sap-ontology-qa/SKILL.md # 온톨로지 Q&A (SAP 연결 불필요)
│   ├── sap-map-build/SKILL.md  # CLI 전용 — UI 지도 제작/보강
│   ├── sap-plan-author/SKILL.md # CLI 전용 — 자연어 → 실행 계획
│   ├── sap-plan-run/SKILL.md   # CLI 전용 — 승인/실행/결과 판정
│   └── agents/
│       ├── claude/sap-map-planner.md          # Claude Code 서브에이전트
│       └── codex/sap-map-planner/SKILL.md     # Codex용 에이전트 스킬
└── tests/                      # 순수 로직 테스트 (SAP/Windows 불필요)
    ├── test_dispatcher.py      # 디스패처 통합 테스트
    ├── test_mcp_server.py      # MCP 서버 테스트
    ├── test_mcp_daemon.py      # MCP → 데몬 위임 테스트
    ├── test_auth.py            # ReBAC 정책/감사 로그 테스트
    ├── test_session_store.py   # AI 실행 세션 테스트
    ├── test_connection_stability.py  # 멀티 시스템/세션 복구 테스트
    ├── test_ontology_ask.py    # 온톨로지 Q&A 테스트
    ├── test_recipe_query.py    # 레시피 검색 테스트
    └── ...                     # 기타 단위 테스트 (실제 수는 코드 변경에 따라 달라짐)
```

> 실제 매크로 데이터는 `~/.sap_cli/macros/`, 온톨로지 지식 데이터는 `~/.sap_cli/knowledge/`에 저장됩니다. 패키지의 `knowledge/` 디렉토리는 시드 데이터 역할만 합니다.

---

## 11. 테스트

SAP GUI나 Windows 없이도 실행할 수 있는 순수 로직 테스트입니다 (COM 의존성 없음).

### 테스트 실행

```powershell
# 가상환경 활성화 후
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS/Linux

# 개발 의존성 설치 (pytest 포함)
pip install -e ".[dev]"

# 전체 테스트 실행
pytest tests/ -v
```

### 테스트 현황

| 항목 | 값 |
|------|---|
| 총 테스트 수 | 환경/커밋에 따라 변동 (pytest로 확인) |
| 실행 시간 | 환경에 따라 변동 |
| SAP/Windows 필요 여부 | 불필요 |

매크로 변수 치환, 온톨로지 스키마 검증, ReBAC 정책 판단, 디스패처 통합, MCP 서버 라우팅, 데몬 위임, 세션 스토어, 레시피 검색, 온톨로지 Q&A, 연결 안정성(멀티 시스템/세션 복구), 병렬 멀티 세션(풀 CRUD/TTL/슬롯 검증/디스패처 통합/스레드 안전) 등을 포함합니다.

---

## 12. AI 스킬·서브에이전트 설치

이 저장소는 Claude Code와 Codex CLI에서 바로 쓸 수 있는 스킬과 에이전트 정의를
`skill/` 아래에 포함합니다. 스킬은 **파일을 복사(또는 심볼릭 링크)하기만 하면**
설치됩니다. 별도 빌드나 등록 명령은 없습니다.

### 12.1 무엇이 들어 있나

| 이름 | 종류 | 범위 | MCP 필요 |
|------|------|------|---------|
| `sap` (`skill/SKILL.md`) | 스킬 | MCP 도구 표면 + SAP 도메인 지식 | O |
| `sap-cli-usage` | 스킬 | CLI 화면 조작 사용법 | X |
| `sap-ontology-qa` | 스킬 | 온톨로지 Q&A (SAP 연결 불필요) | X |
| `sap-map-build` | 스킬 | UI 지도 수집·보강·점검 | X (CLI 전용) |
| `sap-plan-author` | 스킬 | 자연어 요청 → 실행 계획 컴파일 | X (CLI 전용) |
| `sap-plan-run` | 스킬 | 승인·실행·결과 판정 | X (CLI 전용) |
| `sap-map-planner` | 에이전트 | 위 셋을 자연어 요청 하나로 묶어 수행 | X (CLI 전용) |

지도·계획·실행 3종과 에이전트는 MCP 서버 없이 `sap` CLI만으로 동작합니다.
다만 `plan-preview` / `plan-approve` / `plan-execute`와 지도 job 계열은
persistent daemon이 필요하므로 `sap daemon start`를 먼저 실행합니다.

### 12.2 Claude Code에 설치

스킬은 `~/.claude/skills/<이름>/SKILL.md`, 서브에이전트는
`~/.claude/agents/<이름>.md`에 둡니다.

```bash
# 저장소 루트에서
mkdir -p ~/.claude/skills ~/.claude/agents

# CLI 전용 스킬 및 에이전트
cp -r skill/sap-map-build   ~/.claude/skills/
cp -r skill/sap-plan-author ~/.claude/skills/
cp -r skill/sap-plan-run    ~/.claude/skills/

# (선택) 기존 스킬
cp -r skill/sap-cli-usage   ~/.claude/skills/
cp -r skill/sap-ontology-qa ~/.claude/skills/
mkdir -p ~/.claude/skills/sap && cp skill/SKILL.md ~/.claude/skills/sap/

# 서브에이전트
cp skill/agents/claude/sap-map-planner.md ~/.claude/agents/
```

Windows PowerShell:

```powershell
New-Item -ItemType Directory -Force "$HOME\.claude\skills","$HOME\.claude\agents" | Out-Null
Copy-Item -Recurse -Force skill\sap-map-build,skill\sap-plan-author,skill\sap-plan-run "$HOME\.claude\skills\"
Copy-Item -Recurse -Force skill\sap-cli-usage,skill\sap-ontology-qa "$HOME\.claude\skills\"
New-Item -ItemType Directory -Force "$HOME\.claude\skills\sap" | Out-Null
Copy-Item -Force skill\SKILL.md "$HOME\.claude\skills\sap\SKILL.md"
Copy-Item -Force skill\agents\claude\sap-map-planner.md "$HOME\.claude\agents\"
```

저장소 변경을 계속 따라가려면 복사 대신 심볼릭 링크를 씁니다.

```bash
ln -s "$PWD/skill/sap-map-build" ~/.claude/skills/sap-map-build
ln -s "$PWD/skill/agents/claude/sap-map-planner.md" ~/.claude/agents/sap-map-planner.md
```

프로젝트 단위로만 쓰려면 `~/.claude` 대신 저장소의 `.claude/skills`,
`.claude/agents`에 두면 됩니다.

설치 확인은 Claude Code 세션에서 `/skills`(목록에 `sap-map-build`,
`sap-plan-author`, `sap-plan-run`)와 `/agents`(목록에 `sap-map-planner`)로 합니다.

### 12.3 Codex CLI에 설치

Codex 스킬은 `~/.codex/skills/<이름>/SKILL.md`에 둡니다. Codex CLI에는 Claude
Code의 서브에이전트 레지스트리에 해당하는 기능이 없으므로, 에이전트는 **같은
역할을 하는 스킬** 형태로 제공합니다.

```bash
mkdir -p ~/.codex/skills

cp -r skill/sap-map-build   ~/.codex/skills/
cp -r skill/sap-plan-author ~/.codex/skills/
cp -r skill/sap-plan-run    ~/.codex/skills/
cp -r skill/agents/codex/sap-map-planner ~/.codex/skills/
```

Windows PowerShell:

```powershell
New-Item -ItemType Directory -Force "$HOME\.codex\skills" | Out-Null
Copy-Item -Recurse -Force skill\sap-map-build,skill\sap-plan-author,skill\sap-plan-run "$HOME\.codex\skills\"
Copy-Item -Recurse -Force skill\sap-cli-usage,skill\sap-ontology-qa "$HOME\.codex\skills\"
New-Item -ItemType Directory -Force "$HOME\.codex\skills\sap" | Out-Null
Copy-Item -Force skill\SKILL.md "$HOME\.codex\skills\sap\SKILL.md"
Copy-Item -Recurse -Force skill\agents\codex\sap-map-planner "$HOME\.codex\skills\"
```

설치 후 Codex 세션에서 `/skills`로 목록을 확인합니다.

### 12.4 설치 후 첫 실행

```powershell
sap daemon start
sap connect
sap session
```

그다음 자연어로 요청하면 됩니다.

```
MM03 지도를 만들어줘. 테스트 client이고 조회만 할 거야.        → sap-map-build
mm03-readonly 지도로 자재 조회 계획을 보여줘. 실행은 하지 마.  → sap-plan-author
계획 확인했어. 실행해줘.                                       → sap-plan-run
```

세 단계를 한 번에 맡기려면 에이전트를 부릅니다.

```
sap-map-planner로 mm03-readonly 지도를 써서 자재 TEST_MATNR 기본 데이터를 조회해줘.
```

에이전트는 계획을 보여주고 **사용자 확인 전에는 실행하지 않습니다.** 저장·변경·
전표 생성·승인·발송이 포함된 계획은 해당 step만 별도 승인을 받습니다.

### 12.5 업데이트와 제거

업데이트는 같은 경로로 다시 복사하면 됩니다(심볼릭 링크로 설치했으면 불필요).

```bash
cp -r skill/sap-map-build ~/.claude/skills/
```

제거는 설치한 디렉토리를 지우면 됩니다.

- Claude Code: `~/.claude/skills/sap-map-build`, `~/.claude/skills/sap-plan-author`,
  `~/.claude/skills/sap-plan-run`, `~/.claude/agents/sap-map-planner.md`
- Codex: `~/.codex/skills/` 아래 같은 이름의 디렉토리 4개

스킬 파일은 지시문일 뿐이며 SAP 데이터를 담지 않습니다. 지도·레시피·매크로 같은
실제 데이터는 `~/.sap_cli/` 아래에 그대로 남습니다.
